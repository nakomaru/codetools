import time

import pytest

from codetools.commands import Runner, _clip_output, find_shell

requires_bash = pytest.mark.skipif(find_shell("bash") is None, reason="bash is not installed")
requires_powershell = pytest.mark.skipif(find_shell("powershell") is None, reason="PowerShell is not installed")


@requires_bash
def test_timeout_kills_the_process_tree(tmp_path):
    start = time.monotonic()
    result = Runner("bash").run("sleep 30 | cat", tmp_path, timeout=1)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - start < 10


@requires_bash
def test_stdin_is_closed(tmp_path):
    result = Runner("bash").run("read line; echo \"got:$line\"", tmp_path, timeout=10)
    assert result.output == "got:"


@requires_powershell
def test_powershell_output_is_utf8(tmp_path):
    result = Runner("powershell").run("Write-Output 'caf\u00e9 \u65e5\u672c'", tmp_path, timeout=30)
    assert (result.exit_code, result.output) == (0, "caf\u00e9 \u65e5\u672c")


@requires_powershell
def test_powershell_passes_quotes_and_backslashes_through(tmp_path):
    text = r'a\"b c:\x\ "q" \\" $notvar `n'
    result = Runner("powershell").run("[Console]::Out.Write('" + text + "')", tmp_path, timeout=30)
    assert result.output == text


@requires_powershell
def test_powershell_exit_codes(tmp_path):
    runner = Runner("powershell")
    assert runner.run("cmd /c exit 4", tmp_path, timeout=30).exit_code == 4
    assert runner.run("cmd /c exit 4; Write-Output fine", tmp_path, timeout=30).exit_code == 0
    failed = runner.run("Get-Item no-such-file", tmp_path, timeout=30)
    assert failed.exit_code == 1 and "Cannot find path" in failed.output and "CLIXML" not in failed.output
    assert runner.run("if (1) {", tmp_path, timeout=30).exit_code == 1


@requires_powershell
def test_powershell_timeout_kills_the_process_tree(tmp_path):
    start = time.monotonic()
    result = Runner("powershell").run("Start-Sleep -Seconds 30", tmp_path, timeout=2)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - start < 10


def test_long_output_keeps_head_and_tail():
    text = "\n".join(str(i) for i in range(1000))
    clipped = _clip_output(text).split("\n")
    assert clipped[0] == "0" and clipped[-1] == "999"
    assert "[600 lines omitted]" in clipped[100]
