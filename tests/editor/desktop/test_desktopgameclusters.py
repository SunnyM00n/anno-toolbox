"""The island view's distance clusters (``editor.desktop.desktopgameclusters``) with the device side stood in: the
regions and their levels, the far-view models read once, an edit building again only the regions it touched once it
settled, which regions a frame draws at a distance and which instances they stand for."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from tests.render.renderfakes import far_view_bfg

from annotoolbox.editor.desktop import desktopgameclusters as clusters
from annotoolbox.render import clustersnative, gameshadow
from annotoolbox.render.cull import CInstanceCells


class FakeLoader:
    """The view's loader, its work done at once."""

    def __init__(self) -> None:
        self.m_asked: list[str] = []
        self.m_done: dict = {}

    def get(self, key: str, work):
        if key not in self.m_done:
            self.m_asked.append(key)
            self.m_done[key] = work()
        return self.m_done[key]

    def forget(self, key) -> None:
        self.m_done.pop(key, None)


class FakeDeviceClusters:
    """``clustersnative.CNativeClusters`` without a device: what it was handed."""

    def __init__(self, objects, found) -> None:
        self.m_put, self.m_dropped, self.m_synced = [], [], 0

    def put(self, merged):
        mesh = SimpleNamespace(m_merged=merged)
        self.m_put.append(mesh)
        return mesh

    def drop(self, mesh) -> None:
        self.m_dropped.append(mesh)

    def sync(self, atlas) -> None:
        self.m_synced += 1

    def runs(self, drawn, reach):
        return [mesh for mesh, _alpha in drawn], [] if reach is None else [reach]

    def release(self) -> None:
        """Nothing held."""


class FakeBatch:
    def __init__(self, positions, model: str = "data/tree.cfg", kind: str = "props", parts=None) -> None:
        self.m_key = (kind, model)
        self.m_parts = parts if parts is not None else [({"shader": 8}, None)]
        self.place(positions)

    def place(self, positions) -> None:
        matrices = np.repeat(np.identity(4, np.float32)[None], len(positions), axis=0)
        matrices[:, :3, 3] = positions
        self.m_mats, self.m_cells = matrices, CInstanceCells(matrices)


@pytest.fixture
def game(monkeypatch):
    monkeypatch.setattr(clustersnative, "CNativeClusters", FakeDeviceClusters)
    monkeypatch.setattr(clustersnative, "shaders", lambda natives: {})
    monkeypatch.setattr(clustersnative, "CNativeClusters", FakeDeviceClusters)
    monkeypatch.setattr(clusters, "SETTLE_S", 0.0)
    files = SimpleNamespace(
        read=lambda path: far_view_bfg() if path == "data/tree.bfg" else (_ for _ in ()).throw(OSError)
    )
    renderer = SimpleNamespace(m_loader=FakeLoader(), m_editor=SimpleNamespace(m_files=files), m_said=[])
    renderer._message = renderer.m_said.append
    island = SimpleNamespace(m_renderer=renderer, m_native_objects=object(), _m_natives=None)
    return clusters.CGameClusters(island)


def settle(game, batches: list, options=None) -> None:
    """Updates until every region is built (two builds in flight at a time)."""
    for _ in range(50):
        game.update(batches, options or SimpleNamespace(distance_clusters=True))
        if not game.m_waiting:
            return
    raise AssertionError("the regions were not built")


def test_region_keys_and_their_levels():
    keys = clusters.region_keys(np.array([[1.0, 0, 1.0], [17.0, 0, 1.0], [100.0, 0, 1.0]]))
    assert keys.shape == (3, len(clusters.LEVELS_M))
    assert clusters.level_of(keys).tolist() == [[0, 1, 2, 3]] * 3
    assert keys[0, 0] != keys[1, 0] and keys[0, 1] == keys[1, 1]  # 16 apart: one region of 32, two of 16
    assert keys[0, 2] != keys[2, 2] and keys[0, 3] == keys[2, 3]


def test_distances_fades_and_what_moves_on_the_views_clock():
    centres, scales = np.array([[0.0, 0, 0], [100.0, 0, 0]]), np.array([[10.0, 10, 10], [10.0, 10, 10]])
    assert clusters.box_distances(np.zeros(3), centres, scales).tolist() == [0.0, 90.0]
    assert clusters.fade_of(np.array([150.0, 210.0, 400.0]), 200.0).tolist() == [0.0, 0.5, 1.0]
    assert clusters.is_static(FakeBatch([[0, 0, 0]]))
    assert not clusters.is_static(FakeBatch([[0, 0, 0]], parts=[({"shader": 28}, None)]))  # a banner
    assert not clusters.is_static(FakeBatch([[0, 0, 0]], parts=[({"shader": 8, "flow": 1.0}, None)]))


def test_the_shadow_reach_without_known_cascades_is_every_used_one():
    reach = clusters._reach((None, 2, 1024), np.zeros((3, 3)), np.ones((3, 3)))
    assert reach.shape == (3, gameshadow.COUNT) and reach[:, :2].all() and not reach[:, 2:].any()


