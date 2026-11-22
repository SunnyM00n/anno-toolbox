"""The archive layout kept as data and the archive written again (formats/rdalayout.py): an archive the writers made
comes back byte for byte, in every version and block kind; what cannot be kept is named."""

from __future__ import annotations

from pathlib import Path

import pytest

from annotoolbox.formats import rda, rdalayout

FILES = {
    "data/a.txt": b"alpha " * 40,
    "data/b.txt": b"beta " * 55,
    "data/sub/c.cfg": bytes(range(200)) * 3,
    "data/d.xml": b"<x/>" * 90,
}
STAMPS = {"data/a.txt": 1_300_000_000, "data/b.txt": 1_300_000_050, "data/sub/c.cfg": 5, "data/d.xml": 77}


def _write(dest: Path, version: rda.SRdaVersion, blocks, dead=(), dedup=False, level=1, resident=False) -> Path:
    with rda.CRdaWriter(dest, version, STAMPS, dedup=dedup, allow_resident=resident) as w:
        for flags, paths in blocks:
            w.add_block([(p, FILES[p]) for p in paths], flags, level)
        if dead:
            w.add_deleted(dead)
    return dest


def _members(archive: rda.CRdaArchive) -> dict[str, bytes]:
    return {p: archive.read(p) for p in archive.entries}


def _again(tmp_path: Path, original: Path) -> tuple[bytes, rdalayout.SLayout]:
    archive = rda.CRdaArchive(original)
    layout = rdalayout.read_layout(archive)
    rdalayout.pack(layout, _members(archive), tmp_path / "again.rda")
    return (tmp_path / "again.rda").read_bytes(), layout


@pytest.mark.parametrize(
    ("version", "blocks", "dead", "level"),
    [
        (
            rda.V2_0,
            [(3, ["data/a.txt", "data/b.txt"]), (3, ["data/sub/c.cfg"]), (2, ["data/d.xml"])],
            ["data/old.txt"],
            1,
        ),
        (rda.V2_0, [(3, list(FILES))], [], 6),
        (
            rda.V2_2,
            [(0, ["data/a.txt"]), (1, ["data/b.txt", "data/d.xml"]), (0, ["data/sub/c.cfg"])],
            ["data/x", "data/y"],
            9,
        ),
        (rda.V2_2, [(2, list(FILES))], [], 1),
    ],
)
def test_an_archive_the_writer_made_is_written_again_byte_for_byte(tmp_path, version, blocks, dead, level):
    original = _write(tmp_path / "o.rda", version, blocks, dead, level=level)
    data, layout = _again(tmp_path, original)
    assert data == original.read_bytes()
    assert layout.version == version.name and [f for f, _n in layout.blocks][: len(blocks)] == [f for f, _ in blocks]
    assert layout.deleted == list(dead) and layout.stamps["data/a.txt"] == STAMPS["data/a.txt"]
    if any(f & rda.FLAG_COMPRESSED for f, _ in blocks):
        assert layout.level == level


def test_resident_blocks_and_shared_copies_are_kept(tmp_path):
    resident = _write(
        tmp_path / "r.rda", rda.V2_0, [(7, ["data/a.txt", "data/b.txt"]), (3, ["data/d.xml"])], resident=True
    )
    data, layout = _again(tmp_path, resident)
    assert data == resident.read_bytes() and layout.blocks[0][0] == 7
    twin = tmp_path / "t.rda"
    with rda.CRdaWriter(twin, rda.V2_0, 0, dedup=True) as w:
        w.add_block([("data/one", b"same" * 30), ("data/two", b"same" * 30)], 3)
    data, layout = _again(tmp_path, twin)
    assert layout.dedup and data == twin.read_bytes()
    flat = tmp_path / "f.rda"
    with rda.CRdaWriter(flat, rda.V2_0, 0, dedup=False) as w:
        w.add_block([("data/one", b"same" * 30), ("data/two", b"same" * 30)], 3)
    data, layout = _again(tmp_path, flat)
    assert not layout.dedup and data == flat.read_bytes()


