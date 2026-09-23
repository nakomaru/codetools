import subprocess
from pathlib import Path

import pytest

from codetools import batch as states
from codetools.engine import Engine, EngineError, StaleBatch


def batch(*lines: str, message: str | None = "Test batch") -> str:
    tail = ["=== message", message] if message is not None else []
    return "\n".join(["````", "=== batch", *lines, *tail, "=== end", "````"])


def ingest(engine: Engine, text: str, force: bool = False):
    result = engine.ingest(text, force)
    assert result.batch is not None, result
    return result.batch


def edit(path: str, search: list[str], replace: list[str]) -> list[str]:
    return [f"=== edit {path}", "<<<<<<< SEARCH", *search, "=======", *replace, ">>>>>>> REPLACE"]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    run()\n\n\ndef run():\n    print('hi')\n", "utf-8")
    (tmp_path / "src" / "win.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    (tmp_path / ".env").write_text("TOKEN=secret\n", "utf-8")
    return tmp_path


def test_queries_run_immediately_and_batch_is_done(project):
    e = Engine(project)
    b = ingest(e, batch("=== read src/app.py:5-6", "=== grep TOKEN", "=== read .env", "=== find '*.py'"))
    assert b.status == states.DONE
    assert "    5| def run():" in b.report
    assert "0 files" in b.report or "no matches" in b.report
    assert "secret-file pattern" in b.report and "TOKEN=secret" not in b.report
    assert "src/app.py\nsrc/win.py" in b.report
    assert b.report.startswith("[codetools] batch 1: 4 queries (1 failed), done.")


def test_malformed_batch_runs_nothing(project):
    e = Engine(project)
    b = ingest(e, batch("=== write src/new.py", "x = 1", "=== frobnicate"))
    assert b.status == states.MALFORMED
    assert "REJECTED: malformed" in b.report and "unknown op `frobnicate`" in b.report
    assert not (project / "src" / "new.py").exists()
    with pytest.raises(EngineError):
        e.apply(b.id, partial=True)


def test_partial_apply_applies_passing_ops_in_order(project):
    e = Engine(project)
    b = ingest(e, batch(
        *edit("src/app.py", ["def main():", "    run()"], ["def main():", "    run()", "    run()"]),
        *edit("src/app.py", ["def run():", "    print('hello')"], ["def run():", "    print('bye')"]),
        *edit("src/win.py", ["b = 2"], ["b = 3"]),
        "=== write lib/new.py", "x = 1",
        "=== move lib/new.py lib/moved.py",
        "=== edit lib/moved.py", "<<<<<<< SEARCH", "x = 1", "=======", "x = 2", ">>>>>>> REPLACE",
    ))
    assert b.status == states.PENDING
    assert [op.index for op in b.failed()] == [2]
    assert "preflight 5 of 6 ops passed, 1 failed (op 2)" in b.report
    assert "`applypartial 1` applies the 5 passing ops and rejects op 2" in b.report
    assert "closest match is lines 6-7" in b.report
    assert "line numbers include the changes of earlier ops" in b.report

    with pytest.raises(EngineError, match="applypartial"):
        e.apply(b.id, partial=False)
    b = e.apply(b.id, partial=True)
    assert b.status == states.APPLIED and b.partial
    assert (project / "src" / "app.py").read_text("utf-8").startswith("def main():\n    run()\n    run()\n")
    assert (project / "src" / "win.py").read_bytes() == b"a = 1\r\nb = 3\r\n"
    assert (project / "lib" / "moved.py").read_text("utf-8") == "x = 2\n"
    assert not (project / "lib" / "new.py").exists()
    assert "applied partially: 5 ops applied, 1 rejected (op 2)" in b.report
    assert "- A lib/moved.py (+1 -0)" in b.report


def test_edit_keeps_trailing_whitespace_on_lines_it_does_not_change(project):
    (project / "notes.md").write_bytes(b"first line  \nsecond\t\nthird  \n")
    e = Engine(project)
    b = ingest(e, batch(*edit("notes.md", ["first line", "second", "third"], ["first line", "SECOND", "third"])))
    e.apply(b.id, partial=False)
    assert (project / "notes.md").read_bytes() == b"first line  \nSECOND\nthird  \n"


def test_undo_restores_everything(project):
    before_app = (project / "src" / "app.py").read_bytes()
    e = Engine(project)
    b = ingest(e, batch(*edit("src/app.py", ["    print('hi')"], ["    print('yo')"]),
                          "=== write src/extra/new.py", "y = 1", "=== delete src/win.py"))
    e.apply(b.id, partial=False)
    assert not (project / "src" / "win.py").exists()
    report = e.undo(b.id)
    assert "3 files returned to their state before it was applied" in report
    assert "- src/app.py: restored" in report and "- src/win.py: restored" in report
    assert "- src/extra/new.py: removed (the batch created it)" in report
    assert (project / "src" / "app.py").read_bytes() == before_app
    assert (project / "src" / "win.py").read_bytes() == b"a = 1\r\nb = 2\r\n"
    assert not (project / "src" / "extra").exists()
    with pytest.raises(EngineError, match="already undone"):
        e.undo(b.id)


def test_undo_refuses_when_files_changed_afterward(project):
    e = Engine(project)
    b = ingest(e, batch(*edit("src/app.py", ["    print('hi')"], ["    print('yo')"])))
    e.apply(b.id, partial=False)
    (project / "src" / "app.py").write_text("changed by hand\n", "utf-8")
    with pytest.raises(EngineError, match="files changed after"):
        e.undo(b.id)


def test_stale_batch_is_repreflighted_instead_of_applied(project):
    e = Engine(project)
    b = ingest(e, batch(*edit("src/app.py", ["    print('hi')"], ["    print('yo')"])))
    (project / "src" / "app.py").write_text("def main():\n    pass\n", "utf-8")
    with pytest.raises(StaleBatch) as err:
        e.apply(b.id, partial=False)
    assert "re-preflighted" in err.value.report
    assert [op.index for op in b.failed()] == [1]
    assert (project / "src" / "app.py").read_text("utf-8") == "def main():\n    pass\n"


def test_duplicate_clipboard_batches_are_skipped_unless_forced(project):
    e = Engine(project)
    text = batch("=== read src/app.py")
    b = ingest(e, text)
    again = e.ingest("different prose around it\n" + text)
    assert again.batch is None and again.duplicate_of == b.id
    forced = ingest(e, text, force=True)
    assert forced.id == b.id + 1


def test_incomplete_batch_waits_unless_forced(project):
    e = Engine(project)
    text = "````\n=== batch\n=== read src/app.py\n=== edit src/app.py\n<<<<<<< SEARCH\n"
    result = e.ingest(text)
    assert result.incomplete and result.batch is None and not e.batches
    b = ingest(e, text, force=True)
    assert b.status == states.MALFORMED and "reply cut off?" in b.report


def test_heredoc_content_can_hold_batch_markers(project):
    e = Engine(project)
    b = ingest(e, batch("=== write docs/protocol-example.md <<EOF", "=== batch", "=== read a.py", "=== end", "",
                        "EOF", "=== read src/app.py:1"))
    assert [op.verb for op in b.ops] == ["write", "read"]
    e.apply(b.id, partial=False)
    assert (project / "docs" / "protocol-example.md").read_text("utf-8") == "=== batch\n=== read a.py\n=== end\n\n"


def test_reply_and_reports_are_kept_per_batch(project):
    e = Engine(project)
    b = ingest(e, "Some prose.\n" + batch(*edit("src/app.py", ["    print('hi')"], ["    print('yo')"])))
    e.apply(b.id, partial=False)
    d = project / ".codetools" / "batches" / "0001"
    assert (d / "reply.txt").read_text("utf-8").startswith("Some prose.")
    assert {p.name for p in d.iterdir()} >= {"reply.txt", "report-preflight.txt", "report-applied.txt",
                                            "history.json"}
    assert (project / ".codetools" / ".gitignore").read_text("utf-8") == "batches/\nlog.txt\nhistory.git/\n"


def test_context_includes_protocol_notes_tree_and_pins(project):
    e = Engine(project)
    (project / ".codetools" / "notes.md").write_text("<!-- hidden -->\nRun tests with pytest.\n", "utf-8")
    assert e.pin("src/app.py") == "pinned src/app.py"
    assert e.pin("src/*.py") == "pinned src/*.py (2 files)"
    with pytest.raises(EngineError, match="matches no file"):
        e.pin("nope.py")
    ctx = e.context()
    assert ctx.pinned == ["src/app.py", "src/win.py"]
    assert "=== batch" in ctx.text and "Run tests with pytest." in ctx.text and "hidden" not in ctx.text
    assert "  src/\n    app.py (6 lines)\n    win.py (2 lines)" in ctx.text
    assert "## src/app.py (lines 1-6 of 6)" in ctx.text and "    6|     print('hi')" in ctx.text
    assert "TOKEN=secret" not in ctx.text
    assert e.unpin("src/*.py") == "unpinned src/*.py"
    assert e.state.pins() == ["src/app.py"]


def test_outline_op_covers_files_and_directories(project):
    (project / "src" / "notes.md").write_text("# A\n## B\n", "utf-8")
    e = Engine(project)
    b = ingest(e, batch("=== outline src", "=== outline src/win.py .env"))
    first, second = (b.results[op.index] for op in b.ops)
    assert first.summary == "4 entries in 3 files"
    assert "src/app.py (6 lines)\n        1-2  def main()\n        5-6  def run()" in first.detail
    assert not second.ok and "secret-file pattern" in second.summary


def test_reports_fold_their_body_and_end_with_room_to_type(project):
    e = Engine(project)
    b = ingest(e, batch("=== read src/app.py"))
    head, rest = b.report.split("\n", 1)
    assert head.startswith("[codetools] batch 1: 1 query, done.")
    assert rest.startswith("````\n") and rest.endswith("\n````\n\n\n")
    assert "The operator's task follows." not in b.report
    assert "\n```\n    1| def main():" in rest


def test_context_starts_with_the_environment(project):
    ctx = Engine(project).context()
    head, rest = ctx.text.split("\n", 1)
    assert head.startswith(f"[codetools] project context for `{project.name}`, as of ")
    fence = rest.split("\n", 1)[0]
    assert set(fence) == {"`"} and len(fence) >= 5
    assert f"- Project root: `{project}`. Every path in a batch is relative to it" in ctx.text
    assert "- Local time: " in ctx.text and "(UTC" in ctx.text
    assert ctx.text.endswith(f"{fence}\n\nThe operator's task follows.\n\n\n")


def test_settings_are_read_from_the_project(project):
    (project / ".codetools").mkdir()
    (project / ".codetools" / "settings.yaml").write_text(
        "context:\n  include_protocol: false\n  tree_line_counts: false\nsecrets:\n  extra_patterns: ['*.sqlite']\n",
        "utf-8")
    (project / "data.sqlite").write_text("rows\n", "utf-8")
    e = Engine(project)
    assert not e.settings.include_protocol
    b = ingest(e, batch("=== read data.sqlite", "=== tree"))
    assert "secret-file pattern" in b.report and "app.py (" not in b.report
    assert "=== batch" not in e.context().text


def test_batch_ids_continue_across_sessions(project):
    Engine(project).ingest(batch("=== read src/app.py"))
    b = ingest(Engine(project), batch("=== read src/win.py"))
    assert b.id == 2


def test_directory_move_and_delete(project):
    (project / "pkg" / "sub").mkdir(parents=True)
    (project / "pkg" / "a.py").write_text("a\n", "utf-8")
    (project / "pkg" / "sub" / "b.py").write_text("b\n", "utf-8")
    e = Engine(project)
    b = ingest(e, batch("=== move pkg lib/pkg", "=== delete lib/pkg/sub", "=== delete lib/pkg/sub -r"))
    assert [op.index for op in b.failed()] == [2]
    e.apply(b.id, partial=True)
    assert (project / "lib" / "pkg" / "a.py").read_text("utf-8") == "a\n"
    assert not (project / "lib" / "pkg" / "sub").exists()
    assert not (project / "pkg").exists()


def test_write_refuses_existing_and_overwrite_refuses_missing(project):
    e = Engine(project)
    b = ingest(e, batch("=== write src/app.py", "x", "=== overwrite src/nope.py", "x",
                          "=== overwrite src/win.py", "c = 3"))
    results = [b.results[op.index] for op in b.ops]
    assert not results[0].ok and "already exists" in results[0].summary
    assert not results[1].ok and "use write" in results[1].summary
    assert results[2].ok
    e.apply(b.id, partial=True)
    assert (project / "src" / "win.py").read_bytes() == b"c = 3\r\n"


def test_patch_applies_by_content(project):
    e = Engine(project)
    b = ingest(e, batch(
        "=== patch",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -99,2 +99,2 @@",
        " def run():",
        "-    print('hi')",
        "+    print('patched')",
        "--- /dev/null",
        "+++ b/src/made.py",
        "@@ -0,0 +1,2 @@",
        "+one",
        "+two",
    ))
    assert not b.failed(), b.report
    e.apply(b.id, partial=False)
    assert "print('patched')" in (project / "src" / "app.py").read_text("utf-8")
    assert (project / "src" / "made.py").read_text("utf-8") == "one\ntwo\n"


def test_commands_run_after_changes(project):
    e = Engine(project)
    b = ingest(e, batch("=== write out.txt", "hello", "=== run cat out.txt && exit 3"))
    e.apply(b.id, partial=False)
    result = b.runs[2]
    assert result.exit_code == 3 and result.output == "hello"
    assert "### op 2: run cat out.txt && exit 3: exit 3 in" in b.report


def test_reject_copies_operator_note(project):
    e = Engine(project)
    b = ingest(e, batch("=== write x.py", "x"))
    b = e.reject(b.id, "use the existing helper instead")
    assert b.status == states.REJECTED
    assert "Operator note: use the existing helper instead" in b.report
    with pytest.raises(EngineError, match="rejected, not pending"):
        e.apply(b.id, partial=False)


def history_log(project: Path) -> list[str]:
    git_dir = project / ".codetools" / "history.git"
    out = subprocess.run(["git", f"--git-dir={git_dir}", "log", "--format=%s"], capture_output=True, check=True)
    return out.stdout.decode("utf-8").splitlines()


def test_undo_reverses_what_commands_did(project):
    e = Engine(project)
    b = ingest(e, batch("=== write out.txt", "hello",
                        "=== run mv src/win.py src/moved.py && echo made > gen.txt && echo NEW=1 >> .env",
                        message="Move win.py\n\nAnd generate gen.txt."))
    e.apply(b.id, partial=False)
    assert (project / "src" / "moved.py").exists() and (project / "gen.txt").exists()
    report = e.undo(b.id)
    assert "- src/moved.py: removed (the batch created it)" in report
    assert "- src/win.py: restored (the batch deleted it)" in report
    assert "batch 1 (Move win.py) undone" in report
    assert (project / "src" / "win.py").read_bytes() == b"a = 1\r\nb = 2\r\n"
    assert (project / ".env").read_text("utf-8") == "TOKEN=secret\n"
    assert not (project / "src" / "moved.py").exists() and not (project / "gen.txt").exists()
    assert not (project / "out.txt").exists()


def test_history_has_a_commit_per_batch_and_for_edits_between_them(project):
    e = Engine(project)
    first = ingest(e, batch(*edit("src/app.py", ["    print('hi')"], ["    print('yo')"]), message="Say yo"))
    e.apply(first.id, partial=False)
    (project / "notes.txt").write_text("by hand\n", "utf-8")
    second = ingest(e, batch("=== write lib.py", "x = 1", message="Add lib.py"))
    e.apply(second.id, partial=False)
    e.undo(first.id)
    assert history_log(project) == ["Undo batch 1: Say yo", "Add lib.py", "Project state before batch 2", "Say yo",
                                    "Project state before batch 1"]
    assert "yo" not in (project / "src" / "app.py").read_text("utf-8")
    assert (project / "lib.py").exists() and (project / "notes.txt").exists()


def test_existing_state_gitignore_gains_the_history_entry(project):
    (project / ".codetools").mkdir()
    (project / ".codetools" / ".gitignore").write_text("batches/\nlog.txt\n", "utf-8")
    Engine(project)
    assert (project / ".codetools" / ".gitignore").read_text("utf-8") == "batches/\nlog.txt\nhistory.git/\n"
