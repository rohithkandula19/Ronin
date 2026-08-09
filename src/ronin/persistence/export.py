"""Exporters: a recorded session as Markdown, or as one shareable HTML file.

Both are pure functions of ``(events, metadata) -> str``. No file writing, no
clock, no cwd lookup: an exporter that reads anything is an exporter you cannot
test on a fixture, and the caller that has the path is the caller that should own
the write.

Both use :func:`ronin.persistence.resume.fold_turns`, so the exported prose and the
resumed state apply the same ``StreamReset`` rule. Duplicating that rule here would
eventually mean an export that shows an answer twice while the resumed session
shows it once.

**The HTML is the security-relevant one.** A transcript contains model output, file
contents, shell output and tool arguments — arbitrary text from arbitrary sources —
and the export is a file people paste into chats and attach to bug reports. So
every interpolated value goes through :func:`html.escape` with ``quote=True``,
including inside ``<summary>`` and ``<pre>``: a ``</details>`` in a file the agent
read would otherwise close the disclosure early, and a ``<script>`` in it would
otherwise run in whoever opened the file. There is no ``<script>``, no ``<link>``,
no remote font and no CDN reference in the output — ``<details>`` is native HTML, so
collapsing tool calls needs no JavaScript at all, and a self-contained file is one
that renders identically offline in five years.

Markdown is not escaped, deliberately and only where it is the point: the
assistant's prose *is* Markdown and rendering it is the reason to export Markdown.
Everything that is not prose — tool arguments, tool results, approval renderings —
goes inside a fence whose length is computed from the content, so a result
containing three backticks cannot break out of its block.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from html import escape

from ..core.types import Event
from .resume import ReplayTurn, fold_turns
from .transcript import SessionMeta

_STYLE = """
:root { color-scheme: light dark; --fg:#1a1a1a; --bg:#fdfdfc; --dim:#5a5a5a;
        --line:#dcdcd8; --panel:#f4f4f1; --err:#8a1f11; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e6; --bg:#191919; --dim:#a0a09a; --line:#333;
          --panel:#222220; --err:#ff9b8a; }
}
body { max-width: 46rem; margin: 2rem auto; padding: 0 1rem; background: var(--bg);
       color: var(--fg); font: 15px/1.6 ui-sans-serif, system-ui, sans-serif; }
h1 { font-size: 1.35rem; } h2 { font-size: 1.05rem; margin-top: 2rem; }
table.meta { border-collapse: collapse; font-size: .85rem; width: 100%; }
table.meta th { text-align: left; color: var(--dim); font-weight: 500;
                padding: .15rem .75rem .15rem 0; vertical-align: top;
                white-space: nowrap; }
table.meta td { padding: .15rem 0; word-break: break-word; }
section.turn { border-top: 1px solid var(--line); padding-top: .5rem; }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 4px;
      padding: .6rem .7rem; overflow-x: auto; font-size: .82rem; white-space: pre-wrap;
      word-break: break-word; }
details { border: 1px solid var(--line); border-radius: 4px; padding: .35rem .6rem;
          margin: .5rem 0; background: var(--panel); }
summary { cursor: pointer; font-size: .85rem; }
.prose { white-space: pre-wrap; }
.dim { color: var(--dim); font-size: .82rem; }
.err { color: var(--err); }
.tag { font-family: ui-monospace, monospace; font-size: .8rem; }
"""


def _stamp(when: float) -> str:
    """UTC, seconds precision. A shared export must not depend on the reader's zone."""
    if when <= 0:
        return "unrecorded"
    return datetime.fromtimestamp(when, tz=UTC).isoformat(timespec="seconds")


def _meta_rows(meta: SessionMeta, turns: Sequence[ReplayTurn], events: int) -> tuple[
    tuple[str, str], ...
]:
    """The header every export carries. One source, so the two agree."""
    return (
        ("session", meta.session_id),
        ("title", meta.title or "—"),
        ("model", meta.model or "unrecorded"),
        ("cwd", meta.cwd),
        ("started", _stamp(meta.started_at)),
        ("updated", _stamp(meta.updated_at)),
        ("duration", f"{meta.duration_seconds:.2f}s"),
        ("cost", f"${meta.cost_usd:.4f}"),
        ("turns", f"{meta.turns} recorded / {len(turns)} replayed"),
        ("events", str(events)),
        ("files touched", ", ".join(meta.files_touched) or "none recorded"),
        ("checkpoints", ", ".join(meta.checkpoint_ids) or "none recorded"),
        ("stale metadata", "yes (sidecar missing)" if meta.stale else "no"),
    )


def _fence(content: str) -> str:
    """A fence longer than the longest backtick run inside ``content``.

    Deterministic, and the reason a tool result containing ``` cannot break out of
    its code block and start injecting Markdown into the document.
    """
    longest = 0
    run = 0
    for char in content:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _block(content: str, language: str = "") -> str:
    fence = _fence(content)
    body = content if content.endswith("\n") or not content else content + "\n"
    return f"{fence}{language}\n{body}{fence}"


