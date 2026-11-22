"""How strongly a brush acts at a distance from its centre: the falloff curves of the height, paint, forest and path
blocker brushes.

``falloff(distance_tiles, radius_tiles, curve)`` returns the weight (1 at the centre, 0 at the rim and beyond) for every
distance in ``distance_tiles``; ``CURVES`` names the curves. ``smooth`` is the cosine the brushes always used, so an edit
file made before the choice existed paints the same (island-editor "Full spec").
"""

from __future__ import annotations

import numpy as np

CURVES = ("smooth", "linear", "sharp", "sphere", "constant")
DEFAULT = "smooth"


def falloff(distance_tiles, radius_tiles: float, curve: str = DEFAULT) -> np.ndarray:
    """The weight of each distance in ``distance_tiles`` for a brush of ``radius_tiles``; nothing outside the radius."""
    distance_tiles = np.asarray(distance_tiles, np.float64)
    if radius_tiles <= 0:
        return (distance_tiles <= 0).astype(np.float64)
    fraction = np.clip(distance_tiles / radius_tiles, 0.0, 1.0)
    if curve == "smooth":
        weight = 0.5 * (1 + np.cos(np.pi * fraction))
    elif curve == "linear":
        weight = 1 - fraction
    elif curve == "sharp":
        weight = (1 - fraction) ** 2
    elif curve == "sphere":
        weight = np.sqrt(np.clip(1 - fraction * fraction, 0.0, 1.0))
    elif curve == "constant":
        weight = np.ones_like(fraction)
    else:
        raise ValueError(f"unknown falloff {curve!r}: one of {', '.join(CURVES)}")
    return weight * (distance_tiles <= radius_tiles)
