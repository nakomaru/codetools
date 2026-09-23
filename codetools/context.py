"""The start-of-chat dump: environment, protocol, project notes, git state, project tree, outlines, and pinned
files in full."""

import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .commands import find_bash
from .globmatch import matches
from .outline import supported
from .queries import outline_files, read_whole, render_tree
from .report import fence, package
from .settings import ProjectState
from .textfile import plural
from .workspace import Workspace

PROTOCOL_PATH = Path(__file__).with_name("PROTOCOL.md")
_GLOB_CHARS = set("*?[{")
_GIT_STATUS_MAX_LINES = 50
_GIT_LOG_COUNT = 8
_TAIL = "The operator's task follows."


@dataclass
class Context:
    text: str
    pinned: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def protocol_text() -> str:
    return PROTOCOL_PATH.read_text(encoding="utf-8")


def expand(ws: Workspace, entries: list[str], label: str, dirs: bool = False) -> tuple[list[str], list[str]]:
    """Files named by paths or globs (and directories, when dirs is set), plus warnings for entries that name
    nothing. Secret files are left out."""
    files = ws.list_files()
    resolved: list[str] = []
    warnings = []
    for entry in entries:
        pattern = entry.strip().replace("\\", "/").strip("/")
        if _GLOB_CHARS & set(pattern):
            found = [f for f in files if matches(pattern, f)]
        elif ws.abs(pattern).is_file():
            found = [pattern]
        elif dirs and ws.abs(pattern).is_dir():
            found = ws.files_under(pattern)
        else:
            found = []
        if not found:
            warnings.append(f"{label} {entry!r} matches no file")
        for rel in found:
            if ws.is_secret(rel):
                warnings.append(f"{label} {rel} is a secret file and was left out")
            elif rel not in resolved:
                resolved.append(rel)
    return resolved, warnings


def resolve_pins(ws: Workspace, pins: list[str]) -> tuple[list[str], list[str]]:
    return expand(ws, pins, "pin")


def build(ws: Workspace, state: ProjectState) -> Context:
    s = ws.settings
    ws.invalidate()
    sections = [_environment(ws)]
    if s.include_protocol:
        sections.append(protocol_text().strip())
    notes = state.notes()
    if notes:
        sections.append("# Project notes\n\n" + notes)
    if s.include_git and ws.git:
        sections.append("# Git\n\n" + fence(_git_summary(ws)))
    tree, count, tree_notes = render_tree(ws, ".", s.tree_depth)
    sections.append("\n".join([f"# Project tree ({plural(count, 'file')}, depth {s.tree_depth})\n\n" + fence(tree),
                               *(f"- {n}" for n in tree_notes)]))

    warnings: list[str] = []
    if s.outline:
        files, outline_warnings = expand(ws, s.outline, "outline", dirs=True)
        warnings += outline_warnings
        text, _, _, outline_notes = outline_files(ws, [f for f in files if supported(f)])
        warnings += outline_notes
        if text:
            sections.append("# Outlines (line ranges of classes, functions, and headings)\n\n" + fence(text))

    pinned, pin_warnings = resolve_pins(ws, state.pins())
    warnings += pin_warnings
    if pinned:
        parts = ["# Pinned files"]
        for rel in pinned:
            result = read_whole(ws, rel)
            if not result.ok:
                warnings.append(f"pin {rel}: {result.summary}")
                continue
            parts.append(f"## {rel} ({result.summary})")
            parts += [f"- {n}" for n in result.notes]
            if result.detail:
                parts.append(fence(result.detail))
        sections.append("\n\n".join(parts))

    head = f"[codetools] project context for `{ws.root.name}`, as of {datetime.now():%Y-%m-%d %H:%M}"
    return Context(package(head + "\n" + "\n\n".join(sections), s.fold, _TAIL), pinned, warnings)


def _environment(ws: Workspace) -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    bash = find_bash()
    lines = [
        "# Environment",
        "",
        f"- Project root: `{ws.root}`. Every path in a batch is relative to it, and run ops start in it.",
        f"- Local time: {now:%Y-%m-%d %H:%M} {now:%A} (UTC{offset[:3]}:{offset[3:]})",
        f"- Platform: {platform.system()} {platform.release()} ({platform.version()})",
        f"- run ops execute in: {'Git Bash' if bash else 'no bash found'}",
        f"- Git: {'repository; state below' if ws.git else 'not a git repository'}",
    ]
    return "\n".join(lines)


def _git_summary(ws: Workspace) -> str:
    status = ws.run_git("status", "--short", "--branch").stdout.decode("utf-8", errors="replace").splitlines()
    if len(status) > _GIT_STATUS_MAX_LINES:
        status = status[:_GIT_STATUS_MAX_LINES] + [f"... {len(status) - _GIT_STATUS_MAX_LINES} more changed paths"]
    log = ws.run_git("log", "--oneline", f"-{_GIT_LOG_COUNT}").stdout.decode("utf-8", errors="replace").strip()
    return "\n".join(["$ git status --short --branch", *status, "", f"$ git log --oneline -{_GIT_LOG_COUNT}",
                      log or "(no commits)"])
