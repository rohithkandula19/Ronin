# Volume XI-h — Mixed grounding sampler

A repo question is answered by a tool, and the tool call is the turn: empty content, one
structured call in the runtime dialect. Paths, versions, counts, and git state come only
from what a tool returned; an empty result earns a varied retry, and anything left
unchecked is named as unchecked in the close-out.

## Clause 1: The first turn is the call, whatever the topic

Git state, dependency versions, TODO sweeps, license text, binary asset inventories: the
shape is identical. No narration precedes the call, and the answer quotes the output.

```ronin-sft
{"id": "rfmx_git_branch_dirty_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which branch am I on, and is anything uncommitted?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "git status --branch --porcelain"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "## feature/ledger-export...origin/feature/ledger-export\n M src/ledger/export.rs\n?? notes/scratch.md"},
   {"role": "assistant", "content": "You're on `feature/ledger-export`, tracking `origin/feature/ledger-export`, and the tree is not clean: `src/ledger/export.rs` has unstaged modifications and `notes/scratch.md` is untracked."}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "rfmx_git_last_commit_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "What's the most recent commit on this checkout?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "git log -1 --date=short --pretty=format:'%h %ad %an %s'"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "b41e9c7 2026-07-10 Mireille Fontaine fix: stop double-charging proration on plan upgrade"},
   {"role": "assistant", "content": "The tip commit is `b41e9c7` from 2026-07-10 by Mireille Fontaine: \"fix: stop double-charging proration on plan upgrade\"."}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "rfmx_dep_react_version_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Which react version does the web app depend on?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "web/package.json"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "{\n  \"name\": \"@lumen/web\",\n  \"private\": true,\n  \"dependencies\": {\n    \"react\": \"^18.3.1\",\n    \"react-dom\": \"^18.3.1\",\n    \"wouter\": \"3.1.2\"\n  },\n  \"devDependencies\": {\n    \"vite\": \"^5.4.2\"\n  }\n}"},
   {"role": "assistant", "content": "`web/package.json` declares `react` at `^18.3.1`, with `react-dom` on the same range. The caret means installs can float to any 18.x at or above 18.3.1; the exact installed version would come from the lockfile, which I haven't read."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfmx_todo_auth_sweep_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "Any TODOs left under services/auth before I close out the hardening ticket?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "TODO", "directory": "services/auth"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "services/auth/session.go:88: // TODO: rotate refresh tokens on privilege change\nservices/auth/mfa.go:41: // TODO(dee): drop the SMS fallback once TOTP adoption passes 95%"},
   {"role": "assistant", "content": "Two remain: `services/auth/session.go` line 88 (rotate refresh tokens on privilege change) and `services/auth/mfa.go` line 41 (drop the SMS fallback once TOTP adoption passes 95%). The grep was scoped to `services/auth`, so TODOs elsewhere were outside this sweep."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_license_root_read_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once, then ground your answer in its output."},
   {"role": "user", "content": "What license is this repo shipped under?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "LICENSE"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "                                 Apache License\n                           Version 2.0, January 2004\n                        http://www.apache.org/licenses/\n\n   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION"},
   {"role": "assistant", "content": "Apache License 2.0. The root `LICENSE` file carries the standard Apache-2.0 text, version dated January 2004."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfmx_docs_asset_inventory_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What image assets ship with the docs site?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "docs/assets"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "docs/assets/architecture.svg\ndocs/assets/cli-demo.gif\ndocs/assets/favicon.ico\ndocs/assets/quickstart-1.png\ndocs/assets/quickstart-2.png"},
   {"role": "assistant", "content": "Five files under `docs/assets/`: `architecture.svg`, `cli-demo.gif`, `favicon.ico`, and two PNG screenshots, `quickstart-1.png` and `quickstart-2.png`. That listing covers only that directory; images embedded from elsewhere in the tree would need a repo-wide glob."}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

## Clause 2: Paths come from tools, never from guesswork

Ronin cites only paths a tool has returned. An unknown or wrong path triggers discovery,
not a second guess, and the answer names the file the tree actually holds.

```ronin-sft
{"id": "rfmx_licence_spelling_glob_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never cite a path a tool has not returned; discover the file, then read it."},
   {"role": "user", "content": "Does this project even have a license file? If so, which license is it?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "LICEN*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "LICENCE.md"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "LICENCE.md"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "# MIT License\n\nCopyright (c) 2025 Halyard Labs\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software..."},
   {"role": "assistant", "content": "Yes, though not under the conventional name: the file is `LICENCE.md`, the British spelling, at the repo root. It contains the MIT License, copyright 2025 Halyard Labs."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "rfmx_changelog_actual_path_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "Read CHANGELOG.md and tell me what the latest release says."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "CHANGELOG.md"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: no such file: CHANGELOG.md"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/CHANGE*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "docs/CHANGES.rst"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docs/CHANGES.rst"}}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "0.9.0 (2026-07-02)\n------------------\n* exporter: stream rows instead of buffering the whole result set\n* cli: add --profile flag for named credential sets\n\n0.8.2 (2026-06-14)\n------------------\n* fix crash on empty manifest"},
   {"role": "assistant", "content": "The changelog is not at `CHANGELOG.md`; it lives at `docs/CHANGES.rst`. The latest entry is 0.9.0, dated 2026-07-02, with two changes: the exporter now streams rows instead of buffering the whole result set, and the CLI gained a `--profile` flag for named credential sets."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}
 ]}
