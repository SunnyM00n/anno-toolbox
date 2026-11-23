"""Rivers and lakes by 117's inland water shader (``render.gameinland``): the ribbon and the lake as the engine's
``InlandWaterVertex``, a line's flow and look from the island file, the flow map the game's passes draw (installed
game only)."""

from dataclasses import replace

import numpy as np
import pytest

gameinland = pytest.importorskip("annotoolbox.render.gameinland")


def _vertices(mesh) -> np.ndarray:
    return np.frombuffer(mesh.m_data, gameinland.VERTEX)


def _vector(b: np.ndarray) -> np.ndarray:
    """A vertex's packed vector as the shader reads it: ``UnBias``, then ``.bgra``."""
    return (b[..., [2, 1, 0]].astype(float) / 255) * 2 - 1


def test_a_rivers_ribbon_has_both_shores_per_point_its_flags_and_the_views_mirrored_x():
    body = gameinland.SBody("River", [[10.0, 2.0, 8.0], [10.0, 2.0, 16.0], [10.0, 1.0, 24.0]], [4.0, 6.0],
                           [(1.0, 2.0, 3.0), None, (8.0, 8.0, 8.0)])  # fmt: skip
    mesh = gameinland.river_mesh(body, 32.0)
    v = _vertices(mesh)
    assert mesh.m_stride == 48 == gameinland.VERTEX.itemsize and mesh.m_count == 6 and len(mesh.m_indices) == 12
    # flowing along +z in the island's axes: the right hand is +x there, -x in the view's mirrored axes
    assert np.allclose(v["pos"][0], [32 - 8, 2, 8]) and np.allclose(v["pos"][1], [32 - 12, 2, 8])
    assert np.allclose(v["pos"][3, 0] - v["pos"][2, 0], -6.0)  # the second point's width, the last kept for the third
    assert list(v["flags"]) == [2, 3, 0, 1, 4, 5]  # right shore 1, start 2, end 4
    assert np.allclose(v["speed"][2], gameinland.MEAN_SPEEDS) and np.allclose(v["speed"][4], 8.0)
    along = 8 + np.hypot(8, 1)  # the distance along the line, its drop included
    assert np.allclose(v["uv"][:, 0], [0, 0, 8, 8, along, along]) and list(v["uv"][:, 1]) == [0, 1, 0, 1, 0, 1]
    tangent, normal, across = (_vector(v[k]) for k in ("t", "n", "b"))
    assert tangent[0, 2] > 0.99 and normal[:, 1].min() > 0.9  # along the flow, up
    assert across[1, 0] < -0.99  # from the left shore to the right one, in the view's axes
    assert np.all(mesh.m_indices < 6)


def test_a_lake_lies_flat_at_its_first_points_height():
    body = gameinland.SBody("Lake", [[0.0, 3.0, 0.0], [4.0, 5.0, 0.0], [4.0, 1.0, 4.0]], triangles=[(0, 1, 2)])
    mesh = gameinland.lake_mesh(body, 10.0)
    v = _vertices(mesh)
    assert np.allclose(v["pos"][:, 1], 3.0) and np.allclose(v["pos"][:, 0], [10, 6, 6])
    assert np.allclose(_vector(v["n"])[:, 1], 1.0, atol=0.01) and not v["flags"].any()
    assert gameinland.lake_mesh(gameinland.SBody("Lake", [[0, 0, 0]] * 3), 10.0) is None  # no triangles
    assert gameinland.river_mesh(gameinland.SBody("River", [[0, 0, 0]]), 10.0) is None  # one point


def test_a_lines_flow_and_look_come_from_the_file_while_it_matches_it():
    lines = [{"kind": "River", "points": [[0, 0, 0], [1, 0, 0]], "widths": [2, 2]},
             {"kind": "Lake", "points": [[0, 0, 0], [2, 0, 0], [2, 0, 2]], "widths": []},
             {"kind": "River", "points": [[0, 0, 0], [1, 0, 0], [2, 0, 0]], "widths": []}]  # fmt: skip
    found = [{"kind": "River", "speeds": [(1, 2, 3), (4, 5, 6)], "look": {"ViewDepth": 3.0, "RenderOrder": 2}},
             {"kind": "Lake", "speeds": [None] * 3, "look": {"ColorDeep": (0.1, 0.2, 0.3)}},
             {"kind": "River", "speeds": [(1, 1, 1)] * 2, "look": {"ViewDepth": 9.0}}]  # fmt: skip
    got = gameinland.bodies(lines, found, lambda outline: [(0, 1, 2)])
    assert got[0].speeds == [(1, 2, 3), (4, 5, 6)] and got[0].order == 2 and got[0].look == {"ViewDepth": 3.0}
    assert got[1].triangles == [(0, 1, 2)] and got[1].look["ColorDeep"] == (0.1, 0.2, 0.3)
    assert got[2].speeds == [] and got[2].look == {}  # an edited line: three points, the file's two
    values = gameinland.look_values(got[1])
    assert values["cIWColorDeep"] == [0.1, 0.2, 0.3] and values["cIWViewDepth"] == 4.0  # the lake's template
    assert gameinland.look_values(got[0])["cIWViewDepth"] == 3.0 and values["cIWWaveSpeed"] == 1.0


