"""Offline tests for the Telegram bridge (`ronin telegram`).

Every Telegram HTTP call is mocked via an injected fake client; no test touches
the network. The agent is a stub so no LLM/provider is needed.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pathlib import Path

from ronin_cli.main import app
from ronin_cli.telegram_bot import (
    ENV_ROOT,
    MAX_MESSAGE_CHARS,
    REDACTED,
    TelegramBot,
    TelegramConfigError,
    _chunk_text,
    get_bot_token,
    is_secret_path,
    parse_allowed_chat_ids,
    redact_token,
    resolve_root,
)

runner = CliRunner()

VALID_TOKEN = "123456789:AAExampleSecretTokenValue"


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Records POSTs and returns a queued/templated response per method."""

    def __init__(self, getupdates_result: list | None = None) -> None:
        self.calls: list[dict] = []
        self._getupdates_result = getupdates_result or []
        self._getupdates_served = False

    def post(self, url: str, json: dict | None = None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        if url.endswith("/getMe"):
            return FakeResponse({"ok": True, "result": {"username": "ronin_test_bot"}})
        if url.endswith("/getUpdates"):
            # Serve the queued updates once, then empty (so loops can terminate).
            if not self._getupdates_served:
                self._getupdates_served = True
                return FakeResponse({"ok": True, "result": self._getupdates_result})
            return FakeResponse({"ok": True, "result": []})
        if url.endswith("/sendMessage"):
            return FakeResponse({"ok": True, "result": {"message_id": 1}})
        return FakeResponse({"ok": True, "result": {}})

    # convenience accessors -------------------------------------------------
    def sent_messages(self) -> list[dict]:
        return [c["json"] for c in self.calls if c["url"].endswith("/sendMessage")]

    def edited_messages(self) -> list[dict]:
        return [c["json"] for c in self.calls if c["url"].endswith("/editMessageText")]


# The bot sends a single "working..." status message before the agent runs and
# edits it for progress. Tests that care about the FINAL answer filter the status
# message out so the new live-progress behavior does not perturb their assertions.
def _answer_messages(client: "FakeClient") -> list[dict]:
    return [m for m in client.sent_messages() if m.get("text") != "working..."]


def _update(chat_id: int, text: str, update_id: int = 100) -> dict:
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": chat_id}},
    }


# ---------- token validation / fail-closed --------------------------------

def test_get_bot_token_missing_fails_closed() -> None:
    with pytest.raises(TelegramConfigError):
        get_bot_token(env={})


def test_get_bot_token_malformed_fails_closed() -> None:
    with pytest.raises(TelegramConfigError):
        get_bot_token(env={"TELEGRAM_BOT_TOKEN": "not-a-token"})


def test_get_bot_token_valid() -> None:
    assert get_bot_token(env={"TELEGRAM_BOT_TOKEN": VALID_TOKEN}) == VALID_TOKEN


def test_parse_allowed_chat_ids_merges_and_dedups() -> None:
    ids = parse_allowed_chat_ids("10, 20, oops, 10", config_ids=[20, 30])
    assert ids == [10, 20, 30]


def test_parse_allowed_chat_ids_empty() -> None:
    assert parse_allowed_chat_ids(None) == []
    assert parse_allowed_chat_ids("") == []


# ---------- 1. allowed chat -> agent invoked, answer sent ------------------

