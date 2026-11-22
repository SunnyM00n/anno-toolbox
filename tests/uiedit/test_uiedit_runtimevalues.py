"""What the game gives a scene at run time, as the preview's typical state: one selected entry per group (a list's
first element, a menu's first access button), one game object shared by every file that holds it (down, up, between
siblings), a label's shown size, texts compared as texts, the object loader's child by its object's class, what a list
gives every item from all of them (``ValueCompare``), a wheel's segments and the end of an intro animation."""

from __future__ import annotations

import os
import uuid
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import image_node, root_node, scene_instance, scene_pictures, template_container
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenixedit
from annotoolbox.uiedit import (
    gamedata,
    gameplans,
    listlayout,
    operators,
    placeholders,
    scope,
)
from annotoolbox.uiedit.viewmodels import SClassDef, SFieldDef

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HANDS, READS = (str(uuid.uuid5(uuid.NAMESPACE_URL, n)) for n in ("runtime-hands", "runtime-reads"))
POSITION = phoenixedit.BUILTIN["Position"]
FIELD = 0x2891C796  # a field of the handed object


def test_one_entry_of_a_group_is_selected_the_first():
    groups = {0x2D1B4DE0: True, 0x28569230: False}.get
    under = "scene:list/30000001/{:08x}/2ed53815/2bf6e886/256328d9"
    assert placeholders.selection_for("IsSeleted", under.format(0x90000000), groups) is True  # the game's spelling
    assert placeholders.selection_for("IsSelected", under.format(0x90000002), groups) is False
    assert placeholders.selection_for("IsSelected", "scene/10000002/188dddb6/2d1b4de0/23c6da0b/256328d9", groups)
    assert (
        placeholders.selection_for("IsSelected", "scene/10000002/188dddb6/28569230/23c6da0b/256328d9", groups) is False
    )
    assert placeholders.selection_for("IsSelected", "scene/10000002/23c6da0b/256328d9", groups) is False  # alone: off
    assert placeholders.selection_for("IsVisible", under.format(0x90000000), groups) is None


def test_a_tab_play_mode_selected_takes_the_selection_flag():
    """The click on the fourth options tab writes ``TabsData.SelectedTabID`` 3: that element is selected, not the first."""
    under = "scene:list/30000001/{:08x}/256328d9"
    chosen = {"scene:list/30000001": 3}.get
    assert placeholders.selection_for("IsSeleted", under.format(0x90000003), index_of=chosen) is True
    assert placeholders.selection_for("IsSeleted", under.format(0x90000000), index_of=chosen) is False
    from annotoolbox.uiedit import memo, playstate

    choices = playstate.SChoices({"scene.#0000abcd.SelectedTabID": 3})  # the object holding the list, then its field
    ctx = SimpleNamespace(m_play_choices=choices, m_memo=memo.CMemo(), m_game_data=None)
    assert playstate.chosen_index(ctx, "scene/0000abcd/0000beef") == 3
    assert playstate.chosen_index(ctx, "scene/0000dcba/0000beef") is None  # another object's list
    assert (playstate.TOKEN, "under:scene.#0000abcd") in playstate.choice_tokens("scene.#0000abcd.SelectedTabID")


def test_the_first_of_side_by_side_child_objects_comes_from_the_data_classes():
    child = lambda name: SFieldDef(name, "rdgs.CButtonData", "child")  # noqa: E731
    menu = SClassDef("rdgs.CMenu", [SFieldDef("IsVisible", "boolean", "value"), child("BuildingsButton"),
                                   child("OrnamentsButton"), SFieldDef("Lone", "rdgs.CLone", "child")])  # fmt: skip
    groups = gamedata.stub_groups(SimpleNamespace(m_classes={menu.name: menu}))
    assert groups == {"BuildingsButton": True, "OrnamentsButton": False}


