"""The kept opaque frame (``render.gamestill``) and the view's idle rate (``desktopview.CIslandControls._due``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from annotoolbox.render import gamestill


def test_decide_draws_the_first_frame_of_a_key_plainly_then_keeps_then_reuses() -> None:
    still = gamestill.CStill()
    assert still.decide(("a",), 0.0) is None  # the first frame of the key
    assert still.decide(("a",), 0.1) is False  # drawn in full and kept
    assert still.decide(("a",), 0.2) is True
    assert still.decide(("a",), 0.3) is True
    assert still.m_reused == 2


def test_decide_a_changed_key_or_no_key_forgets_the_kept_frame() -> None:
    still = gamestill.CStill()
    for now in (0.0, 0.1, 0.2):
        still.decide(("a",), now)
    assert still.decide(("b",), 0.3) is None
    assert still.decide(("b",), 0.4) is False
    assert still.decide(None, 0.5) is None  # the scene still settles
    assert still.decide(("b",), 0.6) is None  # nothing kept any more: the key's first frame again


def test_decide_draws_the_kept_frame_anew_after_keep_seconds() -> None:
    still = gamestill.CStill()
    still.decide(("a",), 0.0)
    still.decide(("a",), 0.1)
    assert still.decide(("a",), 0.1 + gamestill.KEEP_SECONDS - 0.01) is True
    assert still.decide(("a",), 0.1 + gamestill.KEEP_SECONDS + 0.01) is False


def test_light_key_leaves_the_water_clock_out() -> None:
    first = gamestill.light_key({"sunDir": (0.1, 0.9, 0.2), "uwTime": 1.0, "uwCaustics": 0.5, "name": "noon"})
    later = gamestill.light_key({"sunDir": (0.1, 0.9, 0.2), "uwTime": 7.0, "uwCaustics": 0.1, "name": "noon"})
    moved = gamestill.light_key({"sunDir": (0.1, 0.8, 0.2), "uwTime": 1.0, "uwCaustics": 0.5, "name": "noon"})
    assert first == later
    assert first != moved


@pytest.mark.parametrize(
    ("input_ago_s", "since_draw_s", "is_on", "working", "due"),
    [
        (1.0, 0.07, True, False, True),  # input lately: every tick
        (10.0, 0.07, True, False, False),  # at rest, drawn a moment ago
        (10.0, 0.2, True, False, True),  # at rest, the slow rate's turn
        (10.0, 0.07, False, False, True),  # the switch is off
        (10.0, 0.07, True, True, True),  # parts still come in
    ],
)
def test_idle_rate_redraws_slowly_only_at_rest(input_ago_s, since_draw_s, is_on, working, due) -> None:
    from annotoolbox.editor.desktop.desktopview import CIslandControls

    now = 100.0
    view = SimpleNamespace(
        m_opts=SimpleNamespace(idle_rate=is_on), _m_input_at=now - input_ago_s, _m_drawn_at=now - since_draw_s
    )
    assert CIslandControls._due(view, SimpleNamespace(m_working=working), now) is due
    assert (view._m_drawn_at == now) is due
