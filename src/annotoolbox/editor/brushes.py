"""How strongly a brush acts at a distance from its centre: the falloff curves of the height, paint, forest and path
blocker brushes.

``falloff(d, radius, curve)`` returns the weight (1 at the centre, 0 at the rim and beyond) for every distance in
``d``; ``CURVES`` names the curves. ``smooth`` is the cosine the brushes always used, so an edit file made before
the choice existed paints the same (island-editor "Full spec").
"""

from __future__ import annotations

import numpy as np

CURVES = ("smooth", "linear", "sharp", "sphere", "constant")
DEFAULT = "smooth"


def falloff(d, radius: float, curve: str = DEFAULT) -> np.ndarray:
    """The weight of each distance in ``d`` for a brush of ``radius``; nothing outside the radius."""
    d = np.asarray(d, np.float64)
    if radius <= 0:
        return (d <= 0).astype(np.float64)
    t = np.clip(d / radius, 0.0, 1.0)
    if curve == "smooth":
        w = 0.5 * (1 + np.cos(np.pi * t))
    elif curve == "linear":
        w = 1 - t
    elif curve == "sharp":
        w = (1 - t) ** 2
    elif curve == "sphere":
        w = np.sqrt(np.clip(1 - t * t, 0.0, 1.0))
    elif curve == "constant":
        w = np.ones_like(t)
    else:
        raise ValueError(f"unknown falloff {curve!r}: one of {', '.join(CURVES)}")
    return w * (d <= radius)