def test_the_options_page_follows_the_tab_play_mode_selected(monkeypatch):
    from annotoolbox.uiedit import gameplans

    monkeypatch.setattr(gameplans, "_options_pages", lambda game, ctx: [(1, ""), (2, ""), (3, "")])
    game = SimpleNamespace(m_scene="optionsscene", m_play=None)
    assert gameplans._open_page(game, None) == [(1, "")]  # the game opens on the first
    game.m_play = SimpleNamespace(values={"optionsscene.GameContext.SceneData.TabsData.SelectedTabID": 2})
    assert gameplans._open_page(game, None) == [(3, "")]


def test_a_list_gives_every_item_the_smallest_and_largest_value_of_all():
    assert listlayout.compared([(107.0, 32.0, 0.0), (190.3, 30.0, 0.0), None]) == (
        (107.0, 30.0, 0.0),
        (190.3, 32.0, 0.0),
    )
    assert listlayout.compared([None]) is None


def test_a_wheel_gives_each_item_its_angle_on_the_circle():
    sectors = listlayout.sectors("radial-wheel", {0x3D4CBCB7: 84.0}, 8)
    assert [round(a) for _, _, a, _ in sectors] == [0, 45, 90, 135, 180, 225, 270, 315]
    x, y, _, span = sectors[2]
    assert (round(x, 3), round(y, 3), span) == (84.0, 0.0, 45.0)  # the game's item 2 stands at 84, 0


def test_an_object_is_passed_with_its_written_fields_and_they_win_over_a_stored_one():
    ref = scope.SGameObjectRef("file/147c2af3", holder="instance")
    passed = scope.passed(ref, {(FIELD,): True})
    assert passed == scope.SGivenObject(scope.SGameObjectRef("file/147c2af3"), (((FIELD,), True),))  # no holder
    assert scope.passed(ref, {}) == scope.SGameObjectRef("file/147c2af3")
    stored = {(0x1062D868, FIELD): False, (0x1062D868,): passed}  # the instance's own stored field loses
    given = scope.inputs_of(stored)[0x1062D868]
    assert given.base == scope.SGameObjectRef("file/147c2af3") and dict(given.fields) == {(FIELD,): True}


def test_texts_compare_as_texts():
    equal, not_equal = "9bd3b2dc", "1bd6542a"
    assert operators.evaluate(equal, ["icon_2d_thresher.png", ""]) == [False]  # an icon is not the empty text
    assert operators.evaluate(not_equal, ["", "Sprache"]) == [True]
    assert operators.evaluate(equal, [1, 1.0]) == [True] and operators.evaluate(equal, ["", ""]) == [True]


def test_an_animator_the_scene_start_does_not_fire_holds_its_start():
    node = image_node("Text")
    uid = phoenixedit.unique_id(0x70000000, "Animator")
    template = "d2506d55-8f94-47a1-93d0-db3b244a1daa"
    phoenixedit.set_objects(node, "Components", [phoenixedit.component(SCHEMA, template, "Animator", uid, VERSIONS)])
    path, written = (uid, scope.ANIM_CURRENT), {(uid, scope.ANIM_END): 59.456}
    assert scope.animated_end(node, path, {}, written, lambda _uid: 0.0) == 59.456  # nothing stored: as declared
    assert scope.animated_end(node, path, {}, written) is None  # declared values unknown


def test_an_intro_animation_is_shown_at_its_end_in_the_design_view():
    node = image_node("Panel")
    uid = phoenixedit.unique_id(0x70000000, "Animator")
    template = "d2506d55-8f94-47a1-93d0-db3b244a1daa"
    phoenixedit.set_objects(node, "Components", [phoenixedit.component(SCHEMA, template, "Animator", uid, VERSIONS)])
    stored = {(uid, scope.ANIM_START): -30.0, (uid, scope.ANIM_CURRENT): -30.0, (uid, scope.ANIM_END): 40.0}
    assert scope.animated_end(node, (uid, scope.ANIM_CURRENT), stored, {}) == 40.0
    assert scope.animated_end(node, (uid, scope.ANIM_CURRENT), stored, {(uid, scope.ANIM_END): 12.0}) == 12.0
    moved = {**stored, (uid, scope.ANIM_CURRENT): 5.0}  # not at its start: as stored
    assert scope.animated_end(node, (uid, scope.ANIM_CURRENT), moved, {}) is None


