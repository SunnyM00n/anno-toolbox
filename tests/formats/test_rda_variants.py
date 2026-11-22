"""Every RDA variant: written, read back, flags kept; and read from the installed games (docs/rda-format.md)."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from tests.formats.formatfakes import write_rda

from annotoolbox.formats import rda
from annotoolbox.formats.rda import (
    FLAG_COMPRESSED,
    FLAG_CONTIGUOUS,
    FLAG_DELETED,
    FLAG_ENCRYPTED,
    V1_1,
    V2_0,
    V2_2,
    CRdaArchive,
    CRdaError,
    CRdaWriter,
)

VERSIONS = pytest.mark.parametrize("version", [V2_0, V2_2], ids=["v2.0", "v2.2"])
FILES = {"data/a/one.xml": b"<One/>" * 40, "data/a/two.bin": bytes(range(256)) * 5 + b"\x07", "data/b/three.txt": b"3"}
FLAG_MIXES = [0, FLAG_COMPRESSED, FLAG_ENCRYPTED, FLAG_COMPRESSED | FLAG_ENCRYPTED]
FLAG_IDS = ["stored", "zlib", "xor", "zlib+xor"]


def _read_all(path: Path) -> dict[str, bytes]:
    arc = CRdaArchive(path)
    return {e.path: arc.read_entry(e) for e in arc}


@VERSIONS
@pytest.mark.parametrize("flags", FLAG_MIXES, ids=FLAG_IDS)
def test_the_streaming_writer_writes_every_flag_mix(tmp_path: Path, version: rda.SRdaVersion, flags: int):
    dest = tmp_path / "a.rda"
    with CRdaWriter(dest, version, timestamp=9) as w:
        w.add_block(FILES.items(), flags)
    arc = CRdaArchive(dest)
    assert arc.version is version and _read_all(dest) == FILES
    assert {e.flags for e in arc} == {flags} and {e.mtime for e in arc} == {9}
    if flags & FLAG_ENCRYPTED:  # also without compression: the bytes on disk are not the file
        assert b"<One/>" not in dest.read_bytes()
    else:
        assert FILES["data/b/three.txt"] in dest.read_bytes()


@VERSIONS
@pytest.mark.parametrize("flags", FLAG_MIXES, ids=FLAG_IDS)
def test_the_one_block_per_file_writers_write_every_flag_mix(tmp_path: Path, version: rda.SRdaVersion, flags: int):
    writer = rda.write_v20 if version is V2_0 else rda.write_v22
    dest = tmp_path / "a.rda"
    dest.write_bytes(writer(FILES, 3, flags=flags))
    assert _read_all(dest) == FILES and {e.flags for e in CRdaArchive(dest)} == {flags}
    assert len(list(CRdaArchive(dest).blocks())) == len(FILES)
    if flags & FLAG_ENCRYPTED:
        assert b"<One/>" not in dest.read_bytes()


def test_the_one_block_writers_refuse_flags_they_do_not_write():
    with pytest.raises(CRdaError, match="block flags"):
        rda.write_v22(FILES, flags=FLAG_CONTIGUOUS)


@VERSIONS
@pytest.mark.parametrize("flags", FLAG_MIXES, ids=FLAG_IDS)
def test_resident_blocks_round_trip_and_match_the_reference_layout(tmp_path: Path, version, flags: int):
    resident = flags | FLAG_CONTIGUOUS
    dest = tmp_path / "a.rda"
    with CRdaWriter(dest, version, timestamp=1234567890, allow_resident=True) as w:
        w.add_block(FILES.items(), resident, level=6)
    arc = CRdaArchive(dest)
    assert _read_all(dest) == FILES and {e.flags for e in arc} == {resident} and all(e.contiguous for e in arc)
    (block,) = arc.blocks()
    assert block.flags == resident and block.file_count == 3 and block.blob_size == sum(map(len, FILES.values()))
    assert len({e.blob_offset for e in arc}) == 1 and arc.entries["data/a/two.bin"].offset == len(
        FILES["data/a/one.xml"]
    )
    if not flags & FLAG_COMPRESSED:  # the test writer's blob and file headers are the same bytes
        ref = write_rda(tmp_path / "ref.rda", [(resident, FILES)], version)
        assert ref.read_bytes() == dest.read_bytes()


def test_resident_members_are_refused_unless_asked_for(tmp_path: Path):
    with pytest.raises(CRdaError, match="resident"), CRdaWriter(tmp_path / "a.rda") as w:
        w.add_block(FILES.items(), FLAG_CONTIGUOUS)
    assert not list(tmp_path.iterdir())


def test_a_resident_block_is_cut_at_the_byte_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rda, "RESIDENT_BYTES", 1000)
    files = {f"data/p/{i}.bin": bytes([i + 1]) * 600 for i in range(5)}
    dest = tmp_path / "a.rda"
    with CRdaWriter(dest, allow_resident=True) as w:
        w.add_block(files.items(), FLAG_CONTIGUOUS | FLAG_COMPRESSED)
    assert _read_all(dest) == files and [b.file_count for b in CRdaArchive(dest).blocks()] == [2, 2, 1]


@VERSIONS
def test_deleted_blocks_list_paths_the_archive_does_not_have(tmp_path: Path, version):
    dest = tmp_path / "a.rda"
    with CRdaWriter(dest, version) as w:
        w.add_block({"data/keep.txt": b"keep"}.items())
        w.add_deleted(["data/gone/a.txt", "data/gone/b.txt"])
    arc = CRdaArchive(dest)
    assert sorted(arc.entries) == ["data/keep.txt"] and arc.deleted == {"data/gone/a.txt", "data/gone/b.txt"}
    flags = [b.flags for b in arc.blocks()]
    assert flags == [FLAG_COMPRESSED, FLAG_DELETED]
    raw = dest.read_bytes()
    assert raw.count("data/gone/a.txt".encode("utf-16-le")) == 1  # the directory is stored, not compressed


def test_a_path_cannot_be_both_added_and_deleted(tmp_path: Path):
    with pytest.raises(CRdaError, match="twice"), CRdaWriter(tmp_path / "a.rda") as w:
        w.add_block({"data/x.txt": b"x"}.items())
        w.add_deleted(["data/x.txt"])
    with pytest.raises(CRdaError, match="cannot be found"), CRdaWriter(tmp_path / "b.rda") as w:
        w.add_deleted(["Data/X.txt"])


def test_the_last_block_decides_between_added_and_deleted(tmp_path: Path):
    write_rda(tmp_path / "a.rda", [(FLAG_COMPRESSED, {"data/x.txt": b"x"}), (FLAG_DELETED, {"data/x.txt": b"x"})])
    arc = CRdaArchive(tmp_path / "a.rda")
    assert "data/x.txt" not in arc and arc.deleted == {"data/x.txt"}
    write_rda(tmp_path / "b.rda", [(FLAG_DELETED, {"data/x.txt": b"x"}), (FLAG_COMPRESSED, {"data/x.txt": b"y"})])
    arc = CRdaArchive(tmp_path / "b.rda")
    assert arc.read("data/x.txt") == b"y" and not arc.deleted


def test_the_index_cache_keeps_the_deleted_paths(tmp_path: Path):
    dest = tmp_path / "a.rda"
    with CRdaWriter(dest) as w:
        w.add_block({"data/x.txt": b"x"}.items())
        w.add_deleted(["data/y.txt"])
    assert CRdaArchive(dest, cache_dir=tmp_path / "idx").deleted == {"data/y.txt"}
    again = CRdaArchive(dest, cache_dir=tmp_path / "idx")
    again._parse_index = lambda: (_ for _ in ()).throw(AssertionError("parsed instead of cached"))  # type: ignore[method-assign]
    assert again.deleted == {"data/y.txt"} and list(again.entries) == ["data/x.txt"]


@VERSIONS
def test_an_archive_with_one_member_per_block_kind_reads_back(tmp_path: Path, version):
    """Stored, zlib, xor and zlib+xor blocks, a resident one and a deleted one in one archive."""
    dest = tmp_path / "a.rda"
    parts = {flags: {f"data/f{flags}/{n}": d for n, d in FILES.items()} for flags in (*FLAG_MIXES, 4, 5, 6, 7)}
    with CRdaWriter(dest, version, allow_resident=True) as w:
        for flags, files in parts.items():
            w.add_block(files.items(), flags)
        w.add_deleted(["data/old/gone.bin"])
    expected = {p: d for files in parts.values() for p, d in files.items()}
    assert _read_all(dest) == expected and CRdaArchive(dest).deleted == {"data/old/gone.bin"}
    assert sorted({b.flags for b in CRdaArchive(dest).blocks()}) == [0, 1, 2, 3, 4, 5, 6, 7, 8]


def test_the_v20_writer_stops_at_four_gib(tmp_path: Path, monkeypatch):
    assert V2_0.max_value == 2**32 - 1 and V2_2.max_value == 2**64 - 1
    monkeypatch.setattr(rda.SRdaVersion, "max_value", property(lambda self: 300))
    dest = tmp_path / "a.rda"
    with pytest.raises(CRdaError, match="bytes a V2.0 field holds"), CRdaWriter(dest, V2_0) as w:
        w.add_block({"data/big.bin": os.urandom(400)}.items(), 0)
    assert not list(tmp_path.iterdir())


def test_the_v22_header_and_block_fields_are_where_the_games_have_them(tmp_path: Path):
    """First block offset at byte 784 (V2.2) / 1044 (V2.0), the first file's data right behind it, as in the games."""
    for version, pos in ((V2_2, 784), (V2_0, 1044)):
        raw = rda.write_v22(FILES) if version is V2_2 else rda.write_v20(FILES)
        assert raw[: len(version.magic)] == version.magic and version.first_block_pos == pos
        (first,) = struct.unpack_from("<" + version.uint, raw, pos)
        assert first > pos + version.uint_size
        (flags, count, *_rest) = struct.unpack_from("<II", raw, first)
        assert count == 1 and flags & FLAG_COMPRESSED