def test_regions_are_built_once_per_level_and_an_edit_builds_again_only_what_it_touched(game):
    batch = FakeBatch([[1.0, 0, 1.0], [40.0, 0, 1.0], [300.0, 0, 1.0]])
    other = FakeBatch([[2.0, 0, 2.0]], model="data/rock.cfg")  # no baked data file: not merged
    settle(game, [batch, other])
    assert game.m_far["data/rock.cfg"] is None and ("props", "data/rock.cfg") not in game.m_members
    built = len(game.m_native.m_put)
    assert built == len(game.m_regions) == 3 * len(clusters.LEVELS_M) - 2  # the two near ones share levels 2 and 3
    asked = len(game.m_renderer.m_loader.m_asked)
    settle(game, [batch, other])
    assert len(game.m_renderer.m_loader.m_asked) == asked + 0 and len(game.m_native.m_put) == built  # nothing new
    batch.place([[1.0, 0, 1.0], [40.0, 0, 1.0], [302.0, 0, 1.0]])  # the far one moved within its regions
    settle(game, [batch, other])
    assert len(game.m_native.m_put) == built + len(clusters.LEVELS_M) and len(game.m_native.m_dropped) == 4


def test_a_frame_draws_the_coarsest_region_its_distance_allows_and_hides_what_they_stand_for(game):
    batch = FakeBatch([[1.0, 0, 1.0], [600.0, 0, 1.0]])
    settle(game, [batch])
    view = SimpleNamespace(eye=np.array([0.0, 50.0, 0.0]))
    frame = game.frame(view, SimpleNamespace(distance_clusters=True), 200.0, 5000.0, None)
    drawn = [mesh.m_merged.centre[0] for mesh in frame.gbuffer]
    assert drawn == [600.5]  # the near one is the objects' own; the far one once, at the level its distance allows
    levels = {clusters.level_of(key) for key, region in game.m_regions.items()
              if any(region.mesh is mesh for mesh in frame.gbuffer)}  # fmt: skip
    assert levels == {1}  # 600 away from a start of 200: level 1 (from 400), not 2 (from 800)
    table = SimpleNamespace(m_batches=[batch], m_starts=np.array([0, 2]))
    hidden = game.hidden(table, frame)
    order = batch.m_cells.m_order  # the table's rows are in the cells' order
    assert hidden[np.argsort(order)].tolist() == [False, True]
    assert game.frame(view, SimpleNamespace(distance_clusters=False), 200.0, 5000.0, None).gbuffer == []


def test_a_far_plane_and_the_shadow_pass_follow_the_frame(game):
    batch = FakeBatch([[600.0, 0, 1.0]])
    settle(game, [batch])
    view = SimpleNamespace(eye=np.zeros(3))
    assert (
        game.frame(view, SimpleNamespace(distance_clusters=True), 200.0, 100.0, None).gbuffer == []
    )  # past the view's object distance
    frame = game.frame(view, SimpleNamespace(distance_clusters=True), 200.0, 5000.0, (None, 3, 1024))
    assert len(frame.casters) == 1 and frame.casters[0][:, :3].all()


def test_a_draw_that_raises_turns_the_clusters_off_said_once(game):
    settle(game, [FakeBatch([[600.0, 0, 1.0]])])
    game.fail("RuntimeError: boom")
    game.fail("again")
    assert (
        game.m_native is None
        and not game.is_on(SimpleNamespace(distance_clusters=True))
        and len(game.m_renderer.m_said) == 1
    )


def test_the_builds_in_flight_are_let_go_with_the_device_side(game):
    batch = FakeBatch([[1.0, 0, 1.0], [40.0, 0, 1.0], [300.0, 0, 1.0]])
    loader = game.m_renderer.m_loader
    ends = loader.get
    loader.get = lambda key, work: (  # the regions' builds never end
        ends(key, work) if not key.startswith("game-cluster-build") else loader.m_done.get(key)
    )
    game.update([batch], SimpleNamespace(distance_clusters=True))
    in_flight = [region.job for region in game.m_regions.values() if region.job]
    assert len(in_flight) == clusters.MAX_BUILDS
    loader.m_done.update(dict.fromkeys(in_flight, (None,)))
    game.fail("RuntimeError: boom")
    assert not set(in_flight) & set(loader.m_done)  # the results nobody takes are forgotten
    game.m_error = ""
    game.update([batch], SimpleNamespace(distance_clusters=True))
    assert len([region for region in game.m_regions.values() if region.job]) == clusters.MAX_BUILDS  # builds go on


def test_a_region_of_hidden_instances_is_built_to_nothing_and_does_not_wait(game):
    batch = FakeBatch([[600.0, 0, 1.0]])
    batch.m_mats[:, :3, :3] = 0  # hidden by a scale of 0
    batch.m_cells = CInstanceCells(batch.m_mats)
    settle(game, [batch])
    assert game.m_native.m_put == [] and all(region.built == region.version for region in game.m_regions.values())
