"""The ronin launch panda — a real panda face rendered in half-block art:
a round white head, two ears, the signature dark eye-patches (with eyes),
and a nose. Generated from shapes (see tools/build_panda.py), not typed by
hand, so it actually reads as a panda. Shown beside the gradient RONIN wordmark."""
from __future__ import annotations

from rich.console import Console

PANDA = (
    "    ▄▄▄▄▄               ▄▄▄▄▄\n"
    "  ▄███████▄           ▄███████▄\n"
    "  █████████▄▄▄▄▄█▄▄▄▄▄█████████\n"
    "  █████████████████████████████\n"
    "   ▀█████████████████████████▀\n"
    "    █████▀   ▀█████▀   ▀█████\n"
    "   █████       ███       █████\n"
    "   ████     ▄   █   ▄     ████\n"
    "  ▀████▄   ▀█▀ ▄█▄ ▀█▀   ▄████▀\n"
    "   █████▄     ▄███▄     ▄█████\n"
    "   ▀██████▄▄▄██▀▀▀██▄▄▄██████▀\n"
    "    ▀█████████▄   ▄█████████▀\n"
    "      ▀███████████████████▀\n"
    "         ▀▀███████████▀▀"
)


def _panda_text():
    from rich.text import Text
    return Text(PANDA.strip("\n"), style="grey85")


def _lockup():
    """The ASCII panda on the left, the RONIN gradient wordmark beside it."""
    from rich.table import Table

    from .banner import _W as _RW, _banner_text

    grid = Table.grid(padding=(0, 4))
    grid.add_column(vertical="middle")
    grid.add_column(vertical="middle")
    grid.add_row(_panda_text(), _banner_text(_RW))
    return grid


def render_panda(console: Console) -> None:
    """Print the ASCII panda + RONIN wordmark lockup."""
    console.print(_lockup())
