"""Writing a Stage to disk with backups, rollback on failure, and undo from the saved manifest."""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from .stage import Stage
from .workspace import Workspace


class ApplyError(Exception):
    pass


def sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def apply_stage(ws: Workspace, stage: Stage, batch_dir: Path) -> dict:
    changed = stage.changed()
    before_dir = batch_dir / "before"
    entries = []
    for rel, data in changed.items():
        original = stage.original[rel]
        if original is not None:
            backup = before_dir / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(original)
        entries.append({"path": rel, "existed": original is not None, "after": sha(data)})

    done: list[str] = []
    deletes = [rel for rel, data in changed.items() if data is None]
    writes = [rel for rel, data in changed.items() if data is not None]
    try:
        for rel in deletes:
            ws.abs(rel).unlink(missing_ok=True)
            done.append(rel)
        _prune(ws, deletes, stage.rmdirs)
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

    manifest = {"applied_at": timestamp(), "undone": False, "entries": entries}
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def undo(ws: Workspace, batch_dir: Path) -> list[tuple[str, bool]]:
    """Each file the batch changed, with whether it existed before the batch."""
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ApplyError("that batch was never applied")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["undone"]:
        raise ApplyError("that batch was already undone")
    entries = manifest["entries"]
    conflicts = [e["path"] for e in entries if sha(_read(ws, e["path"])) != e["after"]]
    if conflicts:
        raise ApplyError(f"files changed after the batch was applied: {', '.join(conflicts)}")

    def original(rel: str) -> bytes | None:
        entry = next(e for e in entries if e["path"] == rel)
        return (batch_dir / "before" / rel).read_bytes() if entry["existed"] else None

    _restore(ws, [e["path"] for e in entries], original)
    manifest["undone"] = True
    manifest["undone_at"] = timestamp()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return [(e["path"], e["existed"]) for e in entries]


def _read(ws: Workspace, rel: str) -> bytes | None:
    path = ws.abs(rel)
    return path.read_bytes() if path.is_file() else None


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
    _prune(ws, removed, [])


def _prune(ws: Workspace, deleted: list[str], rmdirs: list[str]) -> None:
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
