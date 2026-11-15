"""The island forms' model: sections of settings for the open island, written into the edit document as one change."""

import pytest

from annotoolbox.editor import islandprops
from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.formats.a7m.edit117 import VanillaEditor
from annotoolbox.editor.formats.isd.edit import Island2070Editor
from annotoolbox.formats import filedb, isd2070
from annotoolbox.islands import islandedits
from fakes import edit_island
from test_island2070props import island as isd_root
from test_islandinfo import SOURCE, island_files

ASSETS = (
    "<Assets><Asset><Template>RandomIsland</Template><Values><Standard><GUID>7</GUID><Name>isle</Name></Standard>"
    f"<RandomIsland><FilePath>{SOURCE}</FilePath><IslandRegion>Roman</IslandRegion>"
    "<IslandType>Normal</IslandType></RandomIsland></Values></Asset>"
    "<Asset><Template>RandomIsland</Template><Values><Standard><GUID>8</GUID><Name>other</Name></Standard>"
    "<RandomIsland><FilePath>data/other.a7m</FilePath><IslandRegion>Celtic</IslandRegion>"
    "<IslandType>Decoration;Starter</IslandType><IslandPlaceholderSize>Medium</IslandPlaceholderSize>"
    "</RandomIsland></Values></Asset></Assets>"
)


def vanilla_editor() -> VanillaEditor:
    files = island_files()
    files.files[islandprops.ASSETS_117] = ASSETS.encode()
    editor = VanillaEditor.__new__(VanillaEditor)
    editor.files, editor.a7m_path = files, SOURCE
    editor.view = type("View", (), {"info": filedb.read(files.read(SOURCE[:-4] + ".a7minfo"))})()
    return editor


def session(edits=None) -> EditSession:
    return EditSession(edit_island(), edits)


def test_a_vanilla_island_offers_its_info_and_its_registration():
    editor = vanilla_editor()
    info, registration = islandprops.sections_for(editor, session({"target": "new"}))
    assert [f.key for f in info.fields] == ["grid", "start_coast", "active_rect"]
    assert info.base["start_coast"] == pytest.approx(57.3, abs=0.01) and info.base["active_rect"] == [8, 0, 24, 24]
    assert registration.enabled and registration.base["IslandRegion"] == "Roman"
    assert registration.fields[0].choices == ("Celtic", "Roman")  # what the game's own islands use
    assert registration.fields[2].kind == "choices" and registration.fields[2].choices == (
        "Decoration",
        "Normal",
        "Starter",
    )
    assert registration.fields[3].choices == ("", "Medium", "Large")


def test_a_replaced_island_shows_its_registration_but_does_not_edit_it():
    _info, registration = islandprops.sections_for(vanilla_editor(), session())
    assert not registration.enabled and "saved as a new one" in registration.note


def test_values_are_written_as_the_difference_from_the_island_and_undo_as_one_change():
    s = session()
    info, _ = islandprops.sections_for(vanilla_editor(), s)
    problems = islandprops.set_values(s, info, {**info.base, "start_coast": 120.0})
    assert not problems and s.edits["meta"] == {"start_coast": 120.0}  # the unchanged values are not kept
    assert info.values(s.edits)["start_coast"] == 120.0 and info.values(s.edits)["grid"] == 32
    assert len(s.undo.commands) == 1 and s.undo.undo() == "island" and "meta" not in s.edits
    s.undo.redo()
    assert s.edits["meta"] == {"start_coast": 120.0}
    islandprops.set_values(s, info, dict(info.base))  # back to the island's own: the key goes
    assert "meta" not in s.edits
    islandprops.set_values(s, info, dict(info.base))  # nothing to change: no step
    assert len(s.undo.commands) == 2  # the set and the reset; the repeat made no step


