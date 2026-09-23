import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config

if os.name == "nt":
    from . import winjob


@dataclass
class CommandResult:
    exit_code: int | None
    output: str
    seconds: float
    timed_out: bool = False
    killed: bool = False


@lru_cache(maxsize=1)
def find_bash() -> str | None:
    """Git Bash on Windows (never WSL's System32 bash), plain bash elsewhere."""
    if os.name != "nt":
        return shutil.which("bash") or "/bin/sh"
    git = shutil.which("git")
    if git:
        for parent in Path(git).resolve().parents[:3]:
            for candidate in (parent / "bin" / "bash.exe", parent / "usr" / "bin" / "bash.exe"):
                if candidate.is_file():
                    return str(candidate)
    return None


class Runner:
    """Runs one command at a time and lets another thread kill it."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._job = None
        self._killed = False

    def run(self, command: str, cwd: Path, timeout: float) -> CommandResult:
        bash = find_bash()
        if bash is None:
            return CommandResult(None, "Git Bash not found; install Git for Windows", 0.0)
        env = dict(os.environ, GIT_PAGER="cat", PAGER="cat", GIT_TERMINAL_PROMPT="0")
        if os.name == "nt":
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW | winjob.CREATE_SUSPENDED}
        else:
            flags = {"start_new_session": True}
        start = time.monotonic()
        self._killed = False
        self._proc = subprocess.Popen([bash, "-c", command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **flags)
        if os.name == "nt":
            self._job = winjob.Job()
            try:
                self._job.adopt_and_resume(self._proc.pid)
            except OSError:
                self._proc.kill()
                self._proc.communicate()
                self._finish()
                raise
        timed_out = False
        try:
            out, _ = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.kill()
            out, _ = self._proc.communicate()
        code = self._proc.returncode
        self._finish()
        text = out.decode("utf-8", errors="replace").replace("\r\n", "\n")
        killed = self._killed and not timed_out
        return CommandResult(None if timed_out or killed else code, _clip_output(text), time.monotonic() - start,
                             timed_out, killed)

    def _finish(self) -> None:
        self._proc = None
        if self._job is not None:
            self._job.close()
            self._job = None

    def kill(self) -> bool:
        """Kill the running command and everything it started."""
        proc = self._proc
        if proc is None:
            return False
        self._killed = True
        job = self._job
        if job is not None:
            job.terminate()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True


def _clip_output(text: str) -> str:
    lines = text.rstrip("\n").split("\n")
    head, tail = config.COMMAND_OUTPUT_HEAD_LINES, config.COMMAND_OUTPUT_TAIL_LINES
    if len(lines) <= head + tail:
        return "\n".join(lines)
    omitted = len(lines) - head - tail
    return "\n".join(lines[:head] + [f"... [{omitted} lines omitted] ..."] + lines[-tail:])
