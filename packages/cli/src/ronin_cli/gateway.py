"""Messaging gateway — talk to ronin over HTTP from anywhere.

`ronin gateway` starts a small FastAPI server with one generic webhook endpoint:

    POST /message   {"text": "...", "session_id": "..."}  ->  {"reply": "...", ...}

It receives a message, runs the configured agent on it, and returns the reply.
This is a generic HTTP endpoint: it needs ZERO external accounts. A plain `curl`
can drive it, and so can any chat platform that can POST JSON (a thin adapter is
all that's needed). An OPTIONAL Telegram adapter lives in `gateway_telegram.py`
and is gated behind an env flag; the generic webhook never requires a bot token.

Auth is a single bearer token: pass `--token` (or set RONIN_GATEWAY_TOKEN) and
the endpoint requires `Authorization: Bearer <token>`. With no token configured
the endpoint is open (handy for localhost / offline demos).

Sessions are real: each `session_id` keeps its own short-term conversation
history in memory, so a follow-up message sees the prior turns. History is
process-local (no database needed) and reuses ronin's ShortTermMemory.
"""
from __future__ import annotations

from threading import Lock
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import RoninConfig
from .runner import run_ask


# Cap on stored sessions so a long-running gateway can't grow without bound.
_MAX_SESSIONS = 1000
# How many recent turns of a session to feed back into the next prompt.
_KEEP_RECENT = 8


class MessageRequest(BaseModel):
    text: str = Field(..., description="The user's message to the agent.")
    session_id: str = Field("default", description="Conversation id; isolates history per chat.")


class MessageResponse(BaseModel):
    reply: str
    session_id: str
    success: bool
    iterations: int
    usage: dict[str, int] = Field(default_factory=dict)
    demo_mode: bool = False
    error: str | None = None


class _SessionStore:
    """Process-local per-session conversation history. Real, not theater: a
    follow-up message in the same ``session_id`` sees the earlier turns.

    Thread-safe (uvicorn may serve requests on a threadpool). Bounded so a
    long-lived gateway can't leak memory; oldest sessions are evicted first.
    """

    def __init__(self, keep_recent: int = _KEEP_RECENT, max_sessions: int = _MAX_SESSIONS) -> None:
        from ronin_memory import ShortTermMemory

        self._keep_recent = keep_recent
        self._max_sessions = max_sessions
        self._lock = Lock()
        # insertion-ordered dict → cheap LRU-ish eviction of the oldest key.
        self._sessions: dict[str, ShortTermMemory] = {}
        self._ShortTermMemory = ShortTermMemory

    def _get(self, session_id: str):
        mem = self._sessions.get(session_id)
        if mem is None:
            if len(self._sessions) >= self._max_sessions:
                # evict the oldest inserted session
                oldest = next(iter(self._sessions))
                self._sessions.pop(oldest, None)
            mem = self._ShortTermMemory(keep_recent=self._keep_recent)
            self._sessions[session_id] = mem
        return mem

    def build_prompt(self, session_id: str, text: str) -> str:
        """Record the user turn and return a prompt that includes prior context."""
        with self._lock:
            mem = self._get(session_id)
            prior = "\n\n".join(
                f"{t.role.upper()}: {t.content}" for t in mem.turns
            ) or "(start of conversation)"
            mem.add_turn("user", text)
        return f"Conversation so far:\n{prior}\n\nUser's new message: {text}"

    def record_reply(self, session_id: str, reply: str) -> None:
        with self._lock:
            mem = self._get(session_id)
            mem.add_turn("assistant", reply)
            mem.maybe_compress()

    def turns(self, session_id: str) -> int:
        with self._lock:
            mem = self._sessions.get(session_id)
            return len(mem.turns) if mem else 0


def make_gateway_app(config: RoninConfig, *, token: str | None = None) -> FastAPI:
    """Build the gateway FastAPI app.

    ``token`` (optional): when set, every /message call must carry
    ``Authorization: Bearer <token>``. When ``None`` the endpoint is open.
    """
    app = FastAPI(
        title="ronin gateway",
        description="Talk to ronin from anywhere over a generic HTTP webhook.",
    )
    store = _SessionStore()
    # Stash for the optional Telegram adapter / introspection.
    app.state.config = config
    app.state.token = token
    app.state.session_store = store

    def _require_auth(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return  # open mode (localhost / offline demos)
        expected = f"Bearer {token}"
        # length check first keeps the compare constant-time-ish on the secret
        if not authorization or authorization != expected:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "provider": config.provider,
            "model": config.resolved_model(),
            "demo_mode": config.demo_mode,
            "auth": bool(token),
            "services": config.configured_services(),
        }

    @app.post("/message", response_model=MessageResponse, dependencies=[Depends(_require_auth)])
    def message(req: MessageRequest) -> MessageResponse:
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        session_id = req.session_id or "default"

        prompt = store.build_prompt(session_id, text)
        result = run_ask(config, prompt, console=None)
        store.record_reply(session_id, result.output)

        return MessageResponse(
            reply=result.output,
            session_id=session_id,
            success=result.success,
            iterations=result.iterations,
            usage=result.usage,
            demo_mode=result.demo_mode,
            error=result.error,
        )

    return app
