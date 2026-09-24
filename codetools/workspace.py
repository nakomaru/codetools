import fnmatch
from pathlib import Path

from . import config
from .history import History
from .paths import has_protected_part
from .projectgit import open_repo
from .settings import Settings


class OpFailure(Exception):
    pass


class Workspace:
    def __init__(self, root: Path | str, settings: Settings | None = None):
        self.root = Path(root).resolve()
        self.state_dir = self.root / ".codetools"
        self.settings = settings or Settings()
        self._secret_patterns = (*config.SECRET_PATTERNS, *(p.lower() for p in self.settings.extra_secret_patterns))
        self.repo = open_repo(self.root)
        self.git = self.repo is not None
        self.history = History(self.root, self.state_dir / "history.git", () if self.git else config.WALK_SKIP_DIRS)
        self._listing: list[str] | None = None

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
        """Every non-ignored file in the project, sorted: the project repository's view inside a git work tree,
        and the snapshot history's view outside one."""
        if self._listing is None:
            files = self.repo.files() if self.repo else self.history.files()
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
