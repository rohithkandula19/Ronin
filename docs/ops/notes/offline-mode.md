# Offline mode

Ronin can keep local missions and file tools usable when the network is down.

- Prefer cached models and local providers when online checks fail.
- Queue outbound telemetry and MCP calls; flush when connectivity returns.
- Do not treat missing remote providers as a fatal install error.
- Document which commands work fully offline (`doctor --offline`, local file ops).
