from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

from . import batch as states, clipboard, config
from .batch import Batch
from .context import protocol_text
from .engine import Engine, EngineError, IngestResult, StaleBatch
from .protocol import has_batch
from .report import package
from .textfile import plural
from .watchlock import WatchLock

HELP = """\
start of a chat:
  context            copy protocol + git state + tree + pinned files
  pin PATH|GLOB      include a file in full in `context` (unpin PATH, pins to list)
  protocol           copy only the bot instructions
batches (the id is required, so an apply always names what you reviewed):
  apply N            apply batch N; every op must have passed preflight
  applypartial N     apply the passing ops of batch N, reject the failing ones
  reject N [note]    discard batch N and copy a rejection (with your note) for the bot
  undo N             restore the files batch N changed
  diff N             show batch N's staged or applied diffs again
  show N / copy N    print / re-copy batch N's latest report
clipboard:
  paste              ingest the clipboard now: a repeat, or a batch with no `=== end`
  watch [on|off]     toggle clipboard watching (one ct instance watches at a time)
other:
  kill               kill the running command and everything it started
  clear, help, quit"""


class CommandInput(Input):
    BINDINGS = [Binding("up", "history(-1)", show=False), Binding("down", "history(1)", show=False)]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.history: list[str] = []
        self.position = 0

    def remember(self, value: str) -> None:
        if value and (not self.history or self.history[-1] != value):
            self.history.append(value)
        self.position = len(self.history)

    def action_history(self, step: int) -> None:
        if not self.history:
            return
        self.position = max(0, min(len(self.history), self.position + step))
        self.value = self.history[self.position] if self.position < len(self.history) else ""
        self.cursor_position = len(self.value)


