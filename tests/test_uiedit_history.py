"""The UI editor over the History Editions' Phoenix files (``data/ui/studio/gamedata``, scenes with a ``.scene``
suffix): the files listed by name, a project's copy laid over, the library read from the studio folder, a picture found
at its plain file name, the custom properties and binding ends where these games' (and 1800's) layouts keep them; and,
with the games installed, the two games' files."""

from __future__ import annotations

import json
import os
import struct
import uuid
import zlib
from pathlib import Path

import pytest
from tests.test_uiedit import (
    GEN,
    IMAGE,
    SCHEMA,
    VERSIONS,
    WIDGET,
    _guid_of,
    _instance,
    image_file,
    scene_file,
    widget_file,
)

from annotoolbox.formats import leb128, phoenix, phoenixedit, phoenixread, phoenixwrite
from annotoolbox.sources import gametree
from annotoolbox.uiedit import (
    bindings,
    elements,
    fields,
    labels,
    layout,
    library,
    operators,
    pictures,
    projectpart,
    uidoc,
    uifiles,
    viewmodels,
)

GAMEDATA = "data/ui/studio/gamedata/"


@pytest.fixture
def history(tmp_path: Path):
    root = tmp_path / "game"
    folder = root / GAMEDATA
    folder.mkdir(parents=True)
    (folder / "mainmenu.scene").write_bytes(scene_file())
    (folder / WIDGET).write_bytes(widget_file())
    (folder / IMAGE).write_bytes(image_file())
    (folder / "preview.json").write_text("{}")  # a file that is not Phoenix is not listed
    component = root / "data/ui/studio/components/animator_d250.json"
    component.parent.mkdir(parents=True)
    component.write_text(json.dumps({"PhoenixAssetData": {"Object": {
        "ObjectID": "D2506D55-8F94-47A1-93D0-DB3B244A1DAA", "Name": "Animator", "CustomProperties": []}}}))  # fmt: skip
    widget = root / "data/ui/studio/widgets/other_a.json"
    widget.parent.mkdir(parents=True)
    widget.write_text(json.dumps({"PhoenixAssetData": {"Object": {"ObjectID": "A", "Name": "NotLibrary"}}}))
    picture = root / "data/ui/2kimages/main/test.png"
    picture.parent.mkdir(parents=True)
    return root


def test_the_files_are_listed_by_name_and_a_scene_has_no_suffix(history):
    files = uifiles.UiFiles(gametree.loose_tree(history))
    assert files.prefix == GAMEDATA
    assert set(files.entries) == {"mainmenu", WIDGET, IMAGE}
    assert files.entries["mainmenu"].kind == "SC" and files.entries["mainmenu"].path == GAMEDATA + "mainmenu.scene"
    assert files.root("mainmenu") is not None  # the codec reads the scene as it reads 117's


def test_a_projects_copy_of_a_scene_goes_over_the_games_by_the_same_name(history):
    files = uifiles.UiFiles(gametree.loose_tree(history))
    data = scene_file()
    files.lay_over({GAMEDATA + "mainmenu.scene": data})
    assert files.extra["mainmenu"] == data and files.read("mainmenu") == data


def test_an_instance_without_a_name_takes_the_one_the_editors_document_gives(history):
    first = elements.tree(uifiles.UiFiles(gametree.loose_tree(history)).root(WIDGET)).children[0]
    document = history / f"data/ui/studio/widgets/widget_{WIDGET}.json"
    document.write_text(json.dumps({"PhoenixAssetData": {"Object": {"ObjectID": WIDGET.upper(), "Name": "Widget",
        "WidgetInstances": [{"Object": {"ObjectID": first.guid.upper(), "Name": "FromDocument"}}]}}}))  # fmt: skip
    files = uifiles.UiFiles(gametree.loose_tree(history))
    root = files.root(WIDGET)
    first = elements.tree(root).children[0]
    phoenixedit.set_field(first.node, phoenixedit.NAME_FIELD, "")  # as these games store it: no name string ...
    phoenixedit.set_field(first.node, "NameCrc", zlib.crc32(b"FromDocument"))  # ... only the CRC of the name
    names = files.names_of(root)
    assert names[first.guid] == "FromDocument" and names[WIDGET] == "Widget"
    assert elements.tree(root, names).children[0].name == "FromDocument"
    assert elements.tree(root).children[0].name == "(unnamed)"
    assert files.names_of(files.root(IMAGE)) == {}  # a file without a document
    phoenixedit.set_name(first.node, "Renamed")  # renamed in the editor: the document's name is out of date
    phoenixedit.set_field(first.node, phoenixedit.NAME_FIELD, "")
    assert elements.tree(root, names).children[0].name == "(unnamed)"


