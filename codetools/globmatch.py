"""Glob patterns for find and grep -g: `*` within a path segment, `**` across segments, `?`, `[abc]`, `{a,b}`.

A pattern without a slash matches the basename; a pattern with a slash matches the whole relative path.
"""

import os
import re
from functools import lru_cache

_FLAGS = re.IGNORECASE if os.name == "nt" else 0


@lru_cache(maxsize=256)
def compile_glob(glob: str) -> re.Pattern:
    return re.compile("^" + _translate(glob) + "$", _FLAGS)


def matches(glob: str, rel: str) -> bool:
    target = rel if "/" in glob else rel.rsplit("/", 1)[-1]
    return compile_glob(glob).match(target) is not None


def _translate(glob: str) -> str:
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and (close := glob.find("]", i + 2)) != -1:
            body = glob[i + 1:close].replace("\\", "\\\\")
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = close + 1
        elif glob[i] == "{" and (close := glob.find("}", i)) != -1:
            options = glob[i + 1:close].split(",")
            out.append("(?:" + "|".join(_translate(option) for option in options) + ")")
            i = close + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "".join(out)
