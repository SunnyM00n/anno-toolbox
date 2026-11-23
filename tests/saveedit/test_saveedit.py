"""The Saves app's parts on synthetic saves: the binary tag tree and the 2070 container byte for byte, any container
opened, the saves found in a Documents folder, the summaries, the edits written beside the original, the mods a save
needs, the tree made a level at a time; with them the faults the audits of 2026-11-14 and 2026-11-17 found, one
test each (a save is only ever written beside the original, an unchanged one is its bytes)."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from tests.saveedit.saveeditfakes import damaged_117, fake_save117, fake_save2070, rows, section
from tests.sources.sourcesfakes import fake_mod

from annotoolbox.formats import bintree, filedb, savegame117, savegame2070
from annotoolbox.formats.isd2070 import SNode
from annotoolbox.infra.i18n import tr
from annotoolbox.saveedit import container, edits, locations, needed, nodes, records, summary, summary117
from annotoolbox.saveedit.records import SModEntry
from annotoolbox.suite.settings import CSettings

# The binary tag tree and 2070's container


def test_the_binary_tag_tree_reads_and_writes_back_byte_for_byte():
    tree = bintree.STree([SNode("Root", None, None, [SNode("Text", "12"), SNode("Empty", ""), SNode("Blob", data=b"\x01\x02"),
                                                   SNode("None")])])  # fmt: skip
    raw = bintree.write(tree)
    assert raw[:4] == b"BIN " and raw[-8:-4] == b"END "
    back = bintree.read(raw)
    assert back.path("Root/Text").text == "12" and back.path("Root/Empty").text == ""
    assert back.path("Root/Blob").data == b"\x01\x02" and back.path("Root/None").text is None
    assert bintree.write(back) == raw
    with pytest.raises(bintree.CBinTreeError):
        bintree.read(b"BIN " + bytes(12))


def test_a_node_whose_number_the_table_lacks_keeps_its_number():
    raw = bintree.write(bintree.STree([SNode("Root", None, None, [SNode("#9", "x"), SNode("Named", "y")])]))
    back = bintree.read(raw)
    assert back.tags == {"Root": 1, "Named": 2} and back.path("Root/#9").text == "x"
    assert bintree.write(back) == raw


def test_an_unchanged_2070_save_is_its_original_bytes(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    save = savegame2070.read_bytes(path.read_bytes())
    assert save.to_bytes() == path.read_bytes()
    assert savegame2070.read_money(save) == 80000 and savegame2070.human_participant(save) == 0


def test_the_2070_money_lands_in_credits_and_the_load_list(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    original = path.read_bytes()
    save = savegame2070.read_bytes(original)
    edited = savegame2070.set_money(save, 1_000_000)
    data = edited.to_bytes()
    back = savegame2070.read_bytes(data)
    assert savegame2070.read_money(back) == 1_000_000
    credits = savegame2070.participants(back.trees["save.sww"])[0].path("m_Resource/Credits").text
    assert int(credits) == 1_000_000 * 1024 + 6  # the fraction stays
    assert back.trees["stats.xml"].path("GameStats/GoldAmount").text == "1000000"
    assert data[520:544] == original[520:544]  # the preamble's value is kept
    assert back.raw["game2.xml"] == save.raw["game2.xml"] and back.raw["preview.jpg"] == save.raw["preview.jpg"]
    assert savegame2070.read_money(save) == 80000  # the original is untouched
    with pytest.raises(savegame2070.CSave2070Error, match="outside"):
        savegame2070.set_money(save, -1)


def test_the_2070_preamble_is_copied_from_the_original_whatever_it_holds(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    data = bytearray(path.read_bytes())
    data[524:544] = bytes(range(200, 220))  # the 20 bytes the game's own saves differ in
    path.write_bytes(bytes(data))
    written = edits.write_beside(container.open_file(path, "anno2070"), {"money": 7})
    out = written.read_bytes()
    assert out[520:544] == bytes(data[520:544]) and out != bytes(data)
    assert savegame2070.read_money(savegame2070.read_bytes(out)) == 7


def test_a_2070_part_is_packed_as_the_game_packs_it():
    doc = b"BIN " + bytes(100)
    packed = savegame2070.pack(doc)
    assert packed[:4] == b"ZZZZ" and struct.unpack_from("<I", packed, 4)[0] == len(doc)
    assert packed[8:10] == b"\x78\x5e" and packed[-4:] == b"\x00\x00\xff\xff"  # a sync flush, no final block
    assert savegame2070.unpack(packed) == doc


def test_the_2070_writer_reads_back_before_it_replaces(tmp_path: Path):
    path = fake_save2070(tmp_path / "a.sww")
    dest = tmp_path / "b.sww"
    savegame2070.write(savegame2070.set_money(savegame2070.read_bytes(path.read_bytes()), 5), dest)
    assert savegame2070.read_money(savegame2070.read_bytes(dest.read_bytes())) == 5
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".part"] == []


def test_117_reads_from_bytes_as_from_the_file(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "Autosave 0.a8s")
    assert savegame117.read_bytes(path.read_bytes()).docs == savegame117.read(path).docs


# Any container


def test_a_117_save_opens_as_four_tailed_filedb_parts(tmp_path: Path):
    save = container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117")
    assert save.archive == "2.2" and [p.m_name for p in save.parts] == list(savegame117.PARTS)
    assert {p.m_packing for p in save.parts} == {container.TAIL} and {p.m_kind for p in save.parts} == {
        container.FILEDB
    }
    assert save.part("data.a7s").tree().find("MetaGameManager") is not None


def test_a_2070_save_opens_as_packed_trees_and_a_picture(tmp_path: Path):
    save = container.open_file(fake_save2070(tmp_path / "a.sww"), "anno2070")
    kinds = {p.m_name: (p.m_packing, p.m_kind) for p in save.parts}
    assert kinds["save.sww"] == (container.ZZZZ, container.BINTREE)
    assert kinds["preview.jpg"] == (container.STORED, container.PICTURE)
    assert save.archive == "2.0"


def test_a_file_that_is_no_archive_is_one_part(tmp_path: Path):
    path = tmp_path / "x.sww"
    path.write_bytes(b"<Root><A>1</A></Root>")
    save = container.open_file(path, "anno1701")
    assert save.archive == "" and save.parts[0].m_kind == container.TAGTREE
    assert save.parts[0].tree().path("Root/A").text == "1"
    junk = tmp_path / "junk.a6s"
    junk.write_bytes(bytes(range(256)) * 3)
    assert container.open_file(junk).parts[0].m_kind == container.BYTES


# Where the saves are


def test_the_saves_are_found_in_each_games_folder_newest_first(tmp_path: Path):
    account = tmp_path / "Anno 117 - Pax Romana" / "accounts" / "abc"
    old = fake_save117(account / "Marcia" / "Autosave 1.a8s")
    new = fake_save117(account / "Marcia" / "Marcia.a8s")
    (account / "accountdata.a8s").write_bytes(b"not a save")
    os.utime(old, (1_000_000, 1_000_000))
    fake_save2070(tmp_path / "ANNO 2070" / "Accounts" / "Me" / "Savegames" / "Spielstand.sww")
    found = locations.find("anno117", tmp_path)
    assert [r.path for r in found] == [new, old] and {r.group for r in found} == {"Marcia"}
    assert [r.group for r in locations.find("anno2070", tmp_path)] == ["Me"]
    assert locations.find("anno1800", tmp_path) == [] and locations.folder("anno1800", tmp_path) is None
    assert locations.game_of(Path("x.A8S")) == "anno117" and locations.game_of(Path("x.txt")) == ""


@pytest.mark.parametrize(
    ("name", "shown", "game"),
    [
        ("x.sww", "anno1404", "anno1404"),  # audit 2026-11-17: 1404 and 1701 write .sww too, it opened as 2070's
        ("x.SWW", "anno1701", "anno1701"),
        ("x.sww", "anno117", "anno2070"),
        ("x.a8s", "anno1404", "anno117"),
        ("x.bin", "anno2205", "anno2205"),
        ("x.bin", "", ""),
    ],
)
def test_a_file_picked_by_hand_is_of_the_shown_game_where_that_writes_its_extension(name: str, shown: str, game: str):
    assert locations.game_of(Path(name), shown) == game


# Summaries


def test_the_117_summary_reads_version_mods_players_and_sessions(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "a.a8s", mods={"anno2070": "0.1.2", "other": "1.0"}, sessions=2)
    s = summary.summarize(container.open_file(path, "anno117"))
    head = rows(section(s, "Save"))
    assert head["File version"] == "80" and head["Profile"] == "Tester" and head["Money in the load list"] == "1500"
    assert s.m_mods == [SModEntry("anno2070", "0.1.2"), SModEntry("other", "1.0")]
    assert section(s, "Players").table.rows[0][:2] == ["0", "Tester"]
    assert section(s, "Participants").table.rows == [
        ["41", "1,500", "", "91372: 2"],
        ["31102", "9,000", "", "91372: 2"],
    ]
    assert [r[0] for r in section(s, "Sessions").table.rows] == ["3245", "3246"]
    assert ("SetupTaxes", "1") in section(s, "Game setup").named
    assert tr(s.m_sections[-1].title) == "Parts"


def test_the_117_islands_come_from_each_sessions_own_document(tmp_path: Path):
    save = container.open_file(fake_save117(tmp_path / "P" / "a.a8s", sessions=2), "anno117")
    said = []
    islands = summary.sessions(save, lambda text, **places: said.append(tr(text, **places)))
    assert said == ["Reading session 3245…", "Reading session 3246…"]
    assert (tr(islands.rows[0][0]), islands.rows[0][1]) == ("Map template", "3245: data/maps/test.a7tinfo")
    assert islands.table.rows[0] == ["3245", "4120", "32777", "506097522914230528", "1", "2068: 186, 2071: 5", ""]
    assert len(islands.table.rows) == 4


def test_the_2070_summary_reads_players_credits_and_islands(tmp_path: Path):
    s = summary.summarize(container.open_file(fake_save2070(tmp_path / "a.sww", credits=1234), "anno2070"))
    player = section(s, "Players").table.rows[0]
    assert player[:3] == ["0", "Human", "Tester"] and player[-1] == "1,234"
    assert section(s, "Islands").table.rows == [["1", "N_L13", "data/levels/islands/normal/n_l13.isd", ", ", " x ",
                                                 "0: 12"]]  # fmt: skip
    assert ("Seed", "5") in section(s, "Game setup").named
    assert summary.sessions(container.open_file(fake_save2070(tmp_path / "b.sww"), "anno2070")) is None


def test_a_game_without_a_pinned_layout_shows_only_its_parts(tmp_path: Path):
    path = tmp_path / "a.a6s"
    path.write_bytes(bytes(range(256)))
    s = summary.summarize(container.open_file(path, "anno2205"))
    assert s.m_notes and [tr(x.title) for x in s.m_sections] == ["Parts"]
    assert edits.fields(container.open_file(path, "anno2205")) == []


def test_a_2070_summary_survives_numbers_that_are_none(tmp_path: Path):
    path = fake_save2070(tmp_path / "a.sww")
    parsed = savegame2070.read_bytes(path.read_bytes())
    stats = bintree.read(bintree.write(parsed.trees["stats.xml"]))
    stats.path("GameStats").children.append(SNode("PlayingTimeMilliSec", "n/a"))
    damaged = tmp_path / "b.sww"
    savegame2070.write(parsed.with_trees({"stats.xml": stats}), damaged)
    s = summary.summarize(container.open_file(damaged, "anno2070"))
    assert rows(section(s, "Save"))["Play time"] == "0:00 h"
    assert records.number("12") == 12 and records.number("x", 7) == 7 and records.number(None) == 0


def test_a_117_save_whose_world_state_is_damaged_is_summarised_from_its_header(tmp_path: Path):
    save = container.open_file(damaged_117(tmp_path / "P" / "a.a8s"), "anno117")
    assert save.part("data.a7s").m_kind == container.BYTES
    s = summary.summarize(save)  # audit 2026-11-17: this raised, so the save did not open at all
    assert rows(section(s, "Save"))["Profile"] == "Tester" and tr(s.m_sections[-1].title) == "Parts"
    assert [tr(text) for text, _places in s.m_notes] == [tr("saves.world_state_not_read")]
    assert summary.sessions(save).table.rows == []
    with pytest.raises(ValueError):
        edits.fields(save)  # the window shows such a save read only


@pytest.mark.parametrize("size", [0, 1])
def test_a_117_value_cut_short_reads_as_nothing(size: int):
    campaign = filedb.STag("CampaignHandler", [filedb.STag("ActiveChapter", [filedb.SAttrib("id", bytes(size))])])
    root = filedb.STag("", [filedb.STag("MetaGameManager", [filedb.STag("QuestManager", [campaign])])])
    s = records.CSummary("anno117")
    summary117._quests(s, root)  # audit 2026-11-17: a struct.error for a value under two bytes
    assert rows(section(s, "Quests"))["Campaign chapter"] == "0" and records.u16(bytes(size)) == 0


# Edits


def test_the_117_fields_hold_what_the_save_holds(tmp_path: Path):
    save = container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117")
    values = {f.key: f.value for f in edits.fields(save)}
    assert values == {"save_name": "Autosave ", "name": "Tester", "profile_name": "Tester", "color": 91304, "logo": 92,
                      "portrait": 33070, "money:41": 1500, "money:31102": 9000}  # fmt: skip


def test_a_117_edit_is_written_beside_and_the_original_stays(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "a.a8s")
    original = path.read_bytes()
    save = container.open_file(path, "anno117")
    dest = edits.write_beside(save, {"name": "Julia", "money:31102": 77, "save_name": "Mine", "color": 91306})
    assert path.read_bytes() == original
    back = savegame117.read(dest)
    player = savegame117.read_participant(back)
    assert player["header.a7s"].name == "Julia" and player["header.a7s"].profile_name == "Tester"  # the folder's
    assert player["data.a7s"].name == "Julia" and player["gamesetup.a7s"].color == 91306
    assert savegame117.read_money(back, 31102) == 77 and savegame117.read_money(back) == 1500
    assert savegame117.save_name(back) == "Mine"


def test_a_write_takes_the_next_free_name_and_an_unchanged_one_is_the_original(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "Marcia.a8s")
    first = edits.write_beside(container.open_file(path, "anno117"), {})
    second = edits.write_beside(container.open_file(first, "anno117"), {"money:41": 2})  # an edited one: counts on
    third = edits.write_beside(container.open_file(path, "anno117"), {})
    names = ["Marcia (edited).a8s", "Marcia (edited 2).a8s", "Marcia (edited 3).a8s"]
    assert [p.name for p in (first, second, third)] == names
    assert first.read_bytes() == path.read_bytes() == third.read_bytes() != second.read_bytes()


def test_a_2070_edit_is_written_beside_and_one_to_the_same_value_is_the_original(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    save = container.open_file(path, "anno2070")
    assert [(f.key, f.value, f.who) for f in edits.fields(save)] == [("money", 80000, "0")]
    dest = edits.write_beside(save, {"money": 42})
    assert dest.name == "Spielstand (edited).sww"
    assert savegame2070.read_money(savegame2070.read_bytes(dest.read_bytes())) == 42
    assert edits.write_beside(save, {"money": 80000}).read_bytes() == path.read_bytes()


@pytest.mark.parametrize(
    ("game", "fake", "name", "key"),
    [("anno117", fake_save117, "a.a8s", "money:41"), ("anno2070", fake_save2070, "b.sww", "money")],
)
def test_the_original_is_never_touched(tmp_path: Path, game: str, fake, name: str, key: str):
    path = fake(tmp_path / "P" / name)
    path.chmod(0o444)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    edits.write_beside(container.open_file(path, game), {key: 3})
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    path.chmod(0o644)


@pytest.mark.parametrize(
    ("game", "changes", "error"),
    [
        ("anno2205", {}, edits.CEditError),  # a game that is read only
        ("anno117", {"money:41": -5}, savegame117.CSaveError),  # a value the format refuses
        ("anno117", {"moneyy:41": 5}, edits.CEditError),  # a value the save has no field for
    ],
)
def test_a_refused_write_leaves_nothing_beside_the_save(tmp_path: Path, game: str, changes: dict, error):
    if game == "anno117":
        path = fake_save117(tmp_path / "P" / "a.a8s")
    else:
        path = tmp_path / "P" / "a.a6s"
        path.parent.mkdir()
        path.write_bytes(bytes(64))
    with pytest.raises(error):
        edits.write_beside(container.open_file(path, game), changes)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_the_forms_text_is_read_as_a_number_or_a_name():
    money = edits.SField("money", "m", edits.NUMBER, 1500)
    guid = edits.SField("color", "c", edits.GUID, 91304)
    text = edits.SField("name", "n", edits.TEXT, "Tester")
    assert edits.parse_value(money, "0100") == 100 and edits.parse_value(money, " 1,500,000 ") == 1_500_000
    assert edits.parse_value(guid, "0x1F") == 31 and edits.parse_value(guid, "91306") == 91306
    assert edits.parse_value(text, " Julia ") == " Julia "
    with pytest.raises(ValueError):
        edits.parse_value(money, "lots")
    typed = {"money": "1500 ", "color": "91304", "name": "Tester "}
    assert edits.changed_values([money, guid, text], typed) == {"name": "Tester "}
    assert edits.changed_values([money], {"money": "9"}) == {"money": 9} and edits.changed_values([money], {}) == {}


def test_a_numbers_separators_are_read_by_the_interface_language():
    """ "1.000" is a thousand in German and "1,000" in English; neither language takes the other's for it."""
    from annotoolbox.infra import i18n

    money = edits.SField("money", "m", edits.NUMBER, 1500)
    guid = edits.SField("color", "c", edits.GUID, 91304)
    name = edits.SField("name", "n", edits.TEXT, "Tester")
    assert i18n.current() == "en" and edits.parse_value(money, "1,000") == 1000
    for typed in ("1.000", "1.5", "1,0.0"):
        with pytest.raises(ValueError):
            edits.parse_value(money, typed)
    assert edits.parse_value(money, "1.000", "de") == 1000 and edits.parse_value(money, " 1.500.000 ", "de") == 1500000
    assert edits.parse_value(money, "0100", "de") == 100 and edits.parse_value(money, "-2.000", "de") == -2000
    assert edits.parse_value(guid, "0x1F", "de") == 31 and edits.parse_value(name, " 1,5 ", "de") == " 1,5 "
    for typed in ("1,000", "1,5", "1.5", "1.0000", "1..000", ".000", "1.000.00"):
        with pytest.raises(ValueError):
            edits.parse_value(money, typed, "de")
    with pytest.raises(ValueError, match="decimal"):
        edits.parse_value(money, "1,000", "de")
    i18n.use("de")  # the form's own call reads the interface language
    assert edits.changed_values([money], {"money": "2.000"}) == {"money": 2000}
    with pytest.raises(ValueError, match="Dezimal"):
        edits.changed_values([money], {"money": "2,000"})


