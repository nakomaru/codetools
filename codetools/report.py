"""Clipboard reports, written for the chat bot. Each report is self-contained and supersedes earlier ones."""

import re

from .batch import Batch
from .history import FileStatus
from .ops import Op
from .textfile import plural

_TAG = "[codetools]"
_TRAILING_NEWLINES = 3


def fence(text: str, lang: str = "", minimum: int = 3) -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    marker = "`" * max(minimum, longest + 1)
    return f"{marker}{lang}\n{text}\n{marker}"


def package(text: str, fold: bool, tail: str = "") -> str:
    """The clipboard form of a report: its first line, the rest (fenced when fold is on, so chat UIs can
    collapse it), an optional tail line, and trailing blank lines to type after once pasted."""
    head, _, body = text.strip().partition("\n")
    body = body.strip("\n")
    out = head
    if body.strip():
        out += "\n" + fence(body, minimum=4) if fold else "\n\n" + body
    if tail:
        out += "\n\n" + tail
    return out + "\n" * _TRAILING_NEWLINES


def op_list(ops: list[Op]) -> str:
    """"op 7" or "ops 7, 12, 19"."""
    return ("op " if len(ops) == 1 else "ops ") + ", ".join(str(op.index) for op in ops)


def malformed(b: Batch) -> str:
    lines = [f"{_TAG} batch {b.id} REJECTED: malformed. Nothing was run or applied.", ""]
    lines += [f"- {e}" for e in b.errors]
    lines += ["", "Fix these and resend the entire batch."]
    return _finish(lines)


def preflight(b: Batch, prefix: str = "") -> str:
    lines = [_headline(b)] + ([prefix] if prefix else [])
    gated, failed = b.gated(), b.failed()
    if gated:
        passed = len(gated) - len(failed)
        if not failed:
            lines.append(f"Awaiting operator: `apply {b.id}`.")
        elif passed:
            lines.append(f"Awaiting operator: `applypartial {b.id}` applies the {passed} passing ops and rejects "
                         f"{op_list(failed)}; `apply {b.id}` requires every op to pass.")
        else:
            lines.append("Nothing can be applied; fix the failed ops and resend them.")
    _failed_section(b, lines, "Failed ops")
    _queries_section(b, lines, "")
    _passing_section(b, lines)
    return _finish(lines)


def applied(b: Batch) -> str:
    failed = b.failed()
    changes = b.of_kind("change")
    if failed:
        head = (f"{_TAG} batch {b.id} applied partially: {plural(len(b.passed()), 'op')} applied, "
                f"{len(failed)} rejected ({op_list(failed)}).")
    else:
        head = f"{_TAG} batch {b.id} applied: all {plural(len(b.gated()), 'op')}."
    lines = [head]
    if changes:
        lines += ["", "## Files changed"]
        lines += [f"- {fc.describe()}" for fc in b.applied_changes] or ["- (no file contents changed)"]
    _failed_section(b, lines, "Rejected ops (not applied; fix them against the files as they are now)")
    commands = b.of_kind("command")
    if commands:
        lines += ["", "## Commands"]
        for op in commands:
            result = b.runs.get(op.index)
            if result is None:
                lines += [f"### op {op.index}: {op.title}: not run"]
                continue
            if result.timed_out:
                status = f"TIMED OUT after {result.seconds:.1f} s"
            elif result.killed:
                status = f"killed by the operator after {result.seconds:.1f} s"
            else:
                status = f"exit {result.exit_code} in {result.seconds:.1f} s"
            lines += ["", f"### op {op.index}: {op.title}: {status}", fence(result.output or "(no output)")]
    _queries_section(b, lines, " (taken before this batch's changes)")
    return _finish(lines)


def apply_failed(b: Batch, error: str) -> str:
    return _finish([f"{_TAG} batch {b.id} apply FAILED: {error}. Nothing was run."])


def operator_rejected(b: Batch, note: str) -> str:
    lines = [f"{_TAG} batch {b.id} rejected by the operator; nothing was applied or run."]
    return _finish(lines + ([f"Operator note: {note}"] if note else []))


_UNDO_STATUS = {"A": "removed (the batch created it)", "D": "restored (the batch deleted it)"}


def undone(batch_id: int, message: str, files: list[FileStatus]) -> str:
    subject = message.split("\n", 1)[0]
    lines = [f"{_TAG} batch {batch_id} ({subject}) undone by the operator: {plural(len(files), 'file')} returned to "
             "their state before it was applied, including changes its commands made. Files ignored by .gitignore "
             "and anything outside the project were not reversed.", ""]
    lines += [f"- {f.path}: {_UNDO_STATUS.get(f.status, 'restored')}" for f in files]
    return _finish(lines)


def _finish(lines: list[str]) -> str:
    return "\n".join(lines).strip() + "\n"


def _headline(b: Batch) -> str:
    queries, gated, failed = b.of_kind("query"), b.gated(), b.failed()
    query_errors = [op for op in queries if not b.results[op.index].ok]
    query_part = "1 query" if len(queries) == 1 else f"{len(queries)} queries"
    if query_errors:
        query_part += f" ({len(query_errors)} failed)"
    if not gated:
        return f"{_TAG} batch {b.id}: {query_part}, done."
    head = f"{_TAG} batch {b.id}: preflight {len(gated) - len(failed)} of {len(gated)} ops passed"
    if failed:
        head += f", {len(failed)} failed ({op_list(failed)})"
    if queries:
        head += f"; {query_part} ran"
    return head + "."


def _failed_section(b: Batch, lines: list[str], title: str) -> None:
    failed = b.failed()
    if not failed:
        return
    lines += ["", f"## {title}"]
    for op in failed:
        r = b.results[op.index]
        lines += ["", f"### op {op.index}: {op.title}", f"FAILED: {r.summary}"]
        lines += [f"- {note}" for note in r.notes]
        if r.detail:
            lines.append(fence(r.detail, r.detail_lang))


def _queries_section(b: Batch, lines: list[str], suffix: str) -> None:
    queries = b.of_kind("query")
    if not queries:
        return
    lines += ["", f"## Query results{suffix}"]
    for op in queries:
        r = b.results[op.index]
        status = r.summary if r.ok else f"ERROR: {r.summary}"
        lines += ["", f"### op {op.index}: {op.title} ({status})"]
        lines += [f"- {note}" for note in r.notes]
        if r.detail:
            lines.append(fence(r.detail, r.detail_lang))


def _passing_section(b: Batch, lines: list[str]) -> None:
    passing = b.passed()
    if not passing:
        return
    lines += ["", "## Passing ops"]
    for op in passing:
        r = b.results[op.index]
        lines.append(f"- op {op.index}: {op.title}: {r.summary}")
        lines += [f"  - {note}" for note in r.notes]
