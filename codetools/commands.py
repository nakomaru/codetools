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


SHELL_CHOICES = ("auto", "bash", "powershell")

_POWERSHELL_PRELUDE = ("$ProgressPreference = 'SilentlyContinue'; "
                       "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                       "$OutputEncoding = [Console]::OutputEncoding\n")
# A PowerShell script's exit code only reports whether its last statement failed. This gives the exit code of a
# failing native command, 1 for a failing cmdlet, and 0 otherwise, as bash does for its last command.
_POWERSHELL_EPILOGUE = "\nif ($?) { exit 0 }\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\nexit 1\n"


@dataclass(frozen=True)
class Shell:
    name: str
    path: str
    powershell: bool = False

    def argv(self, command: str) -> list[str]:
        if not self.powershell:
            return [self.path, "-c", command]
        return [self.path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                _POWERSHELL_PRELUDE + command + _POWERSHELL_EPILOGUE]

    def guidance(self) -> str:
        """What the bot needs to know to write commands for this shell."""
        if not self.powershell:
            return ""
        chaining = ("`&&` and `||` work" if self.name.startswith("PowerShell 7") else
                    "no `&&` or `||`: chain with `;` and check `$LASTEXITCODE`")
        return (f"PowerShell syntax, {chaining}. Unix tools such as grep, sed, and head don't exist. stderr is "
                "already captured, so don't add `2>&1`.")


@lru_cache(maxsize=None)
def find_shell(preference: str = "auto") -> Shell | None:
    """The shell run ops use. auto prefers bash (Git Bash on Windows), then PowerShell 7, then Windows
    PowerShell."""
    if preference in ("auto", "bash"):
        bash = _find_bash()
        if bash:
            return Shell("Git Bash" if os.name == "nt" else "bash", bash)
    if preference in ("auto", "powershell"):
        return _find_powershell()
    return None


def _find_bash() -> str | None:
    """Git Bash on Windows (never WSL's System32 bash), plain bash or sh elsewhere."""
    if os.name != "nt":
        return shutil.which("bash") or shutil.which("sh")
    roots = [parent for git in filter(None, [shutil.which("git")]) for parent in Path(git).resolve().parents[:3]]
    installs = {"ProgramFiles": "Git", "ProgramW6432": "Git", "LOCALAPPDATA": "Programs/Git"}
    roots += [Path(os.environ[var]) / sub for var, sub in installs.items() if var in os.environ]
    for root in roots:
        for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def _find_powershell() -> Shell | None:
    pwsh = shutil.which("pwsh")
    if pwsh:
        return Shell("PowerShell 7 (pwsh)", pwsh, powershell=True)
    if os.name != "nt":
        return None
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
    windows = shutil.which("powershell") or str(system / "powershell.exe")
    if Path(windows).is_file():
        return Shell("Windows PowerShell (powershell.exe)", windows, powershell=True)
    return None


class Runner:
    """Runs one command at a time and lets another thread kill it."""

    def __init__(self, shell_preference: str = "auto"):
        self.shell_preference = shell_preference
        self._proc: subprocess.Popen | None = None
        self._job = None
        self._killed = False

    def run(self, command: str, cwd: Path, timeout: float) -> CommandResult:
        shell = find_shell(self.shell_preference)
        if shell is None:
            return CommandResult(None, f"no shell found for commands.shell: {self.shell_preference}", 0.0)
        env = dict(os.environ, GIT_PAGER="cat", PAGER="cat", GIT_TERMINAL_PROMPT="0")
        if os.name == "nt":
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW | winjob.CREATE_SUSPENDED}
        else:
            flags = {"start_new_session": True}
        start = time.monotonic()
        self._killed = False
        self._proc = subprocess.Popen(shell.argv(command), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
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
