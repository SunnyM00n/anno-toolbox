"""An island carried between Anno 117 and Anno 1800 (docs/island-regions.md "Between Anno 117 and Anno 1800"): no
displacement left on a 117 island made from an 1800 one, a material ID the source set does not list, the stored meshes
and height pages taken into the target's FileDB layout unchanged; on the installed games an Arctic island ported to
Latium."""

import struct
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from annotoolbox.editor import gametransfer, groundedit, islandconvert, largerdonor
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import regions, regiontables
from annotoolbox.islands.islandgrid import read_bits
from fakes import FakeFiles, asset_xml, installed
from test_regionconvert import CELTIC, ROMAN, _set, ids_word


def u32(n: int) -> bytes:
    return struct.pack("<I", n)


def heights(name: str, values: np.ndarray) -> Tag:
    n = values.shape[0]
    return Tag(
        name, [Attrib("Width", u32(n)), Attrib("Height", u32(n)), Attrib("HeightMap", values.astype("<i2").tobytes())]
    )


def island_117(size: int = 4) -> SimpleNamespace:
    """A 117 donor's terrain: heights, a displacement of its own, the coarse displacement map."""
    n = 2 * size + 1
    hm = np.arange(n * n).reshape(n, n) * 7
    tm = Tag("TerrainManager", [heights("HeightMap", hm), heights("DisplacementHeightMap", hm % 50 - 20)])
    coarse = Tag(
        "CoarseDisplacementMap", [Attrib("width", u32(n)), Attrib("height", u32(n)), Attrib("map", bytes(range(n * n)))]
    )
    return SimpleNamespace(gm=Tag("GameSessionManager", [tm]), rd=Tag("", [Tag("Terrain", [coarse])]), size=size)


def island_1800(size: int = 4) -> SimpleNamespace:
    """An 1800 source: heights only (1800 keeps no displacement layer)."""
    n = 2 * size + 1
    tm = Tag("TerrainManager", [heights("HeightMap", np.full((n, n), 3000))])
    return SimpleNamespace(gm=Tag("GameSessionManager", [tm]), rd=Tag("", [Tag("Terrain", [])]), size=size)


def test_a_117_island_made_from_an_1800_one_has_no_displacement():
    donor = island_117()
    done = gametransfer.fit_target(island_1800(), donor)
    assert {"DisplacementHeightMap", "CoarseDisplacementMap"} <= set(done)
    disp = np.frombuffer(donor.gm.path("TerrainManager/DisplacementHeightMap/HeightMap").data, "<i2")
    assert not disp.any()  # a layer on top of the heights: zero, never the heights again (they stood twice as high)
    assert set(donor.rd.path("Terrain/CoarseDisplacementMap/map").data) == {128}
    hm = np.frombuffer(donor.gm.path("TerrainManager/HeightMap/HeightMap").data, "<i2")
    assert hm.max() > 0  # the heights themselves are not the fit's business


def test_a_117_source_keeps_its_own_displacement_when_carried():
    donor, source = island_117(), island_117()
    before = donor.gm.path("TerrainManager/DisplacementHeightMap/HeightMap").data
    assert "DisplacementHeightMap" not in gametransfer.fit_target(source, donor)
    assert donor.gm.path("TerrainManager/DisplacementHeightMap/HeightMap").data == before


def test_an_id_the_source_set_does_not_list_goes_where_the_sea_bed_goes():
    table = regiontables.material_table(ROMAN, CELTIC)
    full = regiontables.with_unlisted(table, CELTIC)
    assert {k: full[k] for k in table} == table and len(full) == regiontables.ID_LIMIT
    assert full[31] == 21  # Albion's set has no underwater ground: its sand, as the family's fallback says
    underwater = CELTIC + _set((12, "", "None", "data/m/underwater_01.xml"))  # the entries of both
    assert regiontables.with_unlisted(table, underwater)[31] == 12
    assert regiontables.with_unlisted(table, "") == table


