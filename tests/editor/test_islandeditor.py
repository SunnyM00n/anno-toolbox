"""The island editor's model parts, asset library and water network (``editor.islandeditor``, ``modelparts``)."""

from tests.fakes import FakeFiles

from annotoolbox.editor import assetmodels, islandassets, islandeditor, modelparts

CFG = """<Config><ConfigType>MAIN</ConfigType>
<Models><Config><ConfigType>MODEL</ConfigType>
<Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.x>1</Position.x><Position.y>2</Position.y>
<Position.z>3</Position.z><Rotation.x>0</Rotation.x><Rotation.y>0.7071</Rotation.y><Rotation.z>0</Rotation.z>
<Rotation.w>0.7071</Rotation.w><Scale>0.8</Scale></Config></Transformer>
<Materials><Config><ConfigType>MATERIAL</ConfigType>
<Common [PhysicallyBasedSubstance]>Common [PhysicallyBasedSubstance]</Common [PhysicallyBasedSubstance]>
<cModelDiffTex>data\\x\\maps\\tree_diff.psd</cModelDiffTex><cDiffuseColor.g>1.1</cDiffuseColor.g></Config></Materials>
<FileName>data\\x\\rdm\\tree.rdm</FileName></Config></Models>
<Files><Config><ConfigType>FILE</ConfigType><FileName>data/x/flame.cfg</FileName></Config>
<Config><ConfigType>FILE</ConfigType><FileName>data/x/not_a_model.prp</FileName></Config></Files>
</Config>"""


def test_model_parts_reads_an_anno_config():
    spec = modelparts.model_parts(CFG)
    (part,) = spec["parts"]
    assert part["mesh"] == "data/x/rdm/tree.rdm" and part["diffuse"] == "data/x/maps/tree_diff.psd"
    assert part["position"] == [1, 2, 3] and part["rotation"] == [0, 0.7071, 0, 0.7071] and part["scale"] == [0.8] * 3
    assert part["color"] == [1.0, 1.1, 1.0]
    assert [f["cfg"] for f in spec["files"]] == ["data/x/flame.cfg"]  # only nested configs, no other files


def test_asset_models_and_variations():
    assets = """<ModOps>
<ModOp Type="addNextSibling" GUID="1"><Asset><Template>Slot</Template><Values><Standard><GUID>2070200011</GUID></Standard>
<Object><Variations><Item><Filename>data\\a\\mine_base.cfg</Filename></Item></Variations></Object></Values></Asset></ModOp>
</ModOps>"""
    models = assetmodels.CAssetModels(assets.encode())
    assert models.first_model(2070200011) == "data/a/mine_base.cfg"
    assert models.asset(2070200011).variations == ["data/a/mine_base.cfg"]


def test_a_mod_asset_without_models_takes_its_base_assets_first_model():
    assets = b"""<ModOps>
<ModOp Type="addNextSibling" GUID="1"><Asset><Template>Slot</Template><Values><Standard><GUID>11</GUID></Standard>
<Object><Variations><Item><Filename>data/a/base.cfg</Filename></Item></Variations></Object></Values></Asset></ModOp>
<ModOp Type="addNextSibling" GUID="1"><Asset><Template>Slot</Template><BaseAssetGUID>11</BaseAssetGUID>
<Values><Standard><GUID>12</GUID></Standard></Values></Asset></ModOp></ModOps>"""
    models = assetmodels.CAssetModels(assets)
    assert models.first_model(12) == "data/a/base.cfg" and models.first_model(99) == ""


def test_parse_cfg_takes_the_games_own_quirks():
    from annotoolbox.models import configtree

    root = configtree.parse("<Config><A;>1</A;><B [x y]>2</><C /><D>3</></Config>")
    assert root.findtext("A") == "1" and root.findtext("D") == "3" and root.find("C") is not None
    assert root.findtext(configtree.GROUP) == "B [x y]"  # a heading with a bracket (a material's section) is kept


def test_each_material_of_a_mesh_is_a_part_of_its_own():
    cfg = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/x/rdm/house_lod0.fbx</FileName><Materials>
