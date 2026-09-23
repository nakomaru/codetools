import asyncio
from pathlib import Path

import pytest

from codetools import clipboard
from codetools.engine import Engine
from codetools.tui import CodetoolsApp
from codetools.watchlock import WatchLock


class FakeClipboard:
    def __init__(self, monkeypatch):
        self.text = ""
        monkeypatch.setattr(clipboard, "read", lambda: self.text)
        monkeypatch.setattr(clipboard, "write", self._write)

    def _write(self, text: str) -> bool:
        self.text = text
        return True


async def wait_for(condition, pilot, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", "utf-8")
    return tmp_path


def test_watch_ingests_copies_report_and_applies_partially(project, monkeypatch):
    clip = FakeClipboard(monkeypatch)

    async def scenario():
        app = CodetoolsApp(Engine(project), lock=WatchLock(project, project.parent / "watch.json"))
        async with app.run_test() as pilot:
            clip.text = "\n".join(["=== batch", "=== edit a.py", "<<<<<<< SEARCH", "x = 1", "=======", "x = 10",
                                   ">>>>>>> REPLACE", "=== edit a.py", "<<<<<<< SEARCH", "z = 3", "=======",
                                   "z = 4", ">>>>>>> REPLACE", "=== end"])
            await wait_for(lambda: clip.text.startswith("[codetools] batch 1: preflight 1 of 2"), pilot)
            report = clip.text

            await pilot.click("CommandInput")
            await pilot.press(*"applypartial 1", "enter")
            await wait_for(lambda: clip.text.startswith("[codetools] batch 1 applied partially"), pilot)
            await pilot.pause(1.2)
            assert app.engine.batches.keys() == {1}
            return report

    report = asyncio.run(scenario())
    assert "rejects op 2" in report
    assert (project / "a.py").read_text("utf-8") == "x = 10\ny = 2\n"
