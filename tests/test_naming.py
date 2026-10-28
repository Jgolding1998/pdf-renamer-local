import pytest

from pdf_renamer_local.naming import assign_names, display_name, safe_stem, valid_prefix


def test_safe_stem_combines_prefix_and_value():
    assert safe_stem("Customer-", "C10042") == "Customer-C10042"
    assert safe_stem("", "A-1") == "A-1"


@pytest.mark.parametrize("prefix", ["../", "a/b", r"a\b", "a b", "a.b", "x" * 33, "é", "a:b", "\x00"])
def test_prefix_allowlist_rejects_unsafe(prefix):
    assert not valid_prefix(prefix)
    assert safe_stem(prefix, "C1") is None


@pytest.mark.parametrize("value", ["../C1", "C1/..", r"C1\x", "C 1", ".C1", "C1.", "", "-C1"])
def test_unsafe_values_rejected(value):
    assert safe_stem("", value) is None


@pytest.mark.parametrize("value", ["CON", "con", "NUL", "COM1", "lpt9", "AUX", "PRN", "COM0"])
def test_windows_reserved_names_are_escaped(value):
    assert safe_stem("", value) == "_" + value


def test_reserved_check_is_on_full_stem():
    assert safe_stem("CO", "N") == "_CON"
    assert safe_stem("X-", "CON") == "X-CON"


def test_duplicates_get_deterministic_suffixes():
    assert assign_names(["A", "A", None, "A", "B"]) == ["A.pdf", "A-2.pdf", None, "A-3.pdf", "B.pdf"]


def test_duplicates_are_case_insensitive():
    assert assign_names(["abc", "ABC", "Abc"]) == ["abc.pdf", "ABC-2.pdf", "Abc-3.pdf"]


def test_suffix_never_collides_with_real_value():
    assert assign_names(["A", "A-2", "A"]) == ["A.pdf", "A-2.pdf", "A-3.pdf"]
    assert assign_names(["A", "A", "A-2"]) == ["A.pdf", "A-2.pdf", "A-2-2.pdf"]


def test_display_name_strips_paths_and_controls():
    assert display_name("C:" + chr(92) + "Users" + chr(92) + "x" + chr(92) + ".." + chr(92) + "evil.pdf") == "evil.pdf"
    assert display_name("../../etc/passwd") == "passwd"
    assert display_name("a\x00b\u202e.pdf") == "ab.pdf"
    assert display_name("") == "(unnamed)"
    assert len(display_name("x" * 1000)) == 120
