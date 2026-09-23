import time

from codetools.commands import Runner, _clip_output


def test_timeout_kills_the_process_tree(tmp_path):
    start = time.monotonic()
    result = Runner().run("sleep 30 | cat", tmp_path, timeout=1)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - start < 10


def test_stdin_is_closed(tmp_path):
    result = Runner().run("read line; echo \"got:$line\"", tmp_path, timeout=10)
    assert result.output == "got:"


def test_long_output_keeps_head_and_tail():
    text = "\n".join(str(i) for i in range(1000))
    clipped = _clip_output(text).split("\n")
    assert clipped[0] == "0" and clipped[-1] == "999"
    assert "[600 lines omitted]" in clipped[100]
