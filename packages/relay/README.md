# ronin-relay

A small self-hosted relay that lets your phone send a task to a Ronin gateway
running on your laptop, without opening any inbound port on the laptop. The
laptop dials OUT to a relay server you own (a free VM). The phone talks to the
relay. The relay forwards the request down the connection the laptop already
opened.

This is a scaffold with working code and tests. It is not deployed and has no
users. Read the limits section before you rely on it.

## Why this exists

You want to reach the Ronin gateway on your laptop from your phone when you are
away from home. The naive way is to open a port on your router and forward it to
the laptop. That exposes the laptop to the whole internet and many home routers
and ISPs do not allow it. ronin-relay avoids that: the laptop opens an outbound
websocket to a relay you control, and nothing inbound is ever opened.

## Architecture at a glance

```
  phone (web UI at /  or  any HTTP client)
         --HTTPS POST /api/task-->  relay (your free VM)
                                     |
                                     |  pushes the request down a
                                     |  persistent websocket the laptop opened
                                     v
                                  connector (on the laptop, outbound only)
                                     |
                                     |  one local HTTP call to the
                                     |  configured target
                                     v
                                  Ronin gateway (localhost on the laptop)
                                     |
                                     v
                                  response travels back the same path
```

Three processes:

1. Relay. Runs on a tiny free VM. Accepts the phone request on HTTP and the
   laptop connector on a websocket, and serves a self-contained mobile web UI at
   `/`. Pure Python, FastAPI, uvicorn, websockets. No database. In-memory
   session map only.
2. Connector. Runs on the laptop. Dials out to the relay and holds the
   websocket open. When a request arrives it makes ONE local call to the Ronin
   gateway and ships the result back. It forwards to nothing else.
3. Ronin gateway. Your existing local service. ronin-relay does not change it.

See `docs/ARCHITECTURE.md` for the message protocol and module layout.

## Security model

This is security-sensitive infrastructure. It lets a phone reach a laptop. The
defaults are built to fail closed.

- Shared token on every endpoint. The phone presents `RONIN_RELAY_TOKEN` as a
  bearer token on `POST /api/task`, and the connector presents it when it dials
  `GET /connect` (as a websocket query parameter, since websocket clients cannot
  reliably set headers). Unauthenticated or wrong-token requests get 401, and a
  connector with a bad token is refused the websocket. The relay refuses to
  start if the token is missing or shorter than 24 characters.
- Constant-time token compare. The token check uses `hmac.compare_digest` so it
  does not leak the token through timing.
- Outbound-only connector. The laptop never opens an inbound port. It dials out
  to the relay. Your router and firewall stay closed.
- Fixed target, no shell. The connector forwards only to one configured local
  URL, the Ronin gateway (`RONIN_TARGET_URL`). The phone cannot pick a
  different host, and there is no path that runs an arbitrary shell command from
  the internet. The phone chooses a method, a path, and a JSON body; the host is
  always the configured target.
- TLS in production. Run the relay behind HTTPS (a reverse proxy such as Caddy
  or nginx with a real certificate). The connector then uses `wss://` and the
  phone uses `https://`. The token is only as safe as the transport, so do not
  run the relay on plain HTTP on the public internet. On localhost, plain HTTP
  is fine for tests.
- Rate limiting and structured logging. The relay applies a small in-memory
  sliding-window rate limit per token (429 when exceeded). It logs each relayed
  request, auth failure, and timeout as a single line of JSON (event name plus
  fields, never the token), so the logs stay greppable and auditable.
- Single connector. One laptop attaches at a time. A new connector replaces the
  old session.

What this does NOT give you: it is not a VPN, not a general reverse tunnel, and
not a way to run commands on the laptop. It forwards HTTP-shaped requests to one
local service.

## Honest limits

- The laptop must be ON and AWAKE. The connector only works while it is running
  and connected. If the laptop is off, asleep, or has no network, there is no
  connection for the relay to use, and the phone request returns 503 (laptop
  offline). A relay cannot wake or reach a laptop that is off. This tool does
  not do wake-on-LAN and makes no promise to reach an offline machine.
- The relay is a single small process with in-memory state. If you restart it,
  the session drops and the connector reconnects. There is no clustering and no
  persistence, by design, to stay free-tier friendly.
- It forwards to one configured target. It is not a general proxy.
- This repo is a scaffold. The code runs and the tests pass offline, but it has
  not been deployed or hardened in production. Treat it accordingly.

## Free Oracle VM setup (relay side)

Any 512MB always-free box works (Oracle Cloud Always Free, or similar). Steps:

1. Create a small VM and SSH in. Install Python 3.11 to 3.13 and pip.
2. Copy this repo to the VM and install it:

   ```
   python -m pip install -r requirements.txt
   ```

3. Generate a strong token and export it:

   ```
   export RONIN_RELAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   ```

   Save that token; the phone and the laptop both need the exact same value.

