"""The object builder on a synthetic game of loose files: an object opened with its info and feedback files (117's
``shared/`` copy first), edits drawn by the viewer's scene and undone, the ways to save and what each writes into
the project (the live link told), the shapes over the view, and the window offscreen."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.fakegame import FakeLink
from tests.objbuild.objbuildfakes import HOUSE_CFG as HOUSE
from tests.objbuild.objbuildfakes import OBJECT_CFG as CFG
from tests.objbuild.objbuildfakes import SHARED_HOUSE_CFG as SHARED
from tests.objbuild.objbuildfakes import objbuild_game as game  # noqa: F401

from annotoolbox.models import cfgdoc, cfgparts
from annotoolbox.objbuild import objdoc, projectpart, shapes, writeback
from annotoolbox.objbuild.objdoc import CObjectDoc
from annotoolbox.sources import gametree
from annotoolbox.suite import buildout, livelink
from annotoolbox.suite.project import CProject
from annotoolbox.viewer.modelscene import SOptions


def _position(obj: CObjectDoc) -> cfgdoc.SNode:
    return next(n for n in obj.document("cfg").root.iter() if n.name == "Position.y")


def test_an_object_opens_with_its_files_and_follows_edits(game):  # noqa: F811
    assert objdoc.object_paths("data/base/house/house.ifo")["cfg"] == HOUSE
    obj = CObjectDoc.open(game, "data/base/house/house.fc", "anno117")
    assert set(obj.m_opened) == {"cfg", "ifo", "fc"} and obj.main.read == SHARED and "baked copy" in obj.title
    scene = obj.scene(SOptions())
    assert scene.m_config == SHARED and len(scene.m_parts) == 1 and scene.m_parts[0].matrix[1, 3] == 0
    seen = []
    obj.m_listeners.append(seen.append)
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "2.5"))
    assert seen == [{"cfg"}] and obj.dirty() == ["cfg"] and obj.m_undo.dirty
    assert obj.scene(SOptions()).m_parts[0].matrix[1, 3] == pytest.approx(2.5)  # the view reads the edited bytes
    obj.m_undo.undo()
    assert obj.dirty() == [] and obj.scene(SOptions()).m_parts[0].matrix[1, 3] == 0
    plain = CObjectDoc.open(game, HOUSE, "anno117", is_shared=False)
    assert plain.main.read == HOUSE and plain.scene(SOptions()).m_config == HOUSE


def test_the_ways_to_save_write_what_they_say(game):  # noqa: F811
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "1"))
    patch = writeback.plan(obj, writeback.PATCH)
    assert list(patch.files) == [SHARED + ".xml"] and b"Position.y" in patch.files[SHARED + ".xml"]
    whole = writeback.plan(obj, writeback.REPLACE)
    assert list(whole.files) == [SHARED] and whole.files[SHARED] == obj.document("cfg").to_bytes()
    new = writeback.plan(obj, writeback.NEW, "data/mine/house2.cfg")
    assert sorted(new.files) == ["data/mine/house2.cfg", "data/mine/house2.fc", "data/mine/house2.ifo"]
    assert writeback.plan(obj, writeback.NEW, HOUSE).problems  # a new file needs a path of its own
    hidden = CObjectDoc.open(game, HOUSE, "anno117", is_shared=False)
    hidden.apply("cfg", cfgdoc.set_value(_position(hidden), "1"))
    refused = writeback.plan(hidden, writeback.REPLACE)
    assert not refused.files and "shared/" in refused.problems[0]
    old = CObjectDoc.open(game, "data/graphics/old/old.cfg", "anno2070")
    assert [w.way for w in writeback.ways(old) if w.allowed] == ["new", "replace"]


def test_a_save_lands_in_the_project_and_tells_the_game(game, tmp_path):  # noqa: F811
    link = livelink.use(FakeLink())
    try:
        project = CProject(tmp_path / "proj", mod_id="my-mod")
        notice = projectpart.EDITS.save_announced(project, "anno117", {SHARED + ".xml": b"<ModOps />", SHARED: b"x"})
        assert projectpart.EDITS.saved_files(project)[SHARED].read_bytes() == b"x"
        assert buildout.read_all(projectpart.PART.render(project, None, lambda _m: None)) == {
            SHARED: b"x",
            SHARED + ".xml": b"<ModOps />",
        }
        assert projectpart.PART.has_work(project) and notice.sent and "files.changed" in link.names
    finally:
        livelink.reset()


def test_a_second_save_in_another_way_replaces_the_first_instead_of_adding_to_it(game, tmp_path):  # noqa: F811
    project = CProject(tmp_path / "proj", mod_id="my-mod")
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "1"))
    livelink.use(FakeLink())
    try:
        patch = writeback.plan(obj, writeback.PATCH, saved=[])
        projectpart.EDITS.save_announced(project, "anno117", patch.files, patch.stale)
        saved = projectpart.EDITS.saved_files(project)
        assert list(saved) == [SHARED + ".xml"] and writeback.held(obj, saved) == [SHARED + ".xml"]
        whole = writeback.plan(obj, writeback.REPLACE, saved=saved)
        assert whole.stale == [SHARED + ".xml"] and any("removed" in n for n in whole.notes)
        projectpart.EDITS.save_announced(project, "anno117", whole.files, whole.stale)
        assert list(projectpart.EDITS.saved_files(project)) == [SHARED]  # the mod no longer carries both
        again = writeback.plan(obj, writeback.PATCH, saved=projectpart.EDITS.saved_files(project))
        assert again.stale == [SHARED]
        same = writeback.plan(obj, writeback.REPLACE, saved=projectpart.EDITS.saved_files(project))
        assert not same.stale and any("replaces what the project holds" in n for n in same.notes)
    finally:
        livelink.reset()


def _held(project):
    saved = projectpart.EDITS.saved_files(project, "anno117")
    return lambda p: saved[p].read_bytes() if p in saved else None


def _texture(obj: CObjectDoc) -> cfgdoc.SNode:
    return next(n for n in obj.document("cfg").root.iter() if n.name == "cModelDiffTex")


@pytest.mark.parametrize("way", [writeback.PATCH, writeback.REPLACE])
def test_a_second_session_keeps_the_edits_of_the_first(game, tmp_path, way):  # noqa: F811
    livelink.use(FakeLink())
    try:
        project = CProject(tmp_path / "proj", mod_id="my-mod")
        first = CObjectDoc.open(game, HOUSE, "anno117")
        first.apply("cfg", cfgdoc.set_value(_position(first), "1"))
        saved = writeback.plan(first, way)
        projectpart.EDITS.save_announced(project, "anno117", saved.files, saved.stale)

        second = CObjectDoc.open(game, HOUSE, "anno117", held=_held(project))
        assert second.m_restored == {"cfg": "patch" if way == writeback.PATCH else "whole"} and not second.m_notes
        assert second.document("cfg").to_bytes() == first.document("cfg").to_bytes() and second.dirty() == ["cfg"]
        assert not second.m_undo.dirty and second.scene(SOptions()).m_parts[0].matrix[1, 3] == pytest.approx(1.0)
        second.apply("cfg", cfgdoc.set_value(_texture(second), "data/base/house/roof.psd"))
        again = writeback.plan(second, way, saved=projectpart.EDITS.saved_files(project, "anno117"))
        projectpart.EDITS.save_announced(project, "anno117", again.files, again.stale)

        third = CObjectDoc.open(game, HOUSE, "anno117", held=_held(project))
        text = third.document("cfg").to_bytes()
        assert b"roof.psd" in text and b"<Position.y>1</Position.y>" in text  # both sessions' edits
        assert third.base_bytes("cfg") == game.read(SHARED)  # a patch is still made against the game's file
    finally:
        livelink.reset()


def test_what_the_project_holds_and_cannot_be_read_is_said_not_applied(game):  # noqa: F811
    held = {SHARED: b"not a config", HOUSE + ".xml": b"<ModOps/>"}
    obj = CObjectDoc.open(game, HOUSE, "anno117", held=lambda p: held.get(p))
    assert obj.m_restored == {} and "does not open" in obj.m_notes[0] and not obj.dirty()
    stale = {SHARED + ".xml": b"<ModOps><ModOp Type='remove' Path='/Config/Nothing'/></ModOps>"}
    obj = CObjectDoc.open(game, HOUSE, "anno117", held=lambda p: stale.get(p))
    assert (
        obj.m_restored == {"cfg": "patch"} and obj.m_notes and not obj.dirty()
    )  # the loader's warning, nothing changed


def test_a_changed_list_of_parts_saves_the_baked_data_file_that_follows_it(game, tmp_path):  # noqa: F811
    from annotoolbox.models import cfgbfg

    bfg = SHARED[: -len(".cfg")] + ".bfg"
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "1"))
    assert bfg not in writeback.plan(obj, writeback.REPLACE).files  # a value changed: the game's stays right
    model = obj.document("cfg").find("Models/0")
    obj.apply("cfg", cfgdoc.duplicate(model.parent, model.node))
    for way in (writeback.REPLACE, writeback.PATCH):
        found = writeback.plan(obj, way)
        assert bfg in found.files and any("baked data follows" in n for n in found.notes)
        assert cfgbfg.mismatches(obj.document("cfg"), found.files[bfg]) == []  # it lines up with the edited config
    assert bfg not in writeback.plan(obj, writeback.NEW, "data/mine/house2.cfg").files  # a new path has no shared/ copy
    project = CProject(tmp_path / "proj", mod_id="my-mod")
    livelink.use(FakeLink())
    try:
        whole = writeback.plan(obj, writeback.REPLACE)
        projectpart.EDITS.save_announced(project, "anno117", whole.files, whole.stale)
        saved = projectpart.EDITS.saved_files(project)
        assert writeback.held(obj, saved) == sorted([SHARED, bfg])
        obj.m_undo.undo()  # the duplicate goes: the game's baked data is right again, the project's copy is stale
        again = writeback.plan(obj, writeback.PATCH, saved=saved)
        assert bfg not in again.files and bfg in again.stale and SHARED in again.stale
    finally:
        livelink.reset()
    old = CObjectDoc.open(game, "data/graphics/old/old.cfg", "anno2070")
    assert not [p for p in writeback.plan(old, writeback.REPLACE).files if p.endswith(".bfg")]


def test_a_new_file_cannot_be_named_outside_the_game_folders(game):  # noqa: F811
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "1"))
    for path in ("../../x.cfg", "c:/x.cfg"):
        found = writeback.plan(obj, writeback.NEW, path)
        assert found.problems and not found.files


def test_a_failed_write_leaves_no_temp_file_in_the_mod(tmp_path, monkeypatch):
    from annotoolbox.infra import buildcache

    project = CProject(tmp_path / "proj", mod_id="my-mod")
    projectpart.EDITS.save_files(project, "anno117", {"data/a.cfg": b"1"})

    def refuse(src, dest):
        raise PermissionError("the game holds it")

    monkeypatch.setattr(buildcache, "_replace", refuse)
    with pytest.raises(PermissionError):
        projectpart.EDITS.save_files(project, "anno117", {"data/a.cfg": b"2"})
    left = [p.name for p in projectpart.EDITS.folder(project).rglob("*") if p.is_file()]
    assert left == ["a.cfg"]
    # a half-written file a crash left is no game file either
    (projectpart.EDITS.folder(project) / "data" / "tmpab12.part").write_bytes(b"x")
    assert list(projectpart.EDITS.saved_files(project)) == ["data/a.cfg"]


@pytest.mark.parametrize("path", ["../x.cfg", "data/../../x.cfg", "c:/x.cfg", "data//x.cfg", ""])
def test_a_path_out_of_the_game_folder_is_not_saved(tmp_path, path):
    project = CProject(tmp_path / "proj", mod_id="my-mod")
    with pytest.raises(ValueError):
        projectpart.EDITS.save_files(project, "anno117", {path: b"x"})
    assert not (tmp_path / "x.cfg").exists()


def test_the_info_and_feedback_files_become_lines_over_the_view(game):  # noqa: F811
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    found = {s.kind: s for s in shapes.info_shapes(obj.document("ifo"))}
    assert found["box"].path == "BoundingBox#0" and len(found["box"].points) == 8
    assert max(p[1] for p in found["box"].points) == pytest.approx(2.0)
    assert found["area"].points[1] == (2.0, 0.0, -2.0) and len(found["area"].segments) == 3
    dummy = shapes.feedback_shapes(obj.document("fc"))[0]
    assert dummy.name == "door" and dummy.points[0][0] == pytest.approx(1 - shapes.CROSS)
    fixed = cfgdoc.SDoc.read(
        b"<Info><Dummy><Position><x>4096</x><y>0</y><z>8192</z></Position></Dummy></Info>", "a.ifo"
    )
    assert shapes.info_shapes(fixed)[0].points[0][0] == pytest.approx(1 - shapes.CROSS)


def window_on(tree, tmp_path, path: str = ""):
    """The object builder's window on a game's files, ``path`` opened in it."""
    from annotoolbox.objbuild.objwindow import CObjectWindow
    from annotoolbox.suite.settings import CSettings

    window = CObjectWindow(CSettings(workspace=str(tmp_path / "ws")), None)
    if tree is not None:
        window.set_tree(tree)
    if path:
        assert window.open_path(path)
    return window


def test_the_window_edits_an_object(qt, game, tmp_path):  # noqa: F811
    window = window_on(game, tmp_path)
    assert window.open_path(HOUSE) and window.m_scene.m_config == SHARED
    titles = [window.m_objects.topLevelItem(k).text(0) for k in range(window.m_objects.topLevelItemCount())]
    assert titles[0].startswith("Config") and len(titles) == 3
    window.m_objects.select("cfg", "Models/0/Materials/0")
    rows = {window.m_props.topLevelItem(k).text(0) for k in range(window.m_props.topLevelItemCount())}
    assert "cModelDiffTex" in rows
    window.m_props.edited.emit(
        window.m_object_doc.document("cfg").find("Models/0/Materials/0").node.children[1], "data/x.psd"
    )
    assert b"data/x.psd" in window.m_object_doc.document("cfg").to_bytes() and window.windowTitle().startswith(
        "house.cfg"
    )
    window.undo()
    assert not window.m_object_doc.dirty()
    window.m_objects.select("cfg", "Models/0")
    window.duplicate_part()
    assert len(window.m_object_doc.document("cfg").find("Models").children) == 2
    window._redraw()
    assert len(window.m_scene.m_parts) == 2
    window.remove_part()
    assert len(window.m_object_doc.document("cfg").find("Models").children) == 1
    window.m_objects.select("cfg", "", "empty", "Lights")
    window.add_part()
    assert window.m_object_doc.document("cfg").find("Lights/0").kind == "LIGHT"
    bar = window.m_sequences.m_view.m_bars[0]
    window.m_sequences.m_view.moved.emit(bar, 500, 2500)
    assert (
        window.m_sequences.m_view.m_bars[0].start == 500
        and b"<EndTime>2500</EndTime>" in window.m_object_doc.document("cfg").to_bytes()
    )
    window.m_object_doc.m_undo.mark_clean()
    window.close()


def test_the_window_opens_an_installed_game_in_the_background(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from annotoolbox.objbuild.objwindow import CObjectWindow
    from annotoolbox.suite.settings import CSettings

    seen = []
    monkeypatch.setattr(gametree, "open_game", lambda key, folder, index: seen.append((key, index)) or game)
    settings = CSettings(anno117=str(tmp_path / "117"), workspace=str(tmp_path / "ws"))
    window = CObjectWindow(settings, None)
    window.open_game("anno117", HOUSE, is_waiting=True)
    assert (
        window.m_tree is game
        and window.m_object_doc is not None
        and seen == [("anno117", settings.index_dir("anno117"))]
    )
    window.m_object_doc.m_undo.mark_clean()
    window.close()


@pytest.mark.parametrize(
    ("kind", "value", "refused"),
    [("number", "nan", True), ("number", "inf", True), ("number", "2.5", False), ("file", "a<b>c", True),
     ("file", "x CDATA[ y", True), ("file", "a & b", True), ("file", "a &amp; b", False),
     ("file", "a&#38;b", False), ("binary", "0a ff", False)],
)  # fmt: skip
def test_a_typed_value_cannot_break_the_config_or_hold_nan(kind, value, refused):
    from annotoolbox.objbuild.objprops import problem

    assert bool(problem(kind, value)) is refused


def test_the_view_switches_turn_on_again(qt, game, tmp_path):  # noqa: F811
    window = window_on(None, tmp_path)
    shadows = window.m_toggles["shadows"]
    shadows.trigger()
    assert window.m_view.m_state.shadows is False
    shadows.trigger()
    assert window.m_view.m_state.shadows is True  # the lambda used to get on=False every time
    window.m_toggles["cloth"].trigger()
    window.m_toggles["torn"].trigger()
    assert window.m_view.m_state.cloth is True and window.m_view.m_state.torn is True
    window.m_toggles["figures"].trigger()  # no object open: the figures stay none
    assert window.m_figures.m_shown is True and window.m_figures.m_figures is None
    window.close()


def test_a_new_track_copied_from_the_file_keeps_one_element():
    from annotoolbox.objbuild import timeline

    element = CFG[CFG.index("<TrackElement>") : CFG.index("</TrackElement>") + len("</TrackElement>") + 2]
    text = CFG.format(mesh="x.fbx").replace(element, element + element)
    track = cfgparts.first_of(cfgdoc.SDoc.read(text.encode(), "a.cfg"), "Track")
    assert len([c for c in track.children if c.name == "TrackElement"]) == 2
    timeline.one_element(track)
    assert len([c for c in track.children if c.name == "TrackElement"]) == 1


def test_new_parts_offer_the_kinds_of_their_list():
    assert cfgparts.kinds_for("m_Transformer")[0] == "ORIENTATION_TRANSFORM" and cfgparts.title("Clothes") == "Cloths"


MATERIAL = (
    b"<Config><ConfigType>MATERIAL</ConfigType>\r\n<ShaderID>8</ShaderID>\r\n<cModelDiffTex>data/a/old.png</cModelDiffTex>"
    b"\r\n<Diffuse.r>1</Diffuse.r>\r\n<Diffuse.g>0.8</Diffuse.g>\r\n<Diffuse.b>0.6</Diffuse.b>\r\n"
    b"<Conditions>40</Conditions>\r\n<Range>2</Range>\r\n</Config>\r\n"
)


def _props(qt, game_name="anno117", text=MATERIAL):
    from annotoolbox.objbuild.objprops import CPropertyEditor

    editor = CPropertyEditor()
    editor.m_game, editor.m_source_names = game_name, True
    editor.m_files = lambda: ["data/a/wall_diff_0.dds", "data/a/wall_diff_1.dds", "data/a/m_lod0.rdm"]
    changes = []
    editor.edited.connect(lambda node, value: changes.append([(node.name, value)]))
    editor.edited_many.connect(lambda pairs: changes.append([(n.name, v) for n, v in pairs]))
    doc = cfgdoc.SDoc.read(text, "a.cfg")
    editor.show_fields(cfgdoc.fields(doc.parts().node), False, "MATERIAL")
    return editor, changes


def _row(editor, name):
    from PySide6.QtCore import Qt

    return next(item for item in editor.findItems(name, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive))


def test_a_shader_is_a_drop_down_that_keeps_a_value_it_does_not_know(qt):
    editor, changes = _props(qt)
    combo = editor.itemWidget(_row(editor, "ShaderID"), 1)
    assert combo.currentText().startswith("8  Physically-Based") and combo.findData("22") >= 0
    combo.setCurrentIndex(combo.findData("22"))
    combo.activated.emit(combo.currentIndex())
    assert changes == [[("ShaderID", "22")]]
    combo.activated.emit(combo.findData("8"))  # the value it has: no edit
    assert len(changes) == 1
    odd, _c = _props(qt, text=MATERIAL.replace(b"<ShaderID>8", b"<ShaderID>3"))
    kept = odd.itemWidget(_row(odd, "ShaderID"), 1)
    assert "not in the list" in kept.currentText() and kept.currentData() == "3"
    other, _c = _props(qt, "anno2070")
    assert other.itemWidget(_row(other, "ShaderID"), 1) is None  # no list for 2070: a number


def test_a_colour_group_takes_the_system_colour_dialog_as_one_edit(qt, monkeypatch):
    from annotoolbox.objbuild import pickers

    editor, changes = _props(qt)
    row = _row(editor, "Diffuse.r")
    assert not row.icon(1).isNull() and _row(editor, "Diffuse.g").icon(1).isNull()
    monkeypatch.setattr(
        pickers, "pick_colour", lambda _p, channels: [255, 0, 153] if channels == [255, 204, 153] else None
    )
    editor.itemDoubleClicked.emit(row, 1)
    assert changes == [[("Diffuse.g", "0")]]
    monkeypatch.setattr(pickers, "pick_colour", lambda _p, channels: None)  # cancelled
    editor.itemDoubleClicked.emit(row, 1)
    assert len(changes) == 1


def test_the_bits_of_conditions_open_a_check_list_in_117_only(qt, monkeypatch):
    from annotoolbox.objbuild import pickers

    editor, changes = _props(qt)
    row = _row(editor, "Conditions")
    assert "Selected" in row.text(1) and "Common01" in row.text(1)  # 40 = bit 3 and bit 5
    seen = []
    monkeypatch.setattr(pickers, "pick_flags", lambda _p, name, names, value: seen.append((name, value)) or 32)
    editor.itemDoubleClicked.emit(row, 1)
    assert seen == [("Conditions", 40)] and changes == [[("Conditions", "32")]]
    old, _c = _props(qt, "anno2070")
    assert _row(old, "Conditions").text(1) == "40"


def test_a_file_value_is_picked_among_the_games_files(qt, monkeypatch):
    from annotoolbox.objbuild import pickers

    editor, changes = _props(qt)
    asked = []

    def pick(_parent, name, hint, find, current):
        asked.append((name, hint, find(""), find("m_"), current))
        return "data/a/wall_diff.psd"

    monkeypatch.setattr(pickers, "pick_file", pick)
    editor.itemDoubleClicked.emit(_row(editor, "cModelDiffTex"), 1)
    assert asked[0][0] == "cModelDiffTex" and asked[0][2] == ["data/a/wall_diff.psd"] and asked[0][3] == []
    assert asked[0][4] == "data/a/old.png" and changes == [
        [("cModelDiffTex", "data/a/wall_diff.png")]
    ]  # the png ending
    opened = []
    editor.open_file.connect(opened.append)
    editor.itemDoubleClicked.emit(_row(editor, "cModelDiffTex"), 0)  # the name opens the file in the viewer
    assert opened == ["data/a/old.png"]


def test_a_read_only_file_opens_no_picker_and_a_number_still_types(qt, monkeypatch):
    from annotoolbox.objbuild import pickers

    editor, changes = _props(qt)
    cfg = cfgdoc.SDoc.read(MATERIAL, "a.cfg")
    editor.show_fields(cfgdoc.fields(cfg.parts().node), True, "MATERIAL")
    monkeypatch.setattr(pickers, "pick_file", lambda *_a: pytest.fail("a read-only file is not edited"))
    monkeypatch.setattr(pickers, "pick_colour", lambda *_a: pytest.fail("a read-only file is not edited"))
    editor.itemDoubleClicked.emit(_row(editor, "cModelDiffTex"), 1)
    editor.itemDoubleClicked.emit(_row(editor, "Diffuse.r"), 1)
    assert not changes and not editor.itemWidget(_row(editor, "ShaderID"), 1).isEnabled()
    free, _c = _props(qt)
    free.itemDoubleClicked.emit(_row(free, "Range"), 1)
    assert free.state() == free.State.EditingState  # a plain number is typed in its row


def test_the_window_makes_a_colour_one_undo_step(qt, game, tmp_path):  # noqa: F811
    window = window_on(game, tmp_path)
    assert (
        window.m_props.m_game == "anno117"
        and window.m_props.m_source_names
        and "data/base/house/house.cfg" in window.m_props.m_files()
    )
    window.open_path(HOUSE)
    window.m_objects.select("cfg", "Models/0/Materials/0")
    assert window.m_props.m_part_kind == "MATERIAL"
    node = window.m_object_doc.document("cfg").find("Models/0/Materials/0").node
    leaves = [c for c in node.children if c.name == "cModelDiffTex"]
    window.m_props.edited_many.emit([(leaves[0], "data/a.psd"), (leaves[0], "data/b.psd")])
    assert b"data/b.psd" in window.m_object_doc.document("cfg").to_bytes()
    window.undo()
    assert not window.m_object_doc.dirty()  # both changes in one step
    window.m_object_doc.m_undo.mark_clean()
    assert window.m_objects.m_items
    window.close_object()
    assert window.m_object_doc is None and window.m_objects.m_items == {}
    window.close()


def test_a_1701_info_draws_its_cells_and_boxes_over_the_view():
    text = (
        b"<BoundingBox><Min><x>-32</x><y>0</y><z>-32</z></Min><Max><x>32</x><y>64</y><z>32</z></Max></BoundingBox>"
        b"<NoBuildings><GridResolution>1</GridResolution><GridPos><x>1</x><z>1</z></GridPos>"
        b"<GridPos><x>2</x><z>1</z></GridPos></NoBuildings>"
        b"<Dummies><hitbox01><Center.x>0</Center.x><Center.y>2048</Center.y><Center.z>0</Center.z>"
        b"<Extents.x>2048</Extents.x><Extents.y>2048</Extents.y><Extents.z>2048</Extents.z></hitbox01></Dummies>"
    )
    doc = cfgdoc.SDoc.read(text, "a.info")
    found = shapes.info_shapes(doc, (0.5, 0.0))
    by_kind = {s.kind: s for s in found}
    assert by_kind["box"].name == "BoundingBox" and max(p[1] for p in by_kind["box"].points) == pytest.approx(2.0)
    assert by_kind["area"].name == "NoBuildings"  # two cells in a row: one rectangle, its origin shifted
    assert sorted({p[0] for p in by_kind["area"].points}) == pytest.approx([-0.5, 1.5])
    assert sorted({p[2] for p in by_kind["area"].points}) == pytest.approx([0.0, 1.0])
    assert by_kind["dummy"].name == "hitbox01" and by_kind["dummy"].points[0] == pytest.approx((-0.5, 0.0, -0.5))


def test_a_1701_object_config_gives_the_objects_origin_to_its_cells():
    from tests.fakes import FakeFiles

    class FakeFiles1701(FakeFiles):
        game = "anno1701"

    files = FakeFiles1701({"a/x.ocf": b"<MeshConfig>a/b.mcf</MeshConfig><XOffset>16</XOffset><ZOffset>0</ZOffset>",
                   "a/b.mcf": b"<Config></Config>", "a/b.info": b"<NoBuildings><GridPos><x>1</x><z>1</z></GridPos></NoBuildings>"})  # fmt: skip
    obj = CObjectDoc.open(files, "a/x.ocf", "anno1701")
    assert obj.m_origin == pytest.approx((0.5, 0.0))
    area = shapes.info_shapes(obj.document("ifo"), obj.m_origin)[0]
    assert min(p[0] for p in area.points) == pytest.approx(-0.5)


def test_a_save_the_project_cannot_take_is_said_and_stays_unsaved(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.suite.project import SEditsFolder

    def refuse(*_args, **_kwargs):
        raise PermissionError("no room")

    said = []
    monkeypatch.setattr(SEditsFolder, "save_announced", refuse)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: said.append(text))
    window = window_on(game, tmp_path, HOUSE)
    window.m_objects.select("cfg", "Models/0")
    window.duplicate_part()
    window.write(writeback.SPlan("replace", {HOUSE: b"x"}))
    assert "no room" in said[0] and window.m_object_doc.m_undo.dirty
    window.m_object_doc.m_undo.mark_clean()
    window.close()


def test_a_part_from_a_file_that_is_no_config_is_said_not_raised(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QInputDialog

    from annotoolbox.models.cfgdoc import SDoc

    def unreadable(_data, path):
        raise ValueError(f"{path} is no config")

    window = window_on(game, tmp_path, HOUSE)
    window.m_objects.select("cfg", "Models/0")
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: (HOUSE, True))
    monkeypatch.setattr(SDoc, "read", unreadable)
    window.add_from_file()
    assert "is no config" in window.statusBar().currentMessage() and not window.m_object_doc.m_undo.dirty
    window.close()


def test_a_part_from_a_path_the_game_lacks_is_said(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QInputDialog

    window = window_on(game, tmp_path, HOUSE)
    window.m_objects.select("cfg", "Models/0")
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("data/mistyped.cfg", True))
    window.add_from_file()
    assert "data/mistyped.cfg" in window.statusBar().currentMessage()
    window.close()


def test_a_picture_that_is_not_written_is_said(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QFileDialog

    window = window_on(game, tmp_path, HOUSE)
    monkeypatch.setattr(window.m_view, "image", lambda: QImage(4, 4, QImage.Format.Format_RGB32), raising=False)
    target = tmp_path / "no such folder" / "house.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    window._save_picture()
    assert "house.png" in window.statusBar().currentMessage() and not target.exists()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(tmp_path / "house.png"), ""))
    window._save_picture()
    assert (tmp_path / "house.png").is_file()
    window.close()


def test_the_shared_copy_stays_ticked_when_the_reopen_is_declined(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox

    window = window_on(game, tmp_path, HOUSE)
    window.m_objects.select("cfg", "Models/0")
    window.duplicate_part()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    window.m_shared_action.trigger()  # Save / Discard / Cancel: Cancel keeps the edits and the tick
    assert window.m_shared_action.isChecked() and window.m_object_doc.m_undo.dirty and window.options().shared
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    window.m_shared_action.trigger()
    assert not window.m_shared_action.isChecked() and not window.m_object_doc.m_undo.dirty
    window.close()


def test_the_figures_menu_reads_the_asset_list_once_per_game_not_once_per_opened_object(qt, monkeypatch):
    from PySide6.QtWidgets import QMenu, QWidget

    from annotoolbox.objbuild.figuremenu import CFigureMenu
    from annotoolbox.viewer import figures

    reads = []
    monkeypatch.setattr(figures, "units_of", lambda files, game: reads.append(game) or object())  # noqa: F811
    menu = CFigureMenu(QWidget(), QMenu())

    class FakeGame:
        game = "anno117"

    class FakeOpened:  # an object's own CEditedFiles is new at every open
        def __init__(self, base, game):  # noqa: F811
            self.m_files, self.m_game = objdoc.CEditedFiles(base), game

    base = FakeGame()
    first = menu.units(FakeOpened(base, "anno117"))
    assert menu.units(FakeOpened(base, "anno117")) is first and reads == ["anno117"]
    menu.units(FakeOpened(FakeGame(), "anno1800"))
    assert reads == ["anno117", "anno1800"]


def test_the_save_dialog_offers_the_ways_and_returns_the_chosen_plan(qt, game):  # noqa: F811
    obj = CObjectDoc.open(game, HOUSE, "anno117")
    obj.apply("cfg", cfgdoc.set_value(_position(obj), "1"))
    dialog = writeback.CSaveDialog(obj)
    assert dialog.chosen() == writeback.REPLACE and dialog.m_save_button.isEnabled()  # the first way that is open
    assert dialog.m_path.text() == HOUSE.replace(".cfg", "_mod.cfg")
    dialog._save()
    assert list(dialog.m_result_plan.files) == [SHARED] and dialog.m_result_plan.way == writeback.REPLACE