# The mods a save needs


def test_the_mods_a_save_needs_against_the_installed(tmp_path: Path):
    mods = tmp_path / "mods"
    fake_mod(mods, "same", {})
    fake_mod(mods, "older", {}, Version="0.9")
    settings = CSettings(mods=str(mods))
    installed = needed.installed_mods(settings, "anno117")
    need = needed.check([SModEntry("same", "1.0.0"), SModEntry("older", "1.0"), SModEntry("gone", "2")], installed)
    assert [(n.mod_id, n.state, n.installed) for n in need] == [
        ("same", needed.INSTALLED, "1.0.0"), ("older", needed.OTHER, "0.9"), ("gone", needed.MISSING, ""),
    ]  # fmt: skip
    assert needed.installed_mods(settings, "anno2070") == []


# The tree


def test_the_tree_is_made_a_level_at_a_time_and_opens_a_sessions_document(tmp_path: Path):
    save = container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117")
    roots = nodes.roots(save)
    assert [r.m_name for r in roots] == list(savegame117.PARTS) and roots[3].loaded() == []
    manager = roots[3].children()[0]
    sessions = next(c for c in manager.children() if c.m_name == "GameSessions")
    record = next(c for c in sessions.children() if c.m_name == "#1")
    blob = record.children()[1].children()[0]
    assert blob.m_name == "BinaryData" and blob.m_preview == "FileDB document" and blob.has_children()
    assert [c.m_name for c in blob.children()] == ["SessionFileVersion", "GameSessionManager"]
    assert blob.path() == "data.a7s/MetaGameManager/GameSessions/#1/SessionData/BinaryData"
    assert [i.m_name for i in nodes.search(roots, "binarydata")] == ["BinaryData"]


def test_a_values_readings_follow_its_size():
    found = dict(nodes.readings(struct.pack("<I", 41)))
    assert found["u32"] == "41" and found["i32"] == "41" and "u16" not in found
    assert dict(nodes.readings("Tester".encode("utf-16-le")))["UTF-16"] == "Tester"
    assert not nodes.nested_document(b"short") and nodes.nested_document(filedb.write(filedb.STag("", [
        filedb.SAttrib("A", bytes(80))])))  # fmt: skip
