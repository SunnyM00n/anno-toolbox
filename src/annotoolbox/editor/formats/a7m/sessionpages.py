"""The normal-map pages of an Anno 2205 session after a height edit: the ground's shading follows the new slope.

A page (``_a6mdata/<name>/normalmaps/<level>/<x>_<z>.dds`` at level 0, ``<x>x<z>`` above; BC1, a tangent normal in
RGB, green up) covers ``16 << level`` tiles. ``rewrite`` takes the slope the page stores, adds the slope of the
edit's height delta (smoothed over a tile, as the hill is) and encodes the result again; only the 4 x 4 blocks the
edit changes are re-encoded, so the rest of the page keeps its bytes (docs/formats/anno2205-map-format.md, "Writing
a map"). Which texels a page's colours map to was read by fitting a page to the heights (corr. 0.92 on level 0).
"""

from __future__ import annotations

import re

import numpy as np

from ....islands.islandgrid import ground_at
from ....models import bc1encode, ddsdecode
from .session import Session2205

HEADER = 128  # the DDS header; the top mip follows it
PAGE = re.compile(r"normalmaps/(\d+)/(\d+)[_x](\d+)\.dds$")
MIN_SLOPE = 1e-3  # a slope the edit adds below this (world units per tile) is not drawn


def _listing(files, prefix: str) -> list[str]:
    """The paths below ``prefix`` of any of the file sources the editor and the build hand over."""
    for name in ("list", "under", "iter_paths"):
        method = getattr(files, name, None)
        if method is not None:
            return list(method(prefix))
    raise TypeError(f"{type(files).__name__} cannot list its files")


def page_files(isl: Session2205) -> list[tuple[str, int, int, int]]:
    """The session's normal-map pages as (path, level, page x, page z)."""
    out = []
    for path in _listing(isl.files, isl.gamedata_dir):
        m = PAGE.search(path)
        if m:
            out.append((path, int(m[1]), int(m[2]), int(m[3])))
    return sorted(out)


def _box_blur(a: np.ndarray, width: int, axis: int) -> np.ndarray:
    """A moving average of ``width`` samples along ``axis`` (the edges repeat)."""
    if width <= 1:
        return a
    pad = [(0, 0), (0, 0)]
    pad[axis] = (width // 2, width - 1 - width // 2)
    total = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    zero = np.zeros_like(np.take(total, [0], axis=axis))
    total = np.concatenate([zero, total], axis=axis)
    head = np.take(total, range(width, total.shape[axis]), axis=axis)
    tail = np.take(total, range(0, total.shape[axis] - width), axis=axis)
    return (head - tail) / width


def delta_slopes(deltas: np.ndarray, x0: float, z0: float, span: float, side: int) -> tuple[np.ndarray, np.ndarray]:
    """The slope (world units per tile) of the height delta on a ``side`` x ``side`` texel page whose corner is tile
    (``x0``, ``z0``) and which is ``span`` tiles wide: the bilinear delta blurred over one tile, then differentiated."""
    per_tile = side / span
    margin = int(np.ceil(per_tile)) + 1
    coords = (np.arange(-margin, side + margin) + 0.5) / per_tile
    xs, zs = np.meshgrid(x0 + coords, z0 + coords)
    field = ground_at(deltas, xs, zs)
    width = max(1, int(round(per_tile)))
    for axis in (0, 1):
        field = _box_blur(_box_blur(field, width, axis), width, axis)
    gz, gx = np.gradient(field, 1 / per_tile)
    return gx[margin:-margin, margin:-margin], gz[margin:-margin, margin:-margin]


def lit(old_rgb: np.ndarray, gx: np.ndarray, gz: np.ndarray) -> np.ndarray:
    """A page's texels (``(h, w, 3)`` uint8) with the slope ``(gx, gz)`` added to the slope they store."""
    n = old_rgb.astype(np.float64) / 255 * 2 - 1
    up = np.maximum(n[..., 1], 0.2)
    sx, sz = -n[..., 0] / up + gx, -n[..., 2] / up + gz
    new = np.stack([-sx, np.ones_like(sx), -sz], axis=-1)
    new /= np.linalg.norm(new, axis=-1, keepdims=True)
    return np.clip(np.rint((new * 0.5 + 0.5) * 255), 0, 255).astype(np.uint8)


def rewrite_page(raw: bytes, level: int, px: int, pz: int, deltas: np.ndarray) -> bytes | None:
    """One page's file with the edit's slope laid on it, or None when no block of it changes."""
    old = ddsdecode.read_dds(raw)[..., :3]
    side = old.shape[0]
    if old.shape[1] != side or side % 4 or len(raw) < HEADER + side * side // 2:
        return None
    span = 16 << level
    gx, gz = delta_slopes(deltas, px * span, pz * span, span, side)
    touched = np.hypot(gx, gz) > MIN_SLOPE  # where the edit slopes the ground: the rest keeps its texels exactly
    new = np.where(touched[..., None], lit(old, gx, gz), old)
    old_blocks, new_blocks = bc1encode.to_blocks(old), bc1encode.to_blocks(new)
    hit = (new_blocks != old_blocks).any(axis=(1, 2))
    if not hit.any():
        return None
    data = bytearray(raw)
    top = np.frombuffer(raw, np.uint8, side * side // 2, HEADER).reshape(-1, 8).copy()
    top[hit] = bc1encode.encode_blocks(new_blocks[hit])
    data[HEADER : HEADER + top.size] = top.tobytes()
    return bytes(data)


def rewrite(isl: Session2205, deltas: np.ndarray) -> dict[str, bytes]:
    """The pages the corner deltas (tiles x tiles, world units) reach, rewritten: path -> file."""
    nz, nx = np.nonzero(deltas)
    box = (nx.min() - 2, nz.min() - 2, nx.max() + 2, nz.max() + 2)  # the blur reaches a tile beyond the corners
    out = {}
    for path, level, px, pz in page_files(isl):
        span = 16 << level
        if px * span > box[2] or (px + 1) * span < box[0] or pz * span > box[3] or (pz + 1) * span < box[1]:
            continue
        data = rewrite_page(isl.files.read(path), level, px, pz, deltas)
        if data is not None:
            out[path] = data
    return out
