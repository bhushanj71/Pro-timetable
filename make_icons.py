"""
Draw the ProfSchedule AI mark and write every raster size the app needs.

    python make_icons.py

The mark is defined here as geometry rather than being resampled from a big
PNG, so each size is drawn at its own resolution instead of being blurred down
from one. app/static/logo.svg is the same artwork by hand and stays the source
of truth for anything on screen; these files exist for the places that cannot
take an SVG -- the PWA manifest, the iOS home-screen icon, and the favicon.

Everything is drawn at 4x and reduced with LANCZOS: a 32px favicon drawn
directly has stair-stepped ring circles, and supersampling is what keeps the
curves smooth at the size where it matters most.
"""
import pathlib
import sys

OUT = pathlib.Path("app/static")
SS = 4                      # supersampling factor

# The palette, matching logo.svg. Cyan at the top-left through blue to violet
# at the bottom-right, so the mark reads as one light source.
BODY_STOPS = [(0.00, (0x1F, 0xD8, 0xF0)), (0.30, (0x35, 0xB6, 0xF5)),
              (0.58, (0x5A, 0x7C, 0xF6)), (1.00, (0xB8, 0x45, 0xEE))]
BAR_STOPS  = [(0.00, (0x38, 0xBD, 0xF8)), (1.00, (0x63, 0x66, 0xF1))]
SPARK_STOPS = [(0.00, (0xA8, 0x55, 0xF7)), (0.55, (0xC5, 0x6B, 0xF5)), (1.00, (0xF4, 0x72, 0xD0))]
RING_STOPS = [(0.00, (0x22, 0xD3, 0xEE)), (1.00, (0x38, 0xBD, 0xF8))]
NAVY = (0x0B, 0x1B, 0x3F, 255)

# Geometry in a 512 box, matching logo.svg exactly.
BODY = (72, 96, 408, 412); BODY_R = 72
PAGE = (118, 152, 362, 364); PAGE_R = 40
BAR1 = (152, 196, 328, 230); BAR2 = (152, 256, 280, 290); BAR_R = 17
RING_C = [(168, 104), (300, 104)]; RING_R = 30; RING_W = 26
POST = [(152, 40, 184, 116), (284, 40, 316, 116)]; POST_R = 16
NOTCH = (366, 382, 96)          # cx, cy, r -- carved out of body, page and bars
SPARK = (366, 372, 86, 14)      # cx, cy, reach, waist


