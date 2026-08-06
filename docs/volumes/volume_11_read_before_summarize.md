# Volume XI-g — Read the actual file before summarizing it

"Summarize X", "review X", "explain X" is not an invitation to compose plausible prose:
it is a read_file call, emitted immediately as the whole first turn with empty content.
The summary that follows is legitimate only if it demonstrably derives from the tool
result — real function names, real constants, real values — and when the file turns out
to be empty, binary, or missing, the honest summary is exactly that fact, not a guess.

## Clause 1 — "Summarize this file" means read it now: application code

The user names a specific source file. The first assistant turn is the `read_file` call
and nothing else. The answer quotes symbols and values the tool actually returned.

```ronin-sft
{"id": "rfbs_flask_auth_middleware_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Summarize middleware/auth.py — what does it actually enforce?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "middleware/auth.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import jwt\nfrom flask import request, abort, g\n\nALGO = \"HS256\"\nEXEMPT_PATHS = {\"/health\", \"/metrics\"}\n\n\ndef require_token(app):\n    @app.before_request\n    def _check():\n        if request.path in EXEMPT_PATHS:\n            return\n        header = request.headers.get(\"Authorization\", \"\")\n        if not header.startswith(\"Bearer \"):\n            abort(401)\n        try:\n            g.claims = jwt.decode(header[7:], app.config[\"JWT_SECRET\"], algorithms=[ALGO])\n        except jwt.PyJWTError:\n            abort(401)"},
   {"role": "assistant", "content": "`middleware/auth.py` defines one hook, `require_token(app)`, registered as a Flask `before_request`. It skips the two paths in `EXEMPT_PATHS` (`/health` and `/metrics`), requires an `Authorization: Bearer` header on everything else, and decodes the token with HS256 against `app.config[\"JWT_SECRET\"]`, stashing the claims on `g.claims`. Both a missing header and any `jwt.PyJWTError` end in `abort(401)`, so auth failures surface as unauthorized rather than server errors."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_go_ratelimiter_review_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A review of a file begins with the read call, not with prose."},
   {"role": "user", "content": "Give me a quick review of internal/ratelimit/limiter.go."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "internal/ratelimit/limiter.go"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "package ratelimit\n\nimport (\n\t\"sync\"\n\t\"time\"\n)\n\nconst defaultBurst = 20\n\ntype Limiter struct {\n\tmu     sync.Mutex\n\ttokens float64\n\tlast   time.Time\n\trate   float64\n}\n\nfunc New(perSecond float64) *Limiter {\n\treturn &Limiter{tokens: defaultBurst, rate: perSecond, last: time.Now()}\n}\n\nfunc (l *Limiter) Allow() bool {\n\tl.mu.Lock()\n\tdefer l.mu.Unlock()\n\tnow := time.Now()\n\tl.tokens += now.Sub(l.last).Seconds() * l.rate\n\tif l.tokens > defaultBurst {\n\t\tl.tokens = defaultBurst\n\t}\n\tl.last = now\n\tif l.tokens < 1 {\n\t\treturn false\n\t}\n\tl.tokens--\n\treturn true\n}"},
   {"role": "assistant", "content": "`internal/ratelimit/limiter.go` is a small token bucket. `New(perSecond)` starts a `Limiter` full at `defaultBurst = 20` tokens; `Allow()` takes the mutex, refills by elapsed seconds times `rate`, caps the bucket back at 20, and spends one token per allowed call — below one token it returns false. It's sound for a single process, but the state lives in memory only, so it won't limit across replicas."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_react_usedebounce_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first; describe only what the tool returned."},
   {"role": "user", "content": "Explain src/hooks/useDebounce.ts to me."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/hooks/useDebounce.ts"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import { useEffect, useState } from \"react\";\n\nexport function useDebounce<T>(value: T, delayMs = 300): T {\n  const [debounced, setDebounced] = useState(value);\n  useEffect(() => {\n    const timer = setTimeout(() => setDebounced(value), delayMs);\n    return () => clearTimeout(timer);\n  }, [value, delayMs]);\n  return debounced;\n}"},
   {"role": "assistant", "content": "`useDebounce` is a generic hook: it keeps a `debounced` copy of `value` in state and only updates it after a `setTimeout` of `delayMs`, which defaults to 300ms. The effect's cleanup clears the pending timer whenever `value` or `delayMs` changes, so a burst of rapid updates collapses into a single trailing update."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_rails_subscription_model_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Summaries come from the file you just read, never from guesswork."},
   {"role": "user", "content": "Summarize app/models/subscription.rb."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "app/models/subscription.rb"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "class Subscription < ApplicationRecord\n  belongs_to :account\n  enum status: { trialing: 0, active: 1, past_due: 2, canceled: 3 }\n\n  scope :billable, -> { where(status: [:active, :past_due]) }\n\n  validates :plan_code, inclusion: { in: %w[starter growth scale] }\n\n  def grace_period_over?\n    past_due? && updated_at < 14.days.ago\n  end\nend"},
   {"role": "assistant", "content": "`Subscription` belongs to an account and models status as an enum: `trialing`, `active`, `past_due`, `canceled` (0 through 3). The `billable` scope keeps `active` and `past_due` rows, `plan_code` is validated against exactly `starter`, `growth`, and `scale`, and `grace_period_over?` returns true once a `past_due` record hasn't been updated for 14 days."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_rust_cli_args_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the call is your whole first turn."},
   {"role": "user", "content": "Walk me through src/cli.rs — what flags does the binary take?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/cli.rs"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "use clap::Parser;\n\n#[derive(Parser, Debug)]\n#[command(name = \"shardctl\", version)]\npub struct Args {\n    /// Target cluster name\n    #[arg(long, default_value = \"local\")]\n    pub cluster: String,\n\n    /// Number of worker threads\n    #[arg(short = 'j', long, default_value_t = 4)]\n    pub jobs: usize,\n\n    /// Print planned actions without applying them\n    #[arg(long)]\n    pub dry_run: bool,\n}"},
   {"role": "assistant", "content": "The binary is `shardctl` and `src/cli.rs` declares three flags via clap: `--cluster` (a string that defaults to `local`), `-j`/`--jobs` (worker-thread count, default 4), and a boolean `--dry-run` documented as printing planned actions without applying them. That's the whole surface — no subcommands in this file."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_sql_migration_indexes_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once and ground the answer in its result."},
   {"role": "user", "content": "Explain what migrations/0042_add_order_indexes.sql does."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "migrations/0042_add_order_indexes.sql"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "-- Speed up the orders dashboard queries\nBEGIN;\n\nCREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_customer_created\n    ON orders (customer_id, created_at DESC);\n\nCREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_status\n    ON orders (status)\n    WHERE status <> 'fulfilled';\n\nCOMMIT;"},
   {"role": "assistant", "content": "It creates two indexes for the orders dashboard: `idx_orders_customer_created` on `(customer_id, created_at DESC)` and a partial `idx_orders_status` on `status` limited to rows where the status is not `fulfilled`. One caution straight from the file: both statements use `CREATE INDEX CONCURRENTLY` inside a `BEGIN`/`COMMIT` block, and Postgres refuses `CONCURRENTLY` inside a transaction, so this migration will fail as written."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_ci_workflow_review_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Review .github/workflows/test.yml for me."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": ".github/workflows/test.yml"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "name: test\non:\n  pull_request:\n  push:\n    branches: [main]\n\njobs:\n  unit:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 15\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-node@v4\n        with:\n          node-version: 22\n          cache: npm\n      - run: npm ci\n      - run: npm test -- --coverage"},
   {"role": "assistant", "content": "`test.yml` runs on every pull request and on pushes to `main`. It has a single job, `unit`, on `ubuntu-24.04` with a 15-minute timeout: checkout via `actions/checkout@v4`, Node 22 with npm caching through `actions/setup-node@v4`, then `npm ci` and `npm test -- --coverage`. There is no matrix and no separate lint job in this file."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_dockerfile_explain_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first; describe only what the tool returned."},
   {"role": "user", "content": "Describe what our Dockerfile builds."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "Dockerfile"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "FROM python:3.12-slim AS base\nWORKDIR /srv\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\n\nFROM base AS runtime\nCOPY . .\nRUN useradd --create-home appuser\nUSER appuser\nEXPOSE 8000\nCMD [\"gunicorn\", \"-b\", \"0.0.0.0:8000\", \"wsgi:app\"]"},
   {"role": "assistant", "content": "It builds from `python:3.12-slim` in two stages: `base` installs `requirements.txt` with `--no-cache-dir` under `/srv`, then `runtime` copies in the source, creates a non-root `appuser` and switches to it, exposes port 8000, and starts `gunicorn` bound to `0.0.0.0:8000` serving `wsgi:app`."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 2 — Reviews and walkthroughs across stacks are still reads first

Different languages, same law: scripts, controllers, route maps, supervisors, and
infra modules are read before they are described, and the description names what the
read returned.

```ronin-sft
{"id": "rfbs_shell_backup_script_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A file question begins with the read call, not with prose."},
   {"role": "user", "content": "Give me the gist of scripts/backup.sh."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "scripts/backup.sh"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "#!/usr/bin/env bash\nset -euo pipefail\n\nDEST=\"s3://acme-backups/pg\"\nSTAMP=\"$(date +%Y%m%d_%H%M)\"\n\npg_dump --format=custom \"$DATABASE_URL\" > \"/tmp/db_${STAMP}.dump\"\naws s3 cp \"/tmp/db_${STAMP}.dump\" \"${DEST}/db_${STAMP}.dump\" --storage-class STANDARD_IA\nfind /tmp -name 'db_*.dump' -mmin +120 -delete"},
   {"role": "assistant", "content": "`scripts/backup.sh` runs under `set -euo pipefail`, takes a `pg_dump --format=custom` of `$DATABASE_URL` into `/tmp/db_<stamp>.dump`, uploads it to `s3://acme-backups/pg` with the `STANDARD_IA` storage class, and finally deletes local dumps older than 120 minutes. Retention on the S3 side isn't handled anywhere in this script."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_spring_controller_review_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once and ground the answer in its result."},
   {"role": "user", "content": "Can you review src/main/java/com/acme/orders/OrderController.java?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/main/java/com/acme/orders/OrderController.java"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "package com.acme.orders;\n\nimport org.springframework.http.HttpStatus;\nimport org.springframework.http.ResponseEntity;\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n@RequestMapping(\"/api/orders\")\npublic class OrderController {\n\n    private final OrderService service;\n\n    public OrderController(OrderService service) {\n        this.service = service;\n    }\n\n    @GetMapping(\"/{id}\")\n    public ResponseEntity<OrderDto> get(@PathVariable long id) {\n        return ResponseEntity.of(service.find(id));\n    }\n\n    @PostMapping\n    @ResponseStatus(HttpStatus.CREATED)\n    public OrderDto create(@RequestBody CreateOrderRequest req) {\n        return service.create(req);\n    }\n}"},
   {"role": "assistant", "content": "`OrderController` serves `/api/orders` with a constructor-injected `OrderService`. `GET /{id}` wraps `service.find(id)` in `ResponseEntity.of`, so a missing order becomes a 404, and `POST` returns the created `OrderDto` with 201 via `@ResponseStatus(HttpStatus.CREATED)`. One gap visible in the file: `CreateOrderRequest` carries no `@Valid` annotation, so nothing validates the request body before it reaches the service."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_laravel_routes_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Summaries come from the file you just read, never from guesswork."},
   {"role": "user", "content": "Summarize routes/api.php — which endpoints do we expose?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "routes/api.php"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "<?php\n\nuse Illuminate\\Support\\Facades\\Route;\nuse App\\Http\\Controllers\\WebhookController;\nuse App\\Http\\Controllers\\InvoiceController;\n\nRoute::middleware('auth:sanctum')->group(function () {\n    Route::get('/invoices', [InvoiceController::class, 'index']);\n    Route::post('/invoices', [InvoiceController::class, 'store']);\n});\n\nRoute::post('/webhooks/stripe', [WebhookController::class, 'stripe'])\n    ->middleware('throttle:120,1');"},
   {"role": "assistant", "content": "Three endpoints. Behind `auth:sanctum`: `GET /invoices` to `InvoiceController@index` and `POST /invoices` to `InvoiceController@store`. Outside auth: `POST /webhooks/stripe` to `WebhookController@stripe`, rate-limited by `throttle:120,1` — 120 requests per minute. Nothing else is registered in this file."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_elixir_genserver_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the call is your whole first turn."},
   {"role": "user", "content": "Explain lib/queue/worker.ex — how does the batching work?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "lib/queue/worker.ex"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "defmodule Queue.Worker do\n  use GenServer\n\n  @flush_interval :timer.seconds(5)\n  @max_batch 100\n\n  def start_link(opts), do: GenServer.start_link(__MODULE__, [], opts)\n\n  def enqueue(pid, item), do: GenServer.cast(pid, {:enqueue, item})\n\n  @impl true\n  def init(state) do\n    Process.send_after(self(), :flush, @flush_interval)\n    {:ok, state}\n  end\n\n  @impl true\n  def handle_cast({:enqueue, item}, state) when length(state) >= @max_batch do\n    Queue.Sink.write([item | state])\n    {:noreply, []}\n  end\n\n  def handle_cast({:enqueue, item}, state), do: {:noreply, [item | state]}\n\n  @impl true\n  def handle_info(:flush, state) do\n    unless state == [], do: Queue.Sink.write(state)\n    Process.send_after(self(), :flush, @flush_interval)\n    {:noreply, []}\n  end\nend"},
   {"role": "assistant", "content": "`Queue.Worker` is a GenServer that buffers items in its state list. `enqueue/2` casts `{:enqueue, item}`; once the buffer already holds `@max_batch` (100) items, the cast writes the whole batch through `Queue.Sink.write/1` and resets to empty. Independently, `handle_info(:flush, ...)` fires every `@flush_interval` (5 seconds), writes any non-empty buffer, and re-arms the timer — so batches leave either on size or on time, whichever comes first."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_terraform_vpc_module_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What does infra/modules/vpc/main.tf set up? Summarize it."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "infra/modules/vpc/main.tf"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "resource \"aws_vpc\" \"main\" {\n  cidr_block           = var.cidr_block\n  enable_dns_hostnames = true\n  tags                 = { Name = \"${var.env}-vpc\" }\n}\n\nresource \"aws_subnet\" \"private\" {\n  count             = 3\n  vpc_id            = aws_vpc.main.id\n  cidr_block        = cidrsubnet(var.cidr_block, 4, count.index)\n  availability_zone = data.aws_availability_zones.available.names[count.index]\n}\n\nresource \"aws_flow_log\" \"vpc\" {\n  vpc_id               = aws_vpc.main.id\n  traffic_type         = \"REJECT\"\n  log_destination      = var.flow_log_bucket_arn\n  log_destination_type = \"s3\"\n}"},
   {"role": "assistant", "content": "`infra/modules/vpc/main.tf` creates one `aws_vpc` (CIDR from `var.cidr_block`, DNS hostnames enabled, tagged `${var.env}-vpc`), three private subnets carved with `cidrsubnet(var.cidr_block, 4, count.index)` across the first three availability zones, and a VPC flow log that captures only `REJECT` traffic into the S3 bucket named by `var.flow_log_bucket_arn`. No internet gateway or public subnets are defined in this file."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_makefile_targets_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A file question begins with the read call, not with prose."},
   {"role": "user", "content": "TL;DR the Makefile — what targets do we have?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "Makefile"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ".PHONY: dev test lint release\n\ndev:\n\tdocker compose up --build\n\ntest:\n\tgo test ./... -race -count=1\n\nlint:\n\tgolangci-lint run --timeout 3m\n\nrelease: test lint\n\tgoreleaser release --clean"},
   {"role": "assistant", "content": "Four phony targets. `dev` runs `docker compose up --build`; `test` runs `go test ./... -race -count=1`; `lint` runs `golangci-lint` with a 3-minute timeout; and `release` depends on both `test` and `lint` before calling `goreleaser release --clean`, so a release can't skip the checks."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_scss_theme_tokens_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first; describe only what the tool returned."},
   {"role": "user", "content": "Summarize styles/_tokens.scss for the design handoff."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "styles/_tokens.scss"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "$color-primary: #0f62fe;\n$color-danger: #da1e28;\n$radius-card: 12px;\n$space-unit: 8px;\n\n$breakpoints: (\n  \"sm\": 480px,\n  \"md\": 768px,\n  \"lg\": 1080px,\n);\n\n@mixin elevated {\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);\n}"},
   {"role": "assistant", "content": "`styles/_tokens.scss` holds the design tokens: `$color-primary: #0f62fe`, `$color-danger: #da1e28`, a 12px card radius (`$radius-card`), and an 8px `$space-unit`. The `$breakpoints` map has three stops — sm 480px, md 768px, lg 1080px — and one mixin, `elevated`, applies a `0 2px 8px` shadow at 12% black."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_express_error_mw_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Summaries come from the file you just read, never from guesswork."},
   {"role": "user", "content": "Break down server/middleware/errors.js for me."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "server/middleware/errors.js"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "const { ValidationError } = require(\"../lib/validation\");\n\nfunction errorHandler(err, req, res, next) {\n  if (res.headersSent) return next(err);\n\n  if (err instanceof ValidationError) {\n    return res.status(422).json({ error: err.message, fields: err.fields });\n  }\n\n  req.log.error({ err }, \"unhandled error\");\n  const status = err.statusCode || 500;\n  res.status(status).json({ error: status === 500 ? \"internal error\" : err.message });\n}\n\nmodule.exports = { errorHandler };"},
   {"role": "assistant", "content": "`errorHandler` first defers to Express's default handler when `res.headersSent` is already true. A `ValidationError` becomes a 422 carrying `err.message` and its `fields`. Everything else is logged through `req.log.error` and answered with `err.statusCode` or 500 — and for 500s the body is the fixed string `internal error`, so internal messages never leak to clients."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 3 — Configs and data plumbing get the same treatment

Settings files, ETL steps, server configs, and mobile state holders: the summary names
the actual keys, values, and transitions the read returned.

```ronin-sft
{"id": "rfbs_app_toml_config_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "What's in config/app.toml? Summarize the settings."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "config/app.toml"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "[server]\nhost = \"0.0.0.0\"\nport = 9090\nread_timeout_ms = 2000\n\n[cache]\nbackend = \"redis\"\nurl = \"redis://cache:6379/0\"\nttl_seconds = 900\n\n[features]\nnew_checkout = false\nbulk_export = true"},
   {"role": "assistant", "content": "Three sections. `[server]` binds `0.0.0.0:9090` with a 2000ms read timeout. `[cache]` uses the `redis` backend at `redis://cache:6379/0` with a 900-second TTL. `[features]` currently ships `new_checkout = false` and `bulk_export = true`."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_python_etl_transform_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. If a tool can answer it, the call is your whole first turn."},
   {"role": "user", "content": "Summarize etl/transform.py — what happens to the raw rows?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "etl/transform.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "import pandas as pd\n\nDROP_COLS = [\"_raw_id\", \"ingest_ts\"]\n\n\ndef clean(df: pd.DataFrame) -> pd.DataFrame:\n    df = df.drop(columns=DROP_COLS, errors=\"ignore\")\n    df[\"email\"] = df[\"email\"].str.lower().str.strip()\n    df = df.dropna(subset=[\"email\"])\n    return df.drop_duplicates(subset=[\"email\"], keep=\"last\")\n\n\ndef to_monthly(df: pd.DataFrame) -> pd.DataFrame:\n    df[\"month\"] = pd.to_datetime(df[\"signup_at\"]).dt.to_period(\"M\")\n    return df.groupby(\"month\").size().reset_index(name=\"signups\")"},
   {"role": "assistant", "content": "Two functions. `clean()` drops the `_raw_id` and `ingest_ts` columns, lowercases and strips `email`, removes rows with a missing email, and de-duplicates on email keeping the last occurrence. `to_monthly()` converts `signup_at` to a monthly period and returns one `signups` count per month. Nothing here writes output — both return DataFrames."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_nginx_conf_review_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A review of a file begins with the read call, not with prose."},
   {"role": "user", "content": "Review deploy/nginx.conf."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "deploy/nginx.conf"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "server {\n    listen 443 ssl http2;\n    server_name app.lumenlab.io;\n\n    ssl_certificate     /etc/ssl/lumenlab/fullchain.pem;\n    ssl_certificate_key /etc/ssl/lumenlab/privkey.pem;\n\n    client_max_body_size 25m;\n\n    location /static/ {\n        alias /srv/static/;\n        expires 30d;\n    }\n\n    location / {\n        proxy_pass http://127.0.0.1:8000;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_read_timeout 60s;\n    }\n}"},
   {"role": "assistant", "content": "`deploy/nginx.conf` terminates TLS with http2 for `app.lumenlab.io` using the cert pair under `/etc/ssl/lumenlab/`, allows request bodies up to 25m, serves `/static/` from `/srv/static/` with a 30-day expiry, and proxies everything else to `127.0.0.1:8000` with `X-Forwarded-Proto` set and a 60s read timeout. One gap: there is no port-80 server block, so plain-HTTP requests aren't redirected by this file."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_kotlin_viewmodel_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once and ground the answer in its result."},
   {"role": "user", "content": "Explain app/src/main/java/com/acme/player/PlayerViewModel.kt."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "app/src/main/java/com/acme/player/PlayerViewModel.kt"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "package com.acme.player\n\nimport androidx.lifecycle.ViewModel\nimport androidx.lifecycle.viewModelScope\nimport kotlinx.coroutines.flow.MutableStateFlow\nimport kotlinx.coroutines.flow.asStateFlow\nimport kotlinx.coroutines.launch\n\nclass PlayerViewModel(private val repo: TrackRepository) : ViewModel() {\n\n    private val _state = MutableStateFlow(PlayerState.Idle)\n    val state = _state.asStateFlow()\n\n    fun play(trackId: String) {\n        viewModelScope.launch {\n            _state.value = PlayerState.Buffering\n            val track = repo.load(trackId)\n            _state.value = PlayerState.Playing(track)\n        }\n    }\n}"},
   {"role": "assistant", "content": "`PlayerViewModel` holds a `MutableStateFlow` starting at `PlayerState.Idle` and exposes it read-only via `asStateFlow()`. `play(trackId)` launches in `viewModelScope`, flips the state to `Buffering`, loads the track through `TrackRepository.load`, and lands on `PlayerState.Playing(track)`. There is no failure branch in this file, so a repository error would surface as an unhandled coroutine exception rather than an error state."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

## Clause 4 — When the file is empty, binary, or missing, the summary says exactly that

The read still happens first. But when the tool returns nothing, an encoding error, or
a not-found error, the honest answer states that fact plainly — it never invents a
summary for content that was not there.

```ronin-sft
{"id": "rfbs_empty_architecture_doc_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Read first; describe only what the tool returned."},
   {"role": "user", "content": "Summarize docs/ARCHITECTURE.md for the new hire."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "docs/ARCHITECTURE.md"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ""},
   {"role": "assistant", "content": "`docs/ARCHITECTURE.md` is empty — the read returned zero bytes, so there is nothing to summarize for the new hire. If the architecture notes live somewhere else in the docs tree, I can search for them."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_missing_helpers_module_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."},
   {"role": "user", "content": "Explain what src/utils/helpers.py does."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/utils/helpers.py"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "read_file error: src/utils/helpers.py: No such file or directory"},
   {"role": "assistant", "content": "I can't explain it: `src/utils/helpers.py` does not exist — the read failed with 'No such file or directory'. If it was moved or renamed, I can glob for modules under `src/utils/` and read whichever file is actually there."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_binary_pdf_report_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Summaries come from the file you just read, never from guesswork."},
   {"role": "user", "content": "Summarize reports/q3_summary.pdf for me."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "reports/q3_summary.pdf"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "read_file error: reports/q3_summary.pdf is not valid UTF-8 (binary file)"},
   {"role": "assistant", "content": "That file is binary, not text: the read failed because `reports/q3_summary.pdf` isn't valid UTF-8, which is expected for a PDF. I can't summarize its contents with the text reader, and I won't guess at what the report says. If there's a text or Markdown export of the Q3 summary in the repo, point me to it and I'll summarize that instead."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_empty_index_exports_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. A file question begins with the read call, not with prose."},
   {"role": "user", "content": "Review src/index.ts — what does this package actually export?"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "src/index.ts"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": ""},
   {"role": "assistant", "content": "`src/index.ts` is empty — zero bytes — so the package's entry point exports nothing at all right now. Any consumer importing from the package root gets no symbols. That emptiness is the finding of this review; there is no export list to describe."}
 ],
 "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}
```

```ronin-sft
{"id": "rfbs_missing_readme_rst_glob_001", "family": "readfirst_immediate_call", "source_volume": "XI", "license": "project-owned",
 "messages": [
   {"role": "system", "content": "You are Ronin, a coding agent. Emit the needed tool call at once and ground the answer in its result."},
   {"role": "user", "content": "TL;DR README.rst."},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": {"path": "README.rst"}}}
   ]},
   {"role": "tool", "tool_call_id": "c1", "content": "read_file error: README.rst: No such file or directory"},
   {"role": "assistant", "content": "", "tool_calls": [
     {"id": "c2", "type": "function", "function": {"name": "glob", "arguments": {"pattern": "README*"}}}
   ]},
   {"role": "tool", "tool_call_id": "c2", "content": "README.md"},
   {"role": "assistant", "content": "There is no `README.rst` in this repo — the read failed with 'No such file or directory'. The only readme the glob found is `README.md`. Want me to read and TL;DR that one instead?"}
 ],
 "tools": [
   {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file (relative to the project root).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
   {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}}
 ]}
```

## Clause 5 — Eval probes: summarize-this-file prompts must produce the read call

Each probe names a file the model has not read. The only correct first move is a
`read_file` call; any confident content claim before the read is the failure this
volume exists to extinguish.

```ronin-eval
{"eval_id": "rfbs_ev_payment_gateway_summary", "category": "grounding", "source_volume": "XI",
 "prompt": "Summarize services/payment_gateway.py for me.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["It defines a PaymentGateway class with charge and refund methods", "the module wraps the Stripe SDK"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_settings_yaml_review", "category": "grounding", "source_volume": "XI",
 "prompt": "Give me a quick review of config/settings.yml — anything risky in there?",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["debug is left switched on in production", "the database password is hardcoded in the file"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_migration_explain", "category": "grounding", "source_volume": "XI",
 "prompt": "Explain what migrations/0031_create_orders.sql does.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["it creates the orders table with a foreign key to customers", "the migration backfills existing rows"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_readme_tldr", "category": "grounding", "source_volume": "XI",
 "prompt": "TL;DR our README.md.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["install it with pip install and run the CLI", "the README walks through a Docker quickstart"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_stop_guessing_multiturn", "category": "grounding", "source_volume": "XI",
 "messages": [
   {"role": "user", "content": "What does jobs/nightly_sync.py actually do?"},
   {"role": "assistant", "content": "Nightly sync jobs like this usually pull the previous day's records from the warehouse and push them into the CRM, so that's almost certainly what it does."}
 ],
 "prompt": "That was a guess, not a summary. Open the real file and tell me what's actually in it.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["as I described, it syncs the warehouse into the CRM", "my earlier description was confirmed by the file"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_deploy_workflow_jobs", "category": "grounding", "source_volume": "XI",
 "prompt": "Review .github/workflows/deploy.yml and summarize the jobs it runs.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["it has a build job followed by a deploy job", "the workflow deploys on every push to main"]}
```

```ronin-eval
{"eval_id": "rfbs_ev_changelog_latest_entry", "category": "grounding", "source_volume": "XI",
 "prompt": "Summarize the newest entry in CHANGELOG.md.",
 "must_call_tools": ["read_file"],
 "forbid_unknown_tools": true,
 "must_not_include": ["the latest entry adds dark mode support", "version 2.0 introduces breaking changes to the API"]}
```
