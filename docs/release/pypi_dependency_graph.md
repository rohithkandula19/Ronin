# Ronin PyPI Dependency Graph

Mapped from `packages/*/pyproject.toml` and the built `ronin_cli` wheel METADATA
on `fix/pypi-standalone-packaging` (off main `149d151`).

## Internal packages (uv workspace)

| Distribution (PyPI name) | Import name | Version | Publishable? | User-facing? |
|---|---|---|---|---|
| `ronin-cli` | `ronin_cli` | 1.0.0rc3 | yes (the binary) | yes — `ronin` / `ro` |
| `ronin-agent-patterns` | `ronin_agent_patterns` | 1.0.0rc3 | yes | yes (usable standalone) |
| `ronin-eval-suite` | `ronin_eval_suite` | 1.0.0rc3 | yes | yes |
| `ronin-hardening` | `ronin_hardening` | 1.0.0rc3 | yes | yes |
| `ronin-memory` | `ronin_memory` | 1.0.0rc3 | yes | yes |
| `ronin-mcp-servers` | `ronin_mcp_servers` | 1.0.0rc3 | yes | implementation |
| `ronin-relay` | `ronin_relay` | 1.0.0rc3 | yes | separate feature (NOT a cli dep) |

## Direction (what depends on what)

```
ronin-cli
├── ronin-agent-patterns      (core agent loop + provider layer)
├── ronin-eval-suite          (evals + SWE-bench harness)
├── ronin-hardening           (injection scanner, faithfulness, budget)
├── ronin-memory              (memory layers)
└── ronin-mcp-servers         (read-only MCP server templates)

ronin-relay        — standalone (phone→laptop relay); NOT required by ronin-cli
deployment-templates — not a distribution (templates on disk)
```

- **No circular dependencies.** `ronin-cli` is the only leaf that pulls the five libraries; the libraries do not import `ronin_cli`.
- **Dependency direction is one-way** (cli → libraries).

## Metadata reality (verified against the built wheel)

- `ronin-cli`'s `[project.dependencies]` declares the 5 internal deps as **PEP 440 version specs** (`ronin-agent-patterns==1.0.0rc3`, …), not paths.
- `[tool.uv.sources] { workspace = true }` is **dev-only** and is **stripped from the built wheel** — `Requires-Dist` in the wheel METADATA carries only the version specs. Confirmed: **no `file://` / `@ path` dependency in the artifact.**
- Optional extras (`browser`, `local`, `postgres`, `server`) are correctly gated with `extra == …` and stay optional.
- Both console scripts (`ronin`, `ro`) are present in `entry_points.txt`.
- No secret / `.env` / database / `.ronin/` state file is bundled in the wheel.

## Conclusion

The metadata is already **correct and publishable**. The standalone-install
"failure" observed in Phase 1 was solely that the five internal wheels are **not
yet on any index** — a bare `pip install ronin_cli.whl` cannot resolve them.
Providing the complete wheel set (or publishing the packages) resolves it. See
`pypi_packaging_decision.md`.
