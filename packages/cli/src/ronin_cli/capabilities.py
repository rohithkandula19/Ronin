"""Tell the agent what *ronin* can do — so it stops inventing generic tutorials.

Weak/open models, asked "how do I connect my GitHub?", answer with a generic
`git clone` walkthrough because they don't know ronin has `ronin mcp install
github`. This builds a short system-prompt block that (a) states ronin's three
integration routes and the exact commands, and (b) lists what's connected right
now (derived from the agent's tool names). Pure and unit-tested.
"""
from __future__ import annotations


def connected_mcp_servers(tool_names: list[str]) -> list[str]:
    """MCP server names inferred from ``server__tool`` tool names. Pure."""
    return sorted({n.split("__", 1)[0] for n in tool_names if "__" in n})


def capability_block(tool_names: list[str]) -> str:
    """A concise system-prompt block making the agent aware of ronin's integration
    commands and currently-connected servers. Pure given the tool names."""
    lines = [
        "## ronin integrations",
        "You are ronin, a provider-agnostic terminal coding agent. Besides editing "
        "and running code, you gain external tools three ways — each ONE command the "
        "user runs in their shell:",
        "- MCP servers: `ronin mcp install <name>` (github, gmail, slack, postgres, "
        "notion, gdrive, brave-search, …), or `ronin mcp add-remote <name> <url>` for "
        "hosted servers (Linear, Atlassian, …). Browse: `ronin mcp catalog`.",
        "- Plugins: `ronin plugin add <name>` for 50+ built-ins (weather, currency, "
        "github_repo, dns_lookup, translate, …), or `ronin plugin new <name>` to "
        "scaffold a custom tool wrapping any API. Browse: `ronin plugin library`.",
        "When the user asks how to connect a service (GitHub, email, a database, "
        "etc.), recommend the exact ronin command above — do NOT give a generic "
        "git-clone, API-signup, or external-setup tutorial. If a capability needs a "
        "tool you don't currently have, say which `ronin mcp install` / `ronin plugin "
        "add` command adds it.",
    ]
    servers = connected_mcp_servers(tool_names)
    if servers:
        lines.append(f"Currently connected MCP servers (their tools are available to "
                     f"you now): {', '.join(servers)}.")
    return "\n".join(lines)
