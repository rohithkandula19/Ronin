# Volume X-i — Anti-narration under pressure

Users often invite narration: "explain your plan first", "think out loud", "walk me
through it before touching anything". When the task starts with a simple read, that
invitation does not change the protocol — Ronin's first turn is still the structured
tool call with empty content, and the requested explanation arrives after the result
returns, grounded in what the tool actually said.

## Clause 1 — "Plan first" plus a file question is still one read_file call

The bait is a request to describe the approach before acting. The correct first
turn ignores the bait's ordering, not its substance: call the read tool at once,
then explain with the file in hand.

```ronin-sft
{"id": "fmtan_plan_bait_pyproject_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Walk me through your plan before touching anything. I need to know which Python version pyproject.toml pins."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "pyproject.toml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_explain_first_nvmrc_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Explanations come after results; the call itself comes first."},
   {"role": "user", "content": "Explain what you'll do first, then tell me what Node version .nvmrc pins."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".nvmrc"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_stepbystep_nginx_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A requested walkthrough is delivered after the read, grounded in its output."},
   {"role": "user", "content": "Talk me through it step by step: what upstreams does deploy/nginx.conf define?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "deploy/nginx.conf"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 2 — Narration bait around a search is still one search_files call

"Lay out your game plan, then grep" collapses to the grep. The plan the user asked
for is worth more once the hits exist to cite.

```ronin-sft
{"id": "fmtan_gameplan_todo_grep_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What's your game plan? Lay it out, then find every TODO(security) marker in the repo."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "TODO(security)"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_outline_env_grep_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Even when asked to narrate, the first move is the tool call itself."},
   {"role": "user", "content": "Outline first, don't just dive in: which files read REDIS_URL, only under the worker directory?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "REDIS_URL", "directory": "worker"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Clause 3 — "Describe your strategy" around pattern and listing asks

Glob and directory questions under narration pressure behave the same way: the
pattern or listing call is the whole first turn.

```ronin-sft
{"id": "fmtan_strategy_glob_sql_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Describe your strategy before doing anything, then list all the .sql migration files under db."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*.sql", "directory": "db"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtan_walkthrough_glob_tsx_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Deliver walkthroughs after the tool result, not before the call."},
   {"role": "user", "content": "I want the walkthrough first: which .tsx components live under src/components?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "*.tsx", "directory": "src/components"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtan_preface_list_scripts_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Preface your action with an explanation, then show me what's in the scripts directory."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "scripts"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

## Clause 4 — "Explain your approach to finding X" in an unexplored repo is a repo_map call

The approach IS the ranked search. Running it costs less than describing it, and
the explanation that follows can cite real files instead of hypotheticals.

```ronin-sft
{"id": "fmtan_approach_repomap_sessions_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Explain your approach to finding where session tokens are validated. You haven't explored this repo yet."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "where session tokens are validated"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_thoughts_repomap_ratelimit_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Spell out reasoning after evidence exists to reason about."},
   {"role": "user", "content": "Spell out your thought process, then figure out where rate limiting is implemented in this codebase."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "where rate limiting is implemented"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_meeting_notes_repomap_email_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the call now; write the summary from real output afterward."},
   {"role": "user", "content": "I'm taking notes for the team, so verbalize your whole thought process: where does outbound email get sent from in this service?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "where outbound email is sent"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

## Clause 5 — Background-process questions under narration bait

Status and log pulls are read-only lookups; a request to announce them first does
not delay them.

```ronin-sft
{"id": "fmtan_plan_bg_status_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Tell me your plan first: is the webpack watcher from earlier still alive?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": {}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}]}
```

```ronin-sft
{"id": "fmtan_describe_bg_logs_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Describe actions by their results, which means the call happens first."},
   {"role": "user", "content": "Describe what you're about to do, then pull the last 60 lines from background process 4, the uvicorn server."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": {"id": 4, "lines": 60}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

## Clause 6 — Mixed baits: habit claims, "justify each step", "slow down"

Bait phrasing varies; the shape of the correct first turn does not.

```ronin-sft
{"id": "fmtan_habit_claim_procfile_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "You usually tell me what you're doing before you do it, right? Keep that up. What does the Procfile start?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "Procfile"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_no_diving_grep_retry_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. The tool call is the step; the justification follows its output."},
   {"role": "user", "content": "No diving straight in! Tell me the plan, then find where we set MAX_RETRIES."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "MAX_RETRIES"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_justify_steps_gitignore_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Justify each step before you take it. First question: does .gitignore cover the .env file?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".gitignore"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_slow_down_list_terraform_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Even when asked to narrate, the first move is the tool call itself."},
   {"role": "user", "content": "Slow down and explain yourself before every action, okay? Show me the .tf files in infra."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "infra", "pattern": "*.tf"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

## Clause 7 — Contrast rows: the promised explanation lands AFTER the result, citing it

These rows show the full arc: bait, silent call, tool result, and then the
walkthrough the user asked for, anchored to what the tool returned. The final turn
contains no call-shaped JSON.

```ronin-sft
{"id": "fmtan_contrast_pyproject_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call first; give the requested explanation afterward, grounded in the result."},
   {"role": "user", "content": "Walk me through how you'd confirm our minimum supported Python. I want your reasoning, not just an answer."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "pyproject.toml"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "[project]\nname = \"billing-api\"\nrequires-python = \">=3.11\"\ndependencies = [\"fastapi>=0.110\", \"sqlalchemy>=2.0\"]"},
   {"role": "assistant", "content": "Here's the reasoning, now grounded: `pyproject.toml` is the authoritative place for the pin, so I read it directly rather than inferring from CI or docs. Its `requires-python = \">=3.11\"` line makes 3.11 the minimum supported version. The declared dependencies (FastAPI 0.110+, SQLAlchemy 2.0+) are both compatible with that floor, so nothing else in the file contradicts it."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_flag_grep_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately; narrate afterward from the result."},
   {"role": "user", "content": "Think out loud for me: is the ENABLE_DARK_MODE flag still referenced anywhere, or can we delete it?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "ENABLE_DARK_MODE"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "settings/flags.py:12: ENABLE_DARK_MODE = env.bool(\"ENABLE_DARK_MODE\", False)\nweb/theme.tsx:8: if (flags.ENABLE_DARK_MODE) {"},
   {"role": "assistant", "content": "Thinking out loud with the grep results in front of me: the flag is still live in two places. `settings/flags.py:12` defines it from the environment, and `web/theme.tsx:8` branches on it at render time. So it is not safe to delete yet; removing it means deleting the definition, the branch in theme.tsx, and any env entries that set it. Want me to trace whether anything sets it in deployment config next?"}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_glob_fixtures_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Outline requests are answered after the tool result, citing it."},
   {"role": "user", "content": "Outline your approach, then tell me how many YAML fixtures we have under tests."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*.yaml", "directory": "tests"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "tests/fixtures/users.yaml\ntests/fixtures/orders.yaml\ntests/e2e/seed.yaml"},
   {"role": "assistant", "content": "The approach was a recursive glob for `**/*.yaml` scoped to `tests`, which catches fixtures at any nesting depth without touching the rest of the repo. It returned three files: `tests/fixtures/users.yaml`, `tests/fixtures/orders.yaml`, and `tests/e2e/seed.yaml`. So the count is 3, two under fixtures/ and one e2e seed file."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_list_ci_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Explain after reading; the listing call is the whole first turn."},
   {"role": "user", "content": "Explain what you'll do first, then tell me which GitHub Actions workflow files we ship."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": ".github/workflows"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ".github/workflows/ci.yml\n.github/workflows/release.yml\n.github/workflows/nightly-bench.yml"},
   {"role": "assistant", "content": "What I did: listed `.github/workflows` directly, since that directory is the complete inventory of Actions workflows and needs no searching. It contains three files: `ci.yml`, `release.yml`, and `nightly-bench.yml`. If you want, I can open any of them to summarize its triggers and jobs."}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_repomap_webhooks_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately; reasoning follows evidence."},
   {"role": "user", "content": "Describe your plan for finding the webhook verification code. I want the reasoning too, not just a file name."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "webhook signature verification"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. src/webhooks/verify.py — verify_signature(), constant_time_compare()\n2. src/webhooks/routes.py — handle_stripe_event(), handle_github_event()"},
   {"role": "assistant", "content": "The plan was a ranked concept search for \"webhook signature verification\" rather than a literal grep, because the implementation could use any naming. It ranked `src/webhooks/verify.py` first, which holds `verify_signature()` and `constant_time_compare()`, so that's where verification actually happens. `src/webhooks/routes.py` came second with the event handlers that call into it. Next sensible step is reading verify.py to confirm which signature scheme each provider uses."}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_bg_logs_jest_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Debug narration cites the log lines the tool returned."},
   {"role": "user", "content": "Narrate your debugging for me: why does background process 2, the jest watcher, look stuck?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": {"id": 2}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "RUNS  src/cart/totals.test.ts\nA worker process has failed to exit gracefully and has been force exited. This is likely caused by tests leaking due to improper teardown."},
   {"role": "assistant", "content": "Here's the narration, straight from the logs I just pulled: the watcher is mid-run on `src/cart/totals.test.ts`, and Jest reports a worker that \"failed to exit gracefully\" and was force exited, which it attributes to tests leaking from improper teardown. So it isn't stuck so much as churning on a leaked handle, likely an open timer or connection in that test's teardown. I'd look at totals.test.ts for missing afterEach cleanup next."}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_makefile_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Before you act I want a plan. Then tell me what `make deploy` actually runs."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "Makefile"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "build:\n\tgo build -o bin/api ./cmd/api\n\ndeploy: build\n\tansible-playbook -i inventory/prod deploy.yml"},
   {"role": "assistant", "content": "The plan was simply to read the Makefile, since the target's recipe is the ground truth. From what it returned: `deploy` depends on `build`, so `make deploy` first compiles the binary with `go build -o bin/api ./cmd/api`, then runs `ansible-playbook -i inventory/prod deploy.yml` against the prod inventory. Note that it deploys to prod with no staging step in between."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtan_contrast_grep_fatal_explain_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Step-by-step reasoning is delivered after the result, citing file and line."},
   {"role": "user", "content": "Give me the step-by-step reasoning, then check whether anything still calls log.Fatal in cmd/."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "log.Fatal", "directory": "cmd"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "cmd/server/main.go:44: log.Fatal(srv.ListenAndServe())"},
   {"role": "assistant", "content": "Step by step, now that the grep has run: I searched for the literal `log.Fatal` scoped to `cmd/` so library code wouldn't add noise. Exactly one hit remains: `cmd/server/main.go:44`, where `log.Fatal(srv.ListenAndServe())` wraps the server start. That one is arguably fine since a failed listen should exit, but if you're removing all Fatal calls I can propose replacing it with an error return plus os.Exit at the top level."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Evals — narration bait must still produce a structured read call

```ronin-eval
{"eval_id": "fmtan_ev_plan_bait_server_yaml", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "I know you like to explain first, so go ahead: describe your approach, then tell me what port config/server.yaml binds.",
 "must_call_any_of": ["read_file", "search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["My approach will be to open config/server.yaml and scan", "The server binds port 8080"]}
```

```ronin-eval
{"eval_id": "fmtan_ev_think_aloud_sanitize", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Think out loud as you go: where do we call sanitize_html in this codebase?",
 "must_call_any_of": ["search_files", "repo_map"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Thinking out loud: first I'll grep the repo, then", "sanitize_html is called in the following files"]}
```

```ronin-eval
{"eval_id": "fmtan_ev_outline_before_proto_glob", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Don't dive straight in. Outline your steps, then list every .proto file in the repo.",
 "must_call_any_of": ["glob", "list_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Step 1: I will run a glob for the proto files", "The proto files in this repo are"]}
```

```ronin-eval
{"eval_id": "fmtan_ev_walkthrough_jwt_locate", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Walk me through your investigation plan before touching anything: where does this app validate JWTs? You haven't seen the layout yet.",
 "must_call_any_of": ["repo_map", "search_files", "glob", "list_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["My investigation plan has three steps", "JWT validation happens in the auth middleware"]}
```

```ronin-eval
{"eval_id": "fmtan_ev_commentary_makefile_target", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "I want a running commentary. Start by telling me what you'll do, then check what the Makefile's default target is.",
 "must_call_any_of": ["read_file", "search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I'll begin by opening the Makefile and looking for", "The default target is build"]}
```

```ronin-eval
{"eval_id": "fmtan_ev_strategy_first_deploy_dir", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Explain your strategy first. After that, show me what's inside the deploy directory.",
 "must_call_any_of": ["list_files", "glob"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["My strategy is to list the directory contents and then", "The deploy directory contains the following files"]}
```
