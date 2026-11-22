"""The preview's placeholder state on synthetic files: a value the game sets at run time stands in as a coherent
typical state (no lock, no error, one alternative of a loader, the file's hidden layers hidden), a component's
property reads its declared default, what a placing file writes into a placed file's object reaches it, and the
bindings fire as the game's do (the one whose source changed last wins)."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import GEN, IMAGE, image_file, image_node, root_node, scene_instance, template_container
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import bindings, labels, layout, loaders, placeholders, uifiles, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
CHILD, COMP = (str(uuid.uuid5(uuid.NAMESPACE_URL, n)) for n in ("state-child", "state-component"))
GAME_VALUE = [0x10000001, 0x20000002]  # a path through a game object the samples do not hold
POSITION = phoenixedit.BUILTIN["Position"]


def _path(ids, instance="x", mode=0) -> bindings.SEnd:
    return bindings.SEnd(0, "path", {}, [(mode, instance)], list(ids))


def _describe(names: dict[int, tuple[str, str]]):
    """(value type, name) by an end's last id: the built-ins' own, else the names given."""
    return lambda e: names.get(e.ids[-1]) or placeholders.target_kind(e.ids[-1]) if e.ids else ("value", "")


def test_a_flag_takes_the_typical_state_its_names_say():
    source = _path(GAME_VALUE, None, 1)
    locked = _path([0x10000003])
    names = {0x10000003: ("bool", "-IsLocked"), 0x10000004: ("bool", "HasError"), 0x10000005: ("bool", "IsUnlocked")}
    describe = _describe(names)
    assign = bindings.SBinding({}, "c346a1bd", [source], [locked])
    assert placeholders.guess(assign, 0, describe) is False  # unlocked unless the preview's settings lock it
    assert placeholders.guess(assign, 0, describe, locked=True) is True
    invert = bindings.SBinding({}, "111e8241", [source], [_path([0x10000005])])  # IsUnlocked = not <game flag>
    assert placeholders.guess(invert, 0, describe) is False
    error = bindings.SBinding({}, "c346a1bd", [source], [_path([0x10000004])])
    assert placeholders.guess(error, 0, describe) is False
    assert placeholders.state_for("IsInvalid") is False and placeholders.state_for("IsValid") is True
    assert placeholders.state_for("Visible") is None


def test_a_flag_follows_the_hidden_layer_the_file_stores_and_its_other_readers():
    source = _path(GAME_VALUE, None, 1)
    shown = bindings.SBinding({}, "c346a1bd", [source], [_path([0x80000007])])  # -> a layer's Visible
    describe = _describe({0x10000006: ("bool", "-EditActive")})
    assert placeholders.guess(shown, 0, describe) is True  # nothing known: a visibility shows
    assert placeholders.guess(shown, 0, describe, stored=lambda e: False) is False  # the file keeps it hidden
    edit = bindings.SBinding({}, "c346a1bd", [source], [_path([0x10000006])])
    # the same game value also feeds an edit mode: one value, one state, so the layer it shows is hidden too
    assert placeholders.guess(shown, 0, describe, readers=((edit, 0),)) is False


def test_an_enum_loader_shows_nothing_for_a_value_no_child_has(monkeypatch):
    a, b, plain = (SimpleNamespace(node={}, entry=e) for e in (11, 12, 0))
    monkeypatch.setattr(loaders, "_entry", lambda c: c.entry)
    kids = [a, b, plain]
    assert loaders.chosen("enum", kids, {loaders.CURRENT_ENUM_VALUE: 12}, True) == [b, plain]
    assert loaders.chosen("enum", kids, {loaders.CURRENT_ENUM_VALUE: 99}, True) == [plain]  # "unlocked": no lock
    assert loaders.chosen("enum", kids, {}, True) == [a, plain]  # unknown: the design view's first entry


def test_a_label_takes_the_layout_modes_its_bindings_write():
    node = scene_instance("41a404e5", "Text")
    phoenixedit.set_field(node, "AlignmentX", 0)
    assert labels.mode(node, {(labels.MODE_IDS["AlignmentX"],): 1}, "AlignmentX") == 1
    assert labels.mode(node, {(labels.MODE_IDS["AlignmentX"],): 42}, "AlignmentX") == 0  # no such entry: stored
    assert labels.mode(node, {}, "AlignmentX") == 0


def _child_file() -> bytes:
    """A picture placed by a vector property with a declared default and by a field of an object property."""
    root = root_node(CHILD)
    picture, marker = image_node("Picture"), image_node("Marker")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_instance(marker)
    wf.add_property("Size", "vec3", (200.0, 40.0, 0.0))
    wf.add_property("Layout", "gameobject")
    wf.bind(wf.own("Size"), wf.end(picture, [POSITION]))
    wf.bind(wf.end(None, [wf.m_unique_ids["Layout"], 0x294028E1]), wf.end(marker, [POSITION]))
    wf.finish("state-child")
    return _write("WI", root)


def _component_file() -> bytes:
    """Stands in for a component file: what is read of it is its declared custom property."""
    root = root_node(COMP)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_property("ScalingFactor", "vec3", (3.0, 4.0, 0.0))
    wf.finish("state-component")
    return _write("WI", root)


def _scene() -> bytes:
    """Writes one axis of the child's size and a field of its object; reads a component of its own; and has two
    bindings writing one value, the later one in the file from a value that never changes."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    child, scaled, last = template_container("Child", CHILD), image_node("Scaled"), image_node("Last")
    comp_uid = phoenixedit.unique_id(0x70000000, "UIScaling")
    phoenixedit.set_objects(root, "Components", [phoenixedit.component(SCHEMA, COMP, "UIScaling", comp_uid, VERSIONS)])
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (child, scaled, last):
        wf.add_instance(node)
    wf.add_property("X", "float", 7.0)
    wf.add_property("Plane", "vec3", (5.0, 6.0, 0.0))
    for name in ("Zero", "Mid", "Out"):
        wf.add_property(name, "vec3", (0.0, 0.0, 0.0))
    wf.add_property("A", "vec3", (9.0, 9.0, 0.0))
    size = phoenixedit.unique_id(0x10000000, "Size")
    layout_uid = phoenixedit.unique_id(0x10000000, "Layout")
    wf.bind(wf.own("X"), wf.end(child, [size], 2))  # one axis: the other is the declared 40
    wf.bind(wf.own("Plane"), wf.end(child, [layout_uid, 0x294028E1]))
    wf.bind(wf.end(None, [comp_uid, phoenixedit.unique_id(0x10000000, "ScalingFactor")]), wf.end(scaled, [POSITION]))
    wf.bind(wf.own("Zero"), wf.own("Out"))  # fires once, at the start
    wf.bind(wf.own("Mid"), wf.own("Out"))  # fires again when Mid changes: its value stays
    wf.bind(wf.own("A"), wf.own("Mid"))
    wf.bind(wf.own("Out"), wf.end(last, [POSITION]))
    wf.finish("state-scene")
    return _write("SC", root)


