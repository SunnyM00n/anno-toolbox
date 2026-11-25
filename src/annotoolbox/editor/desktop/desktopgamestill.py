"""When the game look's opaque frame can be reused (``render.gamestill``): ``reuse_of`` builds the key of what the
opaque passes read and asks the frame's ``CStill``. Everything that can change the G-buffer, the shadow atlas or the
lit colour is in the key or ends the reuse by its own flag: the camera and size, the light, the options, the nodes and
decals drawn, the objects' draws (``desktopgameobjects.CGameObjects.frame`` hands out the same ones while nothing in
them changed), the edits, and the bake, the pages and the cascades still settling. docs/rendering/performance.md.
"""

from __future__ import annotations

import time

import numpy as np

from ...render.gamestill import light_key


def reuse_of(island, view_frame, size_px: tuple[int, int], options, models: list, decals: list, objects,
             can_keep: bool, shadows: bool) -> bool | None:  # fmt: skip
    """None: draw as always (the switch is off, the frame cannot be kept, the scene still settles); False: draw in
    full and keep the opaque frame; True: the kept frame stands in for the opaque passes."""
    still = island.m_frame.m_still
    pages = island.m_pages
    settling = island._m_unbaked or (pages is not None and pages.busy()) or (shadows and island._m_late)
    if not getattr(options, "still_frame", True) or not can_keep or settling:
        still.forget()
        return None
    key = (
        np.asarray(view_frame.view).tobytes(),
        np.asarray(view_frame.view_proj).tobytes(),
        tuple(size_px),
        repr(options),
        light_key(view_frame.lt),
        tuple(id(model) for model in models),
        tuple(id(decal) for decal in decals),
        id(objects),
        island.m_edits,
        shadows,
    )
    return still.decide(key, time.monotonic())


def nodes_of(island, view_frame, eye, options) -> list:
    """The terrain's nodes for this frame: the last frame's while the camera, the edits and the island's files stand
    and nothing was settling (the pick is a pure function of those), else picked anew (``CTerrain.nodes``)."""
    terrain, pages = island.m_terrain, island.m_pages
    key = (np.asarray(view_frame.view_proj).tobytes(), tuple(eye), bool(getattr(options, "cull", True)),
           island.m_edits, id(terrain), id(terrain.m_source), getattr(options, "still_frame", True))  # fmt: skip
    settled = not (island._m_unbaked or (pages is not None and pages.busy()))
    last = island.m_nodes
    if settled and last is not None and last[0] == key and getattr(options, "still_frame", True):
        return last[1]
    models = terrain.nodes(eye, island._visible(view_frame.planes))
    island.m_nodes = (key, models)
    return models
