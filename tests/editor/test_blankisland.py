"""A new 117 island from scratch: a vanilla island of the grid, cleared and reshaped by a height edit."""

import numpy as np
import pytest

from annotoolbox.editor import blankisland, editorapp
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.islands import islandedits
from annotoolbox.islands.donor117 import GRID_SIZES
from annotoolbox.islands.islandgrid import HEIGHT_UNITS
from annotoolbox.suite.settings import CSettings


def vanilla(grid: int) -> np.ndarray:
    """A stand-in island: a square of land at 0.8 with a bump, sea bed at -30 round it."""
    h = np.full((grid + 1, grid + 1), -30.0, np.float32)
    q = grid // 4
    h[q:-q, q:-q] = 0.8
    h[q + 2 : q + 6, q + 2 : q + 6] = 6.0
    return h


@pytest.mark.parametrize("grid", GRID_SIZES)
def test_every_grid_size_has_a_vanilla_island_to_start_on(grid):
    assert blankisland.donor(grid).endswith(".a7m")
    assert blankisland.name(grid) == f"new{grid}"


def test_the_plateau_sits_at_the_islands_own_land_level_and_the_edge_is_kept():
    base = vanilla(128)
    shaped = blankisland.target_heights(base)
    assert blankisland.land_level(base) == pytest.approx(0.8)
    assert shaped[64, 64] == pytest.approx(0.8)  # the centre is plateau, the bump gone
    assert np.allclose(shaped[0], base[0]) and np.allclose(shaped[:, -1], base[:, -1])
    assert shaped.min() >= base[0, 0] - 1e-3  # never deeper than the sea bed it meets


class FakeVanilla:
    """What ``CApp.open_blank117`` asks of the opened vanilla island: its height map (2 samples a tile), its forest."""

    def __init__(self, files, a7m_path, cache, mod_assets="", edits=None, tree2070=None):
        h = np.repeat(np.repeat(vanilla(128), 2, axis=0), 2, axis=1)[:257, :257]
        self.m_view = type("View", (), {"height_map": h * HEIGHT_UNITS})()
        self.m_edit_meta = {}

    def forest(self):
        return {"tiles": [[40, 41]], "models": []}


