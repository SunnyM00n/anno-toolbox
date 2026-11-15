"""Anno 1800's objects in the island view by its own model shaders (``render.objects1800``,
``editor.desktop.desktopobjects1800``): the instance words and matrices against the shaders' reads, the level of
detail by 1800's ``LODDistances``, the draws of a mesh sharing their first one's pipeline and buffers, a part's runs
cut at the words a draw holds; installed (ANNO1800_DIR): a part of a building compiled with its caster and maps.
docs/rendering/island-1800-2205-view.md section 12."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from annotoolbox.render import cull, objects1800
from annotoolbox.render import gameshader as gs


def test_the_matrix_buffers_elements_are_the_worlds_first_three_rows_as_its_float4x3_packs_them():
    rng = np.random.default_rng(1)
    worlds = rng.normal(size=(3, 4, 4)).astype(np.float32)
    worlds[:, 3] = [0, 0, 0, 1]
    got = objects1800.matrix_elements(worlds, bones=2)
    assert got.shape == (6, 12)
    for k, w in enumerate(worlds):  # the viewer's own write of one matrix (``ForwardModel.fill_model``: m.T)
        packed = np.frombuffer(gs._pack(w.T, "float4x3", False), np.float32)
        assert np.allclose(got[2 * k], packed) and np.allclose(got[2 * k + 1], packed)


def test_the_instance_words_point_at_each_instances_first_matrix_and_the_casters_carry_their_cascade():
    plain = objects1800.words(3, bones=4)
    assert plain[:, 3].tolist() == [0, 4, 8]
    assert set(plain[:, 0].tolist()) == {objects1800.WHITE} and set(plain[:, 1].tolist()) == {1 << 7}
    cast = objects1800.words(2, cascades=4)
    assert len(cast) == 8 and (cast[:, 0] >> 30).tolist() == [0, 1, 2, 3, 0, 1, 2, 3]
    assert ((cast[:, 0] & 0x3FFFFFFF) == (objects1800.WHITE & 0x3FFFFFFF)).all()
    assert cast[:, 3].tolist() == [0, 0, 0, 0, 1, 1, 1, 1]  # model_base.fxh GetWorldMtxIndex: w


def test_a_draw_holds_at_most_280_words_and_the_matrices_its_instances_take():
    assert objects1800.chunk_size(1) == 280
    assert objects1800.chunk_size(30) == 1024 // 30
    assert objects1800.chunk_size(2000) == 1


SETTINGS = """<Engine><QualityLevel0><MinimumObjectSizeInPixels>12</MinimumObjectSizeInPixels><LODDistances>
<LOD0>0</LOD0><LOD1>0</LOD1><LOD2>40</LOD2><LOD3>110</LOD3><LOD4>375</LOD4></LODDistances></QualityLevel0>
<QualityLevel1><LODDistances><LOD0>0</LOD0><LOD1>20</LOD1><LOD2>40</LOD2><LOD3>130</LOD3><LOD4>425</LOD4>
</LODDistances></QualityLevel1></Engine>"""


def test_1800s_level_of_detail_goes_by_its_lod_distances_and_drops_the_too_small():
    tables = objects1800.lod_tables(SETTINGS)
    assert tables == [(0, 0, 40, 110, 375), (0, 20, 40, 130, 425)]
    view, proj = np.identity(4), np.array([[1.5, 0, 0, 0], [0, 2.0, 0, 0], [0, 0, -1, -0.2], [0, 0, -1, 0]])
    centres = np.array([[0, 0, -10.0], [0, 0, -30.0], [0, 0, -60.0], [0, 0, -500.0], [0, 0, -100.0]])
    radii = np.array([5.0, 5.0, 5.0, 5.0, 0.001])
    rules = cull.LodRules(bias=1.0)
    got = objects1800.distance_levels(centres, radii, view, proj, (800, 600), rules, tables[1], 5)
    assert got.tolist() == [0, 1, 2, 4, -1]
    low = objects1800.distance_levels(centres, radii, view, proj, (800, 600), rules, tables[0], 5)
    assert low.tolist()[:3] == [1, 1, 2]  # the lowest level never draws LOD0
    plain = objects1800.distance_levels(centres, radii, view, proj, (800, 600), rules, (), 5)
    assert plain.tolist() == [0, 0, 0, 0, -1]


def test_the_casters_words_are_declared_at_the_most_a_constant_buffer_holds():
    source = object.__new__(objects1800.ObjectSource)
    text = b"shared global_table StructuredBuffer<uint4> cModelGPUCulledInstanceData : register(t6);\nfloat x;"
    with mock.patch("annotoolbox.models.shaderforward.ForwardSource._read", return_value=text):
        got = source._read("data/shaders/include/constants_model.fxh").decode()
    assert "cbuffer cb_cModelGPUCulledInstanceData { uint4 cModelGPUCulledInstanceData[4096]; };" in got
    assert "StructuredBuffer" not in got and got.endswith("float x;")


def _reflection(name: str, size: int = 64):
    return SimpleNamespace(name=name, size=size, variables=[SimpleNamespace(name="x", offset=0)])


def _program():
    program = mock.MagicMock()
    program.cbuffers = {n: _reflection(n) for n in ("cbPerFrame", "cbMaterialModelCommon", "cbModel")}
    program.numbers = {}
    return program


def _draw(program, lead=None, shared=None):
    rhi = mock.MagicMock()
    rhi.newBuffer.side_effect = lambda *a: mock.MagicMock(name="buffer")
    ctx = SimpleNamespace(rhi=rhi, later=lambda r: None)
    binding = objects1800.Binding(shared)
    draw = objects1800.ObjectDraw(ctx, program, mock.MagicMock(), mock.MagicMock(), None, binding, lead)
    draw._make_srb = mock.MagicMock()
    draw._vertex_resources = mock.MagicMock(side_effect=lambda batch: draw.keep.append("vertices"))
    draw._make_pipeline = mock.MagicMock(side_effect=lambda rp, sig: setattr(draw, "pipeline", "pipeline"))
    draw.vbufs, draw.ibuf, draw.attrs, draw.fill_stride = ["v"], "i", [], 4
    return draw


def test_the_frames_buffers_are_filled_once_a_frame_for_every_draw_and_a_parts_material_once():
    shared = objects1800.SharedBuffers(mock.MagicMock())
    program, filled = _program(), []
    first, second = _draw(program, shared=shared), _draw(program, shared=shared)
    for _frame in range(2):
        shared.begin()
        for d in (first, second):
            d.prepare("rp", ((1,), 1), mock.MagicMock(), {}, lambda c: filled.append(sorted(c)))
    # frame 1: the frame's buffer by the first draw, each part's material once, each draw's instances
    assert filled == [["cbPerFrame"], ["cbMaterialModelCommon"], ["cbModel"], ["cbMaterialModelCommon"], ["cbModel"],
                      ["cbPerFrame"], ["cbModel"], ["cbModel"]]  # fmt: skip
    assert first.buffers["cbPerFrame"] is second.buffers["cbPerFrame"]
    assert first.buffers["cbMaterialModelCommon"] is not second.buffers["cbMaterialModelCommon"]


def test_a_further_draw_of_a_mesh_draws_through_its_first_and_writes_only_its_instances():
    shared = objects1800.SharedBuffers(mock.MagicMock())
    program, filled = _program(), []
    lead = _draw(program, shared=shared)
    shared.begin()
    lead.prepare("rp", ((1,), 1), mock.MagicMock(), {}, lambda c: None)
    chunk = _draw(program, lead=lead)
    chunk.prepare("rp", ((1,), 1), mock.MagicMock(), {}, lambda c: filled.append(sorted(c)))
    assert chunk.pipeline == "pipeline" and chunk.vbufs is lead.vbufs and not chunk._vertex_resources.called
    for name in ("cbPerFrame", "cbMaterialModelCommon"):
        assert chunk.buffers[name] is lead.buffers[name]
    assert chunk.buffers["cbModel"] is not lead.buffers["cbModel"] and filled == [["cbModel"]]


def test_a_further_draw_waits_while_its_first_draw_is_not_made():
    program = _program()
    lead = _draw(program, shared=objects1800.SharedBuffers(mock.MagicMock()))
    chunk = _draw(program, lead=lead)
    chunk.prepare("rp", ((1,), 1), mock.MagicMock(), {}, lambda c: pytest.fail("filled"))
    assert chunk.pipeline is None  # not drawn this frame, not a failure


def test_a_parts_instances_are_cut_into_runs_of_the_words_a_draw_holds():
    from annotoolbox.editor.desktop import desktopobjects1800 as d

    part = d.Part1800.__new__(d.Part1800)
    mesh = SimpleNamespace()
    draws = mock.MagicMock()
    draws.take.side_effect = lambda: mock.MagicMock()
    part.got = SimpleNamespace(levels=[[(mesh, 1)]], prop=False)
    part.draws, part.transparent, part.runs = {id(mesh): (draws, draws, draws)}, False, []
    worlds = np.tile(np.identity(4, np.float32), (600, 1, 1))
    spheres = (np.zeros((600, 3)), np.ones(600))
    runs, cast = part.models(0, worlds, True, spheres)
    assert [len(r.worlds) for r in runs] == [280, 280, 40] and cast == []
    assert all(r.shadow is not None and r.shadow.run is r for r in runs)
    assert all(r.prepass is not None and r.prepass.instances == len(r.worlds) for r in runs)  # the pre-pass's
    assert part.all() == runs


def test_a_cast_draws_a_copy_per_instance_and_cascade_and_the_frames_values_go_into_the_frames_buffers_only():
    draw, part = mock.MagicMock(), mock.MagicMock()
    part.textures.return_value = {"cModelDiffTex": "diffuse"}
    run = mock.MagicMock(worlds=np.zeros((10, 4, 4)), part=part)
    cast = objects1800.Cast(draw, run)
    cast.instances = 3  # the frame's cUsedShadowPartitions
    frame_fill = mock.MagicMock()
    cast.prepare("rp", "sig", "batch", {}, frame_fill)
    assert draw.instances == 30 and draw.prepare.call_args[0][3] == {"cModelDiffTex": "diffuse"}
    own = draw.prepare.call_args[0][4]
    own({"cbShadow": "frame"})
    own({"cbModel": "mine"})
    frame_fill.assert_called_once_with({"cbShadow": "frame"})
    run.fill_caster.assert_called_once_with({"cbModel": "mine"})


def test_an_1800_island_looks_objects_are_drawn_by_1800s_model_shaders_and_a_2205_sessions_by_2205s():
    from annotoolbox.editor.desktop import desktopgame1800, desktopgame2205

    def made(cls):
        island = object.__new__(cls)
        island.r = mock.MagicMock()
        return island._objects()

    assert type(made(desktopgame1800.GameIsland1800)).__name__ == "Objects1800"
    assert type(made(desktopgame2205.GameIsland2205)).__name__ == "Objects2205"


def test_the_terrains_nodes_bind_the_frames_buffers_shared_and_own_only_their_node_and_cascade():
    from annotoolbox.render import terrain1800

    t = object.__new__(terrain1800.Terrain1800)
    t.ctx, t.dummies, t.shared = SimpleNamespace(rhi=mock.MagicMock()), None, None
    program = _program()
    assert type(t._draw(program, mock.MagicMock(), None, 0)) is objects1800.FxcDraw  # no owner's buffers: its own
    t.shared = (objects1800.SharedBuffers(mock.MagicMock()), objects1800.SharedBuffers(mock.MagicMock()))
    colour, cast = t._draw(program, mock.MagicMock(), None, 0), t._draw(program, mock.MagicMock(), None, 1)
    assert colour.shared is t.shared[0] and colour.own == ("cbTerrainNode",)
    assert cast.shared is t.shared[1] and cast.own == ("cbTerrainNode", "cbShadowPartition")  # its cascade


def test_a_terrain_nodes_frame_values_go_into_the_frames_buffers_only():
    from annotoolbox.render import terrain1800

    draw = mock.MagicMock()
    node = terrain1800.TerrainNode(draw, {"cTrnChunkLevelParams": 3}, {})
    with mock.patch.object(terrain1800.frameforward, "fill_frame") as fill_frame:
        node.prepare("rp", "sig", "batch", "cam", {})
        fill = draw.prepare.call_args[0][4]
        node_buffer, frame_buffer = mock.MagicMock(), mock.MagicMock()
        fill({"cbTerrainNode": node_buffer})
        fill_frame.assert_not_called()
        fill({"cbPerFrame": frame_buffer})
        fill_frame.assert_called_once()


def test_a_buildings_part_compiles_by_1800s_model_shaders_with_its_caster_levels_and_maps():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from annotoolbox.editor import modelparts
    from annotoolbox.editor.desktop import desktopobjects1800 as d
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.islands.islandview import Files
    from annotoolbox.othergames.installs import GAMES

    files = Files(GameFiles(GAMES["anno1800"].index(Path(root), None)))
    compiler = Fxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    cfg = "data/graphics/buildings/3rd_party/3rd_party_06/3rd_party_06_platform_01_var06.cfg"
    part = modelparts.model_parts(files.read(cfg).decode("utf-8", "replace"))["parts"][0]
    got = d.compile_part(objects1800.ObjectSource(files, compiler), files, part)
    assert got.shadow is not None and got.levels and got.levels[0][0][1] == 1
    assert got.prepass is not None and got.prepass[1].depth_write  # an opaque part's normals and depth
    assert {"cModelDiffTex", "cModelNormalTex"} <= set(got.maps)
    casters = next(cb for cb in got.shadow[0].reflection.cbuffers if cb.name == objects1800.CASTERS)
    assert [(v.name, v.elements) for v in casters.variables] == [("cModelGPUCulledInstanceData", 4096)]


def test_a_casters_instances_go_only_into_the_cascades_their_spheres_reach():
    near = np.diag([0.1, 0.1, 0.1, 1.0])  # clip xy = world xz / 10 (row vectors): x and y of the world
    far = near.copy()
    far[3, 0] = -3.0  # the second cascade lies three clip units to the side
    centres, radii = np.array([[0.0, 0.0, 0.0], [30.0, 0.0, 0.0], [9.0, 0.0, 0.0]]), np.array([1.0, 1.0, 5.0])
    pairs = objects1800.cascade_pairs(centres, radii, [near, far])
    assert pairs.tolist() == [[0, 0], [2, 0], [1, 1]]  # the third reaches the first cascade by its radius
    rows = objects1800.pair_words(3, 1, pairs)
    assert (rows[:, 0] >> 30).tolist() == [0, 0, 1] and rows[:, 3].tolist() == [0, 2, 1]
    run = mock.MagicMock(worlds=np.zeros((3, 4, 4)), centres=centres, radii=radii)
    cast = objects1800.Cast(mock.MagicMock(), run)
    cast.instances = 2
    objects1800.cull_casts([cast], [near, far])
    assert cast.count() == 3  # three pairs, not 3 instances x 2 cascades
    cast.instances = 1  # the frame reads one cascade: the pairs of the second left out
    assert cast.count() == 2


def _turned(rows: np.ndarray, p: np.ndarray) -> np.ndarray:
    """A point of a prop's own space placed as ``vsPlant`` does: ``RotateWithQuaternion(p * scale, q) + position``."""
    q, s, t = rows[1].astype(float), rows[2, :3].astype(float), rows[0, :3].astype(float)
    p = p * s
    return p + 2 * np.cross(q[:3], np.cross(q[:3], p) + q[3] * p) + t


