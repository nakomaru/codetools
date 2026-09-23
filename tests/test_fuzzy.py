import pytest

from codetools.fuzzy import LocateFailure, locate

FILE = ["class A:", "    def f(self):", "        return 1", "", "    def g(self):", "        return 1"]


def failure(search: list[str]) -> LocateFailure:
    with pytest.raises(LocateFailure) as e:
        locate(FILE, search, "a.py")
    return e.value


def test_exact_match_ignores_trailing_whitespace():
    loc = locate(["a  ", "b"], ["a", "b   "], "x")
    assert (loc.start, loc.end) == (0, 2)


def test_ambiguous_match_names_every_start_line():
    e = failure(["        return 1"])
    assert "matches 2 places" in e.message and "lines 3, 6" in e.message


def test_line_number_prefixes_are_diagnosed_not_applied():
    e = failure(["    2|     def f(self):", "    3|         return 1"])
    assert "prefixes copied from read output" in e.message


def test_indentation_difference_is_diagnosed_not_applied():
    e = failure(["def g(self):", "    return 1"])
    assert "lines 5-6 only if indentation is ignored" in e.message
    assert "+    def g(self):" in e.detail and "-def g(self):" in e.detail


def test_miss_reports_closest_region_as_diff():
    e = failure(["    def g(self):", "        return 42"])
    assert "closest match is lines 5-6" in e.message
    assert "+++ a.py lines 5-6 (actual)" in e.detail
    assert "-        return 42" in e.detail and "+        return 1" in e.detail


def test_miss_with_no_shared_lines():
    assert "no line of SEARCH appears" in failure(["nothing like it"]).message
