"""Preflight of change ops against a Stage. An op either stages all of its effects or none of them."""

import difflib

from .fuzzy import LocateFailure, locate
from .ops import Hunk, Op, OpResult
from .paths import has_protected_part
from .stage import Stage
from .textfile import NotText, TextFile, plural
from .workspace import OpFailure


def preflight(op: Op, stage: Stage) -> OpResult:
    try:
        return _CHANGES[op.verb](op.args, stage)
    except OpFailure as e:
        return OpResult(False, str(e))
    except OSError as e:
        return OpResult(False, f"{type(e).__name__}: {e}")


def _check_target(stage: Stage, rel: str) -> None:
    stage.ws.abs(rel)
    parts = rel.split("/")
    for k in range(1, len(parts)):
        ancestor = "/".join(parts[:k])
        if stage.get(ancestor) is not None:
            raise OpFailure(f"{ancestor} is a file, so {rel} can't exist under it")


def _missing(stage: Stage, rel: str) -> OpFailure:
    if stage.is_dir(rel):
        return OpFailure(f"{rel} is a directory")
    return OpFailure(f"{rel} does not exist{stage.ws.suggest(rel)}")


def _decode(rel: str, data: bytes) -> TextFile:
    try:
        return TextFile.decode(data)
    except NotText as e:
        raise OpFailure(f"{rel}: {e}") from None


def _apply_hunks(tf: TextFile, hunks: list[Hunk], label: str) -> TextFile:
    lines = list(tf.lines)
    total = len(hunks)
    for k, hunk in enumerate(hunks, 1):
        where = f"hunk {k} of {total}: " if total > 1 else ""
        try:
            loc = locate(lines, hunk.search, label)
        except LocateFailure as e:
            raise _HunkFailure(where + e.message, e.detail) from None
        lines[loc.start:loc.end] = _keep_unchanged(lines[loc.start:loc.end], hunk.search, hunk.replace)
    return tf.with_lines(lines)


def _keep_unchanged(original: list[str], search: list[str], replace: list[str]) -> list[str]:
    """REPLACE lines, except that lines REPLACE leaves unchanged keep the file's exact text.

    SEARCH matching ignores trailing whitespace, so the file's own lines are kept wherever REPLACE repeats a
    SEARCH line; trailing whitespace (such as a Markdown line break) survives unless the line itself changes.
    """
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, [s.rstrip() for s in search], [r.rstrip() for r in replace],
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        out.extend(original[i1:i2] if tag == "equal" else replace[j1:j2])
    return out


class _HunkFailure(Exception):
    def __init__(self, message: str, detail: str):
        super().__init__(message)
        self.message = message
        self.detail = detail


def _staged_note(stage: Stage, rel: str, detail: str) -> list[str]:
    if detail and rel in stage.files:
        return ["line numbers include the changes of earlier ops in this batch"]
    return []


def _edit(a: dict, stage: Stage) -> OpResult:
    rel = a["path"]
    stage.ws.guard_secret(rel)
    data = stage.get(rel)
    if data is None:
        raise _missing(stage, rel)
    tf = _decode(rel, data)
    notes = [tf.note] if tf.note else []
    try:
        new = _apply_hunks(tf, a["hunks"], rel)
    except _HunkFailure as e:
        return OpResult(False, e.message, e.detail, "diff", _staged_note(stage, rel, e.detail))
    if new.lines == tf.lines:
        notes.append("no effect: the replacement text equals the current text")
    stage.commit([(rel, new.encode())])
    return OpResult(True, f"{plural(len(a['hunks']), 'hunk')} located", notes=notes)


def _write(a: dict, stage: Stage) -> OpResult:
    rel = a["path"]
    stage.ws.guard_secret(rel)
    _check_target(stage, rel)
    if stage.get(rel) is not None:
        raise OpFailure(f"{rel} already exists; use edit, or overwrite to replace it entirely")
    if stage.is_dir(rel):
        raise OpFailure(f"{rel} is a directory")
    content = a["content"]
    stage.commit([(rel, TextFile(lines=list(content)).encode())])
    return OpResult(True, f"creates {rel} ({plural(len(content), 'line')})")


def _overwrite(a: dict, stage: Stage) -> OpResult:
    rel = a["path"]
    stage.ws.guard_secret(rel)
    data = stage.get(rel)
    if data is None:
        raise _missing(stage, rel) if stage.is_dir(rel) else OpFailure(f"{rel} does not exist; use write for new files")
    try:
        old = TextFile.decode(data)
    except NotText:
        old = TextFile()
    new = old.with_lines(a["content"])
    stage.commit([(rel, new.encode())])
    return OpResult(True, f"replaces {len(old.lines)} lines with {len(new.lines)}")


