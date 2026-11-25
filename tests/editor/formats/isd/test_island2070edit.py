"""Anno 2070 editing: an island edited for Anno 2070 itself, written back into its .isd and patch9.rda."""

import json
import struct

import numpy as np
import pytest
from tests.formats.isdfakes import ISLAND2070_SIZE as SIZE
from tests.formats.isdfakes import ISLAND2070_SOURCE as SOURCE
from tests.formats.isdfakes import install
from tests.formats.isdfakes import isd2070_bytes as isd_bytes

from annotoolbox.editor.formats.isd import build as isd_build
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import isd2070
from annotoolbox.formats.isd2070 import SNode
from annotoolbox.formats.rda import CRdaArchive, write_v20
from annotoolbox.islands import islandedits, objects2070


def test_serialize_gives_the_parsed_bytes_back():
    data = isd_bytes()
    root = isd2070.parse(data)
    assert isd2070.serialize(root) == data
    assert root.find("Terrain").short and not root.find("m_GOPManager").short  # </> and </Name> both kept
    assert data.count(b"</>\r\n") == 1 and b"CDATA[" in data


def test_objects_move_turn_scale_and_go():
    isl = isd_edit.CIsland2070(isd_bytes())
    edits = {
        "objects": {
            "2070:12": {"position": [3.5, 2.0, 4.25], "rotation": [0, -0.70710678, 0, 0.70710678], "scale": 1.5},
            "2070:11": {"position": [9, 1, 9], "scale": 0.25},
            "2070:13": {"deleted": True},
            "2070:99": {"deleted": True},
        }
    }
    report = isl.apply(edits, objects2070.read_types(install()))
    assert report["objects changed"] == 2 and report["objects deleted"] == 1 and report["unmatched"] == ["2070:99"]
    out = isd2070.CIsland(isl.write())
    tree = next(o for o in out.objects() if o.guid == 40)
    assert (tree.x, tree.y, tree.z) == (3.5, 2.0, 4.25) and tree.scale == 1.5
    assert tree.direction == 90 * 4096 and tree.orientation[1] == pytest.approx(-0.70710678)
    assert len(out.objects()) == 3
    assert out.m_root.path("m_GOPManager/ObjectGroups").children == []  # the group named the deleted tree
    prop = next(o for o in out.m_root.path("m_GOPManager/Objects/Simple/Objects").children)
    pos_quat_scale = isd_edit.RENDER_TRANSFORM.unpack_from(
        prop.path("PropertyMesh/m_RenderParameters").data, len(isd_edit.RENDER_HEAD)
    )
    assert pos_quat_scale[:3] == (9.0, 1.0, 9.0) and pos_quat_scale[-1] == 0.25  # drawn where it now stands


def test_library_objects_are_added_in_their_group():
    isl = isd_edit.CIsland2070(isd_bytes())
    types = objects2070.read_types(install())
    edits = {
        "added_objects": [
            {"asset": 40, "variation": 1, "position": [16, 1, 16], "rotation": [0, 0, 0, 1], "scale": 1.2},
            {"source": "data/graphics/props/crate.cfg", "position": [6, 1, 6]},  # shared with a Resource: the prop
            {"asset": 350, "position": [1, 1, 1]},  # a resource: written as a copy (placeholder handle object)
            {"asset": 999999, "position": [2, 1, 2]},  # no such asset
        ]
    }
    report = isl.apply(edits, types)
    assert report["objects added"] == 3 and len(report["unmatched"]) == 1
    out = isd2070.CIsland(isl.write())
    added = [o for o in out.objects() if (o.x, o.z) in ((16, 16), (6, 6))]
    assert sorted((o.kind, o.guid) for o in added) == [("Nature", 40), ("Simple", 333)]
    palm = next(o for o in added if o.guid == 40)
    assert palm.variation == 1 and palm.scale == 1.2
    ids = [n.int("m_ID") for n in out.m_root.iter() if n.name == "Object"]
    assert len(ids) == len(set(ids)) and max(ids) == 16  # the next free m_ID each


def test_heights_change_the_chunks_and_the_logic_grid():
    isl = isd_edit.CIsland2070(isd_bytes())
    assert isl.apply_heights({"16,16": 2.0, "16,17": 2.0}) == 2
    out = isd2070.CIsland(isl.write())
    hm = np.array(out.heightmap())
    assert hm[16, 16] == 3.0 and hm[16, 17] == 3.0 and hm[15, 16] == 1.0  # the corner all four chunks share
    v2 = np.frombuffer(out.m_root.path("m_GOPManager/m_GRIDManager/m_HeightMap_v2").data, "<i2").reshape(SIZE, SIZE)
    assert v2[16, 16] == 1024 + 1024 and v2[15, 16] == 1024 + 1024 and v2[15, 15] == 1024 + 512 and v2[0, 0] == 1024