def test_a_props_instance_data_places_it_as_its_world_also_where_the_views_mirror_turns_it_over():
    rng = np.random.default_rng(3)
    for mirrored in (False, True):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        x, y, z, w = q
        turn = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])  # fmt: skip
        world = np.identity(4)
        world[:3, :3] = np.diag([-1.0 if mirrored else 1.0, 1, 1]) @ turn @ np.diag([0.5, 1.2, 2.0])
        world[:3, 3] = (100.0, 3.0, -40.0)
        rows = objects1800.prop_rows(world[None])[0]
        for p in rng.normal(size=(5, 3)):
            assert np.allclose(_turned(rows, p), world[:3, :3] @ p + world[:3, 3], atol=1e-3)
        words = rows.view(np.uint32)
        assert words[2, 3] == objects1800.NO_VISIBILITY_CHECK and words[3, 0] == objects1800.PROP_WHITE
        assert rows[0, 3] == 1.0 and rows[3, 2] == 1.0  # shown, its shadow not fading


def test_a_props_casters_carry_their_cascade_in_the_colour_words_low_bits():
    worlds = np.tile(np.identity(4, np.float32), (2, 1, 1))
    rows = objects1800.prop_caster_rows(objects1800.prop_rows(worlds), np.array([[0, 0], [1, 3], [0, 2]]))
    words = rows.view(np.uint32)[:, 3, 0]
    assert (words & 3).tolist() == [0, 3, 2] and ((words >> 2) == (objects1800.PROP_WHITE >> 2)).all()


