"""HTTP server mode — expose the configured agent as a REST API.

Run:
    csk serve --port 8000

POST /ask with ``{"question": "..."}``, get ``{"output": "...", "trace": [...]}``.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import RoninConfig
from .runner import run_ask


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    success: bool
    output: str
    iterations: int
    trace: list[dict[str, Any]]
    usage: dict[str, int]
    error: str | None = None
    demo_mode: bool = False


def make_app(config: RoninConfig) -> FastAPI:
    app = FastAPI(
        title="csk",
        description="Ask Claude questions about your Stripe / Linear / Slack / Notion / Postgres data.",
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "provider": config.provider,
            "model": config.resolved_model(),
            "demo_mode": config.demo_mode,
            "services": config.configured_services(),
        }

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest) -> AskResponse:
        if not req.question.strip():
            raise HTTPException(status_code=400, detail="question is required")
        result = run_ask(config, req.question, console=None)
        return AskResponse(
            success=result.success,
            output=result.output,
            iterations=result.iterations,
            trace=result.trace,
            usage=result.usage,
            error=result.error,
            demo_mode=result.demo_mode,
        )

    return app
