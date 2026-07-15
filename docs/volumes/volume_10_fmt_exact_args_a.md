# Volume X-c — Exact arguments I

A tool call is only executable if its arguments match the runtime schema exactly: the
schema's key names and nothing else, the schema's types (integer ids stay integers),
and values copied character for character from the request. This volume drills that
precision: `edit_file`'s three required keys, `multi_edit`'s nested `edits` array,
integer arguments for the background tools, and verbatim paths, patterns, queries,
commands, and content everywhere.

## Clause 1 — edit_file takes path, old_string, new_string. Nothing else.

Every `edit_file` call carries exactly those three keys. No `file`, no `find`, no
`replace`, no options the schema does not define. The strings are copied exactly,
whitespace included.

```ronin-sft
{"id": "fmtxa_edit_flag_flip_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "In settings.py, change ENABLE_BETA = False to ENABLE_BETA = True."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"settings.py\", \"old_string\": \"ENABLE_BETA = False\", \"new_string\": \"ENABLE_BETA = True\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtxa_edit_yaml_indent_002", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Tool arguments use the schema's exact keys; copy strings from the request verbatim, whitespace included."},
   {"role": "user", "content": "In deploy/values.yaml the line '  replicas: 2' (two leading spaces) should become '  replicas: 4'. Keep the indentation intact."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"deploy/values.yaml\", \"old_string\": \"  replicas: 2\", \"new_string\": \"  replicas: 4\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtxa_edit_deep_path_003", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the tool call directly; the path goes into the call exactly as the user wrote it."},
   {"role": "user", "content": "Fix the typo in srv/api/v2/handlers.go: the function name HandleUsrProfile should be HandleUserProfile."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"srv/api/v2/handlers.go\", \"old_string\": \"HandleUsrProfile\", \"new_string\": \"HandleUserProfile\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

A rejected call is corrected by re-emitting it with the schema's keys, not by
apologizing in prose. The broken attempt appears only in the user's report; the
correction is a structured call.

```ronin-sft
{"id": "fmtxa_edit_wrong_keys_fix_004", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When a call is rejected for bad argument keys, resend it with the schema's exact keys. No narration."},
   {"role": "user", "content": "This call was rejected by the runtime: {\"name\": \"edit_file\", \"arguments\": {\"file\": \"Cargo.toml\", \"find\": \"edition = \\\"2018\\\"\", \"replace\": \"edition = \\\"2021\\\"\"}}. Emit it correctly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"Cargo.toml\", \"old_string\": \"edition = \\\"2018\\\"\", \"new_string\": \"edition = \\\"2021\\\"\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtxa_edit_missing_key_fix_005", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A call missing a required key is fixed by resending the complete call."},
   {"role": "user", "content": "Ronin rejected this because new_string was missing: {\"name\": \"edit_file\", \"arguments\": {\"path\": \"Makefile\", \"old_string\": \"PYTHON ?= python3.10\"}}. The replacement should be PYTHON ?= python3.12. Send the fixed call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"Makefile\", \"old_string\": \"PYTHON ?= python3.10\", \"new_string\": \"PYTHON ?= python3.12\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

When the runtime reports that `old_string` is not unique, the fix is a longer
`old_string` that includes surrounding context, re-emitted immediately.

```ronin-sft
{"id": "fmtxa_edit_unique_retry_006", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. If a tool rejects a call, correct the arguments and call again without narration."},
   {"role": "user", "content": "In app/models/user.rb, extend the User class validation `validates :email, presence: true` to `validates :email, presence: true, uniqueness: true`."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/models/user.rb\", \"old_string\": \"validates :email, presence: true\", \"new_string\": \"validates :email, presence: true, uniqueness: true\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: old_string matches 2 locations in app/models/user.rb; it must be unique. A distinct anchor for the User class occurrence is:\nclass User < ApplicationRecord\n  validates :email, presence: true"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/models/user.rb\", \"old_string\": \"class User < ApplicationRecord\\n  validates :email, presence: true\", \"new_string\": \"class User < ApplicationRecord\\n  validates :email, presence: true, uniqueness: true\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

## Clause 2 — multi_edit nests its replacements in an edits array

`multi_edit` takes `path` plus an `edits` array whose items each carry `old_string`
and `new_string`. The replacements are never flattened into top-level keys, never a
bare object, and never joined by invented options.

```ronin-sft
{"id": "fmtxa_multiedit_rename_pair_007", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "In src/cache.ts rename getTTL to getTtl in both places: the definition `function getTTL(key: string)` and the export line `export { getTTL };`. One approval for both."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"src/cache.ts\", \"edits\": [{\"old_string\": \"function getTTL(key: string)\", \"new_string\": \"function getTtl(key: string)\"}, {\"old_string\": \"export { getTTL };\", \"new_string\": \"export { getTtl };\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_three_targets_008", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Several replacements to one file go into a single multi_edit call with one edits array."},
   {"role": "user", "content": "Three changes to config/nginx.conf in one atomic step: 'worker_processes 2;' becomes 'worker_processes 4;', 'keepalive_timeout 65;' becomes 'keepalive_timeout 30;', and 'gzip off;' becomes 'gzip on;'."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"config/nginx.conf\", \"edits\": [{\"old_string\": \"worker_processes 2;\", \"new_string\": \"worker_processes 4;\"}, {\"old_string\": \"keepalive_timeout 65;\", \"new_string\": \"keepalive_timeout 30;\"}, {\"old_string\": \"gzip off;\", \"new_string\": \"gzip on;\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_flat_keys_fix_009", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A rejected multi_edit is corrected by nesting the replacements in the edits array."},
   {"role": "user", "content": "The runtime threw this out: {\"name\": \"multi_edit\", \"arguments\": {\"path\": \"schema.sql\", \"old_string\": \"VARCHAR(64)\", \"new_string\": \"VARCHAR(128)\", \"old_string_2\": \"INT(4)\", \"new_string_2\": \"BIGINT\"}}. It needs the nested edits array. Re-emit."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"schema.sql\", \"edits\": [{\"old_string\": \"VARCHAR(64)\", \"new_string\": \"VARCHAR(128)\"}, {\"old_string\": \"INT(4)\", \"new_string\": \"BIGINT\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_no_extra_keys_010", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. multi_edit takes path and edits only; never invent extra options like dry_run or backup."},
   {"role": "user", "content": "Atomically in Dockerfile: change 'FROM node:18-alpine' to 'FROM node:20-alpine' and 'EXPOSE 3000' to 'EXPOSE 8080'."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"Dockerfile\", \"edits\": [{\"old_string\": \"FROM node:18-alpine\", \"new_string\": \"FROM node:20-alpine\"}, {\"old_string\": \"EXPOSE 3000\", \"new_string\": \"EXPOSE 8080\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_version_bump_011", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Quoted JSON fragments in the request are copied into old_string and new_string exactly, quotes included."},
   {"role": "user", "content": "In package.json bump \"version\": \"2.4.1\" to \"version\": \"2.5.0\" and \"lastRelease\": \"2.4.1\" to \"lastRelease\": \"2.5.0\" in one shot."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"package.json\", \"edits\": [{\"old_string\": \"\\\"version\\\": \\\"2.4.1\\\"\", \"new_string\": \"\\\"version\\\": \\\"2.5.0\\\"\"}, {\"old_string\": \"\\\"lastRelease\\\": \\\"2.4.1\\\"\", \"new_string\": \"\\\"lastRelease\\\": \\\"2.5.0\\\"\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_object_fix_012", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. The edits argument is an array of objects, even for a single replacement; correct rejected calls silently."},
   {"role": "user", "content": "This failed schema validation because edits was an object instead of an array: {\"name\": \"multi_edit\", \"arguments\": {\"path\": \"styles/theme.css\", \"edits\": {\"old_string\": \"--accent: #ff6b35\", \"new_string\": \"--accent: #0f766e\"}}}. Also add a second replacement: --radius: 2px becomes --radius: 6px. Corrected call, please."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"styles/theme.css\", \"edits\": [{\"old_string\": \"--accent: #ff6b35\", \"new_string\": \"--accent: #0f766e\"}, {\"old_string\": \"--radius: 2px\", \"new_string\": \"--radius: 6px\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtxa_multiedit_env_values_013", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "In .env.staging change API_HOST=api.staging.internal to API_HOST=api.stage2.internal and API_PORT=8443 to API_PORT=9443, as a single atomic step."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \".env.staging\", \"edits\": [{\"old_string\": \"API_HOST=api.staging.internal\", \"new_string\": \"API_HOST=api.stage2.internal\"}, {\"old_string\": \"API_PORT=8443\", \"new_string\": \"API_PORT=9443\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

## Clause 3 — background tools take integer ids and line counts

`background_logs` and `stop_background` take `id` as a JSON integer, and `lines` is
an integer too. A quoted number is a schema violation, not a stylistic choice.

```ronin-sft
{"id": "fmtxa_bglogs_int_id_014", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Process ids are JSON integers, never quoted strings."},
   {"role": "user", "content": "What has background process 7 printed lately?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 7}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtxa_bglogs_lines_015", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Give me the last 120 lines of output from process 3."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 3, \"lines\": 120}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtxa_stopbg_int_016", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. stop_background takes a single integer id; no other keys exist."},
   {"role": "user", "content": "Background job 12 is stuck. Kill it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 12}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "stop_background", "description": "Stop (kill) a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtxa_bglogs_string_fix_017", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A call rejected for a type error is resent with the correct JSON types."},
   {"role": "user", "content": "Rejected: {\"name\": \"background_logs\", \"arguments\": {\"id\": \"4\", \"lines\": \"60\"}}. The schema wants integers, not strings. Fix and resend."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 4, \"lines\": 60}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

## Clause 4 — copy paths, patterns, and queries verbatim

The user's path, glob pattern, or search string goes into the argument exactly as
given: same capitalization, same punctuation, no helpful rewording. Tools that take
no arguments get an empty object, not invented filters.

```ronin-sft
{"id": "fmtxa_read_exact_path_018", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Open docs/adr/0007-event-sourcing.md."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"docs/adr/0007-event-sourcing.md\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtxa_search_literal_019", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Search queries are literal strings copied exactly, capitalization and punctuation included."},
   {"role": "user", "content": "Grep the services directory for the exact string X-Request-Id, with that capitalization."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"X-Request-Id\", \"directory\": \"services\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtxa_glob_pattern_020", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Glob patterns go into the pattern argument exactly as requested."},
   {"role": "user", "content": "Find every *.spec.tsx file anywhere under packages/web."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.spec.tsx\", \"directory\": \"packages/web\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtxa_repomap_k_021", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. repo_map takes a query string and an integer k; a requested result count becomes k."},
   {"role": "user", "content": "Where is rate limiting implemented? Give me the top 5 ranked files."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"where is rate limiting implemented\", \"k\": 5}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtxa_bgstatus_empty_022", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. background_status takes no arguments; send an empty object, never invented filters."},
   {"role": "user", "content": "Which of my background jobs are still alive?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}]}
```

## Clause 5 — commands and content, character for character

When the user dictates a command or file content, the argument value is that text
exactly, including quotes, flags, and trailing newline conventions. Long-running
commands keep the same `command` key under `run_background`.

```ronin-sft
{"id": "fmtxa_runcmd_verbatim_023", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A dictated command goes into the command argument exactly as typed."},
   {"role": "user", "content": "Run exactly: pytest tests/unit -k \"parser and not slow\" -q"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"pytest tests/unit -k \\\"parser and not slow\\\" -q\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtxa_runbg_key_fix_024", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. run_background's only argument key is command; correct rejected calls by resending with the schema's key."},
   {"role": "user", "content": "The runtime bounced this: {\"name\": \"run_background\", \"arguments\": {\"cmd\": \"npm run dev -- --port 5173\"}}. The key is wrong. Emit the valid call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run dev -- --port 5173\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return immediately with a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtxa_write_exact_025", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Dictated file content is written exactly as given."},
   {"role": "user", "content": "Create .nvmrc containing exactly one line: 20.11.1"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".nvmrc\", \"content\": \"20.11.1\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. Gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

## Clause 6 — evals

Each case probes one argument-precision behavior: the right tool is called, values
from the ask appear verbatim in the call, and no completion is claimed before a
result exists.

```ronin-eval
{"eval_id": "fmtxa_ev_edit_exact_keys", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "In lib/retry.ex change `@max_attempts 3` to `@max_attempts 5`.",
 "must_call_tools": ["edit_file"],
 "must_include": ["lib/retry.ex", "@max_attempts 5"],
 "must_not_include": ["I have updated lib/retry.ex", "I applied the edit to lib/retry.ex"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmtxa_ev_multiedit_nested_array", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "In tsconfig.json, atomically change \"target\": \"es2019\" to \"target\": \"es2022\" and \"strict\": false to \"strict\": true. One gated step for the whole file.",
 "must_call_tools": ["multi_edit"],
 "must_include": ["tsconfig.json", "es2022"],
 "must_not_include": ["I have applied both edits", "tsconfig.json now targets es2022"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmtxa_ev_bglogs_integer_args", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Show me the last 60 lines from background process 9.",
 "must_call_tools": ["background_logs"],
 "must_not_include": ["here is the latest output from process 9", "the logs show the server started"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmtxa_ev_stopbg_integer_id", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Background job 21 is wedged. Stop it.",
 "must_call_tools": ["stop_background"],
 "must_not_include": ["I stopped process 21", "process 21 has been stopped"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmtxa_ev_search_query_verbatim", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Grep the whole repo for the exact literal string TODO(mara): remove after Q3.",
 "must_call_tools": ["search_files"],
 "must_include": ["TODO(mara): remove after Q3"],
 "must_not_include": ["I found 3 occurrences", "no matches were found"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "fmtxa_ev_multiedit_correction", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "messages": [
   {"role": "user", "content": "My previous agent tried this and Ronin's runtime rejected it: {\"name\": \"multi_edit\", \"arguments\": {\"path\": \"internal/worker/pool.go\", \"old_string\": \"maxWorkers = 8\", \"new_string\": \"maxWorkers = 16\"}}. It flattened the edits and dropped the second replacement entirely."},
   {"role": "assistant", "content": "That attempt is missing the required edits array, so the runtime was right to reject it. Give me the second replacement and I will send the corrected call."}
 ],
 "prompt": "The second replacement is queueDepth = 256 becomes queueDepth = 512. Emit the corrected call now.",
 "must_call_tools": ["multi_edit"],
 "must_include": ["queueDepth = 512"],
 "must_not_include": ["I have applied both replacements", "pool.go has been updated"],
 "forbid_unknown_tools": true}
```