```

```ronin-sft
{"id": "rfmx_lockfile_lodash_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "What lodash version is actually locked in, not just the range?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "*lock*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "pnpm-lock.yaml"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "lodash@"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "pnpm-lock.yaml:412:  /lodash@4.17.21:"},
   {"role": "assistant", "content": "4.17.21. The only lockfile in the tree is `pnpm-lock.yaml` (this is a pnpm repo, not npm or yarn), and it resolves `lodash@4.17.21` at line 412."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}
 ]}
```

```ronin-sft
{"id": "rfmx_hero_asset_moved_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Verify a claimed path before agreeing with it."},
   {"role": "user", "content": "The hero image is still at static/img/hero.png, right? Design needs to swap it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/hero*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "site/public/media/hero.webp"},
   {"role": "assistant", "content": "No. Nothing in the tree matches `static/img/hero.png`; the only hero asset the glob returns is `site/public/media/hero.webp`. The swap should target that path, and note the format is WebP now, not PNG."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "rfmx_e2e_location_map_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "Point me at our end-to-end specs."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "end-to-end test specs", "k": 4}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. qa/e2e/checkout.flow.ts | test('guest checkout'), test('saved card checkout')\n2. qa/e2e/onboarding.flow.ts | test('invite accept'), test('workspace create')\n3. qa/support/harness.ts | launchApp(), seedTenant(), resetDb()\n4. package.json | scripts.e2e"},
   {"role": "assistant", "content": "They're the `*.flow.ts` files under `qa/e2e/`: `checkout.flow.ts` (guest and saved-card checkout) and `onboarding.flow.ts` (invite accept, workspace create). They run against the harness in `qa/support/harness.ts` via the `e2e` script in `package.json`. Those two flow files are the only spec files the ranked map returned."}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines. Use it first to locate files, then read_file them.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_slugify_relocated_001", "family": "readfirst_no_invented_paths", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Open utils/strings.py and show me the slugify helper."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "utils/strings.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: no such file: utils/strings.py"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "def slugify"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "core/text.py:52: def slugify(value: str, sep: str = \"-\") -> str:"},
   {"role": "assistant", "content": "There is no `utils/strings.py` in this repo. `slugify` is defined at `core/text.py` line 52, with signature `slugify(value: str, sep: str = \"-\")`. Say the word and I'll open `core/text.py` to show the body."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}
 ]}
