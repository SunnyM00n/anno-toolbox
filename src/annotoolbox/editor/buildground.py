"""The ground under a building's footprint, for the live rules (``placedrules``): how much it slopes, whether the
island's path blockers close it (2070's and 1404's mountains) and whether a rock or cliff model stands in it that
carries no footprint of its own.

``problems(session, polys)`` is the entry: sentences, empty when the ground is fit to build on. The slope limit and
the model-name rule are the editor's own (the games do not publish theirs); the blocker cells are the island's.
docs/island-editor.md "Placed objects".
"""

from __future__ import annotations

import re

import numpy as np

from ..infra.i18n import tr
from . import footprints
from .editsession import CEditSession, label_of
from .sessionpath import CELLS

FLAT_STEP = 0.5  # world units: the most the ground may rise or fall between the corners under a footprint
ROCK = re.compile(r"rock|cliff|mountain|boulder", re.IGNORECASE)  # models that are terrain features


def slope(session: CEditSession, polys: list[np.ndarray]) -> float:
    """The largest height difference between the corners of the tiles under the footprint (0: no tile under it)."""
    centres = footprints.cell_centres(polys)
    if not len(centres):
        return 0.0
    corners = session.corner_heights()
    x, z = np.floor(centres[:, 0]).astype(int), np.floor(centres[:, 1]).astype(int)
    heights = [
        corners[np.clip(z + offset_z, 0, corners.shape[0] - 1), np.clip(x + offset_x, 0, corners.shape[1] - 1)]
        for offset_z in (0, 1)
        for offset_x in (0, 1)
    ]
    found = np.concatenate(heights)
    return float(found.max() - found.min())


def slope_problem(session: CEditSession, polys: list[np.ndarray]) -> str:
    step = slope(session, polys)
    if step <= FLAT_STEP:
        return ""
    return tr("islands.stands_sloping_ground_differs_under", step=f"{step:.2f}")


def blocked_problem(session: CEditSession, polys: list[np.ndarray]) -> str:
    """Whether a blocked path cell (2070 and 1404: the height layer, which is where mountains are) lies under it."""
    blocked = session.m_path.m_blocked
    if blocked is None:
        return ""
    cells = np.floor(footprints.cell_centres(polys, 1 / CELLS) * CELLS).astype(int)
    cell_count = len(blocked)
    on_island = (cells[:, 0] >= 0) & (cells[:, 0] < cell_count) & (cells[:, 1] >= 0) & (cells[:, 1] < cell_count)
    hit = blocked[cells[on_island, 1], cells[on_island, 0]] if on_island.any() else np.zeros(0, bool)
    if not hit.any():
        return ""
    return tr("islands.stands_blocked_ground_mountain_cliff", n=int(hit.sum()))


def rock_problem(session: CEditSession, polys: list[np.ndarray]) -> str:
    """A rock or cliff model (by its file name) whose origin is within the footprint, one that has no footprint of its
    own to overlap."""
    table = session.m_island.get("footprints") or {}
    hulls = [footprints.hull(poly) for poly in polys]
    for entry in session.m_entries:
        if not entry.shown or entry.kind == "tree" or entry.cfg in table or not ROCK.search(entry.cfg):
            continue
        position = np.array([[entry.position[0], entry.position[2]]])
        if any(
            footprints.inside(position - (hull.mean(axis=0) - position) * 0.01, hull)[0] for hull in hulls
        ):  # on an edge is not in it
            return tr("islands.stands_rock_cliff_no_footprint", name=label_of(entry.base) or entry.cfg)
    return ""


def problems(session: CEditSession, polys: list[np.ndarray]) -> list[str]:
    """Why the ground under a footprint is not fit to build on (slope, blocked cells, a rock or cliff)."""
    found = (slope_problem(session, polys), blocked_problem(session, polys), rock_problem(session, polys))
    return [problem for problem in found if problem]
