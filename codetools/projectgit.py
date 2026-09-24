"""Read-only views of the project's own git repository, through pygit2, so no git installation is needed."""

import os
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import pygit2
from pygit2.enums import FileStatus as Status

_INDEX_LETTERS = ((Status.INDEX_NEW, "A"), (Status.INDEX_MODIFIED, "M"), (Status.INDEX_DELETED, "D"),
                  (Status.INDEX_RENAMED, "R"), (Status.INDEX_TYPECHANGE, "T"))
_WORKTREE_LETTERS = ((Status.WT_MODIFIED, "M"), (Status.WT_DELETED, "D"), (Status.WT_RENAMED, "R"),
                     (Status.WT_TYPECHANGE, "T"))


@dataclass
class ProjectRepo:
    repo: pygit2.Repository
    root: Path
    prefix: str
    """The project root's path inside the work tree, with a trailing slash, or empty when they are the same."""

    def files(self) -> list[str]:
        return work_tree_files(self.repo, self.root, self.prefix)

    def summary(self, max_status_lines: int, log_count: int) -> str:
        """The branch, uncommitted changes, and recent commits, in the formats of `git status --short --branch`
        and `git log --oneline`."""
        changes = [f"{_letters(flags)} {self._display(path)}"
                   for path, flags in sorted(self.repo.status(untracked_files="normal").items())
                   if flags != Status.CURRENT and not flags & Status.IGNORED]
        if len(changes) > max_status_lines:
            changes = changes[:max_status_lines] + [f"... {len(changes) - max_status_lines} more changed paths"]
        commits = [] if self.repo.head_is_unborn else islice(self.repo.walk(self.repo.head.target), log_count)
        log = [f"{str(c.id)[:7]} {c.message.splitlines()[0] if c.message.strip() else ''}" for c in commits]
        return "\n".join(["Status:", self._branch(), *changes, "", "Recent commits:", *(log or ["(no commits)"])])

    def _branch(self) -> str:
        if self.repo.head_is_detached:
            return "## HEAD (no branch)"
        name = self.repo.references["HEAD"].target.removeprefix("refs/heads/")
        if self.repo.head_is_unborn:
            return f"## No commits yet on {name}"
        branch = self.repo.branches.local.get(name)
        try:
            upstream = branch.upstream if branch else None
        except pygit2.GitError:
            upstream = None
        if upstream is None:
            return f"## {name}"
        ahead, behind = self.repo.ahead_behind(branch.target, upstream.target)
        counts = ", ".join(c for c in (f"ahead {ahead}" if ahead else "", f"behind {behind}" if behind else "") if c)
        return f"## {name}...{upstream.shorthand}" + (f" [{counts}]" if counts else "")

    def _display(self, path: str) -> str:
        """A work tree path relative to the project root, as git shows it from there."""
        if path.startswith(self.prefix):
            return path[len(self.prefix):]
        return Path(os.path.relpath(Path(self.repo.workdir) / path, self.root)).as_posix() + (
            "/" if path.endswith("/") else "")


def work_tree_files(repo: pygit2.Repository, root: Path, prefix: str = "") -> list[str]:
    """Tracked files plus untracked ones that aren't ignored, relative to root, which is prefix inside the work
    tree: what `git ls-files --cached --others --exclude-standard` lists."""
    index = repo.index
    index.read(force=False)
    names = {entry.path for entry in index}
    names.update(path for path, flags in repo.status(untracked_files="all").items() if flags & Status.WT_NEW)
    cut = len(prefix)
    return [name[cut:] for name in names if name.startswith(prefix) and (root / name[cut:]).is_file()]


def open_repo(root: Path) -> ProjectRepo | None:
    """The git repository whose work tree contains root, or None when there is none."""
    try:
        found = pygit2.discover_repository(str(root))
        repo = pygit2.Repository(found) if found else None
    except pygit2.GitError:
        return None
    if repo is None or repo.is_bare or not repo.workdir:
        return None
    workdir = Path(repo.workdir).resolve()
    if root != workdir and workdir not in root.parents:
        return None
    return ProjectRepo(repo, root, "" if root == workdir else root.relative_to(workdir).as_posix() + "/")


def _letters(flags: int) -> str:
    if flags & Status.CONFLICTED:
        return "UU"
    if flags == Status.WT_NEW:
        return "??"
    x = next((letter for bit, letter in _INDEX_LETTERS if flags & bit), " ")
    y = next((letter for bit, letter in _WORKTREE_LETTERS if flags & bit), " ")
    return x + y
