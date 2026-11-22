"""Stand-ins for the UI editor: scene and widget files, the play mode's elements and the live values."""

from __future__ import annotations

import time
import uuid

from tests.fakegame import FakeGame, connect, disconnect
from tests.ui.uifakes import SCHEMA

from annotoolbox.formats import phoenix, phoenixedit, phoenixwrite
from annotoolbox.sources import gametree
from annotoolbox.suite import livelink
from annotoolbox.uiedit import elements, layout, listlayout, playinput, uifiles, viewmodels

FULL = phoenix.full_schema(SCHEMA)


UIEDIT_VERSIONS = {h: r["guid"] for h, r in FULL.items() if r.get("guid")}


WIDGET, IMAGE, STYLE = (str(uuid.uuid5(uuid.NAMESPACE_URL, n)) for n in ("widget", "image", "style"))


GEN = phoenix.GENERATED


def guids_of(root: dict) -> dict[str, str]:
    out = {}
    for n in phoenixedit.walk(root):
        for rec in [*phoenix.type_chain(FULL, n["schema_type"]), f"{n['type']:08x}"]:
            out.setdefault(rec, UIEDIT_VERSIONS.get(rec, "00000000-0000-0000-0000-000000000001"))
    return out


def ui_file_bytes(kind: str, root: dict) -> bytes:
    return phoenixwrite.write_file(SCHEMA, kind, root, guids_of(root))


def scene_instance(type_hash: str, name: str, pos=(0.0, 0.0, 0.0)) -> dict:
    node = phoenixedit.new_object(
        SCHEMA, type_hash, guid=uuid.uuid5(uuid.NAMESPACE_URL, name).bytes_le, versions=UIEDIT_VERSIONS
    )
    phoenixedit.set_name(node, name)
    phoenixedit.set_field(node, "Position", pos)
    return node


def widget_file() -> bytes:
    """A widget file: an image (64 x 32 picture), a label whose text an own property gives, anchored bottom right."""
    root = phoenixedit.new_object(
        SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(WIDGET).bytes_le, root=True, versions=UIEDIT_VERSIONS
    )
    image = scene_instance("c19f99dd", "Picture", (10.0, 20.0, 0.0))
    phoenixedit.set_raw(image, "Image", phoenix.ref_bytes(phoenixedit.IMAGE_ASSET, IMAGE))
    label = scene_instance("41a404e5", "Caption", (-100.0, -40.0, 0.0))
    phoenixedit.set_field(label, "AnchorPoint", 8)
    phoenixedit.set_field(label, "TextPlane", (100.0, 1.0))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    wf.add_instance(image)
    wf.add_instance(label)
    wf.add_property("Title", "string", "Hello")
    wf.bind(wf.own("Title"), wf.end(label, [phoenixedit.BUILTIN["Text"]]))
    wf.finish("test")
    return ui_file_bytes("WI", root)


def image_file() -> bytes:
    return ui_file_bytes(
        "IM", phoenixedit.image_asset(SCHEMA, IMAGE, "data/ui/2kimages/main/test.png", (64.0, 32.0), UIEDIT_VERSIONS)
    )


def scene_file() -> bytes:
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=UIEDIT_VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    box = scene_instance("0f561333", "Panel", (100.0, 100.0, 0.0))
    phoenixedit.set_raw(box, "Template", phoenix.ref_bytes(phoenixedit.WIDGET_TEMPLATE, WIDGET))
    phoenixedit.set_objects(root, "WidgetInstances", [box])
    return ui_file_bytes("SC", root)


def arrange_files(tmp_path, **named):

    from annotoolbox.sources import gametree
    from annotoolbox.uiedit import uifiles

    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / IMAGE).write_bytes(image_file())
    for key, root in named.items():
        (gen / key).write_bytes(ui_file_bytes("WI", root))
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))


def picture_node(name: str, **fields) -> dict:

    node = image_node(name)
    for key, value in fields.items():
        phoenixedit.set_field(node, key, value)
    return node


INNER, OUTER, LIST, ITEM = (str(uuid.uuid5(uuid.NAMESPACE_URL, n)) for n in ("inner", "outer", "list", "item"))


def root_node(guid: str) -> dict:
    return phoenixedit.new_object(SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(guid).bytes_le, root=True,
                                  versions=UIEDIT_VERSIONS)  # fmt: skip


