"""AgentLab — interactive demo for Ronin.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...   # optional; demo mode kicks in if absent
    uv run uvicorn app.main:app --reload --port 8000

Open http://localhost:8000 — pick an agent pattern, send a message, see the trace.

If ANTHROPIC_API_KEY is unset, the API returns a canned trace so the page is still
interactive for anyone clicking through the live demo.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ro_claude_kit_agent_patterns import (
    PlannerExecutorAgent,
    ReActAgent,
    ReflexionAgent,
    SubAgent,
    SupervisorAgent,
    Tool,
)
from ro_claude_kit_hardening import InjectionScanner

app = FastAPI(title="AgentLab")

INJECTION_SCANNER = InjectionScanner()


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _calc_tool() -> Tool:
    """Toy tool the demo agents can use: a safe arithmetic evaluator."""

    def calc(expression: str) -> str:
        allowed = set("0123456789+-*/(). ")
        if not all(c in allowed for c in expression):
            return "ERROR: only digits and + - * / ( ) allowed"
        try:
            return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 — sandboxed
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    return Tool(
        name="calc",
        description="Evaluate a small arithmetic expression.",
        input_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        handler=calc,
    )


def _kb_search_tool() -> Tool:
    KB = {
        "claude": "Claude is a family of LLMs by Anthropic.",
        "react": "ReAct combines reasoning and acting via tool use in a loop.",
        "reflexion": "Reflexion adds a critic that triggers retries with feedback.",
        "ro-claude-kit": "Ronin is an opinionated reference implementation for Claude agents.",
    }

    def search(query: str) -> str:
        q = query.lower()
        hits = [v for k, v in KB.items() if k in q or q in k]
        return "\n".join(hits) if hits else "no results"

    return Tool(
        name="search",
        description="Search the demo knowledge base.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=search,
    )


class RunRequest(BaseModel):
    pattern: str
    system: str
    message: str


class RunResponse(BaseModel):
    success: bool
    output: str
    iterations: int
    trace: list[dict[str, Any]]
    demo_mode: bool = False
    error: str | None = None


def _serialize_trace(trace: list) -> list[dict[str, Any]]:
    return [{"kind": s.kind, "content": str(s.content)[:600]} for s in trace]


def _canned_response(pattern: str, message: str) -> RunResponse:
    """Returned when ANTHROPIC_API_KEY isn't set — keeps the demo browseable."""
    return RunResponse(
        success=True,
        output=f"[demo mode — no API key set]\nThe {pattern} agent would respond to: {message!r}",
        iterations=1,
        trace=[
            {"kind": "thought", "content": f"Pattern: {pattern}. (demo mode — no real call made.)"},
            {"kind": "tool_call", "content": {"name": "search", "input": {"query": message}}},
            {"kind": "tool_result", "content": {"name": "search", "result": "(canned)"}},
            {"kind": "final", "content": "Demo mode response."},
        ],
        demo_mode=True,
    )


