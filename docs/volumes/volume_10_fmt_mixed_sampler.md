# Volume X-j — Mixed dialect sampler

A cross-family drill set that mixes the four v3 dialect skills in the awkward corners
of real repos: dotfiles, monorepo workspace paths, unicode filenames, binary payloads,
multi-gigabyte files, and Windows-style paths pasted into a POSIX runtime. Every
exemplary turn is the structured call itself: empty content, exact schema, values
copied verbatim; corrections re-emit a valid call instead of apologizing in prose.

## Clause 1 — The call is the turn, even for odd names and odd payloads

Hidden dotfiles, accented filenames, workspace globs, and huge or binary files all
resolve the same way: one immediate structured call with no narration. A file too big
to slurp gets a bounded shell read; a binary gets `file`, not a doomed text read.

```ronin-sft
{"id": "fmtmx_read_editorconfig_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Does our .editorconfig use tabs or spaces?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".editorconfig"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtmx_read_unicode_notes_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Paths go into the call exactly as they exist, accents included."},
   {"role": "user", "content": "Open docs/notas/diseño-general.md, the team keeps their design notes there."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docs/notas/diseño-general.md"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtmx_glob_monorepo_manifests_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "In this pnpm monorepo, find the package.json of every workspace under packages/."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "packages/*/package.json"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtmx_list_github_dir_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call right away instead of describing it."},
   {"role": "user", "content": "What is inside the hidden .github directory?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": ".github"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtmx_head_giant_csv_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A file too large to read whole gets a bounded shell read; emit that call directly."},
   {"role": "user", "content": "data/exports/orders_2025.csv is about 3 GB, so do not slurp it. Show me the header row plus the first 20 data rows."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "head -n 21 data/exports/orders_2025.csv"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtmx_file_type_binary_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Binary files are inspected with shell tooling, not read as text; call the tool at once."},
   {"role": "user", "content": "assets/fonts/Inter.var.woff2 might be mislabeled. Check what the file actually is from its magic bytes."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "file assets/fonts/Inter.var.woff2"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtmx_map_monorepo_invoices_001", "family": "fmt_immediate_call", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a question needs a read tool, call it at once, with no narration first."},
   {"role": "user", "content": "I am new to this Nx monorepo. Which app or lib owns invoice PDF rendering? Give me the top 6 candidates."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "invoice PDF rendering", "k": 6}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus symbol outlines.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

## Clause 2 — Arguments carry the user's values verbatim, whatever the alphabet

Umlauts, printf-style format strings, dotfile names, nested workspace paths, and
integer ids all pass through untouched. The schema's keys, the schema's types, the
user's characters.

```ronin-sft
{"id": "fmtmx_edit_unicode_value_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Tool arguments copy the schema's exact keys and the user's values verbatim."},
   {"role": "user", "content": "In i18n/de.json change \"welcome\": \"Willkommen zurück\" to \"welcome\": \"Schön, dass du wieder da bist\". Keep the umlauts intact."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": {"path": "i18n/de.json", "old_string": "\"welcome\": \"Willkommen zurück\"", "new_string": "\"welcome\": \"Schön, dass du wieder da bist\""}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtmx_edit_prettierrc_width_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Dotfiles are ordinary paths; emit the surgical edit directly."},
   {"role": "user", "content": "Our .prettierrc still has \"printWidth\": 80. The team agreed on 100."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": {"path": ".prettierrc", "old_string": "\"printWidth\": 80", "new_string": "\"printWidth\": 100"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}]}
```

```ronin-sft
{"id": "fmtmx_multiedit_web_tsconfig_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Several changes to one file go into multi_edit's nested edits array, one approved step."},
   {"role": "user", "content": "Two changes to apps/web/tsconfig.json in one approved step: \"target\": \"ES2019\" becomes \"ES2022\", and \"strict\": false becomes true."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "multi_edit", "arguments": {"path": "apps/web/tsconfig.json", "edits": [{"old_string": "\"target\": \"ES2019\"", "new_string": "\"target\": \"ES2022\""}, {"old_string": "\"strict\": false", "new_string": "\"strict\": true"}]}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical string replacements to one file in a single approved step (all-or-nothing). Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}}]}
