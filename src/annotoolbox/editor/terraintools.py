"""Further height brushes: noise, terrace, ramp and spike removal, on top of the four of ``editsession``.

The brushes work on the island's tile corners (rows z). ``delta`` gives the change a stamp makes for noise, terrace
and spike removal; ``ramp_heights`` lays a slope between two points; ``step`` is what the edit session calls for
each point of a stroke. Every one changes the corner differences only, so a stroke is an ordinary height edit in
the edit file (island-editor "Full spec").
"""

from __future__ import annotations

import math

import numpy as np

from ..infra.i18n import N_
from . import brushes

MODES = ("noise", "terrace", "ramp", "despike")
LABELS = {
    "noise": N_("islands.noise"),
    "terrace": N_("islands.terrace"),
    "ramp": N_("islands.ramp"),
    "despike": N_("islands.spike_removal_label"),
}
NOISE_SCALE = 3.0  # tiles between the lattice points of the noise
NOISE_AMPLITUDE = 0.35  # world units one stamp at full strength moves a corner by
TERRACE_STEP = 1.0  # world units between the terraces
SPIKE = 0.4  # world units a corner must stand off its neighbours' median to count as a spike
SEED = 7


def _hash(ix: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    """A repeatable value in [-1, 1) for each lattice point (an integer hash, no state)."""
    h = (ix.astype(np.int64) * 374761393 + iz.astype(np.int64) * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return (h & 0xFFFF) / 32768.0 - 1.0


def value_noise(xx, zz, scale: float = NOISE_SCALE, seed: int = SEED) -> np.ndarray:
    """Smooth noise in [-1, 1] at the points (xx, zz) in tiles: lattice values every ``scale`` tiles, blended with a
    smoothstep, two octaves. The same point always gives the same value, so a second stroke deepens the relief."""
    out = np.zeros(np.shape(xx), np.float64)
    amp, total = 1.0, 0.0
    for octave in range(2):
        s = scale / (2**octave)
        gx, gz = np.asarray(xx, np.float64) / s, np.asarray(zz, np.float64) / s
        x0, z0 = np.floor(gx), np.floor(gz)
        fx, fz = gx - x0, gz - z0
        fx, fz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)
        corner = [_hash(x0 + dx, z0 + dz, seed + octave) for dz in (0, 1) for dx in (0, 1)]
        top = corner[0] * (1 - fx) + corner[1] * fx
        bottom = corner[2] * (1 - fx) + corner[3] * fx
        out += amp * (top * (1 - fz) + bottom * fz)
        total += amp
        amp *= 0.5
    return out / total


def despike(full: np.ndarray, zs: int, ze: int, xs: int, xe: int) -> np.ndarray:
    """Per corner of the window, the change that puts a spike (a corner more than ``SPIKE`` off the median of its
    eight neighbours) on that median; 0 elsewhere. ``full`` is the whole corner grid."""
    pad = np.pad(full, 1, mode="edge")
    stack = np.stack(
        [
            pad[zs + 1 + dz : ze + 2 + dz, xs + 1 + dx : xe + 2 + dx]
            for dz in (-1, 0, 1)
            for dx in (-1, 0, 1)
            if (dz, dx) != (0, 0)
        ]
    )
    median = np.median(stack, axis=0)
    cur = full[zs : ze + 1, xs : xe + 1]
    return np.where(np.abs(cur - median) > SPIKE, median - cur, 0.0)


def delta(mode: str, full: np.ndarray, window: tuple[int, int, int, int], w: np.ndarray, k: float) -> np.ndarray:
    """What one stamp of ``mode`` adds to the corners of ``window`` (zs, ze, xs, xe, inclusive) of the corner
    grid ``full``; ``w`` is the brush's falloff there, ``k`` its strength (0.1 to 1)."""
    zs, ze, xs, xe = window
    cur = full[zs : ze + 1, xs : xe + 1]
    if mode == "noise":
        zz, xx = np.mgrid[zs : ze + 1, xs : xe + 1]
        return NOISE_AMPLITUDE * k * w * value_noise(xx, zz)
    if mode == "terrace":
        return (np.round(cur / TERRACE_STEP) * TERRACE_STEP - cur) * 0.5 * k * w
    if mode == "despike":
        return despike(full, zs, ze, xs, xe) * min(1.0, 2 * k) * (w > 0)
    raise ValueError(f"not a stamp brush: {mode!r} (one of noise, terrace, despike)")


def ramp_heights(
    full: np.ndarray, start, end, radius: float, k: float, curve: str = brushes.DEFAULT
) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """The corner heights of a slope from ``start`` to ``end`` (each (x, z) in tiles) laid over ``full``: the ground
    along the line takes the straight line between the two ends' own heights, a band of ``radius`` either side
    blending back to the old ground by the falloff. Returns the new heights of the box that changed and the box
    (zs, ze, xs, xe, inclusive), or None when the ends are the same point."""
    (ax, az), (bx, bz) = start, end
    length = math.hypot(bx - ax, bz - az)
    if length < 1e-6:
        return None
    size = full.shape[0] - 1
    zs, ze = max(0, math.floor(min(az, bz) - radius)), min(size, math.ceil(max(az, bz) + radius))
    xs, xe = max(0, math.floor(min(ax, bx) - radius)), min(size, math.ceil(max(ax, bx) + radius))
    if ze < zs or xe < xs:
        return None
    zz, xx = np.mgrid[zs : ze + 1, xs : xe + 1].astype(np.float64)
    dx, dz = (bx - ax) / length, (bz - az) / length
    t = np.clip(((xx - ax) * dx + (zz - az) * dz) / length, 0.0, 1.0)
    d = np.hypot(xx - (ax + t * (bx - ax)), zz - (az + t * (bz - az)))
    ha, hb = _height_at(full, ax, az), _height_at(full, bx, bz)
    blend = np.minimum(1.0, 2 * k * brushes.falloff(d, radius, curve))
    cur = full[zs : ze + 1, xs : xe + 1]
    return cur + (ha + (hb - ha) * t - cur) * blend, (zs, ze, xs, xe)


def _height_at(full: np.ndarray, x: float, z: float) -> float:
    """The corner grid's height at (x, z) in tiles, bilinear."""
    size = full.shape[0] - 1
    x, z = min(max(x, 0.0), size - 1e-6), min(max(z, 0.0), size - 1e-6)
    i, j = int(x), int(z)
    fx, fz = x - i, z - j
    return float(
        (full[j, i] * (1 - fx) + full[j, i + 1] * fx) * (1 - fz)
        + (full[j + 1, i] * (1 - fx) + full[j + 1, i + 1] * fx) * fz
    )


def step(session, b: dict, cx: float, cz: float) -> None:
    """One point of a stroke of a mode in ``MODES`` for ``session`` (``EditSession``) with the stroke ``b`` it began
    (``begin_height``): a stamp for noise, terrace and spike removal, the whole slope again for a ramp (from where the
    stroke began to this point, the ground the stroke began on underneath)."""
    from .editchange import Change, union_box

    s = session
    if b["mode"] == "ramp":
        base = s.base_heights[:: s.spt, :: s.spt] + b["before"]
        laid = ramp_heights(base, b["start"], (cx, cz), b["radius"], b["strength"], s.falloff)
        if laid is None:
            return
        heights, (zs, ze, xs, xe) = laid
        box = union_box(b.get("box"), (xs, zs, xe, ze))
        s.D[box[1] : box[3] + 1, box[0] : box[2] + 1] = b["before"][box[1] : box[3] + 1, box[0] : box[2] + 1]
        s.D[zs : ze + 1, xs : xe + 1] += heights - base[zs : ze + 1, xs : xe + 1]
        b["touched"][box[1] : box[3] + 1, box[0] : box[2] + 1] = True
        b["box"] = box
        s._redraw(*box)
        s._notify(Change(heights=box))
        return
    r, size = b["radius"], s.size
    zs, ze = max(0, math.floor(cz - r)), min(size, math.ceil(cz + r))
    xs, xe = max(0, math.floor(cx - r)), min(size, math.ceil(cx + r))
    zz, xx = np.mgrid[zs : ze + 1, xs : xe + 1]
    d = np.hypot(xx - cx, zz - cz)
    w = brushes.falloff(d, r, s.falloff)
    change = delta(b["mode"], s.base_heights[:: s.spt, :: s.spt] + s.D, (zs, ze, xs, xe), w, b["strength"])
    s.D[zs : ze + 1, xs : xe + 1] += change.astype(np.float32)
    b["touched"][zs : ze + 1, xs : xe + 1] |= d <= r
    s._redraw(xs, zs, xe, ze)
    s._notify(Change(heights=(xs, zs, xe, ze)))
