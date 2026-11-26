"""When the game look reuses its kept opaque frame (``editor.desktop.desktopgamestill.reuse_of``)."""

from types import SimpleNamespace

import numpy as np

from annotoolbox.editor.desktop import desktopgamestill
from annotoolbox.render import gamestill


def _island(is_sky_running: bool) -> SimpleNamespace:
    sky = SimpleNamespace(is_running=is_sky_running)
    return SimpleNamespace(m_frame=SimpleNamespace(m_still=gamestill.CStill()), m_pages=None, _m_unbaked=False,
                           _m_late=False, m_edits=0, m_native_shading=SimpleNamespace(m_sky=sky))  # fmt: skip


def _decisions(island, count: int) -> list:
    view_frame = SimpleNamespace(view=np.identity(4), view_proj=np.identity(4), lt={"sunDir": (0.0, 1.0, 0.0)})
    options = SimpleNamespace(still_frame=True)
    return [
        desktopgamestill.reuse_of(island, view_frame, (8, 8), options, [], [], None, True, False) for _ in range(count)
    ]


def test_a_standing_view_keeps_its_frame_once_it_was_drawn_in_full():
    assert _decisions(_island(False), 4) == [None, False, True, True]


def test_the_frame_is_drawn_in_full_while_the_skys_chain_runs_over_frames():
    """A kept frame dispatches no shading, so the sky's chain took a step only when the kept frame was drawn anew
    (once a second): its steps came a second apart, and the frame kept showed the sky half made."""
    assert _decisions(_island(True), 4) == [None] * 4
    island = _island(False)
    island.m_native_shading = None  # the shading by the current path: no sky chain of the native backend
    assert _decisions(island, 3) == [None, False, True]
