"""Read-only ops. They run as soon as a batch is ingested, against the files as they are on disk."""

from . import config, outline
from .globmatch import matches
from .ops import Op, OpResult
from .textfile import NotText, TextFile, plural
from .workspace import OpFailure, Workspace


def run_query(op: Op, ws: Workspace) -> OpResult:
    return run_query_args(op.verb, op.args, ws)


def load_text(ws: Workspace, rel: str) -> TextFile:
    path = ws.abs(rel)
    size = path.stat().st_size
    if size > config.MAX_TEXT_FILE_BYTES:
        raise OpFailure(f"{rel} is {size:,} bytes, over the {config.MAX_TEXT_FILE_BYTES:,}-byte text limit")
    try:
        return TextFile.decode(path.read_bytes())
    except NotText as e:
        raise OpFailure(f"{rel}: {e}") from None


def clip_line(line: str) -> str:
    limit = config.OUTPUT_LINE_MAX_CHARS
    return line if len(line) <= limit else f"{line[:limit]}... [+{len(line) - limit} chars]"


def _read(a: dict, ws: Workspace) -> OpResult:
    rel = a["path"]
    ws.guard_secret(rel)
    path = ws.abs(rel)
    if path.is_dir():
        raise OpFailure(f"{rel} is a directory; use `=== tree {rel}`")
    if not path.is_file():
        raise OpFailure(f"{rel} does not exist{ws.suggest(rel)}")
    tf = load_text(ws, rel)
    total = len(tf.lines)
    if total == 0:
        return OpResult(True, "empty file")
    start = a["start"] or 1
    if start > total:
        raise OpFailure(f"{rel} has only {total} lines")
    notes = []
    end = a["end"]
    if end is None:
        end = min(total, start - 1 + config.READ_MAX_LINES)
        if end < total:
            notes.append(f"output stops at {config.READ_MAX_LINES} lines; request `{rel}:{end + 1}-{total}` for the rest")
    end = min(end, total)
    body = "\n".join(f"{n:>5}| {tf.lines[n - 1]}" for n in range(start, end + 1))
    return OpResult(True, f"lines {start}-{end} of {total}", body, notes=notes)


def _grep(a: dict, ws: Workspace) -> OpResult:
    regex, context, glob = a["regex"], a["context"], a["glob"]
    files: list[str] = []
    missing = []
    for base in a["paths"]:
        path = ws.abs(base)
        if path.is_file():
            files.append(base)
        elif path.is_dir():
            files.extend(ws.files_under(base))
        else:
            missing.append(base)
    if missing and not files:
        raise OpFailure(f"no such path: {', '.join(missing)}")
    files = [f for f in dict.fromkeys(files) if not ws.is_secret(f) and (glob is None or matches(glob, f))]

    out: list[str] = []
    match_count = file_count = 0
    truncated = False
    for rel in files:
        try:
            lines = load_text(ws, rel).lines
        except (OpFailure, OSError):
            continue
        hits = [i for i, line in enumerate(lines) if regex.search(line)]
        if not hits:
            continue
        if match_count + len(hits) > config.GREP_MAX_MATCHES:
            hits = hits[:config.GREP_MAX_MATCHES - match_count]
            truncated = True
        file_count += 1
        match_count += len(hits)
        out.extend(_grep_lines(rel, lines, hits, context))
        if truncated:
            break
    notes = [f"no such path: {', '.join(missing)}"] if missing else []
    if truncated:
        notes.append(f"stopped at {config.GREP_MAX_MATCHES} matches; narrow the pattern, path, or glob")
    if not match_count:
        return OpResult(True, f"no matches in {plural(len(files), 'file')}", notes=notes)
    summary = f"{match_count}{'+' if truncated else ''} matches in {plural(file_count, 'file')}"
    return OpResult(True, summary, "\n".join(out), notes=notes)


def _grep_lines(rel: str, lines: list[str], hits: list[int], context: int) -> list[str]:
    if context == 0:
        return [f"{rel}:{i + 1}: {clip_line(lines[i])}" for i in hits]
    hit_set = set(hits)
    ranges: list[list[int]] = []
    for i in hits:
        lo, hi = max(0, i - context), min(len(lines) - 1, i + context)
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], hi)
        else:
            ranges.append([lo, hi])
    out = []
    for k, (lo, hi) in enumerate(ranges):
        if k:
            out.append("--")
        for i in range(lo, hi + 1):
            sep = ":" if i in hit_set else "-"
            out.append(f"{rel}{sep}{i + 1}{sep} {clip_line(lines[i])}")
    return out


def _find(a: dict, ws: Workspace) -> OpResult:
    base, glob = a["path"], a["glob"]
    if not ws.abs(base).is_dir():
        raise OpFailure(f"{base} is not a directory")
    files = ws.files_under(base)
    dirs = sorted({f.rsplit("/", i)[0] for f in files for i in range(1, f.count("/") + 1)})
    if base != ".":
        dirs = [d for d in dirs if d.startswith(base + "/")]
    found = [d + "/" for d in dirs if matches(glob, d)] + [f for f in files if matches(glob, f)]
    found.sort(key=lambda p: p.rstrip("/"))
    notes = []
    if len(found) > config.FIND_MAX_RESULTS:
        notes.append(f"showing the first {config.FIND_MAX_RESULTS} of {len(found)}; narrow the glob or path")
    return OpResult(True, plural(len(found), "match", "es"), "\n".join(found[:config.FIND_MAX_RESULTS]), notes=notes)


