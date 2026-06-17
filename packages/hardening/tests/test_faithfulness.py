"""Tests for the faithfulness / grounding harness.

All offline: no provider, no network, no keys. The integration test drives the
real ReAct agent through the in-memory FakeProvider to prove the trace hook
works end to end.
"""
from __future__ import annotations

import pytest

from ronin_hardening import (
    FaithfulnessHarness,
    Source,
    extract_symbols,
    sources_from_trace,
    split_claims,
)

# A small "codebase" the agent supposedly read.
AUTH_SOURCE = Source(
    origin="read_file",
    text=(
        "def login(username, password):\n"
        "    token = make_token(username)\n"
        "    return Session(token=token)\n"
        "\n"
        "class Session:\n"
        "    def __init__(self, token):\n"
        "        self.token = token\n"
    ),
)


# ---------------------------------------------------------------------------
# Claim decomposition
# ---------------------------------------------------------------------------


def test_split_claims_breaks_sentences() -> None:
    claims = split_claims("The login function returns a Session. It calls make_token.")
    assert len(claims) == 2
    assert any("login" in c for c in claims)
    assert any("make_token" in c for c in claims)


def test_split_claims_keeps_code_fence_whole() -> None:
    answer = "Here is the code:\n```python\nx = login(u, p)\ny = 1\n```\nDone."
    claims = split_claims(answer)
    fence = [c for c in claims if c.startswith("```")]
    assert len(fence) == 1
    assert "login(u, p)" in fence[0]


def test_split_claims_empty() -> None:
    assert split_claims("") == []
    assert split_claims("   ") == []


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def test_extract_symbols_finds_code_identifiers() -> None:
    syms = extract_symbols("Call make_token(x) then return Session.token from auth.py")
    assert "make_token" in syms
    assert "Session.token" in syms
    assert "auth.py" in syms


def test_extract_symbols_ignores_plain_prose() -> None:
    syms = extract_symbols("the function returns a value when the input is valid")
    # No snake_case, dotted, path, or CamelCase tokens here.
    assert syms == set()


# ---------------------------------------------------------------------------
# Grounding: the core signal must be REAL (a constant impl fails these).
# ---------------------------------------------------------------------------


def test_grounded_answer_scores_high() -> None:
    harness = FaithfulnessHarness()
    answer = (
        "The login function calls make_token and returns a Session. "
        "Session stores the token."
    )
    report = harness.check(answer, [AUTH_SOURCE])
    assert not report.abstain
    assert report.score >= 0.65
    assert report.grounded_fraction == 1.0
    assert report.hallucinated_symbols == []


def test_hallucinated_symbol_is_flagged() -> None:
    """A function the agent never read must be flagged and drag the score down."""
    harness = FaithfulnessHarness()
    answer = (
        "The login function calls make_token. "
        "It then calls refresh_oauth_token to rotate the credentials."
    )
    report = harness.check(answer, [AUTH_SOURCE])
    assert "refresh_oauth_token" in report.hallucinated_symbols
    # The fabricated-symbol claim is ungrounded.
    bad = [c for c in report.claims if "refresh_oauth_token" in c.claim]
    assert bad and not bad[0].grounded
    assert report.score < 1.0


def test_fully_fabricated_answer_scores_low() -> None:
    """An answer about a totally different, unread API is not grounded."""
    harness = FaithfulnessHarness()
    answer = (
        "The PaymentGateway.charge method validates the credit_card_number "
        "and calls stripe_client.create_charge with the billing_address."
    )
    report = harness.check(answer, [AUTH_SOURCE])
    assert report.score < 0.45
    assert {"PaymentGateway.charge", "stripe_client.create_charge"} <= set(
        report.hallucinated_symbols
    )


def test_not_constant_grounded_vs_ungrounded_diverge() -> None:
    """Guards against a stubbed 'always grounded' regression: the two scores
    must genuinely differ for the same harness on different inputs."""
    harness = FaithfulnessHarness()
    good = harness.check(
        "login returns a Session and calls make_token.", [AUTH_SOURCE]
    )
    bad = harness.check(
        "delete_everything wipes the QuantumDatabase via warp_drive().", [AUTH_SOURCE]
    )
    assert good.score > bad.score
    assert good.score - bad.score > 0.4


# ---------------------------------------------------------------------------
# Calibration + abstain
# ---------------------------------------------------------------------------


def test_abstain_when_no_sources() -> None:
    harness = FaithfulnessHarness()
    report = harness.check("login returns a Session.", [])
    assert report.abstain
    assert "too few sources" in report.reason


def test_abstain_in_uncertain_band() -> None:
    """A genuinely ambiguous answer — partial lexical overlap, no hallucinated
    symbol either way — lands in the uncertain band, so the harness abstains
    rather than asserting grounded or not. We force a narrow band so a 0.5-ish
    score is squarely uncertain."""
    harness = FaithfulnessHarness(abstain_low=0.4, abstain_high=0.75)
    answer = (
        "The login function returns a Session object. "
        "It also records a structured event before responding."
    )
    report = harness.check(answer, [AUTH_SOURCE])
    # One claim grounds (login/Session), one does not (the event prose), and no
    # symbol is fabricated — exactly the ambiguous middle.
    assert harness.abstain_low < report.score < harness.abstain_high
    assert report.abstain


def test_half_fabricated_answer_scores_low_and_flags() -> None:
    """One solid claim plus one with a fabricated symbol must score firmly low
    (below the abstain band) and surface the hallucinated symbol — a clear
    hallucination is not 'uncertain', it is ungrounded."""
    harness = FaithfulnessHarness()
    answer = (
        "The login function returns a Session. "
        "The platform also exposes a GraphQLResolver.subscribe streaming endpoint."
    )
    report = harness.check(answer, [AUTH_SOURCE])
    assert report.score < harness.abstain_low
    assert "GraphQLResolver.subscribe" in report.hallucinated_symbols


