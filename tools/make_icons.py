"""Génère les icônes PNG de la PWA (même dessin que app/static/icon.svg), sans dépendance externe.

Usage : python tools/make_icons.py
"""
import math
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
START, END = (0x63, 0x66, 0xF1), (0xEC, 0x48, 0x99)
STAR_FILL, STAR_EDGE = (0xFD, 0xE6, 0x8A), (0xFF, 0xFF, 0xFF)
STAR = [(256, 92), (302, 198), (416, 206), (329, 280), (356, 392),
        (256, 332), (156, 392), (183, 280), (96, 206), (210, 198)]


def inside(x: float, y: float, poly) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def edge_distance(x: float, y: float, poly) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def pixel(x: float, y: float, maskable: bool):
    """Couleur au point (x, y) dans le repère 512x512, ou None si transparent."""
    if not maskable:  # coins arrondis (rayon 112)
        cx, cy = min(max(x, 112), 400), min(max(y, 112), 400)
        if math.hypot(x - cx, y - cy) > 112:
            return None
    if maskable:  # étoile réduite pour rester dans la zone de sécurité des icônes « maskable »
        sx, sy = 256 + (x - 256) / 0.8, 256 + (y - 256) / 0.8
    else:
        sx, sy = x, y
    if edge_distance(sx, sy, STAR) <= 5:
        return STAR_EDGE
    if inside(sx, sy, STAR):
        return STAR_FILL
    t = (x + y) / 1024
    return tuple(round(a + (b - a) * t) for a, b in zip(START, END))


def render(size: int, maskable: bool) -> bytes:
    ss = 3  # sur-échantillonnage pour l'anti-crénelage
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + .5) / ss) * 512 / size
                    y = (py + (sy + .5) / ss) * 512 / size
                    c = pixel(x, y, maskable)
                    if c:
                        acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 1
            n = acc[3]
            row += bytes([acc[0] // n, acc[1] // n, acc[2] // n, 255 * n // (ss * ss)] if n else [0, 0, 0, 0])
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    (STATIC / "icon-192.png").write_bytes(render(192, maskable=False))
    (STATIC / "icon-512.png").write_bytes(render(512, maskable=True))
    print("Icônes générées dans", STATIC)
