#!/usr/bin/env python3
"""Seed a store through the service layer and dump it as JSON.

    python3 scripts/seed.py --out /tmp/seeded.json

This script is why the rules live in the services: it never builds an App, so any
rule enforced in a route would not apply to seeded data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notedeck.config import Settings  # noqa: E402
from notedeck.errors import NotedeckError  # noqa: E402
from notedeck.util.clock import FixedClock  # noqa: E402
from notedeck.wiring import build_services  # noqa: E402

PEOPLE = (
    ("ada@example.com", "Ada", "pro"),
    ("grace@example.com", "Grace", "free"),
)

NOTES = (
    ("Kitchen rota", "Weeks alternate between the two of us."),
    ("Reading list", "Two books left in the pile."),
    ("Shopping", "Coffee, oats, tinned tomatoes."),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/app.json")
    parser.add_argument("--out", default="-")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    services = build_services(Settings.load(args.config), FixedClock("2024-01-01T00:00:00"))
    try:
        for email, name, plan in PEOPLE:
            services.users.create(email=email, display_name=name, plan=plan)
        for title, body in NOTES:
            services.notes.create(user_id="user-1", title=title, body=body)
    except NotedeckError as exc:
        print(f"seeding failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(services.store.snapshot(), indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(payload)
    else:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"seeded {len(PEOPLE)} users and {len(NOTES)} notes into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
