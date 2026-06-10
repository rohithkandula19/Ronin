"""Tests for splitting a multi-file git diff (so /diff highlights per language)."""
from __future__ import annotations

from ronin_cli.code_tools import split_git_diff

_TWO_FILE = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("a")
+print("b")
diff --git a/web/style.css b/web/style.css
index 333..444 100644
--- a/web/style.css
+++ b/web/style.css
@@ -1 +1 @@
-body{}
+body{color:red}
"""


def test_splits_per_file() -> None:
    parts = split_git_diff(_TWO_FILE)
    assert len(parts) == 2
    assert parts[0][0] == "app.py"
    assert parts[1][0] == "web/style.css"
    # each chunk carries that file's hunk
    assert 'print("b")' in parts[0][1]
    assert "color:red" in parts[1][1]


def test_single_file() -> None:
    parts = split_git_diff(_TWO_FILE.split("diff --git a/web")[0])
    assert len(parts) == 1 and parts[0][0] == "app.py"


def test_empty() -> None:
    assert split_git_diff("") == []
    assert split_git_diff("   \n  ") == []


def test_unparseable_path_is_none() -> None:
    parts = split_git_diff("diff --git garbage header\n+x\n")
    assert len(parts) == 1 and parts[0][0] is None
