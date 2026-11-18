"""The brush strokes of the 3D view without OpenGL (``BrushTools``), and the ribbon's buttons for the further height
brushes and the object brush."""

import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopribbon
from annotoolbox.editor.desktop.desktopbrush import BrushTools
from annotoolbox.editor.editsession import EditSession
from fakes import edit_island


class Signal:
    def __init__(self):
        self.sent = []

    def emit(self, v):
        self.sent.append(v)


class FakeView:
    """What ``BrushTools`` asks of ``IslandView3D``; view space mirrors x as the real view does."""

    def __init__(self, session, tool="height", **brush):
        self.session, self.tool, self.pick, self.paint_material = session, tool, None, None
        self.brush = {"mode": "raise", "radius": 3, "strength": 0.5, "plant": True, "spacing": 1.5, **brush}
        self.status, self.hidden_applied, self.updates = Signal(), 0, 0

    def from_view(self, v):
        return [self.session.size - v[0], v[1], v[2]]

    def apply_hidden(self):
        self.hidden_applied += 1

    def update(self):
        self.updates += 1


def stroke(view, *points):
    tools = BrushTools(view)
    started = tools.start(points[0])
    for p in points[1:]:
        tools.to(p)
    tools.end()
    return started


def test_a_ramp_stroke_through_the_view_lays_a_slope():
    s = EditSession(edit_island())
    view = FakeView(s, mode="ramp", radius=1.5)
    assert stroke(view, (15.0, 0.0, 8.0), (8.0, 0.0, 8.0))  # view x = 16 - x: from x = 1 to x = 8
    assert s.corner_heights()[8, 1] == pytest.approx(-2.0, abs=0.01) and s.corner_heights()[8, 5] > -1.0
    assert len(s.undo.commands) == 1 and s.edits["heights"]


@pytest.mark.parametrize("mode", ["noise", "terrace", "despike"])
def test_the_other_height_modes_stroke_through_the_view(mode):
    s = EditSession(edit_island(), {"heights": {"8,8": 2.0}})
    view = FakeView(s, mode=mode, strength=1.0)
    assert stroke(view, (8.0, 0.0, 8.0), (7.0, 0.0, 8.0))
    assert len(s.undo.commands) == 1


def test_a_stroke_ends_as_the_tool_it_began_with_when_a_key_changes_the_tool_meanwhile():
    """S during a height stroke: the next mouse move looked for the Select tool's stroke and raised, and the stroke
    was never ended."""
    s = EditSession(edit_island())
    view = FakeView(s, mode="raise")
    tools = BrushTools(view)
    assert tools.start((8.0, 0.0, 8.0))
    view.tool = "select"
    tools.to((7.0, 0.0, 8.0))
    tools.end()
    assert len(s.undo.commands) == 1 and s.edits["heights"] and tools.tool is None
    tools.to((6.0, 0.0, 8.0))  # no stroke under way: nothing
    tools.end()
    assert len(s.undo.commands) == 1


def test_the_spray_stroke_places_the_picked_model_and_asks_for_a_pick_first():
    s = EditSession(edit_island())
    view = FakeView(s, tool="spray")
    assert not stroke(view, (8.0, 0.0, 8.0)) and view.status.sent == ["pick a model in the library to spray"]
    view.pick = {"cfg": "data/base/tree_pine.cfg"}
    assert stroke(view, (8.0, 0.0, 8.0), (6.0, 0.0, 8.0))
    assert len(s.edits["added_objects"]) > 3 and view.hidden_applied >= 1 and view.status.sent[-1].endswith("sprayed")
    assert len(s.undo.commands) == 1 and s.undo.commands[0].label == "spray"


def test_paint_needs_a_material_and_forest_and_blockers_stroke_as_before():
    s = EditSession(edit_island())
    view = FakeView(s, tool="paint")
    assert not stroke(view, (8.0, 0.0, 8.0)) and view.status.sent == ["pick a ground material"]
    view.tool = "forest"
    assert stroke(view, (8.0, 0.0, 8.0))


def test_the_ribbon_offers_the_new_brushes_where_the_island_allows_them():
    ids = {it.id for it in desktopribbon.items()}
    assert {"tool_noise", "tool_terrace", "tool_ramp", "tool_despike", "tool_spray", "spacing"} <= ids
    state = {"island": True, "editable": True, "tools": {"select", "height"}}
    assert desktopribbon.available("height", state) and not desktopribbon.available("spray", state)
    assert desktopribbon.tool_action("height", {"mode": "ramp", "plant": True}) == "tool_ramp"
    assert desktopribbon.tool_action("spray", {"mode": "raise", "plant": True}) == "tool_spray"
    with_spray = {
        it.id
        for _t, groups in desktopribbon.tabs_for(("anno1800",), {"select", "height", "spray"})
        for _g, g in groups
        for it in g
    }
    assert "tool_spray" in with_spray
    without = {
        it.id
        for _t, groups in desktopribbon.tabs_for(("anno1800",), {"select", "height"})
        for _g, g in groups
        for it in g
    }
    assert "tool_spray" not in without and "spacing" not in without


def test_the_area_id_tool_paints_the_chosen_id_or_says_the_island_has_none():
    import numpy as np

    from annotoolbox.editor import cellbrush

    ids = np.array([[0x4001 if x == 3 else 0x2001 for x in range(16)]] * 16)
    s = EditSession(edit_island(area_grid=cellbrush.pack_area(ids)))
    view = FakeView(s, tool="active", grid="areakind", areakind=0, radius=1.0)  # the first id: 0x4001
    assert stroke(view, (10.5, 0.0, 6.5))
    assert s.edits["area"] and set(s.edits["area"].values()) == {0x4001}
    view.brush["areakind"] = 1  # 0x6001: the island has no such area
    assert not stroke(view, (4.5, 0.0, 6.5)) and view.status.sent == ["this island has no area 0x6001"]
    assert desktopribbon.tool_action("active", view.brush | {"grid": "areakind"}) == "tool_areakind"


def test_the_tint_tool_strokes_through_the_cell_tool_with_the_boxs_preset_and_the_strength():
    s = EditSession(edit_island())
    view = FakeView(s, tool="active", grid="tint", tint=1, radius=2.0, strength=0.6)
    assert stroke(view, (10.0, 0.0, 6.0), (7.0, 0.0, 6.0))  # view x = 16 - x: from x = 6 to x = 9
    (st,) = s.edits["tint"]
    assert st["strength"] == 0.6 and st["color"] == [1.4, 1.4, 1.4] and len(st["points"]) == 2
    assert desktopribbon.tool_action("active", view.brush) == "tool_tint"