def template_container(name: str, template: str, pos=(0.0, 0.0, 0.0)) -> dict:
    node = scene_instance("0f561333", name, pos)
    phoenixedit.set_raw(node, "Template", phoenix.ref_bytes(phoenixedit.WIDGET_TEMPLATE, template))
    return node


def image_node(name: str, pos=(0.0, 0.0, 0.0)) -> dict:
    node = scene_instance("c19f99dd", name, pos)
    phoenixedit.set_raw(node, "Image", phoenix.ref_bytes(phoenixedit.IMAGE_ASSET, IMAGE))
    return node


def inner_file() -> bytes:
    """A picture moved by an input (``Offset``), and an output (``Width`` = the picture's size x) for its placer."""
    root = root_node(INNER)
    picture = image_node("Picture")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    wf.add_instance(picture)
    wf.add_property("Offset", "vec3", (0.0, 0.0, 0.0))
    wf.add_property("Width", "float", 0.0)
    wf.bind(wf.own("Offset"), wf.end(picture, [phoenixedit.BUILTIN["Position"]]))
    wf.bind(wf.end(picture, [phoenixedit.BUILTIN["Size"]], 2), wf.own("Width"))
    wf.finish("inner")
    return ui_file_bytes("WI", root)


def outer_file() -> bytes:
    """Places the inner file twice: once moved by its input, once at the first one's output width."""
    root = root_node(OUTER)
    first = template_container("First", INNER, (100.0, 0.0, 0.0))
    second = template_container("Second", INNER)
    hidden = image_node("Hidden", (500.0, 500.0, 0.0))
    phoenixedit.set_field(hidden, "Visible", False)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    for node in (first, second, hidden):
        wf.add_instance(node)
    offset = phoenixedit.unique_id(0x10000000, "Offset")
    pv = phoenixedit.new_object(SCHEMA, "2df5f3e4", versions=UIEDIT_VERSIONS)
    phoenixedit.set_field(pv, "PropertyIdPath", [offset])
    phoenixedit.set_raw(pv, "Value", phoenix.boxed(0x1200, (0.0, 50.0, 0.0)))
    phoenixedit.set_objects(first, "PropertyValues", [pv])
    width = phoenixedit.unique_id(0x10000000, "Width")
    wf.bind(wf.end(first, [width]), wf.end(second, [phoenixedit.BUILTIN["Position"]], 2))
    wf.finish("outer")
    return ui_file_bytes("WI", root)


def list_file() -> bytes:
    """A list widget file: its one child is the item, a picture."""
    root = root_node(LIST)
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "dbcf9d78-ce36-4fd1-aaa9-acba02520297"))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    wf.add_instance(image_node("Item"))
    wf.finish("list")
    return ui_file_bytes("WI", root)


