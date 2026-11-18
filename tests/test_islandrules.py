"""The island checked against the game's rules: findings a mapper can act on."""

from annotoolbox.editor import editsession, islandrules
from annotoolbox.editor.editsession import EditSession
from annotoolbox.infra.i18n import tr
from fakes import edit_island


def messages(rows, level=None):
    return [f.message for f in rows if level is None or f.level == level]


def test_a_plain_island_has_only_its_readout():
    s = EditSession(edit_island())
    rows = islandrules.check(s, "2070")
    assert [f.level for f in rows] == ["info"]
    assert "grid 16 tiles" in rows[0].message and "Small" in rows[0].message
    assert islandrules.report(rows)[1] == "the island follows the game's rules"


def test_a_117_grid_must_be_whole_chunks():
    rows = islandrules.check(EditSession(edit_island()), "117")
    assert any("not a multiple of 64" in m for m in messages(rows, "error"))


def test_objects_off_the_grid_floating_buried_and_stacked():
    s = EditSession(edit_island())
    s.place({"cfg": "tower.cfg"}, [20.0, 0.7, 3.0])
    s.place({"cfg": "tower.cfg"}, [8.0, 5.0, 8.0])
    s.place({"cfg": "well.cfg"}, [8.0, -4.0, 9.0])
    s.place({"cfg": "hut.cfg"}, [8.0, 0.7, 10.0])
    s.place({"cfg": "hut.cfg"}, [8.01, 0.7, 10.0])
    rows = islandrules.check(s, "2070")
    errors, warnings = messages(rows, "error"), messages(rows, "warning")
    assert any("off the island's grid" in m for m in errors)
    assert any("floats 4.3" in m for m in warnings) and any("buried 4.7" in m for m in warnings)
    assert any("two copies of hut" in m for m in warnings)
    assert all(f.place is not None for f in rows if f.level != "info")


def test_rivers_paint_and_heights_out_of_range():
    s = EditSession(edit_island())
    s.edits["rivers"]["0"] = {"points": [[4.0, 0.5, 4.0]], "widths": [2.0]}
    s.edits["paint"].append({"material": 40, "radius": 2, "strength": 1, "points": [[5, 5]]})
    s.D[3, 3] = 200.0
    s._redraw(0, 0, s.size, s.size)
    rows = islandrules.check(s, "117")
    errors = messages(rows, "error")
    assert any("river 0 has 1 point" in m for m in errors)
    assert any("material 40" in m for m in errors)
    s.edits["paint"].append({"material": "sand", "radius": 2, "strength": 1, "points": [[5, 5]]})  # a hand-edited file
    assert any("material -1" in m for m in messages(islandrules.check(s, "117"), "error"))
    assert any("past the 128 units" in m for m in errors)
    assert rows[0].level == "error"


def test_an_edit_that_crosses_a_size_class_is_named():
    s = EditSession(edit_island())
    assert tr(islandrules.size_class(9_000)) == "Small" and tr(islandrules.size_class(9_001)) == "Medium"
    assert tr(islandrules.size_class(10**7)) == "Continental"
    info = islandrules.readout(s)
    assert info["land tiles"] == info["land tiles as opened"] > 0
    assert info["active cells"] >= 1


def test_the_checks_tab_runs_the_rules_of_the_islands_own_game():
    s = EditSession(edit_island(game="anno1800"))
    assert editsession.game_of(s.island) == "anno1800" and editsession.game_of(edit_island()) == "anno117"
    assert editsession.game_of({"mode": "2070"}) == "anno2070"
    assert editsession.game_of({"game": "anno1404"}, kind="anno2070") == "anno1404"  # the island's own game first
    rows, message = islandrules.RULES.report(islandrules.RULES.run(s, None))
    assert "1 error(s)" in message and rows[0][0].startswith("error: the grid of 16 tiles")


def test_a_map_has_ids_for_127_islands():
    assert not islandrules.check_map(127)
    assert "128 islands" in islandrules.check_map(128)[0].message