def test_what_the_game_would_not_take_is_returned_and_the_values_are_kept_all_the_same():
    s = session({"target": "new"})
    info, registration = islandprops.sections_for(vanilla_editor(), s)
    assert islandprops.set_values(s, info, {"active_rect": [0, 0, 99, 99]})
    assert s.edits["meta"] == {"active_rect": [0, 0, 99, 99]}
    problems = islandprops.set_values(s, registration, {"IslandType": "Volcano", "IslandPlaceholderSize": "Large"})
    assert any("Volcano" in p for p in problems)
    assert s.edits["registration"] == {"IslandType": "Volcano", "IslandPlaceholderSize": "Large"}


def test_the_edit_file_keys_reach_the_island_build(tmp_path):
    from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117

    s = session({"target": "new"})
    info, _ = islandprops.sections_for(vanilla_editor(), s)
    islandprops.set_values(s, info, {"start_coast": 270.0})
    islandedits.save(tmp_path / "e.json", s.edits)
    done = a7m_edit117.apply(island_files(), SOURCE, islandedits.load(tmp_path / "e.json"), template=None)
    assert done.report["meta"] == 1


def test_a_2070_island_offers_its_properties_and_writes_them_through_the_edit_file():
    editor = Island2070Editor.__new__(Island2070Editor)
    editor.isl = isd2070.Island.from_tree(isd_root("-28"))
    (section,) = islandprops.sections_for(editor, session())
    assert [f.key for f in section.fields] == ["size", "clime", "difficulty", "sea_level"]
    assert section.base["sea_level"] == -28 and section.base["size"] == 64
    s = session()
    assert not islandprops.set_values(s, section, {**section.base, "clime": 1, "sea_level": -30})
    assert s.edits["properties"] == {"clime": 1, "sea_level": -30}
    assert islandprops.set_values(s, section, {"clime": 5})  # no island of the game has it


def test_an_island_with_nothing_to_set_or_only_looked_at_has_no_sections():
    assert islandprops.sections_for(object(), session()) == []
    assert islandprops.sections_for(vanilla_editor(), EditSession(edit_island(), readonly=True)) == []
    editor = Island2070Editor.__new__(Island2070Editor)
    editor.isl = isd2070.Island.from_tree(isd_root(None))
    editor.isl.root.children = [c for c in editor.isl.root.children if c.name in ("Width", "Height")]
    assert islandprops.sections_for(editor, session()) == []


def test_the_session_section_offers_the_games_ambient_and_vegetation_names_and_writes_the_difference():
    from annotoolbox.islands import sessionsettings

    editor = vanilla_editor()
    folder = sessionsettings.AMBIENT_FOLDER
    editor.files.files |= {
        f"{folder}amb_roman_01.xml": b"",
        f"{folder}amb_celtic_01.xml": b"",
        f"{folder}readme.txt": b"",
    }
    editor.files.files[sessionsettings.PROP_SETS] = (
        b"<PropSets><GroundTypes><GroundType><Name>All</></></GroundTypes>"
        b"<PropSet><Name>ls_a</><Large/></><PropSet>\n  <Name>ls_b</></></PropSets>"
    )
    own = {"ambient": "amb_roman_01", "vegetation": "ls_a", "playable_area": [0, 0, 16, 16]}
    s = EditSession(edit_island(session=own))
    *_, section = islandprops.sections_for(editor, s)
    assert section.edit_key == "session" and [f.key for f in section.fields] == [
        "ambient",
        "vegetation",
        "playable_area",
    ]
    assert section.fields[0].choices == ("amb_celtic_01", "amb_roman_01") and section.fields[1].choices == (
        "ls_a",
        "ls_b",
    )
    assert section.fields[2].kind == "rect"
    problems = islandprops.set_values(s, section, {**own, "ambient": "amb_celtic_01", "playable_area": [0, 0, 99, 16]})
    assert s.edits["session"] == {"ambient": "amb_celtic_01", "playable_area": [0, 0, 99, 16]} and len(problems) == 1
    assert s.session_value("ambient") == "amb_celtic_01"
    assert not [x for x in islandprops.sections_for(editor, EditSession(edit_island())) if x.edit_key == "session"]
