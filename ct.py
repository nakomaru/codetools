"""Launcher for codetools: `ct [PROJECT_ROOT]` from any directory, via the shims in bin/.

Keeps its own virtual environment at ~/venvs/.venv_codetools, creating it when it doesn't exist and
reinstalling requirements.txt whenever that file changes.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
VENV = Path.home() / "venvs" / ".venv_codetools"
REQUIREMENTS = REPO / "requirements.txt"
STAMP = VENV / "codetools-requirements.sha256"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def running_in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV.resolve()


def say(message: str) -> None:
    print(f"ct: {message}", file=sys.stderr, flush=True)


def ensure_requirements() -> None:
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.is_file() and STAMP.read_text(encoding="utf-8").strip() == wanted:
        return
    say(f"installing {REQUIREMENTS.name} into {VENV}")
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r",
                    str(REQUIREMENTS)], check=True)
    STAMP.write_text(wanted, encoding="utf-8")


def main() -> int:
    if not running_in_venv():
        if not venv_python().is_file():
            say(f"creating {VENV}")
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        try:
            return subprocess.call([str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]])
        except KeyboardInterrupt:
            return 130
    ensure_requirements()
    sys.path.insert(0, str(REPO))
    from codetools.__main__ import main as run
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