<Config><ConfigType>MATERIAL</ConfigType><cModelDiffTex>data/x/wall.psd</cModelDiffTex></Config>
<Config><ConfigType>MATERIAL</ConfigType><cModelDiffTex>data/x/roof.psd</cModelDiffTex></Config></Materials>
<MaterialLODInfos><i><Indices><i>1</i><i>0</i></Indices></i></MaterialLODInfos></Config></Models></Config>"""
    (part,) = modelparts.model_parts(cfg)["parts"]
    assert part["mesh"] == "data/x/rdm/house_lod0.rdm" and part["diffuse"] == "data/x/roof.psd"
    assert {k: m["diffuse"] for k, m in part["materials"].items()} == {0: "data/x/roof.psd", 1: "data/x/wall.psd"}


def test_fbx_names_become_the_shipped_rdm():
    cfg = "<Config><Models><Config><FileName>data/x/rdm/waterfall.fbx</FileName></Config></Models></Config>"
    assert modelparts.model_parts(cfg)["parts"][0]["mesh"] == "data/x/rdm/waterfall.rdm"


def test_water_network_is_read_beside_the_island():
    from types import SimpleNamespace

    files = {"data/i/isle.water.json": b'{"rivers": [], "lakes": [{"points": [[1, 2]], "level": 3.5}]}'}

    ed = SimpleNamespace(m_files=FakeFiles(files), m_view=SimpleNamespace(folder="data/i/", name="isle"))
    assert islandeditor.CIslandEditor.water_network(ed)["lakes"][0]["level"] == 3.5
    ed.m_view.name = "vanilla"  # an island the island build did not make has none
    assert islandeditor.CIslandEditor.water_network(ed) is None


def test_library_lists_117_models_and_converted_2070_ones():
    from types import SimpleNamespace

    paths = [
        "data/base/graphics/library/rocks/rock_01.cfg",
        "data/base/graphics/ui/icon.cfg",  # not a model folder
        "data/anno2070/graphics/landscape/stone.cfg",
    ]
    files = SimpleNamespace(list=lambda prefix: [p for p in paths if p.startswith(prefix)], where=lambda p: "mod")
    ed = SimpleNamespace(m_files=files, m_tree2070=None, editor_assets=lambda: ([{"guid": 1, "template": "Herd"}], {}))
    lib = islandeditor.CIslandEditor.library(ed)
    assert lib["117"] == [{"name": "rock_01", "cfg": "data/base/graphics/library/rocks/rock_01.cfg", "group": "rocks"}]
    assert lib["2070"] == [
        {"name": "stone", "template": "", "cfg": "data/anno2070/graphics/landscape/stone.cfg", "group": "landscape"}
    ]
    assert lib["editor"] == [{"guid": 1, "template": "Herd", "group": "Herd"}]


def test_library_groups_by_folder():
    group = modelparts.library_group
    assert group("data/base/graphics/library/props/fences/wall/wall_01.cfg") == "props / fences"
    assert group("data/base/graphics/roman/buildings/ornaments/statue.cfg") == "buildings / ornaments (roman)"
    assert group("data/graphics/landscape/trees/fir_big_01.cfg") == "landscape / trees"


def test_only_an_orientation_transform_places_a_part():
    cfg = """<Config><ConfigType>MAIN</ConfigType><Files>
<Config><ConfigType>FILE</ConfigType><FileName>data/x/boat_static.cfg</FileName>
<Transformer><Config><ConfigType>ANIMATION_TRANSFORM</ConfigType><PositionOffset.y>0.5</PositionOffset.y>
<Rotation.x>10</Rotation.x><Rotation.y>8</Rotation.y><Rotation.z>14</Rotation.z></Config></Transformer></Config>
</Files></Config>"""
    (boat,) = modelparts.model_parts(cfg)["files"]
    assert boat["rotation"] == [0, 0, 0, 1] and boat["position"] == [0, 0.5, 0]  # a sway, not a turn


def test_water_parts_carry_their_shader_and_flipbook():
    cfg = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/x/rdm/fall.rdm</FileName><Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>7</ShaderID>
<cModelDiffTex>data/x/maps/fall_diff.psd</cModelDiffTex><cModelNormalTex>data/x/maps/fall_norm.psd</cModelNormalTex>
<cFlowSpeed>0.25</cFlowSpeed><FLIPBOOK_ENABLED>1</FLIPBOOK_ENABLED><cNumSubImagesX>8</cNumSubImagesX>
<cNumSubImagesY>8</cNumSubImagesY><cNumFrames>64</cNumFrames><cFrameDuration>0.03</cFrameDuration>
<cFlipbookTiling.x>1</cFlipbookTiling.x><cFlipbookTiling.y>0.3</cFlipbookTiling.y></Config></Materials></Config></Models></Config>"""
    (part,) = modelparts.model_parts(cfg)["parts"]
    assert part["shader"] == 7 and part["flow"] == 0.25 and part["normal"] == "data/x/maps/fall_norm.psd"
    assert part["flipbook"] == {"x": 8, "y": 8, "frames": 64, "duration": 0.03, "tiling": [1.0, 0.3]}


