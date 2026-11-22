"""Play mode, round 2, on synthetic scenes: a change placed again alone (and the same as a whole layout), a list's
copies given only their own values, a scroll bar's and a draggable button's drag, the focus walk by direction, an
animation a hover starts played over time, a toggle's entries and a combo box's list. docs/ui/117/game-data.md
"Play mode"."""

from __future__ import annotations

import dataclasses
import os
import uuid

import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import (
    BAR,
    BUTTON,
    CHECK,
    GEN,
    IMAGE,
    ROWS,
    TYPES,
    bar_file,
    button_file,
    checkbox_file,
    image_file,
    image_node,
    own_property,
    root_node,
    rows_file,
    template_container,
)
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import animations, layout, listlayout, playinput, playstate, scope, uifiles, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
KINDS = {"scrollbar": "ad55d6e7-3783-4574-87fd-bcc313e2f606", "draggable": "71b842da-47a7-4343-b130-8e073185d7f1",
         "switch": "8b647527-7eef-42c3-8a5f-a20299b72760", "combo": "4464ab4e-3922-4985-a10d-3ee41b269cf2"}  # fmt: skip
SCROLLBAR, PIN, SWITCH, COMBO, GLOWING = (str(uuid.uuid5(uuid.NAMESPACE_URL, f"play2-{n}"))
                                          for n in ("bar", "pin", "switch", "combo", "glow"))  # fmt: skip
SCENE, ANIMATED = "play2scene", "glowscene"
ROWS_SCROLL, OPTIONS = 0x21000040, 0x21000050
ALPHA, START = phoenixedit.BUILTIN["AlphaTint"], playstate.START


def _typed(guid: str, kind: str) -> dict:
    root = root_node(guid)
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", {**TYPES, **KINDS}[kind]))
    return root


def _plain(guid: str, kind: str, *children: dict) -> bytes:
    root = _typed(guid, kind)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in children:
        wf.add_instance(node)
    wf.finish(f"play2-{kind}")
    return _write("WI", root)


def scrollbar_file() -> bytes:
    """A scroll bar: a track 320 high and a handle at its top."""
    track = image_node("Track")
    phoenixedit.set_field(track, "Scale", (0.5, 10.0, 1.0))
    return _plain(SCROLLBAR, "scrollbar", track, template_container("Handle", BUTTON))


def _values(node: dict, *values: tuple) -> dict:
    phoenixedit.set_objects(node, "PropertyValues",
                            [phoenixedit.property_value(SCHEMA, list(p), k, v, VERSIONS) for p, k, v in values])  # fmt: skip
    return node