def test_allowed_chat_runs_agent_and_sends_answer() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return f"answer to: {text}"

    client = FakeClient(getupdates_result=[_update(42, "hello ronin")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == ["hello ronin"], "agent should be invoked once with the text"
    # A live-status "working..." message is sent first; the final answer follows.
    answers = _answer_messages(client)
    assert len(answers) == 1
    assert answers[0]["chat_id"] == 42
    assert answers[0]["text"] == "answer to: hello ronin"


# ---------- live progress: streams via editMessageText, not a flood --------

def test_two_arg_answer_fn_gets_progress_and_edits_status() -> None:
    """A two-arg answer_fn receives progress(); calling it EDITS the single
    status message in place (editMessageText), it does not send new messages."""
    got_progress: list = []

    def streaming_agent(text: str, progress) -> str:
        got_progress.append(progress)
        progress("| reading a.py")
        progress("/ editing a.py")
        return f"done: {text}"

    client = FakeClient(getupdates_result=[_update(42, "do it")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=streaming_agent,
        http=client,
    )
    bot.poll_once()

    assert got_progress and callable(got_progress[0]), "progress must be passed"
    # Exactly ONE status message ("working...") was sent; progress edited it.
    sent = client.sent_messages()
    status = [m for m in sent if m["text"] == "working..."]
    assert len(status) == 1, "exactly one live-status message is sent"
    # Two progress() calls -> two editMessageText calls (not new messages).
    edits = client.edited_messages()
    assert len(edits) == 2, "progress must EDIT in place, not flood new messages"
    assert edits[0]["text"] == "| reading a.py"
    assert edits[1]["text"] == "/ editing a.py"
    assert all(e["message_id"] == 1 for e in edits), "edits target the status message"
    # The final answer is still sent as a normal reply.
    answers = _answer_messages(client)
    assert answers[-1]["text"] == "done: do it"


def test_one_arg_answer_fn_still_works_unchanged() -> None:
    """An EXISTING one-arg answer_fn is called as answer_fn(text); no progress is
    forced on it, and no editMessageText is attempted by the agent path."""
    seen: list[str] = []

    def legacy_agent(text: str) -> str:  # one positional arg, as before
        seen.append(text)
        return "legacy ok"

    client = FakeClient(getupdates_result=[_update(42, "hello")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=legacy_agent,
        http=client,
    )
    bot.poll_once()

    assert seen == ["hello"], "one-arg answer_fn must still be invoked with the text"
    # No progress was pushed, so no edits happened.
    assert client.edited_messages() == []
    assert _answer_messages(client)[-1]["text"] == "legacy ok"


def test_progress_edit_failure_does_not_crash_answer() -> None:
    """If editMessageText raises, progress swallows it and the answer still sends."""
    class FlakyClient(FakeClient):
        def post(self, url: str, json=None, **kwargs):  # noqa: A002
            if url.endswith("/editMessageText"):
                raise RuntimeError("telegram edit blew up")
            return super().post(url, json=json, **kwargs)

    def streaming_agent(text: str, progress) -> str:
        progress("| working")  # this edit raises; must not propagate
        return "still answered"

    client = FlakyClient(getupdates_result=[_update(42, "go")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=streaming_agent,
        http=client,
    )
    bot.poll_once()  # must not raise

    assert _answer_messages(client)[-1]["text"] == "still answered"


def test_accepts_progress_arity_detection() -> None:
    assert TelegramBot._accepts_progress(lambda t, p: t) is True
    assert TelegramBot._accepts_progress(lambda t: t) is False
    assert TelegramBot._accepts_progress(lambda t, *a: t) is True


# ---------- 2. non-allowed chat -> agent NOT invoked, no answer ------------

def test_non_allowed_chat_is_ignored() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return "should not happen"

    client = FakeClient(getupdates_result=[_update(999, "let me in")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],  # 999 is not allowed
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == [], "agent must NOT run for a non-allowed chat"
    assert client.sent_messages() == [], "no message should be sent to a non-allowed chat"


# ---------- 3. empty allowlist -> onboarding reply only -------------------

def test_empty_allowlist_replies_only_with_chat_id() -> None:
    invoked: list[str] = []

    def fake_agent(text: str) -> str:
        invoked.append(text)
        return "should not happen"

    client = FakeClient(getupdates_result=[_update(777, "hi")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[],  # empty: nobody runs the agent
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    assert invoked == [], "agent must NOT run when the allowlist is empty"
    sent = client.sent_messages()
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 777
    assert "your chat id is 777" in sent[0]["text"]
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in sent[0]["text"]


# ---------- 4. missing token -> command fails closed, never polls ----------

def test_cli_missing_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # If the command ever tried to poll, this would explode loudly.
    def explode(*_a, **_k):
        raise AssertionError("must not construct a bot or poll without a token")

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", explode)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0
    assert "TELEGRAM_BOT_TOKEN" in r.stdout


def test_cli_malformed_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bogus")
    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0


# ---------- 5. long answer is chunked across multiple sendMessage ----------

def test_long_answer_is_chunked() -> None:
    long_text = "x" * (MAX_MESSAGE_CHARS * 2 + 500)

    def fake_agent(_text: str) -> str:
        return long_text

    client = FakeClient(getupdates_result=[_update(42, "give me a long one")])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=fake_agent,
        http=client,
    )
    bot.poll_once()

    sent = _answer_messages(client)  # drop the "working..." status message
    assert len(sent) >= 3, "a >2x-cap answer must span multiple sendMessage calls"
    assert all(len(m["text"]) <= MAX_MESSAGE_CHARS for m in sent)
    # the reassembled text matches the original (newline-stripped chunking ok here)
    assert "".join(m["text"] for m in sent) == long_text


def test_chunk_text_prefers_newline_boundaries() -> None:
    text = "a" * 10 + "\n" + "b" * (MAX_MESSAGE_CHARS)
    chunks = _chunk_text(text, MAX_MESSAGE_CHARS)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 10
    assert chunks[1] == "b" * MAX_MESSAGE_CHARS


# ---------- offset advances so messages are not reprocessed ----------------

def test_offset_advances_past_handled_updates() -> None:
    client = FakeClient(getupdates_result=[_update(42, "hi", update_id=555)])
    bot = TelegramBot(
        token=VALID_TOKEN,
        allowed_chat_ids=[42],
        answer_fn=lambda t: "ok",
        http=client,
    )
    bot.poll_once()
    assert bot._offset == 556, "offset must be one past the highest update_id seen"


# ---------- getMe surfaces the username ------------------------------------

def test_get_me_returns_username() -> None:
    client = FakeClient()
    bot = TelegramBot(token=VALID_TOKEN, http=client)
    me = bot.get_me()
    assert me["username"] == "ronin_test_bot"


# ---------- token never leaks via logs or error output ---------------------

def test_redact_token_masks_token_in_message() -> None:
    url = f"https://api.telegram.org/bot{VALID_TOKEN}/getUpdates"
    msg = f"Client error '429 Too Many Requests' for url '{url}'"
    out = redact_token(msg, VALID_TOKEN)
    assert VALID_TOKEN not in out
    assert REDACTED in out


def test_redact_token_noops_on_empty_token() -> None:
    # An empty/unknown token must not blank out unrelated text.
    msg = "some error with no token"
    assert redact_token(msg, "") == msg


def test_run_forever_redacts_token_in_poll_error_log(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """An httpx error during polling must be logged with the token redacted.

    The real httpx.HTTPStatusError str() contains the full request URL, which
    embeds the token. We assert the bot scrubs it before logging.
    """
    import httpx

    url = f"https://api.telegram.org/bot{VALID_TOKEN}/getUpdates"
    request = httpx.Request("POST", url)
    response = httpx.Response(429, request=request)

    # Sanity: the raw exception string really does carry the token.
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as raw:
        assert VALID_TOKEN in str(raw)

    class RaisingClient:
        def post(self, url: str, json=None, **kwargs):  # noqa: A002
            req = httpx.Request("POST", url)
            resp = httpx.Response(429, request=req)
            resp.raise_for_status()  # raises HTTPStatusError carrying the token

    # Make run_forever do exactly one error iteration, then stop the loop.
    sleeps: list[float] = []

    def fake_sleep(_seconds: float) -> None:
        sleeps.append(_seconds)
        raise KeyboardInterrupt  # break out of the while True after one error

    monkeypatch.setattr("ronin_cli.telegram_bot._sleep", fake_sleep)

    bot = TelegramBot(token=VALID_TOKEN, allowed_chat_ids=[42], http=RaisingClient())

    import logging

    with caplog.at_level(logging.WARNING, logger="ronin.telegram"):
        with pytest.raises(KeyboardInterrupt):
            bot.run_forever()

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert joined, "expected a warning log for the poll error"
    assert VALID_TOKEN not in joined, "token must not appear in poll error logs"
    assert REDACTED in joined


def test_cli_getme_failure_redacts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A startup getMe failure must not print the token to the terminal."""
    import httpx

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)

    def failing_get_me(self):
        url = f"https://api.telegram.org/bot{self.token}/getMe"
        req = httpx.Request("POST", url)
        resp = httpx.Response(401, request=req)
        resp.raise_for_status()

    monkeypatch.setattr(
        "ronin_cli.telegram_bot.TelegramBot.get_me", failing_get_me
    )

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code != 0
    assert "getMe failed" in r.stdout
    assert VALID_TOKEN not in r.stdout, "token must not be printed on getMe failure"
    assert REDACTED in r.stdout


# ---------- read-only file agent: root resolution -------------------------

def test_resolve_root_defaults_to_home() -> None:
    assert resolve_root(env={}) == Path.home().resolve()


def test_resolve_root_reads_env_and_expands(tmp_path: Path) -> None:
    assert resolve_root(env={ENV_ROOT: str(tmp_path)}) == tmp_path.resolve()


# ---------- secret-path guard ---------------------------------------------

def test_is_secret_path_blocks_ssh_dir(tmp_path: Path) -> None:
    # A file under ~/.ssh is protected, resolved against a fake home.
    home = tmp_path
    assert is_secret_path(home / ".ssh" / "id_rsa", home=home)
    assert is_secret_path(home / ".ssh" / "known_hosts", home=home)


def test_is_secret_path_blocks_other_secret_dirs(tmp_path: Path) -> None:
    home = tmp_path
    for rel in (".ronin/state.json", ".aws/credentials", ".gnupg/secring.gpg",
                ".config/gh/hosts.yml", ".netrc"):
        assert is_secret_path(home / rel, home=home), rel


def test_is_secret_path_blocks_secret_filename_globs(tmp_path: Path) -> None:
    home = tmp_path
    proj = home / "projects" / "app"
    for name in ("server.pem", "tls.key", "id_rsa", "id_ed25519", ".env",
                 "prod.env", "credentials.json", "my_secret_token.txt"):
        assert is_secret_path(proj / name, home=home), name


def test_is_secret_path_blocks_nested_dotfile_secret_component(tmp_path: Path) -> None:
    # A nested .ssh anywhere in the tree is refused, not just under home.
    home = tmp_path
    assert is_secret_path(home / "projects" / "x" / ".ssh" / "config", home=home)


def test_is_secret_path_allows_ordinary_files(tmp_path: Path) -> None:
    home = tmp_path
    for rel in ("projects/app/README.md", "notes.txt", "src/main.py",
                "envs.md", "key_takeaways.md"):
        assert not is_secret_path(home / rel, home=home), rel


def test_guard_takes_effect_on_read_file_tool(tmp_path: Path) -> None:
    """The deny predicate wired into the read tools actually refuses a secret."""
    from ronin_cli.code_tools import build_code_tools

    home = tmp_path
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY DATA")
    (home / "notes.txt").write_text("hello world")

    tools = {
        t.name: t
        for t in build_code_tools(home, deny=lambda p: is_secret_path(p, home=home))
    }
    # secret -> refused, contents never returned
    out = tools["read_file"].handler(path=".ssh/id_rsa")
    assert out == "refused: that path is protected"
    assert "PRIVATE KEY DATA" not in out
    # ordinary file -> still readable
    assert tools["read_file"].handler(path="notes.txt") == "hello world"


def test_guard_skips_secret_in_listing_and_search(tmp_path: Path) -> None:
    from ronin_cli.code_tools import build_code_tools

    home = tmp_path
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("SECRET=topsecret")
    (home / "notes.txt").write_text("SECRET=public-note")

    tools = {
        t.name: t
        for t in build_code_tools(home, deny=lambda p: is_secret_path(p, home=home))
    }
    listing = tools["list_files"].handler(directory=".")
    assert not any("id_rsa" in entry for entry in listing)
    assert any("notes.txt" in entry for entry in listing)

    hits = tools["search_files"].handler(query="SECRET=")
    assert all(".ssh" not in h["file"] and "id_rsa" not in h["file"] for h in hits)


def test_build_code_tools_default_deny_is_noop(tmp_path: Path) -> None:
    """No deny predicate => read tools behave exactly as before for everyone."""
    from ronin_cli.code_tools import build_code_tools

    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("KEYDATA")
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    # Without a deny predicate the secret file is readable (unchanged behavior).
    assert tools["read_file"].handler(path=".ssh/id_rsa") == "KEYDATA"


# ---------- CLI wiring: read-only code agent, never write path ------------

class _FakeRes:
    def __init__(self, output: str = "", error: str | None = None) -> None:
        self.output = output
        self.error = error


def test_cli_runs_read_only_code_agent_rooted_at_default_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowed message runs run_code_agent(read_only=True, root=home) and
    sends its output back. No model/network: run_code_agent is mocked."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.delenv(ENV_ROOT, raising=False)

    calls: list[dict] = []

    def fake_run_code_agent(config, task, **kwargs):
        calls.append({"task": task, **kwargs})
        return _FakeRes(output=f"saw: {task}")

    monkeypatch.setattr("ronin_cli.code_mode.run_code_agent", fake_run_code_agent)

    client = FakeClient(getupdates_result=[_update(42, "what is in my repo?")])

    real_bot = TelegramBot

    captured: dict = {}

    def capture_bot(**kwargs):
        kwargs["http"] = client
        bot = real_bot(**kwargs)
        captured["bot"] = bot
        return bot

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", capture_bot)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code == 0, r.stdout

    assert len(calls) == 1, "the read-only code agent must run exactly once"
    call = calls[0]
    assert call["read_only"] is True
    assert call["root"] == Path.home().resolve()
    assert call["console"] is None
    assert call["include_image_tool"] is False
    assert callable(call["deny"]), "the secret-path guard must be passed as deny"
    assert call["max_iterations"] >= 1
    # the output is sent back to the chat (after the "working..." status message)
    answers = _answer_messages(client)
    assert answers and answers[0]["chat_id"] == 42
    assert answers[0]["text"] == "saw: what is in my repo?"
    # an on_step_cb must be wired so progress streams as steps arrive
    assert callable(call.get("on_step_cb")), "on_step_cb must be passed for live progress"


def test_cli_streams_progress_via_edit_message_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the CLI answer_fn wires on_step_cb; a step emitted by
    run_code_agent turns into a live editMessageText, and the final answer is a
    normal reply. No model/network: run_code_agent is mocked and emits a step."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.delenv(ENV_ROOT, raising=False)

    class _Step:
        def __init__(self, kind, content):
            self.kind = kind
            self.content = content

    def fake_run_code_agent(config, task, **kwargs):
        cb = kwargs.get("on_step_cb")
        if cb is not None:
            cb(_Step("tool_call", {"name": "read_file", "input": {"path": "x.py"}}))
        return _FakeRes(output="all done")

    monkeypatch.setattr("ronin_cli.code_mode.run_code_agent", fake_run_code_agent)

    client = FakeClient(getupdates_result=[_update(42, "look at x.py")])

    def capture_bot(**kwargs):
        kwargs["http"] = client
        return TelegramBot(**kwargs)

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", capture_bot)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code == 0, r.stdout

    edits = client.edited_messages()
    assert edits, "the step must have produced a live editMessageText"
    assert "reading x.py" in edits[-1]["text"]
    assert _answer_messages(client)[-1]["text"] == "all done"


def test_cli_never_passes_write_path_to_code_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_code_agent is NEVER called with read_only False, and no extra_tools
    or full-access write path is passed."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.delenv(ENV_ROOT, raising=False)

    calls: list[dict] = []

    def fake_run_code_agent(config, task, **kwargs):
        calls.append(kwargs)
        return _FakeRes(output="ok")

    monkeypatch.setattr("ronin_cli.code_mode.run_code_agent", fake_run_code_agent)

    client = FakeClient(getupdates_result=[_update(42, "hi")])

    def capture_bot(**kwargs):
        kwargs["http"] = client
        return TelegramBot(**kwargs)

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", capture_bot)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code == 0, r.stdout

    assert calls, "the code agent should have been invoked"
    for kwargs in calls:
        assert kwargs.get("read_only") is True, "must always be read-only"
        assert kwargs.get("read_only") is not False
        assert "extra_tools" not in kwargs, "no extra/write tools may be passed"
        # config.full_access stays at its default (False); the command never sets it.


# ---------- main.py: step -> human progress line + throttling --------------

class _FakeStep:
    """Minimal stand-in for ronin_agent_patterns Step (kind + content)."""
    def __init__(self, kind: str, content) -> None:
        self.kind = kind
        self.content = content


def test_step_line_maps_tool_calls_to_human_lines() -> None:
    from ronin_cli.main import _telegram_step_line

    line = _telegram_step_line(_FakeStep("tool_call", {"name": "read_file", "input": {"path": "src/app.py"}}))
    assert line == "reading src/app.py"
    assert _telegram_step_line(
        _FakeStep("tool_call", {"name": "edit_file", "input": {"path": "main.py"}})
    ) == "editing main.py"
    assert _telegram_step_line(
        _FakeStep("tool_call", {"name": "run_command", "input": {"command": "pytest -q"}})
    ) == "ran: pytest -q"
    assert _telegram_step_line(
        _FakeStep("tool_call", {"name": "search_files", "input": {"query": "TODO"}})
    ) == "searching for TODO"
    # non tool_call steps (thoughts, results) are skipped
    assert _telegram_step_line(_FakeStep("thought", "thinking...")) is None
    assert _telegram_step_line(_FakeStep("tool_result", {"name": "read_file"})) is None


def test_step_progress_calls_progress_as_steps_arrive_and_throttles() -> None:
    """on_step_cb pushes a line on the first step, then coalesces by time so it
    does not edit the status message on every single step."""
    from ronin_cli.main import _telegram_step_progress

    pushed: list[str] = []
    clock = {"t": 100.0}
    on_step = _telegram_step_progress(pushed.append, _now=lambda: clock["t"])

    # first tool_call -> shown immediately
    on_step(_FakeStep("tool_call", {"name": "read_file", "input": {"path": "a.py"}}))
    # second within the same instant -> throttled away
    on_step(_FakeStep("tool_call", {"name": "read_file", "input": {"path": "b.py"}}))
    assert len(pushed) == 1, "rapid steps must be coalesced, not one edit per step"

    # after the interval, the next step shows again
    clock["t"] += 2.0
    on_step(_FakeStep("tool_call", {"name": "edit_file", "input": {"path": "c.py"}}))
    assert len(pushed) == 2
    assert "reading a.py" in pushed[0]
    assert "editing c.py" in pushed[1]
    # non tool steps never push
    on_step(_FakeStep("thought", "hmm"))
    assert len(pushed) == 2


def test_cli_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """RONIN_TELEGRAM_ROOT overrides the default home root."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.setenv(ENV_ROOT, str(tmp_path))

    calls: list[dict] = []

    def fake_run_code_agent(config, task, **kwargs):
        calls.append(kwargs)
        return _FakeRes(output="ok")

    monkeypatch.setattr("ronin_cli.code_mode.run_code_agent", fake_run_code_agent)

    client = FakeClient(getupdates_result=[_update(42, "hi")])

    def capture_bot(**kwargs):
        kwargs["http"] = client
        return TelegramBot(**kwargs)

    monkeypatch.setattr("ronin_cli.telegram_bot.TelegramBot", capture_bot)

    r = runner.invoke(app, ["telegram", "--once"])
    assert r.exit_code == 0, r.stdout
    assert calls and calls[0]["root"] == tmp_path.resolve()
