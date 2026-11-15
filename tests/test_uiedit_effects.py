"""The UI preview's later effects on arrays and in a placed scene: blur, distance field, blink, texture scroll, and
the effects of a container reaching the pictures it holds."""

from __future__ import annotations

import numpy as np
import pytest
from tests.test_uiedit_compose import composed  # noqa: F401  (a fixture)

from annotoolbox.formats import phoenixedit
from annotoolbox.uiedit import effects, layout


def _square() -> np.ndarray:
    rgba = np.zeros((16, 16, 4), np.uint8)
    rgba[6:10, 6:10] = (255, 255, 255, 255)
    return rgba


def test_blur_spreads_a_square_and_keeps_its_weight():
    out = effects.apply(_square(), {"blur": {"Strength": 0.5}})
    assert out[6, 6, 3] < 255 and out[5, 6, 3] > 0  # the corner softens and the edge reaches outward
    assert abs(int(out[..., 3].sum()) - int(_square()[..., 3].sum())) < 0.1 * int(_square()[..., 3].sum())
    assert effects.drawn({"blur": {"Strength": 0.0}}) == {}  # no strength: nothing to draw


def test_a_distance_field_turns_the_alpha_into_a_soft_edged_shape_with_an_outline():
    rgba = np.zeros((16, 16, 4), np.uint8)
    rgba[..., 3] = np.linspace(255, 0, 16).astype(np.uint8)[None, :]  # alpha falls from left to right: a distance
    plain = {"Thickness": 0.5, "Gradient": 0.1, "InnerSize": 0.0, "OuterSize": 0.0}
    out = effects.apply(rgba, {"distance_field": plain})
    assert out[8, 0, 3] == 255 and out[8, 15, 3] == 0 and 0 < out[8, 8, 3] < 255  # inside, outside, the soft edge
    outlined = effects.apply(rgba, {"distance_field": {**plain, "OuterSize": 0.8, "OuterGradient": 0.3,
                                                       "OuterColor": (1.0, 0.0, 0.0)}})  # fmt: skip
    assert outlined[8, 12, 3] > 0 and outlined[8, 12, 0] > outlined[8, 12, 2]  # a red glow past the edge


def test_blink_brightens_toward_a_warm_white_and_a_scroll_wraps():
    rgba = np.full((4, 4, 4), (60, 60, 60, 255), np.uint8)
    lit = effects.apply(rgba, {"blink": {"FadeIntensity": 1.0}}, seconds=1.0)  # the wave's peak at one second
    assert lit[0, 0, 0] > 100 and lit[0, 0, 0] > lit[0, 0, 2]  # warm: red above blue
    assert effects.apply(rgba, {"blink": {"FadeIntensity": 0.0}})[0, 0, 0] == 60  # fading takes it away
    strip = np.zeros((2, 4, 4), np.uint8)
    strip[:, 0] = 255
    moved = effects.apply(strip, {"scroll": {"Speed": 0.25}}, seconds=1.0)
    assert moved[0, 1, 0] == 255 and moved[0, 0, 0] == 0  # a quarter of the width in a second
    assert effects.apply(strip, {"scroll": {"Speed": 0.25}}).tolist() == strip.tolist()  # at time zero: as stored


def test_the_effects_of_a_container_reach_the_pictures_it_holds(composed, monkeypatch):  # noqa: F811
    read = effects.read

    def container_whitens(node, overrides, load):
        if phoenixedit.object_name(node) == "First":
            return {"whiten": {"Enable": True}}
        return read(node, overrides, load)

    monkeypatch.setattr(effects, "read", container_whitens)
    frame = layout.place("composed", composed.root("composed"), layout.Context(composed))
    pictures = [i for i in frame.items if i.kind == "image"]
    first, second = pictures[0], pictures[1]  # the container "First" places the inner file; "Second" does not
    assert "whiten" in first.effects and "whiten" not in second.effects


def _bright_picture():
    from annotoolbox.uiedit.pictures import Picture

    return Picture("grey", np.full((4, 4, 4), (60, 60, 60, 255), np.uint8))


def test_a_blink_and_a_scroll_are_made_again_per_time_step_and_other_effects_once():
    pytest.importorskip("PySide6")
    from annotoolbox.uiedit import canvas

    pic, blink = _bright_picture(), {"blink": {"FadeIntensity": 1.0}}
    at_zero, at_peak = canvas.with_effects(pic, blink, 0.0), canvas.with_effects(pic, blink, 0.5)
    assert at_peak.rgba[0, 0, 0] > at_zero.rgba[0, 0, 0]  # the wave's peak (a quarter of its 2 s) against its middle
    assert canvas.with_effects(pic, blink, 0.5 + effects.STEP_SECONDS / 4) is at_peak  # inside one step: the same
    still = {"whiten": {"Enable": True}}
    assert canvas.with_effects(pic, still, 0.0) is canvas.with_effects(pic, still, 7.3)  # time does not enter its key


def test_a_frame_says_whether_a_picture_in_it_moves():
    pytest.importorskip("PySide6")
    from annotoolbox.uiedit import canvas

    def frame(**fx):
        return layout.Frame((10.0, 10.0), [layout.DrawItem("image", layout.IDENTITY, (10.0, 10.0), effects=fx)])

    assert canvas.moves(frame(blink={"FadeIntensity": 1.0})) and canvas.moves(frame(scroll={"Speed": 0.5}))
    assert not canvas.moves(frame(whiten={"Enable": True})) and not canvas.moves(frame())


def test_the_preview_runs_its_clock_only_for_a_frame_with_a_moving_effect_while_playing():
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit.preview import Preview

    QApplication.instance() or QApplication([])
    view = Preview()

    def frame(**fx):
        return layout.Frame((10.0, 10.0), [layout.DrawItem("image", layout.IDENTITY, (10.0, 10.0), effects=fx)])

    view.show_frame(frame(whiten={"Enable": True}), None)
    assert not view._clock.isActive()  # a still file costs nothing
    view.show_frame(frame(scroll={"Speed": 0.5}), None)
    assert view._clock.isActive()
    view.effect_seconds = 2.0
    view.set_play_effects(False)
    assert not view._clock.isActive() and view.effect_seconds == 0.0  # frozen at time zero
    view.set_play_effects(True)
    assert view._clock.isActive()
    view.show_frame(None, None)
    assert not view._clock.isActive()
