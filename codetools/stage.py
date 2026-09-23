import os
from pathlib import Path

from .workspace import Workspace


class Stage:
    """In-memory overlay of the project that change ops are preflighted against, in batch order.

    files maps a relative path to its staged bytes (None = deleted). original holds each touched path's disk
    bytes when it was first staged, which apply uses for backups and to detect files changed after preflight.
    """

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.files: dict[str, bytes | None] = {}
        self.original: dict[str, bytes | None] = {}
        self.mkdirs: list[str] = []
        self.rmdirs: list[str] = []
        self.origins: dict[str, str] = {}

    def _disk(self, rel: str) -> bytes | None:
        path = self.ws.abs(rel)
        return path.read_bytes() if path.is_file() else None

    def get(self, rel: str) -> bytes | None:
        return self.files[rel] if rel in self.files else self._disk(rel)

    def is_dir(self, rel: str) -> bool:
        if rel == "." or rel in self.mkdirs:
            return True
        prefix = rel + "/"
        if any(k.startswith(prefix) and v is not None for k, v in self.files.items()):
            return True
        return rel not in self.rmdirs and self.ws.abs(rel).is_dir()

    def exists(self, rel: str) -> bool:
        return self.get(rel) is not None or self.is_dir(rel)

    def files_under(self, rel: str) -> list[str]:
        """Every file under a directory, including ignored ones, as currently staged."""
        prefix = rel + "/"
        found = set()
        base = self.ws.abs(rel)
        if base.is_dir() and rel not in self.rmdirs:
            for dirpath, _, filenames in os.walk(base):
                found.update(self.ws.rel(Path(dirpath, name)) for name in filenames)
        for k, v in self.files.items():
            if k.startswith(prefix):
                if v is None:
                    found.discard(k)
                else:
                    found.add(k)
        return sorted(found)

    def commit(self, changes: list[tuple[str, bytes | None]], mkdirs: tuple[str, ...] = (),
               rmdirs: tuple[str, ...] = (), origins: dict[str, str] | None = None) -> None:
        for rel, data in changes:
            if rel not in self.original:
                self.original[rel] = self._disk(rel)
            self.files[rel] = data
        self.mkdirs.extend(d for d in mkdirs if d not in self.mkdirs)
        self.rmdirs.extend(d for d in rmdirs if d not in self.rmdirs)
        if origins:
            self.origins.update(origins)

    def changed(self) -> dict[str, bytes | None]:
        return {rel: data for rel, data in sorted(self.files.items()) if data != self.original[rel]}

    def stale(self) -> list[str]:
        return [rel for rel, data in sorted(self.original.items()) if self._disk(rel) != data]