def test_paint_raises_its_layer_and_lowers_those_above():
    isl = isd_edit.CIsland2070(isd_bytes())
    stroke = {"material": 78, "radius": 3, "strength": 1, "points": [[24, 24]]}  # beach where only chunk 0 has it
    assert isl.apply_paint([{**stroke, "material": "beach"}]) == 0  # a hand-edited file: left out, not a failed build
    assert isl.apply_paint([stroke]) == 1
    out = isd2070.CIsland(isl.write())
    chunks = list(out.texture_chunks())
    last = next(layers for cx, cz, _, layers in chunks if (cx, cz) == (1, 1))
    assert [i for i, _ in last] == [0, 52, 78]  # the layer added on top
    beach = dict(last)[78]
    assert beach[8][8] == 255 and beach[0][0] == 0  # the stroke's middle (tile 24 = sample 8) and outside it
    assert dict(last)[52][8][8] == 200  # below the painted layer: kept
    first = dict(next(layers for cx, cz, _, layers in chunks if (cx, cz) == (0, 0)))
    assert first[78][0][0] == 100  # untouched


def test_patch_carries_the_edited_islands_and_the_games_own_files(tmp_path):
    tree = install()
    edits_dir = tmp_path / "edits"
    islandedits.save(edits_dir / isd_edit.edit_name(SOURCE), {"source": SOURCE, "heights": {"1,1": 1.0}})
    original = tmp_path / "patch9_game.rda"
    original.write_bytes(write_v20({"data/loca/eng/txt/credits.txt": b"credits"}))
    tree.files[SOURCE.removesuffix(".isd") + isd_build.FAR_SUFFIX] = _far_dds(64)
    files, written = isd_build.edited_files(tree, edits_dir, objects2070.read_types(tree), lambda _: None)
    patch = isd_build.patch_bytes(files, written, original)
    assert written == [SOURCE]
    (tmp_path / "patch.rda").write_bytes(patch)
    arc = CRdaArchive(tmp_path / "patch.rda")
    assert arc.read("data/loca/eng/txt/credits.txt") == b"credits"
    assert np.array(isd2070.CIsland(arc.read(SOURCE)).heightmap())[1, 1] == 2.0
    assert arc.read("data/levels/islands/normal/isle_2070_0.dds")[:4] == b"DDS "  # redrawn: the heights changed
    assert isd_build.is_ours(tmp_path / "patch.rda") and not isd_build.is_ours(original)


def test_install_keeps_the_games_patch_and_restores_it(tmp_path):
    game, backup = tmp_path / "Anno 2070", tmp_path / "workspace" / "patch9-original.rda"
    (game / "maindata").mkdir(parents=True)
    vanilla = write_v20({"data/loca/eng/txt/credits.txt": b"credits"})
    (game / isd_build.PATCH).write_bytes(vanilla)
    ours = write_v20({isd_build.MARKER: b"x"})
    isd_build.install_patch(game, ours, backup, log=lambda _: None)
    isd_build.install_patch(game, ours, backup, log=lambda _: None)  # again: the backup stays the game's
    assert backup.read_bytes() == vanilla and isd_build.is_ours(game / isd_build.PATCH)
    assert isd_build.uninstall_patch(game, backup, log=lambda _: None)
    assert (game / isd_build.PATCH).read_bytes() == vanilla
    assert not isd_build.uninstall_patch(game, backup, log=lambda _: None)  # nothing of ours left


def test_a_repaired_patch_is_kept_afresh_and_ours_without_a_copy_is_refused(tmp_path):
    """After a launcher repair the old copy stayed, and a later restore put back a stale patch9.rda; with ours in
    place and no copy, a build packed a patch without the game's files."""
    game, backup = tmp_path / "Anno 2070", tmp_path / "backups" / "patch9.rda"
    (game / "maindata").mkdir(parents=True)
    (game / isd_build.PATCH).write_bytes(write_v20({"data/loca/eng/txt/credits.txt": b"credits"}))
    ours = write_v20({isd_build.MARKER: b"x"})
    isd_build.install_patch(game, ours, backup, log=lambda _: None)
    repaired = write_v20({"data/loca/eng/txt/credits.txt": b"credits 2"})
    (game / isd_build.PATCH).write_bytes(repaired)  # the launcher's file check
    assert isd_build.own_patch(game, backup) == game / isd_build.PATCH  # a build keeps the repaired one's files
    isd_build.install_patch(game, ours, backup, log=lambda _: None)
    assert backup.read_bytes() == repaired
    backup.unlink()
    with pytest.raises(RuntimeError, match="verify the files in the launcher"):
        isd_build.own_patch(game, backup)
    with pytest.raises(RuntimeError, match="verify the files in the launcher"):
        isd_build.install_patch(game, write_v20({isd_build.MARKER: b"y"}), backup, log=lambda _: None)
    assert (game / isd_build.PATCH).read_bytes() == ours


