"""An archive mod opened in the asset editor (assetedit/archiveimport.py): a synthetic 2070 tree, an archive that
changes a value, adds and removes an asset, edits a text and brings a file no form holds."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes import asset_xml
from tests.sources.sourcesfakes import asset_list, old_game

from annotoolbox.assetedit import archiveimport, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.formats.rda import write_v20


def mod_archive(path: Path) -> Path:
    """assets.xml with the hut's upkeep set, the stone removed and a new good after the wood; one text changed."""
    assets = asset_list().replace("<Price>12</Price>", "<Price>12</Price>", 1)
    assets = assets.replace(
        asset_xml("Good", 1002, "Stone"), asset_xml("Good", 3002, "Brick", "<Product><Price>7</Price></Product>")
    )
    assets = assets.replace("<Kinds>GameObject</Kinds>", "<Kinds>GameObject</Kinds><Upkeep>8</Upkeep>")
    path.write_bytes(
        write_v20(
            {
                "data/config/game/assets.xml": assets.encode("utf-8"),
                "data/loca/eng/txt/buildings.txt": b"\xff\xfe" + "100=New name\r\n101=Other\r\n".encode("utf-16-le"),
                "data/graphics/extra.bin": b"carried",
            }
        )
    )
    return path


@pytest.fixture
def imported(tmp_path):
    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    result = archiveimport.import_archive(game, mod_archive(tmp_path / "My Mod.rda"), tmp_path / "carry")
    return game, result


def test_the_difference_to_the_game_is_a_change_set(imported):
    _game, result = imported
    changes = result.changes
    assert changes.m_game == "anno2070" and changes.m_mod_id == "My-Mod" and result.archive == "My Mod.rda"
    assert 1002 in changes.m_removed and 3002 in changes.m_new and 2001 in changes.m_assets
    assert "<Upkeep>8</Upkeep>" in str(changes.m_assets[2001])  # a block of Building, as the diff finds the finest form
    assert changes.m_texts == {"english": {"100": "New name"}}


def test_a_file_no_form_holds_is_carried_and_written_with_the_asset_files(imported):
    game, result = imported
    assert result.carried == ["data/graphics/extra.bin"]
    out = native.render(game.document(result.changes))
    assert out["data/graphics/extra.bin"] == b"carried" and "data/config/game/assets.xml" in out
    assert (
        b"<Upkeep>8</Upkeep>" in out["data/config/game/assets.xml"] and b"Brick" in out["data/config/game/assets.xml"]
    )
    assert any("New name" in v.decode("utf-16-le", "ignore") for k, v in out.items() if k.endswith(".txt"))


def test_a_second_import_carries_the_archives_files_only(tmp_path):
    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    stale = tmp_path / "carry" / "data" / "graphics" / "gone.bin"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left by an earlier import")
    result = archiveimport.import_archive(game, mod_archive(tmp_path / "My Mod.rda"), tmp_path / "carry")
    assert sorted(f.name for f in (tmp_path / "carry").rglob("*.bin")) == ["extra.bin"]
    assert "data/graphics/gone.bin" not in native.render(game.document(result.changes))


def test_the_summary_names_the_archive(imported):
    lines = imported[1].lines()
    assert "My Mod.rda" in lines[0] and any("assets:" in line for line in lines)


def test_a_file_that_is_no_archive_is_said(tmp_path):
    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    bad = tmp_path / "bad.rda"
    bad.write_bytes(b"not an archive at all")
    with pytest.raises(ValueError, match="bad.rda"):
        archiveimport.import_archive(game, bad, tmp_path / "carry")


def test_the_window_imports_an_archive_for_an_archive_game_only(tmp_path):
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from annotoolbox.assetedit.assetwindow import CAssetWindow
    from annotoolbox.suite.settings import CSettings

    settings = CSettings(workspace=str(tmp_path / "ws"), anno2070=str(old_game(tmp_path / "game")))
    window = CAssetWindow(settings, tmp_path / "settings.json")
    window.open("anno2070", is_waiting=True)
    assert window.m_acts["import_archive"].isEnabled() and not window.m_acts["import"].isEnabled()
    window.import_archive(mod_archive(tmp_path / "My Mod.rda"), is_waiting=True)
    assert window.m_last_import.archive == "My Mod.rda" and window.m_doc.m_changes.m_mod_id == "My-Mod"
    assert window.m_doc.exists(3002) and not window.m_doc.exists(1002)
    assert (tmp_path / "ws" / "imported-archives" / "My Mod" / "data" / "graphics" / "extra.bin").is_file()


