"""
Generate the favicon and PWA icons from app/static/logo.png.

Run after replacing the logo:

    python make_icons.py

PWA install prompts require 192x192 and 512x512, and maskable icons need the
artwork inset so a circular mask doesn't clip it.
"""
import pathlib
import sys

SRC = pathlib.Path("app/static/logo.png")
OUT = pathlib.Path("app/static")
BRAND_BG = (253, 249, 247, 255)  # matches --bg so the icon never shows letterboxing


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required:  pip install pillow")
        return 1

    if not SRC.exists():
        print(f"Missing {SRC}. Save your logo there first.")
        return 1

    logo = Image.open(SRC).convert("RGBA")

    def render(size: int, inset_ratio: float) -> Image.Image:
        canvas = Image.new("RGBA", (size, size), BRAND_BG)
        inner = int(size * (1 - inset_ratio * 2))
        art = logo.copy()
        art.thumbnail((inner, inner), Image.LANCZOS)
        canvas.paste(art, ((size - art.width) // 2, (size - art.height) // 2), art)
        return canvas

    # Maskable icons get a wider safe margin so a circular crop keeps the mark.
    render(192, 0.10).save(OUT / "icon-192.png")
    render(512, 0.10).save(OUT / "icon-512.png")
    render(180, 0.06).save(OUT / "apple-touch-icon.png")
    render(64, 0.02).save(OUT / "favicon.png")

    print("Wrote icon-192.png, icon-512.png, apple-touch-icon.png, favicon.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
