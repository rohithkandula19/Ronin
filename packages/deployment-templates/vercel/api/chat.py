"""Vercel serverless agent endpoint.

POST /api/chat with body: {"question": "..."}
Returns: {"output": "...", "iterations": N, "trace": [...]}
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

from ronin_agent_patterns import ReActAgent
from ronin_hardening import InjectionScanner


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — Vercel expects this name
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return self._reply(400, {"error": "invalid JSON"})

        question = str(payload.get("question", "")).strip()
        if not question:
            return self._reply(400, {"error": "missing 'question'"})

        scan = InjectionScanner().scan(question)
        if scan.flagged:
            return self._reply(400, {"error": "input flagged", "hits": scan.hits})

        agent = ReActAgent(
            system="You are a helpful research assistant. Be concise.",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            max_iterations=5,
        )
        result = agent.run(question)
        self._reply(200, {
            "success": result.success,
            "output": result.output,
            "iterations": result.iterations,
            "trace": [{"kind": s.kind, "content": str(s.content)[:500]} for s in result.trace],
        })

    def _reply(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))
