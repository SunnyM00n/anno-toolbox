"""The Saves app's names for numbers, the decoded storages and the read-only summaries of the other games, on
synthetic data (no real save of 1800, 2205, 1404 or 1701 exists on the author's machine)."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.saveedit.saveeditfakes import fake_save117, rows, section

from annotoolbox.assetedit.schema import SDataSet, SSchema, SValueSpec
from annotoolbox.formats import savegame2070, storage117
from annotoolbox.infra.i18n import tr
from annotoolbox.saveedit import container, names, records, summary

# The storages


def test_a_large_storage_is_sorted_keys_by_gaps_then_amounts():
    assert storage117.decode("StrgLrg", struct.pack("<2I", 1010017, 20000)) == [(1010017, 20000)]
    pairs = storage117.decode("StrgLrg", struct.pack("<4I", 93770, 916246, 1, 66177))
    assert pairs == [(93770, 1), (1010017, 66177)]
    assert storage117.decode("StrgLrg", struct.pack("<4I", 2068, 0, 5, 6)) == [(2068, 5), (2069, 6)]


def test_a_small_storage_is_a_key_gap_bytes_and_amount_bytes():
    assert storage117.decode("StrgSmll", bytes.fromhex("7d080000001419")) == [(2173, 20), (2174, 25)]
    six = storage117.decode("StrgSmll", bytes.fromhex("17080000") + bytes([0x42, 0x20, 0, 1, 1]) + bytes(6))
    assert [k for k, _ in six] == [2071, 2138, 2171, 2172, 2174, 2176]


def test_a_storage_of_a_size_that_fits_no_layout_is_refused():
    for name, data in (("StrgLrg", bytes(12)), ("StrgSmll", bytes(6)), ("StrgSmll", bytes(3)), ("Other", bytes(8))):
        with pytest.raises(storage117.CStorageError):
            storage117.decode(name, data)


# Names


@dataclass
class FakeEntry:
    name: str


class FakeIndex:
    m_by_guid = {
        2068: FakeEntry("Good Oats"),
        91304: FakeEntry("ParticipantColor01 Lapiz"),
        2071: FakeEntry("Good Lavender"),
    }
    texts = {91304: "Lapiz Lazuli"}

    def element(self, guid: int):
        text = (
            f"<Asset><Values><Text><OasisId>{self.texts[guid]}</OasisId></Text></Values></Asset>"
            if guid in self.texts
            else "<Asset/>"
        )
        return ET.fromstring(text)


class FakeAssetGame:
    m_index = FakeIndex()
    m_schema = SSchema("anno117")

    def __init__(self) -> None:
        self.m_schema.data_sets["DCTaxes"] = SDataSet("DCTaxes", [("Spare", "2"), ("Medium", "1")])
        from annotoolbox.assetedit.schema import SPropertySpec

        self.m_schema.properties["DifficultySetup"] = SPropertySpec(
            "DifficultySetup", (), [SValueSpec("SetupTaxes", "Choice", data_set="DCTaxes")]
        )

    def texts(self, language: str):
        return {"91304": "Lapiz Lazuli", "506097522914230528": "Roma"}


def test_names_come_from_the_games_assets_options_and_texts(tmp_path: Path):
    s = summary.summarize(container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117"))
    found = names.CGameNames(FakeAssetGame())
    assert found.asset(2068) == "Good Oats" and found.asset(91304) == "Lapiz Lazuli" and found.asset(1) == ""
    assert found.option("SetupTaxes", "1") == "Medium" and found.option("SetupTaxes", "9") == ""
    names.apply(s, found)
    players = section(s, "Players").table.rows[0]
    assert records.shown(players[2]) == "Lapiz Lazuli (91304)" and records.shown(players[3]) == "92"  # no name: number
    assert dict(section(s, "Game setup").named)["SetupTaxes"].shown() == "Medium (1)"
    assert records.shown(section(s, "Participants").table.rows[0][0]) == "41"


def test_the_stock_of_each_island_is_decoded_and_named(tmp_path: Path):
    save = container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117")
    islands = summary.sessions(save)
    first, second = islands.table.rows[0], islands.table.rows[1]
    assert str(first[5]) == "2068: 186, 2071: 5" and first[5].m_keys == (2068, 2071) and first[5].m_amounts == (186, 5)
    assert str(second[5]) == "2173: 20, 2174: 25"
    assert records.shown(first[3]) == "506097522914230528"
    labels = names.CGameNames(FakeAssetGame())
    assert labels.resolve(first[5]) == "Good Oats 186, Good Lavender 5"
    assert labels.resolve(first[3]) == "Roma"
    assert labels.resolve(second[5]) == "2173 20, 2174 25"


def test_the_meta_storage_goods_other_than_money_are_listed(tmp_path: Path):
    s = summary.summarize(container.open_file(fake_save117(tmp_path / "P" / "a.a8s"), "anno117"))
    assert section(s, "Participants").table.rows[0][3].m_keys == (91372,)


def test_no_install_folder_gives_no_names(tmp_path: Path):
    @dataclass
    class FakeSettings:
        anno117: str = ""
        workspace: str = ""

    assert names.open_names(FakeSettings(), "anno117") is None
    assert names.open_names(FakeSettings(anno117=str(tmp_path / "missing")), "anno117") is None


# The other games


def test_an_old_game_save_shows_its_world(tmp_path: Path):
    world = (
        b"<WorldConfig><Width>384</Width><Height>384</Height><m_IsSaveGame>1</m_IsSaveGame><Islands>"
        b"<k>1</k><v><IslandConfig><Name>Home</Name><Filename>data/levels/n.isd</Filename><PositionX>16</PositionX>"
        b"<PositionZ>32</PositionZ><Width>80</Width><Height>80</Height></IslandConfig></v></Islands>"
        b"<GameData><PlayerInfos><k>0</k><v><m_Type>1</m_Type><m_Profile>7</m_Profile><m_Color>3</m_Color></v>"
        b"</PlayerInfos></GameData></WorldConfig>"
    )
    plain, packed = tmp_path / "a.sww", tmp_path / "b.sww"
    plain.write_bytes(world)
    packed.write_bytes(savegame2070.pack(world))
    for path in (plain, packed):
        s = summary.summarize(container.open_file(path, "anno1404"))
        assert rows(section(s, "Save"))["World size"] == "384 x 384"
        assert section(s, "Islands").table.rows == [["1", "Home", "data/levels/n.isd", "16, 32", "80 x 80"]]
        assert section(s, "Players").table.rows == [["0", "1", "7", "3"]]
        assert tr(s.m_sections[-1].title) == "Parts"
    older = b"<WorldConfig><m_Width>192</m_Width><m_Height>192</m_Height><Islands><k>1</k><v><m_Name>X</m_Name></v></Islands></WorldConfig>"
    plain.write_bytes(older)
    s = summary.summarize(container.open_file(plain, "anno1701"))
    assert rows(section(s, "Save"))["World size"] == "192 x 192" and section(s, "Islands").table.rows[0][1] == "X"
    plain.write_bytes(b"<Other/>")
    assert summary.summarize(container.open_file(plain, "anno1701")).m_notes


def test_an_1800_save_is_read_with_117s_tags_and_a_2205_one_as_an_outline(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "a.a7s")
    s = summary.summarize(container.open_file(path, "anno1800"))
    assert rows(section(s, "Save"))["Profile"] == "Tester" and any("117" in text for text, _ in s.m_notes)
    tags = {(row[0], row[2]) for row in section(s, "Documents").table.rows}
    assert ("data.a7s", "MetaGameManager") in tags and ("header.a7s", "CorporationProfile") in tags
    other = summary.summarize(container.open_file(path, "anno2205"))
    assert [tr(x.title) for x in other.m_sections] == ["Documents", "Parts"] and not any(
        r.rows for r in other.m_sections[:1]
    )


def test_names_are_in_the_interface_language_and_english_where_it_has_none(monkeypatch):
    class FakeTwoLanguageGame(FakeAssetGame):
        def texts(self, language: str):
            return {"german": {"91304": "Lapislazuli"}}.get(language, super().texts(language))

    monkeypatch.setattr(names.assetgame, "interface_language", lambda: "german")
    found = names.CGameNames(FakeTwoLanguageGame())
    assert found.m_language == "german" and found.asset(91304) == "Lapislazuli"
    assert found.text("506097522914230528") == "Roma"  # the German table lacks it
    assert names.CGameNames(FakeTwoLanguageGame(), "english").asset(91304) == "Lapiz Lazuli"