def test_banner_leaves_name_their_texture_as_the_example_slot_does():
    cfg = CFG.replace("<cModelDiffTex>", "<cAlbedoTexture>").replace("</cModelDiffTex>", "</cAlbedoTexture>")
    cfg = cfg.replace("cDiffuseColor.g", "cAlbedoColor.g").replace(
        "<Materials><Config><ConfigType>MATERIAL</ConfigType>",
        "<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>28</ShaderID>",
    )
    part = modelparts.model_parts(cfg)["parts"][0]
    assert part["diffuse"] == "data/x/maps/tree_diff.psd" and part["color"] == [1.0, 1.1, 1.0] and part["shader"] == 28


def test_textures_for_the_page_lose_what_the_browser_would_spoil(tmp_path):
    import numpy as np
    from PIL import Image

    normal, ground = tmp_path / "n.png", tmp_path / "g.png"
    Image.fromarray(np.full((4, 4, 4), (128, 128, 0, 3), np.uint8), "RGBA").save(normal)  # RG normal, gloss in A
    Image.fromarray(np.full((4, 4, 4), (90, 80, 70, 0), np.uint8), "RGBA").save(ground)  # a blend weight in A
    islandassets.finish_png(normal, is_normal=True, is_opaque=False)
    islandassets.finish_png(ground, is_normal=False, is_opaque=True)
    n = np.asarray(Image.open(normal))
    assert Image.open(normal).mode == "RGB" and tuple(n[0, 0]) == (128, 128, 255)  # straight up, B rebuilt
    assert Image.open(ground).mode == "RGB" and tuple(np.asarray(Image.open(ground))[0, 0]) == (90, 80, 70)


def test_ground_layers_sum_a_materials_slots_and_shrink_to_the_page():
    import numpy as np

    class FakeView:
        texels = 8

        def weights(self):
            mids = np.zeros((6, 16, 16), int)
            mids[1] = 3
            mids[2, :8] = 5  # material 5 over the top half
            wts = np.zeros((6, 16, 16), np.float32)
            wts[0], wts[1] = 0.25, 0.75
            wts[2, :8], wts[0, :8] = 0.5, 0.0
            return mids, wts

    ed = islandeditor.CIslandEditor.__new__(islandeditor.CIslandEditor)
    ed.m_view = FakeView()
    ids, w = ed.ground_layers()
    assert ids == [0, 3, 5] and w.shape == (3, 8, 8)  # 8 texels a tile down to GROUND_TEXELS
    assert np.allclose(w.sum(axis=0)[4:], 1.0) and w[1, 0, 0] == 0.75 and w[2, 0, 0] == 0.5 and w[2, 7, 0] == 0


def test_chained_and_non_uniform_transforms_place_a_part():
    import pytest

    def orient(y, scale, nonuniform_x=1, turn=False):
        rot = "<Rotation.y>0.7071068</Rotation.y><Rotation.w>0.7071068</Rotation.w>" if turn else ""
        return (
            "<Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType>"
            f"<Position.x>1</Position.x><Position.y>{y}</Position.y>{rot}<Scale>{scale}</Scale>"
            f"<NonUniformScale.x>{nonuniform_x}</NonUniformScale.x></Config>"
        )

    from annotoolbox.models import configtree

    transformer = f"<Transformer>{orient(0, 2, turn=True)}{orient(-0.5, 0.75, nonuniform_x=1.5)}</Transformer>"
    node = configtree.read_tree(f"<Config><ConfigType>MODEL</ConfigType>{transformer}</Config>")
    t = modelparts._placed(node)
    assert t["scale"] == pytest.approx([2 * 0.75 * 1.5, 1.5, 1.5])
    # the second offset (1, -0.5, 0) scaled by 2 and turned 90 degrees about y by the first, after its own (1, 0, 0)
    assert t["position"] == pytest.approx([1.0, -1.0, -2.0], abs=1e-5)
    assert t["rotation"] == pytest.approx([0, 0.7071068, 0, 0.7071068], abs=1e-6)


