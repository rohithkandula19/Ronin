"""Modal serverless deployment for an Ronin agent.

Why Modal: Python-native serverless. No Docker config, no Kubernetes. Good for
compute-heavy agents (long-running tool use, batched evals) that don't fit
Vercel's request-time limits.

Setup:
    pip install modal
    modal token new
    modal secret create anthropic ANTHROPIC_API_KEY=sk-ant-...
    modal deploy packages/deployment-templates/modal/app.py

After deploy, hit the agent endpoint:
    curl https://<your-org>--ro-claude-agent-run-agent.modal.run \\
      -d '{"question": "Summarize the ReAct pattern."}'
"""
from __future__ import annotations

import modal

app = modal.App("ro-claude-agent")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "ro-claude-kit-agent-patterns>=0.0.1",
        "ro-claude-kit-hardening>=0.0.1",
        "fastapi>=0.110",
    )
)

ANTHROPIC_SECRET = modal.Secret.from_name("anthropic")


@app.function(image=image, secrets=[ANTHROPIC_SECRET], timeout=300)
@modal.web_endpoint(method="POST")
def run_agent(payload: dict) -> dict:
    """POST endpoint that runs a ReAct agent over the question and returns the trace."""
    from ro_claude_kit_agent_patterns import ReActAgent
    from ro_claude_kit_hardening import InjectionScanner

    question = str(payload.get("question", "")).strip()
    if not question:
        return {"error": "missing 'question' in body"}

    scan = InjectionScanner().scan(question)
    if scan.flagged:
        return {"error": "input flagged as potential prompt injection", "hits": scan.hits}

    agent = ReActAgent(
        system="You are a helpful research assistant. Be concise and cite sources.",
        max_iterations=5,
    )
    result = agent.run(question)
    return {
        "success": result.success,
        "output": result.output,
        "iterations": result.iterations,
        "usage": result.usage,
        "trace": [{"kind": s.kind, "content": str(s.content)[:500]} for s in result.trace],
    }


@app.local_entrypoint()
def main(question: str = "What is the ReAct pattern?") -> None:
    print(run_agent.remote({"question": question}))
