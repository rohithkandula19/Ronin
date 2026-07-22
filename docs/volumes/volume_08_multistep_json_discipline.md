# Volume VIII-e — Long sessions, well-formed calls every single turn

Format discipline is not a first-turn trick: the tenth tool call in a session must be as
well-formed as the first. Every call carries a unique id (`c1..cN`) threaded to its result
via `tool_call_id`, arguments are a JSON-encoded string that matches the tool's real schema,
and one step happens at a time unless two genuinely independent reads batch. State set early
in a session (a process id, a file already read) persists and is reused, never re-invented.

## Clause 1 — Full chains: locate, read, change, prove

A real task walks many different tools in sequence. The chain only advances on what the
previous result actually said, and the close-out separates what was verified from what was not.

```ronin-sft
{"id": "mjd_flask_timeout_chain_001", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Keep every tool call well-formed across the whole session: unique ids, JSON-string arguments, one step at a time."},
   {"role": "user", "content": "Requests to our /weather endpoint hang forever when the upstream API is down. Fix it."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"outbound HTTP request to weather API\", \"k\": 5}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. app/services/weather.py — fetch_current(city), WEATHER_URL\n2. app/routes/weather.py — weather_endpoint()\n3. tests/test_weather.py — test_fetch_current_ok, test_fetch_current_upstream_error"},
   {"role": "assistant", "content": "The outbound call lives in app/services/weather.py. Reading it.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"app/services/weather.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "import requests\n\nWEATHER_URL = \"https://api.weatherhub.io/v2/current\"\n\ndef fetch_current(city):\n    resp = requests.get(WEATHER_URL, params={\"q\": city})\n    resp.raise_for_status()\n    return resp.json()\n"},
   {"role": "assistant", "content": "`requests.get` has no timeout, so a dead upstream blocks the worker forever. Proposing a surgical fix (gated).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/services/weather.py\", \"old_string\": \"resp = requests.get(WEATHER_URL, params={\\\"q\\\": city})\", \"new_string\": \"resp = requests.get(WEATHER_URL, params={\\\"q\\\": city}, timeout=5)\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "edit applied to app/services/weather.py (1 replacement)"},
   {"role": "assistant", "content": "Running the module's tests to see where we stand.", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_weather.py -q\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": ".F\n=== FAILURES ===\ntest_fetch_current_upstream_error - AssertionError: expected call not found.\nExpected: get('https://api.weatherhub.io/v2/current', params={'q': 'Oslo'})\nActual: get('https://api.weatherhub.io/v2/current', params={'q': 'Oslo'}, timeout=5)\n1 failed, 1 passed in 0.41s"},
   {"role": "assistant", "content": "The failure is the test's mock asserting the old call shape; the timeout itself is what we want. Checking the assertion.", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"tests/test_weather.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "import pytest\nimport requests\n\nfrom app.services.weather import WEATHER_URL, fetch_current\n\n\ndef test_fetch_current_ok(mock_get):\n    mock_get.return_value.json.return_value = {\"temp\": 4}\n    assert fetch_current(\"Oslo\") == {\"temp\": 4}\n\n\ndef test_fetch_current_upstream_error(mock_get):\n    mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(\"502\")\n    with pytest.raises(requests.HTTPError):\n        fetch_current(\"Oslo\")\n    mock_get.assert_called_once_with(WEATHER_URL, params={\"q\": \"Oslo\"})\n"},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"tests/test_weather.py\", \"old_string\": \"mock_get.assert_called_once_with(WEATHER_URL, params={\\\"q\\\": \\\"Oslo\\\"})\", \"new_string\": \"mock_get.assert_called_once_with(WEATHER_URL, params={\\\"q\\\": \\\"Oslo\\\"}, timeout=5)\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "edit applied to tests/test_weather.py (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c7", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_weather.py -q\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c7", "content": "..\n2 passed in 0.38s"},
   {"role": "assistant", "content": "Done. I added a 5-second timeout to fetch_current in app/services/weather.py and updated the mock assertion in tests/test_weather.py to match; both tests in that file pass. I did not run the full suite, and a timed-out request now raises requests.Timeout, so make sure your handler catches RequestException, not just HTTPError."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_go_rename_multiedit_002", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. A long refactor chain still emits one schema-valid call per step; multi_edit is for several changes to one file."},
   {"role": "user", "content": "Rename the config field ListenAddr to BindAddr everywhere in the Go service."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"**/*.go\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "cmd/api/main.go\ninternal/config/config.go\ninternal/config/config_test.go\ninternal/server/server.go"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"ListenAddr\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "internal/config/config.go:12:\tListenAddr string `yaml:\"listen_addr\"`\ninternal/config/config.go:31:\tif c.ListenAddr == \"\" {\ninternal/config/config.go:32:\t\tc.ListenAddr = \":8080\"\ninternal/server/server.go:24:\tsrv := &http.Server{Addr: cfg.ListenAddr}"},
   {"role": "assistant", "content": "Four references across two files. config.go takes a multi_edit, server.go a single edit; both are gated. I'll leave the yaml key `listen_addr` alone so existing config files keep working.", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"internal/config/config.go\", \"edits\": [{\"old_string\": \"ListenAddr string `yaml:\\\"listen_addr\\\"`\", \"new_string\": \"BindAddr string `yaml:\\\"listen_addr\\\"`\"}, {\"old_string\": \"if c.ListenAddr == \\\"\\\" {\", \"new_string\": \"if c.BindAddr == \\\"\\\" {\"}, {\"old_string\": \"c.ListenAddr = \\\":8080\\\"\", \"new_string\": \"c.BindAddr = \\\":8080\\\"\"}]}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "3 edits applied to internal/config/config.go"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"internal/server/server.go\", \"old_string\": \"srv := &http.Server{Addr: cfg.ListenAddr}\", \"new_string\": \"srv := &http.Server{Addr: cfg.BindAddr}\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "edit applied to internal/server/server.go (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go build ./...\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "exit status 0 (no output)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"go test ./... -count=1\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "ok  \tinternal/config\t0.012s\nok  \tinternal/server\t0.034s\n?   \tcmd/api\t[no test files]"},
   {"role": "assistant", "content": "Renamed ListenAddr to BindAddr in internal/config/config.go (3 sites) and internal/server/server.go (1 site). Build and tests pass. The yaml key stays `listen_addr` on purpose; if you want the config files renamed too, that is a separate, breaking change."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical replacements to one file in a single approved step. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_ts_health_route_003", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. New files go through write_file, changes to existing files through edit_file/multi_edit; every call stays schema-valid to the end."},
   {"role": "user", "content": "Add a GET /health route to the Express app that returns {\"status\":\"ok\"} and wire it in."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"where express routes are registered\", \"k\": 4}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. src/routes/index.ts — registerRoutes(app)\n2. src/app.ts — createApp()\n3. src/routes/users.ts — usersRouter"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/routes/index.ts\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "import { Express } from \"express\";\nimport { usersRouter } from \"./users\";\n\nexport function registerRoutes(app: Express) {\n  app.use(\"/users\", usersRouter);\n}\n"},
   {"role": "assistant", "content": "New router file first (gated write, brand-new file).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \"src/routes/health.ts\", \"content\": \"import { Router } from \\\"express\\\";\\n\\nexport const healthRouter = Router();\\n\\nhealthRouter.get(\\\"/health\\\", (_req, res) => {\\n  res.json({ status: \\\"ok\\\" });\\n});\\n\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "wrote src/routes/health.ts (7 lines)"},
   {"role": "assistant", "content": "Wiring it into the registrar: import plus registration, two changes to one file, so one multi_edit.", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"src/routes/index.ts\", \"edits\": [{\"old_string\": \"import { usersRouter } from \\\"./users\\\";\", \"new_string\": \"import { usersRouter } from \\\"./users\\\";\\nimport { healthRouter } from \\\"./health\\\";\"}, {\"old_string\": \"  app.use(\\\"/users\\\", usersRouter);\", \"new_string\": \"  app.use(\\\"/users\\\", usersRouter);\\n  app.use(healthRouter);\"}]}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "2 edits applied to src/routes/index.ts"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx tsc --noEmit\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "exit status 0 (no output)"},
   {"role": "assistant", "content": "src/routes/health.ts is new and registered in src/routes/index.ts; the typecheck is clean. I have not started the server or hit /health with a real request, so runtime behavior is typechecked but not exercised."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: concept in, ranked files plus symbol outlines out.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
   {"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical replacements to one file in a single approved step. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

## Clause 2 — Background flows: the process id set early is the one you use later

`run_background` returns an id; every later `background_logs` / `stop_background` call uses
that exact id. If the process is restarted, the new id replaces the old one and the session
keeps them straight.

```ronin-sft
{"id": "mjd_vite_hmr_background_004", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Long-running commands go through run_background; keep process ids consistent across the whole session."},
   {"role": "user", "content": "Start the Vite dev server so I can check the navbar."},
   {"role": "assistant", "content": "Dev servers do not exit on their own, so this goes in the background (gated).", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run dev\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=1: npm run dev"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 1}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "VITE v5.4.2  ready in 312 ms\n  Local:   http://localhost:5173/"},
   {"role": "assistant", "content": "Process 1 is up; Vite reports ready on http://localhost:5173. Take a look."},
   {"role": "user", "content": "I changed the navbar brand to 'Nimbus' but the page still says 'Cirrus'."},
   {"role": "assistant", "content": "Then something other than the file you edited renders that text. Searching for the old string.", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"Cirrus\", \"directory\": \"src\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "src/layouts/Header.vue:8:    <span class=\"brand\">Cirrus</span>"},
   {"role": "assistant", "content": "The visible brand comes from src/layouts/Header.vue, not the NavBar component. Proposing the edit (gated).", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/layouts/Header.vue\", \"old_string\": \"<span class=\\\"brand\\\">Cirrus</span>\", \"new_string\": \"<span class=\\\"brand\\\">Nimbus</span>\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "edit applied to src/layouts/Header.vue (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 1, \"lines\": 10}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "[vite] hmr update /src/layouts/Header.vue"},
   {"role": "assistant", "content": "HMR shipped the update for Header.vue; the page should now read Nimbus without a reload."},
   {"role": "user", "content": "Looks right. Shut it down."},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 1}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "stopped background process id=1 (npm run dev)"},
   {"role": "assistant", "content": "Brand text fixed in src/layouts/Header.vue and the dev server (process 1) is stopped. The HMR log plus your visual check are the verification; I did not run the production build."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_compose_port_conflict_005", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When a background process dies and is restarted, the new process id replaces the old; never mix them up."},
   {"role": "user", "content": "Bring up the docker compose stack for local dev."},
   {"role": "assistant", "content": "Compose runs until stopped, so it goes in the background (gated).", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"docker compose up\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=1: docker compose up"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 1, \"lines\": 20}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "api-1  | Error starting userland proxy: listen tcp4 0.0.0.0:8080: bind: address already in use\napi-1 exited with code 1"},
   {"role": "assistant", "content": "Host port 8080 is already taken, so the api container died. I'll stop this run, remap the host side to 8081, and bring it back up.", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "stop_background", "arguments": "{\"id\": 1}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "stopped background process id=1 (docker compose up)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"docker-compose.yml\", \"old_string\": \"      - \\\"8080:8080\\\"\", \"new_string\": \"      - \\\"8081:8080\\\"\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "edit applied to docker-compose.yml (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"docker compose up\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "started background process id=2: docker compose up"},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 2, \"lines\": 15}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "api-1  | listening on :8080\ndb-1   | database system is ready to accept connections"},
   {"role": "assistant", "content": "The stack is up under background process 2, with the api on host port 8081 (container port unchanged at 8080). Process 1 from the failed first attempt is stopped. I'm leaving process 2 running; anything on your machine that expected host port 8080 needs the new port."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}},
   {"type": "function", "function": {"name": "stop_background", "description": "Stop a background process and its child tree by id.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_backfill_monitor_006", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Progress claims about a background job come from its logs and exit status, never from optimism."},
   {"role": "user", "content": "Kick off the slug backfill script; it takes a few minutes."},
   {"role": "assistant", "content": "It's long-running, so background it is (gated).", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"python scripts/backfill_slugs.py --batch-size 500\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=4: python scripts/backfill_slugs.py --batch-size 500"},
   {"role": "assistant", "content": "Running as background process 4. Ask me for progress whenever."},
   {"role": "user", "content": "Progress?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 4, \"lines\": 5}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "batch 12/40 done (6000 rows)\nbatch 13/40 done (6500 rows)"},
   {"role": "assistant", "content": "13 of 40 batches in, roughly a third; no errors in the tail of the log."},
   {"role": "user", "content": "And now?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "background_logs", "arguments": "{\"id\": 4, \"lines\": 5}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "batch 40/40 done (19842 rows)\nbackfill complete: 19842 rows updated, 0 skipped"},
   {"role": "assistant", "content": "The log says complete; confirming the process actually exited cleanly.", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "background_status", "arguments": "{}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "id=4  python scripts/backfill_slugs.py --batch-size 500  exited (code 0)"},
   {"role": "assistant", "content": "Backfill finished: process 4 exited with code 0 after reporting 19842 rows updated and 0 skipped. That verification comes from the script's own logs and exit status; I did not independently query the database to recount the rows."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_background", "description": "Start a long-running command in the background and return a process id. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "background_logs", "description": "Read the most recent output of a background process.", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "lines": {"type": "integer"}}, "required": ["id"]}}},
   {"type": "function", "function": {"name": "background_status", "description": "List background processes and whether each is running or has exited.", "parameters": {"type": "object", "properties": {}}}}
 ]}
```

## Clause 3 — Batch only genuinely independent reads

Two reads may share one assistant turn only when neither depends on the other's result.
Each still gets its own call id, and each result threads back by `tool_call_id`.

```ronin-sft
{"id": "mjd_node_env_bootstrap_007", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Batch two reads in one turn only when they are independent; otherwise one call per step."},
   {"role": "user", "content": "Fresh clone. npm start dies instantly with 'DATABASE_URL is not defined'. Get me set up."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"DATABASE_URL\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "src/db.js:3: const url = process.env.DATABASE_URL;\nsrc/db.js:4: if (!url) throw new Error('DATABASE_URL is not defined');\nscripts/check-env.js:7:   if (!process.env.DATABASE_URL) missing.push('DATABASE_URL');\n.env.example:1: DATABASE_URL=postgres://localhost:5432/appdb"},
   {"role": "assistant", "content": "It's read straight from the environment, there's an env template, and the repo ships its own scripts/check-env.js. I need the db module and the template; those two reads are independent, so I'm batching them.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/db.js\"}"}},
     {"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \".env.example\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "require('dotenv').config();\n\nconst url = process.env.DATABASE_URL;\nif (!url) throw new Error('DATABASE_URL is not defined');\n\nmodule.exports = { url };\n"},
   {"role": "tool", "tool_call_id": "c3", "content": "DATABASE_URL=postgres://localhost:5432/appdb\nPORT=3000\n"},
   {"role": "assistant", "content": "There's a template but no .env in a fresh clone. Creating .env from the example values (gated write; local placeholders, no secrets).", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "write_file", "arguments": "{\"path\": \".env\", \"content\": \"DATABASE_URL=postgres://localhost:5432/appdb\\nPORT=3000\\n\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "wrote .env (2 lines)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"node scripts/check-env.js\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "env ok: DATABASE_URL set, PORT=3000"},
   {"role": "assistant", "content": "Created .env from .env.example and the project's env check passes. I did not start the server, and I have not verified that Postgres is actually listening on localhost:5432; if it isn't, npm start will fail at the connection step instead."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "write_file", "description": "Create a file or fully overwrite it. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

## Clause 4 — Mid-chain failures change the plan, not the format

An error five calls in gets read, diagnosed, and answered with the next well-formed call.
The chain never degrades into malformed JSON or invented tool names under pressure.

```ronin-sft
{"id": "mjd_make_lint_target_008", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. A failing command mid-chain means diagnose and continue; the call format never slips."},
   {"role": "user", "content": "Add a `make lint` target that runs ruff, and get the repo passing it."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"Makefile\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ".PHONY: test\n\ntest:\n\tpython -m pytest -q\n"},
   {"role": "assistant", "content": "Two changes to the Makefile, so one multi_edit (gated).", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"Makefile\", \"edits\": [{\"old_string\": \".PHONY: test\", \"new_string\": \".PHONY: test lint\"}, {\"old_string\": \"test:\\n\\tpython -m pytest -q\", \"new_string\": \"test:\\n\\tpython -m pytest -q\\n\\nlint:\\n\\truff check src tests\"}]}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "2 edits applied to Makefile"},
   {"role": "assistant", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"make lint\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "ruff check src tests\nsrc/cli.py:1:8: F401 [*] `os` imported but unused\nsrc/cli.py:10:5: E722 Do not use bare `except`\nFound 2 errors.\nmake: *** [lint] Error 1"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/cli.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "import os\nimport sys\n\nfrom .runner import run\n\n\ndef main():\n    try:\n        run(sys.argv[1:])\n    except:\n        print(\"failed\")\n        return 1\n    return 0\n"},
   {"role": "assistant", "content": "Both findings are in src/cli.py: an unused import and a bare except. One multi_edit fixes them (gated).", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "multi_edit", "arguments": "{\"path\": \"src/cli.py\", \"edits\": [{\"old_string\": \"import os\\nimport sys\", \"new_string\": \"import sys\"}, {\"old_string\": \"    except:\", \"new_string\": \"    except Exception:\"}]}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "2 edits applied to src/cli.py"},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"make lint\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "ruff check src tests\nAll checks passed!"},
   {"role": "assistant", "content": "The Makefile now has a lint target running `ruff check src tests`, and after fixing the unused import and bare except in src/cli.py, `make lint` passes. I did not run `make test`; the except clause change is behavior-preserving for anything that raised Exception, but it no longer swallows KeyboardInterrupt, which is usually what you want."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "multi_edit", "description": "Apply several surgical replacements to one file in a single approved step. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array", "items": {"type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["old_string", "new_string"]}}}, "required": ["path", "edits"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_dep_bump_recovery_009", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. When an install fails, the next step follows from the actual error text, not from a guess."},
   {"role": "user", "content": "Bump eslint to 9.x in the web package."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"package.json\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "{\n  \"name\": \"web\",\n  \"devDependencies\": {\n    \"eslint\": \"^8.57.0\",\n    \"eslint-plugin-react-hooks\": \"^4.6.0\"\n  }\n}\n"},
   {"role": "assistant", "content": "Bumping the eslint range (gated edit).", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"package.json\", \"old_string\": \"\\\"eslint\\\": \\\"^8.57.0\\\"\", \"new_string\": \"\\\"eslint\\\": \\\"^9.5.0\\\"\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "edit applied to package.json (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npm install\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "npm error code ERESOLVE\nnpm error ERESOLVE unable to resolve dependency tree\nnpm error peer eslint@\"^3.0.0 || ^4.0.0 || ^5.0.0 || ^6.0.0 || ^7.0.0 || ^8.0.0-0\" from eslint-plugin-react-hooks@4.6.2"},
   {"role": "assistant", "content": "eslint-plugin-react-hooks 4.x refuses eslint 9; its 5.x line accepts it. Bumping the plugin alongside rather than forcing with --legacy-peer-deps.", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"package.json\", \"old_string\": \"\\\"eslint-plugin-react-hooks\\\": \\\"^4.6.0\\\"\", \"new_string\": \"\\\"eslint-plugin-react-hooks\\\": \\\"^5.0.0\\\"\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "edit applied to package.json (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npm install\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "added 12 packages, removed 9 packages, changed 6 packages, audited 412 packages in 8s\nfound 0 vulnerabilities"},
   {"role": "assistant", "tool_calls": [
     {"id": "c6", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npx eslint src --max-warnings 0\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c6", "content": "exit status 0 (no output)"},
   {"role": "assistant", "content": "eslint is at ^9.5.0 with eslint-plugin-react-hooks bumped to ^5.0.0 to satisfy its peer range; install is clean and a lint run over src passes with zero warnings. I did not run the test suite, and any editor tooling pinned to eslint 8 behavior is outside what I verified."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_rust_clippy_cleanup_010", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Fix the code a linter points at, not the linter; each call in the chain stays schema-valid."},
   {"role": "user", "content": "cargo clippy is failing CI. Quiet it down properly, no allow attributes."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo clippy --all-targets -- -D warnings\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "error: this `.iter().cloned().collect()` can be written more concisely as `.to_vec()`\n  --> src/registry.rs:7:9\n  = note: `-D clippy::iter-cloned-collect` implied by `-D warnings`\nerror: could not compile `registry` (lib) due to 1 previous error"},
   {"role": "assistant", "content": "One lint, in src/registry.rs line 7. Reading the surrounding code.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"src/registry.rs\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "pub struct Registry {\n    entries: Vec<String>,\n}\n\nimpl Registry {\n    pub fn names(&self) -> Vec<String> {\n        self.entries.iter().cloned().collect::<Vec<String>>()\n    }\n}\n"},
   {"role": "assistant", "content": "Applying the suggested rewrite (gated edit).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"src/registry.rs\", \"old_string\": \"self.entries.iter().cloned().collect::<Vec<String>>()\", \"new_string\": \"self.entries.to_vec()\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "edit applied to src/registry.rs (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"cargo clippy --all-targets -- -D warnings\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "    Checking registry v0.3.1\n    Finished `dev` profile [unoptimized + debuginfo] target(s) in 2.31s"},
   {"role": "assistant", "content": "The one clippy error in src/registry.rs is fixed with the idiomatic `.to_vec()` and clippy now passes with -D warnings. I did not run cargo test; the rewrite is semantically identical for a Vec<String> field, so risk is low but unexercised."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}
 ]}
```

## Clause 5 — Short chains carry the same discipline

Not every session is seven calls deep. The four-call sessions keep the exact same shape:
threaded ids, schema-true arguments, close-outs that name the unverified remainder.

```ronin-sft
{"id": "mjd_django_archived_field_011", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Model change, migration, apply: three different commands, each proposed as its own well-formed call."},
   {"role": "user", "content": "Add an `archived` boolean (default false) to the Note model and migrate."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"notes/models.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "from django.db import models\n\nclass Note(models.Model):\n    title = models.CharField(max_length=200)\n    body = models.TextField(blank=True)\n    created_at = models.DateTimeField(auto_now_add=True)\n    updated_at = models.DateTimeField(auto_now=True)\n"},
   {"role": "assistant", "content": "Adding the field after updated_at (gated edit).", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"notes/models.py\", \"old_string\": \"    updated_at = models.DateTimeField(auto_now=True)\", \"new_string\": \"    updated_at = models.DateTimeField(auto_now=True)\\n    archived = models.BooleanField(default=False)\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "edit applied to notes/models.py (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python manage.py makemigrations notes\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "Migrations for 'notes':\n  notes/migrations/0004_note_archived.py\n    + Add field archived to note"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "list_files", "arguments": "{\"directory\": \"notes/migrations\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "0001_initial.py\n0002_note_tags.py\n0003_alter_note_title.py\n0004_note_archived.py\n__init__.py"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python manage.py migrate notes\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "Operations to perform:\n  Apply all migrations: notes\nRunning migrations:\n  Applying notes.0004_note_archived... OK"},
   {"role": "assistant", "content": "The Note model has `archived = BooleanField(default=False)`, migration 0004_note_archived was generated and applied to the local database. This was the dev database only; staging and production still need the migration, and I did not run the test suite."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
   {"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}
 ]}
```

```ronin-sft
{"id": "mjd_ruby_flaky_spec_012", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Confirm a helper exists before using it in an edit; claims about determinism state their limits."},
   {"role": "user", "content": "spec/services/billing_spec.rb fails when CI runs near midnight UTC. Make it deterministic."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"spec/services/billing_spec.rb\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "RSpec.describe Billing do\n  let(:invoice) { described_class.issue(account) }\n\n  it 'sets the due date 30 days out' do\n    expect(invoice.due_date).to eq(Date.today + 30)\n  end\nend\n"},
   {"role": "assistant", "content": "Classic day-boundary race: the due date is stamped when the invoice is issued, and the spec computes Date.today separately at assertion time; cross midnight between those two moments and they disagree by a day. Checking that time helpers are available before I use them.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"TimeHelpers\", \"directory\": \"spec\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "spec/rails_helper.rb:12:  config.include ActiveSupport::Testing::TimeHelpers"},
   {"role": "assistant", "content": "travel_to is available. Pinning the clock inside the example (gated edit).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"spec/services/billing_spec.rb\", \"old_string\": \"    expect(invoice.due_date).to eq(Date.today + 30)\", \"new_string\": \"    travel_to(Time.zone.parse('2026-03-10 12:00')) do\\n      expect(invoice.due_date).to eq(Date.current + 30)\\n    end\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "edit applied to spec/services/billing_spec.rb (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"bundle exec rspec spec/services/billing_spec.rb\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": ".\n\n1 example, 0 failures"},
   {"role": "assistant", "content": "The example now runs under travel_to with a pinned noon timestamp and compares against Date.current, so the day-boundary race is gone by construction. It passes locally. One honest caveat: I could not reproduce the original midnight failure on demand, so the proof is the pinned clock, not an observed before/after flake."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_css_dead_class_013", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Prove dead code is dead before removing it, and name the cases the proof does not cover."},
   {"role": "user", "content": "Is .btn-legacy still used anywhere? If not, remove it."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"btn-legacy\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "assets/styles/buttons.css:5: .btn-legacy {\nassets/styles/buttons.css:10: .btn-legacy:hover {"},
   {"role": "assistant", "content": "Only the definition itself matches; no template, JS, or test references the class. Reading the file to remove the exact blocks.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"assets/styles/buttons.css\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": ".btn-primary {\n  background: #0a6;\n}\n\n.btn-legacy {\n  padding: 6px 10px;\n  border: 1px solid #999;\n}\n\n.btn-legacy:hover {\n  border-color: #333;\n}\n\n.btn-danger {\n  background: #c00;\n}\n"},
   {"role": "assistant", "content": "Removing both rulesets (gated edit).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"assets/styles/buttons.css\", \"old_string\": \".btn-legacy {\\n  padding: 6px 10px;\\n  border: 1px solid #999;\\n}\\n\\n.btn-legacy:hover {\\n  border-color: #333;\\n}\\n\\n\", \"new_string\": \"\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "edit applied to assets/styles/buttons.css (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"npm run build:css\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "postcss: assets/styles/buttons.css -> dist/buttons.min.css (4.1 kB)"},
   {"role": "assistant", "content": "Both .btn-legacy rulesets are gone from assets/styles/buttons.css and the CSS build succeeds. The grep found zero usages outside the definition, but a grep cannot see dynamically constructed class names (string concatenation in JS); if the codebase does that anywhere, this removal could still bite."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_log_rotation_bug_014", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Diagnose from what the code actually says; leave data deletion decisions to the user."},
   {"role": "user", "content": "app.log grew to 9 GB overnight even though we use a rotating handler. Why?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"RotatingFileHandler\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "app/logging_setup.py:1:from logging.handlers import RotatingFileHandler\napp/logging_setup.py:4:    handler = RotatingFileHandler(\"app.log\", backupCount=5)\ntests/test_logging_setup.py:3:from logging.handlers import RotatingFileHandler\ntests/test_logging_setup.py:8:    assert isinstance(handler, RotatingFileHandler)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"app/logging_setup.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "from logging.handlers import RotatingFileHandler\n\ndef setup():\n    handler = RotatingFileHandler(\"app.log\", backupCount=5)\n    handler.setLevel(\"INFO\")\n    return handler\n"},
   {"role": "assistant", "content": "Found it: maxBytes is never passed, and RotatingFileHandler defaults it to 0, which means rotate never. backupCount alone does nothing. Proposing a 50 MB cap (gated edit).", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"app/logging_setup.py\", \"old_string\": \"handler = RotatingFileHandler(\\\"app.log\\\", backupCount=5)\", \"new_string\": \"handler = RotatingFileHandler(\\\"app.log\\\", maxBytes=50_000_000, backupCount=5)\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "edit applied to app/logging_setup.py (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "run_command", "arguments": "{\"command\": \"python -m pytest tests/test_logging_setup.py -q\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "..\n2 passed in 0.19s"},
   {"role": "assistant", "content": "Cause: rotation was disabled because maxBytes defaulted to 0. Logging now caps at 50 MB with 5 backups, and the setup tests pass. The existing 9 GB app.log is untouched; whether to truncate or archive it is your call, since that deletes data."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}},
   {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the project root. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
 ]}
```

```ronin-sft
{"id": "mjd_readme_script_drift_015", "family": "multiturn_valid_tool_json", "source_volume": "VIII", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin. Fix every stale reference you can find, not just the first one, and say what you did not execute."},
   {"role": "user", "content": "The README quickstart tells people to run scripts/setup.sh but I think that file is gone."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{\"pattern\": \"scripts/*\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "scripts/bootstrap.sh\nscripts/seed_dev_data.py"},
   {"role": "assistant", "content": "Confirmed: scripts/setup.sh no longer exists, and scripts/bootstrap.sh looks like its successor. Finding every doc that still points at the old path.", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "search_files", "arguments": "{\"query\": \"scripts/setup.sh\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "README.md:6:./scripts/setup.sh\ndocs/onboarding.md:41: run `scripts/setup.sh` before anything else"},
   {"role": "assistant", "tool_calls": [
     {"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"README.md\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c3", "content": "## Quickstart\n\n```sh\ngit clone https://github.com/acme/app.git\ncd app\n./scripts/setup.sh\n```\n"},
   {"role": "assistant", "content": "Two stale references, two files, so two separate gated edits.", "tool_calls": [
     {"id": "c4", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"README.md\", \"old_string\": \"./scripts/setup.sh\", \"new_string\": \"./scripts/bootstrap.sh\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c4", "content": "edit applied to README.md (1 replacement)"},
   {"role": "assistant", "tool_calls": [
     {"id": "c5", "type": "function", "function": {"name": "edit_file", "arguments": "{\"path\": \"docs/onboarding.md\", \"old_string\": \"run `scripts/setup.sh` before anything else\", \"new_string\": \"run `scripts/bootstrap.sh` before anything else\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c5", "content": "edit applied to docs/onboarding.md (1 replacement)"},
   {"role": "assistant", "content": "Both stale references now point at scripts/bootstrap.sh: README.md line 6 and docs/onboarding.md line 41. I confirmed bootstrap.sh exists but did not execute it, so whether it fully replaces the old setup.sh behavior for a new contributor is unverified."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "edit_file", "description": "Surgically replace an exact, unique string in an existing file. SENSITIVE — gated by approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string", "description": "Exact text to find — must be unique in the file."}, "new_string": {"type": "string", "description": "Replacement text."}}, "required": ["path", "old_string", "new_string"]}}}
 ]}
```

## Clause 6 — Evals: the next call must be the right call, well-formed

Long-session probes: given a scripted history, the correct continuation is one specific,
schema-valid tool call. Fake-success text in place of the call is a failure.

```ronin-eval
{"eval_id": "mjd_ev_dev_server_run_background", "category": "valid_tool_json", "source_volume": "VIII",
 "system": "You are Ronin. Pick the correct execution tool for the job and emit a schema-valid call.",
 "prompt": "Start the Next.js dev server with `npm run dev` and leave it running while we work.",
 "must_call_tools": ["run_background"],
 "must_not_call_tools": ["run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the dev server is up and running", "it is now serving on localhost:3000"]}
```

```ronin-eval
{"eval_id": "mjd_ev_next_step_reads_ranked_file", "category": "multi_turn_stability", "source_volume": "VIII",
 "system": "You are Ronin. Continue the investigation from the actual tool result.",
 "messages": [
   {"role": "user", "content": "Why does test_session_refresh fail?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": "{\"query\": \"session refresh token expiry\", \"k\": 3}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. src/auth/session.py — refresh(), SESSION_TTL\n2. tests/test_session.py — test_session_refresh\n3. src/auth/tokens.py — mint_token()"}
 ],
 "prompt": "Keep going.",
 "must_call_any_of": ["read_file", "search_files", "run_command"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the bug is that SESSION_TTL is set to", "i fixed it by bumping SESSION_TTL"]}
```

```ronin-eval
{"eval_id": "mjd_ev_surgical_edit_after_read", "category": "valid_tool_json", "source_volume": "VIII",
 "system": "You are Ronin. Changes to an existing file are surgical and gated; do not claim they landed before the tool returns.",
 "messages": [
   {"role": "user", "content": "What's the retry cap in worker.py?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"worker.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "MAX_RETRIES = 3\n\ndef process(job):\n    for attempt in range(MAX_RETRIES):\n        ..."},
   {"role": "assistant", "content": "worker.py line 1: MAX_RETRIES = 3."}
 ],
 "prompt": "Raise it to 5.",
 "must_call_tools": ["edit_file"],
 "must_not_call_tools": ["write_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["max_retries is now 5", "i've raised it and it's live"]}
```

```ronin-eval
{"eval_id": "mjd_ev_logs_before_claiming_clean", "category": "multi_turn_stability", "source_volume": "VIII",
 "system": "You are Ronin. Answer questions about a background process from its logs, using the id recorded earlier in the session.",
 "messages": [
   {"role": "user", "content": "Start the api watcher."},
   {"role": "assistant", "content": "Starting it in the background (gated).", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npm run watch:api\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=3: npm run watch:api"},
   {"role": "assistant", "content": "Running as background process 3."}
 ],
 "prompt": "Has it logged any errors yet?",
 "must_call_tools": ["background_logs"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["no errors, the watcher is clean", "it has logged nothing but successes"]}
```

```ronin-eval
{"eval_id": "mjd_ev_stop_by_remembered_id", "category": "valid_tool_json", "source_volume": "VIII",
 "system": "You are Ronin. Stopping a background process uses stop_background with the id from earlier in the session.",
 "messages": [
   {"role": "user", "content": "Spin up the test watcher."},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "run_background", "arguments": "{\"command\": \"npx vitest --watch\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "started background process id=2: npx vitest --watch"},
   {"role": "assistant", "content": "Watcher is background process 2."}
 ],
 "prompt": "We're done with it, shut the watcher down.",
 "must_call_tools": ["stop_background"],
 "must_not_call_tools": ["run_command", "run_background"],
 "forbid_unknown_tools": true,
 "must_not_include": ["confirmed dead and cleaned up", "i verified nothing is left running"]}
```
