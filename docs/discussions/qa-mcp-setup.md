# Q&A seed — paste into Ronin Discussions (Q&A category)

Create this as a new **Q&A** discussion, then reply with the answer and mark it as the answer.

## Title

How do I connect an MCP server to Ronin without breaking `--offline`?

## Body

I want to attach a local MCP server (filesystem or GitHub) so Ronin can use extra tools. What is the supported way to register it, and what happens if I start Ronin with `--offline` after that?

## Answer (post as a comment, then Mark as answer)

Register MCP servers in project config, not as a global side-channel.

1. Add the server under the project MCP list (see `docs/site/quickstart.md` and `ronin mcp-serve` in the v2 CLI).
2. Restart the session so the tool list is rebuilt. Use `/mcp` in-session if your build exposes it.
3. `--offline` is a hard network floor: it forces a local model and **strips network tools**. A local stdio MCP server can still run. Anything that calls the public internet (GitHub HTTP, search, hosted APIs) is disabled for that session.
4. If a tool disappears after `--offline`, that is expected — not a config bug. Drop `--offline` when you need those tools.

Keep provider keys out of MCP config. Ronin treats MCP config as untrusted context and should not inherit extra write permissions from a server.
