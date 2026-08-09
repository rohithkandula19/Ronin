"""Print the effective configuration."""

from __future__ import annotations

import json

from cfgmerge.merge import merge

DEFAULTS = {"retries": 3, "log": {"level": "info", "path": "/var/log/app"}}
SITE = {"retries": 5, "log": {"level": "debug"}, "region": "eu-west-1"}


def main() -> None:
    print(json.dumps(merge(DEFAULTS, SITE), sort_keys=True))
