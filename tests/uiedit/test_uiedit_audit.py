"""Fixes of the uiedit audit: the property panel keeps the text of a string field whole, the Play mode's instance
cache cannot answer for a freed object, the effect cache cannot answer for another picture of the same name."""

from __future__ import annotations

import os

import numpy as np
from tests.fakes import FakeFiles

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from annotoolbox.uiedit import canvas, fields, panels, playinput
from annotoolbox.uiedit.pictures import SPicture


def _field(name: str, code: int, value) -> fields.SField:
    return fields.SField("Rec", {"name": name, "hash": "00000000", "code": f"{code:x}"}, True, value)


def _edit(panel: panels.CPropertyPanel, f: fields.SField, text: str) -> list:
    got: list = []
    panel._m_filling = True  # as ``show_node`` builds its rows
    item = panels._item(panel, [f.name, "", ""], ("field", {}, f), is_editable=True)
    panel._m_filling = False
    panel.edit_field.connect(lambda node, name, value: got.append(value))
    item.setText(1, text)
    return got


def test_string_field_keeps_text_after_a_double_space():
    QApplication.instance() or QApplication([])
    panel = panels.CPropertyPanel()
    got = _edit(panel, _field("Text", fields.STRING, "x"), "two  spaces  kept")
    assert got == ["two  spaces  kept"]


def test_enum_field_still_drops_its_name_suffix():
    QApplication.instance() or QApplication([])
    panel = panels.CPropertyPanel()
    got = _edit(panel, _field("AnchorPoint", fields.ENUM, 4), "4  Center")
    assert got == [4]


class FakeContext:
    def __init__(self, files) -> None:
        self.m_files = files


class FakeHit:
    def __init__(self, node: dict) -> None:
        self.element = type("El", (), {"node": node, "template": None})()


def test_known_does_not_answer_for_a_freed_node():
    playinput.forget()
    ctx = FakeContext(FakeFiles())
    answers = []
    for n in range(50):
        node = {"n": n}  # freed at the next turn: its ``id`` may come back for another node
        answers.append((n, playinput._known("x", ctx, FakeHit(node), lambda n=n: n)))
        del node
    assert all(n == got for n, got in answers)


def test_effect_cache_does_not_mix_up_pictures_of_one_name():
    dark = SPicture("icon", np.full((2, 2, 4), (60, 60, 60, 255), np.uint8))
    bright = SPicture("icon", np.full((2, 2, 4), (200, 200, 200, 255), np.uint8))
    fx = {"brightness": {"BrightnessLevel": -0.5}}
    first = canvas.with_effects(dark, fx)
    second = canvas.with_effects(bright, fx)
    assert second is not first
    assert not np.array_equal(first.rgba, second.rgba)


def test_a_project_preview_file_with_odd_values_opens_and_stopping_it_keeps_the_values(tmp_path):
    from annotoolbox.uiedit import previewdefaults

    sidecar = tmp_path / "anno117-preview.json"
    sidecar.write_text('{"format": 1, "values": [1, 2]}', encoding="utf-8")
    store = previewdefaults.CStore({}, "anno117", sidecar)
    assert store.m_defaults.money == previewdefaults.CAMPAIGN_DEFAULTS.money
    store.set("money", 5)
    store.keep_in_project(False)
    assert not sidecar.exists()
    reopened = previewdefaults.CStore(store.m_apps, "anno117", sidecar)
    assert reopened.m_defaults.money == 5


def test_the_graph_finds_the_binding_of_an_operator_box():
    from types import SimpleNamespace

    from PySide6.QtCore import QPointF

    from annotoolbox.uiedit import graphview

    QApplication.instance() or QApplication([])
    graph = graphview.CBindingGraph()
    graph.resize(900, 300)
    binding = SimpleNamespace(operator="none", op_name="Op", sources=[], targets=[])
    graph.show_graph([binding], lambda end: "")
    x = graphview.COL_W + graphview.GAP_X + 10
    assert graph._binding_at(graph.mapFromScene(QPointF(x, graphview.ROW_H / 2))) is binding
    assert graph._binding_at(graph.mapFromScene(QPointF(1.0, 100000.0))) is None


def test_a_track_whose_keys_are_stored_out_of_time_order_still_reads_in_time_order():
    from annotoolbox.uiedit import animations

    track = animations.STrack(0, "e613cfd0", [animations.SKey(1.0, 10.0), animations.SKey(0.0, 0.0)])
    assert track.value_at(0.5) == 5.0
    assert track.value_at(0.0) == 0.0
    assert track.value_at(2.0) == 10.0


def test_a_radial_wheel_of_no_items_places_none():
    from annotoolbox.uiedit import listlayout

    assert listlayout.sectors("radial-wheel", {}, 0) == []
    assert listlayout.offsets("radial-wheel", {}, 0, (10.0, 10.0)) == []


def test_an_empty_dlc_entry_of_the_catalogue_is_skipped():
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace

    from annotoolbox.uiedit import previewcatalogue

    controller = ET.fromstring("<Asset><DLC/><DLC>5</DLC></Asset>")
    index = SimpleNamespace(m_by_guid={previewcatalogue.DLC_CONTROLLER: object()}, element=lambda guid: controller)
    reader = SimpleNamespace(m_index=index, of_template=lambda template: [], option=lambda guid, *a, **k: guid)
    cat = previewcatalogue.SCatalogue()
    previewcatalogue._progression(reader, cat)
    assert cat.dlcs == [5]


def test_keeping_all_files_leaves_the_shown_file_shown(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.uiedit.uiwindow import CUiWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    shown = SimpleNamespace(dirty=False)
    other = SimpleNamespace(dirty=True)
    saved = []
    stub = SimpleNamespace(m_docs={"a": shown, "b": other}, doc=shown)
    stub.save = lambda: saved.append(stub.doc) or False  # the second file could not be kept
    assert CUiWindow.may_leave(stub) is False
    assert saved == [other] and stub.doc is shown


def test_a_slider_whose_maximum_is_zero_keeps_it(monkeypatch):
    from types import SimpleNamespace

    from annotoolbox.uiedit import playstate

    scope = SimpleNamespace(m_graph=SimpleNamespace(bindings=[]))
    values = {(playinput.S_MIN,): -1.0, (playinput.S_MAX,): 0}
    unit = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    hit = playstate.SHit(unit, (100.0, 10.0), (("a", None),), scope, SimpleNamespace(guid="a"), values)
    monkeypatch.setattr(playinput, "kinds", lambda ctx, h: {"slider"})
    assert playinput.slider_range(hit) == (-1.0, 0.0)
    out = playinput.slide(None, None, [hit], (50.0, 5.0))
    assert {path: v for _t, path, v in out.kept}[(playinput.S_VALUE,)] == -0.5
