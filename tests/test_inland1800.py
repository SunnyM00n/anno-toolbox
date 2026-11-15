"""1800's and 2205's rivers and lakes by their own inland water shader (``render.inland1800``): a body's
``cbInlandWater`` (its colour from the island file, the engine's turbidity and flags, the flow map's place), the file's
packed colour read, and with an installed game (``ANNO1800_DIR``, ``ANNO2205_DIR``) the passes compile.
docs/rendering/island-1800-2205-view.md section 18."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands.islandview import inland_water
from annotoolbox.render import gameinland
from annotoolbox.render import inland1800 as il


def test_1800s_river_takes_its_files_colour_the_engines_turbidity_its_flags_and_the_flow_maps_place():
    body = gameinland.Body("River", [[0, 1, 0], [0, 1, 8]], look={"WaterColor": (3, 28, 50)}, flags=2)
    v = il.look_values("anno1800", body, 512.0)
    assert v["cInlandWaterColor"] == [3 / 255, 28 / 255, 50 / 255] and v["cInlandWaterFlags"] == 2
    assert v["cInlandWaterTurbidity"] == 1.0 and v["cInlandWaterHeightOffset"] == 0.0  # the component's default
    side = gameinland.flow_side(512.0)
    assert (
        v["cClusterFlowTextureSize"] == [side, side, 1 / side, 1 / side] and v["cClusterFlowRecSizeWS"] == [1 / 512] * 2
    )
    plain = il.look_values("anno1800", gameinland.Body("River", []), 512.0)
    assert plain["cInlandWaterColor"] == [c / 255 for c in il.GAMES["anno1800"].colour]  # 0xff0a5596, the default


def test_2205s_river_modulates_the_seas_colour_and_has_no_flow_map():
    v = il.look_values("anno2205", gameinland.Body("Lake", []), 512.0)
    assert v == {"cInlandWaterColor": [1.0, 1.0, 1.0], "cInlandWaterSwampIndex": 0.0}
    assert not il.GAMES["anno2205"].flow and il.GAMES["anno1800"].river == "RiverDefault#1"


def test_the_files_packed_colour_is_read_as_its_red_green_and_blue_bytes():
    """1800 stores a river's ``WaterColor`` as a D3D colour word (bytes b, g, r, a): not a float."""
    curve = Tag("BezierCurve", [Tag("", [Attrib("p", bytes(12)), Attrib("u0", struct.pack("<3f", 1, 2, 3))])])
    river = Tag("River", [Attrib("WaterColor", bytes([50, 28, 3, 255])), Attrib("RenderFlags", struct.pack("<i", 2))])
    found = inland_water(Tag("EditorObject", [river, Tag("BezierPath", [Tag("Path", [curve])])]))
    assert found["look"] == {"WaterColor": (3, 28, 50), "RenderFlags": 2} and found["speeds"] == [(1.0, 2.0, 3.0)]


@pytest.mark.parametrize("game", ["anno1800", "anno2205"])
def test_the_installed_games_inland_water_passes_compile(game):
    from fakes import INSTALL_VARS

    folder = os.environ.get(INSTALL_VARS[game])
    if not folder:
        pytest.skip(f"needs an installed {game} ({INSTALL_VARS[game]})")
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.render.terrain1800 import TerrainSource
    from annotoolbox.sources import gametree

    files = gametree.open_game(game, Path(folder))
    stages = il.compile_stages(TerrainSource(files, Fxc.find(Path(folder)), game), game)
    vs, ps = stages["river"][0][:2]
    assert {b.name for b in ps.reflection.bindings} >= {"cbInlandWater", "cWaterInfoMap"}
    assert stages["lake"][1] is not None and ("raster" in stages) == il.GAMES[game].flow
    if game == "anno1800":
        assert {"expand", "dilate"} <= set(stages) and "cWaterDuckweedMap" in stages["maps"]


def test_the_maps_go_up_once_and_2205_makes_no_flow_map():
    from types import SimpleNamespace

    inland = object.__new__(il.Inland)
    passes = []
    ctx = SimpleNamespace(native_pass=passes.append)
    inland.game, inland.files, inland.programs, inland.stages = "anno2205", {}, {}, {}
    inland.own, inland._made, inland._targets, inland._raster, inland._models = {}, [], [], [], []
    inland.size, inland._flow_due, inland._ready = 64.0, True, False
    inland.run(ctx)
    inland.run(ctx)
    assert len(passes) == 1 and inland._targets == [] and not inland._flow_due