def test_values_reach_placed_files_and_components_and_bindings_fire_on_change(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, data in ((CHILD, _child_file()), (COMP, _component_file()), (IMAGE, image_file()), ("state", _scene())):
        (gen / key).write_bytes(data)
    files = uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))
    frame = layout.place("state", files.root("state"), layout.CContext(files, viewmodels.CSamples()))
    at = sorted(layout.apply(i.matrix, 0, 0) for i in frame.items if i.kind == "image")
    assert (7.0, 40.0) in at  # the child's size: x written, y its declared default (not 0)
    assert (5.0, 6.0) in at  # the child's object field, as the placing file wrote it
    assert (3.0, 4.0) in at  # the scene's component: its declared default
    assert (9.0, 9.0) in at  # Out follows Mid, which changed last; not the later binding from Zero


def test_a_live_value_wins_over_a_placeholder_state(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    badge = image_node("Badge")
    phoenixedit.set_field(badge, "Visible", False)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(badge)
    wf.add_property("Data", "gameobject")
    wf.bind(wf.end(None, [wf.m_unique_ids["Data"], 0x2B2357A9]), wf.end(badge, [phoenixedit.BUILTIN["Visible"]]))
    wf.finish("live")
    for key, data in ((IMAGE, image_file()), ("live", _write("SC", root))):
        (gen / key).write_bytes(data)
    files = uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))
    ctx = layout.CContext(files, viewmodels.CSamples())
    assert not [i for i in layout.place("live", files.root("live"), ctx).items if i.kind == "image"]  # kept hidden
    guid = str(uuid.UUID(bytes_le=badge["guid"]))
    ctx.m_live_values = {guid: {(phoenixedit.BUILTIN["Visible"],): True}}
    assert [i for i in layout.place("live", files.root("live"), ctx).items if i.kind == "image"]