# Archives inside archives and archives in memory


def test_an_archive_in_memory_and_a_member_that_is_an_archive(tmp_path: Path):
    inner = rda.write_v22({"data/inner.txt": b"inside"}, 5)
    outer_path = tmp_path / "outer.rda"
    with CRdaWriter(outer_path) as w:
        w.add_block({"data/island.a7m": inner}.items(), 0)
    outer = CRdaArchive(outer_path)
    sub = outer.open_member("data/island.a7m")
    assert sub.version is V2_2 and sub.read("data/inner.txt") == b"inside" and sub.m_path.name == "outer.rda|island.a7m"
    mem = CRdaArchive.from_bytes(rda.write_v20({"data/x": b"y"}))
    assert mem.version is V2_0 and mem.read("data/x") == b"y"
    with pytest.raises(CRdaError, match="not an RDA"):
        _ = CRdaArchive.from_bytes(b"nope" * 20).version


# Version 1.1


def test_v11_archives_stored_and_compressed_in_memory_and_on_disk(tmp_path: Path):
    files = {"data/islands/test.isd": b"heights" * 300, "data/loca/eng/txt/menu.txt": b"x"}
    for compress, raw_flags in ((True, 7), (False, 6)):
        mem = rda.write_v11(files, 3, compress=compress)
        disk = rda.write_v11_file(tmp_path / f"d{raw_flags}.rda", files.items(), 3, compress=compress)
        assert disk.read_bytes() == mem and not list(tmp_path.glob("*.part"))
        arc = CRdaArchive(disk)
        assert arc.version is V1_1 and {e.path: arc.read_entry(e) for e in arc} == files
        assert {e.flags for e in arc} == {FLAG_COMPRESSED if compress else 0}
        table = rda.decrypt(mem[rda.V1_HEADER : rda.V1_HEADER + 2 * rda.V1_ENTRY.size], V1_1.seed)
        assert {rec[4] for rec in rda.V1_ENTRY.iter_unpack(table)} == {raw_flags}
        assert mem.endswith(bytes(rda.V1_TRAILER))


