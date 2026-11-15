"""The gizmo's three drags (``desktopgizmo.GizmoControls``): each answers what the mouse made of the selection."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop.desktopgizmo import GizmoControls
from annotoolbox.render import picking


class _Mouse:
    def __init__(self, x: float, y: float) -> None:
        self._x, self._y = x, y

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y


def _controls(ray, tilt: bool = False) -> GizmoControls:
    c = GizmoControls()
    c.ray = lambda _pos: ray
    c.gizmo = SimpleNamespace(tilt=tilt, mode="translate")
    return c


def test_a_drag_along_an_axis_moves_by_the_distance_the_ray_comes_closest_at():
    handle = {"axis": 0, "dir": np.array([1.0, 0, 0])}
    start = picking.along_axis((np.array([0.0, 5, 0]), np.array([0, -1.0, 0])), np.zeros(3), handle["dir"])
    st = {"handle": handle, "center": np.zeros(3), "anchor": start}
    moved = _controls((np.array([3.0, 5, 0]), np.array([0, -1.0, 0])))._drag_move(st, None)
    assert moved[1:] == (None, None) and moved[0] == pytest.approx([3.0, 0, 0])


def test_a_drag_along_an_axis_the_ray_runs_parallel_to_stays_where_it_is():
    handle = {"axis": 0, "dir": np.array([1.0, 0, 0])}
    st = {"handle": handle, "center": np.array([2.0, 0, 0]), "anchor": 0.0}
    moved = _controls((np.array([0.0, 5, 0]), np.array([1.0, 0, 0])))._drag_move(st, None)
    assert moved[0] == pytest.approx([2.0, 0, 0])


def test_a_drag_over_the_ground_plane_moves_on_it_and_a_ray_that_misses_it_moves_nothing():
    handle = {"axis": "plane", "dir": np.array([0, 1.0, 0])}
    st = {"handle": handle, "center": np.array([1.0, 2, 1]), "anchor": np.array([1.0, 2, 1])}
    hit = _controls((np.array([4.0, 6, 1]), np.array([0, -1.0, 0])))._drag_move(st, None)
    assert hit[0] == pytest.approx([4.0, 2, 1])
    assert _controls((np.array([0.0, 6, 0]), np.array([1.0, 0, 0])))._drag_move(st, None) is None  # parallel to it


def test_a_drag_round_the_vertical_ring_turns_by_the_angle_swept_and_a_tilt_turns_about_the_models_axis():
    handle = {"axis": 1, "dir": np.array([0, 1.0, 0])}
    st = {"handle": handle, "center": np.zeros(3), "anchor": np.array([1.0, 0, 0]), "quat": np.array([0, 0, 0, 1.0])}
    _none, turned, _ = _controls((np.array([0.0, 3, 1]), np.array([0, -1.0, 0])))._drag_turn(st, None)
    assert list(turned) == pytest.approx([0, -math.sin(math.pi / 4), 0, math.cos(math.pi / 4)])  # a quarter turn back
    tilt = {"axis": 0, "dir": np.array([1.0, 0, 0])}
    st = {"handle": tilt, "center": np.zeros(3), "anchor": np.array([0, 1.0, 0]), "quat": np.array([0, 0, 0, 1.0])}
    _none, tilted, _ = _controls((np.array([3.0, 0, 1]), np.array([-1.0, 0, 0])), tilt=True)._drag_turn(st, None)
    assert abs(tilted[0]) == pytest.approx(math.sin(math.pi / 4)) and tilted[1] == tilted[2] == 0


def test_a_scale_drag_follows_the_handles_screen_direction_and_a_prop_stretches_one_axis():
    st = {"handle": {"axis": 1}, "screen": (0.0, 0.0, 10.0, 0.0), "pos": _Mouse(0, 0), "scale": 1.0}
    assert _controls(None)._drag_scale(st, _Mouse(10, 0)) == (None, None, 2.0)
    assert _controls(None)._drag_scale(st, _Mouse(-40, 0))[2] == pytest.approx(0.05)  # never to nothing
    assert _controls(None)._drag_scale({**st, "scale": [1.0, 2.0, 3.0]}, _Mouse(10, 0)) == (None, None, [1.0, 4.0, 3.0])


def test_the_views_key_table_follows_the_camera_mode_the_tool_and_the_selection():
    from PySide6.QtCore import Qt

    from annotoolbox.editor.desktop.desktopview import TOOL_KEYS, IslandControls

    said, seen = [], []
    view = SimpleNamespace(
        camera_mode="orbit",
        tool="select",
        selected=None,
        gizmo=SimpleNamespace(mode="translate", tilt=False),
        status=SimpleNamespace(emit=said.append),
        camera=SimpleNamespace(
            pan=lambda dx, dy: seen.append(("pan", dx, dy)), lift=lambda n: seen.append(("lift", n))
        ),
        placer=SimpleNamespace(turn=lambda n: seen.append(("turn", n))),
        set_tool=lambda tool: seen.append(("tool", tool)),
        focus_on=lambda entries: seen.append(("focus", entries)),
        delete_selection=lambda: None,
        _deselect=lambda: None,
    )
    view._gizmo_key = lambda mode: IslandControls._gizmo_key(view, mode)
    view._turn_key = lambda: IslandControls._turn_key(view)
    keys = Qt.Key
    table = IslandControls._key_table(view)
    assert set(TOOL_KEYS) <= set(table) and keys.Key_PageUp not in table and keys.Key_BracketLeft not in table
    assert keys.Key_F not in table  # nothing selected
    table[keys.Key_Left]()
    table[keys.Key_S]()
    assert seen == [("pan", 40, 0), ("tool", "select")]
    table = IslandControls._key_table(view)
    table[keys.Key_E]()  # the turn handle first, then the tilt handle
    assert (view.gizmo.mode, view.gizmo.tilt) == ("rotate", False)
    table[keys.Key_E]()
    assert view.gizmo.tilt and "tilt" in said[-1]
    view.camera_mode, view.tool, view.selected = "fly", "place", "entry"
    table = IslandControls._key_table(view)
    assert {keys.Key_PageUp, keys.Key_PageDown, keys.Key_BracketLeft, keys.Key_BracketRight, keys.Key_F} <= set(table)
