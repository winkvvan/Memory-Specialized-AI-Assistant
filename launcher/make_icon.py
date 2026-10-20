"""Generate a simple launcher.ico (no external assets required)."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont | None:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return None


def build(sizes=(256, 128, 64, 48, 32, 16)) -> Image.Image:
    images = []
    for size in sizes:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        radius = max(2, int(size * 0.22))
        draw.rounded_rectangle(
            [0, 0, size - 1, size - 1],
            radius=radius,
            fill=(38, 110, 220, 255),
        )
        if size >= 32:
            font = _font(int(size * 0.62))
            if font:
                draw.text(
                    (int(size * 0.30), int(size * 0.16)),
                    "C",
                    font=font,
                    fill=(255, 255, 255, 255),
                )
        images.append(img)
    return images[0]


def main() -> None:
    out = Path(__file__).resolve().parent / "launcher.ico"
    sizes = (256, 128, 64, 48, 32, 16)
    master = build(sizes)
    master.save(out, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
