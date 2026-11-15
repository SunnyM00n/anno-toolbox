"""A model read as it is drawn (models/modelread): 2070 configs, their materials and leaf cards, a mesh's surface."""

import json
import struct

import pytest

from annotoolbox.formats.rdmglb import rdm_to_glb
from annotoolbox.formats.rdmwrite import FORMAT_P4H_N4B_G4B_B4B_T2H, RdmMeshData, RdmSubmesh, write_rdm
from annotoolbox.models import cfg2070, modelread

CFG = r"""<m_Config><m_Models>
<m_Config><m_FileName>data\gfx\rdm\house_lod0.rdm</m_FileName><m_ConfigType>MODEL</m_ConfigType><m_Materials>
<m_Config><m_Name>Wall</m_Name><m_DiffuseTexture>data\gfx\maps\wall_diff.png</m_DiffuseTexture>
<m_NormalTexture>data\gfx\maps\wall_norm.png</m_NormalTexture><m_DiffuseEnabled>1</m_DiffuseEnabled>
<m_NormalEnabled>1</m_NormalEnabled><m_cDiffuseColor.r>0.5</m_cDiffuseColor.r><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
<m_Config><m_Name>Hole</m_Name><m_ShaderIndex>4</m_ShaderIndex><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
<m_Config><m_Name>Ivy</m_Name><m_DiffuseTexture>data\gfx\maps\ivy_leaves.png</m_DiffuseTexture>
<m_DiffuseEnabled>1</m_DiffuseEnabled><m_ConfigType>MATERIAL</m_ConfigType></m_Config></m_Materials>
<m_Transformer><m_Config><m_Position.x>2</m_Position.x><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config>
</m_Transformer></m_Config>
<m_Config><m_FileName>data\gfx\rdm\ruin.rdm</m_FileName><m_ConfigType>MODEL</m_ConfigType>
<m_Transformer><m_Config><m_Conditions>2</m_Conditions><m_ConfigType>VISIBILITY_TRANSFORM</m_ConfigType></m_Config>
</m_Transformer></m_Config></m_Models>
<m_InstancingBBClusters><m_Config><m_BBClusterSize>2</m_BBClusterSize>
<m_BBPosX0>0</m_BBPosX0><m_BBPosY0>2</m_BBPosY0><m_BBPosZ0>0</m_BBPosZ0><m_BBSize0>1</m_BBSize0>
<m_BBPosX1>2</m_BBPosX1><m_BBPosY1>2</m_BBPosY1><m_BBPosZ1>0</m_BBPosZ1><m_BBSize1>0.5</m_BBSize1>
<m_ConfigType>INSTANCING_BBCLUSTER</m_ConfigType></m_Config></m_InstancingBBClusters>
<m_Files><m_Config><m_FileName>data\gfx\flag.cfg</m_FileName><m_ConfigType>FILE</m_ConfigType></m_Config></m_Files>
<m_ConfigType>ROOT</m_ConfigType></m_Config>"""
SHIPPED = {"data/gfx/maps/wall_diff_0.dds", "data/gfx/maps/ivy_leaves_0.dds"}  # no normal map ships


def texture(name: str) -> str:
    stem = name.replace("\\", "/").lower().rsplit(".", 1)[0]
    return next((p for p in (stem + "_0.dds",) if p in SHIPPED), "")


def quad_rdm() -> bytes:
    """Two triangles: the first drawn with material 0, the second with material 2."""
    rows = b"".join(
        struct.pack("<4e", x, 0.0, z, 1.0) + bytes((128, 255, 128, 0)) * 3 + bytes(4)
        for x, z in ((0, 0), (1, 0), (0, 1), (1, 1))
    )
    subs = [RdmSubmesh(0, 3, 0), RdmSubmesh(3, 3, 2)]
    mesh = RdmMeshData("house", rows, 24, FORMAT_P4H_N4B_G4B_B4B_T2H, [0, 1, 2, 2, 1, 3], ["a", "b", "c"], subs,
                       (0.0, 0.0, 0.0, 1.0, 0.0, 1.0))  # fmt: skip
    return write_rdm(mesh)


def test_a_config_reads_as_pieces_with_their_surfaces_in_material_order():
    model = modelread.read_config(CFG, texture, "data/gfx/house.cfg")
    (house,) = model.pieces  # the ruin shows only in a state
    assert house.mesh == "data/gfx/rdm/house_lod0.rdm" and house.place.position == (2.0, 0.0, 0.0)
    assert [(s.name, s.kind) for s in house.surfaces] == [("Wall", "model"), ("Hole", "cutout"), ("Ivy", "model")]
    wall = house.surfaces[0]
    assert (wall.diffuse, wall.normal) == ("data/gfx/maps/wall_diff_0.dds", "")  # the normal map is not shipped
    assert house.materials["ivy"] == ("data/gfx/maps/ivy_leaves_0.dds", "")
    assert model.files == [("data/gfx/flag.cfg", modelread.Place())]
    (leaves,) = model.leaves
    assert (leaves.cfg, leaves.index) == ("data/gfx/house.cfg", 0)


def test_the_cards_of_a_leaf_cluster_face_out_of_its_middle():
    cluster = cfg2070.parse_cfg(CFG).billboard_clusters[0]
    left, right = modelread.cards(cluster)
    assert left.reach == 0.5 and right.reach == 0.25
    assert left.radial == pytest.approx((-1.0, 0.0, 0.0))  # from the middle, level (no lift)
    u0, v0, u1, v1 = cluster.billboards[0].texcoords
    assert left.uvs == ((u0, v1), (u1, v1), (u1, v0), (u0, v0))  # the corners in drawing order
    assert modelread.cards(cfg2070.BillboardCluster([], [], None)) == []


def test_a_mesh_surface_and_one_materials_part():
    data = quad_rdm()
    pos, tri = modelread.surface(data)
    assert pos.shape == (4, 3) and tri.tolist() == [[0, 1, 2], [2, 1, 3]]
    assert modelread.surface(b"not a mesh") is None

    def triangles(glb: bytes) -> int:
        doc = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
        return sum(a["count"] for a in doc["accessors"] if a.get("type") == "SCALAR") // 3

    assert triangles(rdm_to_glb(data)) == 2 and triangles(rdm_to_glb(data, 2)) == 1
    with pytest.raises(ValueError):
        rdm_to_glb(data, 1)  # no submesh draws with it
