"""A small, local Agent Client Protocol (ACP) bridge for Ronin.

The bridge exposes the existing code agent to editor clients over JSON-RPC on
stdin/stdout.  It deliberately stays local and read-only: clients may select a
workspace beneath the trusted root, but cannot supply MCP servers, elevate tool
permissions, or bypass Ronin's context and policy pipeline.

Keeping the protocol adapter thin is intentional.  Planning, context assembly,
memory, provider routing, and tool safety remain owned by ``run_code_agent`` so
the terminal, gateway, and editor surfaces do not drift into separate agents.
"""
from __future__ import annotations

import datetime as _datetime
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = 1
MAX_PROMPT_CHARS = 100_000
_SESSION_ID = re.compile(r"^ronin-[0-9a-f-]{36}$")
_MODES = frozenset({"read_only", "proposal"})


@dataclass
class AcpSession:
    """The local state needed to preserve an editor conversation."""

    session_id: str
    cwd: Path
    history: list = field(default_factory=list)
    mode: str = "read_only"
    turns: int = 0
    cancelled: bool = False
    created: str = field(default_factory=lambda: _now())
    updated: str = field(default_factory=lambda: _now())


AgentRunner = Callable[[str, Path, list, str, Callable[[str], None]], Any]


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _inside(path: Path, root: Path) -> bool:
    """Return whether ``path`` is root or a descendant without string tricks."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_messages(messages: list) -> list[dict[str, Any]]:
    """Serialize structured provider messages without making persistence brittle."""
    safe: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            safe.append(message)
        elif hasattr(message, "model_dump"):
            try:
                value = message.model_dump()
            except Exception:  # noqa: BLE001 - session persistence is best effort.
                continue
            if isinstance(value, dict):
                safe.append(value)
    return safe


class AcpSessionStore:
    """Project-local, atomic ACP session persistence.

    Sessions are deliberately stored under the trusted root instead of the
    user's global home, so a client cannot resume a conversation in an unrelated
    repository simply by guessing an identifier.
    """

    def __init__(self, root: Path) -> None:
        self.directory = root / ".ronin" / "acp-sessions"

    def save(self, session: AcpSession) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{session.session_id}.json"
        payload = {
            "version": 1,
            "session_id": session.session_id,
            "cwd": str(session.cwd),
            "history": _safe_messages(session.history),
            "mode": session.mode,
            "turns": session.turns,
            "cancelled": session.cancelled,
            "created": session.created,
            "updated": session.updated,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load(self, session_id: str) -> AcpSession | None:
        if not _SESSION_ID.fullmatch(session_id):
            return None
        path = self.directory / f"{session_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        cwd = payload.get("cwd")
        mode = payload.get("mode", "read_only")
        if not isinstance(cwd, str) or mode not in _MODES:
            return None
        history = payload.get("history")
        if not isinstance(history, list):
            history = []
        try:
            from ronin_agent_patterns import Message
            restored = [Message(**item) for item in history if isinstance(item, dict)]
        except Exception:  # noqa: BLE001 - legacy/session drift starts with a safe blank history.
            restored = []
        return AcpSession(
            session_id=session_id,
            cwd=Path(cwd).resolve(),
            history=restored,
            mode=mode,
            turns=int(payload.get("turns", 0) or 0),
            cancelled=bool(payload.get("cancelled", False)),
            created=str(payload.get("created", "") or _now()),
            updated=str(payload.get("updated", "") or _now()),
        )


class AcpServer:
    """Stateful ACP v1 handler independent of stdio for straightforward tests."""

    def __init__(self, *, root: Path | str, runner: AgentRunner) -> None:
        self.root = Path(root).resolve()
        self.runner = runner
        self.initialized = False
        self.sessions: dict[str, AcpSession] = {}
        self.store = AcpSessionStore(self.root)

    @staticmethod
    def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _update(session_id: str, text: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
            },
        }

    @staticmethod
    def _activity(session: AcpSession, state: str, **details: Any) -> dict[str, Any]:
        """Ronin extension notification for progress, budget, and proposal evidence."""
        return {
            "jsonrpc": JSONRPC_VERSION,
            "method": "ronin/activity",
            "params": {
                "sessionId": session.session_id,
                "state": state,
                "mode": session.mode,
                "turns": session.turns,
                **details,
            },
        }

    def _persist(self, session: AcpSession) -> None:
        session.updated = _now()
        try:
            self.store.save(session)
        except OSError:
            # An editor transport must remain available when its local session
            # directory is temporarily unwritable; its in-memory session survives.
            pass

    def _new_session(self, request_id: Any, params: Any) -> list[dict[str, Any]]:
        if not isinstance(params, dict):
            return [self._error(request_id, -32602, "session/new params must be an object")]
        if params.get("mcpServers"):
            return [self._error(
                request_id,
                -32602,
                "ACP-provided MCP servers are not accepted; configure trusted tools in Ronin instead.",
            )]
        raw_cwd = params.get("cwd")
        if not isinstance(raw_cwd, str) or not raw_cwd.strip():
            return [self._error(request_id, -32602, "session/new requires a workspace cwd")]
        cwd = Path(raw_cwd).expanduser().resolve()
        if not cwd.is_dir():
            return [self._error(request_id, -32602, "workspace cwd must be an existing directory")]
        if not _inside(cwd, self.root):
            return [self._error(
                request_id,
                -32602,
                f"workspace cwd must be inside the configured trusted root: {self.root}",
            )]
        mode = params.get("roninMode", "read_only")
        if mode not in _MODES:
            return [self._error(request_id, -32602, "roninMode must be read_only or proposal")]
        session_id = f"ronin-{uuid.uuid4()}"
        session = AcpSession(session_id=session_id, cwd=cwd, mode=mode)
        self.sessions[session_id] = session
        self._persist(session)
        return [self._response(request_id, {"sessionId": session_id})]

    def _load_session(self, request_id: Any, params: Any) -> list[dict[str, Any]]:
        if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str):
            return [self._error(request_id, -32602, "session/load requires a sessionId")]
        session = self.store.load(params["sessionId"])
        if session is None or not session.cwd.is_dir() or not _inside(session.cwd, self.root):
            return [self._error(request_id, -32602, "saved session is unavailable in this trusted root")]
        self.sessions[session.session_id] = session
        return [self._response(request_id, {"sessionId": session.session_id})]

    def _prompt(self, request_id: Any, params: Any) -> list[dict[str, Any]]:
        if not isinstance(params, dict):
            return [self._error(request_id, -32602, "session/prompt params must be an object")]
        session_id = params.get("sessionId")
        session = self.sessions.get(session_id) if isinstance(session_id, str) else None
        if session is None:
            return [self._error(request_id, -32602, "unknown sessionId")]
        if session.cancelled:
            session.cancelled = False
            self._persist(session)
            return [self._response(request_id, {"stopReason": "cancelled"})]
        blocks = params.get("prompt")
        if not isinstance(blocks, list) or not blocks:
            return [self._error(request_id, -32602, "session/prompt requires non-empty text content")]
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                return [self._error(
                    request_id,
                    -32602,
                    "Ronin's local ACP bridge currently accepts text prompt blocks only.",
                )]
            text = block.get("text")
            if not isinstance(text, str):
                return [self._error(request_id, -32602, "text prompt blocks require a string text field")]
            parts.append(text)
        prompt = "\n".join(parts).strip()
        if not prompt:
            return [self._error(request_id, -32602, "prompt text must not be empty")]
        if len(prompt) > MAX_PROMPT_CHARS:
            return [self._error(request_id, -32602, f"prompt exceeds {MAX_PROMPT_CHARS} characters")]

        streamed: list[str] = []

        def emit(text: str) -> None:
            if isinstance(text, str) and text:
                streamed.append(text)

        activity = [self._activity(session, "running")]
        try:
            result = self.runner(prompt, session.cwd, session.history, session.mode, emit)
        except Exception as exc:  # The transport must remain alive after one bad turn.
            return [self._error(request_id, -32000, f"Ronin agent turn failed: {exc}")]

        output = str(getattr(result, "output", "") or "")
        messages = getattr(result, "messages", None)
        if isinstance(messages, list) and messages:
            session.history = messages
        session.turns += 1
        self._persist(session)
        updates = activity + [self._update(session.session_id, text) for text in streamed]
        if output and not streamed:
            updates.append(self._update(session.session_id, output))
        usage = getattr(result, "usage", {}) or {}
        proposal = getattr(result, "proposal", None)
        updates.append(self._activity(
            session,
            "completed" if bool(getattr(result, "success", True)) else "failed",
            usage=usage if isinstance(usage, dict) else {},
            proposal=proposal if isinstance(proposal, dict) else None,
            run_id=getattr(result, "run_id", None),
        ))
        self._record_run(session, prompt, result)
        updates.append(self._response(request_id, {"stopReason": "end_turn"}))
        return updates

    def _record_run(self, session: AcpSession, prompt: str, result: Any) -> None:
        """Publish a compact local record for the existing dashboard/run archive."""
        try:
            from .run_store import save_run
            save_run({
                "kind": "acp",
                "goal": prompt[:500],
                "success": bool(getattr(result, "success", False)),
                "planner": "ronin-acp",
                "output": str(getattr(result, "output", "") or ""),
                "session_id": session.session_id,
                "mode": session.mode,
                "usage": getattr(result, "usage", {}) or {},
                "agent_task_run_id": getattr(result, "run_id", None),
                "proposal": getattr(result, "proposal", None),
            })
        except Exception:  # noqa: BLE001 - dashboard evidence never breaks an editor turn.
            pass

    def handle(self, request: Any) -> list[dict[str, Any]]:
        """Handle one JSON-RPC request and return any notifications plus reply."""
        if not isinstance(request, dict) or request.get("jsonrpc") != JSONRPC_VERSION:
            request_id = request.get("id") if isinstance(request, dict) else None
            return [self._error(request_id, -32600, "expected a JSON-RPC 2.0 request")]
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return [self._error(request_id, -32600, "request method must be a string")]
        params = request.get("params", {})

        if method == "initialize":
            if not isinstance(params, dict) or params.get("protocolVersion") != PROTOCOL_VERSION:
                return [self._error(request_id, -32602, "Ronin supports ACP protocol version 1")]
            self.initialized = True
            return [self._response(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {
                        "image": False,
                        "audio": False,
                        "embeddedContext": False,
                    },
                },
                "agentInfo": {
                    "name": "ronin",
                    "title": "Ronin",
                    "version": __version__,
                },
            })]
        if not self.initialized:
            return [self._error(request_id, -32002, "initialize must complete before creating a session")]
        if method == "session/new":
            return self._new_session(request_id, params)
        if method == "session/load":
            return self._load_session(request_id, params)
        if method == "session/prompt":
            return self._prompt(request_id, params)
        if method == "session/cancel":
            if isinstance(params, dict) and isinstance(params.get("sessionId"), str):
                session = self.sessions.get(params["sessionId"])
                if session is not None:
                    # The stdio adapter serializes turns. A cancellation received
                    # between turns is therefore durable and prevents the next
                    # requested model call; active provider calls remain bounded
                    # by their normal iteration/time budgets.
                    session.cancelled = True
                    self._persist(session)
            return [self._response(request_id, {})]
        return [self._error(request_id, -32601, f"unsupported ACP method: {method}")]


def build_runner(*, max_iterations: int) -> AgentRunner:
    """Build the only execution path the ACP adapter is allowed to use."""
    from .code_mode import run_code_agent
    from .config import load_config

    config = load_config()

    def run(prompt: str, cwd: Path, history: list, mode: str, emit: Callable[[str], None]) -> Any:
        if mode == "proposal":
            # Writes are never directed into the editor workspace. The existing
            # orchestrator creates detached worktrees and retains an explicit,
            # reviewable proposal before anything can be staged.
            from .agent_workflow import workflow_for_goal
            from .orchestrate import run_orchestrate

            emit("Planning an isolated implementation proposal.\n")
            return run_orchestrate(
                config, prompt, root=cwd, read_only=False,
                max_iterations=max_iterations, max_agents=4,
                workflow=workflow_for_goal(prompt, write=True),
            )
        return run_code_agent(
            config,
            prompt,
            root=cwd,
            console=None,
            max_iterations=max_iterations,
            message_history=history,
            read_only=True,
            include_image_tool=False,
            role="reviewer",
            on_text_cb=emit,
        )

    return run


def serve_stdio(*, root: Path | str = ".", max_iterations: int = 25,
                input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
    """Run the local ACP server over line-delimited JSON-RPC on stdio."""
    reader = input_stream or sys.stdin
    writer = output_stream or sys.stdout
    server = AcpServer(root=root, runner=build_runner(max_iterations=max_iterations))
    for line in reader:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            responses = [server._error(None, -32700, f"invalid JSON: {exc.msg}")]
        else:
            responses = server.handle(request)
        for response in responses:
            writer.write(json.dumps(response, separators=(",", ":")) + "\n")
        writer.flush()
