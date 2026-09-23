import fnmatch
import os
import subprocess
from pathlib import Path

from . import config
from .paths import has_protected_part
from .settings import Settings


class OpFailure(Exception):
    pass


class Workspace:
    def __init__(self, root: Path | str, settings: Settings | None = None):
        self.root = Path(root).resolve()
        self.state_dir = self.root / ".codetools"
        self.settings = settings or Settings()
        self._secret_patterns = (*config.SECRET_PATTERNS, *(p.lower() for p in self.settings.extra_secret_patterns))
        self.git = self._inside_git_work_tree()
        self._listing: list[str] | None = None

    def run_git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True)

    def _inside_git_work_tree(self) -> bool:
        try:
            result = self.run_git("rev-parse", "--is-inside-work-tree")
        except OSError:
            return False
        return result.returncode == 0 and result.stdout.strip() == b"true"

    def abs(self, rel: str) -> Path:
        """Absolute path for a normalized relative path, refusing anything that resolves outside the root."""
        if rel == ".":
            return self.root
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            raise OpFailure(f"{rel} resolves outside the project root")
        return path

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def is_secret(self, rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1].lower()
        if name in config.SECRET_EXCEPTIONS:
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self._secret_patterns)

    def guard_secret(self, rel: str) -> None:
        if self.is_secret(rel):
            raise OpFailure(f"{rel} matches a secret-file pattern; its contents are off-limits")

    def invalidate(self) -> None:
        self._listing = None

    def list_files(self) -> list[str]:
        """Every non-ignored file in the project (git's view when inside a work tree), sorted."""
        if self._listing is None:
            files = self._git_listing() if self.git else self._walk_listing()
            self._listing = sorted(f for f in files if not has_protected_part(f))
        return self._listing

    def files_under(self, base: str) -> list[str]:
        files = self.list_files()
        if base == ".":
            return files
        prefix = base + "/"
        return [f for f in files if f.startswith(prefix)]

    def suggest(self, rel: str) -> str:
        """A hint naming listed files with the same basename as a path that doesn't exist."""
        name = rel.rsplit("/", 1)[-1].lower()
        matches = [f for f in self.list_files() if f.rsplit("/", 1)[-1].lower() == name][:5]
        return f"; did you mean {', '.join(matches)}?" if matches else ""

    def _git_listing(self) -> list[str]:
        result = self.run_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        names = result.stdout.decode("utf-8", errors="replace").split("\0")
        return [n for n in dict.fromkeys(names) if n and (self.root / n).is_file()]

    def _walk_listing(self) -> list[str]:
        files = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in config.WALK_SKIP_DIRS]
            base = Path(dirpath)
            files.extend(self.rel(base / name) for name in filenames)
        return files