GLOBALS = """<Globals><LSAOMaximum>0.457</>
<DLC03_InlandWater_DrySeason><WaveSize>5</>
<WaveSpeed>1.5</>
<WaveIntensity>1</>
<ColorDeep><x>0.1</>
<y>0.2</>
<z>0.3</>
</>
<PhysicalWaveIntensity>0.6</>
<MediumWaveWeight>0.8</>
</>
<DLC03_InlandWater_FloodSeason><WaveSize>9</>
<WaveSpeed>3</>
<WaveIntensity>2</>
<ColorDeep><x>0.5</>
<y>0.6</>
<z>0.7</>
</>
<PhysicalWaveIntensity>0.4</>
<MediumWaveWeight>0.8</>
</>
<DLC03_InlandWater_TransitionPhysWaveIntensity>1.7</>
<DLC03_InlandWater_SmallWaveWindGustBoost>0.7</>
</Globals>"""


def test_a_river_flagged_for_the_global_look_takes_egypts_season_and_every_body_its_flags():
    """RenderFlags is the shader's cIWFlags (default 3); IWF_USE_GLOBAL_SHADING (0x20) takes globals.xml's season
    look in an Egypt session, blended as the engine blends it (round 12)."""
    seasons = gameinland.read_seasons(GLOBALS)
    assert seasons["Dry"]["ColorDeep"] == (0.1, 0.2, 0.3) and seasons["transition"] == 1.7 and seasons["gust"] == 0.7
    assert gameinland.read_seasons("<Globals></Globals>") is None
    mid = gameinland.season_values(seasons, 0.5)
    assert mid["cIWWaveSize"] == 5 and mid["cIWWaveSpeed"] == 1.5  # the dry season's, never blended
    assert mid["cIWWaveIntensity"] == pytest.approx(1.5) and mid["cIWColorDeep"] == pytest.approx([0.3, 0.4, 0.5])
    assert mid["cIWPhysicalWaveIntensity"] == pytest.approx(1.7)  # the transition's at the middle
    assert gameinland.season_values(seasons, 1.0)["cIWPhysicalWaveIntensity"] == pytest.approx(0.4)
    found = [{"kind": "River", "speeds": [None, None], "look": {"ViewDepth": 3.0, "RenderFlags": 35}},
             {"kind": "River", "speeds": [None, None], "look": {}}]  # fmt: skip
    line = {"kind": "River", "points": [[0, 0, 0], [1, 0, 0]], "widths": []}
    nile, plain = gameinland.bodies([line, line], found, None)
    assert nile.flags == 35 and plain.flags == gameinland.RENDER_FLAGS and "RenderFlags" not in nile.look
    egypt = gameinland.look_values(nile, seasons)
    assert egypt["cIWFlags"] == 35 and egypt["cIWWaveSize"] == 5 and egypt["cIWColorDeep"] == [0.1, 0.2, 0.3]
    assert egypt["cIWSmallWaveWindGustBoost"] == 0.7
    other = gameinland.look_values(plain, seasons)
    assert other["cIWWaveSize"] == 1.0 and other["cIWSmallWaveWindGustBoost"] == 0.7  # every body in Egypt
    rome = gameinland.look_values(nile)  # not an Egypt session: the block's own look
    assert rome["cIWViewDepth"] == 3.0 and rome["cIWWaveSize"] == 1.0 and rome["cIWSmallWaveWindGustBoost"] == 0.0


