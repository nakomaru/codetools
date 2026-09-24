"""Batch lifecycle: ingest (parse, run queries, preflight changes), apply or applypartial, reject, undo.

Every operation returns the report to copy to the clipboard. Engine methods are serialized by a lock so a
clipboard ingest never interleaves with an apply.
"""

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import applier, batch as states, context, report
from .batch import Batch
from .changes import preflight
from .commands import CommandResult, Runner, find_shell
from .diffs import file_changes
from .history import HistoryError
from .ops import OpResult
from .protocol import parse
from .queries import run_query
from .settings import ProjectState
from .stage import Stage
from .textfile import plural
from .workspace import Workspace


class EngineError(Exception):
    """An operation was refused or failed. report, when set, is the text to copy for the bot."""

    def __init__(self, message: str, report_text: str = "", batch: Batch | None = None):
        super().__init__(message)
        self.report = report_text
        self.batch = batch


class StaleBatch(EngineError):
    pass


@dataclass
class IngestResult:
    """batch is set for a new batch; duplicate_of names an identical earlier one; incomplete means the text
    has a `=== batch` without its `=== end` yet. All empty means the text holds no batch."""

    batch: Batch | None = None
    duplicate_of: int | None = None
    incomplete: bool = False


class Engine:
    def __init__(self, root: Path | str):
        root = Path(root).resolve()
        self.state = ProjectState(root / ".codetools")
        self.created_state_files = self.state.ensure()
        self.settings = self.state.load_settings()
        self.ws = Workspace(root, self.settings)
        self.batches: dict[int, Batch] = {}
        self.runner = Runner(self.settings.shell)
        self._seen: dict[str, int] = {}
        self._lock = threading.RLock()
        self._batches_dir = self.ws.state_dir / "batches"
        self.history = self.ws.history

    @property
    def root(self) -> Path:
        return self.ws.root

    def startup_notes(self) -> list[str]:
        shell = find_shell(self.settings.shell)
        notes = [
            f"project root: {self.root}",
            f"file listing: .gitignore rules, {'git repository' if self.ws.git else 'no git repository'}",
            f"commands run with: {f'{shell.name}, {shell.path}' if shell else 'NO SHELL FOUND; run ops will fail'}",
        ]
        if self.created_state_files:
            notes.append(f"created .codetools/ {', '.join(self.created_state_files)}: settings and pins for this "
                         "project")
        if self.state.has_unread_notes():
            notes.append(".codetools/notes.md is no longer sent to the bot; move its contents into a project file, "
                         "such as the README or AGENTS.md, and pin that")
        pins = self.state.pins()
        notes.append(f"pinned: {', '.join(pins) if pins else 'nothing (see `pin`)'}")
        return notes

    def ingest(self, text: str, force: bool = False) -> IngestResult:
        """Parse, run queries, and preflight changes. force ingests a repeat or an incomplete batch."""
        parsed = parse(text)
        if parsed is None:
            return IngestResult()
        if parsed.incomplete and not force:
            return IngestResult(incomplete=True)
        key = hashlib.sha256(parsed.source.encode("utf-8")).hexdigest()
        with self._lock:
            if key in self._seen and not force:
                return IngestResult(duplicate_of=self._seen[key])
            b = Batch(self._next_id(), parsed.source, parsed.ops, parsed.errors, parsed.message)
            self._seen[key] = b.id
            self.batches[b.id] = b
            self._save(b, "reply.txt", text)
            if b.errors:
                b.status = states.MALFORMED
                self._set_report(b, "malformed", report.malformed(b))
                self._log(b, f"ingested: malformed, {len(b.errors)} errors")
                return IngestResult(b)
            self.ws.invalidate()
            for op in b.of_kind("query"):
                b.results[op.index] = run_query(op, self.ws)
            self._preflight(b)
            b.status = states.PENDING if b.gated() else states.DONE
            self._set_report(b, "preflight", report.preflight(b))
            self._log(b, f"ingested: {b.status}, {len(b.ops)} ops, {len(b.failed())} failed")
            return IngestResult(b)

    def context(self) -> context.Context:
        with self._lock:
            return context.build(self.ws, self.state)

    def pins(self) -> tuple[list[str], list[str]]:
        """Pinned files as currently resolved, plus warnings."""
        self.ws.invalidate()
        return context.resolve_pins(self.ws, self.state.pins())

    def pin(self, raw: str) -> str:
        pin = _clean_pin(raw)
        self.ws.invalidate()
        resolved, warnings = context.resolve_pins(self.ws, [pin])
        if not resolved:
            raise EngineError(warnings[0])
        if not self.state.add_pin(pin):
            return f"{pin} is already pinned"
        return f"pinned {pin}" + ("" if resolved == [pin] else f" ({plural(len(resolved), 'file')})")

    def unpin(self, raw: str) -> str:
        pin = _clean_pin(raw)
        if not self.state.remove_pin(pin):
            raise EngineError(f"{pin} is not pinned; `pins` lists the pins")
        return f"unpinned {pin}"

    def _preflight(self, b: Batch) -> None:
        stage = Stage(self.ws)
        failed_paths: dict[str, int] = {}
        for op in b.gated():
            if op.kind == "command":
                continue
            result = preflight(op, stage)
            if not result.ok:
                earlier = sorted({failed_paths[p] for p in op.touched_paths() if p in failed_paths})
                if earlier:
                    result.notes.append(f"earlier failed op(s) {', '.join(map(str, earlier))} touch the same path; "
                                        "this op was checked without their effects")
                for p in op.touched_paths():
                    failed_paths.setdefault(p, op.index)
            b.results[op.index] = result
        for op in b.of_kind("command"):
            b.results[op.index] = OpResult(True, "runs after apply")
        b.stage = stage
        b.staged_changes = file_changes(stage)

    def apply(self, batch_id: int, partial: bool, on_event: Callable[[str, bool], None] = lambda message, ok: None) -> Batch:
        with self._lock:
            b = self._pending(batch_id)
            failed = b.failed()
            if failed and not partial:
                raise EngineError(f"batch {b.id} has {len(failed)} failed op(s) ({report.op_list(failed)}); "
                                  f"`applypartial {b.id}` applies only the passing ones")
            if not b.passed():
                raise EngineError(f"batch {b.id} has no passing ops to apply")
            stale = b.stage.stale()
            if stale:
                self.ws.invalidate()
                self._preflight(b)
                text = report.preflight(b, prefix=f"Files changed on disk after preflight ({', '.join(stale)}); "
                                                  "batch re-preflighted.")
                self._set_report(b, "recheck", text)
                raise StaleBatch(f"files changed on disk after preflight: {', '.join(stale)}; batch {b.id} was "
                                 "re-preflighted, review it and apply again", b.report, b)
            try:
                before = self.history.snapshot(f"Project state before batch {b.id}")
                self._save_record(b.id, {"message": b.message, "before": before, "after": None})
                applier.apply_stage(self.ws, b.stage)
            except (applier.ApplyError, HistoryError) as e:
                self._set_report(b, "apply-failed", report.apply_failed(b, str(e)))
                self._log(b, f"apply failed: {e}")
                raise EngineError(f"apply failed: {e}", b.report, b) from None
            b.applied_changes = b.staged_changes
            b.status = states.APPLIED
            b.partial = bool(failed)
            self.ws.invalidate()
            self._log(b, f"applied{' partially' if failed else ''}: {len(b.applied_changes)} files changed: {b.subject}")
            for op in b.of_kind("command"):
                command = op.args["command"]
                on_event(f"op {op.index}: running {command}", True)
                result = self.runner.run(command, self.root, self.settings.timeout_seconds)
                b.runs[op.index] = result
                self._log(b, f"op {op.index} ran {command!r}: {outcome(result)}, {result.seconds:.1f} s")
                on_event(f"op {op.index}: {outcome(result)} after {result.seconds:.1f} s", result.exit_code == 0)
            try:
                after = self.history.snapshot(f"{b.message}\n\nCodetools-Batch: {b.id}")
                self._save_record(b.id, {"message": b.message, "before": before, "after": after})
            except HistoryError as e:
                self._log(b, f"no snapshot after apply, so it can't be undone: {e}")
                on_event(f"batch {b.id} can't be undone: {e}", False)
            self._set_report(b, "applied", report.applied(b))
            return b

    def kill_command(self) -> bool:
        return self.runner.kill()

    def reject(self, batch_id: int, note: str = "") -> Batch:
        with self._lock:
            b = self._pending(batch_id)
            b.status = states.REJECTED
            self._set_report(b, "rejected", report.operator_rejected(b, note))
            self._log(b, "rejected by operator")
            return b

    def undo(self, batch_id: int) -> str:
        with self._lock:
            try:
                record, files = self._undo(batch_id)
            except HistoryError as e:
                raise EngineError(f"can't undo batch {batch_id}: {e}") from None
            self.ws.invalidate()
            text = report.package(report.undone(batch_id, record["message"], files), self.settings.fold)
            b = self.batches.get(batch_id)
            if b is not None:
                b.status = states.UNDONE
                b.report = text
            self._save_text(batch_id, "report-undone.txt", text)
            self._log_line(f"batch {batch_id} undone: {len(files)} files restored")
            return text

    def _undo(self, batch_id: int) -> tuple[dict, list]:
        record_path = self._dir(batch_id) / "history.json"
        if not record_path.is_file():
            raise HistoryError("that batch was never applied")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("undone_at"):
            raise HistoryError("that batch was already undone")
        if record["after"] is None:
            raise HistoryError("it has no snapshot from after it was applied")
        now = self.history.snapshot(f"Project state before undoing batch {batch_id}")
        files = self.history.changes(record["before"], record["after"])
        later = {f.path for f in self.history.changes(record["after"], now)}
        conflicts = [f.path for f in files if f.path in later]
        if conflicts:
            raise HistoryError(f"files changed after the batch was applied: {', '.join(conflicts)}")
        self.history.restore(record["before"], files)
        applier.prune(self.ws, [f.path for f in files if f.status == "A"], [])
        subject = record["message"].split("\n", 1)[0]
        record["undone_at"] = applier.timestamp()
        record["undo"] = self.history.snapshot(f"Undo batch {batch_id}: {subject}")
        self._save_record(batch_id, record)
        return record, files

    def _save_record(self, batch_id: int, record: dict) -> None:
        self._save_text(batch_id, "history.json", json.dumps(record, indent=2))

    def pending_ids(self) -> list[int]:
        return [b.id for b in self.batches.values() if b.status == states.PENDING]

    def _pending(self, batch_id: int) -> Batch:
        b = self.batches.get(batch_id)
        if b is None:
            raise EngineError(f"no batch {batch_id} in this session")
        if b.status != states.PENDING:
            raise EngineError(f"batch {batch_id} is {b.status}, not pending")
        return b

    def _next_id(self) -> int:
        on_disk = [int(p.name) for p in self._batches_dir.glob("*") if p.name.isdigit()] if self._batches_dir.is_dir() else []
        return max([0, *on_disk, *self.batches]) + 1

    def _dir(self, batch_id: int) -> Path:
        return self._batches_dir / f"{batch_id:04d}"

    def _save_text(self, batch_id: int, name: str, text: str) -> None:
        d = self._dir(batch_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def _save(self, b: Batch, name: str, text: str) -> None:
        self._save_text(b.id, name, text)

    def _set_report(self, b: Batch, kind: str, text: str) -> None:
        b.report = report.package(text, self.settings.fold)
        self._save(b, f"report-{kind}.txt", b.report)

    def _log(self, b: Batch, message: str) -> None:
        self._log_line(f"batch {b.id} {message}")

    def _log_line(self, message: str) -> None:
        self.ws.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.ws.state_dir / "log.txt").open("a", encoding="utf-8") as f:
            f.write(f"{applier.timestamp()} {message}\n")


def _clean_pin(raw: str) -> str:
    return raw.strip().strip("'\"").replace("\\", "/").strip("/")


def outcome(result: CommandResult) -> str:
    if result.timed_out:
        return "timed out"
    if result.killed:
        return "killed"
    return f"exit {result.exit_code}"
