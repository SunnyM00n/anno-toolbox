"""The UI editor's motion on synthetic files: eased keys, the sequence editor's key edits with undo, the tween
components played in the preview, and the window's Sequence dock offscreen."""

from __future__ import annotations

import os
import uuid

import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import GEN, WIDGET, widget_file
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenixedit, phoenixread
from annotoolbox.sources import gametree
from annotoolbox.uiedit import animations, elements, fields, layout, library, tweens, uidoc, uifiles

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ANIMATED = str(uuid.uuid5(uuid.NAMESPACE_URL, "animated"))
ALPHA_TWEEN = "09d9ca6f-e3e8-4358-9b10-5ce63b067273"


def _key(t: float, v: float, interpolator: int = 1) -> dict:
    k = phoenixedit.new_object(SCHEMA, "32f7d270", versions=VERSIONS)
    phoenixedit.set_field(k, "Time", t)
    phoenixedit.set_field(k, "Value", v)
    phoenixedit.set_field(k, animations.INTERPOLATOR, interpolator)
    return k


def animated_file(interpolator: int = 1) -> bytes:
    """The widget file with one animation: a float track on end 0 from 0 to 1 in one second."""
    root = phoenixread.load(widget_file(), SCHEMA).m_root
    root["guid"] = uuid.UUID(ANIMATED).bytes_le
    anim = phoenixedit.new_object(SCHEMA, "b03e911f", guid=uuid.uuid5(uuid.NAMESPACE_URL, "in").bytes_le,
                                  versions=VERSIONS)  # fmt: skip
    phoenixedit.set_name(anim, "Anim_In")
    phoenixedit.set_field(anim, "FrameRate", 60)
    phoenixedit.set_field(anim, "Duration", 60)
    track = phoenixedit.new_object(SCHEMA, "e613cfd0", versions=VERSIONS)
    phoenixedit.set_field(track, "AnimatedPropertyIndex", 0)
    phoenixedit.set_objects(track, "Track", [_key(0.0, 0.0, interpolator), _key(1.0, 1.0)])
    phoenixedit.set_objects(anim, "Tracks", [track])
    phoenixedit.set_objects(root, "AnimationInstances", [anim])
    return _write("WI", root)


def test_eases_follow_the_named_curves():
    assert animations.ease("Linear", 0.25) == 0.25
    assert animations.ease("Quad_In", 0.5) == 0.25 and animations.ease("Quad_Out", 0.5) == 0.75
    assert animations.ease("Cubic_InOut", 0.5) == pytest.approx(0.5)
    for name in animations.EASES:
        assert animations.ease(name, 0.0) == pytest.approx(0.0, abs=1e-3), name
        assert animations.ease(name, 1.0) == pytest.approx(1.0, abs=1e-3), name
    assert animations.interpolator_name(0) == "Constant" and animations.interpolator_name(3) == "Sine_Out"


def test_a_key_interpolator_shapes_the_track():
    (linear,) = animations.read(phoenixread.load(animated_file(1), SCHEMA).m_root)
    assert linear.values_at(0.5) == [(0, 0.5)]
    (quad,) = animations.read(phoenixread.load(animated_file(5), SCHEMA).m_root)  # 5 = Quad_In
    assert quad.values_at(0.5) == [(0, 0.25)]
    (held,) = animations.read(phoenixread.load(animated_file(0), SCHEMA).m_root)
    assert held.values_at(0.99) == [(0, 0.0)] and held.values_at(1.0) == [(0, 1.0)]


def test_the_sequence_edits_keys_in_time_order_and_undoes(ui_tree, tmp_path):
    (tmp_path / "game" / GEN / ANIMATED).write_bytes(animated_file())
    files = uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))
    doc = uidoc.CUiDoc(files, ANIMATED, files.read(ANIMATED))
    (anim,) = animations.read(doc.m_root)
    track = anim.tracks[0]
    doc.add_key(track.node, 0.5, 0.8, interpolator=5)
    times = [k.time for k in animations.read(doc.m_root)[0].tracks[0].keys]
    assert times == [0.0, 0.5, 1.0]
    first = animations.read(doc.m_root)[0].tracks[0].keys[0]
    doc.set_key(track.node, first.node, time=0.75, value=0.1)
    keys = animations.read(phoenixread.load(doc.to_bytes(), SCHEMA).m_root)[0].tracks[0].keys
    assert [(k.time, k.value) for k in keys] == [(0.5, pytest.approx(0.8)), (0.75, pytest.approx(0.1)), (1.0, 1.0)]
    assert keys[0].interpolator == 5
    doc.remove_key(track.node, keys and animations.read(doc.m_root)[0].tracks[0].keys[1].node)
    assert len(animations.read(doc.m_root)[0].tracks[0].keys) == 2
    while doc.m_undo.undo() is not None:
        pass
    assert doc.to_bytes() == doc.m_original


