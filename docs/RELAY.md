# Remote access (relay)

Status: working scaffold with tests. Not deployed. No users. Run it yourself if
you want remote access to your own gateway.

The relay lets a phone send a task to your local Ronin gateway without opening
an inbound port on the laptop. It lives in `packages/relay` (the `ronin_relay`
package) and is wired into the CLI as the `ronin relay` command group.

## How it works

There are two sides:

1. A relay server you run on a VM you own (`ronin relay serve`). It exposes a
   small HTTP and websocket surface and serves a single self-contained mobile
   web page at `/`.
2. A connector you run on the laptop next to the gateway (`ronin relay
   connect`). It dials OUTBOUND to the relay over a websocket and holds that
   connection open.

A phone posts a task to the relay. The relay forwards it down the open websocket
to the connector. The connector makes ONE local HTTP call to its single
configured target (the gateway) and ships the reply back the same way. The
laptop opens no inbound port.

## Security model

This is the part that matters. It is preserved exactly from the standalone
package:

- Outbound only. The connector dials out. The laptop firewall and router stay
  closed. There is no inbound listener on the laptop.
- One configured target. The connector forwards to exactly one local URL and
  nothing else. The phone never chooses the host. There is no shell, no eval,
  no arbitrary URL or command.
- Token auth. Every endpoint requires a shared bearer token, compared in
  constant time. The process fails closed if the token is missing or shorter
  than the minimum length. Use TLS so the token is protected in transit.

The boundary is enforced by runtime and config (outbound-only dialing, a single
fixed target URL, mandatory token, fail-closed validation in
`packages/relay/src/ronin_relay/security.py` and `config.py`). Merging the code
into this monorepo does not change that boundary; nothing here weakens it.

## Commands

    ronin relay serve     run the relay server (VM side); needs RONIN_RELAY_TOKEN
    ronin relay connect   run the laptop connector (dials OUT to the relay)
    ronin relay webui     print the mobile web page the relay serves at "/"

`python -m ronin_relay` keeps working too, with the same `serve` / `connect`
verbs.

### Example

On the VM:

    RONIN_RELAY_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))") \
      ronin relay serve --port 8000

On the laptop:

    ronin relay connect \
      --relay wss://relay.example.com/connect \
      --target http://127.0.0.1:8000/webhooks/agent \
      --token "$RONIN_RELAY_TOKEN"

Flags win over the environment. Omitted flags fall back to RONIN_RELAY_URL,
RONIN_TARGET_URL, RONIN_RELAY_TOKEN, and RONIN_TARGET_TIMEOUT.

## More

Architecture notes: `packages/relay/docs/ARCHITECTURE.md`.
Package README: `packages/relay/README.md`.
