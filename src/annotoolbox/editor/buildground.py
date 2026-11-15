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
from .editsession import EditSession, label_of
from .sessionpath import CELLS

FLAT_STEP = 0.5  # world units: the most the ground may rise or fall between the corners under a footprint
ROCK = re.compile(r"rock|cliff|mountain|boulder", re.IGNORECASE)  # models that are terrain features


def slope(session: EditSession, polys: list[np.ndarray]) -> float:
    """The largest height difference between the corners of the tiles under the footprint (0: no tile under it)."""
    centres = footprints.cell_centres(polys)
    if not len(centres):
        return 0.0
    corners = session.corner_heights()
    x, z = np.floor(centres[:, 0]).astype(int), np.floor(centres[:, 1]).astype(int)
    heights = [
        corners[np.clip(z + dz, 0, corners.shape[0] - 1), np.clip(x + dx, 0, corners.shape[1] - 1)]
        for dz in (0, 1)
        for dx in (0, 1)
    ]
    found = np.concatenate(heights)
    return float(found.max() - found.min())


def slope_problem(session: EditSession, polys: list[np.ndarray]) -> str:
    step = slope(session, polys)
    if step <= FLAT_STEP:
        return ""
    return tr("stands on sloping ground: the ground differs by {step} under it", step=f"{step:.2f}")


def blocked_problem(session: EditSession, polys: list[np.ndarray]) -> str:
    """Whether a blocked path cell (2070 and 1404: the height layer, which is where mountains are) lies under it."""
    blocked = session.path.blocked
    if blocked is None:
        return ""
    cells = np.floor(footprints.cell_centres(polys, 1 / CELLS) * CELLS).astype(int)
    n = len(blocked)
    ok = (cells[:, 0] >= 0) & (cells[:, 0] < n) & (cells[:, 1] >= 0) & (cells[:, 1] < n)
    hit = blocked[cells[ok, 1], cells[ok, 0]] if ok.any() else np.zeros(0, bool)
    if not hit.any():
        return ""
    return tr("stands on blocked ground (a mountain or cliff: {n} path cells)", n=int(hit.sum()))


def rock_problem(session: EditSession, polys: list[np.ndarray]) -> str:
    """A rock or cliff model (by its file name) whose origin is within the footprint, one that has no footprint of its
    own to overlap."""
    table = session.island.get("footprints") or {}
    hulls = [footprints.hull(p) for p in polys]
    for e in session.entries:
        if not e.shown or e.kind == "tree" or e.cfg in table or not ROCK.search(e.cfg):
            continue
        at = np.array([[e.position[0], e.position[2]]])
        if any(footprints.inside(at - (h.mean(axis=0) - at) * 0.01, h)[0] for h in hulls):  # on an edge is not in it
            return tr(
                "{name} stands in it: a rock or cliff with no footprint of its own", name=label_of(e.base) or e.cfg
            )
    return ""


def problems(session: EditSession, polys: list[np.ndarray]) -> list[str]:
    """Why the ground under a footprint is not fit to build on (slope, blocked cells, a rock or cliff)."""
    found = (slope_problem(session, polys), blocked_problem(session, polys), rock_problem(session, polys))
    return [p for p in found if p]
