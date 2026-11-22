"""``file_h.db``, the archive map: which archive the game reads a path from, and the deleted flag (docs/formats/archive-map.md)."""

import struct
from pathlib import Path

import pytest
from tests.formats.formatfakes import write_rda

from annotoolbox.formats import filedb
from annotoolbox.sources import anno117


def entry(path: str, archive: int | None, flags: int | None = None) -> list:
    tag = filedb.STag("#1", [filedb.SAttrib("FileName", path.encode("utf-16-le"))])
    if archive is not None:
        tag.children.append(filedb.SAttrib("ArchiveFileIndex", struct.pack("<I", archive)))
    if flags is not None:
        tag.children.append(filedb.SAttrib("Flags", struct.pack("<I", flags)))
    return [filedb.SAttrib("#32768", bytes(8)), tag]


def archive_map(archives: list[str], entries: list[list]) -> bytes:
    files = filedb.STag("ArchiveFiles")
    for name in archives:
        files.children.append(
            filedb.STag("#1", [filedb.SAttrib("#32768", name.encode("utf-16-le")), filedb.SAttrib("#32768", b"\0")])
        )
    file_map = filedb.STag("FileMap", [node for e in entries for node in e])
    return filedb.write(filedb.STag("", [filedb.STag("ArchiveMap", [file_map, files])]))


@pytest.fixture
def install(tmp_path: Path) -> Path:
    maindata = tmp_path / "maindata"
    maindata.mkdir()
    write_rda(
        maindata / "config.rda", [(0, {"data/a.txt": b"config", "data/b.txt": b"config", "data/c.txt": b"config"})]
    )
    write_rda(maindata / "zz_patchfiles_200.rda", [(0, {"data/A.txt": b"patch200", "data/b.txt": b"patch200"})])
    write_rda(maindata / "zz_patchfiles_300.rda", [(0, {"data/a.txt": b"patch300", "data/d.txt": b"patch300"})])
    return tmp_path


def test_without_a_map_the_later_archive_wins(install: Path):
    files = anno117.CAnno117Files(install)
    assert files.archive_map is None
    assert files.read("data/a.txt") == b"patch300"


def test_the_map_names_the_archive(install: Path):
    names = ["maindata/config.rda", "maindata/zz_patchfiles_200.rda", "maindata/zz_patchfiles_300.rda"]
    data = archive_map(names, [entry("data/a.txt", 1), entry("data/b.txt", None), entry("data/d.txt", 2)])
    (install / "maindata" / "file_h.db").write_bytes(data)
    files = anno117.CAnno117Files(install)
    assert files.read("data/a.txt") == b"patch200"  # the map, not the archive order (and the path's case)
    assert files.read("data/b.txt") == b"config"  # a missing index is the first archive
    assert files.read("data/d.txt") == b"patch300"
    assert files.read("data/c.txt") == b"config"  # not in the map: the name rule


def test_a_deleted_path_is_not_a_file(install: Path):
    names = ["maindata/config.rda", "maindata/zz_patchfiles_200.rda", "maindata/zz_patchfiles_300.rda"]
    (install / "maindata" / "file_h.db").write_bytes(archive_map(names, [entry("data/a.txt", 2, flags=8)]))
    files = anno117.CAnno117Files(install)
    assert not files.exists("data/a.txt") and files.exists("data/b.txt")
    assert files.archive_map["data/a.txt"].deleted


def test_a_path_in_an_archive_not_installed_is_not_a_file(install: Path):
    (install / "maindata" / "file_h.db").write_bytes(archive_map(["maindata/fr_fr0.rda"], [entry("data/c.txt", 0)]))
    assert not anno117.CAnno117Files(install).exists("data/c.txt")


def test_a_short_index_or_flags_field_raises_the_readers_own_error():
    for field, size in (("ArchiveFileIndex", 2), ("Flags", 3)):
        good = entry("data/a.txt", 0, flags=0)
        bad = [good[0], filedb.STag("#1", [good[1].children[0], filedb.SAttrib(field, bytes(size))])]
        with pytest.raises(anno117.CArchiveMapError):
            anno117.read_archive_map(archive_map(["maindata/config.rda"], [bad]))
    with pytest.raises(anno117.CArchiveMapError):
        anno117.read_archive_map(b"not a file database")


def test_not_an_archive_map():
    with pytest.raises(anno117.CArchiveMapError):
        anno117.read_archive_map(filedb.write(filedb.STag("", [filedb.STag("Other")])))


def test_every_archive_holding_a_path_and_the_deleted_ones_are_listed(install: Path):
    names = ["maindata/config.rda", "maindata/zz_patchfiles_200.rda", "maindata/zz_patchfiles_300.rda"]
    data = archive_map(names, [entry("data/a.txt", 1), entry("data/d.txt", 2, flags=8)])
    (install / "maindata" / "file_h.db").write_bytes(data)
    files = anno117.CAnno117Files(install)
    assert files.holders("DATA/A.txt") == ["config.rda", "zz_patchfiles_200.rda", "zz_patchfiles_300.rda"]
    assert files.archive_of("data/a.txt") == "zz_patchfiles_200.rda" and files.deleted() == ["data/d.txt"]
    from annotoolbox.sources import gametree

    tree = gametree.open_game("anno117", install)
    assert tree.origins("data/a.txt") == ["zz_patchfiles_200.rda", "zz_patchfiles_300.rda", "config.rda"]
    assert tree.m_deleted == ["data/d.txt"] and tree.origins("data/d.txt") == ["zz_patchfiles_300.rda"]


def test_an_asset_block_ends_at_the_close_tag_of_its_own_depth():
    assets = anno117.CAnno117Assets.__new__(anno117.CAnno117Assets)
    inner = "<Asset><Values><Standard><GUID>7</GUID></Standard></Values></Asset>"
    outer = f"<Asset><Values><Standard><GUID>5</GUID></Standard><Quest>{inner}</Quest></Values></Asset>"
    assets.m_text = f"<Assets><Asset><Values><Standard><GUID>4</GUID></Standard></Values></Asset>{outer}</Assets>"
    assert assets.asset_xml(5) == outer
    assert assets.asset_xml(7) == inner
    with pytest.raises(KeyError):
        assets.asset_xml(6)
