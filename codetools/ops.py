from dataclasses import dataclass, field
from typing import Any

QUERY_VERBS = frozenset({"read", "grep", "find", "tree", "outline"})
CHANGE_VERBS = frozenset({"edit", "write", "overwrite", "patch", "move", "copy", "delete", "mkdir"})
COMMAND_VERBS = frozenset({"run"})
MESSAGE_VERB = "message"
BODY_VERBS = frozenset({"edit", "write", "overwrite", "patch", MESSAGE_VERB})
ALL_VERBS = QUERY_VERBS | CHANGE_VERBS | COMMAND_VERBS | {MESSAGE_VERB}


@dataclass
class Hunk:
    search: list[str]
    replace: list[str]


@dataclass
class FilePatch:
    """One file section of a unified diff. old_path None creates the file, new_path None deletes it."""

    old_path: str | None
    new_path: str | None
    hunks: list[Hunk]


@dataclass
class Op:
    index: int
    verb: str
    line: int
    title: str
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.verb in QUERY_VERBS:
            return "query"
        if self.verb in COMMAND_VERBS:
            return "command"
        return "change"

    def touched_paths(self) -> list[str]:
        a = self.args
        if self.verb == "patch":
            return [p for fp in a["files"] for p in (fp.old_path, fp.new_path) if p]
        if self.verb in ("move", "copy"):
            return [a["src"], a["dst"]]
        return [a["path"]] if "path" in a else []


@dataclass
class OpResult:
    ok: bool
    summary: str
    detail: str = ""
    detail_lang: str = ""
    notes: list[str] = field(default_factory=list)