def test_the_view_shows_the_isd_as_the_editor_draws_islands(tmp_path):
    ed = isd_edit.CIsland2070Editor(install(), SOURCE, tmp_path / "cache")
    isl = ed.island()
    assert isl["mode"] == "2070" and isl["width"] == SIZE + 1 and isl["samples_per_tile"] == 1
    objs = {o["key"]: o for o in isl["objects"]}
    assert objs["2070:11"]["cfg"] == "" and objs["2070:11"]["marker"] == "SimpleObject"  # no model in the install
    assert objs["2070:13"]["cfg"] == "data/graphics/landscape/trees/palm_02.cfg"  # its variation, as the game has it
    assert objs["2070:11"]["scale"] == 0.5  # a prop's scale from its render parameters
    ids, weights = ed.ground_layers(texels=2)
    assert ids == [0, 52, 78] and weights.shape == (3, SIZE * 2, SIZE * 2)
    assert np.allclose(weights.sum(axis=0), 1.0)  # the sea bed takes what the layers above leave
    assert weights[1, 40, 40] == pytest.approx(200 / 255) and weights[2, 40, 40] == 0
    assert [m["id"] for m in ed.materials()] == [78, 52, 0]  # the island's own, by name
    library = ed.library()
    assert [(e["asset"], e["variation"]) for e in library["2070"]] == [(40, 0), (40, 1)]  # the crate is not installed
    assert library["117"] == [] and library["editor"] == []


