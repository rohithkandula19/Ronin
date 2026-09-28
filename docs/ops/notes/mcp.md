# MCP

Model Context Protocol servers extend Ronin tools without shipping secrets in-repo.

- Register servers in local config only; never commit tokens.
- Health-check each server from `doctor` before a mission that depends on it.
- Isolate failing MCP servers so one timeout does not block local tools.
- Prefer stdio transports for local ops; use HTTP only when documented.
