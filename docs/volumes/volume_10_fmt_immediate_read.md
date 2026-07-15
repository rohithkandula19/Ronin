# Volume X-a — Immediate calls: read tools

When a user's question is answerable by a read tool, Ronin's entire turn is the
structured tool call: empty content, one call, exact schema. Narrating the call,
quoting it as prose JSON, or wrapping it in markdown are all protocol failures;
the runtime can only execute a structured `tool_calls` entry.

## Clause 1 — A file question IS a read_file call

The user names or implies a specific file; the turn is one `read_file` with the
path and nothing else. No "let me open that for you" preamble.

```ronin-sft
{"id": "fmtir_read_dockerfile_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What base image does our Dockerfile use?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"Dockerfile\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtir_read_cargo_toml_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a question needs a read tool, call it at once, with no narration first."},
   {"role": "user", "content": "Open Cargo.toml, I need to check which Rust edition the crate targets."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"Cargo.toml\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtir_read_ci_workflow_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Answer file and repo questions by emitting the tool call directly."},
   {"role": "user", "content": "Show me the GitHub Actions workflow at .github/workflows/ci.yml."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \".github/workflows/ci.yml\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtir_read_go_router_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "What routes does internal/http/router.go register?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"internal/http/router.go\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 2 — "Grep for X" IS a search_files call

Literal-string hunts go straight to `search_files`, scoped with `directory` when
the user scoped the ask. Never shelled out, never described in prose.

```ronin-sft
{"id": "fmtir_search_fixme_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Grep the repo for FIXME comments."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"FIXME\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_search_env_var_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call right away instead of describing it."},
   {"role": "user", "content": "Which files reference STRIPE_WEBHOOK_SECRET?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"STRIPE_WEBHOOK_SECRET\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_search_scoped_symbol_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A read that a tool can do is a tool call, not narration."},
   {"role": "user", "content": "Find usages of parseInvoice, but only inside the services directory."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"parseInvoice\", \"directory\": \"services\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_search_css_class_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Answer file and repo questions by emitting the tool call directly."},
   {"role": "user", "content": "Where is the CSS class .btn-danger defined or used?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \".btn-danger\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Clause 3 — Pattern asks ARE glob calls

Extension and wildcard questions become one `glob` call with the pattern the
user implied.

```ronin-sft
{"id": "fmtir_glob_migrations_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "List all the SQL migration files under db/migrations."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"*.sql\", \"directory\": \"db/migrations\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtir_glob_proto_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Find every .proto file anywhere in this repo."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.proto\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtir_glob_go_tests_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a question needs a read tool, call it at once, with no narration first."},
   {"role": "user", "content": "Which Go test files do we have?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*_test.go\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtir_glob_vue_components_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call right away instead of describing it."},
   {"role": "user", "content": "Show the .vue single-file components under src/components."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"*.vue\", \"directory\": \"src/components\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

## Clause 4 — Directory questions ARE list_files calls

"What's in that folder" becomes one `list_files`, with `pattern` when the user
asked for a specific kind of file.

```ronin-sft
{"id": "fmtir_list_repo_root_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What's at the top level of this project?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \".\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtir_list_scripts_dir_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A read that a tool can do is a tool call, not narration."},
   {"role": "user", "content": "What scripts live in the scripts/ folder?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"scripts\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtir_list_k8s_manifests_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Answer file and repo questions by emitting the tool call directly."},
   {"role": "user", "content": "List the YAML manifests under deploy/k8s."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"deploy/k8s\", \"pattern\": \"*.yaml\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtir_list_notebooks_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Do we have any Jupyter notebooks in the analysis directory?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"analysis\", \"pattern\": \"*.ipynb\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

## Clause 5 — "Where is X handled?" in unfamiliar code IS a repo_map call

Concept-level location questions in a repo Ronin hasn't mapped go to `repo_map`
first: one ranked search, then read what it surfaces.

```ronin-sft
{"id": "fmtir_map_session_auth_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "I just cloned this repo. Where is login and session handling implemented?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"login session authentication handling\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_map_rate_limiting_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call right away instead of describing it."},
   {"role": "user", "content": "Where does this service enforce rate limiting? Give me the top 5 candidate files."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"rate limiting enforcement middleware\", \"k\": 5}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_map_payment_retries_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A read that a tool can do is a tool call, not narration."},
   {"role": "user", "content": "Which module owns the payment retry logic in this codebase?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"payment retry logic\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtir_map_config_loading_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a question needs a read tool, call it at once, with no narration first."},
   {"role": "user", "content": "How does this app load its configuration? I don't know the layout yet, so cast a wide net."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"where configuration is loaded\", \"k\": 8}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

## Clause 6 — Process questions ARE background_status / background_logs calls

Whether a background process is alive is `background_status`; what it printed is
`background_logs` with the process id. Neither ever needs prose first.

```ronin-sft
{"id": "fmtir_status_tailwind_watcher_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Did the tailwind watcher exit, or is it still going?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}]}
```

```ronin-sft
{"id": "fmtir_status_all_jobs_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Which background jobs are running right now?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}]}
```

```ronin-sft
{"id": "fmtir_logs_process_two_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Answer file and repo questions by emitting the tool call directly."},
   {"role": "user", "content": "Show me the latest output from background process 2."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 2}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtir_logs_jest_watcher_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call right away instead of describing it."},
   {"role": "user", "content": "The jest watcher is process 5. Pull its last 80 lines."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 5, \"lines\": 80}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtir_logs_stuck_webpack_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a question needs a read tool, call it at once, with no narration first."},
   {"role": "user", "content": "Process 1 is the webpack build and it seems stuck. Check its recent output for errors."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 1, \"lines\": 60}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

## Clause 7 — Evals: the correct first move is the call itself

```ronin-eval
{"eval_id": "fmtir_ev_read_file_direct", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Show me what's in src/middleware/auth.ts.",
 "must_call_tools": ["read_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Here's what src/middleware/auth.ts contains", "The middleware validates the JWT and then"]}
```

```ronin-eval
{"eval_id": "fmtir_ev_grep_is_search_files", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Find every occurrence of the string DEPRECATED_ENDPOINT in this repo.",
 "must_call_tools": ["search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I found the following occurrences", "It appears in these files:"]}
```

```ronin-eval
{"eval_id": "fmtir_ev_glob_spec_pattern", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Which files match tests/**/*.spec.js in this project?",
 "must_call_tools": ["glob"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The matching spec files are"]}
```

```ronin-eval
{"eval_id": "fmtir_ev_locate_concept_any_read", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Where does this codebase handle password hashing? You haven't explored the file layout yet.",
 "must_call_any_of": ["repo_map", "search_files", "glob", "list_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Password hashing is handled in"]}
```

```ronin-eval
{"eval_id": "fmtir_ev_background_status_check", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Is the vite dev server you started earlier still up?",
 "must_call_tools": ["background_status"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The dev server is still running fine", "Yes, it's up and serving requests"]}
```

```ronin-eval
{"eval_id": "fmtir_ev_background_logs_pull", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Background process 3 is the pytest watcher. Pull its most recent output.",
 "must_call_tools": ["background_logs"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The watcher's latest output shows all tests passing"]}
```
