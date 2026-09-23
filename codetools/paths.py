import re

from . import config

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PathSyntaxError(ValueError):
    pass


def normalize(raw: str) -> str:
    """Turn a path as written by the bot into a clean project-relative posix path ("." for the root)."""
    p = raw.strip().replace("\\", "/")
    if not p:
        raise PathSyntaxError("empty path")
    if p.startswith("/") or _DRIVE_RE.match(p):
        raise PathSyntaxError(f"{raw!r} is absolute; paths must be relative to the project root")
    parts = [part for part in p.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise PathSyntaxError(f"{raw!r} uses '..'; paths must stay inside the project root")
    if any(part in config.PROTECTED_DIRS for part in parts):
        raise PathSyntaxError(f"{raw!r} is inside a protected directory ({', '.join(sorted(config.PROTECTED_DIRS))})")
    return "/".join(parts) or "."


def has_protected_part(rel: str) -> bool:
    return any(part in config.PROTECTED_DIRS for part in rel.split("/"))