```

```ronin-sft
{"id": "fmtmx_bglogs_two_hundred_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Background tool ids and line counts are integers, never quoted strings."},
   {"role": "user", "content": "Process 9 is the data backfill and it has printed tens of thousands of lines. Only the last 200 matter."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_logs", "arguments": {"id": 9, "lines": 200}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}]}
```

```ronin-sft
{"id": "fmtmx_write_dockerignore_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. write_file content is exact, newlines included; the gate decides whether it lands."},
   {"role": "user", "content": "Create a .dockerignore with exactly three lines: node_modules, dist, .env."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": {"path": ".dockerignore", "content": "node_modules\ndist\n.env\n"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. Gated by approval. Prefer edit_file for changes to existing files.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}]}
```

```ronin-sft
{"id": "fmtmx_search_format_string_001", "family": "fmt_exact_args", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Search queries pass through character for character, punctuation and all."},
   {"role": "user", "content": "Grep the config directory for the exact logging pattern %(asctime)s [%(levelname)s], character for character."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "%(asctime)s [%(levelname)s]", "directory": "config"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Clause 3 — Two-call chains: the second call is built from the first result

Locate, then read. Size-check, then bounded read. A failed text read of a binary
falls forward into shell inspection. Between calls there is a tool result and
nothing else; both assistant turns stay empty-content.

```ronin-sft
{"id": "fmtmx_seq_binary_read_fallback_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Chain tool calls through results; each call is structured, never prose."},
   {"role": "user", "content": "Figure out what assets/img/hero.png really is; the browser refuses to render it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "assets/img/hero.png"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Error: assets/img/hero.png is not valid UTF-8 text (binary data); read_file only reads text files."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": {"command": "file assets/img/hero.png"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmx_seq_size_then_tail_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Check the size of a possibly huge file before reading; keep every step a structured call."},
   {"role": "user", "content": "logs/app.log may be huge. Check its size first; if it is big, just give me the last 100 lines."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "wc -c logs/app.log"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "2147480613 logs/app.log"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": {"command": "tail -n 100 logs/app.log"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}]}
```

```ronin-sft
{"id": "fmtmx_seq_find_unicode_doc_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Locate first, then read; both moves are immediate structured calls."},
   {"role": "user", "content": "Somewhere in this repo there is an API spec whose filename contains 'especificación'. Find it, then open it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*especificación*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "docs/especificación_api.md"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docs/especificación_api.md"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmx_seq_monorepo_hook_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. The second call reuses the path the first result reported, exactly."},
   {"role": "user", "content": "Which workspace defines the useCart hook? Locate it and show me the file."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "export function useCart"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "packages/storefront/src/hooks/useCart.ts:12: export function useCart(sessionId: string) {"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "packages/storefront/src/hooks/useCart.ts"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmx_seq_dotfile_lint_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Do we have an eslint dotfile at the repo root? Read whichever variant exists."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": ".", "pattern": ".eslintrc*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ".eslintrc.cjs"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".eslintrc.cjs"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmx_seq_windows_path_locate_001", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Paths pasted with Windows separators are located in the checkout first, then read with the runtime's own path."},
   {"role": "user", "content": "On my Windows machine the path shows as src\\services\\payment\\refund.py with backslashes. Find where it lives in this checkout, then open it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/refund.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "src/services/payment/refund.py"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/services/payment/refund.py"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 4 — A reported failure is answered with the corrected call, nothing else

The broken attempt lives in the user's report: invalid escapes, single quotes,
narrated pseudo-calls, markdown-fenced text, arguments the schema does not define.
The exemplary response never quotes the wreckage back; it is the fixed structured
call with empty content.

```ronin-sft
{"id": "fmtmx_fix_backslash_escapes_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A rejected call is fixed by re-emitting a valid structured call, not by apologizing."},
   {"role": "user", "content": "The runtime rejected this as invalid JSON with a bad \\u escape: {\"name\": \"read_file\", \"arguments\": {\"path\": \"src\\utils\\dates.ts\"}}. In this checkout paths are POSIX style: src/utils/dates.ts. Emit a call that parses."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/utils/dates.ts"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "fmtmx_fix_narrated_search_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Describing a call in prose executes nothing; the fix is the structured call itself."},
   {"role": "user", "content": "Last turn you wrote 'I would run search_files with query TODO(alex) to find his leftover markers' and nothing executed. Stop narrating and do it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "TODO(alex)"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "fmtmx_fix_fenced_glob_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Markdown-fenced call text is invisible to the runtime; emit the executable call instead."},
   {"role": "user", "content": "Your previous reply wrapped {\"name\": \"glob\", \"arguments\": {\"pattern\": \"**/*.stories.tsx\"}} in a markdown code fence, so the runtime treated it as plain text. Emit it as a real call."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/*.stories.tsx"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "fmtmx_fix_no_limit_arg_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a tool lacks the option you need, switch to a tool that has it; the correction is still one structured call."},
   {"role": "user", "content": "Bounced with 'unexpected argument: limit': {\"name\": \"read_file\", \"arguments\": {\"path\": \"vendor/bundle.min.js\", \"limit\": 500}}. read_file has no limit option and that file is 8 MB of minified JS. I only want the first 500 lines."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": {"command": "head -n 500 vendor/bundle.min.js"}}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmtmx_fix_single_quotes_list_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Tool JSON uses double quotes; resend the call as valid JSON without commentary."},
   {"role": "user", "content": "Rejected as invalid JSON because of single quotes: {'name': 'list_files', 'arguments': {'directory': 'apps/admin/src', 'pattern': '*.svelte'}}. Send it as real JSON."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "apps/admin/src", "pattern": "*.svelte"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "fmtmx_fix_accent_path_001", "family": "fmt_malformed_correction", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Correct a failed path with the real filename, accents and all, in one structured call."},
   {"role": "user", "content": "An earlier session died on: read_file error, no such file 'docs/resume.md'. The actual filename has accents: docs/résumé.md. Open the right one."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docs/résumé.md"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 5 — Evals: mixed drills

```ronin-eval
{"eval_id": "fmtmx_ev_dotfile_printwidth", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "What printWidth does our .prettierrc set?",
 "must_call_tools": ["read_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Your .prettierrc sets printWidth to 80", "printWidth is currently set to 80"]}
```

```ronin-eval
{"eval_id": "fmtmx_ev_monorepo_vitest_glob", "category": "schema_compliance", "source_volume": "X",
 "prompt": "List every vitest.config.ts across the apps/ and packages/ workspaces of this monorepo.",
 "must_call_any_of": ["glob", "list_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The workspaces with a vitest.config.ts are"]}
```

```ronin-eval
{"eval_id": "fmtmx_ev_unicode_changelog", "category": "valid_tool_json", "source_volume": "X",
 "prompt": "Open the changelog at docs/registro/versión-2.md.",
 "must_call_tools": ["read_file"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Version 2 of the changelog introduces"]}
```

```ronin-eval
{"eval_id": "fmtmx_ev_binary_identify", "category": "grounding", "source_volume": "X",
 "prompt": "assets/screenshots/dashboard.png will not open in any viewer. What kind of file is it really?",
 "must_call_any_of": ["run_command", "read_file", "list_files"],
 "must_not_call_tools": ["run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["It is a valid PNG image", "It is actually a JPEG mislabeled as PNG"]}
```

```ronin-eval
{"eval_id": "fmtmx_ev_malformed_search_retry", "category": "recovery", "source_volume": "X",
 "messages": [
   {"role": "user", "content": "Find where DEFAULT_TIMEOUT is defined in this repo."}
 ],
 "prompt": "The runtime rejected your last attempt as invalid JSON: {'query': 'DEFAULT_TIMEOUT} has single quotes and an unterminated string. Re-send it as a valid structured call.",
 "must_call_tools": ["search_files"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["DEFAULT_TIMEOUT is defined in"]}
```

```ronin-eval
{"eval_id": "fmtmx_ev_huge_ndjson_head", "category": "schema_compliance", "source_volume": "X",
 "prompt": "metrics/raw_events.ndjson is around 6 GB. Show me only its first 25 lines without loading the whole file.",
 "must_call_tools": ["run_command"],
 "must_not_call_tools": ["read_file", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["Here are the first 25 lines of raw_events.ndjson"]}
```
