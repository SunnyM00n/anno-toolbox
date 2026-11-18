"""The UI editor's Play mode on a synthetic scene: a hover state, a tab switch, a checkbox writing its setting with
``play=True``, a list scroll, a scene link, a reset of the Play mode choices, Design mode writing nothing, and a hover
placing again only the files on the way to the widget. docs/ui/117/game-data.md "Play mode"."""

from __future__ import annotations

import os
import uuid

import pytest
from tests.test_uiedit import GEN, IMAGE, SCHEMA, VERSIONS, _write, image_file
from tests.test_uiedit_compose import _container, _image, _root

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import gamedata, layout, listlayout, playinput, playstate, scope, uifiles, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
CHECK, BUTTON, BAR, ROWS = (
    str(uuid.uuid5(uuid.NAMESPACE_URL, f"play-{n}")) for n in ("check", "button", "bar", "rows")
)
SCENE, TARGET = "playscene", "targetscene"
TYPES = {"checkbox": "16ea67bd-d62e-4221-895a-30d26507d44b", "button": "776925ee-8722-40ac-9ac9-56860aa26a94",
         "list": "dbcf9d78-ce36-4fd1-aaa9-acba02520297"}  # fmt: skip
TAB_BAR, TAB_ELEMENT = "4f14c372-0064-4999-b3be-0e4298e0bfe5", "f36d9167-2615-466e-b064-80f9e3d9976a"
BAR_UID, TAB_A, TAB_B = 0x71000001, 0x71000002, 0x71000003
CHECKED, OPEN, ICON, CONTEXT = 0x21000010, 0x21000020, 0x21000030, phoenixedit.unique_id(0x10000000, "GameContext")
CHECKED_KEY = f"{SCENE}/{CONTEXT:08x}/{CHECKED:08x}"
NAMES = {CHECKED: "Checked", OPEN: "OpenTarget", ICON: "Icon", CONTEXT: "GameContext"}
VISIBLE = phoenixedit.BUILTIN["Visible"]


def _typed(guid: str, kind: str) -> dict:
    root = _root(guid)
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", TYPES[kind]))
    return root


def _own(wf, name: str, kind: str, uid: int) -> None:
    """A custom property with the UniqueId the game's widget type gives it."""
    wf.add_property(name, kind)
    phoenixedit.set_field(wf.properties[-1], "UniqueId", uid)
    wf.uids[name] = uid


def _hidden(name: str, pos=(0.0, 0.0, 0.0)) -> dict:
    node = _image(name, pos)
    phoenixedit.set_field(node, "Visible", False)
    return node


def checkbox_file() -> bytes:
    """A checkbox: its tick shows while ``IsChecked``."""
    root, box, tick = _typed(CHECK, "checkbox"), _image("Box"), _hidden("Tick")
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (box, tick):
        wf.add_instance(node)
    _own(wf, "IsChecked", "bool", playinput.IS_CHECKED)
    wf.bind(wf.own("IsChecked"), wf.end(tick, [VISIBLE]))
    wf.finish("play-check")
    return _write("WI", root)


def button_file() -> bytes:
    """A button: its glow shows while the pointer is over it (its ``OnMouseEnter``)."""
    root, face, glow = _typed(BUTTON, "button"), _image("Face"), _hidden("Glow")
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (face, glow):
        wf.add_instance(node)
    _own(wf, "OnMouseEnter", "event", playinput.ON_ENTER)
    wf.bind(wf.own("OnMouseEnter"), wf.end(glow, [VISIBLE]))
    wf.finish("play-button")
    return _write("WI", root)


def _tab(name: str, uid: int, x: float) -> dict:
    node = _container(name, BUTTON, (x, 0.0, 0.0))
    phoenixedit.set_objects(node, "Components", [phoenixedit.component(SCHEMA, TAB_ELEMENT, "Tab", uid, VERSIONS)])
    return node


