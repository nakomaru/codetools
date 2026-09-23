from codetools.protocol import has_batch, parse


def batch(*lines: str, message: str | None = "Test batch") -> str:
    tail = ["=== message", message] if message is not None else []
    return "\n".join(["=== batch", *lines, *tail, "=== end"])


def test_text_without_batch_is_ignored():
    assert parse("just prose\n```python\nprint(1)\n```") is None
    assert not has_batch("mentions === batch inline only")


def test_prose_and_fences_around_the_batch_are_ignored():
    text = "Here is the plan.\n\n````\n" + batch("=== read a.py") + "\n````\n\nLet me know."
    parsed = parse(text)
    assert not parsed.errors
    assert [(op.verb, op.args["path"]) for op in parsed.ops] == [("read", "a.py")]


def test_read_expands_multiple_paths_and_ranges():
    parsed = parse(batch("=== read a.py src/b.py:10-40 c.py:5"))
    assert [(op.index, op.args["path"], op.args["start"], op.args["end"]) for op in parsed.ops] == [
        (1, "a.py", None, None), (2, "src/b.py", 10, 40), (3, "c.py", 5, None)]


def test_one_malformed_op_rejects_the_whole_batch_with_line_numbers():
    parsed = parse(batch("=== read a.py", "=== reed b.py", "=== move only_one_arg", "=== read c.py"))
    assert len(parsed.errors) == 2
    assert parsed.errors[0].line == 3 and "unknown op `reed`" in parsed.errors[0].message
    assert parsed.errors[1].line == 4 and "SRC and DST" in parsed.errors[1].message


def test_missing_end_is_reported_as_cut_off():
    parsed = parse("=== batch\n=== read a.py\n")
    assert any("no `=== end`" in e.message for e in parsed.errors)


def test_indented_batch_is_dedented():
    text = "1. do this:\n\n    ````\n    === batch\n    === write a.py\n    def f():\n        return 1\n    === message\n    Add f\n    === end\n    ````"
    parsed = parse(text)
    assert not parsed.errors
    assert parsed.ops[0].args["content"] == ["def f():", "    return 1"]


def test_write_content_may_contain_code_fences_and_trims_outer_blank_lines():
    parsed = parse(batch("=== write notes.md", "", "# Notes", "```python", "print(1)", "```", "", "=== read a.py"))
    assert parsed.ops[0].args["content"] == ["# Notes", "```python", "print(1)", "```"]
    assert parsed.ops[1].verb == "read"


def test_edit_hunks_and_marker_errors():
    parsed = parse(batch("=== edit a.py", "<<<<<<< SEARCH", "x = 1", "=======", "x = 2", ">>>>>>> REPLACE",
                         "<<<<<<< SEARCH", "y = 1", "=======", ">>>>>>> REPLACE"))
    hunks = parsed.ops[0].args["hunks"]
    assert [(h.search, h.replace) for h in hunks] == [(["x = 1"], ["x = 2"]), (["y = 1"], [])]

    broken = parse(batch("=== edit a.py", "<<<<<<< SEARCH", "x = 1", ">>>>>>> REPLACE"))
    assert "no `=======` divider" in broken.errors[0].message
    empty = parse(batch("=== edit a.py", "<<<<<<< SEARCH", "=======", "x", ">>>>>>> REPLACE"))
    assert "SEARCH is empty" in empty.errors[0].message


def test_paths_are_confined():
    for bad in ("../x.py", "/etc/passwd", "C:/x.py", ".git/config", "src/../../x"):
        parsed = parse(batch(f"=== read {bad}"))
        assert parsed.errors, bad
    parsed = parse(batch("=== read src\\win\\style.py ./a.py"))
    assert [op.args["path"] for op in parsed.ops] == ["src/win/style.py", "a.py"]


def test_grep_arguments_keep_backslashes_and_validate_regex():
    parsed = parse(batch(r"=== grep 'def\s+main' src tests -i -g '*.py' -C 2"))
    a = parsed.ops[0].args
    assert a["pattern"] == r"def\s+main" and a["paths"] == ["src", "tests"]
    assert a["glob"] == "*.py" and a["context"] == 2 and a["regex"].flags & 2
    assert "invalid regex" in parse(batch("=== grep '(unclosed'")).errors[0].message
    assert parse(batch("=== grep -- -x")).ops[0].args["pattern"] == "-x"


def test_non_body_ops_reject_content_lines():
    parsed = parse(batch("=== read a.py", "b.py"))
    assert "takes everything on its header line" in parsed.errors[0].message


def test_patch_sections():
    parsed = parse(batch(
        "=== patch",
        "diff --git a/src/a.py b/src/a.py",
        "--- a/src/a.py",
        "+++ b/src/a.py",
        "@@ -1,3 +1,3 @@",
        " def f():",
        "-    return 1",
        "+    return 2",
        "",
        "--- /dev/null",
        "+++ b/new.py",
        "@@ -0,0 +1 @@",
        "+x = 1",
        "--- a/old.py",
        "+++ /dev/null",
    ))
    assert not parsed.errors, parsed.errors
    files = parsed.ops[0].args["files"]
    assert [(f.old_path, f.new_path) for f in files] == [("src/a.py", "src/a.py"), (None, "new.py"), ("old.py", None)]
    assert files[0].hunks[0].search == ["def f():", "    return 1", ""]
    assert files[0].hunks[0].replace == ["def f():", "    return 2", ""]


def test_removed_sql_comment_line_is_not_a_file_header():
    parsed = parse(batch("=== patch", "--- q.sql", "+++ q.sql", "@@", " select 1;", "--- old comment", "+-- new"))
    assert not parsed.errors
    assert parsed.ops[0].args["files"][0].hunks[0].search == ["select 1;", "-- old comment"]


def test_run_takes_the_raw_command():
    parsed = parse(batch("=== run python -m pytest -q 'tests/x y.py' && echo done"))
    assert parsed.ops[0].args["command"] == "python -m pytest -q 'tests/x y.py' && echo done"
    assert parsed.ops[0].kind == "command"


def test_changes_and_commands_need_a_message_but_queries_do_not():
    assert not parse(batch("=== read a.py", message=None)).errors
    for op in ("=== write a.py", "=== run ls"):
        errors = parse(batch(op, message=None)).errors
        assert len(errors) == 1 and "must end with `=== message`" in errors[0].message


def test_message_is_the_last_op_with_a_summary_and_optional_body():
    parsed = parse(batch("=== delete old.py", message="Remove old.py\n\nNothing imports it anymore."))
    assert not parsed.errors and parsed.message == "Remove old.py\n\nNothing imports it anymore."
    early = parse("=== batch\n=== message\nFirst\n=== delete old.py\n=== end")
    assert any("must be the last op" in e.message for e in early.errors)
    assert any("is empty" in e.message for e in parse(batch("=== delete old.py", message="")).errors)
    inline = parse("=== batch\n=== delete old.py\n=== message Remove it\n=== end")
    assert any("lines after the header" in e.message for e in inline.errors)
