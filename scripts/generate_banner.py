"""Regenerates the pure-ASCII CLI startup banner from docs/assets/logo.png.
A maintainer tool, not a runtime dependency -- the banner itself is a
plain string baked into cli.py; this script is just how it was produced,
kept for reproducibility if the logo ever changes.

Pure ASCII only, deliberately: a Unicode block character here would risk
the exact class of bug already found and fixed in agent/render.py --
UnicodeEncodeError on a legacy Windows console (cp1252 codepage), which
has no block-drawing characters at all.

Usage: python scripts/generate_banner.py [cols]
"""

import sys
from pathlib import Path

from PIL import Image

LOGO_PATH = Path(__file__).resolve().parents[1] / "docs" / "assets" / "logo.png"
DEFAULT_COLS = 32


def render(cols: int = DEFAULT_COLS) -> str:
    img = Image.open(LOGO_PATH).convert("RGBA")
    w, h = img.size
    rows = round(cols * (h / w) * 0.5)  # terminal chars are ~2x taller than wide
    cell_w, cell_h = w / cols, h / rows

    lines = []
    for r in range(rows):
        y0, y1 = int(r * cell_h), int((r + 1) * cell_h)
        line = []
        for c in range(cols):
            x0, x1 = int(c * cell_w), int((c + 1) * cell_w)
            total = opaque = 0
            for yy in range(y0, max(y0 + 1, y1)):
                for xx in range(x0, max(x0 + 1, x1)):
                    total += 1
                    if img.getpixel((xx, yy))[3] > 128:
                        opaque += 1
            line.append("#" if (opaque / total if total else 0) > 0.4 else " ")
        lines.append("".join(line).rstrip())
    return "\n".join(lines)


if __name__ == "__main__":
    cols = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COLS
    print(render(cols))
