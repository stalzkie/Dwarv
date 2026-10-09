"""Regenerates docs/assets/logo.svg from docs/assets/logo.png -- a
block-per-block pixel-art SVG (one <rect> per run of same-row filled
cells, merged horizontally so adjacent filled cells become one rect
instead of many 1x1 rects). A maintainer tool, not a runtime dependency;
kept so the logo can be regenerated if the source PNG ever changes.

Same alpha-threshold-per-cell logic as generate_banner.py's ASCII
renderer, just without that script's 0.5 terminal-character aspect
correction (SVG cells are square, not ~2x-tall terminal glyphs) and
rendered as colored rects instead of '#'/' ' text.

Honest caveat: the currently-committed docs/assets/logo.svg was produced
by a one-off interactive script, not saved at the time. This script is a
clean reimplementation of the same approach and renders visually the
same logo, but differs by a handful of 1-cell boundary rects at
anti-aliased edges (confirmed: not byte-identical to the committed file
at the default 0.4 threshold, nor at 0.3/0.45/0.5/0.55/0.6). Re-running
this does not reproduce the exact committed file -- review the output
before replacing it.

Usage: python scripts/generate_logo_svg.py [cols] [hex_color]
"""

import sys
from pathlib import Path

from PIL import Image

LOGO_PATH = Path(__file__).resolve().parents[1] / "docs" / "assets" / "logo.png"
SVG_PATH = Path(__file__).resolve().parents[1] / "docs" / "assets" / "logo.svg"
DEFAULT_COLS = 48
DEFAULT_COLOR = "#3b9eff"  # lighter accent blue -- matches the GUI's --accent token


def _grid(cols: int) -> list[list[bool]]:
    img = Image.open(LOGO_PATH).convert("RGBA")
    w, h = img.size
    rows = round(cols * (h / w))
    cell_w, cell_h = w / cols, h / rows

    grid = []
    for r in range(rows):
        y0, y1 = int(r * cell_h), int((r + 1) * cell_h)
        row = []
        for c in range(cols):
            x0, x1 = int(c * cell_w), int((c + 1) * cell_w)
            total = opaque = 0
            for yy in range(y0, max(y0 + 1, y1)):
                for xx in range(x0, max(x0 + 1, x1)):
                    total += 1
                    if img.getpixel((xx, yy))[3] > 128:
                        opaque += 1
            row.append((opaque / total if total else 0) > 0.4)
        grid.append(row)
    return grid


def render(cols: int = DEFAULT_COLS, color: str = DEFAULT_COLOR) -> str:
    grid = _grid(cols)
    rows = len(grid)

    rects = []
    for r, row in enumerate(grid):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            rects.append(
                f'<rect x="{start}" y="{r}" width="{c - start}" height="1" fill="{color}"/>'
            )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cols} {rows}" shape-rendering="crispEdges">'
    ]
    lines.extend(rects)
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    cols = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COLS
    color = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_COLOR
    svg = render(cols, color)
    SVG_PATH.write_text(svg, encoding="utf-8")
    print(f"wrote {SVG_PATH} ({len(svg)} bytes)")
