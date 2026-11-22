"""Anno 1800's and 2205's ground by their own bake (``render.bake1800``, the looks ``desktopgame1800`` and
``desktopgame2205``): the stored albedo's encode, the normal turned for the view's mirror, a node's values and its
splats drawn in the set's order through the first one's draw (2205 over its clear), the looks baking with the four
maps the games read; installed (ANNO1800_DIR / ANNO2205_DIR): the techniques compile with what they bind.
docs/rendering/island-1800-2205-view.md section 13."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from annotoolbox.render import bake1800, objects1800, terrain1800, terrainbake, terraindraw
from annotoolbox.render.terrainnodes import SNode

R1800, R2205 = bake1800.RECIPES["anno1800"], bake1800.RECIPES["anno2205"]


def test_the_games_own_bake_is_encoded_from_gamma_the_views_from_linear():
    stored, view = terrain1800.encode_fs(linear=False), terrain1800.ENCODE_FS
    assert "pow(" not in stored and "1.0 / 2.2" in view  # the splats draw the maps' gamma colour as stored
    for word in ("0.299", "0.169", "0.419", "sqrt", "vec4(root(cb), y, root(cr)"):
        assert word in stored and word in view
    assert R1800.encodes["encoded"] == ("albedo", stored) == R2205.encodes["encoded"]


def test_the_baked_normal_is_turned_where_the_views_mirror_turns_the_terrains_tangent_frame():
    # 1800: the island matrix mirrors tangent and normal, their cross product (the bitangent) turns: v negated
    assert "1.0 - n.g" in R1800.encodes["flipped"][1] and R1800.encodes["flipped"][0] == "normal"
    assert R1800.stores == {"cBakedDiffTexArray": "encoded", "cBakedNormalTexArray": "flipped"}
    # 2205: the mirrored patch turns the normal's x, not the tangent: u negated; x and y a texture each, from red
    assert "1.0 - n.r" in R2205.encodes["normal_x"][1] and "n.g" in R2205.encodes["normal_y"][1]
    assert R2205.stores == {"cBakedDiffTex": "encoded", "cBakedNormalXTex": "normal_x", "cBakedNormalYTex": "normal_y"}
    assert R1800.targets[-1] == R2205.targets[-1] == "normal" and R2205.clear_pass == "TerrainClear"


def _ground() -> terrainbake.SGroundSource:
    ids = np.full((4, 4), 3 | 7 << 5, np.uint32)  # materials 3 and 7 everywhere
    alphas = np.full((8, 8), 31 | 31 << 5, np.uint32)
    splats = [terrainbake.SSplat(7, 2.0, 5.0), terrainbake.SSplat(1, 4.0, 1.0), terrainbake.SSplat(3, 8.0, 4.0)]
    return terrainbake.SGroundSource(splats, ids, alphas, 4.0)


def test_a_nodes_values_carry_its_page_for_the_displacement_and_each_splat_its_id_tiling_and_sharpness():
    island = terraindraw.island_values(4.0)
    plain = bake1800.bake_values(SNode(2, 0.0, 0.0), _ground(), island)
    assert plain["cTrnBakeDisplacementEnabled"] == 0 and plain["cTrnMatIDBlockSize"] == 2
    assert plain["cTrnBakeMatTexelSizeWS"] == 0.5 and plain["cTrnIslandSize"] == [4.0, 4.0] == plain["cTrnSize"]
    assert plain["cTrnBakeChunkPos"] == [0.0, 0.0]  # 2205: the vertices hold the session's positions
    paged = bake1800.bake_values(SNode(2, 0.0, 0.0), _ground(), island, (0.25, 0.5, 0.125))
    assert paged["cTrnBakeDisplacementEnabled"] == 1 and paged["cTrnGritNormalParams"] == [0.25, 0.5, 0.125]
    assert bake1800.splat_data(terrainbake.SSplat(7, 2.0, 5.0)) == {"cTrnBakeSplatData": [7.0, 2.0, 5.0, 0.0]}


class FakeDraw:
    """An ``ObjectDraw`` that keeps what it was made with and handed."""

    made: list = []

    def __init__(self, ctx, program, mesh, state, dummies, binding=None, lead=None):
        self.m_ctx, self.m_program, self.m_mesh, self.m_state, self.m_dummies = ctx, program, mesh, state, dummies
        b = binding or bake1800.SBinding()
        self.m_lead, self.m_shared, self.m_own, self.m_fallbacks, self.prepared = (
            lead,
            b.shared,
            b.own,
            dict(b.fallbacks),
            [],
        )
        self.m_rows, self.m_is_mirrored = b.rows, b.is_mirrored
        FakeDraw.m_made.append(self)

    def prepare(self, render_pass, signature, batch, textures, fill):
        values = {}
        with mock.patch.object(bake1800.gs, "set_values", lambda cs, v: values.update(v)):
            fill({"cbTerrainBaking": SimpleNamespace(values=None)})
        self.prepared.append((textures, values))

    def draw(self, command_buffer, size_px):
        command_buffer.append(self)


def _bake(clearing=None, recipe=R1800, grit=None, textures=None) -> SimpleNamespace:
    extra = {"clear": clearing} if clearing else {}
    extra["after"] = ("after", None)
    return SimpleNamespace(m_ctx=None, m_program="splat", m_state=None, m_dummies=None, m_shared="shared", m_size_texels=64,
                           m_ground=bake1800.with_extras(_ground(), grit=grit), m_recipe=recipe, m_extra=extra,
                           m_textures=textures or {"cMaterialIDTex": "ids"},
                           m_maps={7: {"cDiffuseTexture": "rock"}, 3: {"cDiffuseTexture": "sand"}})  # fmt: skip


def test_a_node_draws_the_materials_it_holds_in_the_sets_order_each_through_the_first_draw():
    FakeDraw.m_made = []
    with mock.patch.object(objects1800, "CObjectDraw", FakeDraw):
        job = bake1800.CBake.job(_bake(), SNode(2, 0.0, 0.0), "mesh", terraindraw.island_values(4.0))
        job.prepare("rp", ((4,), 1), "batch")
        job.prepare("rp", ((4,), 1), "batch")  # a frame later: the same draws
    made = FakeDraw.m_made
    assert [s.mat_id for s in job.m_splats] == [7, 3]  # the set's order, the absent material 1 left out
    assert len(made) == 2 and made[0].m_lead is None and made[0].m_shared == "shared" and made[1].m_lead is made[0]
    assert made[0].m_own == bake1800.OWN and made[0].m_fallbacks == bake1800.MISSING
    textures, values = made[1].prepared[0]
    assert textures == {"cMaterialIDTex": "ids", "cDiffuseTexture": "sand"}
    assert values["cTrnBakeSplatData"] == [3.0, 8.0, 4.0, 0.0] and values["cTrnBakeDisplacementEnabled"] == 0


def test_2205s_node_clears_its_targets_by_its_own_pass_before_the_splats():
    FakeDraw.m_made = []
    with mock.patch.object(objects1800, "CObjectDraw", FakeDraw):
        bake = _bake(("clear", "state"), R2205)
        job = bake1800.CBake.job(bake, SNode(2, 0.0, 0.0), "mesh", terraindraw.island_values(4.0))
        job.prepare("rp", ((3,), 1), "batch")
    drawn = []
    job.draw(drawn, (64, 64))
    assert [d.m_program for d in drawn] == ["clear", "splat", "splat"] and drawn[0].m_lead is None
    assert drawn[1].m_lead is None and drawn[2].m_lead is drawn[1]  # the splats' lead is the first splat, not the clear


def test_1800s_grit_materials_leave_the_splats_and_draw_over_them_with_their_slope_and_the_baked_normal():
    set_text = (
        "<MaterialSet>\n<Material><ID>7</>\n<GritEnabled>0</>\n<GritDirection>1.0</>\n</>\n"
        "<Material><ID>3</>\n<Type>0</>\n<GritEnabled>1</>\n<GritDirection>0.100000</>\n"
        "<GritSlopeThreshold>0.500000</>\n</>\n"
    )
    grit = bake1800.read_grit(set_text)
    assert grit == {3: (0.1, 0.5)}
    FakeDraw.m_made = []
    with mock.patch.object(objects1800, "CObjectDraw", FakeDraw):
        job = bake1800.CBake.job(_bake(grit=grit), SNode(2, 0.0, 0.0), "mesh", terraindraw.island_values(4.0))
        job.prepare("rp", ((4,), 1), "batch")
        items = job.after_items("normal")
        job.prepare_after("rp", ((3,), 1), "batch", items)
    assert [s.mat_id for s in job.m_splats] == [7] and [s.mat_id for s in job.m_grits] == [3]
    ((textures, values),) = [d.prepared[0] for d in FakeDraw.m_made if d.m_program == "after"]
    assert values["cTrnBakeGritData"] == [0.1, 0.5] and values["cTrnBakeSplatData"] == [3.0, 8.0, 4.0, 0.0]
    assert textures["cGritBakedNormalTexture"] == "normal" and textures["cDiffuseTexture"] == "sand"
    assert R1800.after == bake1800.GRIT and R1800.after_targets == ("albedo", "height", "gloss")


def test_2205s_tint_draws_once_over_the_splats_and_only_with_the_sessions_tint_map():
    plain = bake1800.CBake.job(
        _bake(recipe=R2205, grit={3: (1.0, 0.0)}), SNode(2, 0.0, 0.0), "mesh", terraindraw.island_values(4.0)
    )
    assert [s.mat_id for s in plain.m_splats] == [7, 3] and plain.after_items("normal") == []  # 2205 has no grit
    tinted = _bake(recipe=R2205, textures={"cMaterialIDTex": "ids", bake1800.TINT_MAP: "tint"})
    ((values, textures),) = bake1800.CBake.job(
        tinted, SNode(2, 0.0, 0.0), "mesh", terraindraw.island_values(4.0)
    ).after_items("normal")
    assert textures[bake1800.TINT_MAP] == "tint" and "cGritBakedNormalTexture" not in textures
    assert R2205.after == bake1800.TINT and R2205.after_targets == ("albedo",)
    # the tint's states: twice source times destination by the blend, no depth or stencil in the bake's targets
    from annotoolbox.models.fxstage import SState

    flat = bake1800.flat_state(SState(depth_test=True, stencil=True, cull="back", blend=True, source="dst_color"))
    assert not flat.depth_test and not flat.stencil and flat.cull == "none" and flat.source == "dst_color"


def test_the_sessions_tint_map_is_read_as_bc1_blocks_of_its_width():
    from annotoolbox.render import terrain2205

    def tag(children):
        return SimpleNamespace(find=lambda name: children.get(name))

    raw = bytes(range(8)) * 16  # 16 x 16 texels: 16 blocks of 8 bytes
    rd = SimpleNamespace(path=lambda p: tag({"Tints": SimpleNamespace(data=raw),
                                             "TintMapWidth": SimpleNamespace(u32=lambda: 16)}))  # fmt: skip
    assert terrain2205.tint_map(rd) == (16, raw)
    short = SimpleNamespace(path=lambda p: tag({"Tints": SimpleNamespace(data=raw[:8]),
                                                "TintMapWidth": SimpleNamespace(u32=lambda: 16)}))  # fmt: skip
    assert terrain2205.tint_map(short) is None and terrain2205.tint_map(SimpleNamespace(path=lambda p: None)) is None


def test_every_mip_level_of_the_stored_targets_goes_to_the_nodes_place():
    assert bake1800.levels(256) == 9 and bake1800.levels(1) == 1
    assert len(R1800.mips) == len(R1800.stores) and len(R2205.mips) == len(R2205.stores)
    assert R1800.mips[0] == R2205.mips[0] == "cMipGenDiffuseTexture" and R2205.atlas * 2 == R1800.atlas
    stored = list(R1800.stores.values())
    lane = SimpleNamespace(level_textures=lambda k: {n: SimpleNamespace(native=(n, k)) for n in stored})
    bake = SimpleNamespace(
        m_recipe=R1800, m_size_texels=8, m_levels=4, m_destination=None, m_arrays={n: n for n in R1800.stores}
    )
    with mock.patch.object(bake1800, "_copies") as copies:
        bake1800.CBake._store(bake, "ctx", lane, 5)
    items = copies.call_args[0][1]
    assert len(items) == 8 and items[0] == ("cBakedDiffTexArray", ("encoded", 0), 8, 5, 0)
    assert items[-1] == ("cBakedNormalTexArray", ("flipped", 3), 1, 5, 3)


def test_1800s_look_bakes_with_the_four_maps_its_bake_reads():
    from annotoolbox.editor.desktop import desktopgame1800

    look = object.__new__(desktopgame1800.CGameIsland1800)
    view = SimpleNamespace(material_set="set.xml", files=SimpleNamespace(read=mock.Mock(side_effect=KeyError)))
    look.m_renderer = SimpleNamespace(m_editor=SimpleNamespace(m_view=view))
    with mock.patch.object(desktopgame1800, "island_ground", return_value=_ground()) as ground:
        (got,) = look._ground()
    ground.assert_called_once_with(look.m_renderer.m_editor, bake1800.MAP_KINDS)
    assert isinstance(got, bake1800.SGround) and got.grit == {} and got.tint is None
    assert bake1800.MAP_KINDS == ("Diffuse", "Height", "Spec", "Normal")
    look.m_renderer, look._m_bake_stages, look.m_quality = (
        SimpleNamespace(m_ctx="ctx"),
        {"vs": 1},
        SimpleNamespace(max_texture=512),
    )
    with mock.patch.object(bake1800, "CBake") as made:
        look._new_bake("ground", 128)
    made.assert_called_once_with("ctx", {"vs": 1}, "ground", 128, max_texture=512)


def test_1800s_look_hands_the_shown_objects_baked_decals_to_its_bake_and_the_views_layer_leaves_them_out():
    from annotoolbox.editor.desktop import desktopframe, desktopgame1800

    crack, drawn = _decal(), _decal("Terrain", "1")
    batch = SimpleNamespace(
        m_count=1, m_parts=[], m_mats=np.array([np.identity(4)]), m_key=("objects", "crack.cfg"), m_version=1
    )  # a config of decals alone: no parts, laid all the same
    library = SimpleNamespace(decals=lambda cfg: [crack, drawn], parts=lambda cfg: [])
    loader = SimpleNamespace(get=lambda job, make: make(), forget=mock.Mock())
    look = object.__new__(desktopgame1800.CGameIsland1800)
    look.m_renderer = SimpleNamespace(m_library=library, m_editor=SimpleNamespace(m_files="files"), m_loader=loader,
                             m_session=SimpleNamespace(m_size_tiles=64.0), _all_batches=lambda: [batch],
                             _layer_on=lambda b, opts: True, baked_decal=None)  # fmt: skip
    look.m_bake = SimpleNamespace(m_extra={"decal": 1}, set_decals=mock.Mock(return_value=[(1.0, 2.0, 3.0, 4.0)]))
    look.m_terrain, look._m_decal_key, look.GAME = SimpleNamespace(m_cache=mock.Mock()), None, "anno1800"
    with mock.patch("annotoolbox.editor.desktop.desktopobjects1800.part_maps",
                    side_effect=lambda files, maps, game: {"map": (maps["map"], "dds:" + maps["map"])}):  # fmt: skip
        look._bake_decals(None)
        look._bake_decals(None)  # nothing moved: gathered once
    (found, maps), _kw = look.m_bake.set_decals.call_args
    assert look.m_bake.set_decals.call_count == 1 and len(found) == 1 and found[0].maps["cDiffuseTexture"] in maps
    look.m_terrain.m_cache.spoil.assert_called_once_with([1.0, 2.0, 3.0, 4.0])
    assert look.m_renderer.m_baked_decal is bake1800.baked
    view = SimpleNamespace(m_library=library, m_baked_decal=bake1800.baked, _all_batches=lambda: [batch], _street_cells=lambda: [],
                           _layer_on=lambda b, opts: True, _m_ground_version=0, m_decals=mock.Mock(), m_prog={"model": 0},
                           m_session=SimpleNamespace(m_heights=np.zeros((3, 3)), m_step_tiles=1.0, m_width_samples=3))  # fmt: skip
    batch.m_parts = ["part"]
    desktopframe.CFramePasses._lay_decals(view, SimpleNamespace(game_heights=None), None)
    placed = view.m_decals.update.call_args[0][1]
    assert [d for _m, ds in placed for d in ds] == [drawn]


def test_2205s_look_bakes_into_each_nodes_textures_with_the_session_unturned():
    from annotoolbox.editor.desktop.desktopgame2205 import CGameIsland2205

    look = object.__new__(CGameIsland2205)
    look.m_renderer, look._m_bake_stages, look.m_quality = SimpleNamespace(m_ctx="ctx"), {"vs": 1}, None
    look.m_terrain = SimpleNamespace(m_size_tiles=64.0, m_island={"cTrnUnitScale": 0.25}, slot_textures=lambda k: {"k": k},
                                   use_levels=mock.Mock())  # fmt: skip
    with mock.patch.object(bake1800, "CBake") as made:
        made.return_value.m_levels = 7
        look._new_bake("ground", 128)
    args, kw = made.call_args
    assert args == ("ctx", {"vs": 1}, "ground", terraindraw.SLICE_SIZE) and kw["destination"](5) == ({"k": 5}, 0)
    look.m_terrain.use_levels.assert_called_once_with(7)  # the node textures with every level the bake makes
    island = look._bake_island()
    assert island["cTrnIslandMatrix0"] == [1.0, 0.0, 0.0, 0.0] and island["cTrnUnitScale"] == 0.25


@pytest.mark.parametrize(("game", "variable"), [("anno1800", "ANNO1800_DIR"), ("anno2205", "ANNO2205_DIR")])
def test_the_games_splat_bake_compiles_with_the_install(game, variable):
    import os
    from pathlib import Path

    root = os.environ.get(variable)
    if not root:
        pytest.skip(f"needs an installed {game} ({variable})")
    from annotoolbox.editor.formats.a7m.reader import CGameFiles
    from annotoolbox.infra.fxc import CFxc
    from annotoolbox.islands.islandview import CFiles
    from annotoolbox.othergames.installs import GAMES

    compiler = CFxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    files = CFiles(CGameFiles(GAMES[game].index(Path(root), None)))
    got = bake1800.compile_stages(files, compiler, game)
    assert got["state"].blend and got["state"].cull == "none" and not got["state"].depth_test
    want = {"cDiffuseTexture", "cSpecTexture", "cNormalTexture", "cMaterialIDTex", "cMaterialAlphaTex"}
    assert want <= set(got["ps"].types) and "cHeightTexture" not in got["ps"].types  # no BAKE_HEIGHT_MAP
    names = {cb.name: {v.name for v in cb.variables} for cb in got["ps"].reflection.cbuffers}
    assert "cTrnBakeSplatData" in names["cbTerrainBaking"]
    assert ("clear" in got) == (game == "anno2205")
    after = set(got["after"]["ps"].types)  # 1800's grit splat reads the splats' normal, 2205's tint its map
    assert ("cGritBakedNormalTexture" in after) == (game == "anno1800")
    assert (bake1800.TINT_MAP in after) == (game == "anno2205")
    assert set(bake1800.RECIPES[game].mips) <= set(got["mip"]["ps"].types)  # the mip pass's sources by name
    if game == "anno1800":  # the config decals' bake: no vertex input, its maps and values by the shader's names
        decal = got["decal"]
        assert not [i for i in decal["vs"].inputs if not i.semantic.startswith("SV_")]
        assert {"cDiffuseTexture", "cNormalTexture"} <= set(decal["ps"].types)
        assert "cTrnDecalWorldMtx" in {v.name for cb in decal["vs"].reflection.cbuffers for v in cb.variables}
    else:
        assert "decal" not in got


def _decal(kind: str = "Terrain-Baked", shader: str = "2", **values) -> dict:
    v = {"ShaderID": shader, "NORMAL_ENABLED": "1", "HEIGHT_MAP_ENABLED": "0", "cDiffuseOpacity": "0.5",
         "cGlossinessFactor": "0.25", "cHeightFactor": "0.0", "cDiffuseColor.r": "1.0", "cDiffuseColor.g": "0.5",
         "cDiffuseColor.b": "1.0", **values}  # fmt: skip
    return {"kind": kind, "values": v, "diffuse": "crack_diff_0.dds", "normal": "crack_norm_0.dds",
            "extents": (2.0, 1.0), "matrix": np.identity(4)}  # fmt: skip


def test_only_the_terrain_baked_decals_of_the_terrain_decal_material_are_baked_with_its_values_and_maps():
    assert bake1800.baked(_decal()) and not bake1800.baked(_decal("Terrain", "1"))
    assert not bake1800.baked(_decal(shader="1")) and bake1800.baked(_decal(" terrain-baked "))
    values, maps = bake1800.decal_material(_decal())
    assert values == {"cTrnDecalMaterialFlags": bake1800.BAKE_NORMAL, "cTrnDecalDiffuseOpacity": 0.5,
                      "cTrnDecalDiffuseColor": [1.0, 0.5, 1.0], "cTrnDecalGlossinessFactor": 0.25,
                      "cTrnDecalHeightFactor": 0.0}  # fmt: skip
    assert maps == {"cDiffuseTexture": "crack_diff_0.dds", "cNormalTexture": "crack_norm_0.dds"}
    values, maps = bake1800.decal_material(_decal(NORMAL_ENABLED="0", HEIGHT_MAP_ENABLED="1", cHeightMap="h.png"))
    assert values["cTrnDecalMaterialFlags"] == bake1800.BAKE_HEIGHT and maps["cHeightTexture"] == "h.png"


def test_a_placed_decal_covers_its_extents_in_the_islands_tiles_and_lands_where_the_vertex_shader_puts_it():
    place = np.identity(4)
    place[:3, 3] = (30.0, 0.0, 10.0)  # the view's axes: the island's x is 64 - 30 = 34
    hidden = np.zeros((4, 4))
    (d,) = bake1800.baked_decals([(np.array([place, hidden]), [_decal(), _decal("Terrain", "1")])], 64.0)
    assert d.box == (32.0, 9.0, 36.0, 11.0)  # half sizes 2 and 1
    node = SNode(3, 32.0, 8.0)
    assert bake1800.node_decals([d], node) == [d] and bake1800.node_decals([d], SNode(3, 40.0, 8.0)) == []
    v = bake1800.decal_values(d, node)
    assert v["cTrnDecalNodePosIS"] == [32.0, 8.0] and v["cTrnDecalNodeSizeIS"] == 8.0
    # vsBakeDecal: the corner (0.5, 0, 0.5) by the world (row vectors), into the island by its inverse, onto the node
    inv = bake1800.bake_values(node, _ground(), terraindraw.island_values(64.0))
    corner = np.array([0.5, 0.0, 0.5, 1.0]) @ v["cTrnDecalWorldMtx"]
    m0, m1 = np.array(inv["cTrnIslandMatrixInv0"][:3]), np.array(inv["cTrnIslandMatrixInv1"][:3])
    island = (m0[0] * corner[0] + m0[1] * corner[2] + m0[2], m1[0] * corner[0] + m1[1] * corner[2] + m1[2])
    assert np.allclose(island, (32.0, 11.0))  # the view's +x is the island's -x
    assert np.allclose((np.array(island) - v["cTrnDecalNodePosIS"]) / 8.0, (0.0, 0.375))


def test_a_node_draws_its_decals_over_one_quad_once_their_masks_are_on_the_device_and_moves_spoil_their_boxes():
    place = np.identity(4)
    place[:3, 3] = (30.0, 0.0, 10.0)
    decals = bake1800.baked_decals([(np.array([place]), [_decal()])], 64.0)
    bake = _bake()
    bake.m_extra["decal"] = ("decal", None)
    bake.m_decals, bake.m_decal_maps, bake._m_decal_files = [], {}, {}
    assert bake1800.CBake.set_decals(bake, decals, {"crack_diff_0.dds": "dds"}) == [decals[0].box]
    assert bake._m_decal_files == {"crack_diff_0.dds": "dds"}
    FakeDraw.m_made = []
    with mock.patch.object(objects1800, "CObjectDraw", FakeDraw):
        job = bake1800.CBake.job(bake, SNode(3, 32.0, 8.0), "mesh", terraindraw.island_values(64.0))
        assert job.m_decals == decals and job.decal_items() == []  # its mask not up yet: not drawn
        bake.m_decal_maps = {"crack_diff_0.dds": "diff"}
        job.prepare_decals("rp", ((4,), 1), "batch")
    (made,) = FakeDraw.m_made
    assert made.m_mesh is bake1800.QUAD and made.m_program == "decal"
    textures, values = made.prepared[0]
    assert textures["cDiffuseTexture"] == "diff" and "cNormalTexture" not in textures
    assert values["cTrnDecalNodePosIS"] == [32.0, 8.0] and values["cTrnIslandMatrixInv0"]  # the node's values beside
    assert bake1800.CBake.set_decals(bake, list(decals), {}) == []  # the same decals: nothing to bake again
    moved = bake1800.baked_decals([(np.array([np.identity(4)]), [_decal()])], 64.0)
    assert sorted(bake1800.CBake.set_decals(bake, moved, {})) == sorted([decals[0].box, moved[0].box])
