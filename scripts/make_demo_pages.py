#!/usr/bin/env python3
"""Create two fake comic pages so the pipeline can be smoke-tested."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        path = Path("/usr/share/fonts/truetype/dejavu") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def panel(draw, box, fill, title, body):
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=fill, outline=(20, 20, 20), width=8)
    draw.rounded_rectangle((x0 + 24, y0 + 24, x0 + 360, y0 + 120), 16, fill=(250, 250, 245), outline=(20, 20, 20), width=3)
    draw.text((x0 + 40, y0 + 44), title, fill=(20, 20, 20), font=font(28))
    draw.text((x0 + 40, y0 + 160), body, fill=(250, 250, 245), font=font(36))


def page1(path: Path) -> None:
    im = Image.new("RGB", (1600, 2400), (214, 198, 170))  # yellowed paper
    d = ImageDraw.Draw(im)
    # fake scanner border
    d.rectangle((0, 0, 1599, 2399), outline=(240, 240, 240), width=28)
    panel(d, (80, 80, 760, 760), (46, 78, 126), "Panel 1", "Night. Rain.")
    panel(d, (820, 80, 1520, 760), (92, 44, 44), "Panel 2", "A knock.")
    panel(d, (80, 820, 1520, 1500), (36, 90, 64), "Panel 3", "Wide beat.")
    panel(d, (80, 1580, 720, 2280), (70, 52, 110), "Panel 4", "Close-up.")
    panel(d, (800, 1580, 1520, 2280), (40, 40, 40), "Panel 5", "The door.")
    im.save(path, quality=92)


def page2(path: Path) -> None:
    im = Image.new("RGB", (1600, 2400), (228, 220, 205))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1599, 2399), outline=(250, 250, 250), width=20)
    panel(d, (70, 70, 1530, 2320), (24, 28, 48), "Splash", "Full-page hit.")
    im.save(path, quality=92)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-scans")
    out.mkdir(parents=True, exist_ok=True)
    page1(out / "001.jpg")
    page2(out / "002.jpg")
    print(f"Wrote {out}/001.jpg and 002.jpg")


if __name__ == "__main__":
    main()
