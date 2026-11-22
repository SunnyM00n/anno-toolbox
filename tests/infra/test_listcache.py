"""The lists kept between starts (``infra/listcache``): loaded while the signature of the inputs stands, rebuilt when a
file is added, removed or changed, and a damaged file is a rebuild, never trusted."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.formats.formatfakes import write_rda

from annotoolbox.formats import rda
from annotoolbox.infra import buildcache, listcache
from annotoolbox.sources import anno117
from annotoolbox.sources.overlay import CSourceTree


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANNO2070_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("ANNO2070_NO_CACHE", raising=False)
    return tmp_path / "cache"


def test_a_list_is_built_once_while_its_signature_stands(tmp_path: Path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"1234")
    built = []

    def build():
        built.append(1)
        return {"names": ["x", "y"]}

    sig = listcache.files_signature([source], "v1")
    assert listcache.cached("t", "scope", sig, build) == {"names": ["x", "y"]}
    assert listcache.cached("t", "scope", sig, build) == {"names": ["x", "y"]}
    assert len(built) == 1


def test_a_changed_size_or_time_or_a_new_or_removed_file_changes_the_signature(tmp_path: Path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"1234")
    base = listcache.files_signature([a], "v")
    assert listcache.files_signature([a], "v") == base
    assert listcache.files_signature([a], "w") != base  # the reader's version
    os.utime(a, ns=(1, 1))
    touched = listcache.files_signature([a], "v")
    assert touched != base
    a.write_bytes(b"12345")
    os.utime(a, ns=(1, 1))
    assert listcache.files_signature([a], "v") not in (base, touched)
    b.write_bytes(b"x")
    two = listcache.files_signature([a, b], "v")
    assert two != listcache.files_signature([a], "v")
    b.unlink()
    assert listcache.files_signature([a, b], "v") != two  # a file that went away counts


def test_a_folder_signature_sees_a_new_file_in_a_subfolder(tmp_path: Path):
    (tmp_path / "mod" / "data").mkdir(parents=True)
    (tmp_path / "mod" / "data" / "a.xml").write_text("1")
    before = listcache.folder_signature(tmp_path / "mod")
    assert listcache.folder_signature(tmp_path / "mod") == before
    (tmp_path / "mod" / "data" / "b.xml").write_text("2")
    assert listcache.folder_signature(tmp_path / "mod") != before
    assert listcache.folder_signature(tmp_path / "mod", suffixes=(".png",)) != before


def test_a_damaged_file_is_rebuilt(cache: Path):
    sig = listcache.files_signature([], "v")
    listcache.store("t", "scope", sig, [1, 2, 3])
    (stored,) = (cache / listcache.LISTS_DIR).glob("t-*.pkl")
    for damaged in (b"", b"not a list file", stored.read_bytes()[:40], listcache.MAGIC + b"\xff\n" + b"junk"):
        stored.write_bytes(damaged)
        assert listcache.load("t", "scope", sig) is None
    assert listcache.cached("t", "scope", sig, lambda: [4]) == [4]
    assert listcache.load("t", "scope", sig) == [4]


def test_another_signature_is_a_miss_and_the_new_list_replaces_the_old(cache: Path):
    listcache.store("t", "scope", "one", [1])
    assert listcache.load("t", "scope", "two") is None
    listcache.store("t", "scope", "two", [2])
    assert listcache.load("t", "scope", "one") is None and listcache.load("t", "scope", "two") == [2]
    assert len(list((cache / listcache.LISTS_DIR).glob("t-*.pkl"))) == 1


def test_a_bypassed_cache_neither_reads_nor_writes(cache: Path, monkeypatch):
    listcache.store("t", "scope", "one", [1])
    monkeypatch.setenv("ANNO2070_NO_CACHE", "1")
    assert listcache.load("t", "scope", "one") is None
    listcache.store("t", "other", "one", [2])
    monkeypatch.delenv("ANNO2070_NO_CACHE")
    assert listcache.load("t", "other", "one") is None and listcache.load("t", "scope", "one") == [1]


def test_clearing_the_lists_group_removes_them(cache: Path):
    listcache.store("t", "scope", "one", [1])
    assert buildcache.folder_sizes()["lists"][0] == 1
    buildcache.clear(["lists"])
    assert listcache.load("t", "scope", "one") is None


# The 117 install


@pytest.fixture
def install(tmp_path: Path) -> Path:
    maindata = tmp_path / "maindata"
    maindata.mkdir()
    write_rda(maindata / "config.rda", [(0, {"data/a.txt": b"config", "data/b.txt": b"config"})])
    write_rda(maindata / "zz_patchfiles_200.rda", [(0, {"data/A.txt": b"patch", "data/c.txt": b"patch"})])
    return tmp_path


@pytest.fixture
def parsed(monkeypatch) -> list[str]:
    """The archives whose index was parsed from the file (not read from a kept list)."""
    found: list[str] = []
    original = rda.CRdaArchive._parse_index

    def spy(self):
        found.append(self.m_path.name)
        return original(self)

    monkeypatch.setattr(rda.CRdaArchive, "_parse_index", spy)
    return found


def test_the_117_list_is_kept_and_no_archive_is_opened_the_second_time(install: Path, parsed: list[str]):
    first = anno117.CAnno117Files(install)
    assert sorted(set(parsed)) == ["config.rda", "zz_patchfiles_200.rda"]
    parsed.clear()
    second = anno117.CAnno117Files(install)
    assert parsed == []
    assert second.iter_paths("data/") == first.iter_paths("data/")
    assert second.archive_of("data/a.txt") == "zz_patchfiles_200.rda"
    assert second.read("data/a.txt") == b"patch" and second.read("data/b.txt") == b"config"
    assert second.holders("data/a.txt") == ["config.rda", "zz_patchfiles_200.rda"]
    assert second.deleted() == first.deleted() == []


def test_a_new_changed_or_removed_117_archive_makes_the_list_again(install: Path, parsed: list[str]):
    anno117.CAnno117Files(install)
    maindata = install / "maindata"
    write_rda(maindata / "zz_patchfiles_300.rda", [(0, {"data/new.txt": b"new"})])
    parsed.clear()
    files = anno117.CAnno117Files(install)
    assert parsed and files.exists("data/new.txt")
    anno117.CAnno117Files(install)
    parsed.clear()
    assert anno117.CAnno117Files(install).exists("data/new.txt") and parsed == []
    os.utime(maindata / "zz_patchfiles_300.rda", ns=(5, 5))  # a changed time is a change
    anno117.CAnno117Files(install)
    assert parsed
    (maindata / "zz_patchfiles_300.rda").unlink()
    parsed.clear()
    assert not anno117.CAnno117Files(install).exists("data/new.txt")


def test_a_damaged_117_list_is_made_again(install: Path, cache: Path, parsed: list[str]):
    anno117.CAnno117Files(install)
    for stored in (cache / listcache.LISTS_DIR).glob("anno117-index-*.pkl"):
        stored.write_bytes(stored.read_bytes()[:30])
    parsed.clear()
    files = anno117.CAnno117Files(install)
    assert parsed and files.read("data/c.txt") == b"patch"


# The 2070 overlay


def test_the_overlay_list_follows_its_archives_and_folders(source_root: Path):
    first = CSourceTree(source_root)
    again = CSourceTree(source_root)
    assert again._m_index == first._m_index
    assert sorted(again.iter_paths()) == sorted(first.iter_paths())
    folders = [a for a in first.m_archives if a.rda is None]
    assert folders
    new = folders[0].path / "data" / "added_after_listing.txt"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("x")
    changed = CSourceTree(source_root)
    assert changed.exists("data/added_after_listing.txt") and not first.exists("data/added_after_listing.txt")
    new.unlink()
    assert not CSourceTree(source_root).exists("data/added_after_listing.txt")


def test_a_damaged_overlay_list_is_made_again(source_root: Path, cache: Path):
    first = CSourceTree(source_root)
    for stored in (cache / listcache.LISTS_DIR).glob("overlay-index-*.pkl"):
        stored.write_bytes(b"torn")
    assert CSourceTree(source_root)._m_index == first._m_index
