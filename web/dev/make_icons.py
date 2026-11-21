"""Generates web/icons/*.png with the stdlib only (zlib PNG writer, 4x supersampling).

Usage: python web/dev/make_icons.py
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "icons"
C0 = (0xFF, 0x6A, 0x3D)   # accent
C1 = (0xFF, 0x3D, 0x7F)
BG = (0x0C, 0x0C, 0x10)


def png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def inside_rrect(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def inside_tri(x, y, a, b, c):
    def s(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    d1, d2, d3 = s((x, y), a, b), s((x, y), b, c), s((x, y), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def render(size, safe, full_bleed):
    """safe: fraction of the canvas the tile occupies (maskable needs ~0.8 safe zone)."""
    ss = 4
    rows = []
    m = (1 - safe) / 2
    tile = (m, m, 1 - m, 1 - m)
    rad = 0.0 if full_bleed else 0.22 * safe
    tw = safe
    # play triangle, slightly right of centre
    tri = ((0.40, 0.31), (0.40, 0.69), (0.71, 0.50))
    tri = tuple((0.5 + (px - 0.5) * tw, 0.5 + (py - 0.5) * tw) for px, py in tri)
    # small "vault" ring at the bottom-left of the triangle
    for j in range(size):
        row = []
        for i in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (i + (sx + 0.5) / ss) / size
                    y = (j + (sy + 0.5) / ss) / size
                    if full_bleed or inside_rrect(x, y, *tile, rad):
                        t = min(1, max(0, (x + y) / 2))
                        col = [C0[k] + (C1[k] - C0[k]) * t for k in range(3)]
                        if inside_tri(x, y, *tri):
                            col = [255, 255, 255]
                        acc[0] += col[0]; acc[1] += col[1]; acc[2] += col[2]; acc[3] += 255
                    elif full_bleed is None:
                        acc[0] += BG[0]; acc[1] += BG[1]; acc[2] += BG[2]; acc[3] += 255
            n = ss * ss
            a = acc[3] / n
            if a:
                row += [round(acc[0] / (acc[3] / 255)), round(acc[1] / (acc[3] / 255)), round(acc[2] / (acc[3] / 255)), round(a)]
            else:
                row += [0, 0, 0, 0]
        rows.append(row)
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    jobs = [
        ("icon-192.png", 192, 0.92, False),
        ("icon-512.png", 512, 0.92, False),
        ("maskable-512.png", 512, 1.0, True),     # full-bleed gradient; triangle within the 80% safe zone
        ("apple-touch-icon.png", 180, 1.0, True),  # iOS rounds corners itself and dislikes transparency
    ]
    for name, size, safe, bleed in jobs:
        png(OUT / name, size, size, render(size, safe if not bleed else 0.8, bleed))
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