@pytest.mark.parametrize("compress", [True, False])
def test_a_1701_archive_is_written_again(tmp_path, compress):
    original = tmp_path / "v1.rda"
    rda.write_v11_file(original, list(FILES.items()), STAMPS, compress=compress)
    data, layout = _again(tmp_path, original)
    assert data == original.read_bytes()
    assert layout.default_flags == (rda.V1_FLAGS_ZLIB if compress else rda.V1_FLAGS_STORED)


def test_new_members_get_a_block_at_the_end_and_missing_ones_go(tmp_path):
    original = _write(
        tmp_path / "o.rda", rda.V2_0, [(3, ["data/a.txt", "data/b.txt"]), (3, ["data/d.xml"])], ["data/gone"]
    )
    layout = rdalayout.read_layout(rda.CRdaArchive(original))
    members = {
        **{p: FILES[p] for p in ("data/a.txt", "data/d.xml")},
        "data/new.txt": b"new " * 20,
        "data/gone": b"back " * 9,
    }
    rdalayout.pack(layout, members, tmp_path / "n.rda", {"data/new.txt": 99})
    out = rda.CRdaArchive(tmp_path / "n.rda")
    assert list(out.entries) == ["data/a.txt", "data/d.xml", "data/gone", "data/new.txt"]
    assert out.read("data/new.txt") == b"new " * 20 and out.entries["data/new.txt"].mtime == 99
    assert out.entries["data/a.txt"].mtime == STAMPS["data/a.txt"] and "data/b.txt" not in out.entries


def test_the_layout_survives_json_and_a_rename(tmp_path):
    original = _write(tmp_path / "o.rda", rda.V2_2, [(0, ["data/a.txt", "data/b.txt"])], ["data/x"])
    layout = rdalayout.read_layout(rda.CRdaArchive(original))
    back = rdalayout.SLayout.from_json(layout.to_json())
    assert (back.blocks, back.order, back.stamps, back.level) == (
        layout.blocks,
        layout.order,
        layout.stamps,
        layout.level,
    )
    lowered = layout.mapped(lambda p: None if p == "data/b.txt" else p.upper().lower().replace("data/", "d/"))
    assert lowered.order == ["d/a.txt", "d/x"] and lowered.blocks == [(0, 1), (8, 1)]


def test_what_cannot_be_kept_is_named(tmp_path):
    original = _write(tmp_path / "o.rda", rda.V2_0, [(3, ["data/a.txt", "data/b.txt"]), (3, ["data/d.xml"])], level=6)
    layout = rdalayout.read_layout(rda.CRdaArchive(original))
    other = rdalayout.SLayout.from_json(layout.to_json())
    other.order[0], other.order[1] = other.order[1], other.order[0]
    other.stamps["data/a.txt"] += 1
    other.blocks = [(3, 3)]
    other.dedup = True
    found = rdalayout.differences(layout, other)
    assert {rdalayout.ORDER, rdalayout.BLOCKS, rdalayout.STAMPS, rdalayout.SHARING} <= set(found)
    assert rdalayout.differences(layout, layout) == []
    # a different level gives other stored sizes: named as the compression
    rebuilt = tmp_path / "l1.rda"
    rdalayout.pack(layout, _members(rda.CRdaArchive(original)), rebuilt)
    again = rdalayout.read_layout(rda.CRdaArchive(rebuilt))
    assert layout.level == 6 and again.level == 6 and rdalayout.differences(layout, again) == []
    forced = rdalayout.SLayout.from_json({**layout.to_json(), "level": 1})
    rdalayout.pack(forced, _members(rda.CRdaArchive(original)), rebuilt)
    assert rdalayout.COMPRESSION in rdalayout.differences(layout, rdalayout.read_layout(rda.CRdaArchive(rebuilt)))
