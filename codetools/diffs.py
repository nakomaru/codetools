import difflib
from dataclasses import dataclass

from .stage import Stage
from .textfile import NotText, TextFile


@dataclass
class FileChange:
    path: str
    status: str
    added: int
    removed: int
    origin: str
    diff: str

    def describe(self) -> str:
        counts = f" (+{self.added} -{self.removed})" if self.added or self.removed else ""
        origin = f" [{self.origin}]" if self.origin else ""
        return f"{self.status} {self.path}{counts}{origin}"


def file_changes(stage: Stage) -> list[FileChange]:
    out = []
    for rel, data in stage.changed().items():
        original = stage.original[rel]
        status = "A" if original is None else "D" if data is None else "M"
        old, new = _lines(original), _lines(data)
        if old is None or new is None:
            out.append(FileChange(rel, status, 0, 0, stage.origins.get(rel, ""), "(binary content)"))
            continue
        added = removed = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                removed += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
        diff = "\n".join(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
        out.append(FileChange(rel, status, added, removed, stage.origins.get(rel, ""), diff))
    return out


def _lines(data: bytes | None) -> list[str] | None:
    if data is None:
        return []
    try:
        return TextFile.decode(data).lines
    except NotText:
        return None
