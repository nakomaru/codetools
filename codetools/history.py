"""Snapshots of the project in a private git repository, .codetools/history.git, whose work tree is the project.

Every applied batch gets two snapshots: one just before its changes and one after its commands finish. The
commit between them holds everything the batch did to files git sees, including what its commands did. Edits
made between batches land in their own commits. The project's own .gitignore rules apply, so ignored files are
never snapshotted, and neither are the excluded directories a History is given. The project's real repository is
never touched.

Runs on libgit2 through pygit2, so no git installation is needed. A snapshot records the same tree `git add
--all` would: a nested repository becomes a gitlink to its HEAD commit, and tracked files stay tracked after
they become ignored.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pygit2
from pygit2.enums import CheckoutStrategy, FileMode, FileStatus as Status

from .projectgit import work_tree_files

_CONFIG = {
    "core.bare": False,
    "core.autocrlf": False,
    "core.safecrlf": False,
    "core.quotepath": False,
    "core.longpaths": True,
}
_SIGNATURE = ("codetools", "codetools@localhost")
_WORKTREE_CHANGE = Status.WT_NEW | Status.WT_MODIFIED | Status.WT_DELETED | Status.WT_TYPECHANGE | Status.WT_RENAMED


class HistoryError(Exception):
    pass


@dataclass
class FileStatus:
    """A path that differs between two snapshots. status is git's letter: A added, M modified, D deleted, T
    type changed."""

    status: str
    path: str


class History:
    def __init__(self, root: Path, git_dir: Path, excluded_dirs: Iterable[str] = ()):
        """excluded_dirs are directory names left out wherever they appear, unless already snapshotted."""
        self.root = root
        self.git_dir = git_dir
        self._exclude = "/.codetools/\n" + "".join(f"{name}/\n" for name in sorted(excluded_dirs))

    def files(self) -> list[str]:
        """Every file the next snapshot would hold, relative to the project root."""
        try:
            return work_tree_files(self._repo(), self.root)
        except (pygit2.GitError, OSError) as e:
            raise HistoryError(f"can't list files through {self.git_dir}: {e}") from None

    def snapshot(self, message: str) -> str:
        """Commit the project as it is now and return the commit id. When nothing changed since the last
        snapshot, returns that snapshot's id without committing."""
        try:
            repo = self._repo()
            tree = self._stage_all(repo)
            head = None if repo.head_is_unborn else repo.head.peel(pygit2.Commit)
            if head is not None and head.tree_id == tree:
                return str(head.id)
            signature = pygit2.Signature(*_SIGNATURE)
            text = message if message.endswith("\n") else message + "\n"
            return str(repo.create_commit("HEAD", signature, signature, text, tree, [head.id] if head else []))
        except (pygit2.GitError, OSError) as e:
            raise HistoryError(f"snapshot failed in {self.git_dir}: {e}") from None

    def changes(self, old: str, new: str) -> list[FileStatus]:
        try:
            repo = self._repo()
            diff = repo.diff(repo[old].peel(pygit2.Tree), repo[new].peel(pygit2.Tree))
        except (pygit2.GitError, KeyError, ValueError) as e:
            raise HistoryError(f"can't compare snapshots {old[:10]} and {new[:10]}: {e}") from None
        return [FileStatus(d.status_char(), d.new_file.path) for d in diff.deltas]

    def restore(self, commit: str, files: list[FileStatus]) -> None:
        """Put each file back as it was in commit: files absent there are deleted, the rest checked out."""
        present = [f.path for f in files if f.status != "A"]
        if present:
            try:
                repo = self._repo()
                repo.checkout_tree(repo[commit].peel(pygit2.Tree), paths=present,
                                   strategy=CheckoutStrategy.FORCE | CheckoutStrategy.DISABLE_PATHSPEC_MATCH)
            except (pygit2.GitError, KeyError, OSError) as e:
                raise HistoryError(f"restore from {commit[:10]} failed: {e}") from None
        for f in files:
            if f.status == "A":
                (self.root / f.path).unlink(missing_ok=True)

    def _stage_all(self, repo: pygit2.Repository) -> pygit2.Oid:
        """Bring the index up to date with the work tree and write it as a tree."""
        index = repo.index
        for path, flags in repo.status(untracked_files="all").items():
            if not flags & _WORKTREE_CHANGE:
                continue
            if flags & Status.WT_DELETED:
                index.remove(path)
            elif path.endswith("/"):
                path = path[:-1]
                nested = pygit2.Repository(str(self.root / path))
                if nested.head_is_unborn:
                    continue
                index.add(pygit2.IndexEntry(path, nested.head.target, FileMode.COMMIT))
            else:
                index.add(path)
        index.write()
        return index.write_tree()

    def _repo(self) -> pygit2.Repository:
        if not (self.git_dir / "HEAD").is_file():
            self._create()
        exclude = self.git_dir / "info" / "exclude"
        if not exclude.is_file() or exclude.read_text(encoding="utf-8") != self._exclude:
            exclude.parent.mkdir(exist_ok=True)
            exclude.write_text(self._exclude, encoding="utf-8")
        repo = pygit2.Repository(str(self.git_dir))
        repo.workdir = str(self.root)
        return repo

    def _create(self) -> None:
        """A bare init followed by core.bare=false: git's configuration for a separate git dir, without the .git
        file a non-bare init would write into the project."""
        self.git_dir.mkdir(parents=True, exist_ok=True)
        repo = pygit2.init_repository(str(self.git_dir), bare=True)
        for key, value in _CONFIG.items():
            repo.config[key] = value
