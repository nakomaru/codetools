"""Snapshots of the project in a private git repository, .codetools/history.git, whose work tree is the project.

Every applied batch gets two snapshots: one just before its changes and one after its commands finish. The
commit between them holds everything the batch did to files git sees, including what its commands did. Edits
made between batches land in their own commits. The project's own .gitignore rules apply, so ignored files are
never snapshotted. The project's real repository is never touched.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_CONFIG = {
    "core.bare": "false",
    "core.autocrlf": "false",
    "core.safecrlf": "false",
    "core.quotepath": "false",
    "core.longpaths": "true",
    "core.fsmonitor": "false",
    "commit.gpgsign": "false",
    "user.name": "codetools",
    "user.email": "codetools@localhost",
}
_CLEARED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")


class HistoryError(Exception):
    pass


@dataclass
class FileStatus:
    """A path that differs between two snapshots. status is git's letter: A added, M modified, D deleted, T
    type changed."""

    status: str
    path: str


class History:
    def __init__(self, root: Path, git_dir: Path):
        self.root = root
        self.git_dir = git_dir

    def snapshot(self, message: str) -> str:
        """Commit the project as it is now and return the commit id. When nothing changed since the last
        snapshot, returns that snapshot's id without committing."""
        self._ensure()
        self._git("add", "--all", "--", ".")
        head = self._head()
        if head is not None and self._git("diff", "--cached", "--quiet", "HEAD", check=False).returncode == 0:
            return head
        self._git("commit", "--quiet", "--no-verify", "--allow-empty", "--file=-", input=message.encode("utf-8"))
        return self._head()

    def changes(self, old: str, new: str) -> list[FileStatus]:
        out = self._git("diff", "--name-status", "--no-renames", "-z", old, new).stdout.decode("utf-8")
        fields = out.split("\0")
        return [FileStatus(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]

    def restore(self, commit: str, files: list[FileStatus]) -> None:
        """Put each file back as it was in commit: files absent there are deleted, the rest checked out."""
        present = [f.path for f in files if f.status != "A"]
        if present:
            self._git("checkout", commit, "--pathspec-from-file=-", "--pathspec-file-nul",
                      input="\0".join(present).encode("utf-8"))
        for f in files:
            if f.status == "A":
                (self.root / f.path).unlink(missing_ok=True)

    def _head(self) -> str | None:
        result = self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def _ensure(self) -> None:
        if (self.git_dir / "HEAD").is_file():
            return
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self._git("init", "--quiet")
        self._git("config", "--unset", "core.worktree", check=False)
        for key, value in _CONFIG.items():
            self._git("config", key, value)
        self._git("config", "core.hooksPath", str(self.git_dir / "hooks"))
        (self.git_dir / "info").mkdir(exist_ok=True)
        (self.git_dir / "info" / "exclude").write_text("/.codetools/\n", encoding="utf-8")

    def _git(self, *args: str, input: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in _CLEARED_ENV}
        env["GIT_TERMINAL_PROMPT"] = "0"
        cmd = ["git", "--literal-pathspecs", f"--git-dir={self.git_dir}", f"--work-tree={self.root}", *args]
        try:
            result = subprocess.run(cmd, cwd=self.root, env=env, input=input, capture_output=True)
        except OSError as e:
            raise HistoryError(f"can't run git ({e}); install Git for Windows") from None
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip() or f"exit {result.returncode}"
            raise HistoryError(f"git {args[0]} failed in {self.git_dir}: {detail}")
        return result
