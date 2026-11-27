"""The Drive Sync logo: a blue folder (this PC) folding into a green cloud (Google Drive),
joined by a glowing yellow fold, with a glossy sheen and a light rim that stands out on a dark taskbar.

The shapes are those of assets/drivesync.svg, drawn here with Pillow so the app needs no SVG renderer.
In full color it is the app's icon and the tray's "up to date" icon; while syncing, a gold light runs
round its outline. Drawn in one color (with the fold left as a thin gap) it shows errors (red) and,
with a pause badge, a paused sync (grey).
`python logo.py` rewrites assets/drivesync.ico (the window and .exe icon).
"""
from __future__ import annotations

import sys
from itertools import pairwise
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
_GLINT = [("M", (180, 12)), ("C", (104, 12), (43, 64), (42, 145))]  # light on the folder's top-left corner
_FOLD = ((78, 1000), (650, 220))
_FOLD_SHADOW = ((92, 982), (650, 220))
_X0, _X1, _Y0, _Y1 = 22, 1198, -8, 1008  # the shape plus its rim

# Each palette: folder (light, dark), cloud (dark, light), fold (edge, middle), rim
PALETTES = {
    "vivid": (("#3B9BFF", "#0756D8"), ("#008B43", "#21E68B"), ("#FF9D00", "#FFF36A"), "#A9D9FF"),
    "jewel": (("#477DFF", "#172D9C"), ("#007B55", "#39F0B0"), ("#FF8A18", "#FFF18A"), "#B9D4FF"),
    "glass": (("#2EC5FF", "#0755C8"), ("#00A56B", "#5BFFC0"), ("#FFAA22", "#FFF7A0"), "#C4F2FF"),
    "aurora": (("#397BFF", "#083FAD"), ("#009C65", "#63F56D"), ("#FF7A18", "#FFF04A"), "#AEEAFF"),
}
PALETTE = "glass"


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


def _line(size: int, pts, width: float, opacity: float = 1) -> Image.Image:
    """A mask of a line through pts with round ends; its value is the opacity."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    w = max(1, round(width))
    d.line(pts, fill=255, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=255)
    return m if opacity == 1 else m.point(lambda v: round(v * opacity))


def _ramp(size: int, box, p0, p1) -> Image.Image:
    """An SVG-style linear gradient's position (0-255) at each pixel: p0 -> p1 are fractions of box."""
    bx, by, bw, bh = box[0], box[1], box[2] - box[0], box[3] - box[1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d2 = dx * dx + dy * dy
    # t = ((x-bx)/bw - p0x)*dx/d2 + ((y-by)/bh - p0y)*dy/d2, read from a ramp where u = 256 + 255*t
    a, b = 255 * dx / (d2 * bw), 255 * dy / (d2 * bh)
    c = 256 - 255 * ((bx / bw + p0[0]) * dx + (by / bh + p0[1]) * dy) / d2
    ramp = Image.new("L", (768, 1))
    ramp.putdata([0] * 256 + list(range(256)) + [255] * 256)
    return ramp.transform((size, size), Image.AFFINE, (a, b, c, 0, 0, 0.5), Image.BILINEAR)


def _lut(stops) -> list[float]:
    """256 values blended between (offset, value) stops."""
    out = []
    for i in range(256):
        x = i / 255
        (o0, v0), (o1, v1) = next(((s, e) for s, e in pairwise(stops) if x <= e[0]), stops[-2:])
        f = 0 if o1 == o0 else min(1, max(0, (x - o0) / (o1 - o0)))
        out.append(v0 + (v1 - v0) * f)
    return out


def _colorize(t: Image.Image, stops) -> Image.Image:
    rgb = [(o, tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))) for o, h in stops]
    lut = [round(v) for ch in range(3) for v in _lut([(o, c[ch]) for o, c in rgb])]
    return t.convert("RGB").point(lut)


def _paint(img: Image.Image, fill, mask: Image.Image) -> None:
    """Lay fill (a color or an RGB image) over img where mask is set; mask is the opacity."""
    layer = Image.new("RGBA", img.size, fill) if isinstance(fill, str) else fill.convert("RGBA")
    layer.putalpha(mask)
    img.alpha_composite(layer)