def _reads_file() -> bytes:
    """Places a picture where a field of its object says."""
    root = root_node(READS)
    picture = image_node("Picture")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_property("In", "gameobject")
    wf.bind(wf.end(None, [wf.m_unique_ids["In"], FIELD]), wf.end(picture, [POSITION]))
    wf.finish("runtime-reads")
    return _write("WI", root)


def _hands_file() -> bytes:
    """Writes a field of its own object, then hands the object whole to the file it places."""
    root = root_node(HANDS)
    reads = template_container("Reads", READS)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(reads)
    wf.add_property("States", "gameobject")
    wf.add_property("Value", "vec3", (11.0, 12.0, 0.0))
    wf.bind(wf.own("Value"), wf.end(None, [wf.m_unique_ids["States"], FIELD]))
    wf.bind(wf.own("States"), wf.end(reads, [phoenixedit.unique_id(0x10000000, "In")]))
    wf.finish("runtime-hands")
    return _write("WI", root)


def test_a_nested_file_reads_the_field_written_into_the_object_it_was_handed(tmp_path):
    assert scene_pictures(tmp_path, HANDS, {READS: _reads_file(), HANDS: _hands_file()}) == [(11.0, 12.0)]


WRITER, MIDDLE, TOP, OWNER, FILLER, PARENT = (
    str(uuid.uuid5(uuid.NAMESPACE_URL, f"runtime-{n}"))
    for n in ("writer", "middle", "top", "owner", "filler", "parent")
)
F1, F2 = 0x2B2357A9, 0x2D2E7272  # two fields of a shared object


def _file(guid: str, objects: tuple[str, ...], values: dict, writes: dict, reads: dict, places: dict, hands=()):
    """A widget file: its game object properties, vector properties (name -> default), own values written into a
    field of an object (value -> (object, field)), a picture per field read (object -> field), the files it places
    (name -> file) and what it hands them (own object -> (instance, its property); or (instance, property) ->
    (instance, property): one placed file's output into another)."""
    root = root_node(guid)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    nodes = {name: wf.add_instance(template_container(name, file)) for name, file in places.items()}
    for name in objects:
        wf.add_property(name, "gameobject")
    for name, default in values.items():
        wf.add_property(name, "vec3", default)
    for value, (obj, field) in writes.items():
        wf.bind(wf.own(value), wf.end(None, [wf.m_unique_ids[obj], field]))
    for obj, field in reads.items():
        picture = wf.add_instance(image_node(f"Reads{obj}"))
        wf.bind(wf.end(None, [wf.m_unique_ids[obj], field]), wf.end(picture, [POSITION]))
    for source, (name, prop) in hands:
        start = wf.own(source) if isinstance(source, str) else wf.end(nodes[source[0]], [_uid(source[1])])
        wf.bind(start, wf.end(nodes[name], [_uid(prop)]))
    wf.finish(guid)
    return _write("WI", root)


def _uid(name: str) -> int:
    return phoenixedit.unique_id(0x10000000, name)


def test_a_field_written_three_files_down_reaches_the_file_that_owns_the_object(tmp_path):
    files = {
        WRITER: _file(WRITER, ("Out",), {"Size": (21.0, 22.0, 0.0)}, {"Size": ("Out", F1)}, {}, {}),
        MIDDLE: _file(MIDDLE, ("Obj",), {}, {}, {}, {"Writer": WRITER}, [("Obj", ("Writer", "Out"))]),
        TOP: _file(TOP, ("Obj",), {}, {}, {"Obj": F1}, {"Middle": MIDDLE}, [("Obj", ("Middle", "Obj"))]),
    }
    assert scene_pictures(tmp_path, TOP, files) == [(21.0, 22.0)]  # the text width of the options' tab label


