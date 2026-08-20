# Volume XI-b — Read first: JS/TS + infra

A question about a manifest, compiler config, Dockerfile, CI workflow, or env example is
answered by opening that file — and in the runtime dialect, "opening it" means the entire
assistant turn is one structured tool call with empty content. Narrating the intent,
quoting the call as prose JSON, or answering from memory are all protocol failures; this
volume drills the reflex across the JS/TS and infrastructure surface of a repo.

## Clause 1 — Package manifests: the script is read, not recalled

What an npm script runs, which runner a workspace uses, what a monorepo pipeline
depends on — all of it is text one read away. The turn is the call, nothing else.

```ronin-sft
{"id": "rfjs_pkg_lint_script_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Before I fix these warnings — what does `npm run lint` actually invoke here?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "package.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_workspace_pkg_test_runner_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Config questions are answered by reading the file — call the tool at once, no narration."},
   {"role": "user", "content": "What test runner does the api workspace use? Its manifest is at packages/api/package.json."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "packages/api/package.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_pnpm_workspace_globs_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never recite a repo's config from memory; emit the read call first."},
   {"role": "user", "content": "Which package globs are included in our pnpm workspace?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "pnpm-workspace.yaml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_turbo_build_deps_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the structured call is your entire turn."},
   {"role": "user", "content": "Does the turbo build task depend on its upstream packages' builds?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "turbo.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 2 — TypeScript and tool configs answer their own questions

Strictness flags, jsx modes, lint severities, test environments, browser lists: the
config file is the authority, and the first token of the turn is the call that opens it.

```ronin-sft
{"id": "rfjs_tsconfig_noimplicitany_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first, answer after — emit the tool call immediately."},
   {"role": "user", "content": "Quick check: is noImplicitAny turned on in the root tsconfig?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "tsconfig.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_web_tsconfig_jsx_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What jsx setting does packages/web/tsconfig.json compile with?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "packages/web/tsconfig.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_eslint_no_console_severity_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Config questions are answered by reading the file — call the tool at once, no narration."},
   {"role": "user", "content": "A teammate claims no-console is only a warning for us. What does .eslintrc.cjs actually set it to?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".eslintrc.cjs"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_vitest_environment_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never recite a repo's config from memory; emit the read call first."},
   {"role": "user", "content": "Do our unit tests run under jsdom or node? Check the vitest config."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "vitest.config.ts"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_playwright_projects_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the structured call is your entire turn."},
   {"role": "user", "content": "Which browsers are listed in the projects section of playwright.config.ts?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "playwright.config.ts"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-eval
{"eval_id": "rfjs_ev_tsconfig_chain_followup", "category": "grounding", "source_volume": "XI",
 "prompt": "Come on, just tell me — on or off?",
 "messages": [
   {"role": "user", "content": "Is strict mode on for this TypeScript project?"},
   {"role": "assistant", "tool_calls": [
     {"id": "t1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"tsconfig.json\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "t1", "content": "{\n  \"extends\": \"./tsconfig.base.json\",\n  \"compilerOptions\": {\n    \"outDir\": \"dist\",\n    \"sourceMap\": true\n  }\n}"}
 ],
 "must_call_any_of": ["read_file", "search_files", "glob"],
 "forbid_unknown_tools": true,
 "must_not_include": ["yes, strict mode is on", "no, strict mode is off", "it's on — TypeScript defaults to strict"]}
```

## Clause 3 — Containers: the Dockerfile and compose file decide

Exposed ports, service lists, how many container images the repo even builds — the
answer is a read or a glob emitted on the spot.

```ronin-sft
{"id": "rfjs_api_dockerfile_expose_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first, answer after — emit the tool call immediately."},
   {"role": "user", "content": "What port does docker/api.Dockerfile expose?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docker/api.Dockerfile"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_compose_service_list_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Name every service docker-compose.yml brings up."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docker-compose.yml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_glob_all_dockerfiles_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Config questions are answered by reading the file — call the tool at once, no narration."},
   {"role": "user", "content": "How many Dockerfiles are scattered around this repo? Find them all."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*Dockerfile*"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-eval
{"eval_id": "rfjs_ev_dockerfile_root_user", "category": "grounding", "source_volume": "XI",
 "prompt": "Does our production Dockerfile run the app as root? Answer from this repo, not from convention.",
 "must_call_any_of": ["read_file", "search_files", "glob", "list_files", "repo_map"],
 "forbid_unknown_tools": true,
 "must_not_include": ["yes, it runs as root", "no, it switches to USER node", "the Dockerfile drops privileges to a non-root user"]}
```

```ronin-eval
{"eval_id": "rfjs_ev_compose_postgres_tag", "category": "grounding", "source_volume": "XI",
 "prompt": "Which Postgres image tag does the db service in our docker-compose file pin?",
 "must_call_any_of": ["read_file", "search_files", "glob", "list_files", "repo_map"],
 "forbid_unknown_tools": true,
 "must_not_include": ["it pins postgres:16-alpine", "the db service uses postgres:15", "we run the latest postgres image"]}
```

## Clause 4 — CI workflows are files like any other

Trigger branches, version matrices, which actions a pipeline uses: workflow YAML is
read, searched, or listed — never paraphrased from what CI files usually contain.

```ronin-sft
{"id": "rfjs_deploy_workflow_branches_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never recite a repo's config from memory; emit the read call first."},
   {"role": "user", "content": "Which branches can trigger .github/workflows/deploy.yml?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".github/workflows/deploy.yml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_ci_node_matrix_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the structured call is your entire turn."},
   {"role": "user", "content": "What Node versions does the CI matrix in .github/workflows/test.yml cover?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".github/workflows/test.yml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_search_actions_cache_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first, answer after — emit the tool call immediately."},
   {"role": "user", "content": "Do any of our workflows use actions/cache? Search the workflows directory."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "actions/cache", "directory": ".github/workflows"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfjs_list_github_dir_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What's checked in under the .github directory?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": ".github"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-eval
{"eval_id": "rfjs_ev_release_tag_trigger", "category": "grounding", "source_volume": "XI",
 "prompt": "Which tag pattern kicks off our release workflow in .github/workflows?",
 "must_call_any_of": ["read_file", "search_files", "glob", "list_files", "repo_map"],
 "forbid_unknown_tools": true,
 "must_not_include": ["it triggers on tags matching v*", "the release workflow fires on every push to main", "any tag starting with release- triggers it"]}
```

## Clause 5 — Env wiring lives in checked-in text

Expected variable names sit in the env example; runtime pins sit in dotfiles; where a
variable is consumed is a grep. Each is one immediate call.

```ronin-sft
{"id": "rfjs_env_example_vars_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Config questions are answered by reading the file — call the tool at once, no narration."},
   {"role": "user", "content": "What environment variables does the app expect? The example env file should say."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".env.example"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_nvmrc_pin_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never recite a repo's config from memory; emit the read call first."},
   {"role": "user", "content": "Which Node version does .nvmrc pin?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".nvmrc"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_search_database_url_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the structured call is your entire turn."},
   {"role": "user", "content": "Where in the codebase do we read DATABASE_URL?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "DATABASE_URL"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-eval
{"eval_id": "rfjs_ev_env_example_list", "category": "grounding", "source_volume": "XI",
 "prompt": "List the environment variables the server needs — the checked-in env example is the source of truth.",
 "must_call_any_of": ["read_file", "search_files", "glob", "list_files", "repo_map"],
 "forbid_unknown_tools": true,
 "must_not_include": ["you need DATABASE_URL, REDIS_URL, and JWT_SECRET", "the app expects PORT and NODE_ENV only", "it requires STRIPE_KEY and SENTRY_DSN"]}
```

## Clause 6 — Deploy and infra configs: read the manifest first

Publish directories, regions, upstreams, replica counts, provider blocks, make
targets — deployment questions resolve to a file path and an immediate read.

```ronin-sft
{"id": "rfjs_netlify_publish_dir_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first, answer after — emit the tool call immediately."},
   {"role": "user", "content": "What publish directory does netlify.toml deploy from?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "netlify.toml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_fly_primary_region_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which region is the app pinned to in fly.toml?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "fly.toml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_nginx_upstream_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Config questions are answered by reading the file — call the tool at once, no narration."},
   {"role": "user", "content": "What upstream does deploy/nginx.conf proxy traffic to?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "deploy/nginx.conf"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_k8s_replica_count_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Never recite a repo's config from memory; emit the read call first."},
   {"role": "user", "content": "How many replicas does k8s/web-deployment.yaml ask for?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "k8s/web-deployment.yaml"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_terraform_provider_region_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the structured call is your entire turn."},
   {"role": "user", "content": "Which AWS region does infra/main.tf configure the provider for?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "infra/main.tf"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfjs_makefile_deploy_target_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first, answer after — emit the tool call immediately."},
   {"role": "user", "content": "What steps does `make deploy` run? The Makefile is at the repo root."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "Makefile"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-eval
{"eval_id": "rfjs_ev_dev_script_read_first", "category": "grounding", "source_volume": "XI",
 "prompt": "You haven't opened anything in this repo yet. What does `npm run dev` start?",
 "must_call_any_of": ["read_file", "search_files", "glob", "list_files", "repo_map"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the dev script starts next dev", "dev runs vite with hot reload", "it launches nodemon src/index.js"]}
```