def test_the_library_is_read_from_the_studio_folder_where_the_game_has_no_widget_library(history):
    items = library.Library.read(gametree.loose_tree(history)).items
    assert [i.name for i in items.values()] == ["Animator"]  # a widget file of the folder is not a library item


def test_the_projects_edits_of_these_games_are_kept(tmp_path):
    assert {"anno1404", "anno1701"} <= set(projectpart.EDITS.games)


def test_a_picture_is_found_at_its_plain_file_name():
    assert pictures.candidates(r"data\ui\studio\images\bgr_main_header.png")[-1] == (
        "data/ui/studio/images/bgr_main_header.png"
    )


HISTORY = {"af01aeda": "7d135118-b22a-267b-c94c-afb251ad01e4", "0c454dec": "9f985b52-7e48-9a2d-a33f-4d5c81841095",
           "41a404e5": "705f5a04-c688-cad0-1ea3-04bacfef4089"}  # fmt: skip  # these games' layout versions


def test_a_widget_of_these_games_is_placed_by_117s_anchor_and_modes():
    schema = phoenix.load_schema()
    label = phoenixedit.new_object(schema, "41a404e5", versions=HISTORY)
    assert fields.anchor_fields(label) == dict.fromkeys(fields.ANCHOR_FIELDS, 0)  # top left, proportional
    fields.set_value(label, "HorizontalAnchorConstraint", 1)  # right
    fields.set_value(label, "VerticalAnchorConstraint", 2)  # centre
    fields.set_value(label, "HorizontalResizeMetrics", 1)
    fields.set_value(label, "KeepAspectRatio", True)
    assert fields.anchor_fields(label) == {"AnchorPoint": 5, "XRepositioning": 0, "YRepositioning": 0,
                                           "WidthResizing": 1, "HeightResizing": 0, "AspectRatio": 1}  # fmt: skip
    assert [fields.alignment_index(v) for v in (0, 1, 2, 3, 256, 512, 768)] == [0, 1, 2, 3, 1, 2, 3]
    shipped = phoenixedit.new_object(schema, "41a404e5")
    fields.set_value(shipped, "AnchorPoint", 8)
    assert fields.anchor_fields(shipped)["AnchorPoint"] == 8  # 117's own fields as they are


@pytest.mark.parametrize(("game", "variable"), [("anno1404", "ANNO1404_DIR"), ("anno1701", "ANNO1701_DIR")])
def test_the_installed_game_lists_its_phoenix_files_and_opens_what_the_codec_reads(game, variable):
    folder = os.environ.get(variable)
    if not folder:
        pytest.skip(f"{variable} is not set")
    tree = gametree.open_game(game, Path(folder))
    files = uifiles.UiFiles(tree)
    assert files.prefix == GAMEDATA and len(files.entries) == 732
    assert len([e for e in files.entries.values() if e.kind == "SC"]) == 18
    assert "mainmenu" in files.entries
    for key in files.entries:
        files.load(key)  # every file decodes (docs/formats/phoenix-scene-format.md, "The History Editions' files")
    assert len(library.Library.read(tree).items) > 100
    shown = [el for e in files.of_kind("SC", "WI") for el in elements.tree(files.root(e.key),
             files.names_of(files.root(e.key))).walk() if el.kind != "template"]  # fmt: skip
    assert len(shown) == 1847 and sum(1 for el in shown if el.given) == 1809  # five widget files ship no document


