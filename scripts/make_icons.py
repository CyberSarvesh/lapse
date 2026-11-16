"""Draw the Lapse app icons (teal tile, white clock) into web/. Run once; the PNGs are committed."""

from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parents[1] / "web"
TEAL = (15, 118, 110, 255)


def icon(size: int, rounded: bool) -> Image.Image:
    scale = 4  # draw big, then shrink for smooth edges
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0) if rounded else TEAL)
    d = ImageDraw.Draw(im)
    if rounded:
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=TEAL)
    # Clock sits inside the maskable safe zone (centre 80%).
    c, r, w = s / 2, s * 0.25, max(2, int(s * 0.055))
    d.ellipse((c - r, c - r, c + r, c + r), outline="white", width=w)
    d.line((c, c - r * 0.58, c, c), fill="white", width=w)
    d.line((c, c, c + r * 0.42, c + r * 0.28), fill="white", width=w)
    for x, y in ((c, c - r * 0.58), (c, c), (c + r * 0.42, c + r * 0.28)):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="white")
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    icon(192, rounded=False).save(WEB / "icon-192.png")
    icon(512, rounded=False).save(WEB / "icon-512.png")
    icon(180, rounded=False).convert("RGB").save(WEB / "apple-touch-icon.png")
    print("Wrote icons to", WEB)