def test_the_app_makes_the_edit_file_on_the_first_open_and_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(a7m_edit117, "CVanillaEditor", FakeVanilla)
    monkeypatch.setattr(editorapp.CApp, "game117", lambda self: {})
    settings = CSettings(anno117=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.CApp(tmp_path / "settings.json")
    info = app.open("anno117", "new128")
    assert (
        info["name"] == "new128" and app.m_editor.m_edit_meta["clear"] and app.m_editor.m_edit_meta["name"] == "new128"
    )
    path = app.edits_dir / "117_new128.json"
    edits = islandedits.load(path)
    assert edits["source"] == blankisland.donor(128) and edits["forest"] == {"40,41": 0} and edits["heights"]
    edits["heights"] = {"1,1": 0.5}  # the mapper's own work from then on
    islandedits.save(path, edits)
    app.open_blank117(128)
    assert islandedits.load(path)["heights"] == {"1,1": 0.5}
    row = app.catalogue()["anno117"]["edited"][0]
    assert row["name"] == "new128" and row["open"] == "new128"
    assert blankisland.grid_of("new128") == 128 and blankisland.grid_of("new100") is None


def test_the_edit_file_is_a_cleared_new_island_whose_heights_give_the_shape():
    base = vanilla(128)
    edits = blankisland.blank_edits(base, 128, forest=[(40, 41)])
    assert edits["clear"] and edits["target"] == "new" and edits["name"] == "new128"
    assert edits["forest"] == {"40,41": 0}
    assert edits["source"] == blankisland.donor(128)
    rebuilt = base.copy()
    for k, v in edits["heights"].items():
        z, x = map(int, k.split(","))
        rebuilt[z, x] += v
    assert np.allclose(rebuilt, blankisland.target_heights(base), atol=2e-3)
    with pytest.raises(ValueError):
        blankisland.blank_edits(base, 192)
    with pytest.raises(ValueError):
        blankisland.donor(100)


class Fake1800:
    """What ``CApp.open_blank`` asks of the opened 1800 island: its corner heights at two samples a tile."""

    def __init__(self, grid: int = 192):
        self.m_view = type(
            "View",
            (),
            {
                "size": grid,
                "heights": lambda s: np.repeat(np.repeat(vanilla(grid), 2, 0), 2, 1)[: 2 * grid + 1, : 2 * grid + 1],
            },
        )()


def blank1800_app(tmp_path, monkeypatch):
    settings = CSettings(anno117=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.CApp(tmp_path / "settings.json")

    def opened(self, key, path):
        self.m_editor = Fake1800()
        return {"kind": key, "name": path}

    monkeypatch.setattr(editorapp.CApp, "_open_other", opened)
    return app


def test_a_new_1800_island_of_any_grid_is_shaped_in_place_on_the_first_open_only(tmp_path, monkeypatch):
    app = blank1800_app(tmp_path, monkeypatch)
    source = "data/dlc01/worlds/islands/moderate_3rdparty06_01/moderate_3rdparty06_01.a7m"
    app.open_blank("anno1800", source, is_shaped=True)  # 192 tiles: no 117 donor has that grid
    path = app.edits_dir / "1800_moderate_3rdparty06_01.json"
    edits = islandedits.load(path)
    assert edits["source"] == source and edits["game"] == "anno1800" and edits["clear"] and edits["heights"]
    assert "target" not in edits and "name" not in edits  # 1800 replaces its islands in place
    rebuilt = vanilla(192)
    for k, v in edits["heights"].items():
        z, x = map(int, k.split(","))
        rebuilt[z, x] += v
    assert np.allclose(rebuilt, blankisland.target_heights(vanilla(192)), atol=2e-3)
    edits["heights"] = {"1,1": 0.5}  # the mapper's own work from then on
    islandedits.save(path, edits)
    app.open_blank("anno1800", source, is_shaped=True)
    assert islandedits.load(path)["heights"] == {"1,1": 0.5}


def test_a_blank_1800_island_that_is_not_shaped_keeps_its_vanilla_ground(tmp_path, monkeypatch):
    app = blank1800_app(tmp_path, monkeypatch)
    app.open_blank("anno1800", "data/x/a.a7m")
    edits = islandedits.load(app.edits_dir / "1800_a.json")
    assert edits["clear"] and not edits["heights"]


def test_blank_edits_with_a_source_leave_the_island_where_it_stands():
    edits = blankisland.blank_edits(vanilla(192), 192, source="data/x/a.a7m")
    assert set(edits) == {"source", "clear", "heights"} and edits["heights"]
    with pytest.raises(ValueError):
        blankisland.blank_edits(vanilla(128), 192, source="data/x/a.a7m")


def test_a_blank_1404_or_1701_island_is_painted_one_material_and_its_own_paint_is_left_alone(tmp_path, monkeypatch):
    from annotoolbox.islands.islandground import stroke_cover

    class Fake1404:
        def ground(self):
            return {"width": 4, "materials": [{"id": 11}, {"id": 12}]}

        def ground_weights(self):
            return bytes([10] * 16) + bytes([200] * 16)  # the second layer is most of the ground

    app = blank1800_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        editorapp.CApp, "_open_other", lambda self, key, path: setattr(self, "m_editor", Fake1404()) or {"kind": key}
    )
    monkeypatch.setattr(editorapp.CApp, "_corner_heights", lambda self, key: (None, 100))
    app.open_blank("anno1404", "data/x/a.isd")
    path = app.edits_dir / "1404_a.json"
    (stroke,) = islandedits.load(path)["paint"]
    assert stroke["material"] == 12 and stroke["strength"] == 1.0
    assert stroke_cover(stroke, 100, 1).min() >= 0.9  # every tile of the grid
    edits = islandedits.load(path)
    edits["paint"] = [{"material": 11, "radius": 2, "strength": 1.0, "points": [[1, 1]]}]
    islandedits.save(path, edits)
    app.open_blank("anno1404", "data/x/a.isd")
    assert islandedits.load(path)["paint"][0]["material"] == 11  # the mapper's own paint stays
    app.open_blank("anno1800", "data/x/b.a7m")
    assert not islandedits.load(app.edits_dir / "1800_b.json")["paint"]  # 1800 keeps the island's ground