def test_sibling_files_fill_one_object_together(tmp_path):
    """The object menu: the background file's object goes to the controller, which writes the height the
    background adds its padding to, and reads the background's width (``20261116-231256-906``)."""
    files = {
        OWNER: _file(OWNER, ("BG",), {"W": (5.0, 0.0, 0.0)}, {"W": ("BG", F1)}, {"BG": F2}, {}),
        FILLER: _file(FILLER, ("BG",), {"H": (0.0, 7.0, 0.0)}, {"H": ("BG", F2)}, {"BG": F1}, {}),
        PARENT: _file(
            PARENT, (), {}, {}, {}, {"Owner": OWNER, "Filler": FILLER}, [(("Owner", "BG"), ("Filler", "BG"))]
        ),
    }
    assert scene_pictures(tmp_path, PARENT, files) == [(0.0, 7.0), (5.0, 0.0)]


def test_what_an_instance_only_stores_below_an_object_is_not_passed_on(monkeypatch):
    node = template_container("Text", READS)
    monkeypatch.setattr(scope.fields, "property_values", lambda _n: {(0x1C8107E7, FIELD): ""})
    ref = scope.SGameObjectRef("file/1c8107e7")
    given = scope.inputs_of({(0x1C8107E7,): ref, (0x1C8107E7, FIELD): "", (0x1C8107E7, F1): 3}, node)[0x1C8107E7]
    assert given.local == (((FIELD,), ""),) and given.fields == (((F1,), 3),)  # an author's preview text stays


def test_an_object_loader_shows_the_child_made_for_its_objects_class(monkeypatch):
    kids = [SimpleNamespace(guid="controls"), SimpleNamespace(guid="generic")]
    reads = {"controls": {"Entries", "PresetsControlData"}, "generic": {"Entries", "BenchmarkBtnData"}}
    monkeypatch.setattr(gamedata, "fields_read", lambda _ctx, _root, _uid, child: reads[child])
    game = SimpleNamespace(m_classes={"Generic": frozenset({"Entries", "BenchmarkBtnData"}),
                                    "Controls": frozenset({"Entries", "PresetsControlData"})})  # fmt: skip
    ctx = SimpleNamespace(m_game_data=game, m_language="english", m_defaults=None, m_play_choices=None)
    for name, child in (("Generic", "generic"), ("Controls", "controls")):
        game.class_of = lambda _key, name=name: name
        assert gamedata.object_child(ctx, {}, scope.SGameObjectRef("optionsscene/1"), kids).guid == child
    game.class_of = lambda _key: None
    assert gamedata.object_child(ctx, {}, scope.SGameObjectRef("optionsscene/1"), kids) is None  # the first then


def test_the_options_page_type_names_its_data_class():
    pages = {1: ET.fromstring("<A><Values><SettingsMenu><Type>ControlsMK</Type></SettingsMenu></Values></A>"),
             2: ET.fromstring("<A><Values><SettingsMenu/></Values></A>")}  # fmt: skip
    game = SimpleNamespace(m_assets=SimpleNamespace(element=pages.get))
    assert gameplans._page_class(game, SimpleNamespace(node=(1, ""))) == "rdgs.CControlOptionsPageData"
    assert gameplans._page_class(game, SimpleNamespace(node=(2, ""))) == "rdgs.CGenericOptionsPageData"


def test_a_binding_reads_the_size_a_label_shows_its_text_at(tmp_path):
    root = root_node(TOP)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    label = wf.add_instance(scene_instance("41a404e5", "Text", (0.0, 0.0, 0.0)))
    phoenixedit.set_field(label, "Text", "abcd")
    phoenixedit.set_field(label, "Scale", (2.0, 2.0, 1.0))
    picture = wf.add_instance(image_node("AtTheTextsEnd"))
    wf.bind(wf.end(label, [0x80000052]), wf.end(picture, [POSITION]))  # DisplayedSize
    wf.finish("runtime-displayed")
    # the stand-in measure: 4 characters of half the size (24) wide, one line 1.25 sizes high; times the scale
    assert scene_pictures(tmp_path, TOP, {TOP: _write("WI", root)}) == [(96.0, 60.0)]