4. Put the relay behind HTTPS. The simplest path is Caddy, which gets a free
   certificate automatically. Point a domain at the VM and have Caddy reverse
   proxy to `127.0.0.1:8000`. Open only ports 80 and 443 in the VM firewall
   (security list). Do NOT expose port 8000 directly.

5. Start the relay:

   ```
   ronin-relay serve
   ```

   (Equivalently `python -m ronin_relay serve`. The older verb `relay` still
   works as an alias.) It listens on `0.0.0.0:8000` by default; set
   `RONIN_RELAY_HOST=127.0.0.1` so only your HTTPS proxy can reach it. Use a
   process manager (systemd or similar) to keep it running.

## Laptop connector setup

On the laptop, with the SAME token, run the connector. The documented form
takes flags:

```
ronin-relay connect \
  --relay wss://relay.example.com/connect \
  --target http://127.0.0.1:8000/webhooks/agent \
  --token "$RONIN_RELAY_TOKEN"
```

`--target` is the ONE local URL the connector is allowed to forward to (your
Ronin gateway, for example `http://127.0.0.1:8000/webhooks/agent`). It forwards
to nothing else: there is no path that runs an arbitrary shell command, and the
phone cannot redirect it to another host.

The flags fall back to environment variables when omitted, so this also works:

```
export RONIN_RELAY_TOKEN="...the same token..."
export RONIN_RELAY_URL="wss://relay.example.com/connect"
export RONIN_TARGET_URL="http://127.0.0.1:8000/webhooks/agent"
ronin-relay connect
```

(`ronin-relay connector` is kept as an alias for `connect`.) The connector dials
out, authenticates, and waits. It reconnects on its own with a growing backoff
if the relay restarts. The laptop opens no inbound port.

### Keep the laptop awake

The connector only works while the laptop is on, awake, and running the command.
If the laptop sleeps, the websocket drops and phone requests return 503 until it
wakes and reconnects. Keep it awake while you are away:

- macOS: run the connector under `caffeinate`, for example
  `caffeinate -s ronin-relay connect --relay ... --target ... --token ...`.
  Also set Energy Saver so the machine does not sleep on AC power.
- Linux: disable suspend (for example
  `systemctl mask sleep.target suspend.target`) or run it as a service on a
  machine that stays on.

This tool cannot reach a laptop that is off. There is no wake-on-LAN. See the
Honest limits section.

## Mobile web UI

The relay serves a phone-friendly web page at `/`. Open
`https://relay.example.com/` on your phone:

1. Paste your token once and tap Save. The token is stored only on the device
   (browser storage) and is never baked into the served page.
2. Type a task (a JSON body), pick a method and path, and tap Send.
3. The reply from the Ronin gateway shows on the page.

The page is a single self-contained HTML file with inline CSS and vanilla
JavaScript. It loads NO external resources: no CDN scripts, no web fonts, no
remote images, no analytics. It posts to this relay's own `/api/task` with your
token as a bearer header, over the same HTTPS origin. The relay also sends a
strict Content-Security-Policy that forbids any off-box resource.

## Sending a task from the phone (or any client)

Besides the web UI, any HTTP client (a shortcut, a small app, curl) can POST to
the relay:

```
curl -X POST https://relay.example.com/api/task \
  -H "Authorization: Bearer $RONIN_RELAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"method": "POST", "path": "/run", "body": {"task": "status"}}'
```

The relay forwards this to the laptop connector, which calls
`http://127.0.0.1:8000/webhooks/agent/run` on the Ronin gateway, and returns the
gateway's status and JSON body.

## Local mode (no relay, same WiFi)

When the phone and the laptop are on the same WiFi, you do not need the relay or
a VM at all. Point the phone straight at the laptop over the LAN.

1. On the laptop, find its LAN address. macOS:
   `ipconfig getifaddr en0` (often `192.168.x.x`). Linux: `hostname -I`.
2. Make the Ronin gateway listen on the LAN interface, not only on `127.0.0.1`
   (for example bind it to `0.0.0.0:8000`), so the phone can reach it.
3. From the phone, on the same WiFi, point your HTTP client at the laptop:

   ```
   # laptop LAN address is 192.168.1.50, gateway on port 8000
   curl -X POST http://192.168.1.50:8000/webhooks/agent/run \
     -H "Content-Type: application/json" \
     -d '{"task": "status"}'
   ```

You can also open the relay's web UI in local mode by running the relay on the
laptop itself and browsing to `http://192.168.1.50:8000/` from the phone; the
page works the same, just over the LAN instead of through a VM.

This keeps everything on your local network and uses no VM. The trade-off:
binding the gateway to the LAN exposes it to every device on that WiFi, so
protect the gateway with its own auth if it does anything sensitive, and prefer
this only on a network you trust. The relay is for when the phone and laptop are
on different networks; local mode is for when they share one.

## Running the tests

The tests are fully offline. They use FastAPI's TestClient with an in-process
fake connector and a fake local target, and prove a request really round-trips
through relay, connector, and target. Python 3.11 to 3.13 is required.

```
python -m pip install -e ".[test]"
python -m pytest
```

## License

MIT. See `LICENSE`.