def scene_file() -> bytes:
    """A list of four (two shown) and the scroll bar its ScrollInfo is bound to, a draggable pin that may move 100
    to the right, a toggle and a combo box with the scene's three options, and four checkboxes in a cross (focus)."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    rows, bar = (
        template_container("Rows", ROWS, (0.0, 400.0, 0.0)),
        template_container("Bar", SCROLLBAR, (600.0, 400.0, 0.0)),
    )
    items = [((0x30000001, listlayout.ELEMENT + i, 0x21000030), "string", f"pic{i}") for i in range(4)]
    _values(rows, *items, ((listlayout.LIST_VISIBLE,), "float", 2.0))
    pin = _values(template_container("Pin", PIN, (900.0, 0.0, 0.0)), ((playinput.MIN_POS,), "vec2", (900.0, 0.0)),
                  ((playinput.MAX_POS,), "vec2", (1000.0, 0.0)))  # fmt: skip
    switch, combo = (
        template_container("Switch", SWITCH, (0.0, 800.0, 0.0)),
        template_container("Combo", COMBO, (300.0, 800.0, 0.0)),
    )
    cross = [template_container(n, CHECK, p) for n, p in (("Mid", (1400.0, 500.0, 0.0)), ("Up", (1400.0, 300.0, 0.0)),
             ("Right", (1700.0, 520.0, 0.0)), ("Far", (1900.0, 500.0, 0.0)))]  # fmt: skip
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (rows, bar, pin, switch, combo, *cross):
        wf.add_instance(node)
    own_property(wf, "Options", "gameobject", OPTIONS)
    wf.bind(wf.end(rows, [ROWS_SCROLL]), wf.end(bar, [playinput.SCROLL_INFO]))
    wf.bind(wf.end(None, [OPTIONS]), wf.end(switch, [playinput.SWITCH_ENTRIES]))
    wf.bind(wf.end(None, [OPTIONS]), wf.end(combo, [playinput.COMBO_ENTRIES]))
    wf.finish("play2-scene")
    return _write("SC", root)


def glowing_file() -> bytes:
    """A button whose glow fades from 0.2 to 1 in a second, an animation its ``OnMouseEnter`` starts."""
    root, face, glow = _typed(GLOWING, "button"), image_node("Face"), image_node("Glow")
    anim = phoenixedit.new_object(SCHEMA, "b03e911f", guid=uuid.uuid5(uuid.NAMESPACE_URL, "glow-in").bytes_le,
                                  versions=VERSIONS)  # fmt: skip
    phoenixedit.set_name(anim, "Glow_In")
    phoenixedit.set_field(anim, "FrameRate", 60)
    phoenixedit.set_field(anim, "Duration", 60)
    track = phoenixedit.new_object(SCHEMA, "e613cfd0", versions=VERSIONS)
    phoenixedit.set_field(track, "AnimatedPropertyIndex", 0)
    keys = []
    for t, v in ((0.0, 0.2), (1.0, 1.0)):
        k = phoenixedit.new_object(SCHEMA, "32f7d270", versions=VERSIONS)
        phoenixedit.set_field(k, "Time", t)
        phoenixedit.set_field(k, "Value", v)
        phoenixedit.set_field(k, animations.INTERPOLATOR, 1)  # linear
        keys.append(k)
    phoenixedit.set_objects(track, "Track", keys)
    phoenixedit.set_objects(anim, "Tracks", [track])
    phoenixedit.set_objects(root, "AnimationInstances", [anim])
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (face, glow):
        wf.add_instance(node)
    alpha = wf.end(glow, [ALPHA])  # end 0: the track's
    own_property(wf, "OnMouseEnter", "event", playinput.ON_ENTER)
    start = wf.end(2, [START])
    item = phoenixedit.objects(wf.m_data_objects[-1], "15a30137")[0]
    phoenixedit.set_raw(item, "b25f63de", phoenix.ref_bytes(phoenixedit.INSTANCE_REF, anim["guid"]))
    wf.bind(wf.own("OnMouseEnter"), start)
    wf.bind(wf.end(None, [0x21000099]), alpha)  # keeps end 0 in use
    wf.finish("play2-glow")
    return _write("WI", root)


def animated_scene() -> bytes:
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(root, "WidgetInstances", [template_container("Holder", BAR, (0.0, 0.0, 0.0)),
                                                      template_container("Glowing", GLOWING, (500.0, 0.0, 0.0))])  # fmt: skip
    return _write("SC", root)


@pytest.fixture
def tree(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    files = {CHECK: checkbox_file(), BUTTON: button_file(), BAR: bar_file(), ROWS: rows_file(), IMAGE: image_file(),
             SCROLLBAR: scrollbar_file(), PIN: _plain(PIN, "draggable", image_node("Face")), SCENE: scene_file(),
             SWITCH: _plain(SWITCH, "switch", image_node("Face")), COMBO: _plain(COMBO, "combo", image_node("Face")),
             GLOWING: glowing_file(), ANIMATED: animated_scene()}  # fmt: skip
    for key, data in files.items():
        (gen / key).write_bytes(data)
    return gametree.loose_tree(tmp_path / "game")


class Played:
    """A scene laid out with Play mode's values, as the window does it."""

    def __init__(self, tree, key: str = SCENE) -> None:
        self.key, self.files = key, uifiles.CUiFiles(tree)
        self.ctx = layout.CContext(self.files, viewmodels.CSamples())
        for i in range(3):
            self.ctx.m_samples.set(f"{key}/{OPTIONS:08x}/{listlayout.ELEMENT + i:08x}/21000001", f"option {i}")
        self.state = playstate.CPlayState()
        self.ctx.m_play = self.state.trees(key)
        self.frame = layout.place(key, self.files.root(key), self.ctx)

    def hit(self, *names: str):
        return next(h for h in self.frame.hits if tuple(_name(self, s) for s in h.trail) == names)

    def centre(self, *names: str) -> tuple[float, float]:
        h = self.hit(*names)
        return layout.apply(h.matrix, h.size[0] / 2, h.size[1] / 2)

    def keep(self, outcome: playinput.SOutcome) -> None:
        for trail, path, value in outcome.kept:
            self.state.keep(self.key, trail, path, value)
        for trail, first in outcome.scrolls:
            self.state.scroll(self.key, trail, first)

    def place(self, ages=None) -> layout.SFrame:
        self.ctx.m_play = self.state.trees(self.key, ages)
        self.frame = layout.place(self.key, self.files.root(self.key), self.ctx)
        return self.frame