ASSETS = """<Assets>
<Asset><Template>Season</Template><Values><Standard><GUID>1</GUID><Name>DrySeason</Name></Standard>
<Season><SeasonDuration>10800000</SeasonDuration><IsRiverHeightAdjusted>1</IsRiverHeightAdjusted>
<RiverHeight>0.01</RiverHeight><RiverChangeDuration>480000</RiverChangeDuration></Season></Values></Asset>
<Asset><Template>Season</Template><Values><Standard><GUID>2</GUID><Name>FloodSeason</Name></Standard>
<Season><SeasonType>FloodSeason</SeasonType><SeasonDuration>7200000</SeasonDuration>
<IsRiverHeightAdjusted>1</IsRiverHeightAdjusted><RiverHeight>0.5</RiverHeight>
<RiverChangeDuration>480000</RiverChangeDuration></Season></Values></Asset>
<Asset><Template>SeasonPool</Template><Values><Standard><GUID>3</GUID></Standard><SeasonPool><Seasons>
<Item><Season>1</Season></Item><Item><Season>2</Season></Item></Seasons><LoopAfterLastSeason>1</LoopAfterLastSeason>
<StartSeason>1</StartSeason><StartSeasonDuration>7200000</StartSeasonDuration></SeasonPool></Values></Asset>
<Asset><Template>SeasonFeature</Template><Values><SeasonConfig><DefaultSeasonPool>3</DefaultSeasonPool>
<IsAdjustingRiverHeights>1</IsAdjustingRiverHeights></SeasonConfig></Values></Asset>
</Assets>"""


def test_egypts_season_cycle_moves_the_rivers_height_and_look_as_the_session_runs():
    """Egypt's SeasonPool (rounds 13 and 14): the dry season first for its start length (2 h), the flood 2 h, the
    dry 3 h, again; the rivers move over the change time (8 min) centred on each switch, half way to the mean of both
    heights before it; the look's factor is the height over the highest (the season manager's rule); every body takes
    the height, only the flagged rivers the look."""
    cycle = gameinland.read_cycle(ASSETS)
    hour, minute = 3_600_000, 60_000
    assert cycle.at(0) == (pytest.approx(0.02), 0.01) and cycle.at(2 * hour - 4 * minute - 1)[1] == 0.01
    f, h = cycle.at(2 * hour - 2 * minute)  # half way to the mean
    assert f == pytest.approx(0.265) and h == pytest.approx(0.1325)
    assert cycle.at(2 * hour) == (pytest.approx(0.51), pytest.approx(0.255))  # the switch: the mean
    assert cycle.at(2 * hour + 4 * minute) == (1.0, 0.5) and cycle.at(4 * hour - 4 * minute - 1)[0] == 1.0
    assert cycle.at(4 * hour + 4 * minute)[0] == pytest.approx(0.02)  # dry again
    assert cycle.at(7 * hour + 4 * minute)[0] == pytest.approx(1.0)  # the dry season's own 3 h, then the flood again
    still = gameinland.SSeasonCycle(tuple(replace(s, is_adjusted=False) for s in cycle.seasons), 0, 2 * hour)
    assert still.at(5 * hour) == (0.0, 0.01)  # no season moves the rivers: no factor
    assert gameinland.read_cycle("<Assets></Assets>") is None
    seasons = {**gameinland.read_seasons(GLOBALS), "cycle": cycle}
    line = {"kind": "River", "points": [[0, 0, 0], [1, 0, 0]], "widths": []}
    nile, plain = gameinland.bodies([line, line], [{"kind": "River", "speeds": [None, None], "look": {"RenderFlags": 35}},
                                                   {"kind": "River", "speeds": [None, None], "look": {}}], None)  # fmt: skip
    flood = gameinland.look_values(nile, seasons, (1.0, 0.5))
    assert flood["cIWHeightOffset"] == 0.5 and flood["cIWColorDeep"] == pytest.approx([0.5, 0.6, 0.7])
    assert gameinland.look_values(nile, seasons)["cIWColorDeep"] == pytest.approx([0.1, 0.2, 0.3])  # dry
    assert gameinland.look_values(plain, seasons, (1.0, 0.5))["cIWHeightOffset"] == 0.5


def test_the_flow_map_covers_the_island_at_two_texels_a_unit_up_to_its_largest_side():
    assert gameinland.flow_side(32.0) == 64 and gameinland.flow_side(4000.0) == gameinland.FLOW_MAX
    values = gameinland.flow_values(32.0, 64)
    assert (
        values["cClusterFlowTextureSize"] == [64, 64, 1 / 64, 1 / 64]
        and values["cClusterFlowRecSizeWS"] == [1 / 32] * 2
    )


