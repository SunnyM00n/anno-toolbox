"""Anno 2070 editing: an island edited for Anno 2070 itself, written back into its .isd and patch9.rda."""

import json
import struct

import numpy as np
import pytest

from annotoolbox.editor.formats.isd import build as isd_build
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import isd2070
from annotoolbox.formats.isd2070 import FIXED, Node
from annotoolbox.formats.rda import RdaArchive, write_v20
from annotoolbox.formats.rdmwrite import FORMAT_P4H_N4B_G4B_B4B_T2H, RdmMeshData, RdmSubmesh, write_rdm
from annotoolbox.islands import islandedits, objects2070
from fakes import FakeTree

SOURCE = "data/levels/islands/normal/isle.isd"
SIZE, CHUNKS = 32, 2
ASSETS = """<Assets>
<Asset><Template>Tree</Template><Values><Standard><GUID>40</GUID><Name>palm</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/landscape/trees/palm_01.cfg</Filename></Item>
<Item><Filename>data/graphics/landscape/trees/palm_02.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>SimpleObject</Template><Values><Standard><GUID>333</GUID><Name>crate</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/props/crate.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>Resource</Template><Values><Standard><GUID>350</GUID><Name>slot</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/props/crate.cfg</Filename></Item></Variations></Object></Values></Asset>
</Assets>"""
GROUND = """<Textures>
<Texture><GUID>0</GUID><Name>water</Name><DiffuseTexture>data/t/water_diff.png</DiffuseTexture><R>20</R><G>60</G><B>90</B></Texture>
<Texture><GUID>52</GUID><Name>gras_01</Name><DiffuseTexture>data/t/gras_diff.png</DiffuseTexture>
<NormalTexture>data/t/gras_norm.png</NormalTexture><R>60</R><G>120</G><B>40</B></Texture>
<Texture><GUID>78</GUID><Name>beach_01</Name><DiffuseTexture>data/t/beach_diff.png</DiffuseTexture><R>200</R><G>180</G><B>120</B></Texture>
</Textures>"""


def _obj(oid: int, guid: int, x: float, z: float, nature: bool = True, var: int | None = None) -> Node:
    kids = [Node("m_ID", str(oid)), Node("m_GUID", str(guid))]
    if var:
        kids.append(Node("m_Variation", str(var)))
    kids += [
        Node("m_Position", data=struct.pack("<qqq", round(x * FIXED), round(z * FIXED), round(0.7 * FIXED))),
        Node("m_PlayerID", "15"),
        Node("m_Direction", "0"),
    ]
    if nature:
        kids.append(
            Node(
                "Nature",
                children=[Node("m_Scale", "1.000000"), Node("m_Orientation", data=struct.pack("<4f", 0, 0, 0, 1))],
            )
        )
    else:
        render = isd_edit.RENDER_HEAD + isd_edit.RENDER_TRANSFORM.pack(x, 0.7, z, 0, 0, 0, 1, 0.5)
        kids.append(Node("PropertyMesh", children=[Node("m_RenderParameters", data=render + isd_edit.RENDER_TINT)]))
    return Node("Object", children=kids)


