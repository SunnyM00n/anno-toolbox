"""The rendering quality of 117 and 1800 (``render.quality``): the game's presets and area levels with the games' own
values of their quality levels, Custom kept and read back, a released settings file's names, the player's engine.ini
followed, the older games at their best, what a quality changes in the level of detail (draw distance), what makes a
game frame anew, maps uploaded from a coarser level; with an installed game (``ANNO117_DIR``, ``ANNO1800_DIR``) its
engine settings read."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from annotoolbox.render import cull, quality

LEVELS = "".join(
    f"<QualityLevel{k}><ViewDistance>{500 + k}</ViewDistance><LODDistances><LOD0>9</LOD0></LODDistances>"
    f"<MinimumObjectSizeInPixels>{20 - k}</MinimumObjectSizeInPixels><LODBias>{0.25 * (k + 1)}</LODBias>"
    f"</QualityLevel{k}>"
    for k in range(5)
)


def test_a_preset_sets_the_games_areas_and_takes_the_values_of_their_levels():
    records = quality.game_levels(LEVELS)
    assert len(records) == 5 and records[2] == {
        "ViewDistance": 502.0,
        "MinimumObjectSizeInPixels": 18.0,
        "LODBias": 0.75,
    }
    low, ultra = quality.preset("anno117", "Low", records), quality.preset("anno117", "UltraHigh", records)
    assert low.levels == (0,) * len(quality.AREAS) and ultra.levels == tuple(quality.AREAS.values())
    assert (low.level, low.lod_bias, low.min_pixels) == (0, 0.25, 20.0)
    assert (ultra.level, ultra.lod_bias, ultra.min_pixels) == (4, 1.25, 16.0)
    assert not low.tessellation and not low.ssao and ultra.view_distance == quality.INFINITE
    assert low.render_scale == ultra.render_scale == 1.0  # the game's presets keep the resolution
    high = quality.preset("anno117", "High")  # Terrain and Texture top at 2: High has them at the top already
    assert (high.area("Shadow"), high.area("Terrain"), high.area("Texture"), high.max_texture) == (2, 2, 2, 0)
    vanilla = quality.preset("anno117", "Medium")  # no file: vanilla 117's level 1
    assert (vanilla.lod_bias, vanilla.min_pixels, vanilla.ssr) == (0.5, 15.0, False)
    assert quality.preset("anno1800", "Medium", records).lod_bias == 0.5  # 1800's file has no LODBias: ours stays 1
    assert quality.preset("anno1800", "High").lod_bias == 1.0


def test_the_older_games_draw_at_their_best_and_an_unset_game_at_medium():
    assert quality.resolve("anno2070", "Low").preset == "UltraHigh"
    got = quality.resolve("anno117", None)  # the test's player has no engine.ini (conftest)
    assert got.preset == "Medium" == quality.DEFAULT and not got.is_player_setting
    assert quality.resolve("anno117", "nonsense").preset == quality.DEFAULT


def test_a_released_settings_files_presets_map_to_the_games_names_once():
    """Toolbox 0.3 kept ``low`` .. ``ultra`` and a Custom record with its ``base``: they read as the game's."""
    assert quality.resolve("anno117", "ultra").preset == "UltraHigh" and quality.map_released("medium") == "Medium"
    old = {"preset": "custom", "base": "high", "render_scale": 0.6, "shadow_size": 1024, "particles": 7}
    got = quality.resolve("anno117", old)
    high = quality.preset("anno117", "High")
    assert got.preset == quality.CUSTOM and got.levels == high.levels and got.render_scale == 0.6
    assert got.shadow_size == high.shadow_size and got.particles == high.particles  # now from the levels


def test_a_custom_quality_is_kept_and_read_back():
    levels = {**quality.preset_levels("Low"), "Shadow": 4, "Texture": 2}
    custom = replace(quality.derive("anno117", quality.CUSTOM, levels), render_scale=0.75)
    stored = quality.store(custom)
    assert stored["preset"] == quality.CUSTOM and stored["levels"]["Shadow"] == 4
    back = quality.resolve("anno117", stored)
    assert back == custom and (back.shadow_cascades, back.max_texture, back.render_scale) == (4, 0, 0.75)
    assert quality.store(quality.preset("anno117", "High")) == "High"