def draw(size: int, color: str | None = None, palette: str | None = None) -> Image.Image:
    """The logo as a size x size transparent RGBA image: in full color, or all in one color."""
    big = size * SS
    k = big / (_X1 - _X0)                       # fills the width; centred top to bottom
    oy = (big - (_Y1 - _Y0) * k) / 2 - _Y0 * k

    def xy(x, y):
        return ((x - _X0) * k, oy + y * k)

    outline = _points(_FOLDER, xy)
    folder = _mask(big, outline)
    fold = [xy(*p) for p in _FOLD]
    if color:
        img = Image.new("RGBA", (big, big), color)
        img.putalpha(ImageChops.subtract(folder, _line(big, fold, max(SS, 40 * k))))
        return img.resize((size, size), Image.LANCZOS)

    blue, green, gold, rim = PALETTES[palette or PALETTE]
    whole = (*xy(0, 0), *xy(1200, 1000))
    shine = _ramp(big, whole, (0, 0), (0.8, 1)).point([round(255 * v) for v in _lut([(0, .36), (.5, .05), (1, 0)])])
    img = _colorize(_ramp(big, whole, (0, 0), (0.9, 1)), ((0, blue[0]), (1, blue[1]))).convert("RGBA")
    _paint(img, "#FFFFFF", shine)
    cloud = ImageChops.multiply(_mask(big, _points(_CLOUD, xy)), folder)
    _paint(img, _colorize(_ramp(big, (*xy(78, 220), *xy(1190, 1000)), (0.05, 0.95), (0.95, 0.05)),
                          ((0, green[0]), (1, green[1]))), cloud)
    _paint(img, "#FFFFFF", ImageChops.multiply(cloud, shine.point(lambda v: round(v * .85))))
    _paint(img, "#003D65", _line(big, [xy(*p) for p in _FOLD_SHADOW], 76 * k, .28))
    _paint(img, _colorize(_ramp(big, (*xy(78, 220), *xy(650, 1000)), (0, 1), (1, 0)),
                          ((0, gold[0]), (.48, gold[1]), (1, gold[0]))), _line(big, fold, max(SS, 48 * k)))
    _paint(img, "#FFF9D5", _line(big, fold, 7 * k, .72))
    _paint(img, rim, _line(big, _points(_GLINT, xy), 13 * k))
    img.putalpha(folder)
    _paint(img, rim, _line(big, outline + outline[:1], 15 * k))
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


def _outline_loop(big: int, n: int) -> list[tuple[float, float]]:
    """n points spaced evenly around the logo's outline, on a big x big canvas."""
    k = big / (_X1 - _X0)
    oy = (big - (_Y1 - _Y0) * k) / 2 - _Y0 * k
    pts = _points(_FOLDER, lambda x, y: ((x - _X0) * k, oy + y * k))
    pts.append(pts[0])
    seg = [((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 for (ax, ay), (bx, by) in pairwise(pts)]
    step, out, i, into = sum(seg) / n, [], 0, 0.0
    for j in range(n):
        want = j * step
        while into + seg[i] < want:
            into += seg[i]
            i += 1
        f = (want - into) / seg[i] if seg[i] else 0
        out.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f))
    return out


def draw_syncing_frames(size: int, frames: int = 20, light: str = "#FFD84D",
                        tail: float = 0.4) -> list[Image.Image]:
    """The full-color logo with a gold light running clockwise around its outline, trailing a
    fading tail (tail = the share of the outline it covers): one image per animation frame."""
    base, big, n = draw(size), size * SS, 240
    loop = _outline_loop(big, n)[::-1]           # the outline runs anticlockwise; the light goes clockwise
    w = max(SS, size * SS * 0.14)                # about 2 px at 16 px
    out = []
    for i in range(frames):
        glow, core = Image.new("L", (big, big), 0), Image.new("L", (big, big), 0)
        dg, dc = ImageDraw.Draw(glow), ImageDraw.Draw(core)
        head, length = i * n // frames, round(n * tail)
        for j in range(length, 0, -1):           # tail end first, so the brighter head draws on top
            a, b = loop[(head - j) % n], loop[(head - j + 1) % n]
            fade = (1 - j / length) ** 1.6
            dg.line((a, b), fill=round(110 * fade), width=round(w * 2.2))
            dc.line((a, b), fill=round(255 * fade), width=round(w))
        x, y = loop[head]
        dg.ellipse((x - w * 1.5, y - w * 1.5, x + w * 1.5, y + w * 1.5), fill=110)
        dc.ellipse((x - w * 0.7, y - w * 0.7, x + w * 0.7, y + w * 0.7), fill=255)
        frame = base.copy()
        for mask in (glow, core):
            layer = Image.new("RGBA", (big, big), light)
            layer.putalpha(mask)
            frame.alpha_composite(layer.resize((size, size), Image.LANCZOS))
        out.append(frame)
    return out


def write_ico(path: Path) -> None:
    imgs = [draw(n) for n in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=imgs[:-1])


if __name__ == "__main__":
    out = ASSETS / "drivesync.ico"
    write_ico(out)
    print(f"Wrote {out}")