def _patch(a: dict, stage: Stage) -> OpResult:
    local: dict[str, bytes | None] = {}
    origins = {}

    def get(rel: str) -> bytes | None:
        return local[rel] if rel in local else stage.get(rel)

    for fp in a["files"]:
        for rel in (fp.old_path, fp.new_path):
            if rel:
                stage.ws.guard_secret(rel)
                _check_target(stage, rel)
        if fp.old_path is None:
            rel = fp.new_path
            if get(rel) is not None or stage.is_dir(rel):
                raise OpFailure(f"{rel} already exists, but the patch creates it")
            local[rel] = TextFile(lines=[line for h in fp.hunks for line in h.replace]).encode()
            continue
        data = get(fp.old_path)
        if data is None:
            raise _missing(stage, fp.old_path)
        if fp.new_path is None:
            local[fp.old_path] = None
            continue
        dst = fp.new_path
        if dst != fp.old_path:
            if get(dst) is not None or stage.is_dir(dst):
                raise OpFailure(f"{dst} already exists, but the patch renames {fp.old_path} to it")
            local[fp.old_path] = None
            origins[dst] = f"renamed from {fp.old_path}"
        tf = _decode(fp.old_path, data)
        try:
            new = _apply_hunks(tf, fp.hunks, fp.old_path)
        except _HunkFailure as e:
            return OpResult(False, f"{fp.old_path}: {e.message}", e.detail, "diff", _staged_note(stage, fp.old_path, e.detail))
        local[dst] = new.encode()
    stage.commit(list(local.items()), origins=origins)
    return OpResult(True, f"{plural(len(a['files']), 'file section')} located")


def _move_or_copy(a: dict, stage: Stage, move: bool) -> OpResult:
    src, dst = a["src"], a["dst"]
    _check_target(stage, dst)
    verb = "moved" if move else "copied"
    if src == dst:
        raise OpFailure("SRC and DST are the same path")
    if stage.exists(dst):
        raise OpFailure(f"{dst} already exists; name the full destination path, which must be free")
    data = stage.get(src)
    if data is not None:
        changes = [(dst, data)] + ([(src, None)] if move else [])
        stage.commit(changes, origins={dst: f"{verb} from {src}"})
        return OpResult(True, f"{'moves' if move else 'copies'} 1 file")
    if not stage.is_dir(src):
        raise _missing(stage, src)
    if dst.startswith(src + "/"):
        raise OpFailure(f"can't put {src} inside itself")
    files = stage.files_under(src)
    if not files:
        raise OpFailure(f"{src} is an empty directory; use mkdir for {dst}")
    if any(has_protected_part(f) for f in files):
        raise OpFailure(f"{src} contains a protected directory")
    changes = [(dst + f[len(src):], stage.get(f)) for f in files]
    origins = {dst + f[len(src):]: f"{verb} from {f}" for f in files}
    if move:
        changes += [(f, None) for f in files]
    stage.commit(changes, rmdirs=(src,) if move else (), origins=origins)
    return OpResult(True, f"{'moves' if move else 'copies'} {plural(len(files), 'file')}")


def _delete(a: dict, stage: Stage) -> OpResult:
    rel = a["path"]
    if stage.get(rel) is not None:
        stage.commit([(rel, None)])
        return OpResult(True, "deletes 1 file")
    if not stage.is_dir(rel):
        raise _missing(stage, rel)
    if not a["recursive"]:
        raise OpFailure(f"{rel} is a directory; add -r to delete it and everything in it")
    files = stage.files_under(rel)
    if any(has_protected_part(f) for f in files):
        raise OpFailure(f"{rel} contains a protected directory")
    stage.commit([(f, None) for f in files], rmdirs=(rel,))
    return OpResult(True, f"deletes the directory and {plural(len(files), 'file')}")


def _mkdir(a: dict, stage: Stage) -> OpResult:
    rel = a["path"]
    _check_target(stage, rel)
    if stage.get(rel) is not None:
        raise OpFailure(f"{rel} is a file")
    if stage.is_dir(rel):
        return OpResult(True, "already exists")
    stage.commit([], mkdirs=(rel,))
    return OpResult(True, "creates directory")


_CHANGES = {
    "edit": _edit,
    "write": _write,
    "overwrite": _overwrite,
    "patch": _patch,
    "move": lambda a, stage: _move_or_copy(a, stage, move=True),
    "copy": lambda a, stage: _move_or_copy(a, stage, move=False),
    "delete": _delete,
    "mkdir": _mkdir,
}