@app.post("/api/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    scan = INJECTION_SCANNER.scan(req.message)
    if scan.flagged:
        raise HTTPException(
            status_code=400,
            detail={"error": "input flagged as potential prompt injection", "hits": scan.hits},
        )

    if not _has_api_key():
        return _canned_response(req.pattern, req.message)

    tools = [_kb_search_tool(), _calc_tool()]

    if req.pattern == "react":
        agent = ReActAgent(system=req.system, tools=tools, max_iterations=5)
        result = agent.run(req.message)
    elif req.pattern == "planner-executor":
        agent = PlannerExecutorAgent(
            planner_system="You break tasks into 2-4 concrete steps.",
            executor_system=req.system,
            tools=tools,
        )
        result = agent.run(req.message)
    elif req.pattern == "supervisor":
        researcher = SubAgent(
            name="researcher",
            description="finds facts in the knowledge base",
            system="Search and summarize.",
            tools=[_kb_search_tool()],
        )
        calculator = SubAgent(
            name="calculator",
            description="evaluates arithmetic expressions",
            system="Use the calc tool.",
            tools=[_calc_tool()],
        )
        agent = SupervisorAgent(
            system=req.system,
            sub_agents=[researcher, calculator],
        )
        result = agent.run(req.message)
    elif req.pattern == "reflexion":
        agent = ReflexionAgent(
            agent_system=req.system,
            critic_system="You are a strict reviewer. Reject vague or unsupported answers.",
            tools=tools,
            max_attempts=2,
        )
        result = agent.run(req.message)
    else:
        raise HTTPException(status_code=400, detail=f"unknown pattern: {req.pattern}")

    return RunResponse(
        success=result.success,
        output=result.output,
        iterations=result.iterations,
        trace=_serialize_trace(result.trace),
        error=result.error,
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "demo_mode": not _has_api_key()}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AgentLab — Ronin</title>
<style>
:root { --bg: #fafaf9; --fg: #1a1a1a; --accent: #d4a373; --accent-deep: #a98467; --dim: #888; --card: #fff; --border: #e7e5e4; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); margin: 0; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 2em 1.5em; }
header { display: flex; align-items: baseline; gap: 1em; margin-bottom: 2em; }
h1 { margin: 0; font-size: 1.5em; }
.sub { color: var(--dim); font-size: 0.95em; }
.demo-banner { background: #fef3c7; border: 1px solid #fde68a; padding: 0.6em 1em; border-radius: 6px; margin-bottom: 1em; font-size: 0.9em; }
.layout { display: grid; grid-template-columns: 220px 1fr; gap: 1.5em; }
.patterns { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1em; }
.patterns h3 { margin: 0 0 0.5em; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); }
.pattern-btn { display: block; width: 100%; text-align: left; padding: 0.6em 0.8em; background: transparent; border: 1px solid transparent; border-radius: 6px; cursor: pointer; font-size: 0.95em; margin: 0.2em 0; color: var(--fg); }
.pattern-btn:hover { background: #f5f5f4; }
.pattern-btn.active { background: var(--accent); color: white; }
.main { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5em; }
label { display: block; font-size: 0.85em; color: var(--dim); margin-bottom: 0.3em; }
textarea, input { width: 100%; font-family: ui-monospace, monospace; font-size: 14px; padding: 0.6em 0.8em; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); resize: vertical; }
textarea { min-height: 60px; margin-bottom: 1em; }
button.run { background: var(--accent); color: white; border: none; padding: 0.7em 1.5em; border-radius: 6px; font-size: 1em; cursor: pointer; font-weight: 500; }
button.run:hover { background: var(--accent-deep); }
button.run:disabled { opacity: 0.5; cursor: not-allowed; }
.output { margin-top: 2em; }
.output h3 { font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 1.5em 0 0.5em; }
.answer { background: #fffbeb; border: 1px solid #fde68a; padding: 1em; border-radius: 6px; white-space: pre-wrap; line-height: 1.5; }
.trace { font-family: ui-monospace, monospace; font-size: 13px; }
.step { background: #f5f5f4; padding: 0.5em 0.8em; border-radius: 4px; margin: 0.3em 0; border-left: 3px solid var(--accent); }
.step .kind { color: var(--accent-deep); font-weight: 600; margin-right: 0.5em; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; }
.step .content { white-space: pre-wrap; word-break: break-word; }
.meta { color: var(--dim); font-size: 0.85em; margin-top: 0.5em; }
.error { color: #c0392b; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🐳 AgentLab</h1>
    <span class="sub">interactive demo for Ronin</span>
  </header>
  <div id="banner"></div>

  <div class="layout">
    <aside class="patterns">
      <h3>Pattern</h3>
      <button class="pattern-btn active" data-pattern="react">ReAct</button>
      <button class="pattern-btn" data-pattern="planner-executor">Planner-Executor</button>
      <button class="pattern-btn" data-pattern="supervisor">Supervisor</button>
      <button class="pattern-btn" data-pattern="reflexion">Reflexion</button>
    </aside>

    <section class="main">
      <label>System prompt</label>
      <textarea id="system">You are a helpful research assistant. Use tools (search, calc) when relevant. Be concise.</textarea>

      <label>Your message</label>
      <textarea id="message" placeholder="e.g. What is the ReAct pattern? Or: compute 17 * 23.">What is the ReAct pattern?</textarea>

      <button class="run" id="run">Run agent</button>

      <div class="output" id="output"></div>
    </section>
  </div>
</div>

<script>
let pattern = "react";

document.querySelectorAll(".pattern-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".pattern-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    pattern = btn.dataset.pattern;
  });
});

async function checkHealth() {
  const r = await fetch("/health").then(r => r.json());
  if (r.demo_mode) {
    document.getElementById("banner").innerHTML =
      "<div class='demo-banner'>⚠️ Demo mode — <code>ANTHROPIC_API_KEY</code> isn't set. Responses are canned. " +
      "Set the env var and restart to see real agent runs.</div>";
  }
}
checkHealth();

document.getElementById("run").addEventListener("click", async () => {
  const btn = document.getElementById("run");
  const out = document.getElementById("output");
  btn.disabled = true; btn.textContent = "Running...";
  out.innerHTML = "";

  const body = {
    pattern,
    system: document.getElementById("system").value,
    message: document.getElementById("message").value,
  };

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (!res.ok) {
      out.innerHTML = `<div class='answer error'>${escapeHtml(JSON.stringify(data, null, 2))}</div>`;
    } else {
      const traceHtml = (data.trace || []).map(s => {
        const content = typeof s.content === "string" ? s.content : JSON.stringify(s.content, null, 2);
        return `<div class='step'><span class='kind'>${escapeHtml(s.kind)}</span><span class='content'>${escapeHtml(content)}</span></div>`;
      }).join("");
      out.innerHTML =
        `<h3>Answer</h3>` +
        `<div class='answer'>${escapeHtml(data.output || "(empty)")}</div>` +
        `<div class='meta'>iterations: ${data.iterations} ${data.demo_mode ? "· demo mode" : ""}${data.error ? " · error: " + escapeHtml(data.error) : ""}</div>` +
        `<h3>Trace (${data.trace?.length || 0} steps)</h3>` +
        `<div class='trace'>${traceHtml}</div>`;
    }
  } catch (e) {
    out.innerHTML = `<div class='answer error'>Request failed: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Run agent";
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}
</script>
</body>
</html>
"""