def test_the_props_casters_are_declared_at_the_most_a_constant_buffer_holds():
    source = object.__new__(objects1800.ObjectSource)
    text = b"shared global_table StructuredBuffer<ProGPUCulledInstData> cPropGPUCulledInstanceData : register(t27);\n"
    with mock.patch("annotoolbox.models.shaderforward.ForwardSource._read", return_value=text):
        got = source._read("data/shaders/include/constants_prop.fxh").decode()
    assert "ProGPUCulledInstData cPropGPUCulledInstanceData[1024]; };" in got and "StructuredBuffer" not in got


def test_a_prp_part_compiles_by_its_types_prop_shader_and_its_runs_hold_the_props_data():
    from annotoolbox.editor.desktop import desktopobjects1800 as d
    from annotoolbox.editor.formats.a7m import reader as a7m_reader

    text = "<Prop><Type>Plant</>\n<MeshFileName>a/tree.rdm</>\n<DIFFUSE_ENABLED>1</>\n<cAlphaRef>0.3</>\n</>"
    part = a7m_reader.prop_parts(text)["parts"][0]
    assert part["values"]["Type"] == "Plant" and part["mesh"] == "a/tree.rdm"
    source = mock.MagicMock()
    source.prop_material.return_value = ("fx", (("DIFFUSE_ENABLED", ""),), objects1800.PROPS["Plant"])
    source.technique_state.return_value = d.block_state({})
    with mock.patch.object(d, "_geometry", return_value=([[("mesh", 1)]], ((0, 0, 0), 1.0))):
        got = d.compile_part(source, mock.MagicMock(), part)
    assert got.prop and got.prepass is not None and not source.model_pass.called
    assert [c.args[2] for c in source.effect_pass.call_args_list] == ["PlantDefault", "PlantShadow",
                                                                      "PlantNormalsDepth"]  # fmt: skip
    assert ("PREZ_NORMAL_RT_ENABLED", "1") in source.effect_pass.call_args_list[2].args[1]  # its normals
    assert source.technique_state.call_args_list[0].args[2:] == ("PlantDefault", 1)  # without the depth pre-pass
    part1800 = d.Part1800.__new__(d.Part1800)
    draws = mock.MagicMock()
    draws.take.side_effect = lambda: mock.MagicMock()
    part1800.got, part1800.transparent, part1800.runs = got, False, []
    part1800.draws = {id("mesh"): (draws, draws, None)}
    with mock.patch.object(part1800, "_draws", return_value=(draws, draws, None)):
        runs, _cast = part1800.models(0, np.tile(np.identity(4, np.float32), (300, 1, 1)), True,
                                      (np.zeros((300, 3)), np.ones(300)))  # fmt: skip
    assert [len(r.worlds) for r in runs] == [256, 44] and all(isinstance(r, objects1800.PropRun) for r in runs)


