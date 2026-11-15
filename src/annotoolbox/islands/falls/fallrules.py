"""The numbers the waterfall rules share. Stdlib only."""

from __future__ import annotations

import math

WATERFALL_SINK = 0.3  # tiles a fall reaches under the water it falls into; rock there is hidden
WATERFALL_WIDE = 3.0  # tiles: a fall this wide is the broad piece, whatever 2070 stacked it from
CLIP = 0.02  # the share of a sheet inside rock that counts as clipping
BURIED = 0.6  # a sheet still this far inside rock after every move is not hung: the rock hides it
SIDES = (0.0, -0.5, 0.5, -1.0, 1.0)  # across, as a share of the room a narrowing leaves on each side
NARROWER = (1.0, 0.85, 0.7, 0.5)  # the width, down to half: a gorge narrower than its river
COST_STRETCH, COST_SIDE, COST_NARROW = 0.03, 0.01, 0.015  # per 0.25 stretch, 0.25 tiles, 0.15 narrower
COST_STAND_OFF = 0.04  # per tile a sheet's curve is widened (fallsheet)
# tiles: the farthest a sheet reaches out past its lip (vanilla's thin sheet: 0.6); a curve widened to 4 tiles to
# clear the rock stood out as a tongue or arched over the rocks like a bridge (in game, 2026-10-23)
LIP_ZONE = 0.6
FALL_RUN_MAX = 0.45  # tiles: the longest run of the ribbon's step under a sheet, which the sheet's curve clears


def rotation(turn: float) -> tuple[float, float, float, float]:
    """The quaternion (x, y, z, w) of a piece turned by ``turn`` about y: a fall hangs straight."""
    return (0.0, math.sin(turn / 2), 0.0, math.cos(turn / 2))
