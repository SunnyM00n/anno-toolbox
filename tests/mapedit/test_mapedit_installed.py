"""The map editor on the installed games' own maps (``ANNO117_DIR``, ``ANNO1800_DIR``, ``ANNO2070_DIR``, ``ANNO1404_DIR``,
``ANNO1701_DIR``; skipped without): every vanilla template, level header, world and random-map template written back
byte for byte, a grown template whose every reference moved, the quarter turn checked against a session's baked
heights, the Aegyptus complexes, 1800's mine slots and the derived difficulties (docs/map-editor.md)."""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

import pytest

from annotoolbox.formats import a7mread, filedb
from annotoolbox.mapedit import grow, levelfile, mapdoc, pool, sessionfile, template, variants, worlds
from annotoolbox.sources import gametree

VARS = {"anno117": "ANNO117_DIR", "anno1800": "ANNO1800_DIR"}
WORLD_VARS = {"anno2070": "ANNO2070_DIR", "anno1404": "ANNO1404_DIR", "anno1701": "ANNO1701_DIR"}
GROWN = {  # a template with objects, camera flights and (117) horizon islands
    "anno117": "data/base/provinces/roman/templates/campaign/roman_province_campaign_01/roman_province_campaign_01.a7tinfo",
    "anno1800": "data/sessions/maps/pool/moderate/moderate_atoll_mm_01/moderate_atoll_mm_01.a7tinfo",
}
# what a growth may keep in a vanilla map: the fixed lines, the flights' actions, the enlarged maps' offsets, scripts
KEEPABLE = re.compile(
    r"^(|the island files|the camera's start.*|CameraSequenceManager action keyframes|MapTemplate/(EnlargementOffset|IslandShift)|lines .*)$"
)


@pytest.fixture(scope="module", params=list(VARS))
def game(request, tmp_path_factory):
    folder = os.environ.get(VARS[request.param])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {VARS[request.param]}")
    tree = gametree.open_game(request.param, Path(folder), tmp_path_factory.mktemp("index"))
    return request.param, tree, pool.CGameMaps.read(tree, request.param)


def test_every_vanilla_template_writes_back_byte_for_byte(game):
    key, tree, _maps = game
    paths = mapdoc.map_paths(tree, key)
    assert paths
    for path in paths:
        raw = tree.read(path)
        assert template.read(raw).render() == raw, path
        level = path.rsplit(".", 1)[0] + ".a7te"
        if tree.exists(level):
            data = tree.read(level)
            assert levelfile.regrown(data, *levelfile.dimensions(data)) == data, level


def _positions(session: sessionfile.SSession) -> list[tuple[float, float, float]]:
    out = []
    for area in session.manager.find("AreaManagerData").children:
        data = area.find("Data") if isinstance(area, filedb.STag) else None
        for node in filedb.read(data.data).iter() if data is not None else []:
            if isinstance(node, filedb.SAttrib) and node.name == "Position" and len(node.data) == 12:
                out.append(struct.unpack("<3f", node.data))
    return out


def _views(session: sessionfile.SSession) -> list[tuple[float, float, float]]:
    return [
        struct.unpack("<3f", n.find("From").data)
        for n in session.manager.iter()
        if isinstance(n, filedb.STag) and n.name == "View"
    ]


def test_a_grown_template_moves_every_reference(game):
    key, tree, maps = game
    path, west, south = GROWN[key], 256, 192
    doc = mapdoc.open_map(tree, key, path, maps)
    before = [(e.x, e.z) for e in doc.elements]
    old = sessionfile.read(tree.read(path.rsplit(".", 1)[0] + ".a7t"))
    grown = doc.grow((west, south, 64, 0))
    t = template.read(grown.files[".a7tinfo"])
    assert t.size == (doc.size[0], doc.size[1]) == (old.size[0] + west + 64, old.size[1] + south)
    assert [(e.x, e.z) for e in t.elements] == [(x + west, z + south) for x, z in before]
    new = sessionfile.read(grown.files[".a7t"])
    assert new.size == t.size
    for read in (_positions, _views):
        want = [v for x, y, z in read(old) for v in (x + west, y, z + south)]
        assert [v for p in read(new) for v in p] == pytest.approx(want)
    assert levelfile.dimensions(grown.files[".a7te"]) == t.size
    pa = struct.unpack("<4i", new.manager.path("SessionSettings/PlayableArea").data)
    assert pa[2] - pa[0] == t.playable[2] - t.playable[0]
    unexpected = [
        r for r in grown.report.kept() if not KEEPABLE.match(r.where) and r.file.endswith((".a7t", ".a7tinfo"))
    ]
    assert not unexpected, unexpected
    if key == "anno117":  # every horizon position moved, one that leaves X out (0) included
        block = grow.horizon_list(maps.asset_of(path).text, grow.HORIZONS)
        xs = [float(m.group(1)) if m else 0.0 for p in re.findall(r"<Position>.*?</Position>", block, re.S)
              for m in [re.search(r"<X>(-?[\d.]+)</X>", p)]]  # fmt: skip
        moved = [float(x) for x in re.findall(r"<X>(-?[\d.]+)</X>", grown.asset_ops[0])]
        assert moved == [x + west for x in xs]