INSTALL_FLOW = """
import json, sys
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.infra import dxc
from annotoolbox.models import shaderview
from annotoolbox.render import gameinland
from annotoolbox.render.rhiview import CRhiView
from annotoolbox.sources.anno117 import CAnno117Files
root = Path(sys.argv[1])
source = shaderview.CShaderSource(CAnno117Files(root), dxc.CDxc.find(anno117=root))
got = gameinland.compile_stages(source)
egypt = gameinland.compile_stages(source, True)
app = QApplication([])
# a river along +z at x 10 of a 32-unit island, 4 wide, at full speed; no session heights (all water)
body = gameinland.SBody("River", [[10.0, 2.0, 4.0], [10.0, 2.0, 16.0], [10.0, 2.0, 28.0]], [4.0] * 3, [(8.0, 8.0, 8.0)] * 3)

class View(CRhiView):
    def ready(self, ctx):
        self.inland = gameinland.CInlandWater(ctx.m_rhi, *got)
        self.inland.set_bodies([body], 32.0)
        self.egypt = gameinland.CInlandWater(ctx.m_rhi, *egypt)
        self.read = None
    def draw(self, ctx, screen, size_px):
        self.inland.run(ctx, 1.0)
        self.read = ctx.read_later(self.inland._m_targets[2])

v = View("d3d12")
for _ in range(3):
    v.picture(16, 16)
flow = np.frombuffer(bytes(v.read.data), np.uint8).reshape(64, 64, 4)
grad = v.inland.m_own["cRiverGradientsRO"].pixelSize()
print(json.dumps({"inside": flow[30:34, 42:46].reshape(-1, 4).mean(0).tolist(), "outside": int(flow[:, :30].max()),
                  "error": v.inland.m_error, "failed": v.m_failed, "gradients": [grad.width(), grad.height()],
                  "prevent": [None, *[str(x) for x in vars(v.egypt.m_prevent[1].stencil).values()]][1:]
                  if v.inland.m_prevent is None else "plain variant has one",
                  "seasons": [vars(x) for x in egypt[5]["cycle"].seasons]}))
"""


def test_the_flow_map_is_drawn_by_the_games_passes_where_the_river_runs():
    """The river's ribbon drawn from above by ``ClusterFlow``, expanded and dilated: its middle holds the flow along
    +z at full speed (alpha 1), the island elsewhere none (island-117-view.md "Phase 6", step 4)."""
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_FLOW.replace("sys.argv[1]", repr(root)))
    assert not got["error"] and not got["failed"], got
    r, g, b, a = got["inside"]  # view x 22 (texel 44), z 16 (row 32)
    # along +z, deep, at full speed (the ends' fade and the expansion's neighbours take off a little)
    assert abs(r - 127.5) < 10 and g > 230 and b > 230 and a > 230, got
    # Egypt's variant: RiverPreventOcean marks the sea's cut-out bit; the season cycle read from the asset file
    assert got["prevent"][:3] == ["ALWAYS", "REPLACE", "1"], got
    assert [(x["is_flood"], x["river_height"]) for x in got["seasons"]] == [(False, 0.01), (True, 0.5)], got
    assert got["outside"] == 0 and got["gradients"] == [512, 512], got


def test_a_river_with_the_lava_flag_draws_by_the_lava_technique_and_a_normal_one_stays_water():
    """``IWF_LAVA`` (RenderFlags bit 4) picks ``LavaRiverDefault``'s pixel shader; Cinis's rivers carry 15, 7 and 6,
    a lake never is lava; the lava river's own offset reaches the shader as a float (rendering/water.md 1.9a)."""
    found = [
        {"kind": "River", "speeds": [None, None], "look": {"RenderFlags": flags, "LavaFlowOffset": -10000}}
        for flags in (15, 7, 6, 3)
    ]
    line = {"kind": "River", "points": [[0, 0, 0], [1, 0, 0]], "widths": []}
    lava15, lava7, lava6, water = gameinland.bodies([line] * 4, found, None)
    assert [gameinland.is_lava_river(body) for body in (lava15, lava7, lava6, water)] == [True, True, True, False]
    assert not gameinland.is_lava_river(gameinland.SBody("Lake", [], flags=15))
    assert gameinland.STAGES["lava.ps"][1:] == ("LavaRiverDefault", "psRiverLava")
    assert gameinland.look_values(lava15)["cIWLavaFlowOffset"] == -10000.0  # permanent lava
    assert gameinland.look_values(water)["cIWFlags"] == 3 and gameinland.LAVA["cLavaTimer"] > 0