def _far_dds(side: int) -> bytes:
    """An A8R8G8B8 texture with mips, as 2070 stores an island's far view: R 10, G 20, B 30, A 40."""
    levels = side.bit_length()
    header = b"DDS " + struct.pack("<7I", 124, 0xA1007, side, side, side * 4, 0, levels) + bytes(44)
    header += (
        struct.pack("<8I", 32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
        + struct.pack("<4I", 0x401008, 0, 0, 0)
        + bytes(4)
    )
    return header + b"".join(bytes([30, 20, 10, 40]) * (side >> k) ** 2 for k in range(levels))


def test_far_view_redraws_land_and_coast_and_keeps_the_ground():
    from io import BytesIO

    from PIL import Image

    isl = isd_edit.CIsland2070(isd_bytes())
    isl.apply_heights({f"{z},{x}": -11.0 for z in range(0, 9) for x in range(0, 9)})  # a bay at z, x = 0
    vanilla = _far_dds(64)
    out = isd_build.far_view(isl.m_island, vanilla)
    assert len(out) == len(vanilla) and out[:128] == vanilla[:128]
    img = np.asarray(Image.open(BytesIO(out)).convert("RGBA")).astype(int)
    assert (img[..., 0] == 10).all() and (img[..., 2] == 30).all()  # the ground picture stays
    assert img[-1, 0, 3] == 0 and img[0, -1, 3] == 254  # the bay is in the last rows: stored from the far edge
    band = img[-30:, 0, 1]  # up the first column: sea, coast at about 9 tiles (18 px), land
    assert band[-1] == 0 and band.max() > 140 and band[0] == 0 and 12 <= 30 - band.argmax() <= 22


def test_heights_out_at_sea_switch_the_chunk_on():
    root = isd2070.parse(isd_bytes())
    el = root.path("Terrain/ChunkMap").findall("Element")[3]
    el.children = [SNode("VertexResolution", "-1"), SNode("Flags", "65536")]  # chunk (1, 1) is open sea
    used = SNode(
        "UsedChunks",
        children=[
            SNode("m_XSize", "2"),
            SNode("m_YSize", "2"),
            SNode("m_IntXSize", "1"),
            SNode("m_BitGrid", data=bytes([1, 0, 0, 0, 1, 0, 0, 0])),
        ],
    )
    root.children.insert(3, used)
    isl = isd_edit.CIsland2070(isd2070.serialize(root))
    assert isl.apply_heights({"24,24": 5.0}) == 1
    out = isd2070.CIsland(isl.write())
    chunk = out.m_root.path("Terrain/ChunkMap").findall("Element")[3]
    assert [c.name for c in chunk.children] == ["VertexResolution", "HeightMap", "Flags", "TexIndexData"]
    assert chunk.int("VertexResolution") == 4 and chunk.int("Flags") == 131072
    hm = np.array(out.heightmap())
    assert (
        hm[24, 24] == 6.0 and hm[16, 16] == 1.0
    )  # its corner raised from where the editor showed it; a shared border kept
    assert out.m_root.path("UsedChunks/m_BitGrid").data == bytes([1, 0, 0, 0, 3, 0, 0, 0])


def test_planted_forest_tiles_become_trees_of_the_islands_own_kinds():
    isl = isd_edit.CIsland2070(isd_bytes())
    types = objects2070.read_types(install())
    assert isd_edit.forest_kinds(isl.m_island, types) == [(40, 0), (40, 1)]  # one of each: by GUID, variation
    forest = {"3,4": 1, "3,5": 1, "10,12": 1, "6,6": 0}  # (10, 12) has its tree already; 0 is a cleared tile
    report = isl.apply({"forest": forest}, types)
    assert report["trees planted"] == 2
    planted = [o for o in isd2070.CIsland(isl.write()).objects() if (int(o.x), int(o.z)) in ((3, 4), (3, 5))]
    assert sorted((o.x, o.z) for o in planted) == [(3.5, 4.5), (3.5, 5.5)] and all(o.y == 1.0 for o in planted)
    for o in planted:  # the kind the page drew for the tile
        pick = isd_edit.tile_pick(int(o.x), int(o.z))
        assert (o.guid, o.variation or 0) == [(40, 0), (40, 1)][pick % 2] and o.direction == (pick % 360) * 4096


def test_fine_chunks_are_read_at_their_corners_and_edited_between_them():
    """A chunk with a sample every half tile (33 x 33 for 16 tiles): the corners are its even
    samples, and a height edit reaches the samples between corners interpolated."""
    root = isd2070.parse(isd_bytes())
    el = root.path("Terrain/ChunkMap").findall("Element")[0]
    fine = np.add.outer(np.arange(33) * 100.0, np.arange(33)).astype("<f4")  # row * 100 + column
    el.find("HeightMap").children = [SNode("Width", "33"), SNode("Data", data=fine.tobytes())]
    isl = isd2070.CIsland(isd2070.serialize(root))
    hm = np.array(isl.heightmap())
    assert hm[1, 1] == 202.0 and hm[0, 3] == 6.0 and hm[15, 15] == 3030.0  # samples (2, 2), (0, 6), (30, 30)
    edit = isd_edit.CIsland2070(isd2070.serialize(root))
    edit.apply_heights({"1,1": 2.0})
    data = np.frombuffer(edit.m_root.path("Terrain/ChunkMap").findall("Element")[0].path("HeightMap/Data").data, "<f4")
    after = data.reshape(33, 33) - fine
    assert after[2, 2] == 2.0 and after[3, 2] == 1.0 and after[1, 1] == 0.5 and after[0, 0] == 0.0


def test_a_props_tilt_and_scale_come_from_its_render_parameters():
    """Outside the Nature group 2070 keeps an object's tilt and scale only in what it draws."""
    root = isd2070.parse(isd_bytes())
    prop = root.path("m_GOPManager/Objects/Simple/Objects").children[0]
    tilt = (0.1, -0.3, 0.05, 0.948)
    rp = prop.path("PropertyMesh/m_RenderParameters")
    data = bytearray(rp.data)
    isd2070.RENDER_TRANSFORM.pack_into(data, isd2070.RENDER_AT, 8, 0.7, 8, *tilt, 0.4)
    rp.data = bytes(data)
    obj = next(o for o in isd2070.CIsland(isd2070.serialize(root)).objects() if o.guid == 333)
    assert obj.orientation == pytest.approx(tilt) and obj.scale == pytest.approx(0.4)
    edit = isd_edit.CIsland2070(isd2070.serialize(root))
    edit.apply(
        {"objects": {"2070:11": {"rotation": [0, -0.70710678, 0, 0.70710678]}}}, objects2070.read_types(install())
    )
    moved = next(o for o in isd2070.CIsland(edit.write()).objects() if o.guid == 333)
    assert moved.orientation == pytest.approx((0, -0.70710678, 0, 0.70710678)) and moved.scale == pytest.approx(0.4)


def test_older_1404_objects_keep_their_position_in_a_child_node():
    root = isd2070.parse(isd_bytes())
    tree = next(o for _, objs in isd_edit._groups(root) for o in objs.children if o.int("m_ID") == 12)
    pos = tree.find("m_Position")
    pos.children, pos.data = [SNode("data", data=pos.data)], None
    data = isd2070.serialize(root)
    assert [o.x for o in isd2070.CIsland(data).objects() if o.guid == 40][0] == 10.0
    isl = isd_edit.CIsland2070(data)
    isl.apply({"objects": {"2070:12": {"position": [3.0, 1.0, 4.0]}}}, {})
    moved = next(o for o in isd2070.CIsland(isl.write()).objects() if o.id == 12)
    assert (moved.x, moved.y, moved.z) == (3.0, 1.0, 4.0)


def test_a_2070_model_is_drawn_from_the_games_own_files(tmp_path):
    ed = isd_edit.CIsland2070Editor(install(), SOURCE, tmp_path / "cache")
    palm = "data/graphics/landscape/trees/palm_02.cfg"
    assert ed.model_cfg(palm) == palm and ed.model_cfg("data/graphics/landscape/trees/palm_01.cfg") == ""
    (part,) = ed.model(palm)["parts"]  # the terrain cut-out draws nothing
    assert part["mesh"] == "data/graphics/landscape/trees/rdm/palm_lod0.rdm|0" and part["shader"] == 8
    assert part["diffuse"] == "data/graphics/landscape/trees/maps/palm_diff_0.dds"
    glb = ed.mesh_glb(part["mesh"])
    assert glb[:4] == b"glTF"
    doc = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert [a["count"] for a in doc["accessors"] if a.get("type") == "SCALAR"] == [3]  # the bark's triangle only
    assert ed.convert2070(palm) == palm  # the library places it as it is
    assert any("palm_01" in n for n in ed.model_notes())


STREET = "data/graphics/effects/street/"
STREET_DECAL = (
    "<m_Config><m_TerrainDecals><m_Config><m_Extend.x>0.200000</m_Extend.x><m_Extend.y>0.300000</m_Extend.y>"
    "<m_TexCoords.x>0.0</m_TexCoords.x><m_TexCoords.y>0.0</m_TexCoords.y><m_TexCoords.z>1.0</m_TexCoords.z>"
    "<m_TexCoords.w>1.0</m_TexCoords.w><m_ConfigType>TERRAINDECAL</m_ConfigType><m_Materials><m_Config>"
    "<m_ShaderIndex>0</m_ShaderIndex><m_DiffuseTexture>{texture}</m_DiffuseTexture><m_DiffuseEnabled>1"
    "</m_DiffuseEnabled><m_ConfigType>MATERIAL</m_ConfigType></m_Config></m_Materials><m_Transformer><m_Config>"
    "<m_Position.x>0.5</m_Position.x><m_Position.y>0.0</m_Position.y><m_Position.z>0.5</m_Position.z>"
    "<m_Rotation.w>1.0</m_Rotation.w><m_Scale>1.4</m_Scale><m_Conditions>0</m_Conditions>"
    "<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config></m_Transformer></m_Config></m_TerrainDecals>"
    "<m_ConfigType>ROOT</m_ConfigType></m_Config>"
)


def test_a_ground_decal_config_draws_as_a_decal_and_a_pin_stays_where_its_map_is_missing(tmp_path):
    cracks, lost = STREET + "cracks.cfg", STREET + "lost.cfg"
    files = {
        cracks: STREET_DECAL.format(texture=(STREET + "cracks.png").replace("/", "\\")).encode(),
        lost: STREET_DECAL.format(texture=(STREET + "lost.png").replace("/", "\\")).encode(),
        STREET + "cracks_0.dds": b"DDS ",
    }
    ed = isd_edit.CIsland2070Editor(install(files), SOURCE, tmp_path / "cache")
    assert ed.model_cfg(cracks) == cracks
    spec = ed.model(cracks)
    assert spec["parts"] == []
    (decal,) = spec["decals"]
    assert decal["extents"] == (0.2, 0.3) and decal["on_streets"] is True and decal["kind"] == "Terrain"
    assert decal["diffuse"] == STREET + "cracks_0.dds" and decal["scale"] == [1.4, 1.4, 1.4]
    assert decal["position"] == [0.5, 0.0, 0.5]
    assert ed.model_cfg(lost) == ""  # its map is not in the game's files: the object stays a pin
    assert ed._model_state()[0][lost].startswith("its model config cannot be read")