def test_a_member_that_names_a_place_outside_the_mod_is_left_out(tmp_path):
    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    archive = tmp_path / "evil.rda"
    archive.write_bytes(
        write_v20({"data/graphics/ok.bin": b"fine", "../escaped.bin": b"bad", "c:/windows/x.bin": b"bad"})
    )
    result = archiveimport.import_archive(game, archive, tmp_path / "carry")
    assert result.carried == ["data/graphics/ok.bin"]
    written = sorted(f.name for f in tmp_path.rglob("*.bin"))
    assert written == ["ok.bin"]
    assert sum("outside the mod" in line for line in result.lost) == 2


def _patch_index(archive_name: str, position: int, path: str) -> bytes:
    """A ``file.db`` whose one entry points at ``position`` in the archive ``archive_name``."""
    import struct

    from annotoolbox.formats import filedb
    from annotoolbox.formats.filedb import SAttrib, STag

    def u16(text: str) -> bytes:
        return text.encode("utf-16-le")

    entry = STag(
        "#1",
        [SAttrib("FileName", u16(path)), SAttrib("ArchiveFileIndex", struct.pack("<I", 1)),
         SAttrib("Position", struct.pack("<Q", position)), SAttrib("UncompressedSize", struct.pack("<Q", 9))],
    )  # fmt: skip
    tree = STag("FileTree", [STag("PathMap", [SAttrib("#32768", u16("n")), entry])])
    names = ("maindata/data0.rda", archive_name)
    archives = STag("ArchiveFiles", [STag("#1", [SAttrib("#32768", u16(n)), SAttrib("#32768", b"\0")]) for n in names])
    return filedb.write(STag("", [STag("ArchiveMap", [tree, archives])]), version=1)


def test_a_patch_archives_members_take_their_game_paths_from_the_index_entry(tmp_path):
    from annotoolbox.formats.rda import CRdaArchive
    from annotoolbox.othergames.index2205 import CArchiveIndex

    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    plain = mod_archive(tmp_path / "plain.rda")
    wanted = CRdaArchive(plain).read("data/config/game/assets.xml")
    odd = tmp_path / "patch1.rda"
    odd.write_bytes(write_v20({"blob/1": wanted}))
    position = CRdaArchive(odd).entries["blob/1"].offset
    index = CArchiveIndex(_patch_index("patch1.rda", position, "data/config/game/assets.xml"))
    assert index.paths_in("patch1.rda") == {position: "data/config/game/assets.xml"}
    assert index.paths_in("other.rda") == {}
    result = archiveimport.import_archive(game, odd, tmp_path / "carry", index=index)
    assert 3002 in result.changes.m_new and result.carried == []  # the member was read as the game's asset file
    again = archiveimport.import_archive(game, odd, tmp_path / "carry2")  # without the index it is only a file
    assert again.carried == ["blob/1"]


def test_an_asset_file_that_is_not_valid_xml_is_one_finding_and_the_rest_is_imported(tmp_path):
    game = open_game("anno2070", old_game(tmp_path / "game"), tmp_path / "cache")
    archive = tmp_path / "Broken.rda"
    texts = b"\xff\xfe" + "100=New name".encode("utf-16-le")
    members = {"data/config/game/assets.xml": b"<AssetList><Asset>", "data/loca/eng/txt/buildings.txt": texts}
    archive.write_bytes(write_v20(members))
    result = archiveimport.import_archive(game, archive, tmp_path / "carry")
    assert [line for line in result.lost if "not valid XML" in line and "data/config/game/assets.xml" in line]
    assert result.changes.m_texts == {"english": {"100": "New name"}} and not result.changes.m_assets
