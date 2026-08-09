"""Build the update manifest consumed by the Windows updater.

The updater walks the manifest top to bottom and takes the first entry whose
channel matches the one it was installed with, so the newest release for a
channel has to appear first. The file itself is CRLF because the updater's INI
reader is the one shipped with the installer and it does not accept bare LF.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

HEADER = "; ronin update manifest - newest release first"


@dataclass(frozen=True)
class Release:
    """One published build."""

    version: str
    channel: str
    sha256: str


def parse_index(text: str) -> list[Release]:
    """Parse the `version channel sha256` index the build job writes."""
    releases: list[Release] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 3:
            raise ValueError(f"malformed index line: {raw!r}")
        releases.append(Release(*fields))
    return releases


def _version_key(release: Release) -> tuple[int, ...]:
    """Order releases numerically: 1.10.0 is newer than 1.9.0, not older."""
    return tuple(int(part) for part in release.version.split("."))


def render_manifest(releases: Iterable[Release]) -> str:
    """Render the manifest text, newest release first."""
    lines = [HEADER, ""]
    for release in sorted(releases, key=_version_key, reverse=True):
        lines.append(f"[{release.version}]")
        lines.append(f"channel={release.channel}")
        lines.append(f"sha256={release.sha256}")
        lines.append("")
    return "\r\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path, help="the release index written by the build job")
    parser.add_argument("--out", type=Path, default=Path("manifest.ini"))
    args = parser.parse_args(argv)
    releases = parse_index(args.index.read_text(encoding="utf-8"))
    args.out.write_bytes(render_manifest(releases).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
