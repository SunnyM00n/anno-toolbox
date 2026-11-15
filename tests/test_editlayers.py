"""Edit layers: named groups of objects, hidden or locked, kept in the edit file."""

import pytest

from annotoolbox.editor.editlayers import EditLayers
from annotoolbox.editor.editsession import EditSession
from fakes import edit_island


def test_objects_of_the_island_and_placed_ones_join_a_layer_and_survive_a_reload():
    s = EditSession(edit_island())
    layers = EditLayers(s)
    rock = next(e for e in s.entries if e.cfg == "rock.cfg")
    tower = s.place({"cfg": "tower.cfg"}, [8.0, 0.7, 8.0])
    layers.assign([rock, tower], "town")
    assert layers.names() == ["town"] and set(layers.members("town")) == {rock, tower}
    assert tower.added["layer"] == "town" and s.edits["layers"]["town"]["members"] == [rock.key]
    again = EditSession(edit_island(), s.edits)
    back = EditLayers(again)
    assert {e.cfg for e in back.members("town")} == {"rock.cfg", "tower.cfg"}


def test_hiding_and_locking_follow_the_layer_and_undo():
    s = EditSession(edit_island())
    layers = EditLayers(s)
    rock = next(e for e in s.entries if e.cfg == "rock.cfg")
    layers.assign([rock], "cliffs")
    layers.set_flag("cliffs", "hidden", True)
    layers.set_flag("cliffs", "locked", True)
    assert layers.hides(rock) and layers.locked(rock)
    s.undo.undo()
    assert layers.hides(rock) and not layers.locked(rock)
    layers.assign([rock], None)
    assert layers.layer_of(rock) is None and not layers.hides(rock)


def test_removing_a_layer_keeps_its_objects():
    s = EditSession(edit_island())
    layers = EditLayers(s)
    tower = s.place({"cfg": "tower.cfg"}, [8.0, 0.7, 8.0])
    layers.assign([tower], "town")
    layers.remove("town")
    assert tower.alive and "layer" not in tower.added and not layers.names()
    s.undo.undo()
    assert tower.added["layer"] == "town"
    with pytest.raises(ValueError):
        layers.create("town")
