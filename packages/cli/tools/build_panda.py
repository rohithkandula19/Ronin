"""Build the launch panda art from a real panda image.

Pipeline: image (PNG/JPEG) -> ffmpeg decode+downscale -> PPM (P6) -> flood-fill
the background to transparent (keeping the panda's own white face) -> render as
truecolor *half-block* art (each character holds two vertical pixels via
foreground/background colour) -> write ``panda_pixels.py`` into the package.

Run:  python packages/cli/tools/build_panda.py /tmp/panda/panda.png

This is a build-time tool; the shipped CLI only imports the generated
``panda_pixels.py`` and never needs ffmpeg or a network connection.
"""
from __future__ import annotations

import subprocess
import sys
from collections import deque
from pathlib import Path

TARGET_W = 52          # characters wide
WHITE_CUT = 232        # r,g,b all above this (from a border-connected region) = background


def to_ppm(src: Path, width: int) -> bytes:
    # even height keeps half-block pairing clean
    vf = f"scale={width}:-1:flags=lanczos"
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vf", vf, "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    # we need dimensions; ask ffprobe via a second scaled probe is overkill —
    # instead emit a real PPM so the header carries width/height.
    ppm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vf", vf, "-f", "image2pipe",
         "-vcodec", "ppm", "-"],
        capture_output=True, check=True,
    )
    return ppm.stdout


def parse_ppm(data: bytes) -> tuple[int, int, list[tuple[int, int, int]]]:
    assert data[:2] == b"P6", "expected binary PPM"
    # tokenise header (magic, w, h, maxval), skipping whitespace + comments
    idx = 2
    toks: list[bytes] = []
    while len(toks) < 3:
        while idx < len(data) and data[idx] in b" \t\r\n":
            idx += 1
        if data[idx:idx + 1] == b"#":
            while idx < len(data) and data[idx] not in b"\r\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and data[idx] not in b" \t\r\n":
            idx += 1
        toks.append(data[start:idx])
    w, h, _maxval = (int(t) for t in toks)
    idx += 1  # single whitespace after maxval
    px = data[idx:idx + w * h * 3]
    pixels = [(px[i], px[i + 1], px[i + 2]) for i in range(0, len(px), 3)]
    return w, h, pixels


def background_mask(w: int, h: int, px: list[tuple[int, int, int]]) -> list[bool]:
    """Flood-fill near-white from the borders. Interior white (the face) survives."""
    def near_white(p: tuple[int, int, int]) -> bool:
        return p[0] >= WHITE_CUT and p[1] >= WHITE_CUT and p[2] >= WHITE_CUT

    transp = [False] * (w * h)
    q: deque[int] = deque()
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if near_white(px[i]) and not transp[i]:
                transp[i] = True
                q.append(i)
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if near_white(px[i]) and not transp[i]:
                transp[i] = True
                q.append(i)
    while q:
        i = q.popleft()
        x, y = i % w, i // w
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not transp[j] and near_white(px[j]):
                    transp[j] = True
                    q.append(j)
    return transp


def crop(w: int, h: int, px, transp):
    cols = [x for x in range(w) if any(not transp[y * w + x] for y in range(h))]
    rows = [y for y in range(h) if any(not transp[y * w + x] for x in range(w))]
    x0, x1 = cols[0], cols[-1] + 1
    y0, y1 = rows[0], rows[-1] + 1
    if (y1 - y0) % 2:           # keep even height for clean half-block pairing
        y1 = min(h, y1 + 1)
    nw, nh = x1 - x0, y1 - y0
    np_ = [px[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]
    nt = [transp[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]
    return nw, nh, np_, nt


def hexc(p: tuple[int, int, int]) -> str:
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def render(w: int, h: int, px, transp) -> list[str]:
    lines: list[str] = []
    for y in range(0, h - 1, 2):
        out: list[str] = []
        for x in range(w):
            ti, bi = y * w + x, (y + 1) * w + x
            top = None if transp[ti] else px[ti]
            bot = None if transp[bi] else px[bi]
            if top is None and bot is None:
                out.append(" ")
            elif top is not None and bot is not None:
                out.append(f"[{hexc(top)} on {hexc(bot)}]▀[/]")
            elif top is not None:
                out.append(f"[{hexc(top)}]▀[/]")
            else:
                out.append(f"[{hexc(bot)}]▄[/]")
        lines.append("".join(out))
    return lines


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/panda/panda.png")
    ppm = to_ppm(src, TARGET_W)
    w, h, px = parse_ppm(ppm)
    transp = background_mask(w, h, px)
    w, h, px, transp = crop(w, h, px, transp)
    lines = render(w, h, px, transp)

    pkg = Path(__file__).resolve().parents[1] / "src" / "ronin_cli"
    out = pkg / "panda_pixels.py"
    body = (
        '"""Auto-generated by tools/build_panda.py — a real panda rendered as\n'
        'truecolor half-block art. Do not edit by hand; re-run the builder."""\n\n'
        "PANDA_LINES = [\n"
        + "".join(f"    {line!r},\n" for line in lines)
        + "]\n"
    )
    out.write_text(body)
    print(f"wrote {out}  ({len(lines)} rows x {w} cols)")


if __name__ == "__main__":
    main()
