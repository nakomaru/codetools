import codecs
from dataclasses import dataclass, field, replace


class NotText(Exception):
    pass


@dataclass
class TextFile:
    """A UTF-8 text file split into lines, remembering the encoding details needed to write it back unchanged."""

    lines: list[str] = field(default_factory=list)
    final_newline: bool = True
    newline: str = "\n"
    bom: bool = False
    note: str = ""

    @classmethod
    def decode(cls, data: bytes) -> "TextFile":
        if b"\x00" in data[:8192]:
            raise NotText(f"binary file ({len(data):,} bytes)")
        bom = data.startswith(codecs.BOM_UTF8)
        try:
            text = data[len(codecs.BOM_UTF8) if bom else 0:].decode("utf-8")
        except UnicodeDecodeError as e:
            raise NotText(f"not UTF-8 text ({e.reason} at byte {e.start})") from None
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        newline = "\r\n" if crlf > lf else "\n"
        note = ""
        if crlf and lf:
            label = "CRLF" if newline == "\r\n" else "LF"
            note = f"mixed line endings; writing this file normalizes them to {label}"
        text = text.replace("\r\n", "\n")
        final_newline = text.endswith("\n")
        lines = text.split("\n")
        if final_newline:
            lines.pop()
        if text == "":
            lines = []
        return cls(lines, final_newline, newline, bom, note)

    def with_lines(self, lines: list[str]) -> "TextFile":
        return replace(self, lines=list(lines), final_newline=self.final_newline or not self.lines, note="")

    def text(self) -> str:
        if not self.lines:
            return ""
        return "\n".join(self.lines) + ("\n" if self.final_newline else "")

    def encode(self) -> bytes:
        data = self.text().replace("\n", self.newline).encode("utf-8")
        return codecs.BOM_UTF8 + data if self.bom else data


def plural(count: int, word: str, suffix: str = "s") -> str:
    return f"{count} {word}{'' if count == 1 else suffix}"