def test_the_aegyptus_map_is_four_complexes_whose_seams_are_not_flagged(game):
    key, tree, maps = game
    paths = [p for p in mapdoc.map_paths(tree, key) if "egypt_province_default_01" in p]
    if key != "anno117" or not paths:
        pytest.skip("117's Aegyptus map")
    doc = mapdoc.open_map(tree, key, paths[0], maps)
    assert sorted(len(g) for g in doc.complexes()) == [9, 10, 10, 10]
    assert not [f for f in doc.check() if "overlap" in f.text]
    group = doc.complexes()[0]
    doc.rotate(group)
    doc.move(group, 64, 0)
    assert not [f for f in doc.check() if "overlap" in f.text and set(f.elements) <= set(group)]


def _ccw(a):
    """A height array (rows of v) after one turn (u, v) -> (E - v, u)."""
    return a[::-1].T


def test_the_quarter_turn_matches_the_islands_baked_into_a_campaign_session(game):
    """117's Celtic campaign carries its islands' terrain in its session: each island's own heights match the
    session's at its Position only when turned the way ``placing.turned`` does (map-editor "What the data settles")."""
    np = pytest.importorskip("numpy")
    key, tree, maps = game
    path = (
        "data/base/provinces/celtic/templates/campaign/celtic_province_campaign_01/celtic_province_campaign_01.a7tinfo"
    )
    if key != "anno117" or not tree.exists(path):
        pytest.skip("117's Celtic campaign")
    t = template.read(tree.read(path))
    hm = sessionfile.read(tree.read(path[: -len("info")])).manager.path("TerrainManager/HeightMap")
    width = struct.unpack("<I", hm.find("Width").data)[0]
    session = np.frombuffer(hm.find("HeightMap").data, "<u2").reshape(-1, width).astype(float)
    checked = set()
    for e in t.elements:
        if e.kind != template.KIND_FIXED or e.Rotation90 not in (0, 2, 3) or not tree.exists(e.MapFilePath):
            continue
        n, flat = a7mread.heights(tree.read(e.MapFilePath))
        edge = maps.island(tree, e.MapFilePath).size
        idx = np.linspace(0, n - 1, 2 * edge + 1).round().astype(int)
        own = np.array(flat).reshape(-1, n)[np.ix_(idx, idx)]
        window = session[2 * e.z : 2 * e.z + 2 * edge + 1, 2 * e.x : 2 * e.x + 2 * edge + 1]
        turns, fits = own, []
        for _ in range(4):
            fits.append(abs(float(np.corrcoef(window.ravel(), turns.ravel())[0, 1])))
            turns = _ccw(turns)
        best = sorted(fits)
        if best[-1] > 0.5 and best[-1] - best[-2] > 0.1:  # a clear match
            assert fits.index(best[-1]) == e.Rotation90, e.MapFilePath
            checked.add(e.Rotation90)
    assert {0, 2, 3} <= checked


def test_an_1800_islands_mine_slots_are_its_slot_objects(game):
    key, tree, maps = game
    path = "data/sessions/maps/pool/moderate/moderate_c_01/moderate_c_01.a7tinfo"
    if key != "anno1800" or not tree.exists(path):
        pytest.skip("1800's moderate_c_01")
    island = next(e for e in template.read(tree.read(path)).elements if e.MineSlotActivation)
    slots = {s.id for s in pool.island_slots(tree, island.MapFilePath, maps.m_slot_assets)}
    assert {k for k, _v in island.MineSlotActivation} <= slots


def test_the_vanilla_medium_maps_derive_easy_and_hard_as_vanilla_counts_them(game):
    key, tree, maps = game
    if key != "anno117":
        pytest.skip("117's three difficulties")

    def large(elements) -> int:
        return sum(
            1 for e in elements if e.kind == template.KIND_SLOT and e.type == 0 and e.size == template.SIZE_LARGE
        )

    medium_maps = [p for p in mapdoc.map_paths(tree, key) if "_medium" in p and "/pool/" in p]
    assert medium_maps
    for path in medium_maps:
        medium = template.read(tree.read(path)).elements
        easy, hard = (variants.derive(medium, level, maps.m_classes) for level in ("easy", "hard"))
        assert len(easy) == len(hard) == len(medium) and large(hard) == 0, path
        vanilla_easy = template.read(tree.read(variants.sibling(path, "easy"))).elements
        assert large(easy) > large(medium) and abs(large(easy) - large(vanilla_easy)) <= 4, path


@pytest.mark.parametrize("key", list(WORLD_VARS))
def test_every_world_of_the_older_games_writes_back_and_moves(key, tmp_path):
    folder = os.environ.get(WORLD_VARS[key])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {WORLD_VARS[key]}")
    tree = gametree.open_game(key, Path(folder), tmp_path / "index")
    paths = mapdoc.map_paths(tree, key)
    assert paths
    moved = 0
    for path in paths:
        raw = tree.read(path)
        world = worlds.read(raw, key)
        assert world.render() == raw, path
        if world.elements:
            world.elements[0].x += 16
            again = worlds.read(world.render(), key)
            assert again.elements[0].x == world.elements[0].x, path
            moved += 1
    assert moved