def test_a_hand_edited_custom_quality_keeps_the_defaults_for_what_it_cannot_read():
    stored = {"preset": quality.CUSTOM, "levels": {"Shadow": "x", "Object": 9, "Water": True}, "render_scale": "fast"}
    got = quality.resolve("anno117", stored)  # raised ValueError and TypeError: the view did not open
    medium = quality.preset("anno117", "Medium")
    assert got.area("Shadow") == medium.area("Shadow") and got.area("Object") == 4 and got.render_scale == 1.0
    assert got.area("Water") == medium.area("Water")
    assert quality.resolve("anno117", {"preset": quality.CUSTOM, "levels": None}).levels == medium.levels
    odd = "<QualityLevel0><LODBias>-</LODBias><ViewDistance>1.2.3</ViewDistance><MinimumObjectSizeInPixels>9</MinimumObjectSizeInPixels></QualityLevel0>"
    assert quality.game_levels(odd) == [{"MinimumObjectSizeInPixels": 9.0}]


PLAYER_INI = {
    "RenderEngine": {
        "QualitySetting": "UltraHigh",
        "Custom": {"Water": 3, "Lighting": 3, "Shadow": 4, "Terrain": 2, "Texture": 2, "AA": 3, "Object": 4,
                   "ViewDistance": 4, "Raytracing": 3, "FeedbackQualityLevel": 4},
    }
}  # fmt: skip


def test_without_a_setting_the_view_follows_the_players_engine_ini(tmp_path, monkeypatch):
    got = quality.resolve("anno117", None, player_text=json.dumps(PLAYER_INI))
    assert got.is_player_setting and got.preset == "UltraHigh" and got.levels == tuple(quality.AREAS.values())
    assert quality.store(got) is None  # following the game keeps nothing in the settings
    custom = {"RenderEngine": {"QualitySetting": "Custom", "Custom": {"Shadow": 1, "Object": 7}}}
    got = quality.resolve("anno117", None, player_text=json.dumps(custom))
    assert got.preset == quality.CUSTOM and (got.area("Shadow"), got.area("Object")) == (1, 4)
    assert quality.resolve("anno117", "Low", player_text=json.dumps(PLAYER_INI)).preset == "Low"  # a setting wins
    ini = tmp_path / "engine.ini"
    ini.write_text(json.dumps(PLAYER_INI), encoding="utf-8", newline="\n")
    monkeypatch.setenv(quality.PLAYER_FILE_ENV, str(ini))
    assert (
        quality.resolve("anno117", None).is_player_setting and not quality.resolve("anno1800", None).is_player_setting
    )


@pytest.mark.parametrize(
    "text",
    ["", "{", "[]", '{"RenderEngine": []}', '{"RenderEngine": {"QualitySetting": "Fast"}}', '{"RenderEngine": {}}'],
)
def test_a_players_engine_ini_that_holds_no_setting_gives_none(text):
    assert quality.player_choice(text) is None
    assert quality.player_choice('{"RenderEngine": {"QualitySetting": "High"}}') == "High"  # no Custom block


def test_levels_that_are_no_finite_number_and_a_preset_that_is_no_name_keep_the_defaults():
    """JSON reads ``Infinity`` and ``NaN``: ``int`` raised OverflowError and ValueError on them, and a list as the
    preset's name raised TypeError, so a hand-edited engine.ini or settings file stopped the view."""
    text = '{"RenderEngine": {"QualitySetting": "Custom", "Custom": {"Shadow": Infinity, "Water": NaN, "Object": 1}}}'
    got = quality.resolve("anno117", None, player_text=text)
    medium = quality.preset("anno117", "Medium")
    assert got.is_player_setting and got.area("Shadow") == medium.area("Shadow") and got.area("Object") == 1
    assert got.area("Water") == medium.area("Water")
    assert quality.resolve("anno117", {"preset": ["High"], "levels": {}}).preset == quality.CUSTOM


