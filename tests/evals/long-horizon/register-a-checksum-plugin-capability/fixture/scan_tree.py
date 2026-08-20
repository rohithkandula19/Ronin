#!/usr/bin/env python3
"""Fingerprint every file under a root and flag byte-identical duplicates.

    python3 scan_tree.py --root samples
"""

from __future__ import annotations

import argparse
import sys

from inspectorkit.errors import InspectorError
from inspectorkit.util.fmt import human_size
from inspectorkit.util.io import fingerprint, read_blob, walk_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="samples")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seen: dict[str, list[str]] = {}
    try:
        for path in walk_files(args.root):
            blob = read_blob(path)
            mark = fingerprint(blob.data)
            seen.setdefault(mark, []).append(str(path))
            print(f"{mark}  {human_size(blob.size):>9}  {path}")
    except InspectorError as exc:
        print(f"scan failed: {exc}", file=sys.stderr)
        return 1
    duplicates = {mark: paths for mark, paths in seen.items() if len(paths) > 1}
    for mark, paths in sorted(duplicates.items()):
        print(f"duplicate {mark}: {', '.join(sorted(paths))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