def isd_bytes() -> bytes:
    """A 32x32 island in 2070's form: 2x2 chunks at height 1, the sea bed under grass, a sand
    rim in the first chunk, three objects (two trees, a prop) and a resource in a group."""
    per = SIZE // CHUNKS
    elements = []
    for k in range(CHUNKS * CHUNKS):
        heights = np.ones((per + 1, per + 1), "<f4").tobytes()
        layers = [(0, 255), (52, 200)] + ([(78, 100)] if k == 0 else [])
        el = Node(
            "Element", children=[Node("HeightMap", children=[Node("Width", str(per + 1)), Node("Data", data=heights)])]
        )
        for guid, alpha in layers:
            am = Node(
                "AlphaMap", children=[Node("Width", str(per + 1)), Node("Data", data=bytes([alpha]) * (per + 1) ** 2)]
            )
            el.children.append(Node("TexIndexData", children=[Node("TextureIndex", str(guid)), am]))
        elements.append(el)
    chunk_map = Node("ChunkMap", children=[Node("Width", str(CHUNKS)), Node("Height", str(CHUNKS)), *elements])
    terrain = Node(
        "Terrain", short=True, children=[Node("TileCountX", str(SIZE)), Node("TileCountZ", str(SIZE)), chunk_map]
    )
    v2 = np.full((SIZE, SIZE), 1024, "<i2").tobytes()
    groups = Node(
        "Objects",
        children=[
            Node("Handle", children=[Node("Objects", children=[_obj(10, 350, 5, 5, nature=False)])]),
            Node("Simple", children=[Node("Objects", children=[_obj(11, 333, 8, 8, nature=False)])]),
            Node("Nature", children=[Node("Objects", children=[_obj(12, 40, 10, 12), _obj(13, 40, 20, 22, var=1)])]),
        ],
    )
    gop = Node(
        "m_GOPManager",
        children=[
            Node("m_GRIDManager", children=[Node("m_HeightMap_v2", data=v2)]),
            groups,
            Node("ObjectGroups", children=[Node("i", data=struct.pack("<2I", 12, 13))]),
        ],
    )
    root = Node("", children=[Node("Width", str(SIZE)), Node("Height", str(SIZE)), Node("Clime", "0"), terrain, gop])
    return isd2070.serialize(root)


PALM = r"""<m_Config><m_Models><m_Config><m_FileName>data\graphics\landscape\trees\rdm\palm_lod0.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType><m_Materials>
<m_Config><m_Name>bark</m_Name><m_DiffuseTexture>data\graphics\landscape\trees\maps\palm_diff.png</m_DiffuseTexture>
<m_DiffuseEnabled>1</m_DiffuseEnabled><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
<m_Config><m_Name>hole</m_Name><m_ShaderIndex>4</m_ShaderIndex><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
</m_Materials></m_Config></m_Models><m_ConfigType>ROOT</m_ConfigType></m_Config>"""


def palm_rdm() -> bytes:
    """A mesh of two triangles, one drawn with the bark, one with the terrain cut-out."""
    rows = b"".join(struct.pack("<4e", x, y, z, 1.0) + bytes((128, 255, 128, 0)) * 3 + bytes(4) for x, y, z in
                    ((0, 0, 0), (1, 0, 0), (0, 2, 0), (1, 2, 0)))  # fmt: skip
    subs = [RdmSubmesh(0, 3, 0), RdmSubmesh(3, 3, 1)]
    mesh = RdmMeshData("palm", rows, 24, FORMAT_P4H_N4B_G4B_B4B_T2H, [0, 1, 2, 2, 1, 3], ["bark", "hole"], subs,
                       (0.0, 0.0, 0.0, 1.0, 2.0, 0.0))  # fmt: skip
    return write_rdm(mesh)


def install(extra: dict[str, bytes] | None = None) -> FakeTree:
    """A 2070 install: the island, the assets, the ground textures and a palm model (the other palm draws nothing)."""
    return FakeTree(
        {
            SOURCE: isd_bytes(),
            objects2070.ASSETS_2070: ASSETS.encode(),
            "data/config/engine/groundtextures.xml": GROUND.encode(),
            "data/graphics/landscape/trees/palm_01.cfg": b"<Config/>",
            "data/graphics/landscape/trees/palm_02.cfg": PALM.encode(),
            "data/graphics/landscape/trees/rdm/palm_lod0.rdm": palm_rdm(),
            "data/graphics/landscape/trees/maps/palm_diff_0.dds": b"DDS ",
            **(extra or {}),
        }
    )


def test_serialize_gives_the_parsed_bytes_back():
    data = isd_bytes()
    root = isd2070.parse(data)
    assert isd2070.serialize(root) == data
    assert root.find("Terrain").short and not root.find("m_GOPManager").short  # </> and </Name> both kept
    assert data.count(b"</>\r\n") == 1 and b"CDATA[" in data