@pytest.mark.parametrize(("game", "variable"), [("anno1404", "ANNO1404_DIR"), ("anno1701", "ANNO1701_DIR")])
def test_an_edit_of_an_installed_scene_is_written_and_undone_byte_for_byte(game, variable):
    folder = os.environ.get(variable)
    if not folder:
        pytest.skip(f"{variable} is not set")
    files = uifiles.UiFiles(gametree.open_game(game, Path(folder)))
    data = files.read("mainmenu")
    doc = uidoc.UiDoc(files, "mainmenu", data)
    assert doc.exact
    widget = next(e for e in elements.tree(doc.root).walk() if e.kind == "container")
    assert fields.anchor_fields(widget.node)["AnchorPoint"] in range(9)
    doc.move(widget.node, (12.0, 34.0, 0.0))
    image = doc.new_instance("image", "Added")
    doc.add_instance(elements.tree(doc.root), image, "Added")
    again = uidoc.UiDoc(files, "mainmenu", doc.to_bytes())
    moved = elements.by_guid(elements.tree(again.root), widget.guid)
    assert tuple(fields.get(moved.node, "Position"))[:2] == (12.0, 34.0)
    assert any(f["name"] == "HorizontalAnchorConstraint" for _, f in image["layout"])  # in the file's layout
    while doc.undo.index:
        doc.undo.undo()
    assert phoenixwrite.save(doc.decoder) == data


TEMPLATE_1800 = phoenix.load_schema(phoenix.VERSION_SCHEMA_PATHS[0])["da06bd6e"]["guid"]
TEMPLATE_HISTORY = "9fb62ff2-d074-846e-66b4-73903920785a"  # its widgets in the record itself: no GroupTemplate


@pytest.mark.parametrize("template", [TEMPLATE_1800, TEMPLATE_HISTORY])
def test_an_older_layouts_properties_and_ends_are_read_and_added_where_it_keeps_them(template, tmp_path):
    """1800 keeps a widget file's custom properties in ``CustomProperties``, the History Editions their ends and data
    objects in the ``Unique...`` lists and an own property as a path on the widget itself (written there too)."""
    versions = {**VERSIONS, "da06bd6e": template}
    if template == TEMPLATE_HISTORY:
        versions.pop("2f208351")
    root = phoenixedit.new_object(SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(WIDGET).bytes_le, root=True,
                                  versions=versions)  # fmt: skip
    phoenixedit.set_objects(root, "WidgetInstances", [_instance("41a404e5", "Caption")])
    guid_of = {k: versions[k] if k in versions else v for k, v in _guid_of(root).items() if k in versions}
    (tmp_path / GEN).mkdir(parents=True)
    (tmp_path / GEN / WIDGET).write_bytes(phoenixwrite.write_file(SCHEMA, "WI", root, guid_of))
    files = uifiles.UiFiles(gametree.loose_tree(tmp_path))
    doc = uidoc.UiDoc(files, WIDGET, files.read(WIDGET))
    title = doc.add_property("Title", 4, fields.STRING, "Hello")
    shown = doc.add_property("Shown", 4, fields.STRING, "")
    caption = next(e for e in elements.tree(doc.root).walk() if e.kind == "label")
    on_self = [bindings.EndSpec("path", None, (title,))], [bindings.EndSpec("path", None, (shown,))]
    doc.add_binding(*on_self, operators.ASSIGN)  # a path on the widget itself writes its own property
    doc.add_binding([bindings.EndSpec("path", None, (shown,))], [bindings.EndSpec("path", caption.guid,
                    (labels.TEXT,))], operators.ASSIGN)  # fmt: skip
    again = phoenixread.load(doc.to_bytes(), files.schema).root
    graph = bindings.read(again)
    assert [bindings.property_info(p)["name"] for p in graph.properties] == ["Title", "Shown"]
    assert all(e.steps for b in graph.bindings for e in (*b.sources, *b.targets))
    frame = layout.place(WIDGET, again, layout.Context(files, viewmodels.Samples()))
    assert [i.text for i in frame.items if i.kind == "text"] == ["Hello"]


