"""Parser for the batch format described in PROTOCOL.md.

A batch is the text between a `=== batch` line and an `=== end` line. Text outside those markers (prose, code
fences) is ignored, so a whole chat reply can be pasted as is. Any malformed op makes the whole batch invalid.

An op with content may end its header with `<<TAG`; its content then runs until a line that is exactly TAG,
so the content can hold lines that look like `=== ` headers or batch markers.
"""

import re
import shlex
from dataclasses import dataclass, field

from . import config
from .ops import ALL_VERBS, BODY_VERBS, FilePatch, Hunk, Op
from .paths import PathSyntaxError, normalize

_START_RE = re.compile(r"^([ \t]*)=== batch[ \t]*$")
_HAS_BATCH_RE = re.compile(r"^[ \t]*=== batch[ \t]*$", re.MULTILINE)
_HEADER_RE = re.compile(r"^=== (\S+)(?:[ \t]+(.*?))?[ \t]*$")
_HEREDOC_RE = re.compile(r"(?:^|[ \t])<<([A-Za-z_][A-Za-z0-9_]*)$")
_START_LINE = "=== batch"
_END_LINE = "=== end"
_SEARCH_RE = re.compile(r"^<{5,9} ?SEARCH[ \t]*$")
_DIVIDER_RE = re.compile(r"^={5,9}[ \t]*$")
_REPLACE_RE = re.compile(r"^>{5,9} ?REPLACE[ \t]*$")
_READ_SPEC_RE = re.compile(r"^(.+?):(\d+)(?:-(\d+))?$")
_DIFF_PREAMBLE = ("diff ", "index ", "new file mode", "deleted file mode", "old mode", "new mode",
                  "similarity index", "dissimilarity index", "rename from", "rename to", "copy from", "copy to")


@dataclass
class ParseError:
    line: int | None
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


@dataclass
class ParsedBatch:
    source: str
    ops: list[Op]
    errors: list[ParseError]
    incomplete: bool = False


@dataclass
class _Chunk:
    line: int
    verb: str
    argstr: str
    header: str
    heredoc: str | None
    body: list[tuple[int, str]] = field(default_factory=list)
    sealed: bool = False


class _Fail(Exception):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


def has_batch(text: str) -> bool:
    return _HAS_BATCH_RE.search(text) is not None


