"""Regenerate the ronin launch panda (the PANDA constant in panda_art.py).

A panda is a *white* face with *dark* eye-patches, ears and nose. On a dark
terminal the white is the "ink", so we draw the silhouette as filled ellipses
and punch the eye-patches / nose out as holes (background). The big dark
eye-patches are the unmistakable panda signature. We render at 2x vertical
resolution and pack each pair of pixel rows into a half-block glyph (▀ ▄ █ space)
so the result is roughly square in a terminal.

Run:    python packages/cli/tools/build_panda_face.py
Preview: python packages/cli/tools/build_panda_face.py --png   (needs pillow)

Then paste the printed PANDA block into panda_art.py.
"""
from __future__ import annotations

import sys

W, H = 32, 30  # 32 chars wide; 15 half-block rows (trailing stray row trimmed)


def _ellipse(bm, cx, cy, rx, ry, val):
    for y in range(H):
        for x in range(W):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                bm[y][x] = val


def build() -> str:
    bm = [[0] * W for _ in range(H)]
    _ellipse(bm, 16, 16, 14, 12, 1)      # white face
    _ellipse(bm, 6, 5, 4.5, 4.5, 1)      # left ear
    _ellipse(bm, 26, 5, 4.5, 4.5, 1)     # right ear
    _ellipse(bm, 11, 15, 4.2, 5.6, 0)    # left eye-patch (dark)
    _ellipse(bm, 21, 15, 4.2, 5.6, 0)    # right eye-patch (dark)
    _ellipse(bm, 12, 16, 1.4, 1.4, 1)    # left eye
    _ellipse(bm, 20, 16, 1.4, 1.4, 1)    # right eye
    _ellipse(bm, 16, 22, 2.4, 1.7, 0)    # nose (dark)

    glyph = {(1, 1): "█", (1, 0): "▀", (0, 1): "▄", (0, 0): " "}
    rows = [
        "".join(glyph[(bm[2 * r][x], bm[2 * r + 1][x])] for x in range(W)).rstrip()
        for r in range(H // 2)
    ]
    while rows and len(rows[-1].strip()) <= 1:  # trim trailing stray-pixel rows
        rows.pop()
    return "\n".join(rows)


def main() -> None:
    art = build()
    print(art)
    if "--png" in sys.argv:
        from PIL import Image, ImageDraw, ImageFont

        lines = art.split("\n")
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 28)
        cw, ch = 17, 30
        img = Image.new("RGB", (W * cw + 20, len(lines) * ch + 20), (22, 22, 30))
        d = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            d.text((10, 10 + i * ch), line, font=font, fill=(235, 235, 235))
        img.save("/tmp/panda_pil.png")
        print("\n[wrote /tmp/panda_pil.png]", file=sys.stderr)


if __name__ == "__main__":
    main()
