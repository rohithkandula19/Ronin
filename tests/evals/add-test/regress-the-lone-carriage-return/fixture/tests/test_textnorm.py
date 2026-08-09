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