def _tween_library(tmp_path) -> library.CLibrary:
    def item(guid, name, parent, props):
        return library.read_item("components", {"PhoenixAssetData": {"Object": {
            "ObjectID": guid, "Name": name, "FolderPath": "Animations", "ParentComponentTemplate": parent,
            "CustomProperties": [{"Object": {"UniqueId": uid, "PropertyName": n, "PropertyType": t,
                                             "DefaultValue": {"Value": d}}} for uid, n, t, d in props]}}})  # fmt: skip

    base = item(tweens.TWEEN, "Tween", None, [(11, "Duration", "CustomPropertyType_Float32", 1.0),
                                              (12, "Delay", "CustomPropertyType_Float32", 0.0),
                                              (13, "Yoyo", "CustomPropertyType_Boolean", False),
                                              (14, "LoopCount", "CustomPropertyType_Int32", 1)])  # fmt: skip
    alpha = item(ALPHA_TWEEN, "AlphaTween", tweens.TWEEN, [(21, "StartAlpha", "CustomPropertyType_Float32", 0.0),
                                                           (22, "EndAlpha", "CustomPropertyType_Float32", 1.0)])  # fmt: skip
    return library.CLibrary([base, alpha])


def test_tweens_play_in_the_preview(ui_tree, tmp_path):
    files = uifiles.CUiFiles(ui_tree)
    doc = uidoc.CUiDoc(files, WIDGET, files.read(WIDGET))
    lib = _tween_library(tmp_path)
    picture = next(e for e in elements.tree(doc.m_root).walk() if e.kind == "image")
    comp = doc.add_component(picture.node, ALPHA_TWEEN, "FadeIn")
    uid = int(fields.get(comp, "UniqueId"))
    doc.set_property_value(picture.node, [uid, 11], fields.FLOAT, 2.0)  # two seconds instead of one
    (tw,) = tweens.read(doc.m_root, lib, files.root)
    assert (tw.kind, tw.get("Duration"), tw.target) == ("AlphaTween", 2.0, (0x80000006,))
    assert tw.value_at(1.0, {}) == pytest.approx(0.5) and tw.value_at(5.0, {}) == 1.0
    tw.values["Yoyo"] = True
    assert tw.value_at(3.0, {}) == pytest.approx(0.5) and tw.value_at(9.0, {}) == 0.0
    tw.values["Yoyo"] = False
    assert tweens.SRun([tw]).seconds == 2.0
    ctx = layout.CContext(files, m_design=False)
    ctx.m_tweens = tweens.writes([tw], 0.5, {})
    frame = layout.place(WIDGET, doc.m_root, ctx)
    image = next(i for i in frame.items if i.kind == "image")
    assert image.alpha == pytest.approx(0.25)


def test_a_playing_animation_stands_still_while_its_picture_is_hidden():
    """Another app shown in the suite's window hides the preview: a step would lay it out for nobody."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QWidget

    from annotoolbox.uiedit.panels import CStatesPanel

    QApplication.instance() or QApplication([])
    panel, stage, steps = CStatesPanel(), QWidget(), []
    panel.m_stage = stage
    panel.show_states([SimpleNamespace(name="fade", seconds=1.0, tracks=[], guid="g")], None, str)
    panel.m_combo.setCurrentIndex(1)
    panel.animation.connect(lambda guid, t: steps.append(t))
    panel._toggle()
    assert panel.m_timer.isActive()
    panel._tick()
    assert steps == [] and panel.m_slider.value() == 0  # hidden: nothing is laid out, the play head waits
    stage.show()
    panel._tick()
    panel._tick()
    assert len(steps) == 2 and steps[1] > steps[0] > 0
    stage.hide()
    panel._tick()
    assert len(steps) == 2 and panel.m_timer.isActive()  # still playing: it goes on when the picture is back
    panel._toggle()
    stage.deleteLater()
    panel.deleteLater()


def test_the_window_shows_the_sequence_and_edits_a_key(ui_tree, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.settings import CSettings
    from annotoolbox.uiedit.uiwindow import CUiWindow, _CGame

    (tmp_path / "game" / GEN / ANIMATED).write_bytes(animated_file())
    qt = QApplication.instance() or QApplication([])
    window = CUiWindow(CSettings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    window.m_sync_layout = True  # an edit is made at once, not queued behind a layout running off the thread
    window._opened("anno117", "", _CGame("anno117", gametree.loose_tree(tmp_path / "game"), None))
    window.open_file(ANIMATED)
    window.states.m_combo.setCurrentIndex(1)
    assert window.sequence.m_animation is not None and window.sequence.m_canvas.m_tracks
    assert window.states.m_stage is window.preview  # a playing animation stands still while the preview is hidden
    window.sequence.m_canvas.added.emit(0, 0.25)
    assert [k.time for k in animations.read(window.doc.m_root)[0].tracks[0].keys] == [0.0, 0.25, 1.0]
    assert window.doc.dirty
    canvas = window.sequence.m_canvas  # Del on a picked key is the canvas's, not the window's "Remove the element"
    for current, taken in (((0, 1), True), ((0, -1), False)):
        canvas.m_current = current
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, override)
        assert override.isAccepted() is taken
    window.doc.m_undo.mark_clean()  # closing asks about unsaved edits otherwise
    window.close()
    qt.processEvents()
