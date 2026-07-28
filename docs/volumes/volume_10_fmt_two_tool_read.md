# Volume X-e — Two-call sequences: read then read

A locate call is only half a step: its result names a file, and the very next turn
consumes that name as the argument of a read. These drills train the chain — call one
with empty content, take the path out of the tool result verbatim, call two with empty
content — with no narration, no quoted JSON, and no restating of the plan in between.

## Clause 1 — glob finds the file; the next call reads exactly that path

The pattern match returns paths, not answers. Whatever `glob` surfaces is the literal
`path` argument of the `read_file` that follows, chosen by the user's actual question
when there is more than one hit.

```ronin-sft
{"id": "fmt2r_glob_dockerfile_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which base image does this service build from?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/Dockerfile\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "deploy/Dockerfile"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"deploy/Dockerfile\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_cargo_toml_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; do not narrate a call you can just make."},
   {"role": "user", "content": "List the dependencies of this Rust crate."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/Cargo.toml\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "backend/Cargo.toml"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"backend/Cargo.toml\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_newest_migration_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Open the most recent SQL migration."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"migrations/*.sql\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "migrations/0001_init.sql\nmigrations/0002_add_users.sql\nmigrations/0007_add_indexes.sql"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"migrations/0007_add_indexes.sql\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_svelte_config_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them."},
   {"role": "user", "content": "What adapter is configured for this SvelteKit app?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"svelte.config.*\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "svelte.config.js"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"svelte.config.js\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_proto_orders_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Show me the gRPC service definition for orders."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.proto\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "api/proto/orders.proto"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"api/proto/orders.proto\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_helm_values_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; skip the running commentary."},
   {"role": "user", "content": "What replica count does the web chart default to?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"charts/**/values.yaml\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "charts/webapp/values.yaml"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"charts/webapp/values.yaml\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_gemfile_rails_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Which Rails version does this app pin?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/Gemfile\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Gemfile"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"Gemfile\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_glob_ci_workflow_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them."},
   {"role": "user", "content": "Open the GitHub Actions workflow that runs the test suite."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \".github/workflows/*.yml\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ".github/workflows/ci.yml\n.github/workflows/release.yml"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \".github/workflows/ci.yml\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 2 — repo_map ranks; read the top hit verbatim

`repo_map` returns a ranked list with symbol outlines. The outline is a hint, not an
answer: the follow-up is a `read_file` of the highest-ranked path, copied exactly as
the tool returned it.

```ronin-sft
{"id": "fmt2r_map_session_auth_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "How is session authentication handled in this codebase?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"session authentication\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. src/auth/session.ts — createSession(), validateSession(), destroySession()\n2. src/auth/cookies.ts — signCookie()\n3. src/middleware/guard.ts — requireAuth()"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/auth/session.ts\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_map_ratelimit_go_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; do not narrate a call you can just make."},
   {"role": "user", "content": "Where does request rate limiting live, and what does it actually do?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"rate limiting middleware\", \"k\": 5}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. internal/middleware/ratelimit.go — NewLimiter(), Allow(), cleanup()\n2. internal/middleware/chain.go — Wrap()\n3. cmd/server/main.go — main()"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"internal/middleware/ratelimit.go\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_map_stripe_webhooks_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Find where Stripe webhook events get processed."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"stripe webhook handler\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. app/services/stripe_webhooks.rb — handle_event, verify_signature\n2. app/controllers/webhooks_controller.rb — create\n3. config/routes.rb"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"app/services/stripe_webhooks.rb\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_map_config_loader_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them."},
   {"role": "user", "content": "Where is application configuration loaded from disk?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"configuration loading\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. config/loader.py — load_config(), _merge_env()\n2. config/schema.py — ConfigSchema\n3. app/bootstrap.py — init_app()"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"config/loader.py\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_map_scheduler_elixir_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which module implements the background job scheduler?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"background job scheduler\", \"k\": 3}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. lib/scheduler/core.ex — schedule/2, run_due/0, cancel/1\n2. lib/scheduler/store.ex — persist/1\n3. lib/app/application.ex — start/2"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"lib/scheduler/core.ex\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_map_cache_eviction_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; skip the running commentary."},
   {"role": "user", "content": "How does the response cache decide what to evict?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"response cache eviction\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. pkg/cache/lru.go — Get(), Put(), evictOldest()\n2. pkg/cache/metrics.go — recordHit()\n3. pkg/handlers/api.go — ServeHTTP()"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"pkg/cache/lru.go\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 3 — search_files pins the line; read that file

A grep hit is `file:line:text`. The file portion of the hit becomes the `path` of the
next call, unmodified — no guessed sibling files, no directory-level detours.

```ronin-sft
{"id": "fmt2r_search_fixme_backoff_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Someone left a FIXME about retry backoff. Find it and open the file."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"FIXME: backoff\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "src/net/retry.rs:42: // FIXME: backoff should be jittered"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/net/retry.rs\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_database_url_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; do not narrate a call you can just make."},
   {"role": "user", "content": "Which file reads DATABASE_URL, and how is it parsed?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"DATABASE_URL\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "server/db.js:12: const url = process.env.DATABASE_URL;"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"server/db.js\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_legacy_render_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Track down where legacy_render is still called."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"legacy_render(\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "templates/engine.php:88: return legacy_render($view, $ctx);"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"templates/engine.php\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_feature_flag_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them."},
   {"role": "user", "content": "Where is the new_checkout feature flag checked in the Android app?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"new_checkout\", \"directory\": \"app\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "app/src/main/kotlin/Flags.kt:31: if (flags.isEnabled(\"new_checkout\")) {"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"app/src/main/kotlin/Flags.kt\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_quota_error_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Find the code that raises QuotaExceededError and show me its context."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"raise QuotaExceededError\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "core/quota.py:57:         raise QuotaExceededError(tenant_id)"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"core/quota.py\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_css_token_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; skip the running commentary."},
   {"role": "user", "content": "Which stylesheet defines --brand-accent? Open it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"--brand-accent\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "assets/css/theme.css:4:   --brand-accent: #0f766e;"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"assets/css/theme.css\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_search_swift_version_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Locate where APP_VERSION is defined and open that file."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"let APP_VERSION\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Sources/App/Version.swift:3: let APP_VERSION = \"2.4.1\""},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"Sources/App/Version.swift\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 4 — Listings and file-to-file references chain the same way

`list_files` output and paths referenced inside a file are both tool-observed facts.
The second read targets the exact path the first result surfaced, whether it came
from a directory listing or from the body of a config file.

```ronin-sft
{"id": "fmt2r_list_deploy_script_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What does the deploy script in scripts/ actually do?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"scripts\", \"pattern\": \"*.sh\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "scripts/deploy.sh\nscripts/rollback.sh\nscripts/seed_db.sh"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"scripts/deploy.sh\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_list_terraform_entry_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; do not narrate a call you can just make."},
   {"role": "user", "content": "Open the Terraform entrypoint under infra/."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"infra\", \"pattern\": \"*.tf\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "infra/main.tf\ninfra/variables.tf\ninfra/outputs.tf"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"infra/main.tf\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_read_tsconfig_extends_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble."},
   {"role": "user", "content": "Open tsconfig.json and follow its extends chain to the base config."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"tsconfig.json\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "{\n  \"extends\": \"./tsconfig.base.json\",\n  \"compilerOptions\": {\n    \"outDir\": \"dist\"\n  }\n}"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"tsconfig.base.json\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2r_read_compose_worker_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them."},
   {"role": "user", "content": "docker-compose.yml points the worker at a custom Dockerfile. Open both, compose file first."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"docker-compose.yml\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "services:\n  worker:\n    build:\n      context: .\n      dockerfile: docker/worker.Dockerfile\n    restart: unless-stopped"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"docker/worker.Dockerfile\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 5 — Eval probes: after result one, the only valid move is call two

Each probe replays a conversation that ends on the first tool result. The correct
continuation is a well-formed `read_file` call whose path comes from that result —
not a summary, not a quoted call, not an invented answer about a file never opened.

```ronin-eval
{"eval_id": "fmt2r_ev_glob_gradle", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "messages": [
   {"role": "user", "content": "Find the Gradle build file for the app module and open it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "g1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.gradle\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "g1", "content": "app/build.gradle\nsettings.gradle"}
 ],
 "prompt": "Continue.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["I've opened app/build.gradle and it declares"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmt2r_ev_map_mailer", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Act through tool calls; do not narrate a call you can just make.",
 "messages": [
   {"role": "user", "content": "How does outbound email get sent in this service?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "m1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"outbound email sending\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "m1", "content": "1. src/Mail/Sender.cs — SendAsync(), BuildMessage()\n2. src/Mail/Templates.cs — Render()\n3. src/Jobs/DigestJob.cs — Run()"}
 ],
 "prompt": "Go on.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["I have already read Sender.cs"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmt2r_ev_search_retries", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no preamble.",
 "messages": [
   {"role": "user", "content": "Find where MAX_RETRIES is set and show me the retry logic around it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "s1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"MAX_RETRIES\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "s1", "content": "worker/consumer.py:9: MAX_RETRIES = 5"}
 ],
 "prompt": "Proceed.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["I've reviewed the retry logic in worker/consumer.py and it"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmt2r_ev_list_ansible", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Emit tool calls immediately when the task needs them.",
 "messages": [
   {"role": "user", "content": "Open the main Ansible playbook under ansible/."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "l1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"ansible\", \"pattern\": \"*.yml\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "l1", "content": "ansible/site.yml\nansible/deploy.yml"}
 ],
 "prompt": "Keep going.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["site.yml is now open and it provisions"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmt2r_ev_glob_csproj", "category": "grounding", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "messages": [
   {"role": "user", "content": "Open the project file for the API service so we can check its target framework."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "p1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"services/**/*.csproj\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "p1", "content": "services/Api/Api.csproj\nservices/Worker/Worker.csproj"}
 ],
 "prompt": "Next step.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["The API project targets net8.0"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmt2r_ev_read_include_ini", "category": "grounding", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Act through tool calls; skip the running commentary.",
 "messages": [
   {"role": "user", "content": "config/app.ini pulls in an overrides file. Follow the include and open that file too."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "i1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"config/app.ini\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "i1", "content": "[server]\nport = 8080\n\n[include]\npath = config/overrides.ini"}
 ],
 "prompt": "Carry on.",
 "must_call_tools": ["read_file"],
 "must_not_include": ["I've applied the overrides from config/overrides.ini"],
 "forbid_unknown_tools": true}
```
