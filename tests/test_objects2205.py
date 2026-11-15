"""2205's objects by its own model and prop shaders (``render.objects2205``, ``editor.desktop.desktopobjects2205``):
the words a draw's instances read, the props' rows, the frame's matrices by index, the parts' runs, and with an
installed game (``ANNO2205_DIR``) a tree prop and a model compile. docs/rendering/island-1800-2205-view.md 18."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.render import frameforward, objects1800
from annotoolbox.render import objects2205 as o


def test_a_draws_words_are_white_and_point_at_each_instances_first_matrix():
    w = o.instance_words(3, 10, bones=2)
    assert w[:, 0].tolist() == [-1] * 3 and w[:, 3].tolist() == [10, 12, 14] and not w[:, 1:3].any()


def test_a_props_rows_place_turn_and_scale_it_with_its_colour_as_floats():
    world = np.identity(4)
    world[:3, :3] = np.diag([-2.0, 3.0, 4.0])  # the view's mirror in x
    world[:3, 3] = (5, 6, 7)
    rows = o.prop_rows(world[None], np.array([[0.5, 0.25, 1.0, 1.0]]))
    assert rows[0, 0].tolist() == [5, 6, 7, 1] and np.allclose(rows[0, 2], [-2, 3, 4, 0])
    assert np.allclose(rows[0, 1], [0, 0, 0, 1]) and rows[0, 3].tolist() == [0.5, 0.25, 1.0, 1.0]
    assert o.prop_rows(world[None])[0, 3].tolist() == [1, 1, 1, 1]  # white without a stored colour


def test_the_frames_matrices_are_indexed_in_the_order_they_were_added_a_skin_each_bone():
    m = o.FrameMatrices(rhi=None)
    worlds = np.stack([np.identity(4)] * 2)
    worlds[1, 0, 3] = 9.0
    assert m.add(worlds) == 0 and m.add(worlds[1:], bones=3) == 2 and m.count == 5
    every = np.concatenate(m._parts, axis=1)
    assert np.allclose(every, frameforward.matrix_rows([worlds[0], worlds[1], worlds[1], worlds[1], worlds[1]]))
    m.begin()
    assert m.count == 0


def test_a_model_run_puts_its_words_into_a_slot_its_caster_shares():
    program = mock.MagicMock()
    program.cbuffers = {"cbModel": mock.MagicMock(size=280 * 16)}
    draw, cast = mock.MagicMock(program=program), mock.MagicMock(program=program)
    part = mock.MagicMock(transparent=False)
    run = o.Run2205(draw, cast, part, np.stack([np.identity(4)] * 2), (np.zeros((2, 3)), np.ones(2)))
    rows, matrices = mock.MagicMock(), o.FrameMatrices(rhi=None)
    matrices.add(np.stack([np.identity(4)] * 3))
    with mock.patch.object(objects1800, "place_slot", return_value=("cbModel", 512)) as slot:
        run.place(rows, matrices)
    words = slot.call_args[0][4]
    assert words[:, 3].tolist() == [3, 4] and draw.slot == ("cbModel", 512) and cast.slot == ("cbModel", 512)


def test_a_2205_part_runs_its_games_draws_and_holds_a_draws_words():
    from annotoolbox.editor.desktop import desktopobjects2205 as d

    assert d.Part2205.RUN is o.Run2205 and d.Part2205.PROP_RUN is o.PropRun2205 and d.Part2205.chunk(4) == o.CHUNK
    assert d.Objects2205.GAME == "anno2205" and o.ObjectSource2205.PROPS["Plant"][1] == "PlantDefault"


def test_the_height_map_the_grass_stands_on_is_a_texel_a_world_unit_in_the_views_axes_at_its_centres():
    heights = np.zeros((5, 5), np.float32)  # rows z, the island's x, a tile apart: 4 x 4 tiles
    heights[:, 0] = 8.0  # the island's x 0 is the view's x 4
    grid = o.terrain_heights(heights, 1.0)
    assert grid.shape == (4, 4) and grid.dtype == np.float32
    assert np.allclose(grid[:, 3], 4.0) and np.allclose(grid[:, :3], 0.0)  # the texel at x 3.5: halfway to x 4
    assert o.PROPS["Grass"][1] == o.PROPS["Pebble"][1] == "GrassDefault" and o.PROPS["Decal"][1] == "PropDefault"


def test_a_2205_part_hands_its_props_the_sessions_height_map_made_again_when_the_heights_change():
    from types import SimpleNamespace

    from annotoolbox.editor.desktop import desktopobjects2205 as d

    objs = object.__new__(d.Objects2205)
    rhi = mock.MagicMock()
    rhi.newTexture.return_value.pixelSize.return_value.width.return_value = 4
    objs.r = SimpleNamespace(ctx=SimpleNamespace(rhi=rhi, native_pass=mock.Mock(), later=mock.Mock()))
    heights = np.zeros((5, 5), np.float32)
    objs.island = SimpleNamespace(terrain=SimpleNamespace(heights=heights, step=1.0))
    objs.terrain, objs._height_key = {}, None
    objs._heights()
    objs._heights()  # the same heights: nothing made or sent again
    assert objs.terrain[o.HEIGHT_MAP] is rhi.newTexture.return_value and objs.r.ctx.native_pass.call_count == 1
    heights[2, 2] = 3.0  # an edit writes into the same array
    objs._heights()
    assert objs.r.ctx.native_pass.call_count == 2 and rhi.newTexture.call_count == 1  # the same texture, written
    part = object.__new__(d.Part2205)
    part.terrain, part._textures, part.paths = objs.terrain, {"cPropDiffuseTex": "diff"}, {}
    assert part.textures(None) == {"cPropDiffuseTex": "diff", o.HEIGHT_MAP: rhi.newTexture.return_value}


def test_installed_2205s_tree_prop_and_a_model_compile_by_its_shaders():
    folder = os.environ.get("ANNO2205_DIR")
    if not folder:
        pytest.skip("needs an installed Anno 2205 (ANNO2205_DIR)")
    from annotoolbox.editor.formats.a7m import reader as a7m_reader
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.sources import gametree

    files = gametree.open_game("anno2205", Path(folder))
    source = o.ObjectSource2205(files, Fxc.find(Path(folder)))
    text = "<Prop><Type>Plant</>\n<MeshFileName>a/tree.rdm</>\n<cAlphaRef>0.6</>\n<ENABLE_TREE_RIM_LIGHTING>1</>\n</>"
    values = a7m_reader.prop_parts(text)["parts"][0]["values"]
    fx, defines, kind = source.prop_material(values)
    vs, ps = source.effect_pass(fx, defines, kind[1])[:2]
    assert "cbProp" in {b.name for b in vs.reflection.bindings} and source.effect_pass(fx, defines, kind[3])
    model = {"ShaderID": "8", "cModelDiffTex": "a.psd"}
    assert source.has_technique(model, "Obj2Shadow") and source.colour_technique(model) == "Default"
    for kind, casts in (("Grass", False), ("Pebble", True), ("Decal", False)):  # on the height map, the decal blends
        fx, defines, row = source.prop_material({"Type": kind, "MeshFileName": "a/b.rdm"})
        vs = source.effect_pass(fx, defines, row[1])[0]
        assert (o.HEIGHT_MAP in vs.types) == (kind != "Decal")
        try:
            source.effect_pass(fx, defines, row[3])
            cast = True
        except Exception:
            cast = False
        assert cast == casts, kind