def bar_file() -> bytes:
    """Two tabs, each showing its page while it is the selected one."""
    root = _root(BAR)
    tab_a, tab_b = _tab("TabA", TAB_A, 0.0), _tab("TabB", TAB_B, 100.0)
    page_a, page_b = _image("PageA", (0.0, 100.0, 0.0)), _image("PageB", (100.0, 100.0, 0.0))
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (tab_a, tab_b, page_a, page_b):
        wf.add_instance(node)
    wf.bind(wf.end(tab_a, [TAB_A, playinput.TAB_SELECTED]), wf.end(page_a, [VISIBLE]))
    wf.bind(wf.end(tab_b, [TAB_B, playinput.TAB_SELECTED]), wf.end(page_b, [VISIBLE]))
    wf.finish("play-bar")
    return _write("WI", root)


def rows_file() -> bytes:
    """A list: each item shows its element's icon."""
    root = _typed(ROWS, "list")
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(_image("Item"))
    wf.bind(wf.end(4, [0x30000002, ICON]), wf.end(3, [phoenixedit.BUILTIN["DynamicImageId"]]))
    wf.finish("play-rows")
    return _write("WI", root)


def scene_file() -> bytes:
    """The checkbox written back to ``GameContext.Checked``, the tab bar, a list of four of which two show, a button
    whose release goes to ``GameContext.OpenTarget``."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    check, bar = _container("Check", CHECK, (0.0, 0.0, 0.0)), _container("Bar", BAR, (300.0, 0.0, 0.0))
    phoenixedit.set_objects(bar, "Components", [phoenixedit.component(SCHEMA, TAB_BAR, "Tabs", BAR_UID, VERSIONS)])
    rows, button = _container("Rows", ROWS, (0.0, 400.0, 0.0)), _container("Open", BUTTON, (800.0, 0.0, 0.0))
    values = [phoenixedit.property_value(SCHEMA, [0x30000001, listlayout.ELEMENT + i, ICON], "string", f"pic{i}",
                                         VERSIONS) for i in range(4)]  # fmt: skip
    values.append(phoenixedit.property_value(SCHEMA, [listlayout.LIST_VISIBLE], "float", 2.0, VERSIONS))
    phoenixedit.set_objects(rows, "PropertyValues", values)
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (check, bar, rows, button):
        wf.add_instance(node)
    _own(wf, "GameContext", "gameobject", CONTEXT)
    wf.bind(wf.end(None, [CONTEXT, CHECKED]), wf.end(check, [playinput.IS_CHECKED]))
    wf.bind(wf.end(button, [playinput.ON_RELEASE]), wf.end(None, [CONTEXT, OPEN]))
    wf.finish("play-scene")
    two_way = phoenixedit.new_object(SCHEMA, playinput.TWO_WAY, versions=VERSIONS)
    phoenixedit.set_objects(phoenixedit.objects(root, "a4336119")[0], "ConverterInstance", [two_way])
    return _write("SC", root)


def target_file() -> bytes:
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(root, "WidgetInstances", [_image("Somewhere")])
    return _write("SC", root)


@pytest.fixture
def tree(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, data in ((CHECK, checkbox_file()), (BUTTON, button_file()), (BAR, bar_file()), (ROWS, rows_file()),
                      (IMAGE, image_file()), (SCENE, scene_file()), (TARGET, target_file())):  # fmt: skip
        (gen / key).write_bytes(data)
    return gametree.loose_tree(tmp_path / "game")


class Played:
    """The scene laid out with Play mode's values, as the window does it, without the window."""

    def __init__(self, tree) -> None:
        self.files = uifiles.UiFiles(tree)
        self.ctx = layout.Context(self.files, viewmodels.Samples())
        self.ctx.samples.set(CHECKED_KEY, False)  # the setting's sample: unchecked
        game = gamedata.GameData(gamedata.Assets.from_xml({}), gamedata.FieldTable(NAMES))
        game.scene = SCENE
        gamedata.attach(self.ctx, game)
        self.ctx.play, self.ctx.play_choices = {}, playstate.Choices()
        self.state = playstate.PlayState()
        self.frame = self.place()

    def place(self) -> layout.Frame:
        self.ctx.play = self.state.trees(SCENE)
        self.frame = layout.place(SCENE, self.files.root(SCENE), self.ctx)
        return self.frame

    def hit(self, *names: str):
        return next(h for h in self.frame.hits if tuple(_name(self, t) for t in h.trail) == names)

    def centre(self, *names: str) -> tuple[float, float]:
        h = self.hit(*names)
        return layout.apply(h.matrix, h.size[0] / 2, h.size[1] / 2)

    def keep(self, outcome: playinput.Outcome) -> None:
        for trail, path, value in outcome.kept:
            self.state.keep(SCENE, trail, path, value)