def parse(text: str) -> ParsedBatch | None:
    """Parse every batch section in text into one list of ops, or return None when text holds no batch.

    incomplete is set when a batch never reaches its `=== end`, which usually means the reply was copied
    while the bot was still writing it.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chunks: list[_Chunk] = []
    errors: list[ParseError] = []
    source: list[str] = []
    found = incomplete = False
    i = 0
    while i < len(lines):
        m = _START_RE.match(lines[i])
        i += 1
        if m is None:
            continue
        found = True
        indent = m.group(1)
        start_line = i
        closed = False
        current: _Chunk | None = None
        while i < len(lines):
            raw = lines[i]
            i += 1
            line = raw[len(indent):] if raw.startswith(indent) else raw
            source.append(line)
            if current is not None and current.heredoc and not current.sealed:
                if line.rstrip() == current.heredoc:
                    current.sealed = True
                else:
                    current.body.append((i, line))
                continue
            if line.rstrip() == _END_LINE:
                source.pop()
                closed = True
                break
            if line.rstrip() == _START_LINE:
                errors.append(ParseError(i, "`=== batch` inside a batch; end the first one with `=== end`"))
                continue
            header = _HEADER_RE.match(line)
            if header:
                argstr = header.group(2) or ""
                heredoc = _HEREDOC_RE.search(argstr)
                tag = heredoc.group(1) if heredoc else None
                if heredoc:
                    argstr = argstr[:heredoc.start()].rstrip()
                current = _Chunk(i, header.group(1), argstr, line.strip(), tag)
                chunks.append(current)
            elif current is None:
                if line.strip():
                    errors.append(ParseError(i, f"text before the first op: {_clip(line)!r}"))
            elif current.sealed:
                if line.strip():
                    errors.append(ParseError(i, f"text after the `{current.heredoc}` line that ended "
                                                f"`{_clip(current.header)}`: {_clip(line)!r}"))
            else:
                current.body.append((i, line))
        if not closed:
            incomplete = True
            if current is not None and current.heredoc and not current.sealed:
                errors.append(ParseError(current.line, f"`{_clip(current.header)}` content never reaches its "
                                                       f"`{current.heredoc}` line"))
            errors.append(ParseError(start_line, "batch has no `=== end` line (reply cut off?)"))
    if not found:
        return None
    if not chunks and not errors:
        errors.append(ParseError(None, "batch contains no ops"))
    ops = _build_ops(chunks, errors)
    return ParsedBatch("\n".join(source), ops, errors, incomplete)


def _build_ops(chunks: list[_Chunk], errors: list[ParseError]) -> list[Op]:
    ops: list[Op] = []
    for chunk in chunks:
        try:
            if chunk.heredoc and chunk.verb not in BODY_VERBS:
                raise _Fail(f"`<<{chunk.heredoc}` only applies to ops with content "
                            f"({', '.join(sorted(BODY_VERBS))})")
            specs = _build(chunk.verb, chunk.argstr, chunk.body, exact=chunk.heredoc is not None)
        except _Fail as e:
            errors.append(ParseError(e.line or chunk.line, f"`{_clip(chunk.header)}`: {e}"))
            continue
        for title, args in specs:
            ops.append(Op(index=len(ops) + 1, verb=chunk.verb, line=chunk.line, title=title, args=args))
    return ops


def _build(verb: str, argstr: str, body: list[tuple[int, str]], exact: bool):
    if verb not in ALL_VERBS:
        raise _Fail(f"unknown op `{verb}`; valid ops are {', '.join(sorted(ALL_VERBS))}")
    if verb not in BODY_VERBS:
        stray = next(((n, line) for n, line in body if line.strip()), None)
        if stray:
            raise _Fail(f"`{verb}` takes everything on its header line; unexpected content {_clip(stray[1])!r}",
                        stray[0])
    title = f"{verb} {argstr}".strip()
    if verb == "run":
        if not argstr.strip():
            raise _Fail("needs a command")
        return [(title, {"command": argstr.strip()})]
    args = _split_args(argstr)
    if verb == "read":
        return _build_read(args)
    builders = {"grep": _build_grep, "find": _build_find, "tree": _build_tree, "outline": _build_outline,
                "move": _build_move_copy, "copy": _build_move_copy, "delete": _build_delete, "mkdir": _build_mkdir}
    if verb in builders:
        return [(title, builders[verb](args))]
    if verb == "edit":
        return [(title, {"path": _single_path(args), "hunks": _parse_hunks(body)})]
    if verb in ("write", "overwrite"):
        content = [line for _, line in body] if exact else _content_lines(body)
        return [(title, {"path": _single_path(args), "content": content})]
    if args:
        raise _Fail("takes no arguments; the diff names the files")
    files = _parse_patch(body)
    return [(f"patch {', '.join(dict.fromkeys(p for fp in files for p in (fp.old_path, fp.new_path) if p))}",
             {"files": files})]


def _split_args(argstr: str) -> list[str]:
    lexer = shlex.shlex(argstr, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as e:
        raise _Fail(f"can't split arguments: {e}") from None


def _path(raw: str, allow_root: bool = False) -> str:
    try:
        path = normalize(raw)
    except PathSyntaxError as e:
        raise _Fail(str(e)) from None
    if path == "." and not allow_root:
        raise _Fail("the project root itself is not a valid target here")
    return path


def _single_path(args: list[str]) -> str:
    if len(args) != 1:
        raise _Fail(f"needs exactly one PATH, got {len(args)} arguments")
    return _path(args[0])


def _int_arg(value: str | None, flag: str, low: int, high: int) -> int:
    if value is None or not value.isdigit() or not low <= int(value) <= high:
        raise _Fail(f"{flag} needs a number from {low} to {high}")
    return int(value)


def _build_read(args: list[str]):
    if not args:
        raise _Fail("needs at least one PATH")
    specs = []
    for arg in args:
        path, start, end = arg, None, None
        m = _READ_SPEC_RE.match(arg)
        if m:
            path, start = m.group(1), int(m.group(2))
            end = int(m.group(3)) if m.group(3) else None
            if start < 1 or (end is not None and end < start):
                raise _Fail(f"bad line range in {arg!r}")
        specs.append((f"read {arg}", {"path": _path(path), "start": start, "end": end}))
    return specs


def _build_grep(args: list[str]):
    ignore_case = fixed = False
    glob = None
    context = 0
    positional: list[str] = []
    it = iter(args)
    options_done = False
    for arg in it:
        if options_done or not arg.startswith("-") or arg == "-":
            positional.append(arg)
        elif arg == "--":
            options_done = True
        elif arg == "-i":
            ignore_case = True
        elif arg == "-F":
            fixed = True
        elif arg in ("-g", "--glob"):
            glob = next(it, None)
            if not glob:
                raise _Fail(f"{arg} needs a GLOB")
        elif arg == "-C":
            context = _int_arg(next(it, None), "-C", 0, config.GREP_MAX_CONTEXT)
        else:
            raise _Fail(f"unknown grep flag {arg!r} (put `--` before a pattern that starts with '-')")
    if not positional:
        raise _Fail("needs a PATTERN")
    pattern, raw_paths = positional[0], positional[1:] or ["."]
    try:
        regex = re.compile(re.escape(pattern) if fixed else pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise _Fail(f"invalid regex {pattern!r}: {e}") from None
    return {"pattern": pattern, "regex": regex, "paths": [_path(p, allow_root=True) for p in raw_paths],
            "glob": glob, "context": context}


def _build_find(args: list[str]):
    if not 1 <= len(args) <= 2:
        raise _Fail("usage: find GLOB [PATH]")
    return {"glob": args[0], "path": _path(args[1], allow_root=True) if len(args) == 2 else "."}


def _build_tree(args: list[str]):
    depth = None
    positional = []
    it = iter(args)
    for arg in it:
        if arg == "-d":
            depth = _int_arg(next(it, None), "-d", 1, 50)
        elif arg.startswith("-"):
            raise _Fail(f"unknown tree flag {arg!r}")
        else:
            positional.append(arg)
    if len(positional) > 1:
        raise _Fail("usage: tree [PATH] [-d DEPTH]")
    return {"path": _path(positional[0], allow_root=True) if positional else ".", "depth": depth}


def _build_outline(args: list[str]):
    if not args:
        raise _Fail("needs at least one PATH (a file or a directory)")
    return {"paths": [_path(a, allow_root=True) for a in args]}


def _build_move_copy(args: list[str]):
    if len(args) != 2:
        raise _Fail("needs exactly SRC and DST")
    return {"src": _path(args[0]), "dst": _path(args[1])}


def _build_delete(args: list[str]):
    recursive = "-r" in args
    rest = [a for a in args if a != "-r"]
    if len(rest) != 1:
        raise _Fail("usage: delete PATH [-r]")
    return {"path": _path(rest[0]), "recursive": recursive}


def _build_mkdir(args: list[str]):
    return {"path": _single_path(args)}


def _content_lines(body: list[tuple[int, str]]) -> list[str]:
    lines = [line for _, line in body]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _parse_hunks(body: list[tuple[int, str]]) -> list[Hunk]:
    hunks: list[Hunk] = []
    state = "outside"
    search: list[str] = []
    replace: list[str] = []
    start = None
    for n, line in body:
        if state == "outside":
            if _SEARCH_RE.match(line):
                state, search, replace, start = "search", [], [], n
            elif line.strip():
                raise _Fail(f"expected `<<<<<<< SEARCH`, found {_clip(line)!r}", n)
        elif state == "search":
            if _DIVIDER_RE.match(line):
                state = "replace"
            elif _SEARCH_RE.match(line) or _REPLACE_RE.match(line):
                raise _Fail("SEARCH section has no `=======` divider", n)
            else:
                search.append(line)
        elif _REPLACE_RE.match(line):
            if not any(s.strip() for s in search):
                raise _Fail("SEARCH is empty; it must quote existing lines (use write for new files)", start)
            hunks.append(Hunk(search, replace))
            state = "outside"
        elif _SEARCH_RE.match(line) or _DIVIDER_RE.match(line):
            raise _Fail("REPLACE section is missing its `>>>>>>> REPLACE` line", n)
        else:
            replace.append(line)
    if state != "outside":
        raise _Fail("SEARCH/REPLACE pair is not closed with `>>>>>>> REPLACE`", start)
    if not hunks:
        raise _Fail("needs at least one SEARCH/REPLACE pair")
    return hunks


def _parse_patch(body: list[tuple[int, str]]) -> list[FilePatch]:
    lines = list(body)
    while lines and not lines[-1][1].strip():
        lines.pop()
    files: list[FilePatch] = []
    hunk: Hunk | None = None
    i = 0
    while i < len(lines):
        n, line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1][1].startswith("+++ "):
            old, new = _diff_paths(line[4:], lines[i + 1][1][4:])
            files.append(FilePatch(old, new, []))
            hunk = None
            i += 2
            continue
        i += 1
        if line.startswith("@@"):
            if not files:
                raise _Fail("hunk appears before any `---`/`+++` file header", n)
            hunk = Hunk([], [])
            files[-1].hunks.append(hunk)
        elif line.startswith(_DIFF_PREAMBLE):
            hunk = None
        elif hunk is None:
            if line.strip():
                raise _Fail(f"unexpected line outside a hunk: {_clip(line)!r}", n)
        elif line == "" or line[0] == " ":
            hunk.search.append(line[1:])
            hunk.replace.append(line[1:])
        elif line[0] == "-":
            hunk.search.append(line[1:])
        elif line[0] == "+":
            hunk.replace.append(line[1:])
        elif line[0] != "\\":
            raise _Fail(f"hunk line must start with ' ', '-', or '+': {_clip(line)!r}", n)
    if not files:
        raise _Fail("no `---`/`+++` file headers found")
    for fp in files:
        label = fp.new_path or fp.old_path
        if fp.old_path is None and fp.new_path is None:
            raise _Fail("a file section has /dev/null on both sides")
        if fp.old_path is None:
            if any(h.search for h in fp.hunks):
                raise _Fail(f"{label}: a new file's hunks can only add lines")
        elif fp.new_path is not None:
            if not fp.hunks and fp.old_path == fp.new_path:
                raise _Fail(f"{label}: file section has no hunks")
            for k, h in enumerate(fp.hunks, 1):
                if not any(s.strip() for s in h.search):
                    raise _Fail(f"{label}: hunk {k} has no context or removed lines, so it can't be located")
    return files


def _diff_paths(raw_old: str, raw_new: str) -> tuple[str | None, str | None]:
    old = raw_old.split("\t")[0].strip().strip('"')
    new = raw_new.split("\t")[0].strip().strip('"')
    old_null, new_null = old == "/dev/null", new == "/dev/null"
    prefixed = (old_null or old.startswith("a/")) and (new_null or new.startswith("b/"))
    if prefixed:
        old, new = old[2:] if not old_null else old, new[2:] if not new_null else new
    return (None if old_null else _path(old)), (None if new_null else _path(new))


def _clip(text: str, limit: int = 80) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."
