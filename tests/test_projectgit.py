import shutil
import subprocess
from pathlib import Path

import pygit2
import pytest

from codetools.projectgit import open_repo

GIT = shutil.which("git")


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = pygit2.init_repository(str(tmp_path), initial_head="master")
    write(tmp_path, ".gitignore", "*.log\n")
    write(tmp_path, "app/main.py", "x = 1\n")
    write(tmp_path, "app/tracked.log", "tracked before the ignore rule\n")
    write(tmp_path, "top.txt", "top\n")
    repo.index.add_all()
    repo.index.add("app/tracked.log")
    repo.index.write()
    signature = pygit2.Signature("t", "t@localhost")
    repo.create_commit("HEAD", signature, signature, "First commit\n\nBody.", repo.index.write_tree(), [])
    write(tmp_path, "app/main.py", "x = 2\n")
    write(tmp_path, "app/new/file.py", "new\n")
    write(tmp_path, "app/skip.log", "ignored\n")
    (tmp_path / "top.txt").unlink()
    return tmp_path.resolve()


def test_no_repository(tmp_path):
    assert open_repo(tmp_path.resolve()) is None


def test_files_at_the_work_tree_root(repo_root):
    files = sorted(open_repo(repo_root).files())
    assert files == [".gitignore", "app/main.py", "app/new/file.py", "app/tracked.log"]


def test_files_from_a_subdirectory(repo_root):
    assert sorted(open_repo(repo_root / "app").files()) == ["main.py", "new/file.py", "tracked.log"]


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize("sub", [".", "app"])
def test_files_match_git_ls_files(repo_root, sub):
    root = (repo_root / sub).resolve()
    out = subprocess.run([GIT, "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    expected = sorted(n for n in set(out) if n and (root / n).is_file())
    assert sorted(open_repo(root).files()) == expected


def test_summary(repo_root):
    lines = open_repo(repo_root).summary(max_status_lines=50, log_count=8).splitlines()
    assert lines[:2] == ["Status:", "## master"]
    assert " M app/main.py" in lines and "?? app/new/" in lines and " D top.txt" in lines
    assert not any("skip.log" in line for line in lines)
    assert lines[-2:] == ["Recent commits:", lines[-1]] and lines[-1].endswith(" First commit")


@pytest.mark.skipif(GIT is None, reason="git is not installed")
@pytest.mark.parametrize("sub", [".", "app"])
def test_summary_changes_match_git_status_short(repo_root, sub):
    root = (repo_root / sub).resolve()
    status = subprocess.run([GIT, "-C", str(root), "status", "--short"], capture_output=True, check=True)
    expected = status.stdout.decode("utf-8").splitlines()
    lines = open_repo(root).summary(max_status_lines=50, log_count=8).splitlines()
    assert sorted(lines[2:2 + len(expected)]) == sorted(expected)


def test_summary_before_the_first_commit(tmp_path):
    pygit2.init_repository(str(tmp_path), initial_head="trunk")
    write(tmp_path, "a.txt", "a\n")
    lines = open_repo(tmp_path.resolve()).summary(max_status_lines=50, log_count=8).splitlines()
    assert lines[1] == "## No commits yet on trunk" and "?? a.txt" in lines and lines[-1] == "(no commits)"