class CodetoolsApp(App):
    TITLE = "codetools"
    CSS = """
    #log { height: 1fr; }
    CommandInput { dock: bottom; }
    """
    BINDINGS = [Binding("ctrl+q", "quit", "Quit"), Binding("ctrl+l", "clear", "Clear")]

    def __init__(self, engine: Engine, watch: bool = True, lock: WatchLock | None = None):
        super().__init__()
        self.engine = engine
        self.watching = False
        self._want_watch = watch
        self._watch_lock = lock or WatchLock(engine.root)
        self._last_clip: str | None = None
        self._own_copies: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield CommandInput(placeholder="context | apply N | applypartial N | reject N | undo N | paste | help")
        yield Footer()

    def on_mount(self) -> None:
        for note in self.engine.startup_notes():
            self.out(Text(note, style="dim"))
        self.out(Text("type `context` to start a chat, `help` for commands", style="dim"))
        self._last_clip = clipboard.fingerprint(clipboard.read())
        if self._want_watch:
            holder = self._watch_lock.other_holder()
            if holder:
                self.out(Text(f"another ct (pid {holder['pid']}, {holder['root']}) is watching the clipboard; "
                              "`watch on` takes over", style="yellow"))
            else:
                self._set_watch(True, quiet=True)
        self.set_interval(config.CLIPBOARD_POLL_SECONDS, self._poll_clipboard)
        self._refresh_subtitle()
        self.query_one(CommandInput).focus()

    def on_unmount(self) -> None:
        self._watch_lock.release()

    def out(self, renderable) -> None:
        self.query_one("#log", RichLog).write(renderable)

    def _refresh_subtitle(self) -> None:
        pending = self.engine.pending_ids()
        parts = [str(self.engine.root), f"watch {'on' if self.watching else 'off'}"]
        if pending:
            parts.append(f"pending: {', '.join(map(str, pending))}")
        self.sub_title = "  |  ".join(parts)

    def _copy(self, text: str, what: str) -> None:
        if clipboard.write(text):
            fp = clipboard.fingerprint(text)
            self._own_copies.add(fp)
            self._last_clip = fp
            self.out(Text(f"{what} copied to clipboard ({len(text):,} chars)", style="bold cyan"))
        else:
            self.out(Text(f"clipboard write failed; use `show` to see the {what}", style="bold red"))

    def _show_error(self, message: str) -> None:
        self.out(Text(message, style="bold red"))

    def _set_watch(self, on: bool, quiet: bool = False) -> None:
        self.watching = on
        if on:
            self._watch_lock.claim()
            self._last_clip = clipboard.fingerprint(clipboard.read())
        else:
            self._watch_lock.release()
        if not quiet:
            self.out(Text(f"watch {'on' if on else 'off'}", style="dim"))
        self._refresh_subtitle()

    def _poll_clipboard(self) -> None:
        if not self.watching:
            return
        if not self._watch_lock.owned():
            holder = self._watch_lock.other_holder()
            if holder:
                self.watching = False
                self.out(Text(f"watch off: ct for {holder['root']} took over the clipboard", style="yellow"))
                self._refresh_subtitle()
                return
            self._watch_lock.claim()
        text = clipboard.read()
        fp = clipboard.fingerprint(text)
        if fp is None or fp == self._last_clip:
            return
        self._last_clip = fp
        if fp in self._own_copies or not has_batch(text):
            return
        self._ingest(text, force=False)

    @work(thread=True, group="engine")
    def _ingest(self, text: str, force: bool) -> None:
        try:
            result = self.engine.ingest(text, force)
        except Exception as e:
            self.call_from_thread(self._show_error, f"ingest failed: {type(e).__name__}: {e}")
            return
        self.call_from_thread(self._after_ingest, result, force)

    def _after_ingest(self, result: IngestResult, manual: bool) -> None:
        if result.incomplete:
            self.out(Text("clipboard batch has no `=== end` yet (still streaming?); copy the reply again when it "
                          "finishes, or `paste` to reject it as cut off", style="yellow"))
            return
        if result.duplicate_of is not None:
            self.out(Text(f"clipboard batch is identical to batch {result.duplicate_of}; `paste` ingests it again",
                          style="dim"))
            return
        b = result.batch
        if b is None:
            if manual:
                self.out(Text("no `=== batch` found in the clipboard", style="yellow"))
            return
        self._render_batch(b)
        self._copy(b.report, "report")
        self._refresh_subtitle()

    def _render_batch(self, b: Batch) -> None:
        self.out(Rule(f"batch {b.id}", style="cyan"))
        if b.status == states.MALFORMED:
            self.out(Text(f"MALFORMED: whole batch rejected, nothing ran ({plural(len(b.errors), 'error')})",
                          style="bold red"))
            for error in b.errors:
                self.out(Text(f"  {error}", style="red"))
            return
        for op in b.ops:
            self._render_op(b, op)
        self._render_diffs(b)
        self.out(self._status_line(b))

    def _render_op(self, b: Batch, op) -> None:
        r = b.results[op.index]
        line = Text(f"  op {op.index:>3} ")
        kind_style = {"query": "cyan", "change": "magenta", "command": "yellow"}[op.kind]
        line.append(op.title, style=kind_style)
        line.append("  ")
        line.append(r.summary if r.ok else f"FAILED: {r.summary}", style="green" if r.ok else "bold red")
        self.out(line)
        for note in r.notes:
            self.out(Text(f"           {note}", style="yellow"))
        if not r.ok and r.detail:
            self.out(Syntax(r.detail, "diff", theme="ansi_dark", word_wrap=True))

    def _render_diffs(self, b: Batch) -> None:
        for fc in b.applied_changes or b.staged_changes:
            self.out(Text(f"  {fc.describe()}", style="bold"))
            if fc.diff:
                self.out(Syntax(fc.diff, "diff", theme="ansi_dark", word_wrap=True))

    def _status_line(self, b: Batch) -> Text:
        gated, failed = b.gated(), b.failed()
        if b.status == states.DONE:
            return Text("queries done", style="bold green")
        passed = len(gated) - len(failed)
        if not failed:
            return Text(f"preflight: all {plural(len(gated), 'op')} passed. type `apply {b.id}`", style="bold green")
        if passed:
            return Text(f"preflight: {passed} of {len(gated)} ops passed. type `applypartial {b.id}` to apply "
                        f"{plural(passed, 'passing op')} and reject {plural(len(failed), 'failing op')}, "
                        "or send the report back", style="bold yellow")
        return Text(f"preflight: every op failed. send the report back to the bot", style="bold red")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        box = self.query_one(CommandInput)
        value = event.value.strip()
        box.value = ""
        box.remember(value)
        if not value:
            return
        self.out(Text(f"> {value}", style="bold"))
        verb, _, rest = value.partition(" ")
        verb, rest = verb.lower(), rest.strip()
        handler = {
            "apply": lambda: self._with_id(rest, lambda i: self._apply(i, False)),
            "applypartial": lambda: self._with_id(rest, lambda i: self._apply(i, True)),
            "reject": lambda: self._with_id(rest, self._reject, allow_note=True),
            "undo": lambda: self._with_id(rest, self._undo),
            "diff": lambda: self._with_id(rest, self._diff),
            "show": lambda: self._with_id(rest, self._show),
            "copy": lambda: self._with_id(rest, self._copy_report),
            "context": self._build_context,
            "pin": lambda: self._pin(rest, True),
            "unpin": lambda: self._pin(rest, False),
            "pins": self._pins,
            "protocol": self._copy_protocol,
            "paste": self._paste,
            "watch": lambda: self._set_watch({"on": True, "off": False}.get(rest.lower(), not self.watching)),
            "kill": self._kill,
            "clear": self.action_clear,
            "help": lambda: self.out(Text(HELP)),
            "quit": self.exit,
            "exit": self.exit,
        }.get(verb)
        if handler is None:
            self._show_error(f"unknown command {verb!r}; type `help`")
        else:
            handler()

    def _with_id(self, rest: str, action, allow_note: bool = False) -> None:
        raw_id, _, note = rest.partition(" ")
        if not raw_id.isdigit() or (note and not allow_note):
            pending = self.engine.pending_ids()
            hint = f" (pending: {', '.join(map(str, pending))})" if pending else ""
            self._show_error(f"needs a batch id{hint}")
            return
        if allow_note:
            action(int(raw_id), note.strip())
        else:
            action(int(raw_id))

    @work(thread=True, group="engine")
    def _apply(self, batch_id: int, partial: bool) -> None:
        def event(message: str, ok: bool) -> None:
            self.call_from_thread(self.out, Text(f"  {message}", style="yellow" if ok else "bold red"))

        try:
            b = self.engine.apply(batch_id, partial, event)
        except StaleBatch as e:
            self.call_from_thread(self._after_stale, e)
            return
        except EngineError as e:
            self.call_from_thread(self._after_engine_error, e)
            return
        except Exception as e:
            self.call_from_thread(self._show_error, f"apply failed: {type(e).__name__}: {e}")
            return
        self.call_from_thread(self._after_apply, b)

    def _after_stale(self, e: StaleBatch) -> None:
        self._show_error(str(e))
        self._render_batch(e.batch)
        self._copy(e.report, "re-preflight report")

    def _after_engine_error(self, e: EngineError) -> None:
        self._show_error(str(e))
        if e.report:
            self._copy(e.report, "failure report")
        self._refresh_subtitle()

    def _after_apply(self, b: Batch) -> None:
        label = "applied partially" if b.partial else "applied"
        self.out(Text(f"batch {b.id} {label}: {plural(len(b.applied_changes), 'file')} changed", style="bold green"))
        for fc in b.applied_changes:
            self.out(Text(f"  {fc.describe()}"))
        self._copy(b.report, "apply report")
        self._refresh_subtitle()

    def _reject(self, batch_id: int, note: str) -> None:
        try:
            b = self.engine.reject(batch_id, note)
        except EngineError as e:
            self._show_error(str(e))
            return
        self.out(Text(f"batch {b.id} rejected", style="bold yellow"))
        self._copy(b.report, "rejection")
        self._refresh_subtitle()

    def _undo(self, batch_id: int) -> None:
        try:
            text = self.engine.undo(batch_id)
        except EngineError as e:
            self._show_error(str(e))
            return
        self.out(Text(text.splitlines()[0], style="bold yellow"))
        self._copy(text, "undo report")
        self._refresh_subtitle()

    def _batch(self, batch_id: int) -> Batch | None:
        b = self.engine.batches.get(batch_id)
        if b is None:
            self._show_error(f"no batch {batch_id} in this session")
        return b

    def _diff(self, batch_id: int) -> None:
        b = self._batch(batch_id)
        if b is not None:
            self.out(Rule(f"batch {b.id} diffs ({b.status})", style="cyan"))
            if not (b.applied_changes or b.staged_changes):
                self.out(Text("no file changes", style="dim"))
            self._render_diffs(b)

    def _show(self, batch_id: int) -> None:
        b = self._batch(batch_id)
        if b is not None:
            self.out(Rule(f"batch {b.id} report ({b.status})", style="cyan"))
            self.out(Text(b.report))

    def _copy_report(self, batch_id: int) -> None:
        b = self._batch(batch_id)
        if b is not None:
            self._copy(b.report, f"batch {b.id} report")

    @work(thread=True, group="engine")
    def _build_context(self) -> None:
        try:
            ctx = self.engine.context()
        except Exception as e:
            self.call_from_thread(self._show_error, f"context failed: {type(e).__name__}: {e}")
            return
        self.call_from_thread(self._after_context, ctx)

    def _after_context(self, ctx) -> None:
        pinned = ", ".join(ctx.pinned) if ctx.pinned else "none"
        self.out(Text(f"context: {ctx.text.count(chr(10)):,} lines; pinned files: {pinned}", style="bold"))
        for warning in ctx.warnings:
            self.out(Text(f"  {warning}", style="yellow"))
        self._copy(ctx.text, "context")

    def _pin(self, rest: str, add: bool) -> None:
        if not rest:
            self._show_error(f"usage: {'pin' if add else 'unpin'} PATH|GLOB")
            return
        try:
            message = self.engine.pin(rest) if add else self.engine.unpin(rest)
        except EngineError as e:
            self._show_error(str(e))
            return
        self.out(Text(message, style="green"))

    def _pins(self) -> None:
        pins = self.engine.state.pins()
        if not pins:
            self.out(Text("nothing pinned; `pin PATH` adds a file to `context`", style="dim"))
            return
        resolved, warnings = self.engine.pins()
        self.out(Text(f"pins ({self.engine.state.pins_path}): {', '.join(pins)}", style="bold"))
        for rel in resolved:
            self.out(Text(f"  {rel}"))
        for warning in warnings:
            self.out(Text(f"  {warning}", style="yellow"))

    def _copy_protocol(self) -> None:
        text = package("[codetools] batch protocol\n" + protocol_text(), self.engine.settings.fold)
        self._copy(text, "protocol")

    def _paste(self) -> None:
        text = clipboard.read()
        if text is None:
            self._show_error("clipboard unavailable")
            return
        self._ingest(text, force=True)

    def _kill(self) -> None:
        if self.engine.kill_command():
            self.out(Text("kill sent", style="yellow"))
        else:
            self.out(Text("no command is running", style="dim"))

    def action_clear(self) -> None:
        self.query_one("#log", RichLog).clear()