def test_the_view_draws_each_material_of_a_mesh_from_its_own_cut_of_the_mesh():
    import time

    from annotoolbox.render.modellibrary import CModelLibrary

    cfg = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/x/rdm/house.rdm</FileName><Materials>
<Config><ConfigType>MATERIAL</ConfigType><cModelDiffTex>data/x/wall.psd</cModelDiffTex></Config>
<Config><ConfigType>MATERIAL</ConfigType><cModelDiffTex>data/x/roof.psd</cModelDiffTex></Config></Materials>
</Config></Models></Config>"""
    asked = []

    class FakeEditor:
        def model(self, _cfg):
            return modelparts.model_parts(cfg)

        def mesh_glb(self, path, material=None):
            asked.append((path, material))
            raise FileNotFoundError(path)  # the cut is asked for; no mesh needed here

        def texture_png(self, path, is_normal=False):
            raise FileNotFoundError(path)

    library = CModelLibrary(FakeEditor())
    library.request(["house.cfg"])
    for _ in range(200):
        if library.ready("house.cfg"):
            break
        time.sleep(0.01)
    parts = library.parts("house.cfg")
    assert [(p["mesh_key"], p["diffuse"]) for p in parts] == [("glb:data/x/rdm/house.rdm#0", "data/x/wall.psd"),
                                                             ("glb:data/x/rdm/house.rdm#1", "data/x/roof.psd")]  # fmt: skip
    assert sorted(asked) == [("data/x/rdm/house.rdm", 0), ("data/x/rdm/house.rdm", 1)]
    library.close()


def test_a_cached_file_appears_whole_or_not_at_all(tmp_path):
    import pytest

    out = tmp_path / "a.png"
    with pytest.raises(RuntimeError), islandassets.whole_file(out) as scratch:
        scratch.write_bytes(b"half")
        raise RuntimeError("killed while writing")
    assert not out.exists() and not list(tmp_path.iterdir())
    with islandassets.whole_file(out) as scratch:
        scratch.write_bytes(b"whole")
    assert out.read_bytes() == b"whole" and [p.name for p in tmp_path.iterdir()] == ["a.png"]


def test_an_assets_generator_values_come_from_itself_or_its_base_asset():
    assets = b"""<ModOps>
<ModOp Type="addNextSibling" GUID="1"><Asset><Template>Slot</Template><Values><Standard><GUID>11</GUID><Name>slot a</Name></Standard>
<RandomMapObject><MapGeneratorSlotCount>3</MapGeneratorSlotCount><ActivateAll>0</ActivateAll></RandomMapObject></Values></Asset></ModOp>
<ModOp Type="addNextSibling" GUID="1"><Asset><Template>Slot</Template><BaseAssetGUID>11</BaseAssetGUID>
<Values><Standard><GUID>12</GUID><Name>slot b</Name></Standard><RandomMapObject><ActivateAll>1</ActivateAll></RandomMapObject></Values></Asset></ModOp></ModOps>"""
    models = assetmodels.CAssetModels(assets)
    assert models.value_of(11, "MapGeneratorSlotCount") == "3" and models.value_of(11, "ActivateAll") == "0"
    assert models.value_of(12, "ActivateAll") == "1"  # its own
    assert models.value_of(12, "MapGeneratorSlotCount") == "3"  # its base asset's
    assert models.value_of(99, "ActivateAll") is None and models.value_of(11, "SlotBlocker") is None


def test_a_texture_that_did_not_load_is_listed_in_the_checks():
    from types import SimpleNamespace

    ed = SimpleNamespace(m_texture_failures={}, resolver=lambda: SimpleNamespace(missing=lambda: []))
    islandeditor.CIslandEditor.texture_failure(ed, "data/x/roof.psd", ValueError("no pixels"))
    assert islandeditor.CIslandEditor.file_problems(ed) == [("data/x/roof.psd", "ValueError: no pixels", "")]