def test_score_is_zero_for_no_scorable_claims() -> None:
    harness = FaithfulnessHarness()
    report = harness.check("Here is the result:", [AUTH_SOURCE])
    # Only a trivial claim → abstain, score 0.
    assert report.abstain
    assert report.score == 0.0


def test_semantic_judge_rescues_paraphrase() -> None:
    """A claim the lexical pass misses can be grounded by an optional judge —
    proving the hook exists and is consulted only for ungrounded claims."""
    calls: list[str] = []

    def judge(claim: str, _sources: str) -> float:
        calls.append(claim)
        return 0.9

    harness = FaithfulnessHarness(semantic_judge=judge)
    # Paraphrase with low lexical overlap with the source.
    report = harness.check("Authentication yields a brand new session object.", [AUTH_SOURCE])
    assert calls, "semantic judge was never consulted"
    sem = [c for c in report.claims if c.method == "semantic"]
    assert sem and sem[0].grounded


def test_check_edit_flags_ungrounded_write() -> None:
    """A proposed edit that calls an unread helper is an ungrounded edit."""
    harness = FaithfulnessHarness()
    new_code = "def logout(session):\n    revoke_token(session.token)\n"
    report = harness.check_edit(new_code, [AUTH_SOURCE])
    # revoke_token never appears in the source the agent read.
    assert "revoke_token" in report.hallucinated_symbols


def test_check_edit_grounded_write_is_clean() -> None:
    harness = FaithfulnessHarness()
    new_code = "session = login(username, password)\ntoken = session.token\n"
    report = harness.check_edit(new_code, [AUTH_SOURCE])
    assert report.hallucinated_symbols == []


# ---------------------------------------------------------------------------
# Trace extraction
# ---------------------------------------------------------------------------


def test_sources_from_trace_pulls_read_results() -> None:
    trace = [
        {"kind": "tool_call", "content": {"name": "read_file", "input": {"path": "a.py"}}},
        {"kind": "tool_result", "content": {"name": "read_file", "result": "def f(): pass", "is_error": False}},
        {"kind": "tool_result", "content": {"name": "write_file", "result": "wrote 10 chars", "is_error": False}},
        {"kind": "tool_result", "content": {"name": "read_file", "result": "boom", "is_error": True}},
        {"kind": "final", "content": "done"},
    ]
    sources = sources_from_trace(trace)
    # Only the successful read_file result; write_file and the errored read excluded.
    assert len(sources) == 1
    assert sources[0].text == "def f(): pass"
    assert sources[0].origin == "read_file"


def test_sources_from_trace_include_all_tools() -> None:
    trace = [
        {"kind": "tool_result", "content": {"name": "my_custom_reader", "result": "data", "is_error": False}},
    ]
    assert sources_from_trace(trace) == []  # not in default evidence set
    assert len(sources_from_trace(trace, include_all_tools=True)) == 1


# ---------------------------------------------------------------------------
# End-to-end through the real ReAct agent + FakeProvider (offline)
# ---------------------------------------------------------------------------


def test_end_to_end_with_react_agent_and_fake_provider() -> None:
    ap = pytest.importorskip("ronin_agent_patterns")
    fake_mod = pytest.importorskip("ronin_agent_patterns.providers.fake")

    Tool = ap.Tool
    ReActAgent = ap.ReActAgent
    LLMResponse = ap.LLMResponse
    ToolCall = ap.ToolCall
    FakeProvider = fake_mod.FakeProvider

    file_body = (
        "def login(username, password):\n"
        "    return make_token(username)\n"
    )

    def read_file(path: str) -> str:
        return file_body

    read_tool = Tool(
        name="read_file",
        description="Read a file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=read_file,
    )

    # Turn 1: the model reads the file. Turn 2: it answers (grounded).
    provider = FakeProvider(
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "auth.py"})],
                stop_reason="tool_use",
            ),
            LLMResponse(
                text="The login function calls make_token and returns its result.",
                stop_reason="end_turn",
            ),
        ]
    )
    agent = ReActAgent(system="You are a code assistant.", tools=[read_tool], provider=provider)
    result = agent.run("What does login do?")

    sources = sources_from_trace(result.trace)
    assert sources and "make_token" in sources[0].text

    report = FaithfulnessHarness().check(result.output, sources)
    assert not report.abstain
    assert report.grounded_fraction == 1.0
    assert report.hallucinated_symbols == []


def test_end_to_end_catches_hallucination_in_agent_answer() -> None:
    ap = pytest.importorskip("ronin_agent_patterns")
    fake_mod = pytest.importorskip("ronin_agent_patterns.providers.fake")

    Tool, ReActAgent = ap.Tool, ap.ReActAgent
    LLMResponse, ToolCall = ap.LLMResponse, ap.ToolCall
    FakeProvider = fake_mod.FakeProvider

    def read_file(path: str) -> str:
        return "def login(u, p):\n    return make_token(u)\n"

    read_tool = Tool(
        name="read_file",
        description="Read a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=read_file,
    )
    provider = FakeProvider(
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "auth.py"})],
                stop_reason="tool_use",
            ),
            # The model fabricates a call to a never-seen function.
            LLMResponse(
                text="login calls make_token and then audit_log_to_splunk for compliance.",
                stop_reason="end_turn",
            ),
        ]
    )
    agent = ReActAgent(system="assistant", tools=[read_tool], provider=provider)
    result = agent.run("Explain login.")

    report = FaithfulnessHarness().check(result.output, sources_from_trace(result.trace))
    assert "audit_log_to_splunk" in report.hallucinated_symbols