def _var(name: str, offset: int, type_: str, elements: int = 0, size: int = 0):
    return SimpleNamespace(name=name, offset=offset, type=type_, elements=elements, size=size, members=[],
                           row_major=False)  # fmt: skip


def _cbuffer(name: str, *variables):
    size = max(v.offset + max(v.size, 4) for v in variables)
    return SimpleNamespace(name=name, size=(size + 15) // 16 * 16, variables=list(variables))


CB_PROP = _cbuffer("cbProp", _var("cPropInstanceData", 0, "float4", 2048, 2048 * 16),
                   _var("cPropFadeType", 32768, "float", size=4))  # fmt: skip
CB_PROP_CASTERS = _cbuffer(objects1800.PROP_CASTERS, _var("cPropGPUCulledInstanceData", 0, "ProGPUCulledInstData",
                                                          1024, 1024 * 64))  # fmt: skip
CB_MODEL = _cbuffer("cbModel", _var("cModelInstanceData", 0, "int4", 280, 280 * 16))
CB_MATRICES = _cbuffer(objects1800.frameforward.MATRIX_BUFFER, _var("cModelMatrixBuffer", 0, "float4x3", 1024,
                                                                    1024 * 48))  # fmt: skip
CB_CASTERS = _cbuffer(objects1800.CASTERS, _var("cModelGPUCulledInstanceData", 0, "uint4", 4096, 4096 * 16))


def _rows(dynamic: bool = True) -> objects1800.FrameRows:
    from PySide6.QtGui import QRhi

    rhi = mock.MagicMock()
    rhi.backend.return_value = QRhi.Implementation.D3D12 if dynamic else QRhi.Implementation.D3D11
    rhi.newBuffer.side_effect = lambda _t, _u, size: mock.MagicMock(size=mock.MagicMock(return_value=size))
    return objects1800.FrameRows(SimpleNamespace(rhi=rhi, later=lambda r: None))


def _placed(rows, batch, name: str) -> bytes:
    """What the frame's upload wrote into the buffer ``name``."""
    buf = rows.buffers[name]
    return next(c.args[2] for c in batch.updateDynamicBuffer.call_args_list if c.args[0] is buf)


def test_the_frames_rows_go_into_one_buffer_a_name_each_draw_at_its_aligned_offset_sent_once():
    rows = _rows()
    assert rows.dynamic and not _rows(False).dynamic  # a dynamic offset on Direct3D 12 only (section 16)
    first = rows.place("cbProp", np.full(300, 1, np.uint8), 33000)
    second = rows.place("cbProp", np.full(10, 2, np.uint8), 33000)
    assert (first, second) == (0, 512)
    batch = mock.MagicMock()
    rows.upload(batch)
    rows.upload(batch)  # once a frame
    data = np.frombuffer(_placed(rows, batch, "cbProp"), np.uint8)
    assert batch.updateDynamicBuffer.call_count == 1 and len(data) == 768
    assert (data[:300] == 1).all() and (data[300:512] == 0).all() and (data[512:522] == 2).all()
    assert rows.buffers["cbProp"].size() >= 768 + 33024  # the last draw's binding reaches past its rows


def test_a_prop_runs_rows_and_fade_go_into_its_slot_and_its_casters_rows_into_theirs():
    rows = _rows()
    colour = SimpleNamespace(program=SimpleNamespace(cbuffers={"cbProp": CB_PROP}), instances=0)
    caster = SimpleNamespace(program=SimpleNamespace(cbuffers={"cbProp": CB_PROP, objects1800.PROP_CASTERS:
                                                               CB_PROP_CASTERS}), instances=0)  # fmt: skip
    worlds = np.tile(np.identity(4, np.float32), (3, 1, 1))
    worlds[:, :3, 3] = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    part = SimpleNamespace(transparent=False)
    run = objects1800.PropRun(colour, caster, part, worlds, (worlds[:, :3, 3], np.ones(3)))
    run.shadow.pairs = np.array([[0, 0], [2, 0], [1, 1]])
    prez = SimpleNamespace(program=colour.program, instances=0)
    run.with_prepass(prez)
    objects1800.place_runs(rows, [run], 1)
    batch = mock.MagicMock()
    rows.upload(batch)
    assert colour.slot == prez.slot == ("cbProp", 0) and colour.fixed == {} and colour.own_rows == {}
    data = np.frombuffer(_placed(rows, batch, "cbProp"), np.uint8)
    assert data[: 3 * 64].tobytes() == objects1800.prop_rows(worlds).tobytes()  # words in it: compared as bytes
    assert data[32768:32772].view(np.float32)[0] == 1.0  # shown, not fading
    assert caster.slot == (objects1800.PROP_CASTERS, 0) and set(caster.fixed) == {"cbProp"}
    cast = np.frombuffer(_placed(rows, batch, objects1800.PROP_CASTERS), np.uint8).view(np.float32).reshape(-1, 4, 4)
    assert np.allclose(cast[:2, 0, :3], [[1, 2, 3], [7, 8, 9]]) and not cast[2:].any()  # the pairs of cascade 0


def test_each_prop_takes_the_islands_stored_colour_as_its_colour_word():
    words = objects1800.prop_words(np.array([[1.0, 1.0, 1.0, 1.0], [0.75, 0.5, 0.0, 1.0], [3.0, 1.0, 1.0, 0.5]]))
    assert words.tolist() == [objects1800.PROP_WHITE, 0xFF604000, 0x80FF8080]  # white is the shader's 1, clamped
    rows = _rows()
    colour = SimpleNamespace(program=SimpleNamespace(cbuffers={"cbProp": CB_PROP}), instances=0)
    worlds = np.tile(np.identity(4, np.float32), (2, 1, 1))
    run = objects1800.PropRun(colour, None, SimpleNamespace(transparent=False), worlds, (worlds[:, :3, 3], np.ones(2)))
    run.colours = words[1:]
    objects1800.place_runs(rows, [run], 1)
    batch = mock.MagicMock()
    rows.upload(batch)
    data = np.frombuffer(_placed(rows, batch, "cbProp"), np.uint8)[: 2 * 64].view(np.uint32).reshape(2, 4, 4)
    assert data[:, 3, 0].tolist() == words[1:].tolist()  # GetPropInstanceData's colour word


def test_a_model_runs_draws_share_one_matrix_slot_its_words_are_fixed_and_its_casters_words_its_own():
    rows = _rows()
    names = {"cbModel": CB_MODEL, objects1800.frameforward.MATRIX_BUFFER: CB_MATRICES}
    colour, prez = (SimpleNamespace(program=SimpleNamespace(cbuffers=dict(names)), instances=0) for _ in range(2))
    caster = SimpleNamespace(program=SimpleNamespace(cbuffers={**names, objects1800.CASTERS: CB_CASTERS}),
                             instances=0)  # fmt: skip
    worlds = np.tile(np.identity(4, np.float32), (2, 1, 1))
    run = objects1800.Run(colour, caster, SimpleNamespace(transparent=False), worlds, (np.zeros((2, 3)), np.ones(2)),
                          bones=3)  # fmt: skip
    run.with_prepass(prez)
    objects1800.place_runs(rows, [run], 2)  # no cull: each instance into both cascades
    assert colour.slot == prez.slot == caster.slot == (objects1800.frameforward.MATRIX_BUFFER, 0)
    assert colour.fixed["cbModel"][0] == ("words", 3) and caster.fixed["cbModel"][0] == ("words", 3)
    got = np.frombuffer(caster.own_rows[objects1800.CASTERS], np.uint32).reshape(-1, 4)
    assert (got[:, 0] >> 30).tolist() == [0, 0, 1, 1] and got[:, 3].tolist() == [0, 3, 0, 3]
    run.shadow.instances = 2  # the frame's cUsedShadowPartitions
    assert run.shadow.count() == 4


def test_every_casts_pairs_are_found_at_once_as_each_cast_finds_its_own():
    rng = np.random.default_rng(5)
    matrices = [np.diag([0.1, 0.1, 0.1, 1.0]), np.diag([0.02, 0.02, 0.02, 1.0])]
    casts, alone = [], []
    for n in (4, 0, 7):
        run = SimpleNamespace(centres=rng.normal(scale=15, size=(n, 3)), radii=rng.uniform(0.5, 3, n),
                              worlds=np.zeros((n, 4, 4)))  # fmt: skip
        casts.append(objects1800.Cast(mock.MagicMock(), run))
        alone.append(objects1800.cascade_pairs(run.centres, run.radii, matrices))
    objects1800.cull_casts(casts, matrices)
    assert [c.pairs.tolist() for c in casts] == [a.tolist() for a in alone]


def test_a_further_draw_with_the_frames_rows_draws_by_its_leads_set_or_at_its_own_offset():
    for dynamic in (True, False):
        rows, program = _rows(dynamic), _program()
        program.cbuffers = {"cbPerFrame": _reflection("cbPerFrame"), "cbProp": CB_PROP}
        shared = objects1800.SharedBuffers(mock.MagicMock())
        lead = _draw(program, shared=shared)
        lead.rows = rows
        lead.slot = ("cbProp", rows.place("cbProp", np.zeros(64, np.uint8), CB_PROP.size))
        chunk = _draw(program, lead=lead)
        chunk.slot = ("cbProp", rows.place("cbProp", np.zeros(64, np.uint8), CB_PROP.size))
        shared.begin()
        batch = mock.MagicMock()
        lead.prepare("rp", ((1,), 1), batch, {}, lambda c: None)
        lead.srb = "lead's set"
        chunk.prepare("rp", ((1,), 1), batch, {}, lambda c: None)
        assert lead.buffers["cbProp"] is rows.buffers["cbProp"] and chunk.pipeline == "pipeline"
        assert (chunk.srb == "lead's set") is dynamic  # Direct3D 11: a set of its own at its offset
        assert chunk._make_srb.called is not dynamic and "cbProp" not in chunk.constants


def test_a_still_frame_places_no_rows_again():
    from annotoolbox.editor.desktop import desktopobjects1800 as d

    objects = d.Objects1800.__new__(d.Objects1800)
    objects.device = SimpleNamespace(rows=_rows())
    drawn = SimpleNamespace(gbuffer=[], transparent=[])
    with mock.patch.object(objects1800, "place_runs") as place:
        objects.place(drawn, 2, (b"m",))
        objects.place(drawn, 2, (b"m",))
        objects.place(drawn, 2, (b"n",))  # the cascades moved
    assert place.call_count == 2


def test_a_tree_prop_compiles_by_1800s_plant_shader_with_its_caster():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from annotoolbox.editor.desktop import desktopobjects1800 as d
    from annotoolbox.editor.formats.a7m import reader as a7m_reader
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.islands.islandview import Files
    from annotoolbox.othergames.installs import GAMES

    files = Files(a7m_reader.GameFiles(GAMES["anno1800"].index(Path(root), None)))
    compiler = Fxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    prp = "data/graphics/props/terrain_props/vegetation/trees/moderate/tree_moderate_02_var0.prp"
    part = a7m_reader.prop_parts(files.read(prp).decode("utf-8", "replace"))["parts"][0]
    got = d.compile_part(objects1800.ObjectSource(files, compiler), files, part)
    assert got.prop and got.shadow is not None and "cPropDiffuseTex" in got.maps
    names = {cb.name for cb in got.colour[0].reflection.cbuffers}
    assert "cbProp" in names
    casters = next(cb for cb in got.shadow[0].reflection.cbuffers if cb.name == objects1800.PROP_CASTERS)
    assert [(v.name, v.elements) for v in casters.variables] == [("cPropGPUCulledInstanceData", 1024)]
