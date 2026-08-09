from docforge.textnorm import ensure_trailing_newline, normalize_bytes, normalize_newlines


def test_collapses_crlf_to_lf():
    assert normalize_newlines("first\r\nsecond\r\n") == "first\nsecond\n"


def test_collapses_crlf_in_bytes():
    assert normalize_bytes(b"first\r\nsecond\r\n") == b"first\nsecond\n"


def test_leaves_lf_only_text_alone():
    assert normalize_newlines("first\nsecond\n") == "first\nsecond\n"


def test_adds_a_missing_trailing_newline():
    assert ensure_trailing_newline("body") == "body\n"


def test_does_not_double_the_trailing_newline():
    assert ensure_trailing_newline("body\n") == "body\n"


def test_empty_text_stays_empty():
    assert ensure_trailing_newline("") == ""


def test_lone_carriage_returns_become_newlines_issue_212():
    assert normalize_newlines("first\rsecond\rthird") == "first\nsecond\nthird"
    assert normalize_bytes(b"first\rsecond\rthird") == b"first\nsecond\nthird"


def test_mixed_cr_and_crlf_endings_issue_212():
    assert normalize_bytes(b"a\r\nb\rc\n") == b"a\nb\nc\n"