def _tree(a: dict, ws: Workspace) -> OpResult:
    base = a["path"]
    if not ws.abs(base).is_dir():
        raise OpFailure(f"{base} is not a directory")
    text, count, notes = render_tree(ws, base, a["depth"] or ws.settings.tree_depth)
    return OpResult(True, plural(count, "file"), text, notes=notes)


def render_tree(ws: Workspace, base: str, depth: int) -> tuple[str, int, list[str]]:
    """Indented tree of the listed files under base, collapsing directories deeper than depth."""
    files = ws.files_under(base)
    strip = 0 if base == "." else len(base) + 1
    root: dict = {}
    for f in files:
        node = root
        *dirs, name = f[strip:].split("/")
        for d in dirs:
            node = node.setdefault(d + "/", {})
        node[name] = f
    out = [base.rstrip("/") + "/"]
    _render_tree(ws, root, 1, depth, out)
    notes = []
    if len(out) > config.TREE_MAX_LINES:
        notes.append(f"showing the first {config.TREE_MAX_LINES} lines; tree a subdirectory or lower -d")
        out = out[:config.TREE_MAX_LINES]
    return "\n".join(out), len(files), notes


def _render_tree(ws: Workspace, node: dict, level: int, depth: int, out: list[str]) -> None:
    for name in sorted(node, key=lambda n: (not n.endswith("/"), n.lower())):
        child = node[name]
        indent = "  " * level
        if isinstance(child, str):
            out.append(indent + name + (_size_label(ws, child) if ws.settings.tree_line_counts else ""))
        elif level >= depth:
            out.append(f"{indent}{name} ({plural(_count_files(child), 'file')})")
        else:
            out.append(indent + name)
            _render_tree(ws, child, level + 1, depth, out)
        if len(out) > config.TREE_MAX_LINES:
            return


def _size_label(ws: Workspace, rel: str) -> str:
    path = ws.abs(rel)
    try:
        size = path.stat().st_size
        if size > config.MAX_TEXT_FILE_BYTES:
            return f" ({size:,} bytes)"
        data = path.read_bytes()
    except OSError:
        return ""
    if b"\x00" in data[:8192]:
        return f" (binary, {size:,} bytes)"
    count = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return f" ({plural(count, 'line')})"


def _outline(a: dict, ws: Workspace) -> OpResult:
    files: list[str] = []
    notes: list[str] = []
    for base in a["paths"]:
        path = ws.abs(base)
        if path.is_file():
            if ws.is_secret(base):
                raise OpFailure(f"{base} matches a secret-file pattern; its contents are off-limits")
            if not outline.supported(base):
                notes.append(f"{base}: no outline support for this file type; use grep or read")
                continue
            files.append(base)
        elif path.is_dir():
            files.extend(f for f in ws.files_under(base) if outline.supported(f) and not ws.is_secret(f))
        else:
            raise OpFailure(f"{base} does not exist{ws.suggest(base)}")
    text, entries, count, more_notes = outline_files(ws, list(dict.fromkeys(files)))
    notes += more_notes
    if not count:
        return OpResult(True, "no outlinable files", notes=notes)
    entry_word = "entry" if entries == 1 else "entries"
    return OpResult(True, f"{entries} {entry_word} in {plural(count, 'file')}", text, notes=notes)


def outline_files(ws: Workspace, rels: list[str]) -> tuple[str, int, int, list[str]]:
    """Outline text for files, with the entry count, the file count, and notes."""
    out: list[str] = []
    notes: list[str] = []
    entries = 0
    for rel in rels:
        try:
            lines = load_text(ws, rel).lines
        except (OpFailure, OSError) as e:
            notes.append(f"{rel}: {e}")
            continue
        found, note = outline.outline(rel, lines)
        if note:
            notes.append(f"{rel}: {note}")
        entries += len(found)
        out.append(f"{rel} ({plural(len(lines), 'line')})")
        out.extend(found or ["            (nothing to outline)"])
    shown, truncated = outline.limit(out)
    if truncated:
        notes.append(f"showing the first {len(shown)} lines; outline fewer files at a time")
    return "\n".join(shown), entries, len(rels), notes


def _count_files(node: dict) -> int:
    return sum(1 if isinstance(child, str) else _count_files(child) for child in node.values())


def read_whole(ws: Workspace, rel: str) -> OpResult:
    return run_query_args("read", {"path": rel, "start": None, "end": None}, ws)


def run_query_args(verb: str, args: dict, ws: Workspace) -> OpResult:
    try:
        return _QUERIES[verb](args, ws)
    except OpFailure as e:
        return OpResult(False, str(e))
    except OSError as e:
        return OpResult(False, f"{type(e).__name__}: {e}")


_QUERIES = {"read": _read, "grep": _grep, "find": _find, "tree": _tree, "outline": _outline}
