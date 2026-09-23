from codetools.outline import outline, supported

PY = """\
import os


@decorator
def top(a, b=2) -> int:
    def inner():
        pass
    return a


class Thing(Base, metaclass=Meta):
    async def run(self, *args):
        pass

if os.name == "nt":
    def windows_only():
        pass
""".splitlines()


def flat(entry: str) -> str:
    where, text = entry.split(None, 1)
    return f"{where}  {text}"


def test_python_outline_has_ranges_signatures_and_nesting():
    entries, note = outline("pkg/mod.py", PY)
    assert note == ""
    assert [flat(e) for e in entries] == [
        "4-8  def top(a, b=2) -> int",
        "6-7  def inner()",
        "11-13  class Thing(Base, metaclass=Meta)",
        "12-13  async def run(self, *args)",
        "16-17  def windows_only()",
    ]
    assert entries[1].endswith("    def inner()")


def test_python_syntax_error_falls_back_to_patterns():
    entries, note = outline("bad.py", ["def ok():", "    pass", "def broken(:"])
    assert "syntax error" in note
    assert [e.strip() for e in entries] == ["1  def ok():", "3  def broken(:"]


def test_markdown_headings_get_section_ranges_and_skip_code_fences():
    md = ["# Title", "intro", "## One", "```", "# not a heading", "```", "## Two", "text"]
    entries, _ = outline("README.md", md)
    assert [flat(e) for e in entries] == ["1-8  # Title", "3-6  ## One", "7-8  ## Two"]


def test_pattern_languages():
    ts = ["export class Api {", "  async fetch(url: string): Promise<void> {", "  }", "}",
          "export const handler = async (req) => {", "if (x) {", "export interface Opts {"]
    entries, _ = outline("src/api.ts", ts)
    assert [flat(e) for e in entries] == ["1  export class Api", "2  async fetch(url: string): Promise<void>",
                                          "5  export const handler = async (req) =>", "7  export interface Opts"]
    go, _ = outline("main.go", ["package main", "func main() {", "type Server struct {"])
    assert [e.strip() for e in go] == ["2  func main()", "3  type Server struct"]


def test_supported_extensions():
    assert supported("a.py") and supported("docs/x.md") and supported("x.tsx") and supported("lib.rs")
    assert not supported("data.json") and not supported("Makefile")
