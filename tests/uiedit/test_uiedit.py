"""The UI editor (uiedit) on synthetic Phoenix files: fields, operators, the binding graph and its edits, the layout,
animations, view models, the document's undo and bytes, the project's share, and the window offscreen."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import FULL, GEN, IMAGE, WIDGET, widget_file
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS

from annotoolbox.formats import phoenix, phoenixedit, phoenixread
from annotoolbox.sources import gametree
from annotoolbox.suite import buildout
from annotoolbox.suite.project import CProject
from annotoolbox.uiedit import (
    animations,
    bindings,
    elements,
    fields,
    layout,
    library,
    operators,
    projectpart,
    uidoc,
    uifiles,
    viewmodels,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_fields_decode_what_they_encode():
    for code, value in ((fields.BOOL, True), (fields.I32, -5), (fields.U32, 7), (fields.FLOAT, 1.5),
                        (fields.VEC3, (1.0, 2.0, 3.0)), (fields.STRING, "abc"), (fields.U32_LIST, [1, 0x80000004])):  # fmt: skip
        assert fields.decode(code, fields.encode(code, value)) == value
    assert fields.decode_boxed(phoenix.boxed(fields.FLOAT, 2.0)) == (fields.FLOAT, 2.0)


def test_every_builtin_the_codec_names_is_in_the_table():
    ids = {**phoenixedit.BUILTIN, **phoenixedit.BUILTIN_VIDEO, **phoenixedit.BUILTIN_ANIMATION}
    for name, uid in ids.items():
        if name != "ImageId":
            assert fields.BUILTIN_BY_ID[uid].name in (name, "Start", "Stop", "Pause", "Resume", "Started", "Stopped",
                                                      "Paused", "Resumed", "Completed")  # fmt: skip
    assert {b.name for b in fields.builtins_of("label")} >= {"Text", "Position", "ReferenceSize"}


def test_every_enum_the_editor_names_is_a_field_label():
    labels = {fields.label(f) for n in ("0c454dec", "41a404e5", "1294a820", "7e4fae85") for f in _fields_of_type(n)}
    history = {
        "af01aeda": phoenix.full_schema(SCHEMA)["af01aeda"]["guid"],
        "0c454dec": "9f985b52-7e48-9a2d-a33f-4d5c81841095",
    }  # 1404, 1701
    labels |= {fields.label(f) for f in fields.fields_of(phoenixedit.new_object(SCHEMA, "0c454dec", versions=history))}
    for key in fields.ENUMS:
        assert key in labels, key  # a key the schema renamed would leave the editor's values unnamed
    assert fields.enum_names("XRepositioning")[2] == "FixedToParent"


def _fields_of_type(type_hash: str) -> list:
    return fields.fields_of(phoenixedit.new_object(SCHEMA, type_hash, versions=VERSIONS))


def test_operators_are_schema_types_and_compute():
    for h in operators.OPERATORS:
        assert h in FULL, h
    run = operators.evaluate
    assert run("97f3862c", [1, 2, 3]) == [6]
    assert run("d5037298", [(1.0, 2.0), 3]) == [(3.0, 6.0)]
    assert run("0b2ef3a9", ["a", False]) is None
    assert run("76af01ea", [5, 0, 3]) == [3]
    assert run("19296de7", [7.0]) == ["7.0"] and run("19296de7", [2.123456]) == ["2.123456"]
    assert run("9b18e043", [-2.5]) == [-3]
    assert run("b8770396", [(255, 128, 0), (128, 128, 128)]) == [(128, 64, 0)]
    assert run("fa72bb0a", [1, 2, False]) == [2]


def test_bindings_read_the_one_index_space_in_order():
    d = phoenixread.load(widget_file(), SCHEMA)
    g = bindings.read(d.m_root)
    (b,) = g.bindings
    assert b.op_name == "Assign"
    assert [e.kind for e in b.sources] == ["own"] and [e.kind for e in b.targets] == ["path"]
    assert b.targets[0].ids == [phoenixedit.BUILTIN["Text"]] and b.targets[0].instance is not None


def test_binding_edits_keep_the_indices_and_undo(ui_tree):
    files = uifiles.CUiFiles(ui_tree)
    doc = uidoc.CUiDoc(files, WIDGET, files.read(WIDGET))
    assert doc.m_exact and doc.to_bytes() == doc.m_original
    label = next(e for e in elements.tree(doc.m_root).walk() if e.kind == "label")
    const = bindings.SEndSpec("constant", value=(fields.FLOAT, 0.5), ptype=3)
    alpha = bindings.SEndSpec("path", label.guid, (fields.BUILTIN_BY_ID[0x80000006].uid,))
    doc.add_binding([const], [alpha], operators.ASSIGN)
    own = bindings.SEndSpec("own", name="Title")
    doc.add_binding([own], [bindings.SEndSpec("path", label.guid, (0x80000007,))], "0f8445b8")
    g = bindings.read(phoenixread.load(doc.to_bytes(), SCHEMA).m_root)
    assert [(b.op_name, [e.kind for e in b.sources]) for b in g.bindings] == [
        ("Assign", ["own"]), ("Assign", ["constant"]), ("NumberToBool", ["own"])]  # fmt: skip
    assert g.bindings[1].sources[0].value == (fields.FLOAT, 0.5)
    assert g.bindings[1].targets[0].ids == [0x80000006]
    doc.remove_binding(bindings.read(doc.m_root).bindings[1].node)  # the document's own tree, not the re-read one
    g = bindings.read(phoenixread.load(doc.to_bytes(), SCHEMA).m_root)
    assert [b.op_name for b in g.bindings] == ["Assign", "NumberToBool"]
    assert all(e.kind != "constant" for b in g.bindings for e in b.sources)
    while doc.m_undo.undo() is not None:
        pass
    assert doc.to_bytes() == doc.m_original
    doc.m_undo.redo()
    assert len(bindings.read(doc.m_root).bindings) == 2


def test_document_edits_change_bytes_and_undo_restores(ui_tree):
    files = uifiles.CUiFiles(ui_tree)
    doc = uidoc.CUiDoc(files, WIDGET, files.read(WIDGET))
    image = next(e for e in elements.tree(doc.m_root).walk() if e.kind == "image")
    doc.move(image.node, (50.0, 60.0, 0.0))
    changed = doc.to_bytes()
    assert changed != doc.m_original
    assert fields.get(phoenixread.load(changed, SCHEMA).m_root["fields"][1]["objs"][0], "Position") == (50.0, 60.0, 0.0)
    copy = doc.duplicate(image)
    assert phoenixedit.object_name(copy) == "Picture_copy" and copy["guid"] != image.node["guid"]
    top = elements.tree(doc.m_root)
    assert doc.remove(next(e for e in top.walk() if e.kind == "label")) == 1  # its binding went with it
    phoenixread.load(doc.to_bytes(), SCHEMA)
    doc.m_undo.undo()
    doc.m_undo.undo()
    doc.m_undo.undo()
    assert doc.to_bytes() == doc.m_original


def test_new_instances_components_and_properties(ui_tree):
    files = uifiles.CUiFiles(ui_tree)
    doc = uidoc.CUiDoc(files, WIDGET, files.read(WIDGET))
    top = elements.tree(doc.m_root)
    node = doc.new_instance("label", "Second")
    doc.add_instance(top, node, "Second")
    doc.add_component(node, "c7b1e237-0000-0000-0000-000000000000", "Pivot")
    doc.set_property_value(node, [int(fields.get(phoenixedit.objects(node, "Components")[0], "UniqueId")), layout.PPP_VALUE],
                           fields.VEC3, (0.5, 0.5, 0.0))  # fmt: skip
    uid = doc.add_property("Count", 3, fields.FLOAT, 2.0)
    root = phoenixread.load(doc.to_bytes(), SCHEMA).m_root
    names = [e.name for e in elements.tree(root).walk()]
    assert "Second" in names
    props = {
        bindings.property_info(p)["name"]: bindings.property_info(p) for p in phoenixedit.objects(root, "18b0dff0")
    }
    assert props["Count"]["uid"] == uid and props["Count"]["default"] == (fields.FLOAT, 2.0)


def test_layout_places_by_anchor_size_and_binding(ui_tree):
    files = uifiles.CUiFiles(ui_tree)
    frame = layout.place(WIDGET, files.root(WIDGET), layout.CContext(files))
    items = {i.kind: i for i in frame.items}
    assert items["image"].size == (64.0, 32.0) and layout.apply(items["image"].matrix, 0, 0) == (10.0, 20.0)
    assert items["text"].text == "Hello"  # the own property's default, through the binding
    x, y = layout.apply(items["text"].matrix, 0, 0)
    assert (x, y) == (1920.0 - 100.0, 1080.0 - 40.0)  # anchored bottom right
    scene = layout.place("testscene", files.root("testscene"), layout.CContext(files))
    image = next(i for i in scene.items if i.kind == "image")
    assert layout.apply(image.matrix, 0, 0) == (110.0, 120.0)  # the container's place, then the widget's
    assert len(scene.boxes) == 1  # the scene's own element; the template's are inside it


def test_texts_samples_and_view_models(ui_tree, tmp_path):
    files = uifiles.CUiFiles(ui_tree)
    assert files.m_entries["testscene"].kind == "SC" and files.m_entries[WIDGET].kind == "WI"
    assert files.text_of("A010 D068 1398 4305") == "Tom & Jerry"
    stub = ("---@class rdgs.CTestSceneData\n---@field Title rdgs.CTextData\n---@field Count number\n"
            "--- Gets the child game object Title\n--- Gets the value of Count\n--- Sets the value of Count\n")  # fmt: skip
    c = viewmodels.parse_stub(stub)
    assert [(f.name, f.kind, f.settable) for f in c.fields] == [("Title", "child", False), ("Count", "value", True)]
    models = viewmodels.CViewModels([c])
    assert models.class_for_scene("testscene") is c
    samples = viewmodels.CSamples(tmp_path / "s.json")
    samples.set("a/10000001", 3)
    samples.seed("a/10000001", 9)
    samples.save()
    assert viewmodels.CSamples(tmp_path / "s.json").get("a/10000001") == 3
    names = viewmodels.CFieldNames(tmp_path / "n.json")
    names.set(0x2B2357A9, "CTextData.Text")
    names.save()
    assert viewmodels.CFieldNames(tmp_path / "n.json").short() == {0x2B2357A9: "Text"}
    script = tmp_path / "script-api.json"
    script.write_text(json.dumps({"tables": [{"table": "T", "functions": [{"name": "Value", "kind": "readout",
                                                                        "args": ["i"], "returns": ["number"]}]}]}))  # fmt: skip
    assert [r.call for r in viewmodels.CScriptList.read(script).readouts()] == ["T.Value(i)"]
    for wrong in ("[]", '{"tables": ["T"]}'):  # a JSON file that is no script list: the ValueError the window shows
        script.write_text(wrong)
        with pytest.raises(ValueError):
            viewmodels.CScriptList.read(script)


def test_library_names_components_and_paths(ui_tree):
    files = uifiles.CUiFiles(ui_tree)
    lib = library.CLibrary.read(ui_tree)
    (item,) = lib.of_kind("Component")
    assert item.name == "ProportionalPivotPoint" and item.description == "The pivot as a fraction."
    namer = library.CNamer(lib, files.root)
    node = phoenixedit.new_object(SCHEMA, "c19f99dd", versions=VERSIONS)
    comp = phoenixedit.component(SCHEMA, item.guid, "Pivot", 0x70000001, VERSIONS)
    phoenixedit.set_objects(node, "Components", [comp])
    assert namer.path([0x70000001, layout.PPP_VALUE], node) == "Pivot.ProportionalPivotPoint"
    assert namer.path([0x80000004]) == "Size"


def test_animation_tracks_interpolate():
    root = phoenixedit.new_object(SCHEMA, phoenixedit.WIDGET_TEMPLATE, root=True, guid=bytes(16), versions=VERSIONS)
    anim = phoenixedit.new_object(SCHEMA, "b03e911f", guid=bytes(range(16)), versions=VERSIONS)
    phoenixedit.set_field(anim, "FrameRate", 60)
    phoenixedit.set_field(anim, "Duration", 30)
    track = phoenixedit.new_object(SCHEMA, "e613cfd0", versions=VERSIONS)
    phoenixedit.set_field(track, "AnimatedPropertyIndex", 2)
    keys = []
    for t, v in ((0.0, 0.0), (0.5, 1.0)):
        k = phoenixedit.new_object(SCHEMA, "32f7d270", versions=VERSIONS)
        phoenixedit.set_field(k, "Time", t)
        phoenixedit.set_field(k, "Value", v)
        phoenixedit.set_field(k, animations.INTERPOLATOR, 1)  # linear
        keys.append(k)
    phoenixedit.set_objects(track, "Track", keys)
    phoenixedit.set_objects(anim, "Tracks", [track])
    phoenixedit.set_objects(root, "AnimationInstances", [anim])
    (a,) = animations.read(root)
    assert a.seconds == 0.5 and a.values_at(0.25) == [(2, 0.5)] and a.values_at(9.0) == [(2, 1.0)]


def test_the_project_keeps_and_builds_the_files(ui_tree, tmp_path):
    project = CProject(tmp_path / "proj", mod_id="ui-test")
    assert not projectpart.PART.has_work(project)
    path = f"{GEN}{WIDGET}"
    projectpart.save_file(project, "anno117", path, widget_file())
    assert projectpart.PART.has_work(project)
    assert buildout.read_all(projectpart.PART.render(project, None, lambda _m: None)) == {path: widget_file()}
    missing = projectpart.check(project, "anno117", lambda _p: False)
    assert any(IMAGE in m for m in missing)  # the picture is neither in the project nor in the game
    assert projectpart.check(project, "anno117", ui_tree.exists) == []


def test_real_scenes_place_and_widget_files_keep_their_bytes(tmp_path):
    folder = os.environ.get("ANNO117_DIR")
    if not folder or not Path(folder).is_dir():
        pytest.skip("needs ANNO117_DIR")
    files = uifiles.CUiFiles(gametree.open_game("anno117", Path(folder), tmp_path), tmp_path)
    for key in ("maintitlescene", "loadingscreenscene", "hudgovernorsbarscene"):
        frame = layout.place(key, files.root(key), layout.CContext(files))
        assert len(frame.items) > 100 and not frame.notes
    for entry in files.of_kind("WI")[:150]:
        doc = uidoc.CUiDoc(files, entry.key, files.read(entry.key))
        assert doc.m_exact and doc.to_bytes() == doc.m_original


@pytest.fixture
def window(ui_tree, tmp_path):
    """The UI editor's window offscreen on the synthetic game; closed without a question afterwards."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.settings import CSettings
    from annotoolbox.uiedit.uiwindow import CUiWindow, _CGame

    qt = QApplication.instance() or QApplication([])
    w = CUiWindow(CSettings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    w.m_sync_layout = True  # the layout runs on this thread: the frame is there at once
    w._opened("anno117", "", _CGame("anno117", ui_tree, None))
    yield w
    for doc in w.m_docs.values():
        doc.m_undo.mark_clean()
    w.close()
    qt.processEvents()


def _move_picture(window) -> None:
    """Open the widget file and move its picture: the file has an edit the project does not."""
    window.open_file(WIDGET)
    picture = next(e for e in window.m_top.walk() if e.kind == "image")
    window.preview.moved.emit(id(picture.node), 5.0, 5.0)
    assert window.doc.dirty


def test_the_window_opens_a_file_edits_and_keeps_it(window):
    from annotoolbox.uiedit import canvas

    settings = window.settings
    window.open_file("testscene")
    assert window.m_frame is not None and any(i.kind == "image" for i in window.m_frame.items)
    window.open_file(WIDGET)
    picture = next(e for e in window.m_top.walk() if e.kind == "image")
    window.preview.selected.emit(id(picture.node))  # through the signal: a 64-bit id
    window.preview.moved.emit(id(picture.node), 5.0, 5.0)
    assert fields.get(picture.node, "Position") == (15.0, 25.0, 0.0) and window.doc.dirty
    window.save()
    saved = Path(settings.project) / "ui-edits" / "anno117" / GEN / WIDGET
    assert saved.read_bytes() == window.doc.to_bytes() and not window.doc.dirty
    img = canvas.render(window.m_frame, window.m_pictures, 0.25)
    assert (img.width(), img.height()) == (480, 270)


def test_another_game_asks_about_unkept_edits_and_shows_nothing_of_the_game_before(window, ui_tree, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.uiedit import uiwindow

    opened: list[str] = []
    monkeypatch.setattr(uiwindow.gameopen, "open_installed", lambda _w, _s, key, *_a, **_k: opened.append(key))
    answers = [QMessageBox.StandardButton.Cancel]
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: answers[0])
    _move_picture(window)
    window.open_game("anno1800")
    assert opened == [] and window.doc.dirty  # cancelled: the game and its edits stay
    answers[0] = QMessageBox.StandardButton.No
    window.open_game("anno1800")
    assert opened == ["anno1800"]
    window._opened("anno117", "", uiwindow._CGame("anno117", ui_tree, None))
    assert window.doc is None and window.m_top is None and window.m_selected is None and window.m_frame is None
    assert window.element_tree.topLevelItemCount() == 0 and window.preview.m_frame is None
    assert window.windowTitle() == "UI editor"


def test_a_file_that_cannot_be_kept_is_said_and_keeps_the_window_open(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from annotoolbox.uiedit import projectpart

    def refuse(*_a):
        raise OSError("the disk is full")

    shown: list[str] = []
    monkeypatch.setattr(projectpart, "save_file", refuse)
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: shown.append(text))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    _move_picture(window)
    assert window.save() is False and "the disk is full" in shown[0] and window.doc.dirty
    window.show()
    assert not window.close() and window.isVisible()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Fresh", True))
    window.new_widget_file()  # the same refusal for a new file: said, not raised
    assert len(shown) == 3 and window.doc.m_key == WIDGET


def test_keeping_every_file_on_leaving_leaves_the_shown_file_shown(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.uiedit import projectpart

    window.open_file("testscene")
    window.doc.add_property("Extra", 3, fields.FLOAT, 0.0)
    assert window.doc.dirty
    _move_picture(window)  # the widget file is the one shown; both files are unkept
    shown = window.doc
    assert shown.m_key == WIDGET and len(window.m_docs) == 2 and all(d.dirty for d in window.m_docs.values())

    def refuse(*_a):
        raise OSError("the disk is full")

    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: None)
    monkeypatch.setattr(projectpart, "save_file", refuse)
    assert window.may_leave() is False
    assert window.doc is shown  # the files were kept through the open file; the panels still show this one


def test_closing_drops_a_scene_still_being_drawn(window, monkeypatch):
    class FakeStillScene:
        cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    still = window.m_world_menu.m_still = FakeStillScene()
    window.close()
    assert still.cancelled and window.m_world_menu.m_still is None


def test_a_number_its_field_does_not_hold_is_said_and_leaves_the_file(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.uiedit import panels, timeline

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: shown.append(text))
    window.open_file(WIDGET)
    picture = next(e for e in window.m_top.walk() if e.kind == "image")
    before = window.doc.to_bytes()
    window.props.edit_value.emit(
        picture.node, [0x10000001], fields.I32, 2**40
    )  # no 32 bits: it raised out of the signal
    window.props.edit_field.emit(picture.node, "RotationAngle", 1e40)  # nor a float of 32
    assert len(shown) == 2 and window.doc.to_bytes() == before and not window.doc.dirty
    for text in ("inf", "-inf", "nan"):  # typed where a whole number goes: refused as any other word
        with pytest.raises(ValueError):
            panels.parse(text, fields.U32)
        with pytest.raises(ValueError):
            timeline.parse_key_value(timeline.COLOR_TRACK, f"1 {text} 3")
    assert panels.parse("1e3", fields.I32) == 1000 and panels.parse("0x10", fields.U32) == 16


def test_a_sample_or_name_file_that_does_not_read_is_said_and_not_written_over(window, ui_tree, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.uiedit import projectpart
    from annotoolbox.uiedit.uiwindow import _CGame

    samples, names = (projectpart.sidecar(window.project(), "anno117", n) for n in ("samples", "names"))
    samples.parent.mkdir(parents=True, exist_ok=True)
    samples.write_text('{"format": 1, "values": {"a/1000', encoding="utf-8")  # cut short
    names.write_text('{"names": {"not hex": "Text"}}', encoding="utf-8")
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: shown.append(text))
    window._opened("anno117", "", _CGame("anno117", ui_tree, None))  # it raised here: the game was left half open
    assert len(shown) == 1 and "anno117-samples.json" in shown[0] and "anno117-names.json" in shown[0]
    assert (
        window.file_list.topLevelItemCount() == 3 and window.m_samples.m_values == {} and window.m_names.m_names == {}
    )
    window.m_samples.set("a/10000001", 3)
    window.m_samples.save()
    window.m_names.save()
    assert samples.read_text(encoding="utf-8").endswith("a/1000") and "not hex" in names.read_text(encoding="utf-8")
    for odd in ("[1]", '{"values": [1]}', '{"values": {}, "generated": 5}'):
        samples.write_text(odd, encoding="utf-8")
        assert viewmodels.CSamples(samples).m_error and viewmodels.CSamples(samples).m_path is None


def test_a_folder_of_loose_files_is_read_off_the_windows_thread(window, tmp_path, monkeypatch):
    runs: list[tuple] = []
    monkeypatch.setattr(window.m_background, "run", lambda work, done, is_waiting=False: runs.append((work, done)))
    game = window.game
    window.open_folder(tmp_path / "loose")
    assert len(runs) == 1 and window.game is game  # nothing read yet
    runs[0][1](OSError("no such folder"))
    assert "no such folder" in window.statusBar().currentMessage() and window.game is game


def test_the_states_that_show_a_widget_file_come_through_the_files_that_place_it():
    from annotoolbox.uiedit.uifiles import CUiFiles, SEntry

    files = object.__new__(CUiFiles)  # no tree: only the index and the users matter
    scene = "data/ui/studio/generated/maintitlescene"
    files.m_entries = {
        "maintitlescene": SEntry(scene, "maintitlescene", "SC", 1),
        "mid": SEntry("x/mid", "mid", "WI", 1),
    }
    files.m_users = {"leaf": [("mid", "a")], "mid": [("maintitlescene", "b"), ("mid", "loop")]}
    assert files.scene_states(["data/ui/studio/generated/LEAF"]) == ["MainTitle"]
    assert files.scene_states(["data/ui/studio/generated/unknown"]) == []
