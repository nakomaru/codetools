"""Structural outlines: the classes, functions, and headings in a file, with their line numbers.

Python is parsed with ast, so its entries carry exact line ranges and signatures. Markdown headings carry the
range of their section. Other languages list the lines that declare something, found by pattern.
"""

import ast
import re

from . import config

_SIGNATURE_MAX_CHARS = 160

_JS = [
    r"^\s*(export\s+)?(default\s+)?(async\s+)?function\b",
    r"^\s*(export\s+)?(default\s+)?(abstract\s+)?class\s+\w",
    r"^\s*(export\s+)?(const|let|var)\s+\w+\s*(:[^=]+)?=\s*(async\s+)?(function\b|(\([^)]*\)|\w+)\s*(:[^=]+)?=>)",
    r"^\s*(export\s+)?(declare\s+)?(interface|type|enum|namespace)\s+\w",
    r"^\s+(static\s+|async\s+|get\s+|set\s+|public\s+|private\s+|protected\s+|readonly\s+|override\s+)*"
    r"(?!(if|for|while|switch|catch|return|function|else)\b)[\w$]+\s*(<[^>]*>)?\s*\([^;]*\)\s*(:\s*[^={;]+)?\{\s*$",
]
_PATTERNS = {
    "js": _JS,
    "go": [r"^func\b", r"^type\s+\w"],
    "rust": [r"^\s*(pub(\([\w:]+\))?\s+)?(async\s+|const\s+|unsafe\s+|extern\s+(\"\w+\"\s+)?)*"
             r"(fn|struct|enum|trait|impl|mod|union|macro_rules!)\b"],
    "jvm": [r"^\s*(@\w+\s+)*((public|private|protected|internal|static|final|abstract|sealed|open|data|override|"
            r"virtual|async|partial|readonly|suspend)\s+)*(class|interface|enum|record|struct|object|fun)\s+\w",
            r"^\s*((public|private|protected|internal|static|final|abstract|override|virtual|async|synchronized|"
            r"extern|unsafe|new)\s+)+[\w<>\[\],.?\s]+\s+\w+\s*\([^;]*$"],
    "c": [r"^(struct|class|enum|union|namespace|typedef)\b[^;]*$",
          r"^(?!(if|for|while|switch|return|else|do)\b)[A-Za-z_][\w\s\*&:<>,]*[\s\*&]\**(\w+::)*~?\w+\s*\([^;]*$"],
    "shell": [r"^\s*(function\s+[\w:-]+|[\w:-]+\s*\(\)\s*)\s*\{?\s*$"],
    "powershell": [r"^\s*(function|filter|class|enum)\s+[\w-]+"],
}
_LANGUAGES = {
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".go": "go", ".rs": "rust",
    ".java": "jvm", ".kt": "jvm", ".kts": "jvm", ".cs": "jvm", ".scala": "jvm", ".swift": "jvm",
    ".c": "c", ".h": "c", ".cpp": "c", ".cc": "c", ".cxx": "c", ".hpp": "c", ".hh": "c",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell", ".psm1": "powershell",
}
_COMPILED = {lang: [re.compile(p) for p in patterns] for lang, patterns in _PATTERNS.items()}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def supported(rel: str) -> bool:
    return _extension(rel) in (".py", ".pyi", ".md", ".markdown") or _extension(rel) in _LANGUAGES


def outline(rel: str, lines: list[str]) -> tuple[list[str], str]:
    """Outline entries for a file's lines, plus a note when the outline is approximate."""
    ext = _extension(rel)
    if ext in (".py", ".pyi"):
        try:
            return _python(lines), ""
        except SyntaxError as e:
            fallback = [re.compile(r"^\s*(async\s+)?def\s"), re.compile(r"^\s*class\s")]
            return _by_pattern(lines, fallback), f"syntax error at line {e.lineno}; listed by pattern instead"
    if ext in (".md", ".markdown"):
        return _markdown(lines), ""
    return _by_pattern(lines, _COMPILED[_LANGUAGES[ext]]), ""


def _extension(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    return name[name.rfind("."):] if "." in name else ""


def _entry(start: int, end: int | None, depth: int, text: str) -> str:
    where = f"{start}-{end}" if end and end != start else str(start)
    if len(text) > _SIGNATURE_MAX_CHARS:
        text = text[:_SIGNATURE_MAX_CHARS] + "..."
    return f"{where:>11}  {'  ' * depth}{text}"


def _python(lines: list[str]) -> list[str]:
    tree = ast.parse("\n".join(lines))
    out: list[str] = []

    def visit(nodes: list[ast.stmt], depth: int) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([d.lineno for d in node.decorator_list] + [node.lineno])
                out.append(_entry(start, node.end_lineno, depth, _python_signature(node)))
                visit(node.body, depth + 1)
            elif isinstance(node, ast.If):
                visit(node.body, depth)
                visit(node.orelse, depth)
            elif isinstance(node, ast.Try):
                for block in (node.body, *(h.body for h in node.handlers), node.orelse, node.finalbody):
                    visit(block, depth)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                visit(node.body, depth)

    visit(tree.body, 0)
    return out


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _markdown(lines: list[str]) -> list[str]:
    headings: list[tuple[int, int, str]] = []
    fence = None
    for n, line in enumerate(lines, 1):
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None and (h := _HEADING_RE.match(line)):
            headings.append((n, len(h.group(1)), h.group(2)))
    out = []
    for k, (n, level, title) in enumerate(headings):
        end = next((m - 1 for m, lvl, _ in headings[k + 1:] if lvl <= level), len(lines))
        out.append(_entry(n, end, level - 1, "#" * level + " " + title))
    return out


def _by_pattern(lines: list[str], patterns: list[re.Pattern]) -> list[str]:
    out = []
    for n, line in enumerate(lines, 1):
        if any(p.search(line) for p in patterns):
            stripped = line.strip().rstrip("{").rstrip()
            depth = min((len(line) - len(line.lstrip())) // 2, 8)
            out.append(_entry(n, None, depth, stripped))
    return out


def limit(entries: list[str]) -> tuple[list[str], bool]:
    if len(entries) <= config.OUTLINE_MAX_LINES:
        return entries, False
    return entries[:config.OUTLINE_MAX_LINES], True
