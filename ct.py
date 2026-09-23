"""Launcher for codetools: `ct [PROJECT_ROOT]` from any directory, via the shims in bin/.

Keeps its own virtual environment at ~/venvs/.venv_codetools, creating it when it doesn't exist and
reinstalling requirements.txt whenever that file changes. Python bytecode from that venv's interpreter goes to
the venv's pycache/ folder, so the repo stays free of __pycache__ and .pytest_cache.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO = Path(__file__).resolve().parent
VENV = Path.home() / "venvs" / ".venv_codetools"
REQUIREMENTS = REPO / "requirements.txt"
STAMP = VENV / "codetools-requirements.sha256"
PYCACHE_PTH_NAME = "codetools-pycache.pth"
PYCACHE_PTH = "import os, sys; sys.pycache_prefix = os.path.join(sys.prefix, 'pycache')\n"


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


def ensure_pycache_redirect() -> None:
    """A .pth file that points every interpreter run from this venv, ct and pytest alike, at the venv's
    pycache/ folder."""
    pth = Path(sysconfig.get_paths()["purelib"]) / PYCACHE_PTH_NAME
    if not pth.is_file() or pth.read_text(encoding="utf-8") != PYCACHE_PTH:
        pth.write_text(PYCACHE_PTH, encoding="utf-8")


def remove_stray_caches() -> None:
    for cache in (REPO / ".pytest_cache", *REPO.glob("**/__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)


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
    ensure_pycache_redirect()
    remove_stray_caches()
    sys.pycache_prefix = os.path.join(sys.prefix, "pycache")
    sys.path.insert(0, str(REPO))
    from codetools.__main__ import main as run
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
