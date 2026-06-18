"""The `ronin relay` command group.

Remote access for the local Ronin gateway without opening an inbound port on the
laptop. Two sides:

    ronin relay serve     run the relay server (on a VM you own)
    ronin relay connect   run the laptop connector (dials OUT to the relay)

The connector holds a persistent OUTBOUND websocket to the relay. The relay
forwards a phone request down that socket, the connector makes ONE local HTTP
call to its single configured target, and the reply travels back the same way.
The laptop opens no inbound port. Every path requires a shared token.

ronin_relay is imported LAZILY inside each command body so the core CLI import
stays light and offline: importing this module pulls in nothing from the relay
package (no fastapi, uvicorn, websockets) until a command actually runs.

`python -m ronin_relay` keeps working unchanged; this group just exposes the
same entrypoints under `ronin relay`.
"""
from __future__ import annotations

import os

import typer
from rich.console import Console

relay_app = typer.Typer(
    help="Remote access for the local Ronin gateway via a self-hosted relay. "
         "Outbound-only connector, token auth, no arbitrary command execution.",
)
console = Console()


def _require_relay() -> None:
    """Fail with a clear message if the ronin-relay package is not installed.

    In the uv workspace `uv sync --all-packages` installs it. A standalone
    `pip install ronin-cli` does not pull it in (relay deps are opt-in), so we
    tell the user how to get it rather than dumping an ImportError.
    """
    try:
        import ronin_relay  # noqa: F401
    except ImportError:
        console.print(
            "[red]x[/red] the relay package is not installed. Install it with: "
            "pip install ronin-relay  (or, in the workspace, "
            "uv sync --all-packages)"
        )
        raise typer.Exit(1)


@relay_app.command("serve")
def relay_serve(
    host: str = typer.Option(
        None, "--host",
        help="Bind address for the relay server. Defaults to RONIN_RELAY_HOST "
             "or 0.0.0.0.",
    ),
    port: int = typer.Option(
        None, "--port",
        help="Port for the relay server. Defaults to RONIN_RELAY_PORT or 8000.",
    ),
) -> None:
    """Run the relay server (the VM side).

    Reads RONIN_RELAY_TOKEN and the rate-limit settings from the environment and
    fails closed if the token is missing or too short. Requires uvicorn.

    Example:
      RONIN_RELAY_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))") \\
        ronin relay serve --port 8000
    """
    _require_relay()
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]x[/red] uvicorn is required to run the relay: "
            "pip install uvicorn"
        )
        raise typer.Exit(1)

    from ronin_relay.config import ConfigError, relay_config_from_env
    from ronin_relay.relay import create_app

    try:
        config = relay_config_from_env()
    except ConfigError as exc:
        console.print(f"[red]x[/red] config error: {exc}")
        raise typer.Exit(1)

    app = create_app(config)
    bind_host = host or os.environ.get("RONIN_RELAY_HOST", "0.0.0.0")
    bind_port = port if port is not None else int(os.environ.get("RONIN_RELAY_PORT", "8000"))
    console.print(f"[dim]relay listening on {bind_host}:{bind_port}[/dim]")
    uvicorn.run(app, host=bind_host, port=bind_port)


@relay_app.command("connect")
def relay_connect(
    relay: str = typer.Option(
        None, "--relay",
        help="Relay websocket URL, e.g. wss://relay.example.com/connect. "
             "Falls back to RONIN_RELAY_URL.",
    ),
    target: str = typer.Option(
        None, "--target",
        help="The ONE local gateway URL to forward to, e.g. "
             "http://127.0.0.1:8000/webhooks/agent. Falls back to RONIN_TARGET_URL. "
             "The connector forwards to this one address and nothing else.",
    ),
    token: str = typer.Option(
        None, "--token",
        help="Shared token (or set RONIN_RELAY_TOKEN); never echoed.",
    ),
    target_timeout: float = typer.Option(
        None, "--target-timeout",
        help="Per-request timeout calling the local target, seconds. "
             "Defaults to RONIN_TARGET_TIMEOUT or 25.",
    ),
) -> None:
    """Run the laptop connector (outbound only) that dials OUT to the relay.

    Flags win over the environment; anything omitted falls back to the matching
    variable (RONIN_RELAY_URL / RONIN_TARGET_URL / RONIN_RELAY_TOKEN /
    RONIN_TARGET_TIMEOUT). Fails closed if the token is missing or too short, or
    if either URL is missing.

    Example:
      ronin relay connect --relay wss://relay.example.com/connect \\
        --target http://127.0.0.1:8000/webhooks/agent --token "$RONIN_RELAY_TOKEN"
    """
    _require_relay()
    import asyncio

    from ronin_relay.config import ConfigError, connector_config
    from ronin_relay.connector import run_connector

    resolved_timeout = (
        target_timeout
        if target_timeout is not None
        else float(os.environ.get("RONIN_TARGET_TIMEOUT", "25"))
    )

    try:
        config = connector_config(
            token=token or os.environ.get("RONIN_RELAY_TOKEN"),
            relay_url=relay or os.environ.get("RONIN_RELAY_URL"),
            target_url=target or os.environ.get("RONIN_TARGET_URL"),
            target_timeout_seconds=resolved_timeout,
        )
    except ConfigError as exc:
        console.print(f"[red]x[/red] config error: {exc}")
        raise typer.Exit(1)

    console.print(
        f"[dim]connecting out to {config.relay_url}; forwarding only to "
        f"{config.target_url}[/dim]"
    )
    try:
        asyncio.run(run_connector(config))
    except KeyboardInterrupt:
        pass


@relay_app.command("webui")
def relay_webui() -> None:
    """Print the mobile web UI served by the relay at "/".

    The relay serves this single self-contained HTML page (no CDN, no external
    resources) when you run `ronin relay serve`. There is no separate web server
    to start: the page is part of the relay. This command writes the exact bytes
    the relay would serve, so you can inspect or save them.
    """
    _require_relay()
    from ronin_relay.webui import PAGE_HTML

    # Write the raw page to stdout so it can be piped or saved unchanged.
    typer.echo(PAGE_HTML)