def composed_scene_file() -> bytes:
    """The outer file at (10, 20), a list of the files' own three preview elements, a label fed by game data."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=UIEDIT_VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    outer = template_container("Outer", OUTER, (10.0, 20.0, 0.0))
    rows = template_container("Rows", LIST, (0.0, 300.0, 0.0))
    values = []
    for i in range(3):
        pv = phoenixedit.new_object(SCHEMA, "2df5f3e4", versions=UIEDIT_VERSIONS)
        phoenixedit.set_field(pv, "PropertyIdPath", [0x30000001, listlayout.ELEMENT + i])
        values.append(pv)
    pv = phoenixedit.new_object(SCHEMA, "2df5f3e4", versions=UIEDIT_VERSIONS)
    phoenixedit.set_field(pv, "PropertyIdPath", [listlayout.LIST_SPACE])
    phoenixedit.set_raw(pv, "Value", phoenix.boxed(0x1000, 8.0))
    phoenixedit.set_objects(rows, "PropertyValues", [*values, pv])
    label = scene_instance("41a404e5", "Title", (0.0, 600.0, 0.0))
    phoenixedit.set_field(label, "TextPlane", (300.0, 1.0))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    for node in (outer, rows, label):
        wf.add_instance(node)
    wf.add_property("Data", "gameobject")
    wf.bind(wf.end(None, [wf.m_unique_ids["Data"], 0x2B2357A9]), wf.end(label, [phoenixedit.BUILTIN["Text"]]))
    wf.finish("scene")
    return ui_file_bytes("SC", root)


GONE = str(uuid.uuid5(uuid.NAMESPACE_URL, "a widget the file no longer has"))


KINDS = (*livelink.KINDS, livelink.VALUES)


SCENE = "composed"  # the test scene file (test_uiedit_compose), with its label Title


def link_reply(*widgets: dict, state: str = "Composed", file: str = INNER, cut: bool = False) -> dict:
    return {"file": file, "scenes": [{"state": state, "widgets": list(widgets), "cut": cut}], "cut": cut}


def settle_window(window) -> None:
    """Let the layouts run and the window take their results (and what waited for them)."""
    deadline = time.time() + 20

    def opening() -> bool:  # a file being decoded off the window's thread
        return window._m_opening is not None and (window.doc is None or window.doc.m_key != window._m_opening)

    while (window._m_placing or window._m_queued or opening()) and time.time() < deadline:
        window.m_background.join()
        window.qt.processEvents()


def open_scene(window, key: str) -> None:
    """Open a file and wait until it is shown (it is decoded off the window's thread): its first layout runs then."""
    window.open_file(key)
    deadline = time.time() + 20
    while (window.doc is None or window.doc.m_key != key) and time.time() < deadline:
        window.m_background.join()
        window.qt.processEvents()


def title_element(window) -> elements.SElement:
    return next(e for e in elements.tree(window.doc.m_root).walk() if e.name == "Title")


def take_and_use_offline(window, monkeypatch) -> None:
    """Take the values of the open widget file from a fake game, see them in the preview, then fill it from the
    recording with no game linked; used by the edition test too (tests/editor_blocked.py)."""
    from annotoolbox.uiedit import livevalues

    monkeypatch.setattr(livevalues.linkscenes, "states_for", lambda paths: ["Composed"])
    open_scene(window, SCENE)
    settle_window(window)
    title = title_element(window)
    game = FakeGame(kinds=KINDS)
    link = connect(game)
    game.answers["ui.values"] = (livelink.OK, "", link_reply({"guid": title.guid, "text": "LIVE 42"}, {"guid": GONE}))
    livelink.use(link)
    try:
        assert window.take_values_action is not None and window.values_action.isChecked()
        livevalues.take(window, is_waiting=True)
        settle_window(window)
        assert game.requests[-1] == ("ui.values", {"states": ["Composed"], "file": window.m_top.guid})
        assert any(i.kind == "text" and i.text == "LIVE 42" for i in window.m_frame.items)
        path = livevalues.recordings(window).path("anno117", window.doc.m_key)
        assert path.is_file()
    finally:
        disconnect(link)
        livelink.reset()
    window.live_held = window.live_loaded = None  # what a restart starts with
    livevalues.use_recorded(window)
    settle_window(window)
    assert window.live_held.recorded and "LIVE 42" in window._live_texts().values()
    assert any(i.kind == "text" and i.text == "LIVE 42" for i in window.m_frame.items)  # filled offline
    note = window.values_label.text()
    assert "recorded" in note and "3.0" in note and "stale: 1" in note  # the gone widget keeps its sample
    window.values_action.setChecked(False)
    settle_window(window)
    assert window._live_texts() == {}
    window.values_action.setChecked(True)
    livevalues.clear_recorded(window)
    settle_window(window)
    assert not path.is_file() and window._live_texts() == {}
    assert not any(i.kind == "text" and i.text == "LIVE 42" for i in window.m_frame.items)


CHECK, BUTTON, BAR, ROWS = (
    str(uuid.uuid5(uuid.NAMESPACE_URL, f"play-{n}")) for n in ("check", "button", "bar", "rows")
)


TYPES = {"checkbox": "16ea67bd-d62e-4221-895a-30d26507d44b", "button": "776925ee-8722-40ac-9ac9-56860aa26a94",
         "list": "dbcf9d78-ce36-4fd1-aaa9-acba02520297"}  # fmt: skip


TAB_BAR, TAB_ELEMENT = "4f14c372-0064-4999-b3be-0e4298e0bfe5", "f36d9167-2615-466e-b064-80f9e3d9976a"


BAR_UID, TAB_A, TAB_B = 0x71000001, 0x71000002, 0x71000003


CHECKED, OPEN, PLAY_ICON, CONTEXT = (
    0x21000010,
    0x21000020,
    0x21000030,
    phoenixedit.unique_id(0x10000000, "GameContext"),
)


VISIBLE = phoenixedit.BUILTIN["Visible"]


def _typed(guid: str, kind: str) -> dict:
    root = root_node(guid)
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", TYPES[kind]))
    return root


def own_property(wf, name: str, kind: str, uid: int) -> None:
    """A custom property with the UniqueId the game's widget type gives it."""
    wf.add_property(name, kind)
    phoenixedit.set_field(wf.m_properties[-1], "UniqueId", uid)
    wf.m_unique_ids[name] = uid


def _hidden(name: str, pos=(0.0, 0.0, 0.0)) -> dict:
    node = image_node(name, pos)
    phoenixedit.set_field(node, "Visible", False)
    return node


def checkbox_file() -> bytes:
    """A checkbox: its tick shows while ``IsChecked``."""
    root, box, tick = _typed(CHECK, "checkbox"), image_node("Box"), _hidden("Tick")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    for node in (box, tick):
        wf.add_instance(node)
    own_property(wf, "IsChecked", "bool", playinput.IS_CHECKED)
    wf.bind(wf.own("IsChecked"), wf.end(tick, [VISIBLE]))
    wf.finish("play-check")
    return ui_file_bytes("WI", root)


def button_file() -> bytes:
    """A button: its glow shows while the pointer is over it (its ``OnMouseEnter``)."""
    root, face, glow = _typed(BUTTON, "button"), image_node("Face"), _hidden("Glow")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    for node in (face, glow):
        wf.add_instance(node)
    own_property(wf, "OnMouseEnter", "event", playinput.ON_ENTER)
    wf.bind(wf.own("OnMouseEnter"), wf.end(glow, [VISIBLE]))
    wf.finish("play-button")
    return ui_file_bytes("WI", root)


def _tab(name: str, uid: int, x: float) -> dict:
    node = template_container(name, BUTTON, (x, 0.0, 0.0))
    phoenixedit.set_objects(
        node, "Components", [phoenixedit.component(SCHEMA, TAB_ELEMENT, "Tab", uid, UIEDIT_VERSIONS)]
    )
    return node


def bar_file() -> bytes:
    """Two tabs, each showing its page while it is the selected one."""
    root = root_node(BAR)
    tab_a, tab_b = _tab("TabA", TAB_A, 0.0), _tab("TabB", TAB_B, 100.0)
    page_a, page_b = image_node("PageA", (0.0, 100.0, 0.0)), image_node("PageB", (100.0, 100.0, 0.0))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    for node in (tab_a, tab_b, page_a, page_b):
        wf.add_instance(node)
    wf.bind(wf.end(tab_a, [TAB_A, playinput.TAB_SELECTED]), wf.end(page_a, [VISIBLE]))
    wf.bind(wf.end(tab_b, [TAB_B, playinput.TAB_SELECTED]), wf.end(page_b, [VISIBLE]))
    wf.finish("play-bar")
    return ui_file_bytes("WI", root)


def rows_file() -> bytes:
    """A list: each item shows its element's icon."""
    root = _typed(ROWS, "list")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=UIEDIT_VERSIONS)
    wf.add_instance(image_node("Item"))
    wf.bind(wf.end(4, [0x30000002, PLAY_ICON]), wf.end(3, [phoenixedit.BUILTIN["DynamicImageId"]]))
    wf.finish("play-rows")
    return ui_file_bytes("WI", root)


def scene_pictures(tmp_path, top: str, widget_files: dict[str, bytes]) -> list[tuple[float, float]]:
    """Where the pictures stand in a scene that places the widget file ``top``."""
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    scene = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=UIEDIT_VERSIONS)
    phoenixedit.set_field(scene, "ReferenceSize", (1920.0, 1080.0))
    wf = phoenixedit.CWidgetFile(SCHEMA, scene, versions=UIEDIT_VERSIONS)
    wf.add_instance(template_container("Top", top))
    wf.finish("runtime-scene")
    for key, data in ((IMAGE, image_file()), ("scene", ui_file_bytes("SC", scene)), *widget_files.items()):
        (gen / key).write_bytes(data)
    files = uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))
    frame = layout.place("scene", files.root("scene"), layout.CContext(files, viewmodels.CSamples()))
    return [layout.apply(i.matrix, 0, 0) for i in frame.items if i.kind == "image"]
