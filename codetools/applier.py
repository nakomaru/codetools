"""Writing a Stage to disk, with rollback on failure."""

import os
from datetime import datetime
from pathlib import Path

from .stage import Stage
from .workspace import Workspace


class ApplyError(Exception):
    pass


def timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def apply_stage(ws: Workspace, stage: Stage) -> None:
    """Write every staged change, restoring the files already written when one fails."""
    changed = stage.changed()
    done: list[str] = []
    deletes = [rel for rel, data in changed.items() if data is None]
    writes = [rel for rel, data in changed.items() if data is not None]
    try:
        for rel in deletes:
            ws.abs(rel).unlink(missing_ok=True)
            done.append(rel)
        prune(ws, deletes, stage.rmdirs)
        for rel in stage.mkdirs:
            ws.abs(rel).mkdir(parents=True, exist_ok=True)
        for rel in writes:
            path = ws.abs(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(changed[rel])
            done.append(rel)
    except OSError as e:
        _restore(ws, done, lambda rel: stage.original[rel])
        raise ApplyError(f"{type(e).__name__}: {e}; the {len(done)} file change(s) made were rolled back") from None


def _restore(ws: Workspace, rels: list[str], original) -> None:
    removed = []
    for rel in rels:
        data = original(rel)
        path = ws.abs(rel)
        if data is None:
            path.unlink(missing_ok=True)
            removed.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    prune(ws, removed, [])


def prune(ws: Workspace, deleted: list[str], rmdirs: list[str]) -> None:
    """Remove directories emptied by deletions, plus explicitly deleted directories that are now empty."""
    for rel in rmdirs:
        base = ws.abs(rel)
        if not base.is_dir():
            continue
        for dirpath, _, _ in os.walk(base, topdown=False):
            _rmdir_if_empty(Path(dirpath))
    for rel in deleted:
        parent = ws.abs(rel).parent
        while parent != ws.root and _rmdir_if_empty(parent):
            parent = parent.parent


def _rmdir_if_empty(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError:
        return False