def _name(played: Played, step) -> str:
    for key in played.files.m_entries:
        root = played.files.root(key)
        found = next((e.name for e in scope.root_info(played.ctx, root).top.walk() if e.guid == step[0]), None)
        if found:
            return found
    return "?"


def _look(frame) -> list:
    return [(i.kind, tuple(round(v, 3) for v in i.matrix), i.image_id, round(i.alpha, 3)) for i in frame.items]


def test_a_nested_hover_is_placed_again_alone_and_matches_a_whole_layout(tree, monkeypatch):
    played = Played(tree, ANIMATED)
    old = played.ctx.m_play
    tab = played.hit("Holder", "TabB")
    played.state.m_passing = {tab.trail: playinput.hover_values(played.ctx, tab)}
    new = played.state.trees(ANIMATED)
    assert playstate.changed(old[ANIMATED], new[ANIMATED]) == [tab.trail[:-1]]  # the bar's placing holds the tab
    made: list[str] = []
    original = scope.CScope.__init__

    def counted(self, key, *args, **kwargs):
        made.append(key)
        original(self, key, *args, **kwargs)

    monkeypatch.setattr(scope.CScope, "__init__", counted)
    again = playstate.replay(played.ctx, ANIMATED, played.files.root(ANIMATED), played.frame, old, new)
    assert set(made) <= {BAR, BUTTON}  # the scene and the other button were not placed again
    whole = layout.place(ANIMATED, played.files.root(ANIMATED), played.ctx)
    assert _look(again) == _look(whole) and len(again.hits) == len(whole.hits)
    assert _look(again) != _look(played.frame)  # the hovered tab's glow shows


def test_a_list_copy_takes_only_its_own_play_values():
    child = playstate.SPlayNode(((("x",), 1),))
    node = playstate.SPlayNode((), ((("item", 1), child),))
    given = {5: "a", playstate.PLAY_UID: playstate.SPlayInput(node, ((5, "a"),)), listlayout.TOTAL_ITEMS: 4}
    slot = scope.SListSlot(scope.SGameObjectRef("k/0"), 0, "item")
    assert playstate.PLAY_UID not in playstate.narrow(given, slot)  # copy 0 is as designed: kept from Design mode
    mine = playstate.narrow(given, scope.SListSlot(scope.SGameObjectRef("k/1"), 1, "item"))
    assert mine[playstate.PLAY_UID].node.children == node.children


