# Volume X-g — Malformed calls corrected

When a tool call is rejected as malformed, Ronin's next turn is the corrected call
itself: a structured `tool_calls` entry with strict-JSON arguments, the exact
registry tool name, and empty content. The broken attempt only ever appears quoted
by the user or inside a tool result; Ronin never re-states it, apologizes at
length, or describes the fix in prose before making it.

## Clause 1 — Relaxed JSON is rejected JSON: quote the keys, use double quotes

Unquoted keys and single-quoted strings are the two most common generator bugs.
The correction is the same call with strict-JSON arguments, emitted directly.

```ronin-sft
{"id": "fmtmf_unquoted_read_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "My codegen script produced this and Ronin's runtime rejected it: {name: read_file, arguments: {path: src/payments/refund.py}}. Emit the call correctly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/payments/refund.py\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtmf_unquoted_glob_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Fix a rejected call by emitting the well-formed call, never by quoting JSON in prose."},
   {"role": "user", "content": "The runtime log says unquoted keys are not valid JSON and shows {pattern: **/*.tsx}. That was meant to be a glob call over the whole repo. Re-send it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.tsx\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtmf_squote_search_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "This search_files call failed with 'arguments are not valid JSON': {'query': 'ECONNREFUSED', 'directory': 'server'}. Single quotes, apparently. Fix it and run it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"ECONNREFUSED\", \"directory\": \"server\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_squote_repomap_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Rejected calls get corrected and re-emitted as structured calls, nothing else."},
   {"role": "user", "content": "My orchestrator emitted {'query': 'where user sessions are persisted'} for repo_map and Ronin refused it because of the single quotes. Send the corrected call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"where user sessions are persisted\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_unquoted_runcmd_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Strict JSON arguments, registry tool names, structured calls only."},
   {"role": "user", "content": "Rejected before execution: {command: npx prettier --check .} needs proper JSON quoting on both the key and the value. It was a run_command call; re-emit it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx prettier --check .\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

## Clause 2 — Python literals and wrong argument types

`None`, `True`, and stringified integers are Python leaking into the wire format.
The corrected call drops null-intent keys, removes arguments the schema does not
define, and sends integers as integers.

```ronin-sft
{"id": "fmtmf_pynone_bglogs_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "The runtime refused this background_logs call because None is Python, not JSON: {\"id\": 7, \"lines\": None}. Just give me the recent output of process 7 with the default line count."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 7}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtmf_pytrue_listfiles_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Fix a rejected call by emitting the well-formed call, never by quoting JSON in prose."},
   {"role": "user", "content": "My script generated {\"directory\": \"app\", \"recursive\": True} for list_files and Ronin rejected it. True is not JSON, and recursive is not an argument the tool takes. Re-send a valid listing call for the app directory."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"app\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtmf_strid_stopbg_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Argument types must match the tool schema exactly."},
   {"role": "user", "content": "stop_background bounced my call {\"id\": \"2\"} because it wants an integer id, not a string. Kill process 2 properly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 2}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "stop_background", "description": "Stop (kill) a background process and its child tree by id. Args: id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtmf_pynone_glob_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Strict JSON arguments, registry tool names, structured calls only."},
   {"role": "user", "content": "Another rejected call from my batch runner: {\"pattern\": \"*.gradle\", \"directory\": None}. Drop the Python null and emit the glob correctly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"*.gradle\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

## Clause 3 — Trailing commas

A trailing comma anywhere in the arguments kills the whole call. The fix is the
identical payload minus the comma, re-emitted without commentary.

```ronin-sft
{"id": "fmtmf_trailcomma_edit_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "This edit_file call was rejected as invalid JSON, trailing comma after new_string: {\"path\": \"config/nginx.conf\", \"old_string\": \"keepalive_timeout 65;\", \"new_string\": \"keepalive_timeout 30;\",}. Re-emit it without the comma."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"config/nginx.conf\", \"old_string\": \"keepalive_timeout 65;\", \"new_string\": \"keepalive_timeout 30;\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtmf_trailcomma_search_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Rejected calls get corrected and re-emitted as structured calls, nothing else."},
   {"role": "user", "content": "Runtime says invalid JSON: {\"query\": \"LaunchDarkly\",} because my template left a trailing comma. It was a search_files call over the whole repo. Fix and send."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"LaunchDarkly\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_trailcomma_multiedit_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Fix a rejected call by emitting the well-formed call, never by quoting JSON in prose."},
   {"role": "user", "content": "My theme migration script emitted a multi_edit for src/theme.css that got rejected, trailing comma inside the edits array: {\"path\": \"src/theme.css\", \"edits\": [{\"old_string\": \"#1a73e8\", \"new_string\": \"#0f5bd7\"}, {\"old_string\": \"#fbbc04\", \"new_string\": \"#e8a800\"},]}. Emit the corrected call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"src/theme.css\", \"edits\": [{\"old_string\": \"#1a73e8\", \"new_string\": \"#0f5bd7\"}, {\"old_string\": \"#fbbc04\", \"new_string\": \"#e8a800\"}]}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

## Clause 4 — Wrong tool name: correct to the registry name, keep the intent

`readfile`, `search-files`, `runcommand` are not registry tools. The correction
maps to the real name and preserves the original arguments.

```ronin-sft
{"id": "fmtmf_toolname_readfile_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Only registry tool names exist; correct near-misses to the real name."},
   {"role": "user", "content": "Rejected with unknown tool 'readfile': {\"name\": \"readfile\", \"arguments\": \"{\\\"path\\\": \\\"lib/telemetry.ex\\\"}\"}. The registry name has an underscore. Re-emit it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"lib/telemetry.ex\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtmf_toolname_searchdash_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "My agent framework hyphenates tool names, so Ronin refused this: {\"name\": \"search-files\", \"arguments\": \"{\\\"query\\\": \\\"unwrap()\\\", \\\"directory\\\": \\\"src\\\"}\"}. Send it under the real name."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"unwrap()\", \"directory\": \"src\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_toolname_runcommand_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Strict JSON arguments, registry tool names, structured calls only."},
   {"role": "user", "content": "The queue rejected unknown tool 'runcommand'. The call was {\"name\": \"runcommand\", \"arguments\": \"{\\\"command\\\": \\\"go vet ./...\\\"}\"}. Correct the name and emit it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go vet ./...\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtmf_toolname_bgstatus_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Only registry tool names exist; correct near-misses to the real name."},
   {"role": "user", "content": "My watchdog emitted {\"name\": \"background-status\", \"arguments\": \"{}\"} and got unknown tool back. It just wants the process list. Re-emit it under the registry name."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}]}
```

## Clause 5 — Structural failures: raw-object arguments and markdown-fenced calls

`function.arguments` is a JSON-encoded string, and a call typed into reply text
never executes. Both failures are corrected the same way: one structured call.

```ronin-sft
{"id": "fmtmf_argsobj_write_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Tool arguments travel as a JSON-encoded string, never as a raw object."},
   {"role": "user", "content": "The runtime rejected my write_file call: function.arguments must be a JSON-encoded string, got object. My generator had set arguments to a raw object with path \".editorconfig\" and content \"root = true\" plus a trailing newline. Emit it with properly encoded arguments."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".editorconfig\", \"content\": \"root = true\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtmf_argsobj_repomap_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "Rejected: arguments came through as a raw JSON object instead of an encoded string. The intent was repo_map with query \"rate limiter middleware\" and k set to 5. Re-emit it correctly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"rate limiter middleware\", \"k\": 5}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_mdfence_edit_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A call written into reply text never executes; make it a structured call."},
   {"role": "user", "content": "A previous session printed this in its reply instead of calling anything: ```json {\"tool\": \"edit_file\", \"path\": \"Gemfile\", \"old_string\": \"gem 'rails', '7.1.0'\", \"new_string\": \"gem 'rails', '7.1.3'\"} ``` and nothing executed. Do it as a real call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"Gemfile\", \"old_string\": \"gem 'rails', '7.1.0'\", \"new_string\": \"gem 'rails', '7.1.3'\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtmf_mdfence_runcmd_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Fix a rejected call by emitting the well-formed call, never by quoting JSON in prose."},
   {"role": "user", "content": "The transcript shows the model typed a fenced block ```{\"name\": \"run_command\", \"arguments\": {\"command\": \"cargo test -p auth\"}}``` as plain text, so the runtime never saw a call. Emit it for real."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo test -p auth\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

## Clause 6 — Missing required keys: fill from the user's stated intent

When the rejection names a missing required argument and the user has supplied
the missing value, the correction is the complete call with that key filled in.

```ronin-sft
{"id": "fmtmf_missing_newstring_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "edit_file rejected my call: missing required key 'new_string'. I sent {\"path\": \"Makefile\", \"old_string\": \"-j4\"}. The replacement should be -j8. Emit the complete call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"Makefile\", \"old_string\": \"-j4\", \"new_string\": \"-j8\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtmf_missing_query_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Rejected calls get corrected and re-emitted as structured calls, nothing else."},
   {"role": "user", "content": "My script built a search_files call with only {\"directory\": \"packages\"} and the runtime refused it because query is required. I wanted it to look for getServerSideProps. Send the fixed call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"getServerSideProps\", \"directory\": \"packages\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmf_missing_content_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Strict JSON arguments, registry tool names, structured calls only."},
   {"role": "user", "content": "write_file bounced {\"path\": \".python-version\"} because content is required. The file should contain exactly 3.12 followed by a newline. Emit the corrected call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".python-version\", \"content\": \"3.12\\n\"}"}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

## Clause 7 — The malformed attempt arrives in a tool result

A queue file or runtime log read through a valid call can contain someone else's
broken emission. Ronin extracts the intent and re-emits it well-formed, without
echoing the broken text back.

```ronin-sft
{"id": "fmtmf_toolresult_queue_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "My release helper writes its next Ronin call to .ronin/pending_call.json, but the runtime keeps rejecting it. Read the file and execute what it was trying to do, properly."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \".ronin/pending_call.json\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "{name: run_command, arguments: {command: \"npm run build\"}}"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npm run build\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmf_toolresult_log_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool call is rejected as malformed, re-emit the corrected structured call immediately."},
   {"role": "user", "content": "Ronin's runtime just logged a rejection in logs/runtime_errors.log. Read it and re-emit whatever call it refused."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"logs/runtime_errors.log\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "2026-07-14T10:02:11Z ERROR tool call arguments are not valid JSON: {'path': 'services/billing/invoice.py', 'old_string': 'retries = 1', 'new_string': 'retries = 3'}. Single-quoted strings. The call was not executed."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"services/billing/invoice.py\", \"old_string\": \"retries = 1\", \"new_string\": \"retries = 3\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE, gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}
 ]}
```

## Eval block — the correct next output is the corrected call

```ronin-eval
{"eval_id": "fmtmf_ev_unquoted_readfile", "category": "recovery", "source_volume": "X",
 "prompt": "My generator produced this and the runtime rejected it before execution: {name: read_file, arguments: {path: docker/entrypoint.sh}}. Emit the corrected call.",
 "must_call_tools": ["read_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Here is what docker/entrypoint.sh contains", "I re-ran the corrected call and it succeeded"]}
```

```ronin-eval
{"eval_id": "fmtmf_ev_python_none_bglogs", "category": "recovery", "source_volume": "X",
 "prompt": "This background_logs call was refused because None is not valid JSON: {\"id\": 4, \"lines\": None}. Re-emit it so I get the recent output of process 4.",
 "must_call_tools": ["background_logs"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The latest output of process 4 shows"]}
```

```ronin-eval
{"eval_id": "fmtmf_ev_wrong_tool_name", "category": "recovery", "source_volume": "X",
 "prompt": "Rejected with unknown tool: {\"name\": \"searchfiles\", \"arguments\": \"{\\\"query\\\": \\\"withRetry(\\\"}\"}. Send the corrected call under the registry name.",
 "must_call_tools": ["search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I found these occurrences of withRetry"]}
```

```ronin-eval
{"eval_id": "fmtmf_ev_trailing_comma_multiturn", "category": "recovery", "source_volume": "X",
 "messages": [
   {"role": "user", "content": "My deploy helper queued a Ronin call and the runtime logged: error, arguments are not valid JSON: {\"command\": \"kubectl rollout status deploy/api\",} with a trailing comma."}
 ],
 "prompt": "Re-emit that call so it actually executes.",
 "must_call_tools": ["run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["deployment \"api\" successfully rolled out", "The rollout completed cleanly"]}
```

```ronin-eval
{"eval_id": "fmtmf_ev_missing_content_write", "category": "schema_compliance", "source_volume": "X",
 "prompt": "The runtime refused this write_file call: missing required key 'content'. It was {\"path\": \".nvmrc\"} and the file should contain exactly 20.11.1 followed by a newline. Emit the complete corrected call.",
 "must_call_tools": ["write_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["I've written .nvmrc with the version", "The file now contains 20.11.1"]}
```

```ronin-eval
{"eval_id": "fmtmf_ev_markdown_fenced_glob", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "A prior session typed this into its reply as plain text, so nothing ran: ```json {\"tool\": \"glob\", \"pattern\": \"**/*.test.ts\"} ``` Execute it as a real call.",
 "must_call_tools": ["glob"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The matching test files are"]}
```
