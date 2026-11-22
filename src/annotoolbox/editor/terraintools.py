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


def _hash(index_x: np.ndarray, index_z: np.ndarray, seed: int) -> np.ndarray:
    """A repeatable value in [-1, 1) for each lattice point (an integer hash, no state)."""
    hashed = (
        index_x.astype(np.int64) * 374761393 + index_z.astype(np.int64) * 668265263 + seed * 2246822519
    ) & 0xFFFFFFFF
    hashed = ((hashed ^ (hashed >> 13)) * 1274126177) & 0xFFFFFFFF
    hashed = hashed ^ (hashed >> 16)
    return (hashed & 0xFFFF) / 32768.0 - 1.0


def value_noise(tiles_x, tiles_z, scale_tiles: float = NOISE_SCALE, seed: int = SEED) -> np.ndarray:
    """Smooth noise in [-1, 1] at the points (xx, zz) in tiles: lattice values every ``scale_tiles`` tiles, blended with a
    smoothstep, two octaves. The same point always gives the same value, so a second stroke deepens the relief."""
    out = np.zeros(np.shape(tiles_x), np.float64)
    amp, total = 1.0, 0.0
    for octave in range(2):
        cell_size = scale_tiles / (2**octave)
        lattice_x, lattice_z = np.asarray(tiles_x, np.float64) / cell_size, np.asarray(tiles_z, np.float64) / cell_size
        floor_x, floor_z = np.floor(lattice_x), np.floor(lattice_z)
        fraction_x, fraction_z = lattice_x - floor_x, lattice_z - floor_z
        fraction_x, fraction_z = (
            fraction_x * fraction_x * (3 - 2 * fraction_x),
            fraction_z * fraction_z * (3 - 2 * fraction_z),
        )
        corner = [
            _hash(floor_x + offset_x, floor_z + offset_z, seed + octave) for offset_z in (0, 1) for offset_x in (0, 1)
        ]
        top = corner[0] * (1 - fraction_x) + corner[1] * fraction_x
        bottom = corner[2] * (1 - fraction_x) + corner[3] * fraction_x
        out += amp * (top * (1 - fraction_z) + bottom * fraction_z)
        total += amp
        amp *= 0.5
    return out / total


def despike(full: np.ndarray, min_z: int, max_z: int, min_x: int, max_x: int) -> np.ndarray:
    """Per corner of the window, the change that puts a spike (a corner more than ``SPIKE`` off the median of its
    eight neighbours) on that median; 0 elsewhere. ``full`` is the whole corner grid."""
    pad = np.pad(full, 1, mode="edge")
    stack = np.stack(
        [
            pad[min_z + 1 + offset_z : max_z + 2 + offset_z, min_x + 1 + offset_x : max_x + 2 + offset_x]
            for offset_z in (-1, 0, 1)
            for offset_x in (-1, 0, 1)
            if (offset_z, offset_x) != (0, 0)
        ]
    )
    median = np.median(stack, axis=0)
    current = full[min_z : max_z + 1, min_x : max_x + 1]
    return np.where(np.abs(current - median) > SPIKE, median - current, 0.0)


def delta(
    mode: str, full: np.ndarray, window: tuple[int, int, int, int], falloff: np.ndarray, strength: float
) -> np.ndarray:
    """What one stamp of ``mode`` adds to the corners of ``window`` (zs, ze, xs, xe, inclusive) of the corner
    grid ``full``; ``w`` is the brush's falloff there, ``k`` its strength (0.1 to 1)."""
    min_z, max_z, min_x, max_x = window
    current = full[min_z : max_z + 1, min_x : max_x + 1]
    if mode == "noise":
        grid_z, grid_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1]
        return NOISE_AMPLITUDE * strength * falloff * value_noise(grid_x, grid_z)
    if mode == "terrace":
        return (np.round(current / TERRACE_STEP) * TERRACE_STEP - current) * 0.5 * strength * falloff
    if mode == "despike":
        return despike(full, min_z, max_z, min_x, max_x) * min(1.0, 2 * strength) * (falloff > 0)
    raise ValueError(f"not a stamp brush: {mode!r} (one of noise, terrace, despike)")