def test_objects_move_turn_scale_and_go():
    isl = isd_edit.Island2070(isd_bytes())
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
    out = isd2070.Island(isl.write())
    tree = next(o for o in out.objects() if o.guid == 40)
    assert (tree.x, tree.y, tree.z) == (3.5, 2.0, 4.25) and tree.scale == 1.5
    assert tree.direction == 90 * 4096 and tree.orientation[1] == pytest.approx(-0.70710678)
    assert len(out.objects()) == 3
    assert out.root.path("m_GOPManager/ObjectGroups").children == []  # the group named the deleted tree
    prop = next(o for o in out.root.path("m_GOPManager/Objects/Simple/Objects").children)
    pos_quat_scale = isd_edit.RENDER_TRANSFORM.unpack_from(
        prop.path("PropertyMesh/m_RenderParameters").data, len(isd_edit.RENDER_HEAD)
    )
    assert pos_quat_scale[:3] == (9.0, 1.0, 9.0) and pos_quat_scale[-1] == 0.25  # drawn where it now stands


def test_library_objects_are_added_in_their_group():
    isl = isd_edit.Island2070(isd_bytes())
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
    out = isd2070.Island(isl.write())
    added = [o for o in out.objects() if (o.x, o.z) in ((16, 16), (6, 6))]
    assert sorted((o.kind, o.guid) for o in added) == [("Nature", 40), ("Simple", 333)]
    palm = next(o for o in added if o.guid == 40)
    assert palm.variation == 1 and palm.scale == 1.2
    ids = [n.int("m_ID") for n in out.root.iter() if n.name == "Object"]
    assert len(ids) == len(set(ids)) and max(ids) == 16  # the next free m_ID each


def test_heights_change_the_chunks_and_the_logic_grid():
    isl = isd_edit.Island2070(isd_bytes())
    assert isl.apply_heights({"16,16": 2.0, "16,17": 2.0}) == 2
    out = isd2070.Island(isl.write())
    hm = np.array(out.heightmap())
    assert hm[16, 16] == 3.0 and hm[16, 17] == 3.0 and hm[15, 16] == 1.0  # the corner all four chunks share
    v2 = np.frombuffer(out.root.path("m_GOPManager/m_GRIDManager/m_HeightMap_v2").data, "<i2").reshape(SIZE, SIZE)
    assert v2[16, 16] == 1024 + 1024 and v2[15, 16] == 1024 + 1024 and v2[15, 15] == 1024 + 512 and v2[0, 0] == 1024


def test_paint_raises_its_layer_and_lowers_those_above():
    isl = isd_edit.Island2070(isd_bytes())
    stroke = {"material": 78, "radius": 3, "strength": 1, "points": [[24, 24]]}  # beach where only chunk 0 has it
    assert isl.apply_paint([stroke]) == 1
    out = isd2070.Island(isl.write())
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
    arc = RdaArchive(tmp_path / "patch.rda")
    assert arc.read("data/loca/eng/txt/credits.txt") == b"credits"
    assert np.array(isd2070.Island(arc.read(SOURCE)).heightmap())[1, 1] == 2.0
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
    ed = isd_edit.Island2070Editor(install(), SOURCE, tmp_path / "cache")
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

    isl = isd_edit.Island2070(isd_bytes())
    isl.apply_heights({f"{z},{x}": -11.0 for z in range(0, 9) for x in range(0, 9)})  # a bay at z, x = 0
    vanilla = _far_dds(64)
    out = isd_build.far_view(isl.island, vanilla)
    assert len(out) == len(vanilla) and out[:128] == vanilla[:128]
    img = np.asarray(Image.open(BytesIO(out)).convert("RGBA")).astype(int)
    assert (img[..., 0] == 10).all() and (img[..., 2] == 30).all()  # the ground picture stays
    assert img[-1, 0, 3] == 0 and img[0, -1, 3] == 254  # the bay is in the last rows: stored from the far edge
    band = img[-30:, 0, 1]  # up the first column: sea, coast at about 9 tiles (18 px), land
    assert band[-1] == 0 and band.max() > 140 and band[0] == 0 and 12 <= 30 - band.argmax() <= 22


