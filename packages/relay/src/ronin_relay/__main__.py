"""Command line entrypoints.

  ronin-relay serve         start the relay server (on the free VM)
  ronin-relay connect       start the laptop connector (outbound only)

Examples:

  ronin-relay connect --relay wss://relay.example.com/connect \\
      --target http://127.0.0.1:8000/webhooks/agent \\
      --token "$RONIN_RELAY_TOKEN"

"relay" is kept as an alias for "serve", and "connector" as an alias for
"connect". The connect verb accepts --relay/--target/--token flags and also
falls back to the RONIN_RELAY_URL / RONIN_TARGET_URL / RONIN_RELAY_TOKEN
environment variables, so either style works. Every path fails closed if the
token is missing or weaker than the minimum length.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .config import (
    ConfigError,
    ConnectorConfig,
    connector_config,
    relay_config_from_env,
)


def _run_relay() -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required to run the relay: pip install uvicorn", file=sys.stderr)
        return 1
    from .relay import create_app

    config = relay_config_from_env()
    app = create_app(config)
    host = os.environ.get("RONIN_RELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("RONIN_RELAY_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
    return 0


def build_connect_config(args: list[str]) -> ConnectorConfig:
    """Parse the "connect" flags into a validated ConnectorConfig.

    Flags take precedence; anything omitted falls back to the matching
    environment variable. The token can come from --token or RONIN_RELAY_TOKEN.
    Validation (token present and long enough, both URLs present) is shared with
    the env path, so the flag form is exactly as strict.
    """
    parser = argparse.ArgumentParser(
        prog="ronin-relay connect",
        description="Run the laptop connector (outbound only) to the relay.",
    )
    parser.add_argument(
        "--relay",
        default=os.environ.get("RONIN_RELAY_URL"),
        help="relay websocket URL, e.g. wss://relay.example.com/connect",
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("RONIN_TARGET_URL"),
        help="the ONE local gateway URL to forward to, "
        "e.g. http://127.0.0.1:8000/webhooks/agent",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("RONIN_RELAY_TOKEN"),
        help="shared token (or set RONIN_RELAY_TOKEN); never echoed",
    )
    parser.add_argument(
        "--target-timeout",
        type=float,
        default=float(os.environ.get("RONIN_TARGET_TIMEOUT", "25")),
        help="per request timeout calling the local target, seconds",
    )
    ns = parser.parse_args(args)
    return connector_config(
        token=ns.token,
        relay_url=ns.relay,
        target_url=ns.target,
        target_timeout_seconds=ns.target_timeout,
    )


def _run_connector(args: list[str]) -> int:
    from .connector import run_connector

    config = build_connect_config(args)
    try:
        asyncio.run(run_connector(config))
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    # One log line per record. The relay payload is already structured JSON
    # (see relay.log_event); the timestamp and logger name prefix make grepping
    # easy without hiding the JSON.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"serve", "relay", "connect", "connector"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if args[0] in {"serve", "relay"}:
            return _run_relay()
        return _run_connector(args[1:])
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