def ramp_heights(
    full: np.ndarray, start, end, radius_tiles: float, strength: float, curve: str = brushes.DEFAULT
) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """The corner heights of a slope from ``start`` to ``end`` (each (x, z) in tiles) laid over ``full``: the ground
    along the line takes the straight line between the two ends' own heights, a band of ``radius`` either side
    blending back to the old ground by the falloff. Returns the new heights of the box that changed and the box
    (zs, ze, xs, xe, inclusive), or None when the ends are the same point."""
    (start_x, start_z), (end_x, end_z) = start, end
    length = math.hypot(end_x - start_x, end_z - start_z)
    if length < 1e-6:
        return None
    size = full.shape[0] - 1
    min_z, max_z = (
        max(0, math.floor(min(start_z, end_z) - radius_tiles)),
        min(size, math.ceil(max(start_z, end_z) + radius_tiles)),
    )
    min_x, max_x = (
        max(0, math.floor(min(start_x, end_x) - radius_tiles)),
        min(size, math.ceil(max(start_x, end_x) + radius_tiles)),
    )
    if max_z < min_z or max_x < min_x:
        return None
    grid_z, grid_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1].astype(np.float64)
    direction_x, direction_z = (end_x - start_x) / length, (end_z - start_z) / length
    along = np.clip(((grid_x - start_x) * direction_x + (grid_z - start_z) * direction_z) / length, 0.0, 1.0)
    distance = np.hypot(grid_x - (start_x + along * (end_x - start_x)), grid_z - (start_z + along * (end_z - start_z)))
    start_height, end_height = _height_at(full, start_x, start_z), _height_at(full, end_x, end_z)
    blend = np.minimum(1.0, 2 * strength * brushes.falloff(distance, radius_tiles, curve))
    current = full[min_z : max_z + 1, min_x : max_x + 1]
    return current + (start_height + (end_height - start_height) * along - current) * blend, (
        min_z,
        max_z,
        min_x,
        max_x,
    )


def _height_at(full: np.ndarray, x: float, z: float) -> float:
    """The corner grid's height at (x, z) in tiles, bilinear."""
    size = full.shape[0] - 1
    x, z = min(max(x, 0.0), size - 1e-6), min(max(z, 0.0), size - 1e-6)
    i, j = int(x), int(z)
    fraction_x, fraction_z = x - i, z - j
    return float(
        (full[j, i] * (1 - fraction_x) + full[j, i + 1] * fraction_x) * (1 - fraction_z)
        + (full[j + 1, i] * (1 - fraction_x) + full[j + 1, i + 1] * fraction_x) * fraction_z
    )


def step(session, stroke: dict, center_x: float, center_z: float) -> None:
    """One point of a stroke of a mode in ``MODES`` for ``session`` (``CEditSession``) with the stroke it began
    (``begin_height``): a stamp for noise, terrace and spike removal, the whole slope again for a ramp (from where the
    stroke began to this point, the ground the stroke began on underneath)."""
    from .editchange import SChange, union_box

    if stroke["mode"] == "ramp":
        base = session.m_base_heights[:: session.m_samples_per_tile, :: session.m_samples_per_tile] + stroke["before"]
        laid = ramp_heights(
            base, stroke["start"], (center_x, center_z), stroke["radius"], stroke["strength"], session.m_falloff
        )
        if laid is None:
            return
        heights, (min_z, max_z, min_x, max_x) = laid
        box = union_box(stroke.get("box"), (min_x, min_z, max_x, max_z))
        session.m_corner_deltas[box[1] : box[3] + 1, box[0] : box[2] + 1] = stroke["before"][
            box[1] : box[3] + 1, box[0] : box[2] + 1
        ]
        session.m_corner_deltas[min_z : max_z + 1, min_x : max_x + 1] += (
            heights - base[min_z : max_z + 1, min_x : max_x + 1]
        )
        stroke["touched"][box[1] : box[3] + 1, box[0] : box[2] + 1] = True
        stroke["box"] = box
        session._redraw(*box)
        session._notify(SChange(heights=box))
        return
    radius_tiles, size = stroke["radius"], session.m_size_tiles
    min_z, max_z = max(0, math.floor(center_z - radius_tiles)), min(size, math.ceil(center_z + radius_tiles))
    min_x, max_x = max(0, math.floor(center_x - radius_tiles)), min(size, math.ceil(center_x + radius_tiles))
    grid_z, grid_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1]
    distance = np.hypot(grid_x - center_x, grid_z - center_z)
    falloff = brushes.falloff(distance, radius_tiles, session.m_falloff)
    change = delta(
        stroke["mode"],
        session.m_base_heights[:: session.m_samples_per_tile, :: session.m_samples_per_tile] + session.m_corner_deltas,
        (min_z, max_z, min_x, max_x),
        falloff,
        stroke["strength"],
    )
    session.m_corner_deltas[min_z : max_z + 1, min_x : max_x + 1] += change.astype(np.float32)
    stroke["touched"][min_z : max_z + 1, min_x : max_x + 1] |= distance <= radius_tiles
    session._redraw(min_x, min_z, max_x, max_z)
    session._notify(SChange(heights=(min_x, min_z, max_x, max_z)))