def test_an_unlisted_id_is_not_written_into_the_target_map():
    ids = np.full((4, 4), ids_word(31, 1, 0), np.uint32)  # the sea bed in ID 31, which the source set lacks
    alphas = np.full((8, 8), 31 | (8 << 5), np.uint32)
    table = regiontables.with_unlisted({7: 21, 1: 3}, CELTIC)
    ground = gametransfer.Ground(ids, alphas, 4, table, {7: 0, 1: 1}, {21: 0, 3: 1})
    _ids, _alphas, used = gametransfer.encode_map(ground, 2, 4)
    assert used == (1 << 21) | (1 << 3)


def mesh_file(version: int) -> bytes:
    """A stored mesh cluster of two nodes, one with a triangle (16-byte vertices, u16 indices)."""
    vertex = struct.pack("<4H8B", 32, 33000, 32, 0, 128, 255, 128, 255, 128, 128, 0, 0)
    node = Tag("#1", [Attrib("Indices", struct.pack("<3H", 0, 1, 2)), Attrib("Vertices", vertex * 3)])
    node.children += [Attrib("UsedMaterialBits", u32(1 << 4)), Tag("GroundObjects", [])]
    empty = Tag("#1", [Attrib("Indices", b""), Attrib("Vertices", b""), Attrib("UsedMaterialBits", u32(0))])
    doc = Tag("", [Tag("Meshes", [Attrib("width", u32(32)), Attrib("height", u32(32)), Tag("map", [node, empty])])])
    return filedb.write(doc, version=version)


def test_1800s_meshes_and_pages_are_taken_into_117s_layout_value_for_value():
    old = mesh_file(1)
    assert filedb.version(old) == 1
    new = gametransfer.refile("meshes/2/0x0.tmc", old, 3)
    assert filedb.version(new) == 3 and filedb.read(new) == filedb.read(old)
    body = filedb.write(Tag("", [Tag("HeightMap", [Tag("Downsampled", [Attrib("width", u32(66))])])]), version=1)
    page = b"_CTT" + u32(len(body)) + zlib.compress(body, 1)
    carried = gametransfer.refile("normalmaps/0/0x0.ctt", page, 3)
    inner = zlib.decompress(carried[8:])
    assert carried[:4] == b"_CTT" and struct.unpack_from("<I", carried, 4)[0] == len(inner)
    assert filedb.version(inner) == 3 and filedb.read(inner) == filedb.read(body)


