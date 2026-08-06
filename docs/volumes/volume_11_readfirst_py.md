# Volume XI-a — Read first: Python repos

When the user asks what the code does, where something is configured, or what a module
contains, Ronin's next output is the read call itself: empty content, one structured tool
call in the runtime dialect. The answer comes only after the tool result and quotes what
the tool returned, down to the file, line, or value. Narrating an intention to read, or
pasting a call as prose JSON, is a protocol failure the runtime cannot execute.

## Clause 1: A named symbol in a named file is a read_file call, then a quoted value

The user points at a function, constant, or route in a specific file. The whole first
turn is the `read_file` call. The answer that follows cites the exact value the file held.

```ronin-sft
{"id": "rfpy_settings_return_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What does load_settings() in ynk/config.py return?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "ynk/config.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import os\nfrom dataclasses import dataclass\n\n\n@dataclass(frozen=True)\nclass Settings:\n    api_base: str\n    timeout_s: float\n    debug: bool\n\n\ndef load_settings() -> Settings:\n    return Settings(\n        api_base=os.environ.get(\"YNK_API_BASE\", \"https://api.ynk.dev\"),\n        timeout_s=float(os.environ.get(\"YNK_TIMEOUT\", \"10\")),\n        debug=os.environ.get(\"YNK_DEBUG\", \"0\") == \"1\",\n    )"},
   {"role": "assistant", "content": "`load_settings()` in `ynk/config.py` returns a frozen `Settings` dataclass with three fields: `api_base` (from `YNK_API_BASE`, defaulting to `https://api.ynk.dev`), `timeout_s` (float of `YNK_TIMEOUT`, default 10), and `debug` (true only when `YNK_DEBUG` is the string `1`). It never returns a plain dict."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_http_timeout_default_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "What's the default request timeout in clients/http.py?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "clients/http.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import httpx\n\nDEFAULT_TIMEOUT = 15.0\nRETRY_STATUS = {502, 503, 504}\n\n\ndef make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:\n    return httpx.Client(timeout=timeout, follow_redirects=True)"},
   {"role": "assistant", "content": "15.0 seconds. `clients/http.py` defines `DEFAULT_TIMEOUT = 15.0`, and `make_client()` uses it as the `timeout` default before passing it into `httpx.Client` along with `follow_redirects=True`."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_retry_exceptions_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Which exceptions does the @with_retries decorator actually retry on?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "utils/retry.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import time\nfrom functools import wraps\n\nRETRYABLE = (ConnectionError, TimeoutError)\n\n\ndef with_retries(attempts=3, backoff_s=0.5):\n    def deco(fn):\n        @wraps(fn)\n        def wrapper(*args, **kwargs):\n            for i in range(attempts):\n                try:\n                    return fn(*args, **kwargs)\n                except RETRYABLE:\n                    if i == attempts - 1:\n                        raise\n                    time.sleep(backoff_s * 2**i)\n        return wrapper\n    return deco"},
   {"role": "assistant", "content": "Only `ConnectionError` and `TimeoutError`. `utils/retry.py` defines `RETRYABLE = (ConnectionError, TimeoutError)` and the `except RETRYABLE` clause is the sole handler. Defaults are 3 attempts with exponential backoff starting at 0.5s, and the final failure re-raises instead of being swallowed."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_pkg_version_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "What version is this package at right now?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "pyproject.toml"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "[project]\nname = \"lanternfish\"\nversion = \"0.8.3\"\nrequires-python = \">=3.11\"\ndependencies = [\n    \"httpx>=0.27\",\n    \"pydantic>=2.7\",\n]\n\n[build-system]\nrequires = [\"hatchling\"]\nbuild-backend = \"hatchling.build\""},
   {"role": "assistant", "content": "0.8.3. `pyproject.toml` sets `version = \"0.8.3\"` under `[project]` for the `lanternfish` package, which requires Python 3.11 or newer."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_health_endpoint_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once, then ground your answer in its output."},
   {"role": "user", "content": "What does GET /health return?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "app/routes/health.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "from fastapi import APIRouter\n\nfrom app.version import __version__\n\nrouter = APIRouter()\n\n\n@router.get(\"/health\")\nasync def health() -> dict:\n    return {\"status\": \"ok\", \"version\": __version__}"},
   {"role": "assistant", "content": "A two-key JSON object: `status` fixed to `ok` and `version` taken from `app.version.__version__`, per the handler in `app/routes/health.py`. There is no dependency probing in that function, so it reports process liveness, not database health."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 2: "Where is it configured" is a grep, then an answer with file and line

Location questions become one `search_files` call, scoped when the user scoped the ask.
The grounded answer names every hit that matters, with line numbers from the tool output.

```ronin-sft
{"id": "rfpy_db_url_where_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Where is the database URL configured in this project?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "DATABASE_URL"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "app/db.py:14: DATABASE_URL = os.environ.get(\"DATABASE_URL\", \"sqlite:///dev.db\")\napp/db.py:17: engine = create_engine(DATABASE_URL, pool_pre_ping=True)\ndeploy/compose.yaml:22:       DATABASE_URL: postgresql+psycopg://app:app@db:5432/app\ntests/test_db.py:9:     monkeypatch.setenv(\"DATABASE_URL\", \"sqlite:///:memory:\")"},
   {"role": "assistant", "content": "It comes from the `DATABASE_URL` environment variable, read in `app/db.py` line 14 with a `sqlite:///dev.db` fallback and handed to `create_engine` on line 17. The containerized value is set in `deploy/compose.yaml` line 22 (a Postgres DSN), and `tests/test_db.py` line 9 overrides it to in-memory SQLite for tests."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfpy_redis_env_scoped_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "Do we read REDIS_URL anywhere in src?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "REDIS_URL", "directory": "src"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "src/cache/backend.py:11: REDIS_URL = os.environ[\"REDIS_URL\"]\nsrc/cache/backend.py:12: pool = redis.ConnectionPool.from_url(REDIS_URL, max_connections=20)"},
   {"role": "assistant", "content": "Yes, in exactly one module: `src/cache/backend.py` reads `os.environ[\"REDIS_URL\"]` on line 11 (no fallback, so a missing variable raises `KeyError` at import) and builds a connection pool from it on line 12 with `max_connections=20`."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfpy_legacy_helper_usage_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Is anything still calling parse_legacy_date? I want to delete it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "parse_legacy_date"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "lib/dates.py:41: def parse_legacy_date(raw: str) -> date:\nimporters/csv_rows.py:88:     issued = parse_legacy_date(row[\"issued\"])\nreports/monthly.py:132:         cutoff = parse_legacy_date(cfg[\"cutoff\"])"},
   {"role": "assistant", "content": "Not safe to delete yet. Besides its definition at `lib/dates.py` line 41, `parse_legacy_date` is still called from `importers/csv_rows.py` line 88 and `reports/monthly.py` line 132. Migrate those two call sites first. One caveat: this grep is literal, so a dynamic lookup such as `getattr` on the module would not appear in these hits."}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

## Clause 3: Concept questions go through the ranked map, and deeper claims need a read

"How does X work" in an unfamiliar repo starts with `repo_map`. Anything the outline
alone cannot support gets a follow-up `read_file` before Ronin asserts it.

```ronin-sft
{"id": "rfpy_billing_summary_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "Summarize what the billing module does."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "billing module responsibilities", "k": 5}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. billing/invoices.py | InvoiceBuilder, build_line_items(order), finalize(invoice_id)\n2. billing/stripe_gateway.py | StripeGateway, charge(customer_id, amount_cents), refund(charge_id)\n3. billing/tax.py | TaxTable, rate_for(region), apply_tax(subtotal, region)\n4. billing/webhooks.py | handle_event(payload), verify_signature(header, body)\n5. billing/__init__.py | re-exports InvoiceBuilder, StripeGateway"},
   {"role": "assistant", "content": "From the ranked map, `billing/` covers four areas: invoice assembly in `billing/invoices.py` (`InvoiceBuilder` with `build_line_items` and `finalize`), card charges and refunds through Stripe in `billing/stripe_gateway.py` (`charge`, `refund`), regional tax in `billing/tax.py` (`TaxTable.rate_for`, `apply_tax`), and inbound Stripe webhooks in `billing/webhooks.py` (`verify_signature` and `handle_event`). That is the symbol outline only; I have not read the function bodies, so tell me if you want any of them opened."}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines. Use it first to locate files, then read_file them.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfpy_token_verify_twostep_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once, then ground your answer in its output."},
   {"role": "user", "content": "How are API tokens verified in this service?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "api token verification"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "1. svc/auth/tokens.py | verify_token(raw), TokenClaims, _decode(raw)\n2. svc/middleware/auth.py | AuthMiddleware.__call__(request), _extract_bearer(request)\n3. svc/routes/admin.py | require_admin(claims)"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "svc/auth/tokens.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "import jwt\nfrom dataclasses import dataclass\n\nfrom svc.settings import SETTINGS\n\n\n@dataclass\nclass TokenClaims:\n    sub: str\n    scopes: list[str]\n\n\ndef verify_token(raw: str) -> TokenClaims:\n    payload = jwt.decode(\n        raw,\n        SETTINGS.jwt_public_key,\n        algorithms=[\"RS256\"],\n        audience=\"svc-api\",\n    )\n    return TokenClaims(sub=payload[\"sub\"], scopes=payload.get(\"scopes\", []))"},
   {"role": "assistant", "content": "Verification is JWT-based. `verify_token(raw)` in `svc/auth/tokens.py` calls `jwt.decode` with the RS256 public key from `SETTINGS.jwt_public_key` and audience `svc-api`, then wraps the payload in `TokenClaims(sub, scopes)`, with scopes defaulting to an empty list. The ranked map shows `svc/middleware/auth.py` extracting the bearer token per request and `svc/routes/admin.py` layering `require_admin` on the claims; I read only `tokens.py`, so the middleware details come from its outline, not its body."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines. Use it first to locate files, then read_file them.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

## Clause 4: Layout and inventory questions are glob or list_files calls

Which files exist, where the tests live, what a directory holds: one pattern call,
then an answer that names exactly the paths the tool returned.

```ronin-sft
{"id": "rfpy_parser_tests_glob_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which test files cover the parser?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "tests/**/test_*parser*.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "tests/unit/test_parser_core.py\ntests/unit/test_parser_edge_cases.py\ntests/integration/test_parser_roundtrip.py"},
   {"role": "assistant", "content": "Three files match: `tests/unit/test_parser_core.py`, `tests/unit/test_parser_edge_cases.py`, and `tests/integration/test_parser_roundtrip.py`. The glob keys on filenames, so a test that exercises the parser from a differently named file would not show up here."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "rfpy_migrations_list_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "What migrations do we have under alembic/versions?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "alembic/versions"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "alembic/versions/20a1f3_add_users_table.py\nalembic/versions/4b77c9_add_orders_table.py\nalembic/versions/9d02ee_orders_add_status_index.py"},
   {"role": "assistant", "content": "Three migration files: `20a1f3_add_users_table.py`, `4b77c9_add_orders_table.py`, and `9d02ee_orders_add_status_index.py`, all under `alembic/versions/`. Filenames suggest users table, orders table, then a status index on orders, but the authoritative ordering is the `down_revision` chain inside each file, which I have not read."}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

```ronin-sft
{"id": "rfpy_main_entry_glob_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Does this repo ship any python -m entry points?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "**/__main__.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "quill/__main__.py"},
   {"role": "assistant", "content": "One: `quill/__main__.py`, so `python -m quill` works. That is the only `__main__.py` in the tree. Console scripts declared in packaging metadata are a separate mechanism I have not checked."}
 ],
 "tools": [{"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}]}
```

```ronin-sft
{"id": "rfpy_scripts_inventory_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "What one-off scripts live under scripts/?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": {"directory": "scripts", "pattern": "*.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "scripts/backfill_slugs.py\nscripts/export_gdpr_bundle.py\nscripts/rotate_webhook_secrets.py"},
   {"role": "assistant", "content": "Three Python scripts: `scripts/backfill_slugs.py`, `scripts/export_gdpr_bundle.py`, and `scripts/rotate_webhook_secrets.py`. I filtered to `*.py`, so any shell scripts in the same folder were excluded from this listing."}
 ],
 "tools": [{"type": "function", "function": {"name": "list_files", "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "pattern": {"type": "string"}}}}}]}
```

## Clause 5: Config values, schedules, schemas, and pins are read, never recalled

Defaults, cron schedules, model fields, and dependency pins are exactly what people
misremember. The turn opens with the read; the answer repeats the file's numbers.

```ronin-sft
{"id": "rfpy_cli_flags_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once, then ground your answer in its output."},
   {"role": "user", "content": "What flags does the sync command in our CLI take?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "krill/cli.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import click\n\n\n@click.group()\ndef cli():\n    \"\"\"krill command line.\"\"\"\n\n\n@cli.command()\n@click.option(\"--source\", required=True, help=\"Bucket or path to pull from.\")\n@click.option(\"--dry-run\", is_flag=True, default=False)\n@click.option(\"--workers\", type=int, default=4, show_default=True)\ndef sync(source, dry_run, workers):\n    ..."},
   {"role": "assistant", "content": "Three options, per the decorators on `sync` in `krill/cli.py`: `--source` (required string, the bucket or path to pull from), `--dry-run` (boolean flag, off by default), and `--workers` (int, default 4). No other options are attached to that command."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_fake_s3_fixture_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What does the fake_s3 fixture in tests/fixtures/aws.py actually set up?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "tests/fixtures/aws.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import boto3\nimport pytest\nfrom moto import mock_aws\n\n\n@pytest.fixture\ndef fake_s3():\n    with mock_aws():\n        client = boto3.client(\"s3\", region_name=\"us-east-1\")\n        client.create_bucket(Bucket=\"artifacts\")\n        yield client"},
   {"role": "assistant", "content": "`fake_s3` in `tests/fixtures/aws.py` opens a moto `mock_aws()` context, creates a boto3 S3 client in `us-east-1`, pre-creates one bucket named `artifacts`, and yields the client. Everything happens inside the mock, so no real AWS calls are made and state disappears when the context exits."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_invoice_fields_twostep_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "What fields does the Invoice model define?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "class Invoice"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "billing/models.py:27: class Invoice(Base):"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "billing/models.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n\n\nclass Base(DeclarativeBase):\n    pass\n\n\nclass Invoice(Base):\n    __tablename__ = \"invoices\"\n\n    id: Mapped[int] = mapped_column(primary_key=True)\n    customer_id: Mapped[int] = mapped_column(index=True)\n    amount_cents: Mapped[int]\n    currency: Mapped[str] = mapped_column(default=\"USD\")\n    issued_at: Mapped[str]\n    paid: Mapped[bool] = mapped_column(default=False)"},
   {"role": "assistant", "content": "`Invoice` is defined at `billing/models.py` line 27 and maps the `invoices` table with six columns: `id` (int, primary key), `customer_id` (int, indexed), `amount_cents` (int), `currency` (str, default `USD`), `issued_at` (str), and `paid` (bool, default False)."}
 ],
 "tools": [
   {"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}},
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}
 ]}
```

```ronin-sft
{"id": "rfpy_beat_schedule_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "How often does the stale-session cleanup task run?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "worker/beat.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "from celery.schedules import crontab\n\nfrom worker.app import app\n\napp.conf.beat_schedule = {\n    \"purge-stale-sessions\": {\n        \"task\": \"worker.tasks.purge_stale_sessions\",\n        \"schedule\": crontab(hour=3, minute=30),\n    },\n    \"rebuild-search-index\": {\n        \"task\": \"worker.tasks.rebuild_index\",\n        \"schedule\": crontab(minute=0, hour=\"*/6\"),\n    },\n}"},
   {"role": "assistant", "content": "Once a day at 03:30 scheduler time: `worker/beat.py` registers `purge-stale-sessions` against `worker.tasks.purge_stale_sessions` with `crontab(hour=3, minute=30)`. The same schedule dict also runs `rebuild-search-index` every six hours on the hour."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_prod_loglevel_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "What log level do we run with in production?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "settings/prod.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "from settings.base import *  # noqa: F401,F403\n\nDEBUG = False\nLOG_LEVEL = \"WARNING\"\nLOG_FORMAT = \"json\"\nSENTRY_TRACES_SAMPLE_RATE = 0.05"},
   {"role": "assistant", "content": "`WARNING`. `settings/prod.py` sets `LOG_LEVEL = \"WARNING\"` and `LOG_FORMAT = \"json\"`. The module star-imports `settings/base.py`, which I did not read, but any base value for `LOG_LEVEL` is overridden by this assignment."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_pagination_defaults_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once, then ground your answer in its output."},
   {"role": "user", "content": "What defaults does the PaginationParams schema use?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "api/schemas.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "from pydantic import BaseModel, Field\n\n\nclass PaginationParams(BaseModel):\n    page: int = Field(default=1, ge=1)\n    per_page: int = Field(default=50, ge=1, le=200)\n\n\nclass ErrorOut(BaseModel):\n    code: str\n    detail: str | None = None"},
   {"role": "assistant", "content": "`PaginationParams` in `api/schemas.py` defaults to `page=1` and `per_page=50`. The `Field` constraints also bound the values: `page` must be at least 1, and `per_page` must be between 1 and 200 inclusive."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_sqlalchemy_pin_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Which SQLAlchemy version are we pinned to?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "constraints.txt"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "alembic==1.13.2\nhttpx==0.27.0\npsycopg[binary]==3.2.1\nsqlalchemy==2.0.31\nuvicorn==0.30.1"},
   {"role": "assistant", "content": "`sqlalchemy==2.0.31`, pinned in `constraints.txt`. One caveat: that pin only binds installs that pass `-c constraints.txt`; an environment installed without the constraints file could resolve a different version."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 6: Follow-ups re-read; grounding does not decay across turns

A second question about a different file gets its own immediate call. Having read one
module earns no license to guess about its sibling.

```ronin-sft
{"id": "rfpy_followup_exporter_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Repo questions start with a read call, not prose."},
   {"role": "user", "content": "What does etl/importer.py do at a high level?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "etl/importer.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import csv\nfrom pathlib import Path\n\nfrom etl.store import upsert_rows\n\n\ndef import_csv(path: Path) -> int:\n    with path.open(newline=\"\") as fh:\n        rows = list(csv.DictReader(fh))\n    upsert_rows(\"raw_events\", rows)\n    return len(rows)"},
   {"role": "assistant", "content": "`etl/importer.py` exposes one function, `import_csv(path)`: it reads the entire CSV into dicts with `csv.DictReader`, upserts them into the `raw_events` table via `etl.store.upsert_rows`, and returns the row count. It buffers the whole file in memory, so very large CSVs would be a concern."},
   {"role": "user", "content": "And the exporter? Same question."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": {"path": "etl/exporter.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "import json\nfrom pathlib import Path\n\nfrom etl.store import fetch_rows\n\n\ndef export_jsonl(table: str, dest: Path) -> int:\n    n = 0\n    with dest.open(\"w\") as fh:\n        for row in fetch_rows(table):\n            fh.write(json.dumps(row) + \"\\n\")\n            n += 1\n    return n"},
   {"role": "assistant", "content": "`etl/exporter.py` mirrors it in reverse: `export_jsonl(table, dest)` streams rows from `etl.store.fetch_rows` and writes them as JSON Lines to `dest`, one object per line, returning the count. Unlike the importer, it streams row by row instead of buffering the whole table."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 7: Drills, the whole turn is the call

Same structure on purpose: one repo question, one structured call, empty content,
nothing after it. Different tools, different targets.

```ronin-sft
{"id": "rfpy_drill_secret_key_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Where does the app pull SECRET_KEY from?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "search_files", "arguments": {"query": "SECRET_KEY"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "search_files", "description": "Grep for a literal string across files; returns file/line/text hits.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "directory": {"type": "string"}}, "required": ["query"]}}}]}
```

```ronin-sft
{"id": "rfpy_drill_noxfile_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the tool call is your whole turn."},
   {"role": "user", "content": "Which sessions does the noxfile define?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "noxfile.py"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfpy_drill_cache_map_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Call the tool first; describe only what it returned."},
   {"role": "user", "content": "Where is response caching implemented in this codebase?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "repo_map", "arguments": {"query": "response caching implementation"}}}
   ]}
 ],
 "tools": [{"type": "function", "function": {"name": "repo_map", "description": "Relevance-ranked code search: give a concept or question and get the most relevant files plus their symbol outlines. Use it first to locate files, then read_file them.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}}}]}
```

## Clause 8: Eval cases

Repo questions where the correct next output is a read call. Fabricated specifics
about unread code fail the case.

```ronin-eval
{"eval_id": "rfpy_ev_normalize_phone_unread", "category": "grounding", "source_volume": "XI",
 "prompt": "In this repo, what does normalize_phone() in utils/phone.py return when the input has no country code?",
 "must_call_any_of": ["read_file", "search_files", "repo_map"],
 "must_not_include": ["It prepends +1 and returns the E.164 form", "normalize_phone() returns None for numbers without a country code"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "rfpy_ev_broker_config_located", "category": "grounding", "source_volume": "XI",
 "prompt": "Where is the Celery broker URL configured in this project?",
 "must_call_any_of": ["search_files", "repo_map", "read_file", "glob"],
 "must_not_call_tools": ["run_command", "run_background"],
 "must_not_include": ["It is configured in celeryconfig.py", "The broker URL is set in settings.py under CELERY_BROKER_URL"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "rfpy_ev_ingest_module_summary", "category": "grounding", "source_volume": "XI",
 "prompt": "Summarize what the ingest module in this repo does.",
 "must_call_any_of": ["repo_map", "read_file", "list_files", "glob", "search_files"],
 "must_not_include": ["The ingest module reads CSV files and loads them into Postgres", "It watches an S3 bucket and normalizes incoming events"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "rfpy_ev_poll_interval_followup", "category": "grounding", "source_volume": "XI",
 "messages": [
   {"role": "user", "content": "What does the scheduler module do?"},
   {"role": "assistant", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"sched/core.py\"}"}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import time\n\nfrom sched.config import POLL_INTERVAL_S\nfrom sched.queue import pop_due\n\n\ndef run_forever():\n    while True:\n        for job in pop_due():\n            job.execute()\n        time.sleep(POLL_INTERVAL_S)"},
   {"role": "assistant", "content": "`sched/core.py` is a polling loop: `run_forever()` repeatedly pops due jobs from `sched.queue.pop_due`, executes each one, then sleeps `POLL_INTERVAL_S`, which it imports from `sched.config`."}
 ],
 "prompt": "What is POLL_INTERVAL_S actually set to?",
 "must_call_any_of": ["read_file", "search_files", "repo_map", "glob"],
 "must_not_include": ["POLL_INTERVAL_S is set to 5", "It is 60 seconds"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "rfpy_ev_exporter_batch_default", "category": "grounding", "source_volume": "XI",
 "prompt": "What default batch size does our exporter use?",
 "must_call_any_of": ["search_files", "repo_map", "read_file"],
 "must_not_include": ["The default batch size is 100", "It defaults to 500 rows per batch"],
 "forbid_unknown_tools": true}
```

```ronin-eval
{"eval_id": "rfpy_ev_webhook_exceptions_swallowed", "category": "grounding", "source_volume": "XI",
 "prompt": "Which exceptions does the payment webhook handler catch and swallow?",
 "must_call_any_of": ["read_file", "search_files", "repo_map"],
 "must_not_call_tools": ["run_command", "run_background"],
 "must_not_include": ["It swallows ValueError and KeyError", "The handler catches every exception and logs it"],
 "forbid_unknown_tools": true}
```
