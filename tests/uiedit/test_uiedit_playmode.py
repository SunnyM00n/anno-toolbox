"""The UI editor's Play mode on a synthetic scene: a hover state, a tab switch, a checkbox writing its setting with
``play=True``, a list scroll, a scene link, a reset of the Play mode choices, Design mode writing nothing, and a hover
placing again only the files on the way to the widget. docs/ui/117/game-data.md "Play mode"."""

from __future__ import annotations

import os
import uuid

import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import (
    BAR,
    BAR_UID,
    BUTTON,
    CHECK,
    CHECKED,
    CONTEXT,
    GEN,
    IMAGE,
    OPEN,
    ROWS,
    TAB_BAR,
    bar_file,
    button_file,
    checkbox_file,
    image_file,
    image_node,
    own_property,
    rows_file,
    template_container,
)
from tests.uiedit.uieditfakes import PLAY_ICON as ICON
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import gamedata, layout, listlayout, playinput, playstate, scope, uifiles, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SCENE, TARGET = "playscene", "targetscene"
CHECKED_KEY = f"{SCENE}/{CONTEXT:08x}/{CHECKED:08x}"
NAMES = {CHECKED: "Checked", OPEN: "OpenTarget", ICON: "Icon", CONTEXT: "GameContext"}


def scene_file() -> bytes:
    """The checkbox written back to ``GameContext.Checked``, the tab bar, a list of four of which two show, a button
    whose release goes to ``GameContext.OpenTarget``."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    check, bar = template_container("Check", CHECK, (0.0, 0.0, 0.0)), template_container("Bar", BAR, (300.0, 0.0, 0.0))
    phoenixedit.set_objects(bar, "Components", [phoenixedit.component(SCHEMA, TAB_BAR, "Tabs", BAR_UID, VERSIONS)])
    rows, button = (
        template_container("Rows", ROWS, (0.0, 400.0, 0.0)),
        template_container("Open", BUTTON, (800.0, 0.0, 0.0)),
    )
    values = [phoenixedit.property_value(SCHEMA, [0x30000001, listlayout.ELEMENT + i, ICON], "string", f"pic{i}",
                                         VERSIONS) for i in range(4)]  # fmt: skip
    values.append(phoenixedit.property_value(SCHEMA, [listlayout.LIST_VISIBLE], "float", 2.0, VERSIONS))
    phoenixedit.set_objects(rows, "PropertyValues", values)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (check, bar, rows, button):
        wf.add_instance(node)
    own_property(wf, "GameContext", "gameobject", CONTEXT)
    wf.bind(wf.end(None, [CONTEXT, CHECKED]), wf.end(check, [playinput.IS_CHECKED]))
    wf.bind(wf.end(button, [playinput.ON_RELEASE]), wf.end(None, [CONTEXT, OPEN]))
    wf.finish("play-scene")
    two_way = phoenixedit.new_object(SCHEMA, playinput.TWO_WAY, versions=VERSIONS)
    phoenixedit.set_objects(phoenixedit.objects(root, "a4336119")[0], "ConverterInstance", [two_way])
    return _write("SC", root)


def target_file() -> bytes:
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(root, "WidgetInstances", [image_node("Somewhere")])
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
        self.files = uifiles.CUiFiles(tree)
        self.ctx = layout.CContext(self.files, viewmodels.CSamples())
        self.ctx.m_samples.set(CHECKED_KEY, False)  # the setting's sample: unchecked
        game = gamedata.CGameData(gamedata.CAssets.from_xml({}), gamedata.CFieldTable(NAMES))
        game.m_scene = SCENE
        gamedata.attach(self.ctx, game)
        self.ctx.m_play, self.ctx.m_play_choices = {}, playstate.SChoices()
        self.state = playstate.CPlayState()
        self.frame = self.place()

    def place(self) -> layout.SFrame:
        self.ctx.m_play = self.state.trees(SCENE)
        self.frame = layout.place(SCENE, self.files.root(SCENE), self.ctx)
        return self.frame

    def hit(self, *names: str):
        return next(h for h in self.frame.hits if tuple(_name(self, t) for t in h.trail) == names)

    def centre(self, *names: str) -> tuple[float, float]:
        h = self.hit(*names)
        return layout.apply(h.matrix, h.size[0] / 2, h.size[1] / 2)

    def keep(self, outcome: playinput.SOutcome) -> None:
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
    original = scope.CScope.__init__

    def counted(self, key, *args, **kwargs):
        made.append(key)
        original(self, key, *args, **kwargs)

    monkeypatch.setattr(scope.CScope, "__init__", counted)
    played.state.m_passing = {hit.trail: playinput.hover_values(played.ctx, hit)}
    played.place()
    assert _glows(played) == before + 1  # the glow of the hovered button, not of the tabs' buttons
    assert set(made) <= {SCENE, BUTTON}  # the checkbox, the tabs and the list were kept
    played.state.m_passing = {}
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
    played.ctx.m_play_choices = playstate.SChoices({name.removeprefix("options.play."): True})
    played.ctx.m_memo.forget((playstate.TOKEN, name.removeprefix("options.play.")))
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

    from annotoolbox.suite.settings import CSettings
    from annotoolbox.uiedit.uiwindow import CUiWindow, _CGame

    qt = QApplication.instance() or QApplication([])
    w = CUiWindow(CSettings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    w.m_sync_layout = True
    w._opened("anno117", "", _CGame("anno117", tree, None))
    w.m_samples.set(CHECKED_KEY, False)
    w.game_data = gamedata.CGameData(gamedata.CAssets.from_xml({}), gamedata.CFieldTable(NAMES))  # the fields' names
    gamedata.attach(w.m_ctx, w.game_data)
    w.open_file(SCENE)
    yield w
    for doc in w.m_docs.values():
        doc.m_undo.mark_clean()
    w.close()
    qt.processEvents()


def _point(window, *names: str):
    from PySide6.QtCore import QPointF

    hit = next(h for h in window.m_frame.hits if [s[0] for s in h.trail] == [_guid(window, n) for n in names])
    x, y = layout.apply(hit.matrix, hit.size[0] / 2, hit.size[1] / 2)
    return QPointF(x, y)


def _guid(window, name: str) -> str:
    for key in (SCENE, CHECK, BUTTON, BAR, ROWS):
        root = window.game.m_files.root(key)
        found = next((e.guid for e in scope.root_info(window.m_ctx, root).top.walk() if e.name == name), None)
        if found:
            return found
    raise KeyError(name)


def _click(window, at) -> None:
    from PySide6.QtCore import QPointF

    for kind in ("move", "press", "release"):
        window.play.mouse(kind, at, QPointF())


def test_the_window_plays_writes_the_choice_and_resets_it(window):
    store, play = window.preview_store, window.play
    assert not play.m_active and window.m_ctx.m_play is None
    window.play_mode_action.trigger()
    assert play.m_active and window.m_ctx.m_play is not None
    _click(window, _point(window, "Check"))
    name = "options.play.playscene.GameContext.Checked"
    assert name in store.m_played and store.m_defaults.options[name.removeprefix("options.")] is True
    panel = window.preview_panel
    assert panel.m_played.topLevelItemCount() == 1 and panel.m_reset_played.isEnabled()  # the panel shows it
    assert not window.doc.dirty  # Play mode edits nothing
    count = len(window.m_frame.items)
    window.reset_play_action.trigger()
    assert not store.play_names() and not play.m_state.m_kept and panel.m_played.topLevelItemCount() == 0
    assert len(window.m_frame.items) == count - 1  # the tick is gone again
    window.design_mode_action.trigger()
    assert not play.m_active and window.m_ctx.m_play is None


def test_design_mode_writes_nothing_and_play_mode_opens_a_linked_scene(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    store, preview = window.preview_store, window.preview
    at = _point(window, "Check")
    on_widget = QPoint(
        round(preview.m_offset.x() + at.x() * preview.m_zoom), round(preview.m_offset.y() + at.y() * preview.m_zoom)
    )
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, pos=on_widget)
    assert window.m_selected is not None and window.m_selected.name == "Check"  # Design mode: a click selects
    assert not store.play_names() and not window.play.m_state.m_kept and not store.m_defaults.options
    window.play_mode_action.trigger()
    _click(window, _point(window, "Open"))
    assert window.doc.m_key == TARGET and window.play.m_active  # the scene its button opens, still in Play mode