def bit_tag(name: str, grid: np.ndarray) -> Tag:
    from annotoolbox.islands.islandgrid import write_bits

    h, w = grid.shape
    tag = Tag(name, [Attrib("x", u32(w)), Attrib("y", u32(h)), Attrib("bits", bytes((w + 31) // 32 * 4 * h))])
    write_bits(tag, grid.astype(int).tolist())
    return tag


def large_1800_island(size: int = 24):
    """An 1800 island larger than its level-8 analogue: a grid that is no power of two (as 320, 384 and 768 are), every
    level's mesh bits set over the active map, meshes stored up to the level below the top and none at the top."""
    n = 2 * size + 1
    zz, xx = np.mgrid[0:n, 0:n] / 2.0
    hm = (np.hypot(xx - size / 2, zz - size / 2) < size / 3) * 3 * 256 - 256  # a 3-unit plateau in a 1-unit-deep sea
    levels, files, sizes = [], {}, []
    while not sizes or sizes[-1] > 1:
        sizes.append(-(-size // 2 ** len(sizes)))
    for level, k in enumerate(sizes):
        levels.append(Tag("#1", [bit_tag("HasMeshArray", np.ones((k, k), bool))]))
        if level < 2:
            continue
        vertex = struct.pack("<4H8B", 0, 32768, 0, 0, 128, 255, 128, 255, 128, 128, 0, 0)
        node = [Attrib("Indices", b""), Attrib("Vertices", b"")]
        if level < len(sizes) - 1:  # stored below the top, as 1800 does
            node = [Attrib("Indices", struct.pack("<3H", 0, 0, 0)), Attrib("Vertices", vertex)]
        entries = [Tag("#1", [*node, Attrib("UsedMaterialBits", u32(2))]) for _ in range(32 * 32)]
        doc = Tag("", [Tag("Meshes", [Tag("map", entries)])])
        files[f"meshes/{level}/0x0.tmc"] = filedb.write(doc, version=1)
    coarse = Tag(
        "CoarseHeightMap",
        [Attrib("width", u32(n)), Attrib("height", u32(n)), Attrib("map", hm.astype("<i2").tobytes())],
    )
    active = bit_tag("ActiveMap", np.ones((size // 8, size // 8), bool))
    rd = Tag("", [Tag("Terrain", [active, coarse, Tag("Levels", levels)])])
    return rd, files, len(sizes) - 1


def test_the_top_mesh_level_an_1800_island_leaves_empty_is_written_for_117():
    rd, files, top = large_1800_island()
    below = files[f"meshes/{top - 1}/0x0.tmc"]
    from annotoolbox.islands.islandterrain import fill_empty_meshes

    assert fill_empty_meshes(rd, 24, files) == {top: 1}
    assert files[f"meshes/{top - 1}/0x0.tmc"] == below  # what 1800 stores stays as it is
    node = next(
        e for e in filedb.read(files[f"meshes/{top}/0x0.tmc"]).path("Meshes/map").children if isinstance(e, Tag)
    )
    v = np.frombuffer(node.find("Vertices").data, "<u2").reshape(-1, 8)
    idx = np.frombuffer(node.find("Indices").data, "<u2")
    assert len(v) > 4 and idx.max() < len(v) and len(idx) % 3 == 0
    assert v[:, 0].max() <= 24 * 32 and v[:, 2].max() <= 24 * 32  # the active map's cells only, not the node's 32 tiles
    y = (v[:, 1].astype(float) - 32768) / 256
    assert y.max() == pytest.approx(2.0, abs=0.05) and y.min() < -1  # the plateau and the sea; skirts hang below
    assert struct.unpack("<I", node.find("UsedMaterialBits").data)[0] == 2  # the ground's mask is kept
    assert filedb.version(files[f"meshes/{top}/0x0.tmc"]) == 1  # written back in its own layout
    assert fill_empty_meshes(rd, 24, files) == {}  # nothing left to fill


POOL = "data/base/provinces/roman/islands/pool"


def info_file(size: int) -> bytes:
    rect = struct.pack("<4i", 0, 0, size, size)
    return filedb.write(Tag("", [Attrib("MapSize", struct.pack("<2I", size, size)), Attrib("ActiveMapRect", rect)]))


def latium(*registered: int, unregistered: tuple[int, ...] = ()) -> islandconvert.GameSide:
    """A 117 with Latium pool islands of the ``registered`` sizes (a ``RandomIsland`` asset each) and islands of the
    ``unregistered`` sizes in the pool folder without one, as the continental island of the fixed maps is."""
    files, blocks = {}, []
    for k, size in enumerate((*registered, *unregistered)):
        path = f"{POOL}/isle_{size}/isle_{size}.a7m"
        files[path], files[path[:-4] + ".a7minfo"] = b"", info_file(size)
        if k < len(registered):
            body = f"<RandomIsland><FilePath>{path}</FilePath><IslandRegion>Roman</IslandRegion></RandomIsland>"
            blocks.append(asset_xml("RandomIsland", 100 + k, f"isle_{size}", body))
    side = islandconvert.GameSide("anno117", FakeFiles(files))
    side._pool = blocks
    return side


def test_an_island_larger_than_the_target_pool_holds_is_refused_not_put_in_a_small_islands_place():
    big = SimpleNamespace(info=filedb.read(info_file(768)))
    roman = regions.region("anno117:roman")
    side = latium(256, 512, unregistered=(768,))  # Latium: 768 only on the fixed maps
    with pytest.raises(ValueError, match="no island of 768 tiles"):
        gametransfer.donor_for(side, roman, big)
    assert gametransfer.donor_for(latium(512, 768), roman, big) == (
        f"{POOL}/isle_768/isle_768.a7m",
        largerdonor.Place(768, 768),
    )
    request = islandconvert.Request("anno1800", "x.a7m", "anno117:roman")
    op = islandconvert.registration(side, request, roman, "p/new.a7m", f"{POOL}/isle_768/isle_768.a7m", 512)
    assert 'GUID="101"' in op  # an unregistered donor: the asset of an island of the same size, not the first one
    with pytest.raises(ValueError, match="no island of 768 tiles"):
        islandconvert.registration(side, request, roman, "p/new.a7m", f"{POOL}/isle_768/isle_768.a7m", 768)


MAP = "data/dlc01/provinces/roman/templates/pool/expanded/expanded.a7tinfo"


def map_template(island: str) -> bytes:
    """A map template with the fixed island ``island`` (a mine slot mapping of its own slots) and a random slot."""
    fixed = Tag(
        "Element",
        [
            Attrib("Position", struct.pack("<2i", 1920, 1920)),
            Attrib("MapFilePath", island.encode("utf-16-le")),
            Attrib("Rotation90", b"\x01"),
            Attrib("IslandLabel", b"continental"),
            Tag("MineSlotMapping", [Attrib("#32768", struct.pack("<Q", 7)), Attrib("#32768", u32(1010503))]),
        ],
    )
    slot = Tag("Element", [Attrib("Position", struct.pack("<2i", 64, 64))])
    root = Tag(
        "MapTemplate",
        [
            Attrib("Size", struct.pack("<2i", 2688, 2688)),
            Attrib("PlayableArea", struct.pack("<4i", 20, 20, 2668, 2668)),
            Attrib("ElementCount", u32(2)),
            Tag("TemplateElement", [fixed]),
            Tag("TemplateElement", [Attrib("ElementType", u32(1)), slot]),
        ],
    )
    return filedb.write(Tag("", [root]))


def test_a_768_tile_island_takes_the_place_a_map_holds_for_its_size():
    from annotoolbox.editor import islandplaces
    from annotoolbox.mapedit import template

    big = SimpleNamespace(info=filedb.read(info_file(768)))
    roman = regions.region("anno117:roman")
    side = latium(256, 512, unregistered=(768,))
    continental = f"{POOL}/isle_768/isle_768.a7m"
    side.files.files[MAP] = map_template(continental)
    assert gametransfer.donor_for(side, roman, big, on_maps=True) == (continental, largerdonor.Place(768, 768))
    assert [(p.template, p.x, p.z, p.rotation90) for p in islandplaces.placed_at(side, continental)] == [
        (MAP, 1920, 1920, 1)
    ]
    new = "data/base/provinces/roman/islands/pool/big_roman/big_roman.a7m"
    ((path, data),) = islandplaces.take_place(side, continental, new, "big_roman").items()
    assert path == MAP
    t = template.read(data)
    fixed, slot = t.elements
    assert (fixed.map_file_path, fixed.island_label, fixed.x, fixed.z, fixed.rotation90) == (
        new,
        "big_roman",
        1920,
        1920,
        1,
    )
    assert fixed.mine_slot_activation == ()  # the old island's slot objects: the game deals the mines itself
    assert (slot.kind, slot.x) == (template.KIND_SLOT, 64)  # the rest of the map as it was
    assert islandplaces.take_place(side, "data/other.a7m", new, "x") == {}
    story = "data/dlc01/provinces/roman/templates/campaign/c/c.a7tinfo"  # its quests name its islands
    side.files.files[story] = map_template(continental)
    assert len(islandplaces.placed_at(side, continental)) == 2 and list(
        islandplaces.take_place(side, continental, new, "b")
    ) == [MAP]


INSTALLED_117, INSTALLED_1800 = installed("anno117"), installed("anno1800")
ARCTIC = (
    "data/dlc03/sessions/islands/pool/colony03_a01_01/colony03_a01_01.a7m"  # 320 tiles: a top level 1800 leaves empty
)


@pytest.mark.skipif(INSTALLED_117 is None or INSTALLED_1800 is None, reason="needs ANNO117_DIR and ANNO1800_DIR")
def test_installed_arctic_island_in_latium_has_latiums_ground_a_mesh_for_every_node_and_no_displacement():
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.islands.islandview import Files

    src = islandconvert.GameSide("anno1800", GameFiles(INSTALLED_1800))
    dst = islandconvert.GameSide("anno117", Files(INSTALLED_117))
    done = islandconvert.convert(src, dst, islandconvert.Request("anno1800", ARCTIC, "anno117:roman"), colours=False)
    isl = a7m_edit117.IslandFile(done.a7m, done.info)
    assert not np.frombuffer(isl.gm.path("TerrainManager/DisplacementHeightMap/HeightMap").data, "<i2").any()
    assert set(isl.rd.path("Terrain/CoarseDisplacementMap/map").data) == {128}
    latium = {m.id for m in regiontables.set_materials(dst.text(regions.region("anno117:roman").material_set))}
    ids = groundedit.dds_u32(done.gamedata["materialmaps/0/matid.dds"])
    alphas = groundedit.dds_u32(done.gamedata["materialmaps/0/matalpha.dds"])
    mats, weights = groundedit.layered_weights(ids, alphas, {})
    assert set(mats) <= latium  # every ID drawn is one of Latium's set
    shares = dict(zip(mats, weights.sum(axis=(1, 2)) / weights.sum(), strict=True))
    assert shares.get(2, 0) > 0.5  # its dark mountain rock (72 %) is Latium's rock, no ID outside the set
    level = 2
    for name in [n for n in done.gamedata if n.startswith(f"meshes/{level}/") and n.endswith(".tmc")]:
        assert filedb.version(done.gamedata[name]) == 3
        tx, tz = (int(v) for v in name.rsplit("/", 1)[1][:-4].split("x"))
        nodes = [e for e in filedb.read(done.gamedata[name]).path("Meshes/map").children if isinstance(e, Tag)]
        for k, e in enumerate(nodes):
            if e.find("Vertices") is None or not e.find("Vertices").data:
                continue
            v = np.frombuffer(e.find("Vertices").data, "<u2").reshape(-1, 8)
            span = 32 * 2**level  # a node's side in 1/32 tiles
            cx, cz = tx * 32 + k % 32, tz * 32 + k // 32
            assert cx * span <= v[:, 0].min() and v[:, 0].max() <= (cx + 1) * span
            assert cz * span <= v[:, 2].min() and v[:, 2].max() <= (cz + 1) * span
            assert np.frombuffer(e.find("Indices").data, "<u2").max() < len(v)
    assert done.report["meshes filled"] == {9: 1}  # 1800 stores no level-9 mesh; 117 draws it from afar
    for level, lv in enumerate(isl.rd.path("Terrain/Levels").findall("#1")[2:], start=2):
        flagged = int(np.asarray(read_bits(lv.path("HasMeshArray")), bool).sum())
        stored = sum(
            1
            for n in done.gamedata
            if n.startswith(f"meshes/{level}/") and n.endswith(".tmc")
            for e in filedb.read(done.gamedata[n]).path("Meshes/map").children
            if isinstance(e, Tag) and e.find("Vertices") is not None and e.find("Vertices").data
        )
        assert stored == flagged, level


@pytest.mark.skipif(INSTALLED_117 is None or INSTALLED_1800 is None, reason="needs ANNO117_DIR and ANNO1800_DIR")
def test_installed_continental_islands_stand_on_maps_not_in_a_pool():
    from annotoolbox.editor import islandplaces
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.islands.islandview import Files

    s117 = islandconvert.GameSide("anno117", Files(INSTALLED_117))
    s1800 = islandconvert.GameSide("anno1800", GameFiles(INSTALLED_1800))
    continental = "data/dlc01/provinces/roman/islands/pool/roman_dlc01_island_continental_01/roman_dlc01_island_continental_01.a7m"
    places = islandplaces.placed_at(s117, continental)
    pool = [p for p in places if not islandplaces.story_map(p.template)]
    assert len(pool) == 15 and {(p.x, p.z, p.rotation90) for p in pool} == {(1920, 1920, 0)}
    assert all(p.template.endswith(("expanded.a7tinfo",)) for p in pool)  # DLC 1's enlarged maps
    assert continental not in gametransfer.registered(s117)
    moderate = "data/dlc01/sessions/islands/pool/moderate_c_01/moderate_c_01.a7m"
    assert [(p.x, p.z, p.rotation90) for p in islandplaces.placed_at(s1800, moderate)] == [(1152, 1152, 1)]
    assert moderate not in gametransfer.registered(s1800)
    new = "data/base/provinces/roman/islands/pool/moderate_c_01_roman/moderate_c_01_roman.a7m"
    assert len(islandplaces.take_place(s117, continental, new, "moderate_c_01_roman")) == 15
