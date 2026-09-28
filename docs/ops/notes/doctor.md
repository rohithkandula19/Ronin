# Doctor

`doctor` is the first-pass health check after install or a failed mission.

- Verify PATH, runtime versions, and config file locations.
- Report provider and MCP reachability separately from local file tools.
- Exit non-zero on missing required binaries; warn-only on optional extras.
- Keep output copy-pasteable for SUPPORT.md and issue reports.
