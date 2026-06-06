"""Tests for chmod permission explain/convert."""
from __future__ import annotations

from ronin_cli.chmod_explain import explain_perm, octal_to_symbolic, symbolic_to_octal


def test_octal_to_symbolic() -> None:
    assert octal_to_symbolic("755") == "rwxr-xr-x"
    assert octal_to_symbolic("644") == "rw-r--r--"
    assert octal_to_symbolic("0700") == "rwx------"
    assert octal_to_symbolic("999") is None


def test_symbolic_to_octal() -> None:
    assert symbolic_to_octal("rwxr-xr-x") == "755"
    assert symbolic_to_octal("rw-r--r--") == "644"
    assert symbolic_to_octal("-rwxr-xr-x") == "755"   # leading file-type char
    assert symbolic_to_octal("nope") is None


def test_explain_both_directions() -> None:
    a = explain_perm("644")
    assert a["symbolic"] == "rw-r--r--" and a["owner"] == "read, write" and a["others"] == "read"
    b = explain_perm("rwxr-xr-x")
    assert b["octal"] == "755" and b["group"] == "read, execute"


def test_explain_invalid() -> None:
    assert "error" in explain_perm("hello")
