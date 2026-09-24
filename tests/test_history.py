"""Snapshots through pygit2 must record the tree `git add --all` records. When git is installed, every step is
checked against it: the same project, snapshotted by both, must give the same tree id."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pygit2
import pytest

from codetools.history import History

GIT = shutil.which("git")


def write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def commit_nested(root: Path, rel: str, message: str) -> None:
    path = root / rel
    repo = pygit2.Repository(str(path)) if (path / ".git").is_dir() else pygit2.init_repository(str(path))
    repo.index.add_all()
    repo.index.write()
    signature = pygit2.Signature("t", "t@localhost")
    parents = [] if repo.head_is_unborn else [repo.head.target]
    repo.create_commit("HEAD", signature, signature, message, repo.index.write_tree(), parents)


def remove_tree(path: Path) -> None:
    """shutil.rmtree, after clearing the read-only flag git sets on object files."""
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
    shutil.rmtree(path)


def git_tree(root: Path, git_dir: Path) -> str:
    def git(*args: str) -> str:
        cmd = [GIT, "--literal-pathspecs", f"--git-dir={git_dir}", f"--work-tree={root}", *args]
        return subprocess.run(cmd, cwd=root, capture_output=True, check=True).stdout.decode().strip()

    if not git_dir.exists():
        subprocess.run([GIT, "init", "--quiet", "--bare", str(git_dir)], check=True)
        git("config", "core.bare", "false")
        git("config", "core.autocrlf", "false")
        (git_dir / "info" / "exclude").write_text("/.codetools/\n", encoding="utf-8")
    git("add", "--all", "--", ".")
    return git("write-tree")


def history_tree(history: History, message: str) -> str:
    commit = history.snapshot(message)
    return str(pygit2.Repository(str(history.git_dir))[commit].peel(pygit2.Tree).id)


def build(root: Path) -> None:
    write(root, ".gitignore", b"*.log\nbuild/\n!keep.log\n")
    write(root, "a.py", b"print(1)\r\n")
    write(root, "x.log", b"ignored")
    write(root, "keep.log", b"kept")
    write(root, "build/out.txt", b"ignored directory")
    write(root, "sub/.gitignore", b"secret*\n")
    write(root, "sub/secret.txt", b"ignored by a nested .gitignore")
    write(root, "sub/ok.txt", b"ok")
    write(root, "sub/deep/été 日本.txt", "unicode".encode())
    write(root, "bin.dat", bytes(range(256)))
    write(root, "empty.txt", b"")
    write(root, ".codetools/batches/1/reply.md", b"state")
    write(root, "nested/n.txt", b"file in a nested repository")
    commit_nested(root, "nested", "first")


def step_edit_delete_add(root: Path) -> None:
    write(root, "a.py", b"print(2)\n")
    (root / "bin.dat").unlink()
    write(root, "new/dir/n.txt", b"new")
    write(root, "x.log", b"still ignored")


def step_nested_head_moves(root: Path) -> None:
    write(root, "nested/n2.txt", b"second")
    commit_nested(root, "nested", "second")


def step_tracked_file_becomes_ignored(root: Path) -> None:
    with open(root / ".gitignore", "ab") as f:
        f.write(b"a.py\n")
    write(root, "a.py", b"print(3)\n")


def step_directory_and_nested_repo_deleted(root: Path) -> None:
    remove_tree(root / "sub" / "deep")
    remove_tree(root / "nested")


def step_directory_becomes_file(root: Path) -> None:
    remove_tree(root / "new")
    write(root, "new", b"now a file")


def step_file_becomes_directory(root: Path) -> None:
    (root / "empty.txt").unlink()
    write(root, "empty.txt/inner.txt", b"was a file")


STEPS = [step_edit_delete_add, step_nested_head_moves, step_tracked_file_becomes_ignored,
         step_directory_and_nested_repo_deleted, step_directory_becomes_file, step_file_becomes_directory]


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_snapshots_match_git_add_all(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    build(root)
    history = History(root, root / ".codetools" / "history.git")
    cli_git_dir = tmp_path / "cli.git"
    assert history_tree(history, "initial") == git_tree(root, cli_git_dir)
    for step in STEPS:
        step(root)
        assert history_tree(history, step.__name__) == git_tree(root, cli_git_dir), step.__name__


def test_snapshot_skips_ignored_files_and_state_dir(tmp_path):
    build(tmp_path)
    history = History(tmp_path, tmp_path / ".codetools" / "history.git")
    commit = history.snapshot("initial")
    tree = pygit2.Repository(str(history.git_dir))[commit].peel(pygit2.Tree)
    names = {entry.name for entry in tree}
    assert {"a.py", "keep.log", "sub", "nested", ".gitignore"} <= names
    assert not {"x.log", "build", ".codetools"} & names
    assert tree["nested"].filemode == pygit2.enums.FileMode.COMMIT
    assert {entry.name for entry in tree / "sub"} == {".gitignore", "ok.txt", "deep"}


def test_unchanged_project_reuses_the_last_snapshot(tmp_path):
    build(tmp_path)
    history = History(tmp_path, tmp_path / ".codetools" / "history.git")
    first = history.snapshot("one")
    assert history.snapshot("two") == first
    write(tmp_path, "a.py", b"changed")
    assert history.snapshot("three") != first


def test_nested_repository_without_commits_is_left_out(tmp_path):
    write(tmp_path, "a.txt", b"a")
    pygit2.init_repository(str(tmp_path / "fresh"))
    write(tmp_path, "fresh/f.txt", b"f")
    history = History(tmp_path, tmp_path / ".codetools" / "history.git")
    commit = history.snapshot("one")
    tree = pygit2.Repository(str(history.git_dir))[commit].peel(pygit2.Tree)
    assert {entry.name for entry in tree} == {"a.txt"}


def test_restore_puts_back_every_kind_of_change(tmp_path):
    build(tmp_path)
    history = History(tmp_path, tmp_path / ".codetools" / "history.git")
    before = history.snapshot("before")
    step_edit_delete_add(tmp_path)
    after = history.snapshot("after")
    changes = history.changes(before, after)
    assert {(c.status, c.path) for c in changes} == {("M", "a.py"), ("D", "bin.dat"), ("A", "new/dir/n.txt")}
    history.restore(before, changes)
    assert (tmp_path / "a.py").read_bytes() == b"print(1)\r\n"
    assert (tmp_path / "bin.dat").read_bytes() == bytes(range(256))
    assert not (tmp_path / "new" / "dir" / "n.txt").exists()
    assert history.changes(before, history.snapshot("restored")) == []


def test_history_repo_config_and_no_dot_git_in_project(tmp_path):
    write(tmp_path, "a.txt", b"a")
    history = History(tmp_path, tmp_path / ".codetools" / "history.git")
    history.snapshot("one")
    assert not (tmp_path / ".git").exists()
    config = pygit2.Repository(str(history.git_dir)).config
    assert config.get_bool("core.bare") is False and config.get_bool("core.autocrlf") is False


def test_excluded_dirs_are_left_out_at_any_depth(tmp_path):
    write(tmp_path, "a.py", b"a")
    write(tmp_path, "node_modules/pkg/index.js", b"x")
    write(tmp_path, "web/node_modules/pkg/index.js", b"x")
    write(tmp_path, "web/app.js", b"x")
    history = History(tmp_path, tmp_path / ".codetools" / "history.git", ["node_modules"])
    assert sorted(history.files()) == ["a.py", "web/app.js"]
    commit = history.snapshot("one")
    tree = pygit2.Repository(str(history.git_dir))[commit].peel(pygit2.Tree)
    assert {entry.name for entry in tree} == {"a.py", "web"}
    assert {entry.name for entry in tree / "web"} == {"app.js"}


def test_changed_excluded_dirs_rewrite_the_exclude_file(tmp_path):
    write(tmp_path, "a.py", b"a")
    write(tmp_path, "venv/lib.py", b"x")
    git_dir = tmp_path / ".codetools" / "history.git"
    assert sorted(History(tmp_path, git_dir, ["venv"]).files()) == ["a.py"]
    assert sorted(History(tmp_path, git_dir).files()) == ["a.py", "venv/lib.py"]
    assert (git_dir / "info" / "exclude").read_text(encoding="utf-8") == "/.codetools/\n"
