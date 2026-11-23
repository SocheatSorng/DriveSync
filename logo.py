"""The Drive Sync logo: a navy folder (this PC) folding into a coral cloud (Google Drive).

The shapes are those of assets/drivesync.svg, drawn here with Pillow so the app needs no SVG renderer.
In full color it is the app's icon and the tray's "up to date" icon; drawn in one color (with the
fold left as a thin gap) the tray uses it to show its other states (amber, red, grey).
`python logo.py` rewrites assets/drivesync.ico (the window and .exe icon).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 8  # supersampling factor
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"

# Outlines in the SVG's coordinates (viewBox 0 0 1200 1000): "M"/"L" take one point, "C" three.
_FOLDER = [("M", (180, 0)), ("C", (92, 0), (30, 56), (30, 145)), ("L", (30, 850)),
           ("C", (30, 940), (82, 1000), (180, 1000)), ("L", (820, 1000)),
           ("C", (1040, 1000), (1190, 850), (1190, 650)), ("C", (1190, 552), (1147, 472), (1100, 420)),
           ("C", (1085, 286), (1000, 220), (875, 220)), ("L", (650, 220)),
           ("C", (580, 220), (550, 0), (430, 0))]
_CLOUD = [("M", (78, 1000)), ("L", (650, 220)), ("L", (875, 220)),
          ("C", (1000, 220), (1085, 286), (1100, 420)), ("C", (1147, 472), (1190, 552), (1190, 650)),
          ("C", (1190, 850), (1040, 1000), (820, 1000))]
_RIM = [("M", (30, 145)), ("C", (30, 56), (92, 0), (180, 0))]
_FOLD = ((78, 1000), (650, 220))
_X0, _X1, _H = 30, 1190, 1000  # the folder's left / right edge and height

NAVY = ((0, "#2B3FA0"), (1, "#1E2A78"))
CORAL = ((0, "#FF493F"), (0.55, "#FF6657"), (1, "#FFB15F"))


def _points(path, map_xy) -> list[tuple[float, float]]:
    """The path as a polygon (each curve cut into straight pieces), mapped to pixels."""
    pts = []
    for op, *args in path:
        if op == "C":
            (x0, y0), ((x1, y1), (x2, y2), (x3, y3)) = pts[-1], [map_xy(*a) for a in args]
            for i in range(1, 25):
                t = i / 24
                u = 1 - t
                pts.append((u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                            u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
        else:
            pts.append(map_xy(*args[0]))
    return pts


def _mask(size: int, pts) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def _gradient(size: int, box, p0, p1, stops) -> Image.Image:
    """An SVG-style linear gradient: p0 -> p1 are fractions of box (x0, y0, x1, y1), in pixels."""
    bx, by, bw, bh = box[0], box[1], box[2] - box[0], box[3] - box[1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d2 = dx * dx + dy * dy
    # t = ((x-bx)/bw - p0x)*dx/d2 + ((y-by)/bh - p0y)*dy/d2, read from a ramp where u = 256 + 255*t
    a, b = 255 * dx / (d2 * bw), 255 * dy / (d2 * bh)
    c = 256 - 255 * ((bx / bw + p0[0]) * dx + (by / bh + p0[1]) * dy) / d2
    ramp = Image.new("L", (768, 1))
    ramp.putdata([0] * 256 + list(range(256)) + [255] * 256)
    t = ramp.transform((size, size), Image.AFFINE, (a, b, c, 0, 0, 0.5), Image.BILINEAR)
    lut = []
    rgb = [(o, tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))) for o, h in stops]
    for ch in range(3):
        for i in range(256):
            x = i / 255
            (o0, c0), (o1, c1) = next(((s, e) for s, e in zip(rgb, rgb[1:]) if x <= e[0]), (rgb[-2], rgb[-1]))
            f = 0 if o1 == o0 else min(1, max(0, (x - o0) / (o1 - o0)))
            lut.append(round(c0[ch] + (c1[ch] - c0[ch]) * f))
    return t.convert("RGB").point(lut)


def draw(size: int, color: str | None = None) -> Image.Image:
    """The logo as a size x size transparent RGBA image: in full color, or all in one color."""
    big = size * SS
    k = big / (_X1 - _X0)                       # fills the width; centred top to bottom
    oy = (big - _H * k) / 2

    def xy(x, y):
        return ((x - _X0) * k, oy + y * k)

    folder = _mask(big, _points(_FOLDER, xy))
    fold = Image.new("L", (big, big), 0)
    if color:
        img = Image.new("RGBA", (big, big), color)
        ImageDraw.Draw(fold).line([xy(*p) for p in _FOLD], fill=255, width=max(SS, round(40 * k)))
        img.putalpha(ImageChops.subtract(folder, fold))
    else:
        img = _gradient(big, (*xy(0, 0), *xy(1200, 1000)), (0, 0), (0.9, 1), NAVY).convert("RGBA")
        cloud = ImageChops.multiply(_mask(big, _points(_CLOUD, xy)), folder)
        img.paste(_gradient(big, (*xy(78, 220), *xy(1190, 1000)), (0.05, 0.95), (0.95, 0.05), CORAL), mask=cloud)
        d = ImageDraw.Draw(img)
        line = [xy(*p) for p in _FOLD]
        d.line(line, fill="#FFD16D", width=max(SS // 2, round(17 * k)))
        d.line(line, fill="#FFF0B0", width=max(1, round(4 * k)))
        rim = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(rim).line(_points(_RIM, xy), fill=(138, 155, 255, 122), width=max(1, round(8 * k)))
        img.alpha_composite(rim)
        img.putalpha(folder)
    return img.resize((size, size), Image.LANCZOS)


def _badged(size: int, color: str, badge: str, symbol) -> Image.Image:
    """The logo with a round badge in its bottom-right corner; symbol(draw, cx, cy, d) draws on it.
    A transparent ring is cut around the badge so it stays readable at tray size (16 px)."""
    big = size * SS
    img = draw(big, color)
    d = big * 0.58                       # badge diameter
    x0, y0 = big - d, big - d
    gap = big * 0.07
    hole = Image.new("L", (big, big), 0)
    ImageDraw.Draw(hole).ellipse((x0 - gap, y0 - gap, big + gap, big + gap), fill=255)
    a = img.getchannel("A")
    a.paste(0, mask=hole)
    img.putalpha(a)
    dr = ImageDraw.Draw(img)
    dr.ellipse((x0, y0, big - 1, big - 1), fill=badge)
    symbol(dr, x0 + d / 2, y0 + d / 2, d)
    return img.resize((size, size), Image.LANCZOS)


def draw_paused(size: int, color: str = "#8b949e", badge: str = "#5f6368") -> Image.Image:
    """The logo in grey with a pause badge (two white bars)."""
    def bars(dr, cx, cy, d):
        bw, bh = d * 0.16, d * 0.46      # bar width / height
        for bx in (cx - bw * 1.25, cx + bw * 0.25):
            dr.rectangle((bx, cy - bh / 2, bx + bw, cy + bh / 2), fill="white")
    return _badged(size, color, badge, bars)


def draw_syncing_frames(size: int, color: str = "#ffb020", badge: str = "#5f6368",
                        frames: int = 8) -> list[Image.Image]:
    """The logo in amber with a spinning arc on its badge: one image per animation frame."""
    out = []
    for i in range(frames):
        def arc(dr, cx, cy, d, start=i * 360 / frames):
            r = d * 0.30
            dr.arc((cx - r, cy - r, cx + r, cy + r), start, start + 270, fill="white", width=max(1, round(d * 0.15)))
        out.append(_badged(size, color, badge, arc))
    return out


def write_ico(path: Path) -> None:
    imgs = [draw(n) for n in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=imgs[:-1])


if __name__ == "__main__":
    out = ASSETS / "drivesync.ico"
    write_ico(out)
    print(f"Wrote {out}")
