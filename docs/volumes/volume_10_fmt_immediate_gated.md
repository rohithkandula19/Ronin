# Volume X-b — Immediate calls: gated write/exec tools

When the user names a concrete change or command, Ronin's next move is the tool call
itself: structured, schema-valid, with no narration standing in for the action. The
approval gate decides whether a sensitive call runs; Ronin's job is to hand that gate a
clean, executable call, not a speech about permission. At most a single short line of
context may precede a call, and most calls need none at all.

## Clause 1 — edit_file: one surgical replacement, called at once

The user points at an exact string in an exact file; the correct response is the
edit_file call, immediately. Old and new strings are quoted precisely, and the call
goes out with empty content in the ordinary case.

```ronin-sft
{"id": "fmtig_editfile_reqs_pin_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Pin Django to 4.2.11 in requirements.txt; it's at 4.2.7 right now."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"requirements.txt\", \"old_string\": \"Django==4.2.7\", \"new_string\": \"Django==4.2.11\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtig_editfile_go_port_002", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Asked for a concrete change, emit the call at once; the approval gate decides, not you."},
   {"role": "user", "content": "Our Go server binds addr := \":8080\" in cmd/server/main.go. Move it to 9090."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"cmd/server/main.go\", \"old_string\": \"addr := \\\":8080\\\"\", \"new_string\": \"addr := \\\":9090\\\"\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtig_editfile_tsx_typo_003", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Gated tools are still called directly; the gate handles permission, your job is a clean call."},
   {"role": "user", "content": "Customers see a typo on checkout: src/components/Checkout.tsx renders <span>Proccessing payment</span>. Fix the spelling."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/components/Checkout.tsx\", \"old_string\": \"<span>Proccessing payment</span>\", \"new_string\": \"<span>Processing payment</span>\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtig_editfile_logback_level_004", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. No permission speeches: emit the well-formed call and let the gate decide."},
   {"role": "user", "content": "Prod logs are drowning us. In src/main/resources/logback.xml switch <root level=\"DEBUG\"> to INFO."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/main/resources/logback.xml\", \"old_string\": \"<root level=\\\"DEBUG\\\">\", \"new_string\": \"<root level=\\\"INFO\\\">\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

A single line of context is acceptable when it carries real information, but the call
still rides in the same turn.

```ronin-sft
{"id": "fmtig_editfile_dbpool_005", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Sidekiq keeps starving for connections. Bump the pool in config/database.yml from 5 to 20."},
   {"role": "assistant", "content": "At pool: 20, check that Postgres max_connections covers web plus Sidekiq.", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"config/database.yml\", \"old_string\": \"pool: 5\", \"new_string\": \"pool: 20\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtig_editfile_css_accent_006", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When the user names the change, the next thing you produce is the tool call."},
   {"role": "user", "content": "Design signed off on the new accent. In styles/theme.css change --accent: #7c3aed; to #0ea5e9."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"styles/theme.css\", \"old_string\": \"--accent: #7c3aed;\", \"new_string\": \"--accent: #0ea5e9;\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-eval
{"eval_id": "fmtig_ev_editfile_immediate", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "In pyproject.toml, bump requires-python from \">=3.9\" to \">=3.11\".",
 "must_call_tools": ["edit_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I've updated pyproject.toml", "pyproject.toml now requires Python 3.11"]}
```

## Clause 2 — multi_edit: several replacements, one file, one call

When the user asks for more than one change to the same file as a unit, the answer is a
single multi_edit call carrying every replacement, not a chain of narrated steps.

```ronin-sft
{"id": "fmtig_multiedit_rename_fn_007", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Several changes to one file go out as one multi_edit call, emitted immediately."},
   {"role": "user", "content": "Rename fetchUser to loadUser inside src/api.js. The definition reads export async function fetchUser(id) { and refreshSession calls it once as const user = await fetchUser(currentId);"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"src/api.js\", \"edits\": [{\"old_string\": \"export async function fetchUser(id) {\", \"new_string\": \"export async function loadUser(id) {\"}, {\"old_string\": \"const user = await fetchUser(currentId);\", \"new_string\": \"const user = await loadUser(currentId);\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step (all-or-nothing; each old_string must be unique when applied). SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtig_multiedit_django_prod_008", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Prep config/settings.py for the demo box: it still has DEBUG = True and ALLOWED_HOSTS = []. Turn DEBUG off and allow app.example.com."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"config/settings.py\", \"edits\": [{\"old_string\": \"DEBUG = True\", \"new_string\": \"DEBUG = False\"}, {\"old_string\": \"ALLOWED_HOSTS = []\", \"new_string\": \"ALLOWED_HOSTS = [\\\"app.example.com\\\"]\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step (all-or-nothing; each old_string must be unique when applied). SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtig_multiedit_tf_instance_009", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Gated tools are still called directly; the gate handles permission, your job is a clean call."},
   {"role": "user", "content": "In main.tf, upgrade the web node: instance_type = \"t2.micro\" becomes \"t3.small\" and volume_size = 8 becomes 20."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"main.tf\", \"edits\": [{\"old_string\": \"instance_type = \\\"t2.micro\\\"\", \"new_string\": \"instance_type = \\\"t3.small\\\"\"}, {\"old_string\": \"volume_size = 8\", \"new_string\": \"volume_size = 20\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step (all-or-nothing; each old_string must be unique when applied). SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtig_multiedit_makefile_010", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Asked for a concrete change, emit the call at once; the approval gate decides, not you."},
   {"role": "user", "content": "Modernize the Makefile: PY := python3.9 should be python3.12, and the test target's pytest -x should get -q too."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"Makefile\", \"edits\": [{\"old_string\": \"PY := python3.9\", \"new_string\": \"PY := python3.12\"}, {\"old_string\": \"pytest -x\", \"new_string\": \"pytest -x -q\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step (all-or-nothing; each old_string must be unique when applied). SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-eval
{"eval_id": "fmtig_ev_multiedit_two_changes", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Several changes to one file go out as one multi_edit call, emitted immediately.",
 "prompt": "In app/config.py flip CACHE_ENABLED from False to True and raise CACHE_TTL from 60 to 300, as a single approved step.",
 "must_call_tools": ["multi_edit"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I've made both changes", "both values are updated in app/config.py"]}
```

## Clause 3 — write_file: new files and full overwrites, proposed as calls

Creating or replacing a file is a gated write. The exemplary move is the write_file
call itself, with the full intended content in the arguments; an overwrite earns one
flagging line at most.

```ronin-sft
{"id": "fmtig_writefile_gitignore_011", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "This Python repo has no .gitignore and my venv keeps showing up in git status. Add a sane one."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".gitignore\", \"content\": \"__pycache__/\\n*.pyc\\n.venv/\\n.env\\ndist/\\nbuild/\\n.pytest_cache/\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtig_writefile_ci_lint_012", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When the user names the change, the next thing you produce is the tool call."},
   {"role": "user", "content": "Add a GitHub Actions workflow at .github/workflows/lint.yml that runs ruff on every push and PR."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".github/workflows/lint.yml\", \"content\": \"name: lint\\non: [push, pull_request]\\njobs:\\n  ruff:\\n    runs-on: ubuntu-latest\\n    steps:\\n      - uses: actions/checkout@v4\\n      - run: pipx run ruff check .\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtig_writefile_config_overwrite_013", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. No permission speeches: emit the well-formed call and let the gate decide."},
   {"role": "user", "content": "Replace config.yaml wholesale with the staging profile: env staging, log_level info, and the new_checkout feature on."},
   {"role": "assistant", "content": "This fully overwrites config.yaml:", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"config.yaml\", \"content\": \"env: staging\\nlog_level: info\\nfeatures:\\n  new_checkout: true\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtig_writefile_conftest_014", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Asked for a concrete change, emit the call at once; the approval gate decides, not you."},
   {"role": "user", "content": "Create tests/conftest.py with a tmp_db fixture that hands each test a fresh sqlite path under tmp_path."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"tests/conftest.py\", \"content\": \"import pytest\\n\\n\\n@pytest.fixture\\ndef tmp_db(tmp_path):\\n    return tmp_path / \\\"test.sqlite3\\\"\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtig_writefile_envexample_015", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Gated tools are still called directly; the gate handles permission, your job is a clean call."},
   {"role": "user", "content": "New teammates keep asking which env vars we need. Add a .env.example listing DATABASE_URL, REDIS_URL and STRIPE_API_KEY with empty values."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".env.example\", \"content\": \"DATABASE_URL=\\nREDIS_URL=\\nSTRIPE_API_KEY=\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-eval
{"eval_id": "fmtig_ev_writefile_dockerfile", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Create a Dockerfile that runs our FastAPI app with uvicorn on port 8000.",
 "must_call_tools": ["write_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I've created the Dockerfile", "the Dockerfile is now in place"]}
```

## Clause 4 — run_command: one-shot commands go straight to the gate

Tests, builds, installs and migrations are run_command calls the moment they are asked
for. The model does not pre-announce success and does not describe the command instead
of calling it.

```ronin-sft
{"id": "fmtig_runcmd_pytest_016", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Run the pytest suite."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest -q\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runcmd_npm_install_017", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When the user names the change, the next thing you produce is the tool call."},
   {"role": "user", "content": "We need axios in this project. Add it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npm install axios\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runcmd_docker_build_018", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. No permission speeches: emit the well-formed call and let the gate decide."},
   {"role": "user", "content": "Build the image and tag it shopcart:dev."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"docker build -t shopcart:dev .\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runcmd_clippy_019", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Gated tools are still called directly; the gate handles permission, your job is a clean call."},
   {"role": "user", "content": "Lint the crate with clippy and treat warnings as errors."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo clippy --all-targets -- -D warnings\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runcmd_alembic_020", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Asked for a concrete change, emit the call at once; the approval gate decides, not you."},
   {"role": "user", "content": "The new orders table migration is sitting there unapplied. Apply it."},
   {"role": "assistant", "content": "Heads up: this applies every pending migration, not just the orders table.", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"alembic upgrade head\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-eval
{"eval_id": "fmtig_ev_runcmd_go_tests", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Run the Go tests for the ./internal/... packages.",
 "must_call_tools": ["run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["all tests passed", "the tests are green"]}
```

## Clause 5 — Long-running processes: run_background to start, stop_background to end

A command that never exits on its own goes to run_background, immediately; ending one
is a stop_background call with the known process id. Neither warrants a narrated
substitute for the call.

```ronin-sft
{"id": "fmtig_runbg_vite_dev_021", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Start the dev server so I can poke at the UI."},
   {"role": "assistant", "content": "Long-running, so it goes to the background:", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run dev\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_background", "description": "Start a LONG-RUNNING command in the background (dev server, `--watch` build, test watcher) and return immediately with a process id — use this instead of run_command when the command doesn't exit on its own. SENSITIVE — gated by approval. Args: command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runbg_jest_watch_022", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When the user names the change, the next thing you produce is the tool call."},
   {"role": "user", "content": "I'm about to rework the reducers; keep jest watching so failures show up instantly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npx jest --watch\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_background", "description": "Start a LONG-RUNNING command in the background (dev server, `--watch` build, test watcher) and return immediately with a process id — use this instead of run_command when the command doesn't exit on its own. SENSITIVE — gated by approval. Args: command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_runbg_rails_server_023", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. No permission speeches: emit the well-formed call and let the gate decide."},
   {"role": "user", "content": "Boot the Rails app on port 3000 for me."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"bin/rails server -p 3000\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_background", "description": "Start a LONG-RUNNING command in the background (dev server, `--watch` build, test watcher) and return immediately with a process id — use this instead of run_command when the command doesn't exit on its own. SENSITIVE — gated by approval. Args: command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtig_stopbg_known_id_024", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Gated tools are still called directly; the gate handles permission, your job is a clean call."},
   {"role": "user", "content": "The dev server is background process 3 and it's hogging the port. Kill it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 3}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "stop_background", "description": "Stop (kill) a background process and its child tree by id. Args: id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}]}
```

State set earlier in the session, like a process id from a previous start, is reused in
the later call rather than re-asked.

```ronin-sft
{"id": "fmtig_stopbg_multiturn_025", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Start the TypeScript compiler in watch mode while I refactor."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npx tsc --watch\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=7 (npx tsc --watch)"},
   {"role": "user", "content": "Done refactoring, stop the watcher."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 7}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a LONG-RUNNING command in the background (dev server, `--watch` build, test watcher) and return immediately with a process id — use this instead of run_command when the command doesn't exit on its own. SENSITIVE — gated by approval. Args: command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop (kill) a background process and its child tree by id. Args: id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-eval
{"eval_id": "fmtig_ev_runbg_devserver", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Start the Next.js dev server and leave it running while we work.",
 "must_call_tools": ["run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the dev server is now running", "server is up on port 3000"]}
```

```ronin-eval
{"eval_id": "fmtig_ev_stopbg_multiturn", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Asked for a concrete change, emit the call at once; the approval gate decides, not you.",
 "messages": [
   {"role": "user", "content": "Start the docs build in watch mode."},
   {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run docs:watch\"}"}}]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=4 (npm run docs:watch)"}
 ],
 "prompt": "Actually that's enough, stop the docs watcher.",
 "must_call_tools": ["stop_background"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the watcher has been stopped", "process 4 is no longer running"]}
```
