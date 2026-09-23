"""Locating SEARCH text in a file, with a diagnosis when it can't be located.

A match must be exact apart from trailing whitespace (which the bot can't see in rendered output). Near misses
are never applied; the failure says why the text missed: line-number prefixes copied from read output,
different indentation, or else the most similar region of the file as a diff.
"""

import difflib
import re
from dataclasses import dataclass

_LINE_NUMBER_RE = re.compile(r"^\s*\d+\| ?")
_CANDIDATES_SCORED = 40


@dataclass
class Located:
    start: int
    end: int


class LocateFailure(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def locate(lines: list[str], search: list[str], label: str) -> Located:
    n = len(search)
    trimmed = [line.rstrip() for line in lines]
    found = _find_all(trimmed, [s.rstrip() for s in search])
    if len(found) == 1:
        return Located(found[0], found[0] + n)
    if found:
        raise LocateFailure(_ambiguous(found))

    unnumbered = _strip_line_numbers(search)
    if unnumbered is not None and _find_all(trimmed, [s.rstrip() for s in unnumbered]):
        raise LocateFailure("SEARCH starts its lines with `   12| ` prefixes copied from read output; remove them "
                            "from SEARCH and REPLACE")

    shifted = _find_indent_only(trimmed, search)
    if shifted:
        start = shifted[0]
        where = f"lines {start + 1}-{start + n}" if len(shifted) == 1 else f"{len(shifted)} places"
        detail = _diff(search, lines[start:start + n], f"{label} lines {start + 1}-{start + n} (actual)")
        raise LocateFailure(f"SEARCH matches {where} only if indentation is ignored; copy the file's indentation "
                            "exactly", detail)

    message, detail = _closest(lines, search, label)
    raise LocateFailure(f"SEARCH not found; {message}", detail)


def _ambiguous(starts: list[int]) -> str:
    shown = ", ".join(str(s + 1) for s in starts[:10])
    return f"SEARCH matches {len(starts)} places (starting at lines {shown}); include more surrounding lines"


def _find_all(hay: list[str], needle: list[str]) -> list[int]:
    n = len(needle)
    first = needle[0]
    return [i for i in range(len(hay) - n + 1) if hay[i] == first and hay[i:i + n] == needle]


def _strip_line_numbers(lines: list[str]) -> list[str] | None:
    content = [line for line in lines if line.strip()]
    if not content or not all(_LINE_NUMBER_RE.match(line) for line in content):
        return None
    return [_LINE_NUMBER_RE.sub("", line, count=1) for line in lines]


def _find_indent_only(trimmed: list[str], search: list[str]) -> list[int]:
    return _find_all([line.strip() for line in trimmed], [s.strip() for s in search])


def _diff(search: list[str], actual: list[str], tofile: str) -> str:
    n = max(len(search), len(actual))
    return "\n".join(difflib.unified_diff(search, actual, fromfile="SEARCH (as sent)", tofile=tofile, lineterm="",
                                          n=n))


def _closest(lines: list[str], search: list[str], label: str) -> tuple[str, str]:
    if not lines:
        return "the file is empty", ""
    wanted = {s.strip() for s in search if s.strip()}
    prefix = [0]
    for line in lines:
        prefix.append(prefix[-1] + (line.strip() in wanted))
    n = len(search)
    windows = []
    for size in sorted({min(max(1, n + d), len(lines)) for d in (-1, 0, 1)}):
        for i in range(len(lines) - size + 1):
            count = prefix[i + size] - prefix[i]
            if count:
                windows.append((count, i, size))
    if not windows:
        return "no line of SEARCH appears anywhere in the file", ""
    windows.sort(key=lambda w: (-w[0], w[1]))
    wanted_text = "\n".join(s.strip() for s in search)
    best = None
    for _, i, size in windows[:_CANDIDATES_SCORED]:
        actual_text = "\n".join(line.strip() for line in lines[i:i + size])
        ratio = difflib.SequenceMatcher(None, wanted_text, actual_text, autojunk=False).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, i, size)
    ratio, i, size = best
    detail = _diff(search, lines[i:i + size], f"{label} lines {i + 1}-{i + size} (actual)")
    return f"closest match is lines {i + 1}-{i + size} ({ratio:.0%} similar)", detail
