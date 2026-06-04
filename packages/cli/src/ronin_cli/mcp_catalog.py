"""A curated catalog of popular MCP servers — `ronin mcp catalog` / `install`.

ronin can already connect to *any* MCP server via `ronin mcp add NAME COMMAND …`,
but you have to know each server's package name and which env vars it needs. This
catalog encodes the well-known ones (GitHub, Gmail, Slack, Postgres, Drive, web
search, …) so adding one is `ronin mcp install github`. The catalog data and
resolution are pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnownServer:
    key: str
    command: str
    args: list[str]
    env: list[str] = field(default_factory=list)   # env var names the server needs
    blurb: str = ""
    note: str = ""                                  # caveat (e.g. "community / OAuth")


# Reference servers from modelcontextprotocol/servers plus a couple of widely-used
# community ones (flagged in `note`). npx for node servers, uvx for python ones.
CATALOG: dict[str, KnownServer] = {
    "github": KnownServer(
        "github", "npx", ["-y", "@modelcontextprotocol/server-github"],
        ["GITHUB_PERSONAL_ACCESS_TOKEN"], "Issues, PRs, repo & code search, file ops"),
    "gitlab": KnownServer(
        "gitlab", "npx", ["-y", "@modelcontextprotocol/server-gitlab"],
        ["GITLAB_PERSONAL_ACCESS_TOKEN"], "GitLab issues, MRs, repo ops"),
    "gmail": KnownServer(
        "gmail", "npx", ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        [], "Read, search & send Gmail", note="community · OAuth on first run"),
    "slack": KnownServer(
        "slack", "npx", ["-y", "@modelcontextprotocol/server-slack"],
        ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"], "Read channels & post messages"),
    "filesystem": KnownServer(
        "filesystem", "npx", ["-y", "@modelcontextprotocol/server-filesystem", "."],
        [], "Read/write files under a directory (edit the path arg)"),
    "postgres": KnownServer(
        "postgres", "npx", ["-y", "@modelcontextprotocol/server-postgres"],
        [], "Query Postgres (append your connection URL as an arg)"),
    "sqlite": KnownServer(
        "sqlite", "uvx", ["mcp-server-sqlite", "--db-path", "./database.sqlite"],
        [], "Query a local SQLite database (edit the db path)"),
    "gdrive": KnownServer(
        "gdrive", "npx", ["-y", "@modelcontextprotocol/server-gdrive"],
        [], "Search & read Google Drive files", note="OAuth on first run"),
    "brave-search": KnownServer(
        "brave-search", "npx", ["-y", "@modelcontextprotocol/server-brave-search"],
        ["BRAVE_API_KEY"], "Web search via the Brave API"),
    "fetch": KnownServer(
        "fetch", "uvx", ["mcp-server-fetch"],
        [], "Fetch a URL and convert it to clean markdown"),
    "puppeteer": KnownServer(
        "puppeteer", "npx", ["-y", "@modelcontextprotocol/server-puppeteer"],
        [], "Headless-browser automation (navigate, click, screenshot)"),
    "memory": KnownServer(
        "memory", "npx", ["-y", "@modelcontextprotocol/server-memory"],
        [], "A persistent knowledge-graph memory the agent can write to"),
    "notion": KnownServer(
        "notion", "npx", ["-y", "@notionhq/notion-mcp-server"],
        ["NOTION_API_KEY"], "Read & update Notion pages/databases", note="community"),
}

# friendly aliases → catalog key
_ALIASES = {
    "mail": "gmail", "google-mail": "gmail", "email": "gmail",
    "fs": "filesystem", "files": "filesystem",
    "pg": "postgres", "psql": "postgres",
    "drive": "gdrive", "google-drive": "gdrive",
    "search": "brave-search", "brave": "brave-search", "web": "fetch",
    "browser": "puppeteer",
}


def resolve(name: str) -> KnownServer | None:
    """Look up a catalog entry by key or alias (case-insensitive). Pure."""
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    return CATALOG.get(key)


def catalog_rows() -> list[tuple[str, str, str]]:
    """(key, env-summary, blurb[+note]) for every server, for display. Pure."""
    rows: list[tuple[str, str, str]] = []
    for key, s in CATALOG.items():
        env = ", ".join(s.env) if s.env else "—"
        blurb = s.blurb + (f" ({s.note})" if s.note else "")
        rows.append((key, env, blurb))
    return rows