def test_a_quality_sets_the_level_of_detail_and_cuts_past_its_draw_distance():
    q = quality.preset("anno117", "Low")
    rules = quality.lod_rules(q)
    assert (rules.bias, rules.min_pixels, rules.far) == (q.lod_bias, q.min_pixels, q.view_distance)
    assert quality.lod_rules(None) == cull.SLodRules()
    proj = np.diag([1.0, 1.0, -1.0, 0.0])
    centres = np.array([[0.0, 0.0, -50.0], [0.0, 0.0, -2000.0]])
    near = cull.SLodRules(bias=1.0, min_pixels=0.0, far=1000.0)
    assert cull.lod_levels(centres, np.full(2, 30.0), np.identity(4), proj, (1000, 1000), near, 5)[1] == -1
    assert (
        cull.lod_levels(
            centres, np.full(2, 30.0), np.identity(4), proj, (1000, 1000), cull.SLodRules(min_pixels=0.0), 5
        )[1]
        >= 0
    )


def test_a_frame_is_made_again_only_for_what_it_was_built_with():
    medium = quality.preset("anno117", "Medium")
    assert not quality.remakes(medium, replace(medium, lod_bias=2.0, render_scale=0.5))
    assert quality.remakes(medium, replace(medium, shadow_size=1024)) and quality.remakes(None, medium)
    assert not quality.remakes(None, None)


def test_a_map_is_uploaded_from_the_level_the_quality_allows():
    pytest.importorskip("PySide6")
    from annotoolbox.models.ddsdecode import SDdsInfo
    from annotoolbox.render import gamemaps

    info = SDdsInfo(2048, 1024, 12, 1, "DXT1")
    asked = []
    dds = SimpleNamespace(m_info=info, stored=lambda level, face=0: asked.append(level) or b"")
    small = gamemaps.smaller(dds, 512)
    assert (small.m_info.width_px, small.m_info.height_px, small.m_info.mips) == (512, 256, 10)
    small.stored(0)
    assert asked == [2] and gamemaps.smaller(dds, 0) is dds and gamemaps.smaller(dds, 4096) is dds
    pool = gamemaps.CMapPool(None, 512)  # waiting for its array, the map keeps only the levels it will upload
    asked.clear()
    pool.want("a.dds", dds)
    kept = pool.m_pending["a.dds"]
    assert asked == list(range(2, 12)) and kept.m_info.width_px == 512 and len(kept.m_levels) == 10


def test_the_island_views_maps_are_halved_to_the_quality():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop.desktoprender import shadow_side, shrink

    raw = np.arange(8 * 4 * 4, dtype=np.uint8).reshape(4, 8, 4)
    size, out = shrink(raw.tobytes(), (8, 4), 4, 4)
    assert size == (4, 2) and np.frombuffer(out, np.uint8).reshape(2, 4, 4)[0, 0, 0] == round(raw[:2, :2, 0].mean())
    assert shrink(b"x", (8, 4), 4, 0) == ((8, 4), b"x")
    assert shadow_side(None) == 2048 and shadow_side(quality.preset("anno117", "Low")) == 1024


@pytest.mark.parametrize(
    "game, env, key, column",
    [("anno117", "ANNO117_DIR", "LODBias", 0), ("anno1800", "ANNO1800_DIR", "MinimumObjectSizeInPixels", 1)],
)
def test_an_installed_games_engine_settings_give_its_five_levels(game, env, key, column):
    """Installed: the values ``VANILLA`` stands in with are the game's own (117's bias, 1800's smallest size)."""
    if not os.environ.get(env):
        pytest.skip(f"needs an installed game ({env})")
    from annotoolbox.othergames import installs

    tree = installs.GAMES[game].tree(Path(os.environ[env]))
    if hasattr(tree, "read_text"):  # the archives' tree; 117's files hand out bytes
        text = tree.read_text(quality.SETTINGS_FILE[game])
    else:
        text = tree.read(quality.SETTINGS_FILE[game]).decode("utf-8", "replace")
    levels = quality.game_levels(text)
    assert len(levels) == 5 and [lv[key] for lv in levels] == [row[column] for row in quality.VANILLA[game]]
