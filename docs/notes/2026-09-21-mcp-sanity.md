# MCP server sanity notes

- Prefer explicit allowlists for tools exposed to the agent.
- Fail closed if a server handshake times out.
- Log tool name + latency, never raw secrets from server env.
- Reconnect with backoff; do not spawn duplicate sessions for the same server id.
