"""Granny 2 files (``grannyread``, ``grannyoodle``, ``grannyglb``) and the old games' model configs that name them
(``models.modelread``, ``othergames.grannymodels``, ``editor.grannyviewer``), on files built here: a test encoder writes the Oodle1
stream the decoder reads."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path

import pytest
from tests.fakes import FakeTree
from tests.formats.formatfakes import granny_file, oodle1

from annotoolbox.editor import modelviewer
from annotoolbox.editor.assetmodels import CReason
from annotoolbox.editor.grannyviewer import CGrannyModels, piece_parts
from annotoolbox.formats import grannyoodle
from annotoolbox.formats import grannyread as gr
from annotoolbox.formats.grannyglb import granny_to_glb, read_meshes
from annotoolbox.models import modelread
from annotoolbox.othergames import grannymodels


@pytest.mark.parametrize("pointer", [4, 8])
@pytest.mark.parametrize("compress", [False, True])
def test_file_reads_its_type_tree(pointer, compress):
    f = gr.CGrannyFile(granny_file(pointer, compress))
    f.check()
    root = f.root()
    assert f.m_pointer == pointer and list(root) == ["ArtToolInfo", "Placement", "Meshes", "FromFileName"]
    assert root["ArtToolInfo"]["UnitsPerMeter"] == 1.0 and root["FromFileName"] == "x.max"
    assert root["Placement"]["flags"] == 7 and root["Placement"]["position"] == (1, 2, 3)
    (mesh,) = root["Meshes"]
    assert mesh["Name"] == "house" and [b["Material"]["Name"] for b in mesh["MaterialBindings"]] == ["Wood", "roof"]
    vertices = mesh["PrimaryVertexData"]["Vertices"]
    assert vertices.count == 4 and vertices.names() == ["Position", "Normal", "TextureCoordinates0"]
    assert vertices.column("TextureCoordinates0")[2] == (1.0, 1.0)
    assert mesh["PrimaryTopology"]["Indices"] == []


def test_a_written_mesh_of_over_32768_vertices_keeps_its_16_bit_indices():
    from annotoolbox.formats import grannywrite as gw

    count = 40000
    material = gw.SRec(gw.MATERIAL, {"Name": "Wood"})
    spec = gw.SMeshSpec("big", [(float(i), 0.0, 0.0) for i in range(count)], [(0.0, 1.0, 0.0)] * count,
                       [(0.0, 0.0)] * count, [(material, [0, 39999, 33000])])  # fmt: skip
    root = gw.root_record("big.max", Materials=[material], Meshes=[gw.mesh_record(spec)])
    (mesh,) = read_meshes(gw.write_file(root))
    assert mesh.groups == [("Wood", [0, 39999, 33000])]


def test_damaged_counts_are_errors_not_huge_loops():
    data = bytearray(granny_file())
    struct.pack_into("<I", data, gr.INFO_AT + 16, 10**6)  # the section count
    with pytest.raises(gr.CGrannyError, match="section table"):
        gr.CGrannyFile(bytes(data))
    f = gr.CGrannyFile(granny_file())
    members = f.root().m_members
    with pytest.raises(gr.CGrannyError, match="runs past"):
        gr._array(f, members, 10**9, (0, 0))


def test_meshes_and_glb_per_material():
    data = granny_file(compress=True)
    (mesh,) = read_meshes(data)
    assert mesh.positions[2] == (1.0, 0.0, 1.0) and mesh.normals[0] == (0.0, 1.0, 0.0)
    assert mesh.groups == [("Wood", [0, 1, 2]), ("roof", [0, 2, 3])]
    glb = granny_to_glb(data, "wood")
    doc = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert glb[:4] == b"glTF" and doc["accessors"][-1]["count"] == 3
    with pytest.raises(gr.CGrannyError, match="material 'glass'"):
        granny_to_glb(data, "glass")


def test_oodle1_literals_and_matches_in_three_runs():
    text = b"abcabcabcabc-the quick brown fox, the quick brown fox; " * 6 + bytes(range(256))
    packed = oodle1(text, 40, 90)
    assert len(packed) < len(text)
    assert grannyoodle.decompress(packed, len(text), 40, 90) == text
    with pytest.raises(grannyoodle.CGrannyCompressionError):
        grannyoodle.decompress(packed[:20], 10, 0, 0)
    with pytest.raises(grannyoodle.CGrannyCompressionError):  # cut inside the coded bytes: no IndexError
        grannyoodle.decompress(packed[: len(packed) // 2], len(text), 40, 90)


def test_unknown_files_and_compressions_are_refused():
    with pytest.raises(gr.CGrannyError, match="magic"):
        gr.CGrannyFile(b"RDM\x01" + bytes(100))
    data = bytearray(granny_file())
    struct.pack_into("<I", data, gr.INFO_AT + 72, 4)  # Bitknit
    with pytest.raises(gr.CGrannyError, match="compression 4"):
        gr.CGrannyFile(bytes(data)).check()


CFG_1404 = """<m_Config><m_Models><m_Config><m_FileName>data\\gfx\\gr\\House_lod0.GR2</m_FileName>
<m_ConfigType>MODEL</m_ConfigType><m_Materials><m_Config><m_Name>Wood</m_Name>
<m_DiffuseTexture>data\\gfx\\maps\\wood_diff.png</m_DiffuseTexture><m_NormalTexture>data\\gfx\\maps\\wood_norm.png</m_NormalTexture>
<m_DiffuseEnabled>1</m_DiffuseEnabled><m_NormalEnabled>0</m_NormalEnabled><m_ConfigType>MATERIAL</m_ConfigType></m_Config></m_Materials>
<m_Transformer><m_Config><m_Position.x>2</m_Position.x><m_Scale>2</m_Scale><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config></m_Transformer>
</m_Config><m_Config><m_FileName>data\\gfx\\gr\\ruin.gr2</m_FileName><m_ConfigType>MODEL</m_ConfigType>
<m_Transformer><m_Config><m_Conditions>2</m_Conditions><m_ConfigType>VISIBILITY_TRANSFORM</m_ConfigType></m_Config></m_Transformer>
</m_Config></m_Models>
<m_InstancingModels><m_Config><m_InstancingModel>data\\gfx\\gr\\trunk.gr2</m_InstancingModel><m_ColorCount>1</m_ColorCount>
<m_ColorR0>0.5</m_ColorR0><m_ColorG0>0.25</m_ColorG0><m_ColorB0>1</m_ColorB0><m_ConfigType>INSTANCING_MODEL</m_ConfigType></m_Config></m_InstancingModels>
<m_InstancingBBClusters><m_Config><m_BBClusterSize>2</m_BBClusterSize>
<m_BBPosX0>0</m_BBPosX0><m_BBPosY0>2</m_BBPosY0><m_BBPosZ0>0</m_BBPosZ0><m_BBSize0>1</m_BBSize0><m_BBTexZ0>0.5</m_BBTexZ0><m_BBTexW0>0.5</m_BBTexW0>
<m_BBPosX1>1</m_BBPosX1><m_BBPosY1>2</m_BBPosY1><m_BBPosZ1>0</m_BBPosZ1><m_BBSize1>0.5</m_BBSize1>
<m_ConfigType>INSTANCING_BBCLUSTER</m_ConfigType></m_Config></m_InstancingBBClusters>
<m_Files><m_Config><m_FileName>data\\gfx\\flag.cfg</m_FileName><m_ConfigType>FILE</m_ConfigType>
<m_Transformer><m_Config><m_Position.y>3</m_Position.y><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config></m_Transformer></m_Config></m_Files>
<m_ConfigType>ROOT</m_ConfigType></m_Config>"""


def test_1404_config_pieces_textures_and_leaves():
    tree = FakeTree({"data/gfx/maps/wood_diff_0.dds": b"", "data/gfx/maps/wood_norm_0.dds": b""})
    model = modelread.read_config(CFG_1404, lambda n: modelread.texture_path(n, tree.exists), "data/gfx/house.cfg")
    house, trunk = model.pieces  # the ruin shows only when damaged
    assert house.mesh == "data/gfx/gr/house_lod0.gr2" and house.materials == {
        "wood": ("data/gfx/maps/wood_diff_0.dds", "")
    }
    assert house.place.position == (2.0, 0.0, 0.0) and house.place.scale == (2.0, 2.0, 2.0)
    assert trunk.color == (0.5, 0.25, 1.0)
    assert model.files == [("data/gfx/flag.cfg", modelread.SPlace((0.0, 3.0, 0.0)))]
    (part,) = piece_parts(house, ["Wood"])
    assert part["mesh"] == "data/gfx/gr/house_lod0.gr2|Wood" and part["diffuse"].endswith("wood_diff_0.dds")
    leaves = modelviewer.leaves_part(model.leaves[0])
    assert leaves["shader"] == 28 and modelviewer.leaves_ref(leaves["mesh"]) == ("data/gfx/house.cfg", 0)
    assert modelviewer.leaves_ref(part["mesh"]) is None
    cards = modelviewer.leaf_cards(CFG_1404, 0)
    assert len(cards["indices"]) == 12 and cards["corners"][:2] == [-0.5, -0.5] and cards["uvs"][:2] == [0.0, 0.5]


MCF = """<m_MeshConfig><m_MCClassType>0</m_MCClassType><m_MCChilds>
<m_MCConfig><m_MeshConfig><m_MCClassType>1</m_MCClassType><m_MeshFilename>gr2\\far.gr2</m_MeshFilename></m_MeshConfig>
<m_MeshTransform><m_MCClassType>105</m_MCClassType><m_LODMax>60</m_LODMax></m_MeshTransform></m_MCConfig>
<m_Material><m_MCClassType>51</m_MCClassType><m_Name>01</m_Name><m_DiffuseTexture>tex\\a&amp;b.tga</m_DiffuseTexture><m_BumpTexture/></m_Material>
<m_MCConfig><m_MeshConfig><m_MCClassType>1</m_MCClassType><m_MeshFilename>gr2\\near.gr2</m_MeshFilename></m_MeshConfig>
<m_MeshTransform><m_MCClassType>105</m_MCClassType><m_LODMax>100</m_LODMax></m_MeshTransform>
<m_MeshTransform><m_MCClassType>101</m_MCClassType><m_OffsetPosition.x>32</m_OffsetPosition.x></m_MeshTransform></m_MCConfig>
<m_MCConfig><m_MeshConfig><m_MCClassType>1</m_MCClassType><m_MeshFilename>gr2\\burnt.gr2</m_MeshFilename></m_MeshConfig>
<m_MeshTransform><m_MCClassType>109</m_MCClassType><m_ConditionTrigger>IS_DAMAGED</m_ConditionTrigger></m_MeshTransform></m_MCConfig>
<m_MCConfig><m_MeshConfig><m_MCClassType>2</m_MCClassType><m_MeshConfigFilename>flag.mcf</m_MeshConfigFilename></m_MeshConfig>
<m_MeshTransform><m_MCClassType>102</m_MCClassType><m_q.w>0</m_q.w><m_q.y>1</m_q.y></m_MeshTransform>
<m_MeshTransform><m_MCClassType>107</m_MCClassType><m_ScaleFactor.x>2</m_ScaleFactor.x><m_ScaleFactor.y>2</m_ScaleFactor.y><m_ScaleFactor.z>2</m_ScaleFactor.z></m_MeshTransform></m_MCConfig>
<m_MCConfig><m_MeshConfig><m_MCClassType>7</m_MCClassType><m_GrannyObjectFilename>tree.mcf</m_GrannyObjectFilename></m_MeshConfig></m_MCConfig>
</m_MCChilds></m_MeshConfig>"""
FLAG = """<m_MeshConfig><m_MCClassType>0</m_MCClassType><m_MCChilds><m_MCConfig><m_MeshConfig><m_MCClassType>9</m_MCClassType>
<m_MeshFilename>flag.gr2</m_MeshFilename><m_DiffuseTexture>tex\\flag.tga</m_DiffuseTexture></m_MeshConfig>
<m_MeshTransform><m_MCClassType>101</m_MCClassType><m_OffsetPosition.x>16</m_OffsetPosition.x></m_MeshTransform></m_MCConfig></m_MCChilds></m_MeshConfig>"""
TREE = FLAG.replace("<m_MCClassType>9", "<m_MCClassType>1").replace("flag.gr2", "trunk.gr2")


def test_1701_mesh_configs_nest_scale_and_choose():
    tree = FakeTree({"house.mcf": MCF, "flag.mcf": FLAG, "tree.mcf": TREE, "tex/a&b.tga": b"", "tex/flag.tga": b""})
    near, flag, trunk = grannymodels.read_1701("house.mcf", tree.read_text, tree.exists, 1 / 32).pieces
    assert trunk.mesh == "trunk.gr2" and trunk.place.position == (0.5, 0.0, 0.0)  # vegetation naming an .mcf
    assert near.mesh == "gr2/near.gr2" and near.place.position == (1.0, 0.0, 0.0)
    assert near.materials == {"01": ("tex/a&b.tga", "")}
    assert flag.mesh == "flag.gr2" and flag.materials == {modelread.ANY: ("tex/flag.tga", "")}
    assert flag.place.position == pytest.approx((-1.0, 0.0, 0.0))  # turned half round, scaled 2, 1/32 a unit
    assert flag.place.scale == pytest.approx((2 / 32,) * 3)
    ocf = (
        "<ObjectConfig><GUID>7</GUID><MeshConfig>a\\x.mcf</MeshConfig><MeshConfig>a\\y.mcf</MeshConfig></ObjectConfig>"
    )
    assert grannymodels.object_configs({"x.ocf": ocf, "bad.ocf": "<"}) == {7: ["a/x.mcf", "a/y.mcf"]}


class FakeViewer(CGrannyModels):
    def __init__(self, tree, cache: Path) -> None:
        self.m_tree2070, self.m_cache, self.m_notes = tree, cache, []
        self._m_locks: dict = {}

    def _cached(self, key: str, suffix: str) -> Path:
        return self.m_cache / (str(abs(hash(key))) + suffix)

    def _lock(self, path):
        return self._m_locks.setdefault(path, threading.Lock())

    def model_config(self, cfg):
        exists = self.m_tree2070.exists
        return modelread.read_config(self.m_tree2070.read_text(cfg), lambda n: modelread.texture_path(n, exists), cfg)


def test_viewer_draws_readable_models_and_names_the_rest(tmp_path):
    broken = bytearray(granny_file())
    struct.pack_into("<I", broken, gr.INFO_AT + 72, 3)
    house = CFG_1404.replace("ruin.gr2", "x.gr2")
    tree = FakeTree(
        {
            "data/gfx/house.cfg": house,
            "data/gfx/gr/house_lod0.gr2": granny_file(compress=True),
            "data/gfx/gr/trunk.gr2": granny_file(8),
            "data/gfx/maps/wood_diff_0.dds": b"",
            "data/gfx/partly.cfg": house.replace("trunk.gr2", "bad.gr2"),
            "data/gfx/broken.cfg": CFG_1404.split("<m_InstancingModels>")[0].replace("House_lod0.GR2", "bad.gr2")
            + "</m_Config>",
            "data/gfx/gr/bad.gr2": bytes(broken),
        }
    )
    viewer = FakeViewer(tree, tmp_path)
    assert viewer.model_cfg("data/gfx/house.cfg") == "data/gfx/house.cfg"
    assert viewer.model_cfg("data/gfx/partly.cfg") == "data/gfx/partly.cfg"  # draws without its bad mesh
    assert viewer.model_cfg("data/gfx/broken.cfg") == "" and viewer.model_cfg("data/gfx/none.cfg") == ""
    notes = viewer.model_notes()
    assert notes[0] == "2 model configs drawn as markers" and "broken.cfg: data/gfx/gr/bad.gr2: section 0" in notes[1]
    assert notes[3] == "1 meshes left out of the models" and "compression 3" in notes[4]
    assert [p["mesh"] for p in viewer.model("data/gfx/partly.cfg")["parts"]][-1] == "data/gfx/partly.cfg|leaves0"
    spec = viewer.model("data/gfx/house.cfg")
    assert [p["mesh"].rsplit("/", 1)[1] for p in spec["parts"]] == [
        "house_lod0.gr2|Wood",
        "house_lod0.gr2|roof",
        "trunk.gr2|Wood",
        "trunk.gr2|roof",
        "house.cfg|leaves0",
    ]
    assert spec["files"][0]["cfg"] == "data/gfx/flag.cfg"
    assert viewer.mesh_glb(spec["parts"][1]["mesh"])[:4] == b"glTF"
    assert len(viewer.banner(spec["parts"][-1]["mesh"])["positions"]) == 24


EFFECT_ONLY = (
    "<m_Config><m_Effects><m_Config><m_FileName>data\\gfx\\fx\\smoke.efc</m_FileName>"
    "<m_ConfigType>EFFECT</m_ConfigType></m_Config></m_Effects></m_Config>"
)
DECAL_ONLY = (
    "<m_Config><m_TerrainDecals><m_Config><m_Extend.x>0.5</m_Extend.x><m_ConfigType>TERRAINDECAL</m_ConfigType>"
    "</m_Config></m_TerrainDecals></m_Config>"
)


def test_a_config_of_an_effect_alone_draws_when_a_sprite_does_and_a_decal_alone_says_so(tmp_path):
    tree = FakeTree(
        {
            "data/gfx/smoke.cfg": EFFECT_ONLY,
            "data/gfx/ring.cfg": EFFECT_ONLY.replace("smoke", "ring"),
            "data/gfx/crack.cfg": DECAL_ONLY,
        }
    )
    viewer = FakeViewer(tree, tmp_path)
    viewer.effect = lambda path: [path] if "smoke" in path else []  # a model effect gives no sprite
    assert viewer.model_cfg("data/gfx/smoke.cfg") == "data/gfx/smoke.cfg"  # its particles are the model
    assert viewer.model_cfg("data/gfx/ring.cfg") == "" and viewer.model_cfg("data/gfx/crack.cfg") == ""
    assert viewer._model_state()[0]["data/gfx/crack.cfg"] == CReason.EMPTY  # a pin with a reason the Checks tab lists
