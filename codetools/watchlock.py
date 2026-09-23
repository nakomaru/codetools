"""One clipboard watcher per machine, so a batch copied from one chat is never ingested by two projects.

The lock is a small JSON file in the temp directory naming the watching process. Turning watch on takes the
lock; an instance that finds it has lost the lock turns its own watch off.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

if os.name == "nt":
    from .winjob import pid_alive
else:
    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

DEFAULT_PATH = Path(tempfile.gettempdir()) / "codetools-watch.json"


class WatchLock:
    def __init__(self, root: Path, path: Path = DEFAULT_PATH):
        self.root = root
        self.path = path
        self.token = uuid.uuid4().hex

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def other_holder(self) -> dict | None:
        """The live instance holding the lock, when it isn't this one."""
        data = self._read()
        if not data or data.get("token") == self.token or not pid_alive(int(data.get("pid", 0))):
            return None
        return data

    def claim(self) -> None:
        data = {"pid": os.getpid(), "root": str(self.root), "token": self.token}
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def owned(self) -> bool:
        data = self._read()
        return bool(data) and data.get("token") == self.token

    def release(self) -> None:
        if self.owned():
            self.path.unlink(missing_ok=True)
