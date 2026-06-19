# Ronin for VS Code

Drive the [ronin](../../README.md) coding agent from the editor instead of the
terminal. Two commands talk to a locally-running `ronin serve` HTTP API:

| Command (Command Palette) | ID | What it does |
|---|---|---|
| **Ronin: Run a coding task** | `ronin.code` | Prompts for a task and sends it to ronin. |
| **Ronin: Ask about the codebase** | `ronin.ask` | Prompts for a question and sends it to ronin. |

Both prompt with an input box, POST to `ronin serve`, and render the reply in
the **Ronin** output channel.

> **Status / honesty:** this is a minimal, self-contained scaffold. It is
> written against the VS Code extension API but has **not** been compiled or run
> here — there is no `npm` or VS Code in the authoring environment. You must run
> `npm install` and open it in VS Code to build and launch it (steps below). It
> has **no runtime dependencies** (uses the extension host's built-in `fetch`),
> and it **requires a running `ronin serve`** to do anything.

## Prerequisites

- **VS Code `^1.85`** (the extension host must provide a global `fetch`, i.e.
  the bundled Node ≥ 18; VS Code 1.85+ qualifies).
- **Node.js + npm** to compile the TypeScript.
- **`ronin serve` running locally.** From the ronin repo:

  ```bash
  ronin serve                 # defaults to http://127.0.0.1:8000
  # or pick a port:
  ronin serve --port 8000
  ```

  `ronin serve` requires a provider configured first (`ronin init`). With no
  provider key it falls back to the offline demo brain, and the extension shows
  a `[demo mode]` banner on the reply.

## Build & run (Extension Development Host)

```bash
cd editors/vscode
npm install          # installs @types/vscode, @types/node, typescript (dev-only)
npm run compile      # tsc -> ./out/extension.js   (or: npm run watch)
```

Then in VS Code:

1. Open this `editors/vscode/` folder in VS Code.
2. Press **F5** (Run → Start Debugging) to launch a second VS Code window, the
   **Extension Development Host**, with the extension loaded.
3. Make sure `ronin serve` is running in a terminal.
4. In the dev-host window open the Command Palette (`Cmd/Ctrl+Shift+P`) and run
   **Ronin: Run a coding task** or **Ronin: Ask about the codebase**.
5. Watch the answer appear in the **Ronin** output channel (View → Output →
   "Ronin").

If `ronin serve` is not reachable, the extension shows a friendly error with a
**Run `ronin serve`** button that opens a terminal and starts it for you.

### Packaging a `.vsix` (optional)

```bash
npm install -g @vscode/vsce
vsce package          # produces ronin-0.1.0.vsix
```

## Settings

Configure under **Settings → Extensions → Ronin** (or `settings.json`):

| Setting | Default | Meaning |
|---|---|---|
| `ronin.serveUrl` | `http://127.0.0.1:8000` | Base URL of `ronin serve`. The extension POSTs to `<serveUrl>/ask`. |
| `ronin.token` | `""` | Optional bearer token, sent as `Authorization: Bearer <token>` **only when set**. See the auth note below. |
| `ronin.requestTimeoutMs` | `600000` | Request timeout in ms (coding tasks can be slow; default 10 min). |

## The `ronin serve` request shape this targets

Mirrors `packages/cli/src/ronin_cli/server.py`:

- **Endpoint:** `POST <serveUrl>/ask` (default `http://127.0.0.1:8000/ask`).
- **Request body:** `{ "question": "<the task or question>" }` — JSON, the
  single field `question`.
- **Response body:**

  ```json
  {
    "success": true,
    "output": "…the agent's answer…",
    "iterations": 3,
    "trace": [ { "kind": "...", "content": "..." } ],
    "usage": { "input_tokens": 0, "output_tokens": 0 },
    "error": null,
    "demo_mode": false
  }
  ```

  The extension renders `output`, surfaces `error` when `success` is `false`,
  flags `demo_mode`, and shows `iterations` + `usage` as a footer.
- There is also a `GET <serveUrl>/health` endpoint (not used by the commands).

### Auth note (important)

`ronin serve` is **unauthenticated** — it binds to `127.0.0.1` and has no token
check (see `server.py`). `ronin.token` is therefore **optional and unused by a
plain local `ronin serve`**; it exists only so you can front `ronin serve` with
an authenticating reverse proxy. When blank, no `Authorization` header is sent.

(Do not confuse this with ronin's *other*, token-protected endpoint
`POST /webhooks/agent` in `apps/api`, which uses `{ "message": "..." }` and a
Bearer token — this extension targets `ronin serve`'s `/ask` as specified.)