def test_heights_out_at_sea_switch_the_chunk_on():
    root = isd2070.parse(isd_bytes())
    el = root.path("Terrain/ChunkMap").findall("Element")[3]
    el.children = [Node("VertexResolution", "-1"), Node("Flags", "65536")]  # chunk (1, 1) is open sea
    used = Node(
        "UsedChunks",
        children=[
            Node("m_XSize", "2"),
            Node("m_YSize", "2"),
            Node("m_IntXSize", "1"),
            Node("m_BitGrid", data=bytes([1, 0, 0, 0, 1, 0, 0, 0])),
        ],
    )
    root.children.insert(3, used)
    isl = isd_edit.Island2070(isd2070.serialize(root))
    assert isl.apply_heights({"24,24": 5.0}) == 1
    out = isd2070.Island(isl.write())
    chunk = out.root.path("Terrain/ChunkMap").findall("Element")[3]
    assert [c.name for c in chunk.children] == ["VertexResolution", "HeightMap", "Flags", "TexIndexData"]
    assert chunk.int("VertexResolution") == 4 and chunk.int("Flags") == 131072
    hm = np.array(out.heightmap())
    assert (
        hm[24, 24] == 6.0 and hm[16, 16] == 1.0
    )  # its corner raised from where the editor showed it; a shared border kept
    assert out.root.path("UsedChunks/m_BitGrid").data == bytes([1, 0, 0, 0, 3, 0, 0, 0])


def test_planted_forest_tiles_become_trees_of_the_islands_own_kinds():
    isl = isd_edit.Island2070(isd_bytes())
    types = objects2070.read_types(install())
    assert isd_edit.forest_kinds(isl.island, types) == [(40, 0), (40, 1)]  # one of each: by GUID, variation
    forest = {"3,4": 1, "3,5": 1, "10,12": 1, "6,6": 0}  # (10, 12) has its tree already; 0 is a cleared tile
    report = isl.apply({"forest": forest}, types)
    assert report["trees planted"] == 2
    planted = [o for o in isd2070.Island(isl.write()).objects() if (int(o.x), int(o.z)) in ((3, 4), (3, 5))]
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
    el.find("HeightMap").children = [Node("Width", "33"), Node("Data", data=fine.tobytes())]
    isl = isd2070.Island(isd2070.serialize(root))
    hm = np.array(isl.heightmap())
    assert hm[1, 1] == 202.0 and hm[0, 3] == 6.0 and hm[15, 15] == 3030.0  # samples (2, 2), (0, 6), (30, 30)
    edit = isd_edit.Island2070(isd2070.serialize(root))
    edit.apply_heights({"1,1": 2.0})
    data = np.frombuffer(edit.root.path("Terrain/ChunkMap").findall("Element")[0].path("HeightMap/Data").data, "<f4")
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
    obj = next(o for o in isd2070.Island(isd2070.serialize(root)).objects() if o.guid == 333)
    assert obj.orientation == pytest.approx(tilt) and obj.scale == pytest.approx(0.4)
    edit = isd_edit.Island2070(isd2070.serialize(root))
    edit.apply(
        {"objects": {"2070:11": {"rotation": [0, -0.70710678, 0, 0.70710678]}}}, objects2070.read_types(install())
    )
    moved = next(o for o in isd2070.Island(edit.write()).objects() if o.guid == 333)
    assert moved.orientation == pytest.approx((0, -0.70710678, 0, 0.70710678)) and moved.scale == pytest.approx(0.4)


def test_older_1404_objects_keep_their_position_in_a_child_node():
    root = isd2070.parse(isd_bytes())
    tree = next(o for _, objs in isd_edit._groups(root) for o in objs.children if o.int("m_ID") == 12)
    pos = tree.find("m_Position")
    pos.children, pos.data = [Node("data", data=pos.data)], None
    data = isd2070.serialize(root)
    assert [o.x for o in isd2070.Island(data).objects() if o.guid == 40][0] == 10.0
    isl = isd_edit.Island2070(data)
    isl.apply({"objects": {"2070:12": {"position": [3.0, 1.0, 4.0]}}}, {})
    moved = next(o for o in isd2070.Island(isl.write()).objects() if o.id == 12)
    assert (moved.x, moved.y, moved.z) == (3.0, 1.0, 4.0)


def test_a_2070_model_is_drawn_from_the_games_own_files(tmp_path):
    ed = isd_edit.Island2070Editor(install(), SOURCE, tmp_path / "cache")
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