def _name(played: Played, step) -> str:
    guid = step[0]
    for key in (SCENE, CHECK, BUTTON, BAR, ROWS):
        found = next((e.name for e in scope.root_info(played.ctx, played.files.root(key)).top.walk() if e.guid == guid),
                     None)  # fmt: skip
        if found:
            return found
    return "?"


def _images(played: Played, prefix: str = "") -> list[str]:
    return [i.image_id for i in played.frame.items if i.kind == "image" and (i.image_id or "").startswith(prefix)]


def _glows(played: Played) -> int:
    """The button pictures drawn: a face each, and a glow while hovered (both the test picture)."""
    return sum(1 for i in played.frame.items if i.kind == "image" and not i.image_id)


def test_a_hover_sets_the_buttons_flags_and_only_its_files_are_placed_again(tree, monkeypatch):
    played = Played(tree)
    before = _glows(played)
    hit = played.hit("Open")
    assert playinput.kinds(played.ctx, hit) >= {"button"}
    made: list[str] = []
    original = scope.Scope.__init__

    def counted(self, key, *args, **kwargs):
        made.append(key)
        original(self, key, *args, **kwargs)

    monkeypatch.setattr(scope.Scope, "__init__", counted)
    played.state.passing = {hit.trail: playinput.hover_values(played.ctx, hit)}
    played.place()
    assert _glows(played) == before + 1  # the glow of the hovered button, not of the tabs' buttons
    assert set(made) <= {SCENE, BUTTON}  # the checkbox, the tabs and the list were kept
    played.state.passing = {}
    played.place()
    assert _glows(played) == before


def test_a_tab_click_selects_it_and_switches_the_pages(tree):
    played = Played(tree)
    pages = {i.matrix for i in played.frame.items if i.kind == "image"}
    at = played.centre("Bar", "TabB")
    outcome = playinput.click(played.ctx, played.frame, playinput.under(played.frame, *at), at, SCENE)
    selected = {trail[-1]: value for trail, path, value in outcome.kept if path[1] == playinput.TAB_SELECTED}
    assert sorted(selected.values()) == [False, True]
    bar = played.hit("Bar")
    assert (bar.trail, (BAR_UID, playinput.SELECTED_TAB), 1) in outcome.kept  # the bar's SelectedTabID
    played.keep(outcome)
    played.place()
    after = {i.matrix for i in played.frame.items if i.kind == "image"}
    assert len(after) == len(pages) - 1  # page A is hidden now, page B shows


def test_a_checkbox_flips_and_writes_its_setting_with_play(tree):
    played = Played(tree)
    at = played.centre("Check")
    outcome = playinput.click(played.ctx, played.frame, playinput.under(played.frame, *at), at, SCENE)
    name = "options.play.playscene.GameContext.Checked"
    assert outcome.choices == {name: True} and outcome.keys == [f"{SCENE}/{CONTEXT:08x}/{CHECKED:08x}"]
    count = len(played.frame.items)
    played.ctx.play_choices = playstate.Choices({name.removeprefix("options.play."): True})
    played.ctx.memo.forget((playstate.TOKEN, name.removeprefix("options.play.")))
    played.place()
    assert len(played.frame.items) == count + 1  # the tick shows: the choice is read back through the binding


