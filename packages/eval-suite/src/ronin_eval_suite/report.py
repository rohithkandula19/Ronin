from __future__ import annotations

import html
from collections import Counter
from pathlib import Path

from .types import RunReport


def _bar_row(label: str, value: float, max_value: float, width_px: int = 320) -> str:
    pct = (value / max_value * 100) if max_value else 0
    width = int((value / max_value) * width_px) if max_value else 0
    return (
        f'<tr><td class="lbl">{html.escape(label)}</td>'
        f'<td><div class="bar" style="width:{width}px"></div></td>'
        f'<td class="val">{value:.2f} <span class="dim">({pct:.0f}%)</span></td></tr>'
    )


def _histogram(scores: list[int], scale: tuple[int, int]) -> str:
    lo, hi = scale
    counts = Counter(scores)
    total = sum(counts.values()) or 1
    cells: list[str] = []
    for v in range(lo, hi + 1):
        n = counts.get(v, 0)
        h = int((n / total) * 60) + 4
        cells.append(
            f'<div class="hcol"><div class="hbar" style="height:{h}px" title="{n} cases"></div>'
            f'<div class="hlbl">{v}</div></div>'
        )
    return f'<div class="hist">{"".join(cells)}</div>'


def render_html_report(report: RunReport, path: str | Path | None = None) -> str:
    """Render a self-contained HTML report. Returns the HTML string; writes to ``path`` if given."""
    rubric = report.rubric
    lo, hi = rubric.scale

    summary_rows = "\n".join(
        _bar_row(criterion, report.summary.get(criterion, 0.0), float(hi))
        for criterion in rubric.criteria
    )

    per_criterion_hists = []
    for criterion in rubric.criteria:
        values = [c.scores.get(criterion) for c in report.cases if c.error is None]
        ints = [v for v in values if isinstance(v, int)]
        per_criterion_hists.append(
            f"<div class='card'><h3>{html.escape(criterion)}</h3>"
            f"{_histogram(ints, rubric.scale)}</div>"
        )

    case_rows = []
    for c in report.cases:
        score_cells = " ".join(
            f"<span class='chip'>{html.escape(k)}: {v}</span>" for k, v in c.scores.items()
        )
        err = f"<div class='err'>{html.escape(c.error)}</div>" if c.error else ""
        case_rows.append(
            f"<details><summary><code>{html.escape(c.case_id)}</code> {score_cells}</summary>"
            f"<pre>{html.escape(c.output)[:2000]}</pre>"
            f"<p class='dim'>{html.escape(c.reasoning)}</p>{err}</details>"
        )

    label = html.escape(report.label or "")
    n_total = len(report.cases)
    n_errors = sum(1 for c in report.cases if c.error)

    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 980px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }
    h1 { margin-bottom: 0; }
    .meta { color: #666; margin-bottom: 2em; }
    .card { background: #f7f7f7; border-radius: 8px; padding: 1em 1.25em; margin: 1em 0; }
    table { border-collapse: collapse; width: 100%; }
    td { padding: 4px 8px; vertical-align: middle; }
    .lbl { font-weight: 500; width: 220px; }
    .bar { background: linear-gradient(90deg, #d4a373, #a98467); height: 14px; border-radius: 3px; }
    .val { font-variant-numeric: tabular-nums; width: 140px; }
    .dim { color: #888; }
    .hist { display: flex; gap: 6px; align-items: flex-end; height: 70px; }
    .hcol { display: flex; flex-direction: column; align-items: center; }
    .hbar { width: 32px; background: #a98467; border-radius: 3px 3px 0 0; }
    .hlbl { font-size: 11px; color: #666; margin-top: 2px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1em; }
    details { background: #fff; border: 1px solid #eaeaea; border-radius: 6px; padding: 0.5em 0.75em; margin: 0.5em 0; }
    summary { cursor: pointer; font-family: ui-monospace, monospace; }
    .chip { display: inline-block; background: #eee; border-radius: 10px; padding: 0 8px; margin: 0 4px; font-size: 12px; }
    pre { background: #fafafa; padding: 0.75em; border-radius: 4px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
    .err { color: #c0392b; font-size: 13px; margin-top: 0.5em; }
    """

    page = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Eval report — {label or report.target_model}</title>"
        f"<style>{css}</style></head><body>"
        f"<h1>Eval Report</h1>"
        f"<div class='meta'>"
        f"<strong>Target:</strong> {html.escape(report.target_model)} &nbsp;·&nbsp; "
        f"<strong>Judge:</strong> {html.escape(report.judge_model)} &nbsp;·&nbsp; "
        f"<strong>Cases:</strong> {n_total} ({n_errors} errored)"
        f"{'  &nbsp;·&nbsp; <strong>Label:</strong> ' + label if label else ''}"
        f"</div>"
        f"<div class='card'><h2>Summary (mean / {hi})</h2>"
        f"<table>{summary_rows}</table></div>"
        f"<div class='grid'>{''.join(per_criterion_hists)}</div>"
        f"<h2>Cases</h2>{''.join(case_rows)}"
        f"</body></html>"
    )

    if path is not None:
        Path(path).write_text(page, encoding="utf-8")
    return page
