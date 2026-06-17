"""Read-only dashboard endpoints + the single-page UI for ``ronin ui``.

These endpoints surface REAL on-disk ronin data: they do not recompute anything
and they never return canned JSON. Each one reads a store the CLI already writes:

- ``GET /api/runs``            recent sessions from ``.ronin/sessions/`` (the
                               conversation archive) plus orchestrated runs from
                               ``.ronin/orchestrations/``.
- ``GET /api/runs/{id}``       one run in full: the session transcript, and, when
                               the id is an orchestrated run, the subagent tree
                               (plan + every subtask result).
- ``GET /api/faithfulness``    recent grounding scores from ``.ronin/faithfulness.jsonl``.
- ``GET /api/memory``          durable user facts from ``~/.ronin/memory.json``.
- ``GET /api/skills``          learned procedures from ``~/.ronin/skills/``.
- ``GET /`` (the UI route)     a self-contained HTML page (vanilla JS, inline CSS,
                               no external URLs) that renders all of the above.

The project-local stores (sessions, faithfulness, orchestrations) are read from
the directory in ``RONIN_HOME`` if set, else the current working directory, the
same root the CLI writes to. The user-global stores (memory, skills) come from
``ronin_cli`` directly, which resolves ``~/.ronin`` (``$HOME``). Everything is
reused from ``ronin_cli`` so the dashboard reflects exactly what the agent stored.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse


def _project_root() -> Path:
    """The directory whose project-local ``.ronin/`` we surface. ``RONIN_HOME``
    overrides (so the host / tests can point the dashboard at a specific repo);
    otherwise the current working directory, which is where the CLI writes."""
    home = os.environ.get("RONIN_HOME")
    return Path(home) if home else Path.cwd()


def build_dashboard_router() -> APIRouter:
    """An APIRouter with the read-only dashboard endpoints + the UI page.

    Kept as a router so it mounts onto the existing gateway in ``make_app`` and so
    ``ronin ui`` can serve the same routes on a standalone app."""
    router = APIRouter()

    # ---- recent runs (sessions + orchestrations) -----------------------

    @router.get("/api/runs")
    def runs(limit: int = 50) -> dict:
        from ronin_cli.run_store import list_orchestrations
        from ronin_cli.sessions import list_sessions

        root = _project_root()
        limit = max(1, min(500, limit))
        sessions = [
            {
                "id": s.get("id", ""),
                "kind": "session",
                "title": s.get("title", ""),
                "turns": s.get("turns", 0),
                "updated": s.get("updated", ""),
                "root": s.get("root", ""),
            }
            for s in list_sessions(base=root)[:limit]
        ]
        orchestrations = [
            {
                "id": o.get("id", ""),
                "kind": "orchestration",
                "title": o.get("goal", ""),
                "subtasks": o.get("subtasks", 0),
                "success": o.get("success", False),
                "updated": o.get("ts", ""),
            }
            for o in list_orchestrations(root)[:limit]
        ]
        return {
            "sessions": sessions,
            "orchestrations": orchestrations,
            "count": len(sessions) + len(orchestrations),
        }

    @router.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        from ronin_cli.run_store import load_orchestration
        from ronin_cli.sessions import list_sessions, load_session

        root = _project_root()

        # An orchestrated run carries the subagent tree (plan + subtask results).
        orch = load_orchestration(run_id, root)
        if orch is not None:
            return {
                "id": orch.get("id", run_id),
                "kind": "orchestration",
                "goal": orch.get("goal", ""),
                "success": orch.get("success", False),
                "updated": orch.get("ts", ""),
                "output": orch.get("output", ""),
                "error": orch.get("error"),
                "tree": {
                    "plan": orch.get("plan", []),
                    "subtasks": orch.get("subtasks", []),
                },
            }

        # Otherwise it is a conversation session: return its transcript + metadata.
        transcript = load_session(run_id, base=root)
        if transcript:
            meta = next(
                (s for s in list_sessions(base=root) if str(s.get("id")) == str(run_id)), {}
            )
            return {
                "id": run_id,
                "kind": "session",
                "title": meta.get("title", ""),
                "turns": meta.get("turns", 0),
                "updated": meta.get("updated", ""),
                "root": meta.get("root", ""),
                "transcript": transcript,
            }

        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")

    # ---- faithfulness scores -------------------------------------------

    @router.get("/api/faithfulness")
    def faithfulness(limit: int = 50) -> dict:
        from ronin_cli.run_store import faithfulness_summary, load_faithfulness

        root = _project_root()
        records = load_faithfulness(root, limit=max(1, min(500, limit)))
        return {"summary": faithfulness_summary(root), "scores": records}

    # ---- memory ---------------------------------------------------------

    @router.get("/api/memory")
    def memory() -> dict:
        from ronin_cli.memory_store import load_memories

        # Stored oldest-first; show newest-first for the dashboard.
        items = list(reversed(load_memories()))
        return {"count": len(items), "memories": items}

    # ---- skills ---------------------------------------------------------

    @router.get("/api/skills")
    def skills() -> dict:
        from ronin_cli.skills_store import list_skills

        items = [
            {
                "name": s.name,
                "description": s.description,
                "params": list(s.params),
                "steps": s.steps,
                "source": s.source,
                "created": s.created,
            }
            for s in list_skills()
        ]
        return {"count": len(items), "skills": items}

    # ---- the single-page UI --------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    def ui() -> str:
        return DASHBOARD_HTML

    return router


def mount_dashboard(app: FastAPI) -> FastAPI:
    """Attach the dashboard router to an existing FastAPI app and return it."""
    app.include_router(build_dashboard_router())
    return app


# The dashboard is one self-contained HTML page: vanilla JS, inline CSS, NO
# external resource URLs, so it works fully offline. It calls the /api/* routes
# above and renders recent runs, an expandable subagent tree, faithfulness
# badges, memory, and skills. Read-only.
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ronin dashboard</title>
<style>
  :root {
    --bg: #16161e; --panel: #1a1b26; --panel2: #1f2030; --fg: #c0caf5;
    --mute: #565f89; --accent: #7aa2f7; --green: #9ece6a; --red: #f7768e;
    --amber: #e0af68; --purple: #bb9af7; --border: #2a2b3d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  header {
    padding: 18px 24px; border-bottom: 1px solid var(--border);
    display: flex; align-items: baseline; gap: 14px;
  }
  header h1 { margin: 0; font-size: 20px; letter-spacing: 1px; color: var(--accent); }
  header .sub { color: var(--mute); font-size: 12px; }
  main { display: grid; grid-template-columns: 1.2fr 1fr; gap: 18px; padding: 18px 24px; }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  section {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px 16px; margin-bottom: 0;
  }
  section h2 {
    margin: 0 0 10px; font-size: 13px; text-transform: uppercase;
    letter-spacing: 1px; color: var(--purple);
  }
  .empty { color: var(--mute); font-style: italic; padding: 6px 0; }
  .row {
    border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px;
    margin-bottom: 8px; background: var(--panel2);
  }
  .row .title { color: var(--fg); }
  .row .meta { color: var(--mute); font-size: 12px; margin-top: 2px; }
  .row.click { cursor: pointer; }
  .row.click:hover { border-color: var(--accent); }
  .pill {
    display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-size: 11px; margin-right: 6px; border: 1px solid var(--border);
  }
  .pill.session { color: var(--accent); }
  .pill.orchestration { color: var(--purple); }
  .badge {
    display: inline-block; padding: 1px 8px; border-radius: 4px;
    font-size: 11px; font-weight: bold;
  }
  .badge.grounded { background: rgba(158,206,106,.18); color: var(--green); }
  .badge.ungrounded { background: rgba(247,118,142,.18); color: var(--red); }
  .badge.abstain { background: rgba(224,175,104,.18); color: var(--amber); }
  .tree { margin-top: 8px; padding-left: 4px; }
  .node { border-left: 2px solid var(--border); padding: 4px 0 4px 12px; margin: 4px 0; }
  .node .ok { color: var(--green); }
  .node .fail { color: var(--red); }
  .node .dep { color: var(--mute); font-size: 11px; }
  .node pre {
    background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
    padding: 6px 8px; margin: 4px 0 0; white-space: pre-wrap; word-break: break-word;
    color: var(--fg); font-size: 12px; max-height: 220px; overflow: auto;
  }
  .detail { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 10px; }
  .kv { color: var(--mute); font-size: 12px; }
  .stat { display: inline-block; margin-right: 16px; }
  .stat b { color: var(--fg); }
  code.skill-steps { display: block; white-space: pre-wrap; color: var(--mute); font-size: 12px; margin-top: 4px; }
  button.refresh {
    background: var(--panel2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 4px; padding: 4px 10px; cursor: pointer; font: inherit; font-size: 12px;
  }
  button.refresh:hover { border-color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>ronin</h1>
  <span class="sub">read-only mission-control dashboard</span>
  <span style="flex:1"></span>
  <button class="refresh" onclick="loadAll()">refresh</button>
</header>
<main>
  <section style="grid-row: span 2;">
    <h2>recent runs</h2>
    <div id="runs"><div class="empty">loading...</div></div>
    <div id="run-detail"></div>
  </section>
  <section>
    <h2>faithfulness</h2>
    <div id="faith-summary"></div>
    <div id="faith"><div class="empty">loading...</div></div>
  </section>
  <section>
    <h2>memory</h2>
    <div id="memory"><div class="empty">loading...</div></div>
  </section>
  <section>
    <h2>skills</h2>
    <div id="skills"><div class="empty">loading...</div></div>
  </section>
</main>
<script>
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
function emptyMsg(text) { return '<div class="empty">' + esc(text) + '</div>'; }

async function loadRuns() {
  const el = document.getElementById("runs");
  try {
    const d = await getJSON("/api/runs");
    const all = []
      .concat((d.orchestrations || []).map(function(o){ o.kind = "orchestration"; return o; }))
      .concat((d.sessions || []).map(function(s){ s.kind = "session"; return s; }));
    if (!all.length) { el.innerHTML = emptyMsg("no runs recorded yet"); return; }
    el.innerHTML = all.map(function(r) {
      var meta = r.kind === "orchestration"
        ? (r.subtasks + " subtask(s) - " + (r.success ? "ok" : "failed"))
        : (r.turns + " turn(s)");
      return '<div class="row click" onclick="loadDetail(\'' + esc(r.id) + '\')">'
        + '<span class="pill ' + r.kind + '">' + r.kind + '</span>'
        + '<span class="title">' + esc(r.title || "(untitled)") + '</span>'
        + '<div class="meta">' + esc(r.id) + ' - ' + esc(meta) + ' - ' + esc(r.updated) + '</div>'
        + '</div>';
    }).join("");
  } catch (e) { el.innerHTML = emptyMsg("could not load runs: " + e.message); }
}

async function loadDetail(id) {
  const el = document.getElementById("run-detail");
  el.innerHTML = '<div class="detail"><div class="empty">loading ' + esc(id) + '...</div></div>';
  try {
    const d = await getJSON("/api/runs/" + encodeURIComponent(id));
    if (d.kind === "orchestration") {
      el.innerHTML = renderTree(d);
    } else {
      var t = (d.transcript || []).map(function(line){ return esc(line); }).join("\n");
      el.innerHTML = '<div class="detail"><div class="kv">session ' + esc(d.id)
        + ' - ' + esc(d.turns) + ' turn(s)</div>'
        + '<pre>' + (t || "(empty transcript)") + '</pre></div>';
    }
  } catch (e) {
    el.innerHTML = '<div class="detail">' + emptyMsg("could not load run: " + e.message) + '</div>';
  }
}

function renderTree(d) {
  var tree = d.tree || {};
  var plan = tree.plan || [];
  var byId = {};
  (tree.subtasks || []).forEach(function(s){ byId[s.subtask_id] = s; });
  var nodes = plan.map(function(p) {
    var res = byId[p.id] || {};
    var ok = res.success ? '<span class="ok">ok</span>' : '<span class="fail">failed</span>';
    var dep = (p.depends_on && p.depends_on.length)
      ? '<span class="dep"> depends on: ' + esc(p.depends_on.join(", ")) + '</span>' : "";
    var out = res.output || res.error || "";
    return '<div class="node"><b>' + esc(p.id) + '</b> -> ' + esc(p.assignee) + ' ' + ok + dep
      + '<div class="kv">' + esc(p.description) + '</div>'
      + (out ? '<pre>' + esc(out) + '</pre>' : "") + '</div>';
  }).join("");
  if (!plan.length) nodes = emptyMsg("no subtasks in this run");
  return '<div class="detail"><div class="kv">orchestration ' + esc(d.id)
    + ' - ' + (d.success ? "ok" : "failed") + '</div>'
    + '<div class="kv" style="margin-top:4px">goal: ' + esc(d.goal) + '</div>'
    + '<div class="tree">' + nodes + '</div>'
    + (d.output ? '<pre>' + esc(d.output) + '</pre>' : "") + '</div>';
}

async function loadFaithfulness() {
  const sumEl = document.getElementById("faith-summary");
  const el = document.getElementById("faith");
  try {
    const d = await getJSON("/api/faithfulness");
    const s = d.summary || {};
    sumEl.innerHTML = '<div class="kv" style="margin-bottom:8px">'
      + '<span class="stat">total <b>' + esc(s.total || 0) + '</b></span>'
      + '<span class="stat">grounded <b>' + esc(s.grounded || 0) + '</b></span>'
      + '<span class="stat">ungrounded <b>' + esc(s.ungrounded || 0) + '</b></span>'
      + '<span class="stat">mean <b>' + esc(s.mean_score == null ? "-" : s.mean_score) + '</b></span>'
      + '</div>';
    const rows = d.scores || [];
    if (!rows.length) { el.innerHTML = emptyMsg("no faithfulness checks recorded yet"); return; }
    el.innerHTML = rows.map(function(r) {
      var v = r.verdict || (r.abstain ? "abstain" : "grounded");
      return '<div class="row">'
        + '<span class="badge ' + esc(v) + '">' + esc(v) + ' ' + esc(r.score) + '</span> '
        + '<span class="title">' + esc((r.answer || "").slice(0, 80)) + '</span>'
        + '<div class="meta">' + esc(r.n_sources || 0) + ' source(s) - ' + esc(r.ts || "") + '</div>'
        + '</div>';
    }).join("");
  } catch (e) { el.innerHTML = emptyMsg("could not load faithfulness: " + e.message); }
}

async function loadMemory() {
  const el = document.getElementById("memory");
  try {
    const d = await getJSON("/api/memory");
    const items = d.memories || [];
    if (!items.length) { el.innerHTML = emptyMsg("no memories stored yet"); return; }
    el.innerHTML = items.map(function(m) {
      return '<div class="row"><span class="title">' + esc(m.text) + '</span></div>';
    }).join("");
  } catch (e) { el.innerHTML = emptyMsg("could not load memory: " + e.message); }
}

async function loadSkills() {
  const el = document.getElementById("skills");
  try {
    const d = await getJSON("/api/skills");
    const items = d.skills || [];
    if (!items.length) { el.innerHTML = emptyMsg("no skills learned yet"); return; }
    el.innerHTML = items.map(function(s) {
      var params = (s.params && s.params.length) ? ' (params: ' + esc(s.params.join(", ")) + ')' : "";
      return '<div class="row"><span class="title">' + esc(s.name) + '</span>' + params
        + (s.description ? '<div class="meta">' + esc(s.description) + '</div>' : "")
        + '<code class="skill-steps">' + esc(s.steps) + '</code></div>';
    }).join("");
  } catch (e) { el.innerHTML = emptyMsg("could not load skills: " + e.message); }
}

function loadAll() {
  loadRuns(); loadFaithfulness(); loadMemory(); loadSkills();
  document.getElementById("run-detail").innerHTML = "";
}
loadAll();
</script>
</body>
</html>
"""
