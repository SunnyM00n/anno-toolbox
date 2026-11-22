"""A prop's tint: read from the island's ``PropGrid``, set in the edit document, kept in the edit file, written back."""

import json
import struct

import pytest
from tests.editor.editorfakes import edit_island
from tests.islands.islandfakes import FakeBuilder
from tests.islands.islandfakes import prop_tag as _prop

from annotoolbox.editor import selection
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.islandeditor import CIslandEditor
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import islandedits

RED = [1.0, 0.0, 0.0, 1.0]


def coloured_island():
    """The synthetic island with its waterfall prop carrying a white tint, as the game's props do."""
    island = edit_island()
    island["props"][0] = {**island["props"][0], "color": [1.0, 1.0, 1.0, 1.0]}
    return island


def fall(s: CEditSession):
    return next(e for e in s.m_entries if e.cfg == "fall.cfg")


def test_the_island_reader_gives_a_props_tint():
    names = STag("FileNames", [SAttrib("#32768", "data\\b\\leaf.cfg".encode("utf-16-le"))])
    with_colour = STag("#1", _prop(0, (1, 2, 3)).children + [SAttrib("Color", struct.pack("<4f", 0.5, 0.25, 1.0, 1.0))])
    editor = CIslandEditor.__new__(CIslandEditor)
    editor.tree_table = lambda: None
    editor.model_problem = lambda cfg: ""
    editor.m_view = type(
        "View",
        (),
        {"rd3d": STag("rd", [STag("PropGrid", [names, STag("Instances", [with_colour, _prop(0, (4, 5, 6))])])])},
    )()
    first, second = editor.read_props()
    assert first["color"] == [0.5, 0.25, 1.0, 1.0] and "color" not in second  # a prop with no Color has none


def test_a_tint_is_one_undoable_change_and_survives_moves_and_the_edit_file(tmp_path):
    s = CEditSession(coloured_island())
    e = fall(s)
    assert e.color == [1.0, 1.0, 1.0, 1.0]
    s.set_color(e, RED)
    assert e.color == RED and s.m_edits["props"][e.key]["color"] == RED and len(s.m_undo.m_commands) == 1
    s.transform(e, "move", position=[10.5, 1.0, 4.0])  # a move keeps the tint
    assert s.m_edits["props"][e.key]["color"] == RED
    s.m_undo.undo()
    assert e.color == RED
    assert s.m_undo.undo() == "colour" and e.color == [1.0, 1.0, 1.0, 1.0] and e.key not in s.m_edits["props"]
    s.m_undo.redo()
    islandedits.save(tmp_path / "e.json", s.m_edits)
    again = CEditSession(coloured_island(), islandedits.load(tmp_path / "e.json"))
    assert fall(again).color == RED


def test_only_a_prop_takes_a_tint_and_a_read_only_island_none():
    s = CEditSession(coloured_island())
    rock = next(e for e in s.m_entries if e.cfg == "rock.cfg")
    s.set_color(rock, RED)
    assert rock.color is None and not s.m_undo.m_commands
    locked = CEditSession(coloured_island(), is_readonly=True)
    locked.set_color(fall(locked), RED)
    assert fall(locked).color == [1.0, 1.0, 1.0, 1.0] and not locked.m_edits["props"]


def test_a_copy_and_a_paste_carry_the_tint():
    s = CEditSession(coloured_island())
    s.set_color(fall(s), RED)
    dup = s.duplicate(fall(s))
    assert dup.color == RED and dup.added["color"] == RED
    clip = selection.copy([fall(s)])
    assert clip["items"][0]["color"] == RED
    (pasted,) = selection.paste(s, json.loads(json.dumps(clip)), [3.0, 0.7, 3.0])
    assert pasted.color == RED and pasted.added["color"] == RED
    plain = CEditSession(edit_island())
    (other,) = selection.paste(plain, clip, [3.0, 0.7, 3.0])  # another island takes it the same
    assert other.color == RED


def test_the_build_writes_a_changed_tint_and_a_new_props_tint():
    b = FakeBuilder()
    edits = islandedits.empty()
    edits["props"][islandedits.key("data\\b\\waterfall.cfg", (50, 1, 60))] = {"color": [0.2, 0.4, 0.6, 0.8]}
    b.inst.children[0].children.append(SAttrib("Color", struct.pack("<4f", 1, 1, 1, 1)))
    report = islandedits.apply(b, edits)
    assert report["props changed"] == 1
    assert struct.unpack("<4f", b.inst.children[0].find("Color").data) == pytest.approx((0.2, 0.4, 0.6, 0.8))
    edits = islandedits.empty()
    edits["added_props"] = [
        {"cfg": "data/b/waterfall.cfg", "position": [1, 2, 3], "color": [0.0, 1.0, 0.0, 1.0]},
        {"cfg": "data/b/waterfall.cfg", "position": [4, 5, 6]},
    ]
    report = islandedits.apply(b, edits)
    assert report["props added"] == 2
    green, white = b.inst.children[-2:]
    assert struct.unpack("<4f", green.find("Color").data) == (0.0, 1.0, 0.0, 1.0)
    assert struct.unpack("<4f", white.find("Color").data) == (1.0, 1.0, 1.0, 1.0)  # the default stays white