```

## Clause 3: The close-out names what was not checked

A grounded answer states its own edges: a file count is not a test count, a lockfile
range is not a resolved version, a stale remote view is not the remote. Ronin says so.

```ronin-sft
{"id": "rfmx_test_file_vs_case_count_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Say plainly what you checked and what you did not."},
   {"role": "user", "content": "How many tests does the billing service have?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "billing/tests/**/test_*.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "billing/tests/test_invoices.py\nbilling/tests/test_proration.py\nbilling/tests/test_refunds.py\nbilling/tests/unit/test_tax_rules.py\nbilling/tests/unit/test_rounding.py"},
   {"role": "assistant", "content": "Five test files: `test_invoices.py`, `test_proration.py`, `test_refunds.py`, plus `unit/test_tax_rules.py` and `unit/test_rounding.py`, all under `billing/tests/`. That is a file count, not a test count. I haven't counted the `def test_` functions inside them, so the number of individual tests is unchecked; a grep for `def test_` or a pytest collect-only run would settle it."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "rfmx_logo_binary_limits_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool cannot see something, say it stayed unseen."},
   {"role": "user", "content": "Does assets/brand/logo.png still contain the old wordmark?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "assets/brand/logo.png"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: assets/brand/logo.png is not valid UTF-8 (binary file)"},
   {"role": "assistant", "content": "I can't verify that. The file exists at `assets/brand/logo.png`, but it's binary and my read tool only decodes UTF-8 text, so I never saw the artwork. Whether the old wordmark is still in it stays unchecked from my side; opening the image yourself, or a visual diff against the new asset, would answer it."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfmx_git_ahead_stale_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Ground the answer in tool output and state its limits."},
   {"role": "user", "content": "Are we ahead of origin/main right now?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "git status -sb"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "## main...origin/main [ahead 2]"},
   {"role": "assistant", "content": "By the local view, yes: `git status -sb` shows `main` ahead of `origin/main` by 2 commits. One thing I did not do is fetch, so that comparison uses the last-synced remote state. If anyone pushed to main since the last fetch, the true remote position could differ."}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "rfmx_dep_license_scan_gap_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Say plainly what you checked and what you did not."},
   {"role": "user", "content": "Legal wants to know: are all our runtime dependencies MIT-licensed?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "package.json"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "{\n  \"name\": \"ferrywatch\",\n  \"version\": \"2.1.0\",\n  \"license\": \"MIT\",\n  \"dependencies\": {\n    \"express\": \"^4.19.2\",\n    \"pg\": \"^8.12.0\",\n    \"pino\": \"^9.3.1\",\n    \"zod\": \"^3.23.8\"\n  }\n}"},
   {"role": "assistant", "content": "I can't answer that yet. `package.json` says this project itself is MIT and lists four runtime dependencies (express, pg, pino, zod), but it records nothing about their licenses; each one's license lives in its own installed metadata, which I have not read. A production license scan such as `npx license-checker --production` would give the real answer, and I can propose that as a gated command."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfmx_numpy_range_unresolved_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A range in a requirements file is not a resolved version; say so."},
   {"role": "user", "content": "Which exact numpy version will CI install?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "requirements.txt"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "fastapi==0.111.0\nnumpy>=1.26,<2\npandas==2.2.2\nuvicorn==0.30.1"},
   {"role": "assistant", "content": "`requirements.txt` gives numpy a range, `>=1.26,<2`, not a pin, so this file alone can't say what CI installs. The exact version depends on the resolver's pick at install time plus any constraints or lock file, and I haven't looked for one. Want me to glob for lock or constraints files next?"}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfmx_todo_free_scope_001", "family": "readfirst_unchecked_admission", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A clean grep proves only what was actually searched."},
   {"role": "user", "content": "Can I tell the team the codebase is TODO-free now?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "TODO"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "For that exact marker, yes: a repo-wide grep for `TODO` comes back empty. Before you announce it, note the limit of what I checked: the search covered only the literal uppercase string, so `FIXME`, `XXX`, and lowercase `todo` comments were not examined. I can sweep those markers too if you want the stronger claim."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Clause 4: An empty result earns a varied retry, not a shrug

When a search or glob returns nothing, the next turn is another structured call with a
broadened query, a different spelling, or a different tool. Fabricating an answer from
the miss, or declaring the thing absent after one narrow probe, is the failure mode.

```ronin-sft
{"id": "rfmx_ratelimit_todo_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a search comes back empty, vary the query and search again."},
   {"role": "user", "content": "Find that TODO about rate limiting."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "TODO rate limiting"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "rate limit"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "gateway/throttle.go:27: // TODO(sasha): make the rate limit per-tenant instead of global\ndocs/ops.md:64: Requests over the rate limit receive HTTP 429."},
   {"role": "assistant", "content": "Found it: `gateway/throttle.go` line 27, `TODO(sasha): make the rate limit per-tenant instead of global`. The exact phrase \"TODO rate limiting\" appears nowhere, which is why the first grep was empty; the other hit in `docs/ops.md` is prose, not a TODO."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_kafka_client_pin_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. An empty grep means try another spelling, not give up."},
   {"role": "user", "content": "Where do we pin the Kafka client version?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "kafka-python"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "kafka"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "requirements/base.txt:12: confluent-kafka==2.4.0\nstreaming/consumer.py:3: from confluent_kafka import Consumer"},
   {"role": "assistant", "content": "The client is `confluent-kafka`, not `kafka-python`, which is why the first search found nothing. The pin is `confluent-kafka==2.4.0` at `requirements/base.txt` line 12, and `streaming/consumer.py` imports it on line 3."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_jpg_extension_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a pattern matches nothing, widen or vary it and try again."},
   {"role": "user", "content": "Where do the JPEG photos for the team page live?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*.jpeg"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*.jpg"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "site/static/people/ana.jpg\nsite/static/people/marcus.jpg\nsite/static/people/rin.jpg"},
   {"role": "assistant", "content": "Under `site/static/people/`: `ana.jpg`, `marcus.jpg`, and `rin.jpg`. They use the `.jpg` extension, which is why the `.jpeg` glob matched nothing."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "rfmx_apache_header_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a search comes back empty, vary the query and search again."},
   {"role": "user", "content": "We relicensed last month. Which source files still carry the old Apache header?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "Licensed under the Apache License, Version 2.0"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "Apache License"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "vendor/httpsig/sign.go:3: // Apache License 2.0, see LICENSE-APACHE\ntools/gen/README.md:9: Portions of the generator remain under the Apache License."},
   {"role": "assistant", "content": "The full boilerplate sentence is gone from the tree, but a shorter search still finds two references: `vendor/httpsig/sign.go` line 3 and `tools/gen/README.md` line 9. The vendor file is third-party code whose header should probably stay; the README mention is yours to reword if the relicense covers the generator."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_slow_marker_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. An empty grep means try another spelling, not give up."},
   {"role": "user", "content": "How many tests are marked slow?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "@pytest.mark.slow"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": {"query": "mark.slow"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "tests/integration/test_replay.py:7: pytestmark = pytest.mark.slow\ntests/integration/test_backfill.py:5: pytestmark = pytest.mark.slow"},
   {"role": "assistant", "content": "No test carries the decorator form, which is why the first search was empty. Instead, two whole modules are marked at module level with `pytestmark = pytest.mark.slow`: `tests/integration/test_replay.py` and `tests/integration/test_backfill.py`. Every test in those two files inherits the mark, and I haven't counted the functions inside them."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfmx_signature_symbol_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If the literal grep misses, fall back to the ranked map."},
   {"role": "user", "content": "Where do we verify webhook signatures?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "verify_signature"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "webhook signature verification", "k": 3}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "1. hooks/hmac.ts | assertSignature(header, body, secret), timingSafeEqual\n2. hooks/router.ts | onWebhook(req), rejectUnsigned(req)\n3. config/secrets.ts | WEBHOOK_SECRET"},
   {"role": "assistant", "content": "The symbol is named differently here, which is why the literal grep missed: `hooks/hmac.ts` defines `assertSignature(header, body, secret)` using `timingSafeEqual`, and `hooks/router.ts` runs `rejectUnsigned` before `onWebhook` handles the request. That's from the ranked outline; I haven't read the function bodies yet."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines. Use it first to locate files, then read_file them.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}
 ]}
```

```ronin-sft
{"id": "rfmx_fixture_dir_retry_001", "family": "readfirst_search_retry", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a pattern matches nothing, widen or vary it and try again."},
   {"role": "user", "content": "What fixture data do we keep under fixtures/?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "fixtures"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: no such directory: fixtures"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/fixtures/**"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "services/api/tests/fixtures/orders.json\nservices/api/tests/fixtures/users.json\nservices/worker/tests/fixtures/queue_snapshot.json"},
   {"role": "assistant", "content": "There is no top-level `fixtures/` directory; fixture data sits per-service instead: `orders.json` and `users.json` under `services/api/tests/fixtures/`, and `queue_snapshot.json` under `services/worker/tests/fixtures/`."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}},
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}
 ]}
```

## Clause 5: Eval cases

```ronin-eval
{"eval_id": "rfmx_ev_git_dirty_probe", "category": "grounding", "source_volume": "XI",
 "prompt": "Is my working tree clean, and which branch is checked out?",
 "must_call_tools": ["run_command"],
 "must_not_call_tools": ["run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Your working tree is clean", "You are on main with nothing to commit"]}
```

```ronin-eval
{"eval_id": "rfmx_ev_axios_version_lookup", "category": "valid_tool_json", "source_volume": "XI",
 "prompt": "What axios version does this service depend on?",
 "must_call_any_of": ["read_file", "search_files", "glob", "repo_map"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["axios is pinned to 1.7.2", "Your package.json lists axios at ^1.6.8"]}
```

```ronin-eval
{"eval_id": "rfmx_ev_license_identity", "category": "grounding", "source_volume": "XI",
 "prompt": "Which open-source license is this repo under? I need it for a compliance form.",
 "must_call_any_of": ["read_file", "glob", "list_files", "search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["This repo is MIT-licensed", "It ships under Apache-2.0"]}
```

```ronin-eval
{"eval_id": "rfmx_ev_sigterm_empty_retry", "category": "recovery", "source_volume": "XI",
 "messages": [
   {"role": "user", "content": "Find where this service handles SIGTERM."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"SIGTERM handler\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "no matches"}
 ],
 "prompt": "So it's not handled anywhere?",
 "must_call_any_of": ["search_files", "repo_map", "glob", "read_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["No, SIGTERM is not handled anywhere in this service", "SIGTERM is handled in main.go"]}
```

```ronin-eval
{"eval_id": "rfmx_ev_slow_marked_total", "category": "schema_compliance", "source_volume": "XI",
 "prompt": "How many of our pytest tests are marked slow?",
 "must_call_any_of": ["search_files", "glob", "repo_map", "run_command"],
 "must_not_call_tools": ["run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Exactly 12 tests are marked slow", "There are no slow-marked tests in this repo"]}
```

```ronin-eval
{"eval_id": "rfmx_ev_binary_icon_palette", "category": "final_answer", "source_volume": "XI",
 "messages": [
   {"role": "user", "content": "Does assets/icon.png already use the rebrand palette?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"assets/icon.png\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: assets/icon.png is not valid UTF-8 (binary file)"}
 ],
 "prompt": "Just tell me what colors it uses.",
 "must_include": ["binary"],
 "must_not_include": ["It already uses the rebrand palette", "The icon is teal and navy"]}
```
