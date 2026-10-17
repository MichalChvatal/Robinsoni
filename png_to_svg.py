#!/usr/bin/env python3
"""Convert a PNG image into a simple vector-like SVG by tracing foreground pixels into rectangles.

This is a practical Python approach for logo/graphics conversion when a true vectorizing engine
is not available. It keeps the output editable in SVG tools (Inkscape, Illustrator, Figma).

Example:
    python png_to_svg.py C:\\repo\\Robinsoni\\generated-logo-try.png C:\\repo\\Robinsoni\\generated-logo-try.svg --cell-size 4
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


def _luminance(rgb):
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def build_svg(image_path: Path, output_path: Path, cell_size: int, threshold: int, invert: bool) -> None:
    with Image.open(image_path) as img:
        if img.mode not in ("RGBA", "RGB", "L", "LA"):
            img = img.convert("RGBA")

        if img.mode in ("RGBA", "LA"):
            rgba = img.convert("RGBA")
            pixels = rgba.load()
            width, height = rgba.size
            foreground = []
            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    value = _luminance((r, g, b)) if a > 0 else 255
                    if a == 0:
                        continue
                    if invert:
                        is_fg = value >= threshold
                    else:
                        is_fg = value <= threshold
                    if is_fg:
                        foreground.append((x, y))
        else:
            gray = img.convert("L")
            width, height = gray.size
            pixels = gray.load()
            foreground = []
            for y in range(height):
                for x in range(width):
                    value = pixels[x, y]
                    if invert:
                        is_fg = value >= threshold
                    else:
                        is_fg = value <= threshold
                    if is_fg:
                        foreground.append((x, y))

    if not foreground:
        raise ValueError(f"No foreground pixels found in {image_path}. Try a different threshold.")

    # Compute a coarse but editable vector approximation by grouping connected pixels into rectangular cells.
    cells = {}
    for x, y in foreground:
        cx = (x // cell_size) * cell_size
        cy = (y // cell_size) * cell_size
        cells[(cx, cy)] = True

    width, height = Image.open(image_path).size
    rects = []
    for (cx, cy) in sorted(cells):
        rects.append(f'<rect x="{cx}" y="{cy}" width="{cell_size}" height="{cell_size}" />')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="none"/>
  <g fill="#000000" stroke="none">
    {'\n    '.join(rects)}
  </g>
</svg>
'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    print(f"Saved SVG vector approximation to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a PNG logo into a simple SVG vector approximation.")
    parser.add_argument("input", type=Path, help="PNG input path")
    parser.add_argument("output", type=Path, help="SVG output path")
    parser.add_argument("--cell-size", type=int, default=4, help="Vector cell size (smaller = higher detail, larger = cleaner SVG)")
    parser.add_argument("--threshold", type=int, default=180, help="Luminance threshold for foreground detection")
    parser.add_argument("--invert", action="store_true", help="Interpret darker pixels as background and lighter pixels as foreground")
    args = parser.parse_args()

    build_svg(args.input, args.output, args.cell_size, args.threshold, args.invert)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
