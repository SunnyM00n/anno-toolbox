"""Anno 1701's coast waves (``render.coast1701``; shader-compile.md 13 "The coast and wake waves"): the strips the
engine builds from a coast line, the clocks, the flat and steep kinds, the island's lines with their edits
(``desktopsea1701.coast_lines``); the passes compile from the installed effects (with ``ANNO1701_DIR``)."""

from __future__ import annotations

import math
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.othergames.isd1701 import TILE, SLine1701
from annotoolbox.render import coast1701, ocean1701


def test_a_strip_steps_off_the_line_by_its_width_and_lies_a_unit_above_it():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
    rows = coast1701.strip(points, [3.0, 4.0, 5.0])
    assert rows.shape == (6, 6)
    off, on = rows[0::2], rows[1::2]
    assert np.allclose(on[:, :3], points + [0.0, 1.0, 0.0])  # v 1 on the line
    # the offset is width x (-dz, 0, dx) / |d|: along +x it is +z, so the vertex off the line lies at -z
    assert np.allclose(off[:, :3], points + np.array([[0, 1, -3], [0, 1, -4], [0, 1, -5]]))
    assert np.allclose(off[:, 5], 0.0) and np.allclose(on[:, 5], 1.0)
    assert np.allclose(rows[:, 3], [0, 0, 1, 1, 0, 0])  # three points or more: the ends fade


def test_a_line_of_one_point_has_a_strip_without_a_direction():
    rows = coast1701.strip(np.array([[5.0, 2.0, 7.0]]), [3.0])
    assert rows.shape == (2, 6) and np.allclose(rows[:, :3], [[5.0, 3.0, 7.0]] * 2)  # lifted, not stepped off
    assert coast1701.offsets(np.zeros((0, 3)), []).shape == (0, 3)


def test_the_running_length_is_measured_as_the_engine_sums_it():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    rows = coast1701.strip(points, [2.0, 2.0])
    # the off-line edge from the last point plus its offset, the on-line one with the lift as a height difference
    edge_a = math.sqrt(10.0**2 + 1.0 + 4.0**2)
    edge_b = math.sqrt(10.0**2 + 1.0)
    assert rows[2, 4] == pytest.approx((0.5 * edge_a + 0.5 * edge_b) * coast1701.U_SCALE)
    assert rows[0, 4] == 0.0
    assert np.allclose(rows[:, 3], 1.0)  # two points: no fade


def test_a_point_between_takes_the_sum_of_its_two_unit_segments():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 0.0, 10.0]])
    off = coast1701.offsets(points, [1.0, 1.0, 1.0])
    d = np.array([1.0, 0.0, 1.0]) / math.sqrt(2.0)
    assert np.allclose(off[1], [-d[2], 0.0, d[0]])
    assert np.allclose(coast1701.offsets(np.zeros((2, 3)), [1.0, 1.0]), 0.0)  # no direction, no step


def test_the_clocks_are_the_fractions_the_engine_truncates():
    assert coast1701.clocks(0.0, False) == [0.0, 0.0, 0.0, 0.0]
    flat = coast1701.clocks(30.0, False)
    assert flat == pytest.approx([(0.045 * 30) % 1, 0.03, (0.05 * 30) % 1, (0.025 * 30) % 1])
    steep = coast1701.clocks(3.0, True)
    sway = 0.075 * math.sin(3.75)
    assert steep[3] == pytest.approx(sway - math.trunc(sway)) and steep[3] < 0  # a negative sine stays negative


def test_the_lines_split_into_flat_and_steep_meshes():
    flat = coast1701.SCoastLine(np.array([[0.0, 0, 0], [5, 0, 0], [9, 0, 2]]), np.ones(3), 0)
    steep = coast1701.SCoastLine(np.array([[0.0, 0, 0], [5, 0, 0]]), np.ones(2), coast1701.STEEP)
    meshes = coast1701.strips([flat, steep, coast1701.SCoastLine(np.zeros((1, 3)), np.ones(1), 0)])
    assert meshes["flat"].m_count == 6 and len(meshes["flat"].m_indices) == 3 * 4
    assert meshes["steep"].m_count == 4 and meshes["steep"].m_stride == coast1701.STRIDE
    alpha = np.frombuffer(meshes["flat"].m_data, np.uint8).reshape(6, coast1701.STRIDE)[:, 15]
    assert list(alpha) == [0, 0, 255, 255, 0, 0]
    assert coast1701.strips([])["flat"] is None


def test_the_islands_lines_take_the_edits_points_and_the_files_widths():
    from annotoolbox.editor.desktop.desktopsea1701 import coast_lines

    shipped = SLine1701(1, 0, 1, [(10.0, 0.0, 10.0), (12.0, 0.0, 10.0)], [2.0, 3.0])
    short = SLine1701(2, 0, 0, [(1.0, 0.0, 1.0)], [1.0])  # one point: the session leaves it out
    second = SLine1701(3, 0, 0, [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)], [1.0, 1.0])
    isl = SimpleNamespace(coasts=lambda: [shipped, short, second])
    edits = {"0": {"points": [[10.0, 10.0], [11.9, 10.0], [13.0, 10.0]]}}
    net = {"coasts": [{"points": [[10.0, 10.0], [12.0, 10.0]]}, {"points": [[0.0, 0.0], [1.0, 0.0]]}]}
    session = SimpleNamespace(m_water_net=net)
    session.m_path = SimpleNamespace(coast=lambda k: edits.get(str(k)) or net["coasts"][k])
    lines = coast_lines(session, isl)
    assert len(lines) == 2 and lines[0].variation == 1 and lines[1].variation == 0
    assert np.allclose(lines[0].points, [[320, 0, 320], [380.8, 0, 320], [416, 0, 320]])
    assert np.allclose(lines[0].widths, np.array([2.0, 3.0, 3.0]) * TILE)  # each point its nearest's width
    assert len(coast_lines(session, None)) == 2  # without the file: the common width


def test_the_installed_coast_effects_compile():
    root = os.environ.get("ANNO1701_DIR")
    if not root or not Path(root).is_dir():
        pytest.skip("ANNO1701_DIR is not set")
    from annotoolbox.infra import fxc
    from annotoolbox.models import shader1701
    from annotoolbox.sources import gametree

    try:
        compiler = fxc.CFxc.find(None, library="d3dcompiler_43")
    except Exception as e:  # no compiler on this machine
        pytest.skip(str(e))
    tree = gametree.open_game("anno1701", Path(root))
    shaders = shader1701.CSource1701(tree, compiler)
    for kind, stage in coast1701.STAGES.items():
        got = shaders.effect_pass(*stage)
        names = {b.name for b in got.pixel_stage.reflection.bindings}
        assert names >= set(coast1701.BINDINGS[kind]), kind
        assert got.state.blend and not got.state.depth_test  # the block CoastDynamicWave
    disp = shaders.effect_pass(*ocean1701.WAVE_STAGES["disp_waves"])
    assert {b.name for b in disp.pixel_stage.reflection.bindings} >= {"samplerTex1", "samplerTex3"}
    for path in coast1701.TEXTURES.values():
        assert tree.exists(path), path
