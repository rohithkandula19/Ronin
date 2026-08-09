#!/usr/bin/env python3
"""Replay a list of requests through the app and print the responses.

    python3 serve.py --requests scripts/example_requests.json --now 2024-01-01T00:00:00

There is no socket on purpose: the transport is the least interesting part of this
service, and a replayable request list is what the team debugs with.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from notedeck.config import Settings
from notedeck.util.clock import FixedClock, SystemClock
from notedeck.web.request import Request
from notedeck.wiring import build_app, build_services


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/app.json")
    parser.add_argument("--requests", required=True, help="JSON list of requests")
    parser.add_argument("--now", default=None, help="fix the clock, for reproducible output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        script = json.loads(Path(args.requests).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"no such request file: {args.requests}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.requests} is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(script, list):
        print(f"{args.requests} must contain a JSON list of requests", file=sys.stderr)
        return 2

    clock = FixedClock(args.now) if args.now else SystemClock()
    services = build_services(Settings.load(args.config), clock)
    app = build_app(services)

    worst = 0
    for entry in script:
        request = Request(
            method=str(entry.get("method", "GET")),
            path=str(entry.get("path", "/")),
            body=entry.get("body") or {},
            query={str(k): str(v) for k, v in (entry.get("query") or {}).items()},
        )
        response = app.handle(request)
        worst = max(worst, response.status)
        print(
            json.dumps(
                {
                    "request": f"{request.method} {request.path}",
                    "status": response.status,
                    "body": dict(response.body),
                },
                sort_keys=True,
            )
        )
    return 0 if worst < 500 else 1


if __name__ == "__main__":
    raise SystemExit(main())
