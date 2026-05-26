"""End-to-end customer-support agent built on Ronin.

Demonstrates how the kit's modules compose into a real product:
- ``InjectionScanner`` (hardening) — incoming ticket is scanned first.
- ``SupervisorAgent`` (agent-patterns) — orchestrator delegates to specialists.
- Three sub-agents: triage, knowledge-base lookup, response drafter.
- Stripe + Linear demo tools (mcp-servers) — billing and engineering signals.
- ``OutputValidator`` (hardening) — final response must conform to ``DraftReply`` schema.
- ``ShortTermMemory`` (memory) — kept for hand-offs between turns (multi-message tickets).

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python examples/customer-support/main.py "I was charged twice for my Pro plan!"

The example runs against the same demo Stripe + Linear fixtures the CLI uses, so
no real credentials are required.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from pydantic import BaseModel, Field

from ro_claude_kit_agent_patterns import (
    AnthropicProvider,
    LLMProvider,
    SubAgent,
    SupervisorAgent,
    Tool,
)
from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.tools import build_tools
from ro_claude_kit_hardening import InjectionScanner

# Make the local kb module importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb import search_kb  # noqa: E402


class DraftReply(BaseModel):
    """The structured response the drafter produces."""

    category: str = Field(description="One of: billing, technical, account, feature_request, other")
    summary: str = Field(description="One sentence summary of the ticket.")
    body: str = Field(description="The reply body, ready to send to the customer.")
    cited_kb_ids: list[str] = Field(default_factory=list, description="kb-XXX ids cited.")
    suggested_followups: list[str] = Field(default_factory=list)


KB_SEARCH_TOOL = Tool(
    name="kb_search",
    description="Search the support knowledge base for articles relevant to the customer's question.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Keywords from the ticket."}},
        "required": ["query"],
    },
    handler=lambda query: search_kb(query),
)


def build_supervisor(provider: LLMProvider) -> SupervisorAgent:
    """Wire the orchestrator with three specialist sub-agents."""
    config = CSKConfig(demo_mode=True)  # provides demo Stripe + Linear tools
    service_tools = build_tools(config)
    stripe_tools_only = [t for t in service_tools if t.name.startswith("stripe_")]
    linear_tools_only = [t for t in service_tools if t.name.startswith("linear_")]

    triage = SubAgent(
        name="triage",
        description=(
            "Classifies a customer ticket into category, urgency, and required-data. "
            "Use this first before delegating to billing_lookup or kb_lookup."
        ),
        system=(
            "You categorize support tickets. Classify into exactly one of: billing, technical, "
            "account, feature_request, other. Reply with: 'category=<x>; urgency=<low|med|high>; "
            "needs=<comma-separated list of: stripe, linear, kb>'. Be terse — one line."
        ),
        provider=provider,
    )

    billing_lookup = SubAgent(
        name="billing_lookup",
        description="Looks up Stripe customer + subscription + recent charges by email or customer_id.",
        system=(
            "You are a billing-data assistant. Use the stripe_* tools to look up the customer, their "
            "subscriptions, and recent charges. Summarize what you found in 3-5 lines. Do not draft "
            "the customer-facing reply."
        ),
        tools=stripe_tools_only,
        provider=provider,
    )

    kb_lookup = SubAgent(
        name="kb_lookup",
        description="Searches the support knowledge base for relevant articles.",
        system=(
            "You are a knowledge-base lookup assistant. Use kb_search to find at most 3 relevant "
            "articles. Output the article ids and one-line summaries. Do not draft the reply."
        ),
        tools=[KB_SEARCH_TOOL],
        provider=provider,
    )

    eng_lookup = SubAgent(
        name="eng_lookup",
        description="Checks Linear for known engineering issues that match the ticket symptoms.",
        system=(
            "You search Linear for active engineering issues that may explain a customer's problem. "
            "Use linear_list_issues with state='In Progress' or 'Todo'. Output relevant issue ids "
            "and one-line summaries, or 'no matching issues'."
        ),
        tools=linear_tools_only,
        provider=provider,
    )

    supervisor_system = (
        "You are a senior support engineer orchestrating specialists. For each ticket:\n"
        "1. Delegate to triage to classify and route.\n"
        "2. Based on triage output, delegate to billing_lookup, kb_lookup, and/or eng_lookup as needed.\n"
        "3. Synthesize a final reply for the customer. The reply MUST be a single JSON object "
        "matching this Pydantic schema, wrapped in <reply></reply> tags:\n"
        f"{json.dumps(DraftReply.model_json_schema(), indent=2)}"
    )
    return SupervisorAgent(
        system=supervisor_system,
        sub_agents=[triage, billing_lookup, kb_lookup, eng_lookup],
        provider=provider,
        max_iterations=12,
    )


def parse_reply(text: str) -> DraftReply:
    """Extract the JSON DraftReply from <reply></reply> tags."""
    import re
    match = re.search(r"<reply>(.*?)</reply>", text, re.DOTALL)
    if not match:
        # Try to find a bare JSON object as fallback
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if not match:
            raise ValueError(f"no DraftReply JSON found in: {text[:300]}")
    return DraftReply.model_validate_json(match.group(1).strip())


def handle_ticket(ticket: str, provider: LLMProvider) -> tuple[DraftReply, list[Any]]:
    """Full pipeline: scan, classify, look up, draft."""
    scan = InjectionScanner().scan(ticket)
    if scan.flagged:
        raise ValueError(f"ticket flagged as injection: {scan.hits}")

    supervisor = build_supervisor(provider)
    result = supervisor.run(ticket)
    if not result.success:
        raise RuntimeError(f"agent run failed: {result.error}")

    return parse_reply(result.output), result.trace


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python main.py "<customer ticket text>"')
        sys.exit(1)

    ticket = " ".join(sys.argv[1:])
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — this example needs a real Claude key. Get one from")
        print("  https://console.anthropic.com/settings/keys")
        sys.exit(2)

    provider = AnthropicProvider()
    reply, trace = handle_ticket(ticket, provider)

    print("=" * 70)
    print(f"CATEGORY:        {reply.category}")
    print(f"SUMMARY:         {reply.summary}")
    print(f"CITED KB:        {', '.join(reply.cited_kb_ids) or '(none)'}")
    print(f"FOLLOWUPS:       {', '.join(reply.suggested_followups) or '(none)'}")
    print()
    print("DRAFT REPLY:")
    print("-" * 70)
    print(reply.body)
    print("=" * 70)
    print(f"\n[{len(trace)} trace steps]")


if __name__ == "__main__":
    main()