def test_v11_writes_files_from_disk_and_refuses_paths_it_cannot_hold(tmp_path: Path):
    src = tmp_path / "f.bin"
    src.write_bytes(b"from disk")
    dest = rda.write_v11_file(tmp_path / "a.rda", [("data/f.bin", src)])
    assert CRdaArchive(dest).read("data/f.bin") == b"from disk"
    for bad in ("data/" + "x" * 260, "data/€.txt"):
        with pytest.raises(CRdaError):
            rda.write_v11({bad: b"x"})
        with pytest.raises(CRdaError):
            rda.write_v11_file(tmp_path / "b.rda", [(bad, b"x")])
        assert not (tmp_path / "b.rda.part").exists() and not (tmp_path / "b.rda").exists()


def test_version_names():
    assert [rda.version_named(n) for n in ("1.1", "2.0", "2.2")] == [V1_1, V2_0, V2_2]
    with pytest.raises(CRdaError, match="unknown RDA version"):
        rda.version_named("2.1")


# The installed games: every variant that exists in the wild reads

# env var -> (archives under the install, versions, the block flags that must turn up across them)
GAMES = {
    "ANNO1404_DIR": (("maindata/*.rda", "addon/*.rda", "ahe/*.rda"), {"2.0", "2.2"}, {0, 2, 3, 7}),
    "ANNO1701_DIR": (("*.rda", "ahe/*.rda"), {"1.1", "2.2"}, set()),
    "ANNO2070_DIR": (("maindata/*.rda",), {"2.0"}, {0, 2, 3, 7, 8}),
    "ANNO1800_DIR": (("maindata/*.rda",), {"2.2"}, {0, 4, 8}),
    "ANNO2205_DIR": (("maindata/*.rda",), {"2.2"}, {0, 1, 2, 4, 8}),
    "ANNO117_DIR": (("maindata/*.rda",), {"2.2"}, {0, 8}),
}
SAMPLE_BYTES = 50_000_000  # members bigger than this are not read in a test


