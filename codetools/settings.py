"""Per-project settings and pins, kept in .codetools/ beside the batch history.

settings.yaml and pins.txt are meant to be committed; .codetools/.gitignore keeps the batch history and log out
of git.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .commands import SHELL_CHOICES

SETTINGS_TEMPLATE = """\
# codetools settings for this project. Read when ct starts; restart ct after editing.

context:
  # What the `context` command copies for the start of a chat.
  include_protocol: true    # the batch format instructions
  include_git: true         # branch, uncommitted changes, recent commits
  tree_depth: 4             # directory levels shown in the project tree
  tree_line_counts: true    # line count beside each file in trees
  outline: []               # files, directories, or globs whose outlines (classes, functions, headings) are included

clipboard:
  fold: true                # wrap the body of every copy in a code fence, so chat UIs can collapse it

commands:
  timeout_seconds: 300      # a run op is killed, with everything it started, after this long
  shell: auto               # auto (bash or Git Bash, then pwsh, then Windows PowerShell), bash, or powershell

secrets:
  # Basename patterns added to the built-in list (.env, .env.*, *.pem, *.key, id_rsa*, ...).
  # Matching files are never read, grepped, or edited.
  extra_patterns: []
"""

PINS_TEMPLATE = """\
# Files the `context` command includes in full, one path or glob per line.
# Manage with `pin PATH` and `unpin PATH`, or edit this file.
"""

GITIGNORE = "batches/\nlog.txt\nhistory.git/\n"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class SettingsError(Exception):
    pass


@dataclass
class Settings:
    include_protocol: bool = True
    include_git: bool = True
    tree_depth: int = 4
    tree_line_counts: bool = True
    outline: list[str] = field(default_factory=list)
    fold: bool = True
    timeout_seconds: float = 300
    shell: str = "auto"
    extra_secret_patterns: list[str] = field(default_factory=list)


class ProjectState:
    """The .codetools directory of one project."""

    def __init__(self, state_dir: Path):
        self.dir = state_dir
        self.settings_path = state_dir / "settings.yaml"
        self.pins_path = state_dir / "pins.txt"
        self.notes_path = state_dir / "notes.md"

    def ensure(self) -> list[str]:
        """Create any missing state files from templates; returns the names created."""
        self.dir.mkdir(parents=True, exist_ok=True)
        created = []
        for path, text in ((self.settings_path, SETTINGS_TEMPLATE), (self.pins_path, PINS_TEMPLATE),
                           (self.dir / ".gitignore", GITIGNORE)):
            if not path.exists():
                path.write_text(text, encoding="utf-8", newline="\n")
                created.append(path.name)
        self._complete_gitignore()
        return created

    def _complete_gitignore(self) -> None:
        """Add any entry of GITIGNORE missing from an existing .codetools/.gitignore."""
        path = self.dir / ".gitignore"
        text = path.read_text(encoding="utf-8")
        present = {line.strip() for line in text.splitlines()}
        missing = [entry for entry in GITIGNORE.splitlines() if entry not in present]
        if missing:
            path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "\n".join(missing) + "\n",
                            encoding="utf-8", newline="\n")

    def load_settings(self) -> Settings:
        try:
            data = yaml.safe_load(self.settings_path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return Settings()
        except yaml.YAMLError as e:
            raise SettingsError(f"{self.settings_path}: {e}") from None
        s = Settings()
        try:
            if not isinstance(data, dict):
                raise TypeError("the top level must be a mapping of sections")
            context, commands, secrets, clip = (_section(data, name) for name in
                                                ("context", "commands", "secrets", "clipboard"))
            s.include_protocol = _typed(context, "include_protocol", bool, s.include_protocol)
            s.include_git = _typed(context, "include_git", bool, s.include_git)
            s.tree_depth = _typed(context, "tree_depth", int, s.tree_depth)
            s.tree_line_counts = _typed(context, "tree_line_counts", bool, s.tree_line_counts)
            s.outline = _typed(context, "outline", list, s.outline)
            s.fold = _typed(clip, "fold", bool, s.fold)
            s.timeout_seconds = _typed(commands, "timeout_seconds", (int, float), s.timeout_seconds)
            s.shell = _typed(commands, "shell", str, s.shell)
            if s.shell not in SHELL_CHOICES:
                raise TypeError(f"commands.shell must be one of {', '.join(SHELL_CHOICES)}, not {s.shell!r}")
            s.extra_secret_patterns = _typed(secrets, "extra_patterns", list, s.extra_secret_patterns)
        except TypeError as e:
            raise SettingsError(f"{self.settings_path}: {e}") from None
        return s

    def pins(self) -> list[str]:
        if not self.pins_path.is_file():
            return []
        lines = self.pins_path.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]

    def add_pin(self, pin: str) -> bool:
        if pin in self.pins():
            return False
        text = self.pins_path.read_text(encoding="utf-8") if self.pins_path.is_file() else PINS_TEMPLATE
        if text and not text.endswith("\n"):
            text += "\n"
        self.pins_path.write_text(text + pin + "\n", encoding="utf-8", newline="\n")
        return True

    def remove_pin(self, pin: str) -> bool:
        if not self.pins_path.is_file():
            return False
        lines = self.pins_path.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if line.strip() != pin]
        if len(kept) == len(lines):
            return False
        self.pins_path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        return True

    def has_unread_notes(self) -> bool:
        """Whether .codetools/notes.md, which nothing reads, holds anything besides HTML comments."""
        if not self.notes_path.is_file():
            return False
        return bool(_COMMENT_RE.sub("", self.notes_path.read_text(encoding="utf-8")).strip())


def _section(data: dict, name: str) -> dict:
    section = data.get(name) or {}
    if not isinstance(section, dict):
        raise TypeError(f"{name} must be a mapping of settings")
    return section


def _typed(section: dict, key: str, kind, default):
    value = section.get(key, default)
    if isinstance(value, bool) and kind in (int, (int, float)):
        raise TypeError(f"{key} must be a number")
    if not isinstance(value, kind):
        raise TypeError(f"{key} has the wrong type ({type(value).__name__})")
    return value
