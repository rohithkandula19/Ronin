# ronin toolkit

Beyond the coding agent and the 200-plugin library, ronin ships a set of small,
fast, **offline** developer utilities — plus a genuinely novel **API-to-tool**
pipeline. Everything here runs locally (no LLM, no key) unless noted.

## 🛰️ API workflow (turn any API into an agent tool)

The standout: go from "I found an API" to "the agent has a tool for it" in
seconds.

```bash
ronin endpoint https://catfact.ninja/fact      # probe: status, latency, schema
ronin schema '{"fact":"...","length":35}'      # infer a JSON Schema from a sample
ronin plugin from-api catfacts https://catfact.ninja/fact -f fact -f length
ronin mock '{"id":1,"email":"a@b.com"}' -n 5   # realistic fixtures from a schema/sample
ronin api-test https://api.github.com/repos/torvalds/linux --status 200 --field name=linux
```

- **`ronin plugin from-api NAME URL`** — generate a working agent tool from any
  REST endpoint. `{placeholders}` in the URL become parameters; `-f path`
  extracts JSON fields. *No other coding agent does this.*
- **`ronin endpoint URL`** — probe an endpoint and print a ready `from-api` command.
- **`ronin schema`** — infer a JSON Schema from a sample.
- **`ronin mock -n N`** — generate realistic fixture records from a schema.
- **`ronin api-test`** — assert status + JSON fields; non-zero exit on failure (CI-friendly).

## 🔧 JSON / data

```bash
ronin json users.0.name @data.json     # jq-lite path query (--keys to list keys)
ronin diff-json @before.json @after.json   # structural diff: + added / - removed / ~ changed
```

## 🧰 Dev reference & conversion (offline)

```bash
ronin cron '*/15 9-17 * * 1-5'   # "Every 15 minutes, between 09:00 and 17:59, Mon–Fri"
ronin chmod 644                  # rw-r--r-- with owner/group/others breakdown
ronin http 429                   # explain an HTTP status code
ronin time Asia/Tokyo            # world clock / timezone (no arg = all common zones)
ronin user-agent "<ua-string>"   # browser / OS / device
ronin count @file                # lines / words / chars / bytes (friendly wc)
```

## 🔐 Security & hygiene (offline)

```bash
ronin redact @app.log            # mask emails, IPs, API keys, tokens, JWTs -> [REDACTED-*]
ronin env-example --write        # safe .env.example from .env (blanks secret values)
ronin gitignore python node macos   # .gitignore from 17 stack templates
ronin license mit "Your Name" --write   # generate a LICENSE
```

Many of these also exist as **plugins** (so the agent can call them mid-chat) —
see [INTEGRATIONS.md](INTEGRATIONS.md) for the 200-plugin library, MCP servers,
and writing your own tools.