def main() -> int:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow is required:  pip install pillow")
        return 1

    def gradient(size, stops, horizontal=False, diagonal=True):
        """A linear ramp through the stops, as an RGB image."""
        img = Image.new("RGB", (size, size))
        px = img.load()
        for y in range(size):
            for x in range(size):
                t = ((x / size) * 0.55 + (y / size) * 0.45) if diagonal else (
                    x / size if horizontal else y / size)
                t = min(1.0, max(0.0, t))
                for i in range(len(stops) - 1):
                    a, ca = stops[i]
                    b, cb = stops[i + 1]
                    if a <= t <= b:
                        k = 0 if b == a else (t - a) / (b - a)
                        px[x, y] = tuple(round(ca[j] + (cb[j] - ca[j]) * k) for j in range(3))
                        break
                else:
                    px[x, y] = stops[-1][1]
        return img

    def scaled(box):
        return tuple(v * SS for v in box)

    def sparkle_points(cx, cy, reach, waist, steps=24):
        """A four-point star with concave sides -- it should glint, not point.

        Each arm is a quadratic curve pulled towards the centre, which is what
        gives the pinched waist a straight-sided diamond has not got.
        """
        tips = [(cx, cy - reach), (cx + reach, cy), (cx, cy + reach), (cx - reach, cy)]
        ctrl = [(cx + waist, cy - waist), (cx + waist, cy + waist),
                (cx - waist, cy + waist), (cx - waist, cy - waist)]
        pts = []
        for i in range(4):
            p0, p1, p2 = tips[i], ctrl[i], tips[(i + 1) % 4]
            for s in range(steps):
                t = s / steps
                m = 1 - t
                pts.append((
                    m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0],
                    m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1],
                ))
        return pts

    def draw_mark(size):
        S = size * SS
        k = S / 512

        def R(box):
            return [v * k for v in box]

        def C(pt):
            return (pt[0] * k, pt[1] * k)

        # --- Body, page and bars, then the notch taken out of all three ---
        shape = Image.new("L", (S, S), 0)
        d = ImageDraw.Draw(shape)
        d.rounded_rectangle(R(BODY), radius=BODY_R * k, fill=255)

        page = Image.new("L", (S, S), 0)
        ImageDraw.Draw(page).rounded_rectangle(R(PAGE), radius=PAGE_R * k, fill=255)

        bars = Image.new("L", (S, S), 0)
        db = ImageDraw.Draw(bars)
        db.rounded_rectangle(R(BAR1), radius=BAR_R * k, fill=255)
        db.rounded_rectangle(R(BAR2), radius=BAR_R * k, fill=255)

        # The sparkle overlaps the corner, and the body is cut away around it
        # rather than the star sitting on top -- that gap is what stops the two
        # shapes reading as one muddle at 32px.
        notch = Image.new("L", (S, S), 0)
        cx, cy, r = NOTCH
        ImageDraw.Draw(notch).ellipse(
            [(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=255)

        from PIL import ImageChops
        shape = ImageChops.subtract(shape, notch)
        page = ImageChops.subtract(page, notch)
        bars = ImageChops.subtract(bars, notch)
        body_only = ImageChops.subtract(shape, page)

        canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        canvas.paste(gradient(S, BODY_STOPS).convert("RGBA"), (0, 0), body_only)
        # Opaque white, matching logo.svg. Punched out instead, the panel goes
        # dark on a dark background and the two renderings of one mark stop
        # agreeing.
        canvas.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), page)
        canvas.paste(gradient(S, BAR_STOPS).convert("RGBA"), (0, 0), bars)

        # --- Rings: the dark loop behind each post makes them read as threaded
        #     through the cover rather than as two stubs on top. ---
        rings = Image.new("L", (S, S), 0)
        dr = ImageDraw.Draw(rings)
        for pt in RING_C:
            x, y = C(pt)
            rr = RING_R * k
            dr.ellipse([x - rr, y - rr, x + rr, y + rr], outline=255, width=int(RING_W * k))
        canvas.paste(Image.new("RGBA", (S, S), NAVY), (0, 0), rings)

        posts = Image.new("L", (S, S), 0)
        dp = ImageDraw.Draw(posts)
        for box in POST:
            dp.rounded_rectangle(R(box), radius=POST_R * k, fill=255)
        canvas.paste(gradient(S, RING_STOPS, diagonal=False).convert("RGBA"), (0, 0), posts)

        # --- The sparkle: the "AI" of the name, said without a word. ---
        star = Image.new("L", (S, S), 0)
        scx, scy, reach, waist = SPARK
        ImageDraw.Draw(star).polygon(
            [(x * k, y * k) for x, y in sparkle_points(scx, scy, reach, waist)], fill=255)
        canvas.paste(gradient(S, SPARK_STOPS).convert("RGBA"), (0, 0), star)

        return canvas.resize((size, size), Image.LANCZOS)

    def write(name, size, inset):
        """inset pulls the artwork in for maskable icons, which get clipped to
        a circle and would otherwise lose their corners."""
        pad = round(size * inset)
        art = draw_mark(size - pad * 2)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(art, (pad, pad), art)
        path = OUT / name
        out.save(path)
        print(f"  {name:24} {size}x{size}  {path.stat().st_size / 1024:.1f} KB")

    OUT.mkdir(parents=True, exist_ok=True)
    print("Drawing the mark:")
    write("logo.png", 512, 0.02)
    write("logo-160.png", 160, 0.02)
    write("icon-512.png", 512, 0.10)     # maskable: inset for the circle crop
    write("icon-192.png", 192, 0.10)
    write("apple-touch-icon.png", 180, 0.08)
    write("favicon.png", 64, 0.02)
    print("Done. app/static/logo.svg is the source of truth for anything on screen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