def test_the_wheel_scrolls_a_list_to_its_later_elements(tree):
    played = Played(tree)
    assert _images(played, "pic") == ["pic0", "pic1"]
    hits = playinput.under(played.frame, *played.centre("Rows", "Item"))
    found = playinput.scroll(played.ctx, played.frame, hits, 1, 0)
    assert found is not None and found[1] == 1
    played.state.scroll(SCENE, *found)
    played.place()
    assert _images(played, "pic") == ["pic1", "pic2"]
    hits = playinput.under(played.frame, *played.centre("Rows", "Item"))
    assert playinput.scroll(played.ctx, played.frame, hits, 5, 1)[1] == 2  # no further than the last two


def test_a_button_into_an_open_field_opens_that_scene(tree):
    played = Played(tree)
    at = played.centre("Open")
    outcome = playinput.click(played.ctx, played.frame, playinput.under(played.frame, *at), at, SCENE)
    assert outcome.open == TARGET and TARGET in outcome.note
    assert playinput._scene_of(played.ctx, "x.GameContext.OpenNowhere", SCENE)[0] == ""  # no file: a note only


@pytest.fixture
def window(tree, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.settings import Settings
    from annotoolbox.uiedit.uiwindow import UiWindow, _Game

    qt = QApplication.instance() or QApplication([])
    w = UiWindow(Settings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    w.sync_layout = True
    w._opened("anno117", "", _Game("anno117", tree, None))
    w.samples.set(CHECKED_KEY, False)
    w.game_data = gamedata.GameData(gamedata.Assets.from_xml({}), gamedata.FieldTable(NAMES))  # the fields' names
    gamedata.attach(w.ctx, w.game_data)
    w.open_file(SCENE)
    yield w
    for doc in w.docs.values():
        doc.undo.mark_clean()
    w.close()
    qt.processEvents()


def _point(window, *names: str):
    from PySide6.QtCore import QPointF

    hit = next(h for h in window.frame.hits if [s[0] for s in h.trail] == [_guid(window, n) for n in names])
    x, y = layout.apply(hit.matrix, hit.size[0] / 2, hit.size[1] / 2)
    return QPointF(x, y)


def _guid(window, name: str) -> str:
    for key in (SCENE, CHECK, BUTTON, BAR, ROWS):
        root = window.game.files.root(key)
        found = next((e.guid for e in scope.root_info(window.ctx, root).top.walk() if e.name == name), None)
        if found:
            return found
    raise KeyError(name)


def _click(window, at) -> None:
    from PySide6.QtCore import QPointF

    for kind in ("move", "press", "release"):
        window.play.mouse(kind, at, QPointF())


def test_the_window_plays_writes_the_choice_and_resets_it(window):
    store, play = window.preview_store, window.play
    assert not play.active and window.ctx.play is None
    window.play_mode_action.trigger()
    assert play.active and window.ctx.play is not None
    _click(window, _point(window, "Check"))
    name = "options.play.playscene.GameContext.Checked"
    assert name in store.played and store.defaults.options[name.removeprefix("options.")] is True
    panel = window.preview_panel
    assert panel.played.topLevelItemCount() == 1 and panel.reset_played.isEnabled()  # the panel shows it
    assert not window.doc.dirty  # Play mode edits nothing
    count = len(window.frame.items)
    window.reset_play_action.trigger()
    assert not store.play_names() and not play.state.kept and panel.played.topLevelItemCount() == 0
    assert len(window.frame.items) == count - 1  # the tick is gone again
    window.design_mode_action.trigger()
    assert not play.active and window.ctx.play is None


def test_design_mode_writes_nothing_and_play_mode_opens_a_linked_scene(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    store, preview = window.preview_store, window.preview
    at = _point(window, "Check")
    on_widget = QPoint(
        round(preview.offset.x() + at.x() * preview.zoom), round(preview.offset.y() + at.y() * preview.zoom)
    )
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=on_widget)
    assert window.selected is not None and window.selected.name == "Check"  # Design mode: a click selects
    assert not store.play_names() and not window.play.state.kept and not store.defaults.options
    window.play_mode_action.trigger()
    _click(window, _point(window, "Open"))
    assert window.doc.key == TARGET and window.play.active  # the scene its button opens, still in Play mode
