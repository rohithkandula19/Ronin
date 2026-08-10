# `.ronin/` — the project's committed agent config

Three files here are **committed on purpose**, and everything else in this directory is
ignored. `.gitignore` does that with `.ronin/*` plus an explicit allowlist rather than a
blanket `.ronin/`, because the same directory holds both the config a repo *wants* to
share and the session transcripts, caches and cost ledger it must never publish.

| file | what it decides | committed |
|---|---|---|
| `settings.json` | permissions, mode, protected branches | ✅ shared with everyone who clones |
| `mcp.json` | which MCP servers exist and how far they are trusted | ✅ |
| `hooks.json` | shell commands to run around tool calls | ✅ |
| `settings.local.json` | your personal overrides | ❌ ignored |
| `sessions/`, `cache/`, `checkpoints/` | transcripts, repo map, ledger | ❌ ignored |

The three committed files here are working examples that load without errors — verified,
not assumed. Treat them as a starting point and edit in place.

## `settings.json`

Three layers merge, least to most specific: built-in defaults, then
`~/.ronin/settings.json` (you, everywhere), then `./.ronin/settings.json` (this project,
committed), then `./.ronin/settings.local.json` (you, here, uncommitted). Later layers
win, and `ronin2 doctor` prints which file each effective rule came from — a permission
you cannot trace is a permission you cannot revoke.

**Unknown keys are errors, not ignored.** A silently dropped typo is a permission you
believe you granted. JSON has no comments, which is why this schema lives here rather
than inside the file:

| key | values |
|---|---|
| `mode` | `plan` \| `ask` \| `auto_edit` \| `full` — a permissiveness ladder; `plan` mutates nothing |
| `sandbox` | bool |
| `yolo` | bool |
| `protected_branches` | list of names (default `main`, `master`, `trunk`) |
| `default_decision` | `allow` \| `ask` \| `deny` |
| `taint_min_span` | int |
| `rules` | list, see below |

A rule is `{"tool": ..., "decision": ...}` plus one matcher. The long form is
`"match": {"kind": "regex", "pattern": "..."}`; `command`, `path` and `exact` are
shorthands for the common kinds:

```json
{"tool": "bash", "decision": "allow", "command": "^pytest"}
{"tool": "read_file", "decision": "deny", "path": "**/.env*"}
```

One malformed layer does not take the session down: the bad layer is skipped, the error
is reported by name, and the rest still load. A JSON typo must not become an outage,
because an outage is what makes someone reach for `--yolo`.

## `mcp.json`

`danger_level` and `requires_approval` are the two fields worth deliberating over. The
shipped example declares a read-only filesystem server that needs no approval; a server
that can write or deploy should say so, because a server contributing tools nobody vetted
is indistinguishable from the model choosing not to use them once a session is long.

`env` values interpolate `${VAR}` from the environment, so a token is named here and
lives in `.env` — never written into this file.

## `hooks.json`

Events are `PreToolUse`, `PostToolUse` and `SessionStart`. `matcher` is a
`|`-separated list of globs matched against the tool name, defaulting to `*`, and it is
only meaningful for the two tool events. The event JSON arrives on stdin as
`{"event": ..., "tool_name": ..., "arguments": {...}}` with keys sorted, so a hook's
behaviour is reproducible.

`block_on_timeout` decides whether a hook that hangs stops the tool call or is merely
reported. The shipped examples leave it false; a hook that guards something real —
"never touch migrations" — should set it true, since a guard that fails open is not a
guard.

The `lint-after-edit` example needs `jq`. It is an example, not a dependency.
