"""The instance records and bones kept between frames (``render.instancestore``): a slab per batch's part made once,
an edit sending only the rows that changed, each buffer taking the ranges written since it last looked, the rows of
parts not drawn let go and packed."""

from __future__ import annotations

import numpy as np

from annotoolbox.render import instancestore
from annotoolbox.render.instancestore import CInstanceStore, CRows, SPlaces


def _worlds(xs) -> np.ndarray:
    worlds = np.tile(np.identity(4, np.float32), (len(xs), 1, 1))
    worlds[:, 0, 3] = xs
    return worlds


class FakeBatch:
    """A batch: its instances (``m_cells``) replaced by an edit."""

    def __init__(self, xs) -> None:
        self.m_worlds, self.m_cells = _worlds(xs), object()

    def places(self) -> SPlaces:
        return SPlaces(self, self.m_cells, b"part", False, lambda: self.m_worlds)


def test_a_batchs_records_are_made_once_and_a_moved_instance_sends_its_row_alone():
    store, batch = CInstanceStore(), FakeBatch([0.0, 1.0, 2.0, 3.0])
    asked = []
    places = batch.places()
    places = places._replace(worlds=lambda: asked.append(1) or batch.m_worlds)
    assert store.slab(places) == (0, 0, 4) and store.slab(places) == (0, 0, 4) and asked == [1]  # made once
    assert store.m_records.changes("device") == [(0, 4)]  # a buffer new to the store takes every row
    assert store.m_bones.changes("device") == [(0, 4)]
    batch.m_worlds = batch.m_worlds.copy()
    batch.m_worlds[2, 0, 3] = 9.0  # an edit: one instance moved
    batch.m_cells = object()
    assert store.slab(batch.places()) == (0, 0, 4)  # in place: the count stayed
    assert store.m_records.changes("device") == [(2, 3)] and store.m_bones.changes("device") == [(2, 3)]
    assert store.positions(0, np.array([2]))[0, 0] == 9.0
    batch.m_worlds, batch.m_cells = _worlds([5.0, 6.0]), object()  # instances deleted: rows of another count
    record, _bone, count = store.slab(batch.places())
    assert count == 2 and store.positions(record, np.arange(2))[:, 0].tolist() == [5.0, 6.0]


def test_a_skin_takes_a_bone_per_joint_and_the_terrain_adaptation_makes_every_slab_again():
    store, batch = CInstanceStore(), FakeBatch([0.0, 1.0])
    rigid, skin = store.slab(batch.places()), store.slab(batch.places(), joints=3)
    assert rigid[1] == 0 and skin[1] == 2 and store.m_bones.m_top == 2 + 2 * 3
    assert store.m_records.m_rows[0, 5] & instancestore.NO_TERRAIN_ADAPT
    store.adapt(True)
    store.slab(batch.places())
    assert not store.m_records.m_rows[0, 5] & instancestore.NO_TERRAIN_ADAPT


def test_slabs_not_drawn_are_let_go_and_the_rows_packed():
    store = CInstanceStore()
    kept, gone = FakeBatch([1.0] * 2000), FakeBatch([2.0] * 4000)
    store.slab(gone.places())
    kept_places = kept.places()
    assert store.slab(kept_places)[0] == 4000
    store.m_records.changes("device")
    for _ in range(instancestore.KEEP_FRAMES + 1):
        store.end_frame()
        store.slab(kept_places)
    store.drop_unused()
    record, _bone, count = store.slab(kept_places)
    assert record == 0 and count == 2000 and store.m_records.m_top == 2000  # packed: the kept rows moved down
    assert store.m_records.changes("device") == [(0, 2000)]  # every row again
    assert store.positions(record, np.array([1999]))[0, 0] == 1.0


def test_a_consumer_takes_merged_ranges_and_too_many_turn_into_every_row():
    rows = CRows(np.uint32)
    start = rows.take(10)
    assert rows.changes("a") == [(0, 10)] and start == 0  # a consumer new to the rows takes every row
    rows.write(2, np.ones((2, 12), np.uint32))
    rows.write(3, np.full((3, 12), 2, np.uint32))
    assert rows.changes("a") == [(2, 6)] and rows.changes("b") == [(0, 10)]
    for k in range(instancestore.MAX_RANGES + 1):
        rows.write(k % 10, np.full((1, 12), k + 5, np.uint32))
    assert rows.changes("a") == [(0, 10)]
    rows.give_back(0, 4)
    assert rows.take(3) == 0 and rows.take(1) == 3 and rows.take(2) == 10  # the free range first, then the top


def _engine_world(words: np.ndarray) -> np.ndarray:
    """``GetWorldMatrix``'s rotation and scale of records' ``Rotation`` and ``Scale`` words ([n, 4]), as the shaders
    unpack them (``UncompressQuaternion64(Rotation.y, Rotation.x)``, ``UnpackScale``), in column-vector form."""
    high, low = words[:, 0].astype(np.uint64), words[:, 1].astype(np.uint64)
    bits = np.stack([(high >> 12) & 0xFFFFF, ((low >> 24) & 0xFF) | ((high << 8) & 0xFFF00), (low >> 4) & 0xFFFFF], 1)
    stored = (bits.astype(np.float64) - instancestore.QUATERNION_BIAS) * instancestore.QUATERNION_STEP
    rest = np.where(low & 4, -1.0, 1.0) * np.sqrt(np.maximum(0.0, 1 - (stored**2).sum(1)))
    quaternion = np.zeros((len(words), 4))
    for index in range(4):
        rows = (low & 3) == index
        quaternion[np.ix_(rows, [k for k in range(4) if k != index])] = stored[rows]
        quaternion[rows, index] = rest[rows]
    x, y, z, w = quaternion.T
    rotation = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], 1),
                         np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], 1),
                         np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], 1)], 1)  # fmt: skip
    halves = np.stack([words[:, 2] >> 16, words[:, 2] & 0xFFFF, words[:, 3] & 0xFFFF], 1).astype(np.uint16)
    return rotation * halves.view(np.float16).astype(np.float64)[:, None, :]


def test_a_records_rotation_and_scale_unpack_to_its_place_as_the_engines_culling_reads_them():
    rng = np.random.default_rng(7)
    worlds = np.tile(np.identity(4), (64, 1, 1))
    for k in range(64):
        rotation, _upper = np.linalg.qr(rng.normal(size=(3, 3)))
        rotation *= np.sign(np.linalg.det(rotation))  # a rotation, not a reflection
        worlds[k, :3, :3] = rotation @ np.diag(rng.uniform(0.25, 4.0, 3))
    worlds[::2, :3, 0] *= -1  # the view's x mirrored: a negative x scale
    records = instancestore.instance_records(worlds)
    assert np.abs(_engine_world(records[:, 8:12]) - worlds[:, :3, :3]).max() < 4e-3 * 4  # half floats' precision
    assert (records[:, 3] == instancestore.NO_SPLINE).all()  # no spline: the culling takes the record's own place
    upright = instancestore.instance_records(np.identity(4)[None])
    assert np.allclose(_engine_world(upright[:, 8:12])[0], np.identity(3), atol=1e-5)
