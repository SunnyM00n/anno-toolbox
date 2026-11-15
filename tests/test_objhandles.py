"""The handles over the object builder's 3D view, driven by real mouse events on a window offscreen: a part moved,
turned and scaled by its gizmo, a transformer made when a part has none, a polygon's points dragged, put in and taken
out, a footprint drawn; each drag one undo step, a press away from a handle left to the camera."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import pytest
from tests.test_objbuild import HOUSE, SHARED, game, qt  # noqa: F401  (the fixtures)

from annotoolbox.models import cfgdoc


def _mouse(view, kind, pos, button=None, mods=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    button = button if button is not None else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    event = QMouseEvent(kind, QPointF(pos), QPointF(pos), button, held, mods or Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view, event)


def _drag(view, start, end, steps=4, button=None, mods=None):
    from PySide6.QtCore import QEvent, QPointF

    _mouse(view, QEvent.Type.MouseButtonPress, start, button, mods)
    for k in range(1, steps + 1):
        at = QPointF(start.x() + (end.x() - start.x()) * k / steps, start.y() + (end.y() - start.y()) * k / steps)
        _mouse(view, QEvent.Type.MouseMove, at, button, mods)
    _mouse(view, QEvent.Type.MouseButtonRelease, end, button, mods)


@pytest.fixture
def window(qt, game, tmp_path):  # noqa: F811
    from annotoolbox.objbuild.objwindow import ObjectWindow
    from annotoolbox.suite.settings import Settings

    found = ObjectWindow(Settings(workspace=str(tmp_path / "ws")), None)
    found.resize(1000, 700)
    found.set_tree(game)
    found.open_path(HOUSE)
    found.show()
    qt.processEvents()
    yield found
    found.obj.undo.mark_clean()
    found.close()


def _leaf(window, name, path="Models/0/Transformer/0"):
    node = window.obj.doc("cfg").find(path).node
    return next((f.node for f in cfgdoc.fields(node) if f.name == name), None)


def _middle(points):
    from PySide6.QtCore import QPointF

    return QPointF((points[0].x() + points[1].x()) / 2, (points[0].y() + points[1].y()) / 2)


def _centre(points):
    from PySide6.QtCore import QPointF

    return QPointF(sum(p.x() for p in points[:4]) / 4, sum(p.y() for p in points[:4]) / 4)


def test_a_move_handle_drags_the_part_and_is_one_undo_step(window):
    from PySide6.QtCore import QPointF

    window.objects.select("cfg", "Models/0")
    window.tool.set_mode("move")
    gizmo = window.tool._gizmo()
    assert sorted(gizmo) == ["axis0", "axis1", "axis2", "plane"]
    start = _middle(gizmo["axis1"])
    _drag(window.view, start, start + QPointF(0, -60))
    assert float(_leaf(window, "Position.y").text) > 0.05 and len(window.obj.undo.commands) == 1
    assert window.obj.dirty() == ["cfg"] and b"Position.y" in window.obj.files.edited[SHARED]
    window.undo()
    assert _leaf(window, "Position.y").text == "0" and not window.obj.dirty()
    centre = _centre(window.tool._gizmo()["plane"])
    _drag(window.view, centre, centre + QPointF(40, 25))
    assert float(_leaf(window, "Position.y").text) == 0.0  # on the ground
    assert abs(float(_leaf(window, "Position.x").text)) + abs(float(_leaf(window, "Position.z").text)) > 0.05


def test_a_press_away_from_the_handles_still_turns_the_camera(window):
    from PySide6.QtCore import QEvent, QPointF

    window.objects.select("cfg", "Models/0")
    window.tool.set_mode("move")
    far, before = QPointF(5, 5), window.view.camera.yaw
    _mouse(window.view, QEvent.Type.MouseButtonPress, far)
    _mouse(window.view, QEvent.Type.MouseMove, far + QPointF(30, 0))
    _mouse(window.view, QEvent.Type.MouseButtonRelease, far + QPointF(30, 0))
    assert window.view.camera.yaw != before and not window.obj.dirty()
    on_arrow = _middle(window.tool._gizmo()["axis1"])
    assert window.tool._handle_at(on_arrow) == "axis1"
    window.tool.set_mode("off")
    assert window.tool._handle_at(on_arrow) is None  # off: no handle, whatever is under the mouse


def test_a_ring_turns_and_the_scale_ring_scales(window):
    from PySide6.QtCore import QPointF

    window.objects.select("cfg", "Models/0")
    window.tool.set_mode("rotate")
    ring = window.tool._gizmo()["ring1"]
    k = len(ring) // 8
    _drag(window.view, ring[k], ring[2 * k])
    values = [float(_leaf(window, f"Rotation.{a}").text) for a in "xyzw"]
    assert values[1] != 0 and math.isclose(sum(v * v for v in values), 1.0, rel_tol=1e-3)
    assert len(window.obj.undo.commands) == 1
    window.undo()
    assert _leaf(window, "Rotation.y") is None  # the fields a drag made go with its undo
    window.tool.set_mode("scale")
    point = window.tool._gizmo()["scale"][0]
    _drag(window.view, point, point + QPointF(40, 0))
    assert float(_leaf(window, "Scale").text) > 1.2
    window.undo()
    assert _leaf(window, "Scale") is None and not window.obj.dirty()


def test_a_part_without_a_transformer_gets_one_when_it_is_moved(window):
    from PySide6.QtCore import QPointF

    model = window.obj.doc("cfg").find("Models/0")
    holder = next(c for c in model.node.children if c.name == "Transformer")
    window.obj.apply("cfg", cfgdoc.remove(model.node, holder))
    window.objects.select("cfg", "Models/0")
    window.tool.set_mode("move")
    assert window.tool.target.orient is None
    centre = _centre(window.tool._gizmo()["plane"])
    _drag(window.view, centre, centre + QPointF(50, 0))
    made = window.obj.doc("cfg").find("Models/0/Transformer/0")
    assert made is not None and made.kind == "ORIENTATION_TRANSFORM"
    assert abs(float(_leaf(window, "Position.x").text)) + abs(float(_leaf(window, "Position.z").text)) > 0.05
    window.undo()
    assert window.obj.doc("cfg").find("Models/0/Transformer/0") is None
    window.tool.select(window.objects.current())
    assert window.tool.target.orient is None  # nothing of the preview is left in the target


def _ground(window, x, z):
    from annotoolbox.objbuild.overlay import project

    return project([(x, 0.0, z)], window.view.camera, (window.view.width(), window.view.height()))[0]


def _points(window):
    from annotoolbox.objbuild import areaedit

    return areaedit.area_at(window.obj.doc("ifo"), "BuildBlocker#0").points()


def test_a_point_of_the_footprint_is_dragged_put_in_and_taken_out(window):
    from PySide6.QtCore import QEvent, QPointF, Qt

    window.objects.select("ifo", "BuildBlocker#0")
    assert window.tool.area is not None and window.tool.target is None
    _drag(window.view, _ground(window, 2, -2), _ground(window, 3.1, -2))
    assert _points(window)[1] == (3.0, -2.0) and window.obj.dirty() == ["ifo"]  # snapped to the half units
    assert len(window.obj.undo.commands) == 1
    window.undo()
    assert _points(window)[1] == (2.0, -2.0)
    _drag(window.view, _ground(window, 2, -2), _ground(window, 2.3, -2.2), mods=Qt.KeyboardModifier.AltModifier)
    assert _points(window)[1] == pytest.approx((2.3, -2.2), abs=0.03)  # Alt: free
    window.undo()
    _mouse(window.view, QEvent.Type.MouseButtonDblClick, _ground(window, 0, -2))  # a double-click on a side
    assert len(_points(window)) == 4 and _points(window)[1] == (0.0, -2.0)
    right = Qt.MouseButton.RightButton
    _mouse(window.view, QEvent.Type.MouseButtonPress, _ground(window, 0, -2), right)
    _mouse(window.view, QEvent.Type.MouseButtonRelease, _ground(window, 0, -2), right)
    assert len(_points(window)) == 3  # a right click took it out again
    _mouse(window.view, QEvent.Type.MouseButtonPress, _ground(window, 2, 2), right)
    _mouse(window.view, QEvent.Type.MouseButtonRelease, _ground(window, 2, 2), right)
    assert len(_points(window)) == 3 and "three" in window.statusBar().currentMessage()  # a polygon keeps three
    _mouse(window.view, QEvent.Type.MouseButtonDblClick, QPointF(3, 3))  # away from a side: no point
    assert len(_points(window)) == 3


def test_a_footprint_is_drawn_on_the_grid(window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    def key(code):
        QApplication.sendEvent(window.view, QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier))

    window.objects.select("ifo", "BuildBlocker#0")
    window.footprint_action.trigger()
    assert window.tool.drawing and window.footprint_action.isChecked()
    for x, z in ((-1, -1), (1.1, -1), (1, 1), (-1, 1)):
        _mouse(window.view, QEvent.Type.MouseButtonPress, _ground(window, x, z))
    assert len(window.tool.draft) == 4 and window.tool.draft[1] == (1.0, -1.0)
    key(Qt.Key.Key_Return)
    assert not window.tool.drawing and not window.footprint_action.isChecked()
    assert _points(window) == [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    assert len(window.obj.undo.commands) == 1
    window.undo()
    assert _points(window)[0] == (-2.0, -2.0)
    window.objects.select("cfg", "Models/0")  # none selected, and the file has its footprint
    window.footprint_action.trigger()
    for x, z in ((0, 0), (1, 0), (1, 1)):
        _mouse(window.view, QEvent.Type.MouseButtonPress, _ground(window, x, z))
    key(Qt.Key.Key_Return)
    assert "select it" in window.statusBar().currentMessage() and not window.obj.dirty()
    window.footprint_action.trigger()
    key(Qt.Key.Key_Escape)
    assert not window.tool.drawing


def test_the_handle_menu_sets_the_mode(window):
    assert window.tool.mode == "off" and window.handle_actions["off"].isChecked()
    window.handle_actions["rotate"].trigger()
    assert window.tool.mode == "rotate"
    window.handle_actions["off"].trigger()
    assert window.tool.mode == "off"