ATOM_PROPERTIES = "e23bb15e-a8bb-7ac2-fd39-fe2f054425a4"  # these games' CustomPropertyInstance: with AtomPropertyId
DYNAMIC_CONTAINER = "a9994114-44c2-fedd-ffec-e020b06a6ab7"  # ... and ContainerWidgetInstance: with DynamicProperties
TYPE = str(uuid.uuid5(uuid.NAMESPACE_URL, "widget type"))


def _typed_strings(values: list[tuple[int, str]]) -> bytes:
    """A ``DynamicProperties`` list of strings: per value its id, the type code, 33 and the string."""
    out = leb128.encode(len(values))
    for atom, text in values:
        out += struct.pack("<IHI", atom, fields.STRING, 33) + phoenix.encode_value(fields.STRING, text)
    return out


def _with_atom(wf, name: str, default: str, atom: int) -> int:
    uid = wf.add_property(name, "string", default)
    phoenixedit.set_field(wf.properties[-1], "AtomPropertyId", atom)
    return uid


def test_a_widget_has_its_types_properties_and_a_container_gives_them_by_their_atom_ids():
    """A widget type's properties are every widget's of the type (bindings read them as its own); the History
    Editions' ``DynamicProperties`` give the placed file's and its type's properties by AtomPropertyId."""
    versions = {**VERSIONS, "1294a820": ATOM_PROPERTIES}
    kind = phoenixedit.new_object(SCHEMA, "17afa8e7", guid=uuid.UUID(TYPE).bytes_le, root=True, versions=versions)
    typed = phoenixedit.WidgetFile(SCHEMA, kind, versions=versions)
    kind_uid = _with_atom(typed, "Kind", "from the type", 0xA1)
    phoenixedit.set_objects(kind, "CustomProperties", typed.properties)
    root = phoenixedit.new_object(SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(WIDGET).bytes_le, root=True,
                                  versions=versions)  # fmt: skip
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("17afa8e7", TYPE))
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=versions)
    caption, second = _instance("41a404e5", "Caption"), _instance("41a404e5", "Second", (0.0, 50.0, 0.0))
    wf.add_instance(caption)
    wf.add_instance(second)
    _with_atom(wf, "Title", "Hello", 0xB2)
    wf.bind(wf.own("Title"), wf.end(caption, [phoenixedit.BUILTIN["Text"]]))
    wf.bind(wf.end(None, [kind_uid]), wf.end(second, [phoenixedit.BUILTIN["Text"]]))  # the type's, on itself
    wf.finish("typed")
    box = phoenixedit.new_object(SCHEMA, "0f561333", guid=uuid.uuid4().bytes_le,
                                 versions={**VERSIONS, "0f561333": DYNAMIC_CONTAINER})  # fmt: skip
    phoenixedit.set_raw(box, "Template", phoenix.ref_bytes(phoenixedit.WIDGET_TEMPLATE, WIDGET))
    scene = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(scene, "WidgetInstances", [box])
    trees = {WIDGET: root, TYPE: kind}
    files = type("Files", (), {"root": lambda self, key: trees.get(key), "text_of": lambda self, *a: None})()

    def texts() -> list[str]:
        return [i.text for i in layout.place("scene", scene, layout.Context(files, viewmodels.Samples())).items]

    assert texts() == ["Hello", "from the type"]  # the type's default, read on the widget itself
    phoenixedit.set_raw(box, "DynamicProperties", _typed_strings([(0xB2, "given"), (0xA1, "the type's given")]))
    assert texts() == ["given", "the type's given"]
    _with_atom(typed, "Shadow", "the type's", 0xB2)  # the type declares the atom too: the widget's own takes it
    phoenixedit.set_objects(kind, "CustomProperties", typed.properties)
    assert texts() == ["given", "the type's given"]
