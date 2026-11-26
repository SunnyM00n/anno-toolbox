"""The game's-shaders pass keeps what its parts failed to compile: a ground that did not compile stays False."""

import numpy as np

from annotoolbox.viewer.gamepass import CGamePass


def test_a_ground_that_fails_to_compile_stays_false_and_is_not_asked_again():
    asked = []

    def failing_part(self, key, compile_, make, what, instead, on_error=lambda: None):
        asked.append(key)
        on_error()
        return None

    gate = object.__new__(CGamePass)
    gate.m_ground = None
    gate._part = failing_part.__get__(gate)  # the compile error path of ``ownpass.COwnPass._part``
    assert gate._ground(np.eye(4)) is None
    assert gate.m_ground is False
    assert gate._ground(np.eye(4)) is None
    assert asked == ["ground"]


def test_the_view_draws_on_while_the_clouds_and_fog_settle(monkeypatch):
    """The clouds and fog accumulate over frames (``gameatmosphere.CAccumulation``): after the camera stops, the
    pass is busy until each pixel was marched again, then the view rests as before."""
    from annotoolbox.viewer import ownpass

    monkeypatch.setattr(ownpass.COwnPass, "busy", lambda self: 0)
    gate = object.__new__(CGamePass)
    gate.m_settling = True
    assert gate.busy() == 1
    gate.m_settling = False
    assert gate.busy() == 0


def test_the_scene_key_follows_the_clock_and_each_models_place_pose_values_and_raw_bytes():
    """``SPasses.scene``: the clouds' shadow is kept while every drawn model and the clock stand."""
    from types import SimpleNamespace

    from annotoolbox.viewer.gamepass import _scene_key

    def model(**changes):
        fields = {"m_world": np.eye(4), "m_palette": None, "m_values": {"cbCloth.cWind": 1.0}, "m_raw": {"cb": b"ab"}}
        return SimpleNamespace(**{**fields, **changes})

    plain = SimpleNamespace(m_world=np.eye(4), m_palette=None, m_values={}, m_raw={})
    still = _scene_key(0.0, [("a", model()), ("b", plain)])
    assert _scene_key(0.0, [("a", model()), ("b", plain)]) == still  # made anew each frame, equal by content
    moved = np.eye(4)
    moved[3, 0] = 1.0
    for changed in ({"m_world": moved}, {"m_palette": np.ones((2, 4, 4))}, {"m_values": {"cbCloth.cWind": 2.0}},
                    {"m_raw": {"cb": b"ac"}}):  # fmt: skip
        assert _scene_key(0.0, [("a", model(**changed)), ("b", plain)]) != still
    assert _scene_key(1.0, [("a", model()), ("b", plain)]) != still  # the view's clock moved
    assert _scene_key(0.0, [("a", model())]) != still and _scene_key(0.0, []) == (0.0, (), b"", ())
