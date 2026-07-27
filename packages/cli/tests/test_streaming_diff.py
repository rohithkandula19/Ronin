from __future__ import annotations

import io

from rich.console import Console

from ronin_cli.streaming_diff import StreamingDiffRenderer, render_unified_diff


_DIFF = """--- a/example.py
+++ b/example.py
@@ -1,2 +1,2 @@
-value = \"old\"
+value = \"[bold]new[/bold]\"
 print(value)
"""


def test_streaming_diff_waits_for_complete_lines() -> None:
    renderer = StreamingDiffRenderer(path="example.py", width=48, max_rows=None)
    split_at = _DIFF.index("new")

    partial = renderer.feed(_DIFF[:split_at])
    renderer.feed(_DIFF[split_at:])
    complete = renderer.finish()

    assert "[bold]new[/bold]" not in "\n".join(row.plain for row in partial)
    assert "[bold]new[/bold]" in "\n".join(row.plain for row in complete)


def test_streaming_diff_rows_fit_requested_width_and_summarize() -> None:
    diff = _DIFF + "".join(f"+line_{i} = {'x' * 80}\n" for i in range(20))
    rows = render_unified_diff(diff, path="example.py", width=36, max_rows=3)

    assert all(row.cell_len <= 36 for row in rows)
    assert "more lines" in rows[-1].plain


def test_streaming_diff_never_interprets_diff_content_as_rich_markup() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, no_color=True, width=80)

    for row in render_unified_diff(_DIFF, path="example.py", width=80, max_rows=None):
        console.print(row)

    output = buf.getvalue()
    assert "[bold]new[/bold]" in output
    assert "\x1b[" not in output


def test_streaming_diff_raw_fallback_is_safe_and_width_bounded() -> None:
    rows = render_unified_diff("[red]not a unified diff[/red]", width=24)

    assert rows[0].plain.startswith("[red]not a unified")
    assert rows[0].plain.endswith("...")
    assert rows[0].cell_len <= 24