def _head(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read(64)


def _archives(root: Path, globs: tuple[str, ...]) -> list[Path]:
    """The RDA archives under ``root`` (2070's ``checksums.rda`` are text files with the extension)."""
    found = [p for g in globs for p in sorted(root.glob(g))]
    return [p for p in found if rda.detect_version(_head(p))]


def _sample(arc: CRdaArchive) -> int:
    """Read the smallest two members of every flags kind of ``arc``; the number read."""
    kinds: dict[tuple[int, bool], list[rda.SRdaEntry]] = {}
    for e in arc:
        kinds.setdefault((e.flags, e.contiguous), []).append(e)
    read = 0
    for entries in kinds.values():
        for e in sorted(entries, key=lambda e: e.size)[:2]:
            if e.size < SAMPLE_BYTES:
                assert len(arc.read_entry(e)) == e.size
                read += 1
    return read


@pytest.mark.parametrize("env", sorted(GAMES))
def test_the_installed_games_archives_read(env: str):
    root = Path(os.environ.get(env) or f"{env}-unset")
    if not root.is_dir():
        pytest.skip(f"needs the installed game ({env})")
    globs, versions, wanted = GAMES[env]
    archives = _archives(root, globs)
    assert archives, f"no .rda archives under {root}"
    seen: set[int] = set()
    for path in archives:
        arc = CRdaArchive(path)
        assert arc.version.name in versions, path
        if arc.version is not V1_1:
            seen |= {b.flags for b in arc.blocks()}
            assert all(not e.flags & FLAG_DELETED for e in arc)
        assert _sample(arc) >= (1 if len(arc) else 0), path
    assert wanted <= seen, f"flags {sorted(wanted - seen)} not found in {root}"