def test_a_scroll_bar_drag_scrolls_its_list_and_moves_the_handle(tree):
    played = Played(tree)
    assert [i.image_id for i in played.frame.items if (i.image_id or "").startswith("pic")] == ["pic0", "pic1"]
    bar = played.hit("Bar")
    assert playinput.kinds(played.ctx, bar) >= {"scrollbar"}
    assert playinput.scrolled_by(played.frame, bar).trail == played.hit("Rows").trail  # through its ScrollInfo
    top = layout.apply(bar.matrix, 10.0, 0.0)
    drag = playinput.begin_drag(played.ctx, played.frame, playinput.under(played.frame, *top), top)
    assert drag is not None and drag.kind == "scrollbar"
    out = playinput.drag_to(played.ctx, played.frame, drag, layout.apply(bar.matrix, 10.0, bar.size[1]), {})
    assert out.scrolls == [(played.hit("Rows").trail, 2)]  # the last two shown
    (handle,) = [k for k in out.kept if k[1] == (playinput.POSITION,)]
    assert handle[0] == played.hit("Bar", "Handle").trail and handle[2][1] == pytest.approx(320.0 - 32.0)
    played.keep(out)
    played.place()
    assert [i.image_id for i in played.frame.items if (i.image_id or "").startswith("pic")] == ["pic2", "pic3"]


def test_a_draggable_button_moves_within_its_range(tree):
    played = Played(tree)
    pin = played.hit("Pin")
    at = played.centre("Pin")
    drag = playinput.begin_drag(played.ctx, played.frame, playinput.under(played.frame, *at), at)
    assert drag is not None and drag.kind == "draggable" and drag.origin == (900.0, 0.0)
    out = playinput.drag_to(played.ctx, played.frame, drag, (at[0] + 40.0, at[1] + 30.0), {})
    assert out.kept == [(pin.trail, (playinput.POSITION,), (940.0, 0.0, 0.0))]  # the range holds y
    out = playinput.drag_to(played.ctx, played.frame, drag, (at[0] + 400.0, at[1]), {})
    assert out.kept[0][2][0] == 1000.0  # no further than its MaxPosition
    played.keep(out)
    played.place()
    assert played.hit("Pin").matrix[4] == pytest.approx(1000.0)


def test_arrow_keys_move_the_focus_to_the_nearest_widget_that_way(tree):
    played = Played(tree)
    order = playinput.focusable(played.ctx, played.frame)
    mid = played.hit("Mid")
    for direction, name in (("up", "Up"), ("right", "Right")):  # Right: off the line, but nearer than Far
        found = playinput.neighbour(played.ctx, played.frame, mid, direction, order)
        assert found is not None and found.trail == played.hit(name).trail, direction
    assert playinput.neighbour(played.ctx, played.frame, played.hit("Far"), "right", order) is None
    assert playinput.neighbour(played.ctx, played.frame, None, "down", order) is order[0]


def test_a_hover_animation_plays_over_time(tree):
    played = Played(tree, ANIMATED)

    def glow() -> float:
        return min(i.alpha for i in played.frame.items if i.kind == "image" and layout.apply(i.matrix, 0, 0)[0] >= 500)

    assert glow() == 1.0  # the design state
    hit = played.hit("Glowing")
    played.state.m_passing = {hit.trail: playinput.hover_values(played.ctx, hit)}
    played.place({hit.trail: 0.5})
    assert glow() == pytest.approx(0.6)  # half way from 0.2 to 1
    played.place({hit.trail: 0.25})
    assert glow() == pytest.approx(0.4)
    played.place()  # untimed: at its end
    assert glow() == pytest.approx(1.0)


