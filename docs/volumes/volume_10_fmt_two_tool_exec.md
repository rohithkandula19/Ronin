# Volume X-f — Two-call sequences: exec flows

Exec work comes in pairs: the first call produces a process id, a failing test name, or an applied edit, and the second call consumes it. Every exemplary assistant turn below emits the next step as a structured tool call with an empty content string; ids and arguments are copied from the prior tool result, never invented. Narrating the next call instead of making it is the failure mode this volume trains out.

## Clause 1 — Start, then read: run_background feeds background_logs

The id returned by `run_background` is the only valid argument for the follow-up `background_logs` call. No prose between the result and the read.

```ronin-sft
{"id": "fmt2e_bg_dev_server_logs", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Start the Next.js dev server in the background, then check what it printed."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run dev\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 4: npm run dev"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 4}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_bg_cargo_watch_logs", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "Kick off cargo watch -x test in the background and show me its first output."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"cargo watch -x test\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Background process 2 started: cargo watch -x test"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 2}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_bg_http_server_logs", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "Serve the docs folder on port 8080 in the background and confirm it bound the port."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"python -m http.server 8080 --directory docs\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 9: python -m http.server 8080 --directory docs"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 9}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_bg_uvicorn_logs_60", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away."},
   {"role": "user", "content": "Run the FastAPI app with reload in the background; then pull the last 60 lines of its output."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"uvicorn app.main:app --reload\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 3: uvicorn app.main:app --reload"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 3, \"lines\": 60}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_bg_tsc_watch_logs", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Start the TypeScript compiler in watch mode and check whether the first build was clean."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npx tsc --watch\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 11: npx tsc --watch"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 11}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_bg_gradle_bootrun_logs", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "Boot the Spring app in the background, then grab 80 lines of startup output."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"./gradlew bootRun\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 6: ./gradlew bootRun"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 6, \"lines\": 80}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

A completed chain ends in text only after the second result is in hand, and that text contains no call-shaped JSON.

```ronin-sft
{"id": "fmt2e_bg_storybook_logs_answer", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "Launch Storybook in the background and tell me which URL it is serving on."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run storybook\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 5: npm run storybook"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 5}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "Storybook 8.1.0 started\nLocal: http://localhost:6006/\nOn your network: http://192.168.1.20:6006/"},
   {"role": "assistant", "content": "Storybook is up as background process 5 and serving on http://localhost:6006/ (also reachable on the network at http://192.168.1.20:6006/)."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process. Args: id, lines (default 40).", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

## Clause 2 — Look, then kill: background_status feeds stop_background

`stop_background` takes an id that must come from the `background_status` listing, matched against what the user described. Status first, kill second, both as bare calls.

```ronin-sft
{"id": "fmt2e_status_stop_stale_vite", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Something is still holding port 5173. Find it and kill it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=2 RUNNING: npx vite\nid=5 EXITED (code 0): npm run lint"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 2}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_status_stop_old_flask", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away."},
   {"role": "user", "content": "The old Flask server from earlier is conflicting with the new one. Shut the old one down."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=8 RUNNING: flask --app server run --port 5000 (started 41m ago)\nid=12 RUNNING: flask --app server run --port 5050 (started 2m ago)"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 8}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_status_stop_jest_watch", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "We are done iterating; stop the Jest watcher."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=1 RUNNING: npx jest --watch\nid=3 EXITED (code 0): npm run build"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 1}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_status_stop_webpack_cpu", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "My fans are spinning; some watcher is eating CPU. Kill it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=14 RUNNING: npx webpack --watch (cpu 96%)\nid=6 EXITED (code 1): npm audit"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 14}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_status_stop_tail_answer", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Stop tailing the worker log, I have seen enough."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=4 RUNNING: tail -f logs/worker.log\nid=2 EXITED (code 0): make docs"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 4}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "Stopped background process 4 (tail -f logs/worker.log)."},
   {"role": "assistant", "content": "Stopped the log tail (background process 4). The only other entry from this session, make docs, had already exited cleanly."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_status_stop_storybook_done", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away."},
   {"role": "user", "content": "Design review is over; take Storybook down."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=7 RUNNING: npm run storybook\nid=10 RUNNING: npx tsc --watch"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 7}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "background_status", "description": "List the background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

## Clause 3 — Run the suite, then rerun the failure

The focused rerun's target is lifted verbatim from the failure line in the first result: file path, test name, or line number, exactly as printed.

```ronin-sft
{"id": "fmt2e_pytest_focused_rerun", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Run the test suite, and if something fails rerun just that test verbosely."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest -q\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "128 passed, 1 failed\nFAILED tests/test_auth.py::test_token_expiry - AssertionError: token accepted after expiry"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_auth.py::test_token_expiry -vv\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_jest_focused_rerun", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "Run Jest; zoom in on any failure."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx jest\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Tests: 1 failed, 84 passed\nFAIL src/utils/date.test.ts\n  * formats ISO dates with timezone offset"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx jest src/utils/date.test.ts -t \\\"formats ISO dates with timezone offset\\\"\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_go_focused_rerun", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "Run all Go tests, then isolate whatever breaks."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go test ./...\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "ok   github.com/acme/relay/pkg/router 0.12s\n--- FAIL: TestUnclosedQuote (0.00s)\nFAIL github.com/acme/relay/pkg/parser 0.31s"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go test ./pkg/parser -run TestUnclosedQuote -v\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_cargo_focused_rerun", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away."},
   {"role": "user", "content": "cargo test everything; drill into any red test."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo test\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "test config::tests::env_override ... FAILED\ntest result: FAILED. 61 passed; 1 failed"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo test config::tests::env_override -- --nocapture\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_vitest_focused_rerun", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Run vitest once and chase down any failing spec."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx vitest run\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Test Files 1 failed | 12 passed\nFAIL src/cart/discount.test.ts > applies stacked discounts"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx vitest run src/cart/discount.test.ts\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_rspec_focused_rerun_answer", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "Run RSpec, and if a spec fails rerun it by line number so we can see the detail."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"bundle exec rspec\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Failures:\n  1) Order#total applies bulk pricing\n     # ./spec/models/order_spec.rb:42\n214 examples, 1 failure"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"bundle exec rspec spec/models/order_spec.rb:42\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "1) Order#total applies bulk pricing\n   expected: 90.0\n        got: 100.0\n1 example, 1 failure"},
   {"role": "assistant", "content": "Reproduced it in isolation: Order#total returns 100.0 where bulk pricing should give 90.0, so the discount is never applied. The failure lives at spec/models/order_spec.rb:42; next step would be reading Order#total."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

## Clause 4 — Edit, then verify

An edit is unproven until a test run says otherwise. The verifying command targets the tests that cover the edited file, and it goes out as a bare call the moment the edit result lands.

```ronin-sft
{"id": "fmt2e_edit_pagination_verify", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "pagination.py drops the last item on every page; the slice end is off by one. Fix it and prove it with the pagination tests."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/pagination.py\", \"old_string\": \"return items[start:end - 1]\", \"new_string\": \"return items[start:end]\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to src/pagination.py."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_pagination.py -q\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_edit_nullcheck_verify", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "getUser blows up when the profile is missing. Guard it in user_service.ts, then run its test file."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/services/user_service.ts\", \"old_string\": \"return user.profile.displayName;\", \"new_string\": \"return user.profile?.displayName ?? user.email;\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to src/services/user_service.ts."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx jest src/services/user_service.test.ts\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_edit_sql_group_verify", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away."},
   {"role": "user", "content": "The monthly report query in reports.go groups by city but it should group by region. Fix it and verify with the package tests."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"internal/reports/reports.go\", \"old_string\": \"GROUP BY city\", \"new_string\": \"GROUP BY region\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to internal/reports/reports.go."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go test ./internal/reports\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_edit_timeout_verify", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration."},
   {"role": "user", "content": "Bump the HTTP client read timeout from 5 to 30 seconds in http_client.rb and run its spec."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"lib/http_client.rb\", \"old_string\": \"read_timeout: 5\", \"new_string\": \"read_timeout: 30\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to lib/http_client.rb."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"bundle exec rspec spec/http_client_spec.rb\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_edit_regex_verify", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "The email validator in validator.rs rejects plus-addressing. Allow '+' in the local part, then run the validator tests."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/validator.rs\", \"old_string\": \"^[A-Za-z0-9._-]+@\", \"new_string\": \"^[A-Za-z0-9._+-]+@\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to src/validator.rs."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo test validator\"}"}}
   ]}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "fmt2e_edit_envvar_verify_answer", "family": "fmt_two_tool_sequence", "source_volume": "X", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one."},
   {"role": "user", "content": "settings.py reads CACHE_TTL but the deploy sets CACHE_TTL_SECONDS. Align the code to the deploy name and run the settings tests."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/settings.py\", \"old_string\": \"os.environ.get('CACHE_TTL', '300')\", \"new_string\": \"os.environ.get('CACHE_TTL_SECONDS', '300')\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to app/settings.py."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_settings.py -q\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "3 passed in 0.41s"},
   {"role": "assistant", "content": "Renamed the lookup in app/settings.py to CACHE_TTL_SECONDS so it matches what the deploy exports, and the settings tests pass (3 passed). I did not run the wider suite."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "edit_file", "description": "Replace an exact, unique string in an existing file. Gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. Gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

## Clause 5 — Eval probes

Each probe hands the model the first half of a chain and grades whether the second half arrives as a real call with the right tool and no invented outcome.

```ronin-eval
{"eval_id": "fmt2e_ev_logs_after_start", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "messages": [
   {"role": "user", "content": "Start the dev server in the background."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run dev\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 7: npm run dev"}
 ],
 "prompt": "Good. Now check what it printed on startup.",
 "must_call_tools": ["background_logs"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The server started cleanly and is listening on port 3000", "Startup output looks clean, no errors"]}
```

```ronin-eval
{"eval_id": "fmt2e_ev_stop_uses_status_id", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration.",
 "messages": [
   {"role": "user", "content": "What is still running from this session?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "id=3 RUNNING: ptw --runner 'pytest -q'\nid=5 EXITED (code 0): npm run build"}
 ],
 "prompt": "Kill the test watcher.",
 "must_call_tools": ["stop_background"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The watcher has been stopped", "I stopped process 3 and it is gone"]}
```

```ronin-eval
{"eval_id": "fmt2e_ev_focused_rerun_from_failure", "category": "recovery", "source_volume": "X",
 "system": "You are Ronin, a coding agent. Act through tool calls; emit them immediately when a step needs one.",
 "messages": [
   {"role": "user", "content": "Run the test suite."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx vitest run\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Test Files 1 failed | 9 passed\nFAIL src/billing/proration.test.ts > prorates mid-cycle upgrades"}
 ],
 "prompt": "Rerun just the failing file.",
 "must_call_tools": ["run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["All tests pass now", "The proration failure is fixed"]}
```

```ronin-eval
{"eval_id": "fmt2e_ev_verify_after_edit", "category": "multi_turn_stability", "source_volume": "X",
 "system": "You are Ronin, a coding agent. If a step requires a tool, issue the call right away.",
 "messages": [
   {"role": "user", "content": "Fix the rounding bug in invoice.py: totals should round half up, not bankers-round."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/invoice.py\", \"old_string\": \"round(total, 2)\", \"new_string\": \"float(Decimal(str(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Edit applied to app/invoice.py."}
 ],
 "prompt": "Edit is in. Run the invoice tests to verify it.",
 "must_call_tools": ["run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The invoice tests pass and the fix is verified", "Rounding is confirmed correct now"]}
```

```ronin-eval
{"eval_id": "fmt2e_ev_background_start_immediate", "category": "valid_tool_json", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately.",
 "prompt": "Start `python -m http.server 8000` in the background so we can browse the built docs while we keep working.",
 "must_call_tools": ["run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The docs server is now running on port 8000", "I started the server and it is serving the docs"]}
```

```ronin-eval
{"eval_id": "fmt2e_ev_logs_id_from_result", "category": "schema_compliance", "source_volume": "X",
 "system": "You are Ronin, a coding agent. When the next step is a tool, call it directly with no narration.",
 "messages": [
   {"role": "user", "content": "Start the worker queue consumer in the background."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"python worker.py --queue default\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "Started background process 12: python worker.py --queue default"}
 ],
 "prompt": "Tail the last 80 lines of its output.",
 "must_call_tools": ["background_logs"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["The worker output shows no errors", "It is consuming from the default queue without issues"]}
```
