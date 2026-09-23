import json
import os

from codetools.watchlock import WatchLock


def test_second_instance_sees_the_first_and_can_take_over(tmp_path):
    path = tmp_path / "watch.json"
    first, second = WatchLock(tmp_path / "a", path), WatchLock(tmp_path / "b", path)
    first.claim()
    assert first.owned() and first.other_holder() is None
    holder = second.other_holder()
    assert holder["root"] == str(tmp_path / "a")
    second.claim()
    assert second.owned() and not first.owned()
    assert first.other_holder()["root"] == str(tmp_path / "b")
    first.release()
    assert path.exists()
    second.release()
    assert not path.exists()


def test_lock_left_by_a_dead_process_is_free(tmp_path):
    path = tmp_path / "watch.json"
    path.write_text(json.dumps({"pid": 2 ** 31 - 2, "root": "gone", "token": "x"}), encoding="utf-8")
    assert WatchLock(tmp_path, path).other_holder() is None
    path.write_text(json.dumps({"pid": os.getpid(), "root": "me", "token": "x"}), encoding="utf-8")
    assert WatchLock(tmp_path, path).other_holder()["root"] == "me"