def test_a_toggle_steps_over_its_entries_and_a_combo_box_lists_and_picks(tree):
    played = Played(tree)
    switch, combo = played.hit("Switch"), played.hit("Combo")
    assert playinput.entries(played.ctx, switch) == ["Entry 1", "Entry 2", "Entry 3"]  # the scene's three options
    right = layout.apply(switch.matrix, switch.size[0] * 0.75, switch.size[1] / 2)
    hits = playinput.under(played.frame, *right)
    for want in (1, 2, 0):  # three entries, round
        out = playinput.click(played.ctx, played.frame, hits, right, SCENE)
        assert out.kept[-1] == (switch.trail, (playinput.SWITCH_INDEX,), want)
        played.keep(out)
        played.place()
        hits = playinput.under(played.frame, *right)
    at = played.centre("Combo")
    out = playinput.click(played.ctx, played.frame, playinput.under(played.frame, *at), at, SCENE)
    assert out.kept == [(combo.trail, (playinput.COMBO_OPEN,), True)]
    assert len(playinput.entries(played.ctx, combo)) == 3
    picked = playinput.pick_entry(played.ctx, played.frame, combo, 2)
    assert (combo.trail, (playinput.COMBO_INDEX,), 2) in picked.kept
    assert (combo.trail, (playinput.COMBO_OPEN,), False) in picked.kept
    bare = dataclasses.replace(switch, values={})  # no entries given: a toggle still flips over two
    assert len(playinput.entries(played.ctx, bare)) == 2 and playinput.entries(played.ctx, replace_values(combo)) == []


def replace_values(hit):
    return dataclasses.replace(hit, values={})


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
    for i in range(3):
        w.m_samples.set(f"{SCENE}/{OPTIONS:08x}/{listlayout.ELEMENT + i:08x}/21000001", f"option {i}")
    w.open_file(SCENE)
    w.play_mode_action.trigger()
    yield w
    for doc in w.m_docs.values():
        doc.m_undo.mark_clean()
    w.close()
    qt.processEvents()


def _at(window, *names: str):
    from PySide6.QtCore import QPointF

    for h in window.m_frame.hits:
        if [_window_name(window, s) for s in h.trail] == list(names):
            x, y = layout.apply(h.matrix, h.size[0] / 2, h.size[1] / 2)
            return QPointF(x, y), h
    raise KeyError(names)


def _window_name(window, step) -> str:
    for key in window.game.m_files.m_entries:
        root = window.game.m_files.root(key)
        found = next((e.name for e in scope.root_info(window.m_ctx, root).top.walk() if e.guid == step[0]), None)
        if found:
            return found
    return "?"


def test_the_window_places_a_nested_hover_again_alone(window, monkeypatch):
    from PySide6.QtCore import QPointF

    play, placed = window.play, []
    original = layout.place
    monkeypatch.setattr(layout, "place", lambda *a, **k: placed.append(a[0]) or original(*a, **k))
    at, handle = _at(window, "Bar", "Handle")
    count = len(window.m_frame.items)
    play.mouse("move", at, QPointF())
    assert placed == [] and handle.trail in play.m_state.m_passing  # the bar's placing only (``playstate.replay``)
    assert len(window.m_frame.items) == count + 1  # the handle button's glow
    play.leave()
    assert placed == [] and len(window.m_frame.items) == count


def test_the_window_drags_a_scroll_bar_picks_from_a_combo_box_and_walks_by_arrows(window):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent

    play = window.play
    _, bar = _at(window, "Bar")
    top, bottom = (QPointF(*layout.apply(bar.matrix, 10.0, y)) for y in (1.0, bar.size[1] - 1.0))
    for kind, point in (("press", top), ("move", bottom), ("release", bottom)):
        play.mouse(kind, point, QPointF())
    assert play.m_state.first(SCENE, _at(window, "Rows")[1].trail) == 2
    assert [i.image_id for i in window.m_frame.items if (i.image_id or "").startswith("pic")] == ["pic2", "pic3"]
    at, combo = _at(window, "Combo")
    for kind in ("move", "press", "release"):
        play.mouse(kind, at, QPointF())
    _, names, rows = play._open_list()
    assert len(names) == 3
    play.mouse("press", rows[1].center(), QPointF())
    assert play.m_state.value(SCENE, combo.trail, (playinput.COMBO_INDEX,)) == 1 and play._open_list() is None
    play.m_focus = _at(window, "Mid")[1].trail
    assert play.key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    assert play.m_focus == _at(window, "Up")[1].trail
