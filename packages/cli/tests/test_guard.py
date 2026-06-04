"""Tests for `ronin guard` — diff parsing + debug/secret leftover scan."""
from __future__ import annotations

from ronin_cli.guard import GuardResult, changed_files, iter_added_lines, scan_added

_DIFF = """\
diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,6 @@
 def f():
-    return 1
+    breakpoint()
+    print("ok")  # TODO: clean up
+    return 1
diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -1 +1,2 @@
 x = 1
+key = "AKIAIOSFODNN7EXAMPLE"
"""


def test_changed_files_lists_plus_side() -> None:
    assert changed_files(_DIFF) == ["app.py", "auth.py"]


def test_iter_added_lines_tracks_file_and_strips_marker() -> None:
    pairs = list(iter_added_lines(_DIFF))
    # the `+++` header lines are excluded; content has no leading '+'
    assert ("app.py", '    breakpoint()') in pairs
    assert ("auth.py", 'key = "AKIAIOSFODNN7EXAMPLE"') in pairs
    assert all(not text.startswith("+") for _, text in pairs)


def test_scan_finds_debug_and_secret_with_severity() -> None:
    findings = scan_added(_DIFF)
    kinds = {f.kind for f in findings}
    assert {"breakpoint", "todo", "aws-key"} <= kinds
    aws = next(f for f in findings if f.kind == "aws-key")
    assert aws.severity == "high" and aws.file == "auth.py"


def test_high_severity_property() -> None:
    res = GuardResult(findings=scan_added(_DIFF), changed_files=changed_files(_DIFF))
    assert any(f.kind == "aws-key" for f in res.high)
    assert all(f.severity == "high" for f in res.high)


def test_clean_diff_has_no_findings() -> None:
    clean = ("--- a/x.py\n+++ b/x.py\n@@ -1 +1,2 @@\n y = 1\n+    return y + 1\n")
    assert scan_added(clean) == []
