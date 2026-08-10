"""``python -m slugkit "Some Title"`` — used by the content import script."""

from __future__ import annotations

import sys

from slugkit.slugify import slugify


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m slugkit TITLE [MAX_LENGTH]", file=sys.stderr)
        return 2
    title = args[0]
    max_length = int(args[1]) if len(args) > 1 else 60
    print(slugify(title, max_length))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
