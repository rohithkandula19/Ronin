"""Regenerate the ronin launch panda — the PANDA_FRAMES tuple in panda_art.py.

A panda is a *white* face with *dark* eye-patches, ears and nose. On a dark
terminal the white is the "ink", so we draw the silhouette as filled ellipses
and punch the eye-patches / nose out as holes (background). The big dark
eye-patches are the unmistakable panda signature.

We produce 4 dance frames — head + body sway opposite each other and the arms
wave — so the launch banner animates. We render at 2x vertical resolution and
pack each pair of pixel rows into a half-block glyph (▀ ▄ █ space) so the
result is roughly square in a terminal.

Run:    python packages/cli/tools/build_panda_face.py
Preview side-by-side PNG: --png    (needs pillow)

Paste the printed PANDA_FRAMES block into panda_art.py.
"""
from __future__ import annotations

import sys

W, H = 36, 48  # 36 chars wide; up to 24 half-block rows


def _ellipse(bm, cx, cy, rx, ry, val):
    for y in range(H):
        for x in range(W):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                bm[y][x] = val


def _build_frame(phase: int) -> list[list[int]]:
    bm = [[0] * W for _ in range(H)]
    sway = (-1, 0, 1, 0)[phase]
    cx = 18 + sway
    # face
    _ellipse(bm, cx, 14, 12, 10, 1)
    _ellipse(bm, cx - 9, 4, 4, 4, 1)
    _ellipse(bm, cx + 9, 4, 4, 4, 1)
    _ellipse(bm, cx - 4, 13, 3.5, 5, 0)
    _ellipse(bm, cx + 4, 13, 3.5, 5, 0)
    _ellipse(bm, cx - 3, 14, 1.2, 1.2, 1)
    _ellipse(bm, cx + 3, 14, 1.2, 1.2, 1)
    _ellipse(bm, cx, 19, 2, 1.5, 0)
    # body sways opposite the head
    body_cx = 18 - sway
    _ellipse(bm, body_cx, 30, 9, 6, 1)
    _ellipse(bm, body_cx - 4, 38, 2.5, 3, 1)
    _ellipse(bm, body_cx + 4, 38, 2.5, 3, 1)
    # arms
    left_up = phase in (0, 1)
    right_up = phase in (1, 2)
    if left_up:
        _ellipse(bm, body_cx - 9, 24, 2.5, 5, 1)
        _ellipse(bm, body_cx - 10, 20, 2.5, 2.5, 1)
    else:
        _ellipse(bm, body_cx - 11, 32, 3, 2.5, 1)
        _ellipse(bm, body_cx - 13, 33, 2, 2, 1)
    if right_up:
        _ellipse(bm, body_cx + 9, 24, 2.5, 5, 1)
        _ellipse(bm, body_cx + 10, 20, 2.5, 2.5, 1)
    else:
        _ellipse(bm, body_cx + 11, 32, 3, 2.5, 1)
        _ellipse(bm, body_cx + 13, 33, 2, 2, 1)
    return bm


def _encode(bm: list[list[int]]) -> str:
    glyph = {(1, 1): "█", (1, 0): "▀", (0, 1): "▄", (0, 0): " "}
    rows = [
        "".join(glyph[(bm[2 * r][x], bm[2 * r + 1][x])] for x in range(W)).rstrip()
        for r in range(H // 2)
    ]
    while rows and len(rows[-1].strip()) <= 1:
        rows.pop()
    return "\n".join(rows)


def build_frames() -> list[str]:
    return [_encode(_build_frame(p)) for p in range(4)]


def main() -> None:
    frames = build_frames()
    print("PANDA_FRAMES = (")
    for i, art in enumerate(frames):
        print(f"    # frame {i} ({len(art.splitlines())} rows)")
        lines = art.split("\n")
        if not lines:
            continue
        # join as a single multi-line string literal
        joined = "\\n".join(lines)
        print(f"    {joined!r},")
    print(")")
    if "--png" in sys.argv:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 22)
        cw, ch = 13, 24
        nrows = max(len(f.split("\n")) for f in frames)
        fw = W * cw + 16
        fh = nrows * ch + 16
        img = Image.new("RGB", (fw * 4 + 30, fh + 30), (22, 22, 30))
        d = ImageDraw.Draw(img)
        for fi, art in enumerate(frames):
            ox = 10 + fi * fw
            d.rectangle([ox, 10, ox + fw, 10 + fh], outline=(60, 60, 80))
            for i, line in enumerate(art.split("\n")):
                d.text((ox + 8, 18 + i * ch), line, font=font, fill=(235, 235, 235))
        img.save("/tmp/panda_dance.png")
        print("\n[wrote /tmp/panda_dance.png]", file=sys.stderr)


if __name__ == "__main__":
    main()
