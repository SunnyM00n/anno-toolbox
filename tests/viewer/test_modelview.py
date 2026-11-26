"""The model view's tick asks for a frame only when something arrived, not on every tick while files load."""

from __future__ import annotations

from types import SimpleNamespace

from annotoolbox.viewer import modelrender
from annotoolbox.viewer.modelrender import CModelRenderer


class FakeRenderer:
    """What the tick reads of a renderer, with the loading state as a test sets it."""

    def __init__(self) -> None:
        self.m_busy, self.m_arrived = 3, False
        self.m_box, self.m_longest_fade, self.m_clock_parts = None, 0.0, []

    def busy(self) -> int:
        return self.m_busy

    def wants_frame(self) -> bool:
        arrived, self.m_arrived = self.m_arrived, False
        return arrived


def _ticks(view, count: int) -> int:
    asked = []
    view.update = lambda: asked.append(1)
    for _ in range(count):
        view._tick()
    return len(asked)


def test_the_tick_draws_while_loading_only_when_something_arrived(qt):
    from annotoolbox.viewer.modelview import CRhiModelView

    view = CRhiModelView("d3d12")
    view._m_timer.stop()
    view.m_renderer = FakeRenderer()
    assert _ticks(view, 10) == 0  # ten ticks of loading, nothing arrived: no frame
    view.m_renderer.m_arrived = True
    assert _ticks(view, 3) == 1  # one arrival, one frame
    view.m_playing = True
    assert _ticks(view, 3) == 3  # a playing model still moves on every tick
    view.m_renderer = None
    view.deleteLater()


def _renderer(*, arrived: bool = False, game_arrived: bool = False, again: bool = False, left: int = 0):
    game = SimpleNamespace(changed=lambda: game_arrived, again=lambda: again)
    return SimpleNamespace(
        changed=lambda: arrived,
        m_game_pass=game,
        m_state=SimpleNamespace(look=modelrender.SHADERS),
        m_ctx=SimpleNamespace(m_make_left=left),
    )


def test_a_renderer_wants_a_frame_for_an_arrival_or_work_the_last_frame_left():
    wants = CModelRenderer.wants_frame
    assert not wants(_renderer())  # loading alone is no reason
    assert wants(_renderer(arrived=True))  # a file came in
    assert wants(_renderer(game_arrived=True))  # a shader compile or the install's source came in
    assert wants(_renderer(left=2))  # new parts waited for a later frame (the make budget)
    assert wants(_renderer(again=True))  # a read-back that moved
    plain = _renderer(again=True)
    plain.m_state.look = "editor"
    assert not wants(plain)  # the game's pass is not drawing
