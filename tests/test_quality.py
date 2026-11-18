"""The rendering quality of 117 and 1800 (``render.quality``): the presets with the games' own values of their
quality levels, Custom kept and read back, the older games at their best, what a quality changes in the level of
detail (draw distance), what makes a game frame anew, maps uploaded from a coarser level; with an installed game
(``ANNO117_DIR``, ``ANNO1800_DIR``) its engine settings read."""

from __future__ import annotations

import os
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


def test_a_preset_takes_the_games_values_of_its_level():
    levels = quality.game_levels(LEVELS)
    assert len(levels) == 5 and levels[2] == {"ViewDistance": 502.0, "MinimumObjectSizeInPixels": 18.0, "LODBias": 0.75}
    low, ultra = quality.preset("anno117", "low", levels), quality.preset("anno117", "ultra", levels)
    assert (low.level, low.lod_bias, low.min_pixels) == (0, 0.25, 20.0) and (ultra.lod_bias, ultra.min_pixels) == (
        1.25,
        16.0,
    )
    assert low.render_scale < 1 and not low.tessellation and ultra.view_distance == quality.INFINITE
    vanilla = quality.preset("anno117", "medium")  # no file: vanilla 117's level 1
    assert (vanilla.lod_bias, vanilla.min_pixels) == (0.5, 15.0)
    assert quality.preset("anno1800", "medium", levels).lod_bias == 0.5  # 1800's file has no LODBias: ours stays 1
    assert quality.preset("anno1800", "high").lod_bias == 1.0


def test_the_older_games_draw_at_their_best_and_an_unset_game_at_medium():
    assert quality.resolve("anno2070", "low").preset == "ultra"
    assert quality.resolve("anno117", None).preset == "medium" == quality.DEFAULT
    assert quality.resolve("anno117", "nonsense").preset == "ultra"  # not a preset: the best (an old setting)


def test_a_custom_quality_is_kept_and_read_back():
    custom = quality.preset("anno117", "low").with_values(shadow_size=1024, particles=100)
    stored = quality.store(custom, base="low")
    assert stored["preset"] == quality.CUSTOM and stored["base"] == "low"
    back = quality.resolve("anno117", stored)
    assert back == custom and quality.store(quality.preset("anno117", "high")) == "high"


def test_a_quality_sets_the_level_of_detail_and_cuts_past_its_draw_distance():
    q = quality.preset("anno117", "low")
    rules = quality.lod_rules(q)
    assert (rules.bias, rules.min_pixels, rules.far) == (q.lod_bias, q.min_pixels, q.view_distance)
    assert quality.lod_rules(None) == cull.LodRules()
    proj = np.diag([1.0, 1.0, -1.0, 0.0])
    centres = np.array([[0.0, 0.0, -50.0], [0.0, 0.0, -2000.0]])
    near = cull.LodRules(bias=1.0, min_pixels=0.0, far=1000.0)
    assert cull.lod_levels(centres, np.full(2, 30.0), np.identity(4), proj, (1000, 1000), near, 5)[1] == -1
    assert (
        cull.lod_levels(
            centres, np.full(2, 30.0), np.identity(4), proj, (1000, 1000), cull.LodRules(min_pixels=0.0), 5
        )[1]
        >= 0
    )


def test_a_frame_is_made_again_only_for_what_it_was_built_with():
    medium = quality.preset("anno117", "medium")
    assert not quality.remakes(medium, medium.with_values(lod_bias=2.0, render_scale=0.5))
    assert quality.remakes(medium, medium.with_values(shadow_size=1024)) and quality.remakes(None, medium)
    assert not quality.remakes(None, None)


def test_a_map_is_uploaded_from_the_level_the_quality_allows():
    pytest.importorskip("PySide6")
    from annotoolbox.models.ddsdecode import DdsInfo
    from annotoolbox.render import gamemaps

    info = DdsInfo(2048, 1024, 12, 1, "DXT1")
    asked = []
    dds = SimpleNamespace(info=info, stored=lambda level, face=0: asked.append(level) or b"")
    small = gamemaps.smaller(dds, 512)
    assert (small.info.width, small.info.height, small.info.mips) == (512, 256, 10)
    small.stored(0)
    assert asked == [2] and gamemaps.smaller(dds, 0) is dds and gamemaps.smaller(dds, 4096) is dds
    pool = gamemaps.MapPool(None, 512)  # waiting for its array, the map keeps only the levels it will upload
    asked.clear()
    pool.want("a.dds", dds)
    kept = pool.pending["a.dds"]
    assert asked == list(range(2, 12)) and kept.info.width == 512 and len(kept.levels) == 10


def test_the_island_views_maps_are_halved_to_the_quality():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop.desktoprender import shadow_side, shrink

    raw = np.arange(8 * 4 * 4, dtype=np.uint8).reshape(4, 8, 4)
    size, out = shrink(raw.tobytes(), (8, 4), 4, 4)
    assert size == (4, 2) and np.frombuffer(out, np.uint8).reshape(2, 4, 4)[0, 0, 0] == round(raw[:2, :2, 0].mean())
    assert shrink(b"x", (8, 4), 4, 0) == ((8, 4), b"x")
    assert shadow_side(None) == 2048 and shadow_side(quality.preset("anno117", "low")) == 1024


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
