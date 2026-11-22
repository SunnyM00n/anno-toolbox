"""An island carried onto a larger donor, the pictures drawn from a ported island and the far tint picture
(docs/island-regions.md "A larger donor", "The pictures after a port"): synthetic grids and files."""

import io
import struct

import numpy as np
import pytest
from PIL import Image

from annotoolbox.editor import gametransfer, groundedit, largerdonor, portpictures
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import mappictures
from annotoolbox.islands.islandgrid import bits_array, write_bits
from annotoolbox.models import bc1encode


def u32(n: int) -> bytes:
    return struct.pack("<I", n)


def bit_grid(name: str, grid: np.ndarray) -> STag:
    n = grid.shape[0]
    tag = STag(name, [SAttrib("x", u32(n)), SAttrib("y", u32(n)), SAttrib("bits", bytes(n * n // 8 + 8))])
    write_bits(tag, grid.astype(int).tolist())
    return tag


def test_the_source_lies_in_the_middle_on_the_alignment():
    assert largerdonor.place_for(448, 512) == largerdonor.SPlace(448, 512, 32, 32)
    assert largerdonor.place_for(448, 768) == largerdonor.SPlace(448, 768, 160, 160)
    assert largerdonor.place_for(64, 192) == largerdonor.SPlace(64, 192, 64, 64)
    assert largerdonor.place_for(128, 192).x % largerdonor.ALIGN == 0
    place = largerdonor.place_for(64, 192)
    assert place.padded and not largerdonor.SPlace(64, 64).padded
    rows, cols = place.window(0.5)
    assert (rows.start, rows.stop, cols.start) == (32, 64, 32)


def test_a_donor_is_chosen_by_what_its_active_area_holds():
    land = np.zeros((64, 64), bool)
    land[20:40, 20:40] = True
    narrow, wide = np.zeros((16, 16), bool), np.ones((16, 16), bool)  # 16 cells over 128 tiles: 8 tiles each
    narrow[7:9, 7:9] = True
    pool = [(128, 0, "narrow"), (128, 5, "wide"), (64, 0, "same size"), (256, 0, "huge")]
    active = {"narrow": narrow, "wide": wide, "huge": np.ones((32, 32), bool)}
    path, place = largerdonor.choose(pool, 64, land, active.__getitem__)
    assert path == "wide" and place == largerdonor.SPlace(64, 128, 32, 32)  # the smallest grid that holds the land
    with pytest.raises(ValueError, match="draws the island's land"):
        largerdonor.choose([(128, 0, "narrow")], 64, land, active.__getitem__)
    with pytest.raises(ValueError, match="no pool island larger"):
        largerdonor.choose([(64, 0, "same size")], 64, land, active.__getitem__)


def test_the_corner_heights_keep_the_source_and_blend_the_rest_into_the_donors_sea_bed():
    place = largerdonor.SPlace(32, 64, 16, 16)
    source = np.full((33, 33), -3.0)
    source[10:20, 10:20] = 1.0
    donor = np.full((65, 65), -8.0)
    donor[30:34, 30:34] = 5.0  # the donor's own land: gone under the source's sea
    target = largerdonor.corner_targets(source, donor, place)
    assert np.array_equal(target[16:49, 16:49], source)
    assert target[5, 5] == pytest.approx(-3.0 * (1 - 0.375) - 8.0 * 0.375)  # five tiles from the edge of eight: blend
    assert target[0, 0] == -8.0 and target[32, 32] == source[16, 16]  # the grid's edge is the donor's own sea bed
    landlocked = np.full(
        (65, 65), 50.0
    )  # a continental donor has land at its edge: the source's bed, not a rim of land
    assert largerdonor.corner_targets(source, landlocked, place)[0, 0] == -3.0


def test_bit_grids_and_cell_maps_are_embedded_at_the_sources_place():
    place = largerdonor.SPlace(32, 64, 32, 0)
    source = STag("gm", [bit_grid("Water", np.eye(32, dtype=bool))])
    donor = STag("gm", [bit_grid("Water", np.ones((64, 64), bool))])
    assert largerdonor.embed_bits(donor, source, place) == []  # the grids sit under their managers' names
    nested = STag("gm", [STag("WorldManager", [bit_grid("Water", np.eye(32, dtype=bool))])])
    big = STag("gm", [STag("WorldManager", [bit_grid("Water", np.ones((64, 64), bool))])])
    assert largerdonor.embed_bits(big, nested, place) == ["WorldManager/Water"]
    grid = bits_array(big.path("WorldManager/Water"))
    assert grid[:32].sum() == 32 and grid[:32, 32:].diagonal().all() and not grid[32:].any()

    def cells(name: str, n: int, data: bytes) -> STag:
        return STag(name, [SAttrib("width", u32(n)), SAttrib("height", u32(n)), SAttrib("map", data)])

    src = cells("WaterFlow", 16, bytes([7]) * (16 * 16 * 4))  # 16 cells over 32 tiles: half a cell a tile
    dst = cells("WaterFlow", 32, bytes([1]) * (32 * 32 * 4))
    assert largerdonor.embed_cells(dst, src, place, "map", keep=True)
    out = np.frombuffer(dst.find("map").data, np.uint8).reshape(32, 32, 4)
    assert (out[:16, 16:] == 7).all() and (out[16:] == 1).all() and (out[:16, :16] == 1).all()
    dst2 = cells("WaterFlow", 32, bytes([1]) * (32 * 32 * 4))
    largerdonor.embed_cells(dst2, src, place, "map")
    assert not np.frombuffer(dst2.find("map").data, np.uint8).reshape(32, 32, 4)[16:].any()  # not kept: zero


def test_what_stands_on_the_island_moves_with_it():
    place = largerdonor.SPlace(32, 64, 32, 16)
    point = STag("#1", [SAttrib("p", struct.pack("<3f", 5, 1, 6)), SAttrib("i", struct.pack("<3f", 0, 0, 1))])
    path = STag(
        "Path",
        [
            SAttrib("Minimum", struct.pack("<3f", 5, 1, 6)),
            SAttrib("Maximum", struct.pack("<3f", 5, 1, 6)),
            STag("BezierCurve", [point]),
        ],
    )
    obj = STag("#1", [SAttrib("Position", struct.pack("<3f", 5, 1, 6)), STag("BezierPath", [path])])
    largerdonor.shift_object(obj, place)
    assert struct.unpack("<3f", obj.find("Position").data) == (37, 1, 22)
    assert struct.unpack("<3f", point.find("p").data) == (37, 1, 22)
    assert struct.unpack("<3f", path.find("Minimum").data) == (37, 1, 22)
    assert struct.unpack("<3f", point.find("i").data) == (0, 0, 1)  # a tangent is a direction, not a place
    rd = STag(
        "", [STag("PropGrid", [STag("Instances", [STag("#1", [SAttrib("Position", struct.pack("<3f", 1, 2, 3))])])])]
    )
    assert largerdonor.shift_props(rd, place) == 1
    assert struct.unpack("<3f", rd.path("PropGrid/Instances").children[0].find("Position").data) == (33, 2, 19)
    info, gm = STag("", [SAttrib("ActiveMapRect", struct.pack("<4i", 1, 2, 30, 31))]), STag("", [])
    largerdonor.shift_rect(info, gm, info, gm, place)
    assert struct.unpack("<4i", info.find("ActiveMapRect").data) == (33, 18, 62, 47)


def test_the_donors_meshes_take_the_used_material_mask():
    mesh = STag("", [STag("Meshes", [STag("map", [STag("#1", [SAttrib("UsedMaterialBits", u32(3))])])])])
    files = {"meshes/0/0x0.tmc": filedb.write(mesh), "other": b"x"}
    assert largerdonor.mark_meshes(files, 0b1100) == 1
    node = filedb.read(files["meshes/0/0x0.tmc"]).path("Meshes/map").children[0]
    assert struct.unpack("<I", node.find("UsedMaterialBits").data)[0] == 0b1100
    assert largerdonor.mark_meshes(files, 0b1100) == 0


def ids_word(*mids: int) -> int:
    return sum(m << (5 * s) for s, m in enumerate(mids))


def test_the_ground_of_a_larger_donor_is_the_sources_inside_and_its_sea_bed_round_it():
    ids = np.full((4, 4), ids_word(7), np.uint32)  # 4 tiles, 2 texels a tile, ID blocks of 2
    ids[1:3, 1:3] = ids_word(9)
    alphas = np.full((8, 8), 31, np.uint32)
    ground = gametransfer.SGround(ids, alphas, 4, {7: 21, 9: 22}, {7: 0, 9: 1}, {21: 0, 22: 1})
    own_ids, own_alphas, own_used = gametransfer.encode_map(ground, 2, 4)
    assert gametransfer.sea_bed_material(ids) == 7
    wide = gametransfer.SGround(
        ids, alphas, 4, {7: 21, 9: 22}, {7: 0, 9: 1}, {21: 0, 22: 1}, largerdonor.SPlace(4, 12, 4, 4), 7
    )
    new_ids, new_alphas, used = gametransfer.encode_map(wide, 6, 12)
    assert new_ids.shape == (6, 6) and new_alphas.shape == (12, 12)
    assert np.array_equal(new_ids[2:4, 2:4], own_ids) and np.array_equal(new_alphas[4:8, 4:8], own_alphas)
    assert int(new_ids[0, 0]) & 31 == 21 and int(new_alphas[0, 0]) & 31 == 31  # outside: the sea bed's one material
    assert used == (1 << 21) | (1 << 22) and own_used == used


def test_the_ground_colours_weight_each_material_by_what_the_game_shows():
    ids = np.full((2, 2), ids_word(7, 9), np.uint32)  # 2 tiles, 2 texels a tile, blocks of 2
    alphas = np.full((4, 4), 31, np.uint32)  # slot 0 only, full
    colours = {7: (1.0, 0.0, 0.0), 9: (0.0, 1.0, 0.0)}
    image = portpictures.colour_image(ids, alphas, {7: 0, 9: 1}, colours, 2)
    assert image.shape == (4, 4, 3) and np.allclose(image, [1.0, 0.0, 0.0])
    half = np.full((4, 4), 31 | (31 << 5), np.uint32)  # both slots full: the lower layer shows through nowhere
    mix = portpictures.colour_image(ids, half, {7: 0, 9: 1}, colours, 2)
    assert np.allclose(mix.sum(axis=2), 1.0)


def test_the_pictures_are_drawn_from_the_ground_the_heights_and_the_sea():
    size = 32
    heights = np.full((2 * size + 1, 2 * size + 1), -3.0)
    heights[16:49, 16:49] = 1.0  # land in the middle
    colours = np.zeros((2 * size, 2 * size, 3), np.float32)
    colours[..., 1] = 0.8  # green ground
    window = (4, 4, 28, 28)
    shapes = {name: mappictures.picture_shape(name, window) for name in ("mapimage.png", "gamemapimage.png")}
    assert shapes == {"mapimage.png": (512, 512), "gamemapimage.png": (48, 48)}
    assert mappictures.picture_shape("activemapimage.png", window) == (96, 96)
    out = mappictures.paint(heights, colours, window, size, {**shapes, "provincemapimage.png": (48, 48)})
    game = np.asarray(Image.open(io.BytesIO(out["gamemapimage.png"]))).astype(int)
    assert game[24, 24, 3] == 255 and game[24, 24, 1] > 100 and game[0, 0, 3] == 0  # green land, clear sea
    whole = np.asarray(Image.open(io.BytesIO(out["mapimage.png"]))).astype(int)
    assert tuple(whole[0, 0]) == mappictures.SEA_COLOR  # the browser picture has the sea in it
    province = np.asarray(Image.open(io.BytesIO(out["provincemapimage.png"]))).astype(int)
    assert province[24, 24, 3] == 255 and province[0, 0, 3] == 0 and province[24, 24, 1] > 0


def tint_node(blocks_across: int, rgb: tuple[int, int, int]) -> STag:
    n = blocks_across * 4
    texels = np.empty((n, n, 3), np.uint8)
    texels[:] = rgb
    data = bc1encode.encode_blocks(bc1encode.to_blocks(texels)).tobytes()
    return STag(
        "BaseTextureData",
        [SAttrib("width", u32(blocks_across)), SAttrib("height", u32(blocks_across)), SAttrib("map", data)],
    )


def terrain_with(node: STag) -> STag:
    return STag("", [STag("Terrain", [STag("TintMapLevels", [node])])])


def test_the_far_tint_follows_the_source_at_the_donors_resolution():
    src = terrain_with(tint_node(2, (200, 100, 50)))  # 2 blocks: 8 texels over 8 tiles (a texel a tile, as 117)
    dst = terrain_with(tint_node(8, (128, 128, 128)))  # 8 blocks: 32 texels over 8 tiles (four a tile, as 1800)
    assert portpictures.far_tint(src, dst, largerdonor.SPlace(8, 8))
    img = portpictures.tint_texels(dst.path("Terrain/TintMapLevels/BaseTextureData"))
    assert img.shape == (32, 32, 3) and np.abs(img.astype(int) - (200, 100, 50)).max() < 12
    wide = terrain_with(tint_node(16, (0, 0, 0)))  # a donor of 16 tiles: the source in its middle, its tone round it
    assert portpictures.far_tint(src, wide, largerdonor.SPlace(8, 16, 4, 4))
    out = portpictures.tint_texels(wide.path("Terrain/TintMapLevels/BaseTextureData"))
    assert out.shape == (64, 64, 3) and np.abs(out[0, 0].astype(int) - (200, 100, 50)).max() < 12
    assert not portpictures.far_tint(STag("", []), dst, largerdonor.SPlace(8, 8))


def tint_page(mark: int) -> bytes:
    """A 512 x 512 tint page whose every BC1 block holds its row, its column and ``mark`` (no colours: the graft moves
    blocks, it reads none)."""
    n = largerdonor.TINT_SIDE // 4
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<II", header, 12, largerdonor.TINT_SIDE, largerdonor.TINT_SIDE)
    rows, cols = np.mgrid[0:n, 0:n]
    blocks = np.zeros((n, n, 8), np.uint8)
    blocks[..., 0], blocks[..., 1], blocks[..., 2] = rows, cols, mark
    return bytes(header) + blocks.tobytes()


def blocks_of(raw: bytes) -> np.ndarray:
    n = largerdonor.TINT_SIDE // 4
    return np.frombuffer(raw, np.uint8, n * n * 8, 128).reshape(n, n, 8)


def test_the_sources_tint_pages_are_laid_into_the_donors_block_for_block():
    place = largerdonor.SPlace(64, 128, 32, 32)  # the source's 64 tiles at the donor's tiles 32 to 96
    src = {
        f"tintmaps/{lv}/{x}x{z}.dds": tint_page(1)
        for lv, k in ((0, 4), (1, 2), (2, 1))
        for x in range(k)
        for z in range(k)
    }
    dst = {
        f"tintmaps/{lv}/{x}x{z}.dds": tint_page(9)
        for lv, k in ((0, 8), (1, 4), (2, 2), (3, 1))
        for x in range(k)
        for z in range(k)
    }
    gd = lambda files: groundedit.SGamedata(sorted(files), files.__getitem__)  # noqa: E731
    out = largerdonor.embed_tints(gd(src), gd(dst), place)
    assert "tintmaps/0/0x0.dds" not in out and "tintmaps/3/0x0.dds" not in out  # outside; the source has no level 3
    whole = blocks_of(out["tintmaps/0/2x2.dds"])  # the source's page 0 x 0 of level 0, as it is
    assert (whole == blocks_of(src["tintmaps/0/0x0.dds"])).all()
    quarter = blocks_of(out["tintmaps/2/0x0.dds"])  # level 2: 64 tiles a page, the source's first half in its corner
    assert (quarter[64:, 64:, 2] == 1).all() and (quarter[:64, :, 2] == 9).all() and (quarter[:, :64, 2] == 9).all()
    assert (quarter[64:, 64:, :2] == blocks_of(src["tintmaps/2/0x0.dds"])[:64, :64, :2]).all()
