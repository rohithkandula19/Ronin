import pytest

from loggy.rotate import rotate_if_needed


def test_a_missing_log_is_not_rotated(tmp_path):
    assert rotate_if_needed(tmp_path / "app.log", max_bytes=10) is False


def test_a_small_log_is_not_rotated(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"tiny")
    assert rotate_if_needed(log, max_bytes=1024) is False


def test_a_log_at_the_limit_is_rotated(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"0123456789")
    assert rotate_if_needed(log, max_bytes=10) is True


def test_rejects_a_zero_limit(tmp_path):
    with pytest.raises(ValueError):
        rotate_if_needed(tmp_path / "app.log", max_bytes=0)
