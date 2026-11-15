"""The tint pages of an island (``_gamedata/<island>/tintmaps/<level>/<x>x<z>.dds``) and the tint strokes laid on them.

A page is a 512 x 512 BC1 image of ``16 << level`` tiles; a texel stores a colour as YCC (``r`` Cb, ``g`` Y, ``b`` Cr)
which the terrain shader turns into ``(2 rgb) ^ 2.2``, the factor the albedo is multiplied with. ``apply`` lays the edit
file's ``tint`` strokes (``color``: the factor, ``radius``, ``strength``, ``points``) on every page they reach, at every
level (the levels are independent images); island-editor "Tint pages" has the measurements and the rules.
"""

from __future__ import annotations

import re

import numpy as np

from ..infra.i18n import tr
from ..models import bc1encode, ddsdecode
from .islandground import stroke_cover

PAGE = re.compile(r"tintmaps/(\d+)/(\d+)x(\d+)\.dds$")
NODE_TILES = 16  # the tiles a level-0 node (and its page) covers; each level doubles it
GAMMA = 2.2  # the shader's exponent: tint = (2 rgb) ^ GAMMA
MAX_FACTOR = 4.0  # (2 * 1) ^ 2.2 is 4.59: the factors a page can hold stay below it
MIN_COVER = 1e-3  # a texel a stroke covers less than this keeps its bytes


def to_rgb(stored: np.ndarray) -> np.ndarray:
    """The decoded colour (0..1, floats) of stored texels (``(h, w, 3)``, bytes r Cb, g Y, b Cr), as the shader decodes."""
    cb, y, cr = (stored[..., k].astype(np.float64) / 255 for k in range(3))
    cb, cr = ((2 * c - 1) * np.abs(2 * c - 1) * 0.5 + 0.5 for c in (cb, cr))  # the signed square
    rgb = np.stack(
        [y + 1.402 * (cr - 0.5), y - 0.344 * (cb - 0.5) - 0.714 * (cr - 0.5), y + 1.772 * (cb - 0.5)], axis=-1
    )
    return np.maximum(rgb, 0)


def to_stored(rgb: np.ndarray) -> np.ndarray:
    """``to_rgb`` backwards: the texel bytes (``(h, w, 3)`` uint8) of decoded colours."""
    r, g, b = (rgb[..., k] for k in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    chroma = [np.clip((b - y) / 1.772 + 0.5, 0, 1), np.clip((r - y) / 1.402 + 0.5, 0, 1)]
    cb, cr = (0.5 * (np.sign(v) * np.sqrt(np.abs(v)) + 1) for v in (2 * c - 1 for c in chroma))
    return np.clip(np.rint(np.stack([cb, np.clip(y, 0, 1), cr], axis=-1) * 255), 0, 255).astype(np.uint8)


def valid(stroke: dict) -> bool:
    """Whether a stroke of the edit file can be laid: three factors, a radius, points."""
    try:
        color = [float(v) for v in stroke["color"]]
        return len(color) == 3 and float(stroke.get("radius", 4)) > 0 and bool(stroke["points"])
    except (KeyError, TypeError, ValueError):
        return False


def gain(strokes: list[dict], x0: float, z0: float, span: float, side: int) -> np.ndarray | None:
    """What the strokes multiply the decoded colour of a ``side`` x ``side`` page with (``(side, side, 3)``; page corner
    tile (``x0``, ``z0``), ``span`` tiles wide), None when none of them reaches the page."""
    total = None
    for st in strokes:
        moved = {**st, "points": [[p[0] - x0, p[1] - z0] for p in st["points"]]}
        cover = stroke_cover(moved, side, side / span)[..., None]
        if cover.max() < MIN_COVER:
            continue
        factor = np.clip(np.asarray([float(v) for v in st["color"]]), 0.0, MAX_FACTOR)
        step = (1 + cover * (factor - 1)) ** (1 / GAMMA)  # the factor blended in by the cover, in the albedo's terms
        total = step if total is None else total * step
    return total


def lay_page(raw: bytes, level: int, px: int, pz: int, strokes: list[dict]) -> bytes | None:
    """One page's file with the strokes laid on it, None when no block of it changes."""
    header = 148 if raw[84:88] == b"DX10" else 128
    old = ddsdecode.read_dds(raw)[..., :3]
    side = old.shape[0]
    if old.shape[1] != side or side % 4 or len(raw) < header + side * side // 2:
        return None
    span = NODE_TILES << level
    factor = gain(strokes, px * span, pz * span, span, side)
    if factor is None:
        return None
    new = np.where((np.abs(factor - 1) > 1e-4).any(axis=-1)[..., None], to_stored(to_rgb(old) * factor), old)
    old_blocks, new_blocks = bc1encode.to_blocks(old), bc1encode.to_blocks(new)
    hit = (new_blocks != old_blocks).any(axis=(1, 2))
    if not hit.any():
        return None
    top = np.frombuffer(raw, np.uint8, side * side // 2, header).reshape(-1, 8).copy()
    top[hit] = bc1encode.encode_blocks(new_blocks[hit])
    return raw[:header] + top.tobytes() + raw[header + top.size :]


def dabs(stroke: dict) -> np.ndarray:
    """The points a stroke's brush is set down at (``(n, 2)`` tiles): its own, and the line between two of them filled in
    at a quarter radius, as ``stroke_cover`` lays it."""
    r = float(stroke.get("radius", 4))
    pts = np.asarray([[float(v) for v in p] for p in stroke["points"]]).reshape(-1, 2)
    parts = [pts[:1]]
    for a, b in zip(pts, pts[1:], strict=False):
        steps = max(1, int(np.ceil(np.hypot(*(b - a)) / (r / 4))))
        parts.append(a + (b - a) * (np.arange(1, steps + 1) / steps)[:, None])
    return np.concatenate(parts)


def touches(points: np.ndarray, radius: float, level: int, px: int, pz: int) -> bool:
    """Whether a brush of ``radius`` set down at ``points`` reaches the page ``px`` x ``pz`` of ``level``."""
    span = NODE_TILES << level
    x0, z0 = px * span, pz * span
    inside = (x0 - radius <= points[:, 0]) & (points[:, 0] <= x0 + span + radius)
    return bool((inside & (z0 - radius <= points[:, 1]) & (points[:, 1] <= z0 + span + radius)).any())


def apply(read, names: list[str], strokes: list[dict]) -> tuple[dict[str, bytes], dict]:
    """Lay ``strokes`` on the pages among ``names`` (paths inside ``_gamedata/<island>/``; ``read`` gives a file's
    current bytes). Returns the changed pages and the report: ``pages``, ``strokes`` laid and ``unmatched`` (a stroke no
    page of the island lies under, or one the format cannot hold)."""
    ok = [(k, st, dabs(st), float(st.get("radius", 4))) for k, st in enumerate(strokes) if valid(st)]
    pages = [(name, tuple(int(v) for v in found.groups())) for name in names if (found := PAGE.search(name))]
    out: dict[str, bytes] = {}
    laid: set[int] = set()
    for name, (level, px, pz) in pages:
        mine = [(k, st) for k, st, points, r in ok if touches(points, r, level, px, pz)]
        if not mine:
            continue
        laid |= {k for k, _ in mine}
        data = lay_page(read(name), level, px, pz, [st for _, st in mine])
        if data is not None:
            out[name] = data
    unmatched = [tr("tint stroke {k}", k=k) for k in range(len(strokes)) if k not in laid]
    return out, {"pages": len(out), "strokes": len(laid), "unmatched": unmatched}