def _cell(value: str) -> str:
    """A metadata value that cannot break out of its Markdown table cell."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _arguments(turn_arguments: object) -> str:
    """Tool arguments as stable, readable JSON; unserializable values as ``repr``."""
    return json.dumps(turn_arguments, indent=2, sort_keys=True, default=repr)


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def to_markdown(events: Sequence[Event], meta: SessionMeta) -> str:
    """The session as Markdown: one heading per turn, tool calls as fenced blocks."""
    turns = fold_turns(events)
    out: list[str] = [f"# ronin session `{meta.session_id}`", ""]
    out.append("| field | value |")
    out.append("| --- | --- |")
    for name, value in _meta_rows(meta, turns, len(events)):
        out.append(f"| {name} | {_cell(value)} |")
    out.append("")

    if not turns:
        out.append("_no turns were recorded._")
        return "\n".join(out) + "\n"

    for position, turn in enumerate(turns, start=1):
        out.append(f"## Turn {position} (index {turn.index})")
        out.append("")
        if turn.resets:
            out.append(
                f"> stream was reset {turn.resets}× — text before the last reset was "
                f"discarded, as the recording instructed."
            )
            out.append("")
        if turn.thinking:
            out.append("<!-- reasoning -->")
            out.append(_block(turn.thinking))
            out.append("")
        if turn.text:
            out.append(turn.text.rstrip("\n"))
            out.append("")
        answered = {block.tool_use_id: block for block in turn.results}
        for call in turn.calls:
            out.append(f"### tool `{call.name}` — `{call.id}`")
            out.append("")
            out.append(_block(_arguments(dict(call.arguments)), "json"))
            out.append("")
            approval = next((a for a in turn.approvals if a.tool_use_id == call.id), None)
            if approval is not None:
                out.append(
                    f"approval requested ({approval.danger_level.name.lower()}"
                    f"{', ' + approval.reason if approval.reason else ''}):"
                )
                out.append(_block(approval.rendered))
                out.append("")
            result = answered.get(call.id)
            if result is None:
                out.append("**no result recorded — the session ended mid-call.**")
            else:
                label = "error" if result.is_error else "ok"
                out.append(f"**result ({label}):**")
                out.append(_block(result.content))
            out.append("")
        for error in turn.errors:
            out.append(
                f"**error ({error.kind}, "
                f"{'recoverable' if error.recoverable else 'fatal'}):** {error.message}"
            )
            out.append("")
        if turn.end is not None:
            reason = turn.end.stop_reason or "unspecified"
            out.append(f"_turn ended: {turn.end.state.value} ({reason})_")
        else:
            out.append("_turn has no recorded end — the session stopped here._")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


def _pre(content: str, css_class: str = "") -> str:
    attr = f' class="{css_class}"' if css_class else ""
    return f"<pre{attr}>{escape(content, quote=True)}</pre>"


def _details(summary: str, body: str, css_class: str = "tool") -> str:
    """Collapsed by default. ``summary`` is escaped — it is content, not markup."""
    return (
        f'<details class="{css_class}">'
        f"<summary>{escape(summary, quote=True)}</summary>{body}</details>"
    )


def to_html(events: Sequence[Event], meta: SessionMeta) -> str:
    """The session as one self-contained HTML file, tool calls collapsed.

    Everything interpolated is escaped. Nothing is fetched: no script, no link, no
    remote asset, so the file renders the same offline as online.
    """
    turns = fold_turns(events)
    title = f"ronin session {meta.session_id}"
    out: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title, quote=True)}</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{escape(title, quote=True)}</h1>",
        '<table class="meta">',
    ]
    for name, value in _meta_rows(meta, turns, len(events)):
        out.append(
            f"<tr><th>{escape(name, quote=True)}</th>"
            f"<td>{escape(value, quote=True)}</td></tr>"
        )
    out.append("</table>")

    if not turns:
        out.append('<p class="dim">no turns were recorded.</p>')

    for position, turn in enumerate(turns, start=1):
        out.append('<section class="turn">')
        out.append(f"<h2>Turn {position} <span class=\"dim\">(index {turn.index})</span></h2>")
        if turn.resets:
            out.append(
                f'<p class="dim">stream was reset {turn.resets}× — text before the '
                f"last reset was discarded, as the recording instructed.</p>"
            )
        if turn.thinking:
            out.append(_details("reasoning", _pre(turn.thinking), "thinking"))
        if turn.text:
            out.append(f'<div class="prose">{escape(turn.text, quote=True)}</div>')
        answered = {block.tool_use_id: block for block in turn.results}
        for call in turn.calls:
            result = answered.get(call.id)
            if result is None:
                verdict = "no result recorded"
            elif result.is_error:
                verdict = "error"
            else:
                verdict = "ok"
            body = [_pre(_arguments(dict(call.arguments)))]
            approval = next((a for a in turn.approvals if a.tool_use_id == call.id), None)
            if approval is not None:
                body.append(
                    f'<p class="dim">approval requested '
                    f"({escape(approval.danger_level.name.lower(), quote=True)})</p>"
                )
                body.append(_pre(approval.rendered))
            if result is None:
                body.append(
                    '<p class="dim err">the session ended before this tool returned.</p>'
                )
            else:
                body.append(_pre(result.content, "err" if result.is_error else ""))
            out.append(
                _details(f"tool {call.name} · {call.id} · {verdict}", "".join(body))
            )
        for error in turn.errors:
            kind = "recoverable" if error.recoverable else "fatal"
            out.append(
                f'<p class="err">error ({escape(error.kind, quote=True)}, {kind}): '
                f"{escape(error.message, quote=True)}</p>"
            )
        if turn.end is not None:
            reason = turn.end.stop_reason or "unspecified"
            out.append(
                f'<p class="dim">turn ended: '
                f"{escape(turn.end.state.value, quote=True)} "
                f"({escape(reason, quote=True)})</p>"
            )
        else:
            out.append(
                '<p class="dim">turn has no recorded end — the session stopped here.</p>'
            )
        out.append("</section>")
    out.extend(["</body>", "</html>", ""])
    return "\n".join(out)


__all__ = ["to_html", "to_markdown"]
