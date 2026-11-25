"""The instance records and bone matrices of every shown batch's parts, kept for every frame as the engine keeps them
in video memory (``cModelInstanceDataBuffer``): made when a batch's instances change, and only the rows that changed
go to the device; a frame writes only its per-drawn-instance words (``gameinstances.CFrameInstances``).

``CInstanceStore`` is the entry: ``slab(places, joints)`` gives where a part's records and bones start, ``changes``
what a buffer of them still has to take. docs/rendering/performance.md section 4, item 1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import numpy as np

ALPHA = 15 << 22  # the per-frame word's alpha: 15/15
DEFORM = 1 << 1  # ``Data.x``: the config bends the model to the terrain (``DeformToTerrainHeight``)
NO_TERRAIN_ADAPT = 1 << 7  # ``Data.y``: no terrain adaptation at all (a frame without the session's terrain)
LAST_NONE = 0xFFFFFFFF  # ``LastIndex``: no last frame's orientation
NO_SPLINE = 0xFFFFFFFF  # ``SplineData``: no spline segment (its key all ones: ``INVALID_SPLINE_INSTANCE_DATA_KEY``)
QUATERNION_BIAS = 524287.0  # ``UncompressQuaternion64``: a stored component's zero and its step
QUATERNION_STEP = 0.5**0.5 / 524287.0
ROW_WORDS = 12  # a record and a bone matrix are 12 words (48 bytes) each
EVERYTHING = None  # a consumer's changes when it has to take every row (new, or too many ranges to keep)
MAX_RANGES = 256  # a consumer's ranges kept before it takes every row instead [ours]
KEEP_FRAMES = 600  # a slab not drawn for this many frames is let go (turning at 30 a second: 20 s) [ours]


def bone_rows(worlds: np.ndarray, joints: int = 0) -> np.ndarray:
    """Each instance's place as the engine's bone matrices hold it ([n * max(1, joints), 12] float32: the top three
    rows of each matrix, ``v' = [v, 1] M``), the bind pose for a skin: every joint at the instance's place."""
    rows = np.asarray(worlds, np.float32)[:, :3, :4].reshape(len(worlds), 12)
    return np.repeat(rows, max(1, joints), axis=0)


def instance_records(
    worlds: np.ndarray, colour: int = 0xFFF, deform: bool = False, is_terrain_adapted: bool = True
) -> np.ndarray:
    """Each instance's ``ModelInstanceData`` ([n, 12] words, 48 bytes): its place, no spline, its object colour (12
    bits; 0 is black), ``deform`` (bent to the terrain), its height as the terrain adaptation reads it (7.8 fixed point
    from -4, clamped as the engine packs it; ``is_terrain_adapted`` False: no adaptation, no terrain to read), no last
    frame's orientation, and its rotation and scale (``orientation_words``: the engine's caster culling reads them)."""
    records = np.zeros((len(worlds), 12), np.uint32)
    places = np.asarray(worlds, np.float32)[:, :3, 3]
    records[:, 0:3] = np.ascontiguousarray(places).view(np.uint32)
    height = np.clip(np.rint((places[:, 1].astype(np.float64) + 4.0) * 256), 0, 0x7FFF).astype(np.uint32)
    records[:, 3] = NO_SPLINE
    records[:, 4], records[:, 7] = (colour << 4) | (DEFORM if deform else 0), LAST_NONE
    records[:, 5] = (height << 17) | (0 if is_terrain_adapted else NO_TERRAIN_ADAPT)
    records[:, 8:12] = orientation_words(worlds)
    return records


def orientation_words(worlds: np.ndarray) -> np.ndarray:
    """``Rotation`` and ``Scale`` of each place ([n, 4] words) as ``GetWorldMatrix`` unpacks them: the rotation a
    64-bit quaternion of its three smallest components (20 bits each, the largest one's index and sign in the low
    bits: ``UncompressQuaternion64``), the scale three halves; a mirrored place has its x scale negative."""
    matrices = np.asarray(worlds, np.float64)[:, :3, :3]
    scale = np.linalg.norm(matrices, axis=1)  # each column's length: the scale along that axis
    scale[np.linalg.det(matrices) < 0, 0] *= -1
    rotation = matrices / np.where(scale == 0, 1.0, scale)[:, None, :]
    quaternion = _quaternions(rotation)
    largest = np.argmax(np.abs(quaternion), axis=1)
    rows = np.arange(len(quaternion))
    is_negative = quaternion[rows, largest] < 0
    others = np.array([[k for k in range(4) if k != index] for index in range(4)])[largest]
    stored = np.rint(quaternion[rows[:, None], others] / QUATERNION_STEP + QUATERNION_BIAS)
    stored = np.clip(stored, 0, 0xFFFFF).astype(np.uint32)
    low = largest.astype(np.uint32) | (is_negative.astype(np.uint32) << 2) | (stored[:, 2] << 4) | (stored[:, 1] << 24)
    high = (stored[:, 1] >> 8) | (stored[:, 0] << 12)
    halves = scale.astype(np.float16).view(np.uint16).astype(np.uint32)
    return np.stack([high, low, (halves[:, 0] << 16) | halves[:, 1], halves[:, 2]], axis=1)


def _quaternions(rotation: np.ndarray) -> np.ndarray:
    """(x, y, z, w) of rotation matrices ([n, 3, 3], column vectors), by the largest of the four diagonal sums."""
    m = rotation
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    sums = np.stack([1 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2], 1 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2],
                     1 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2], 1 + trace], axis=1)  # fmt: skip
    pick = np.argmax(sums, axis=1)
    root = np.sqrt(np.maximum(sums[np.arange(len(m)), pick], 1e-12))
    xy, xz, yz = m[:, 1, 0] + m[:, 0, 1], m[:, 0, 2] + m[:, 2, 0], m[:, 2, 1] + m[:, 1, 2]
    wx, wy, wz = m[:, 2, 1] - m[:, 1, 2], m[:, 0, 2] - m[:, 2, 0], m[:, 1, 0] - m[:, 0, 1]
    candidates = np.stack([np.stack([root * root, xy, xz, wx], axis=1), np.stack([xy, root * root, yz, wy], axis=1),
                           np.stack([xz, yz, root * root, wz], axis=1), np.stack([wx, wy, wz, root * root], axis=1)],
                          axis=1)  # fmt: skip
    quaternion = candidates[np.arange(len(m)), pick] / (2 * root)[:, None]
    return quaternion / np.linalg.norm(quaternion, axis=1, keepdims=True)


class SPlaces(NamedTuple):
    """One batch's part as the store keeps it: ``owner`` (the batch, held: it keys the store), ``version`` (its
    instances: another object, and the records are made again), ``part`` (the part's key), ``deform`` (bent to the
    terrain) and ``worlds()``, every instance's place ([n, 4, 4], column vectors), asked only when its rows are made."""

    owner: object
    version: object
    part: object
    deform: bool
    worlds: Callable[[], np.ndarray]

    @staticmethod
    def of(worlds: np.ndarray, deform: bool = False) -> SPlaces:
        """Places of their own, no batch's: ``worlds`` owns them."""
        return SPlaces(worlds, worlds, None, deform, lambda: worlds)


class CRows:
    """Rows of one kind (records or bones) for every frame, in one array the device mirrors: ranges taken and given
    back, and per consumer (a device buffer) the ranges written since it last took them."""

    def __init__(self, dtype) -> None:
        self.m_rows = np.zeros((0, ROW_WORDS), dtype)
        self.m_top = 0  # rows in use up to here
        self.m_free: list[list[int]] = []  # [start, count] given back
        self._m_pending: dict[str, list | None] = {}  # consumer -> ranges written since its last take

    def take(self, count: int) -> int:
        """Where ``count`` free rows start (the first range given back that fits, else at the top)."""
        for k, (start, size) in enumerate(self.m_free):
            if size >= count:
                if size == count:
                    del self.m_free[k]
                else:
                    self.m_free[k] = [start + count, size - count]
                return start
        start = self.m_top
        self.m_top += count
        if self.m_top > len(self.m_rows):
            grown = np.zeros((max(1024, 2 * self.m_top), ROW_WORDS), self.m_rows.dtype)
            grown[: len(self.m_rows)] = self.m_rows
            self.m_rows = grown
        return start

    def give_back(self, start: int, count: int) -> None:
        if count:
            self.m_free.append([start, count])

    def free_count(self) -> int:
        return sum(size for _start, size in self.m_free)

    def write(self, start: int, rows: np.ndarray, is_diffed: bool = False) -> None:
        """``rows`` at ``start``; ``is_diffed``: only the span of rows that differ from what is there goes out."""
        first, last = start, start + len(rows)
        if is_diffed:
            changed = np.flatnonzero((self.m_rows[first:last] != rows).any(axis=1))
            if not len(changed):
                return
            first, last = start + int(changed[0]), start + int(changed[-1]) + 1
        if last > first:
            self.m_rows[first:last] = rows[first - start : last - start]
            self._mark(first, last)

    def _mark(self, start: int, stop: int) -> None:
        for consumer, ranges in self._m_pending.items():
            if ranges is not EVERYTHING:
                ranges.append((start, stop))
                if len(ranges) > MAX_RANGES:
                    self._m_pending[consumer] = EVERYTHING

    def mark_all(self) -> None:
        self._m_pending = dict.fromkeys(self._m_pending, EVERYTHING)

    def changes(self, consumer: str) -> list[tuple[int, int]]:
        """The ranges ``consumer`` has to take since its last call (every row in use the first time, merged)."""
        ranges = self._m_pending.get(consumer, EVERYTHING)
        self._m_pending[consumer] = []
        if ranges is EVERYTHING:
            return [(0, self.m_top)] if self.m_top else []
        merged: list[list[int]] = []
        for start, stop in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], stop)
            else:
                merged.append([start, stop])
        return [(start, stop) for start, stop in merged]


class CInstanceStore:
    """Every shown part's records and bones kept between frames (module docstring): a slab per batch, part, bend and
    joint count, made again when the batch's instances change (in place when their count stays: only the rows that
    differ are sent), let go when not drawn for ``KEEP_FRAMES``."""

    def __init__(self) -> None:
        self.m_records = CRows(np.uint32)
        self.m_bones = CRows(np.float32)
        self.m_is_terrain_adapted = False  # the frame hands the session's terrain (``instance_records``)
        self.m_frame = 0
        # (id of the owner, part, deform, joints) -> [owner, version, record, bone, count, joints, last frame]
        self._m_slabs: dict[tuple, list] = {}

    def adapt(self, is_terrain_adapted: bool) -> None:
        """The records follow whether the frame hands the session's terrain: every slab made again when it changes."""
        if is_terrain_adapted != self.m_is_terrain_adapted:
            self.m_is_terrain_adapted = is_terrain_adapted
            for slab in self._m_slabs.values():
                slab[1] = None  # made again when next drawn

    def slab(self, places: SPlaces, joints: int = 0) -> tuple[int, int, int]:
        """(where the records start, where the bones start, how many instances) of ``places`` with ``joints`` bones
        each (0: rigid), made when new or when its batch's instances changed."""
        key = (id(places.owner), places.part, places.deform, joints)  # the entry holds the owner: its id stays its own
        slab = self._m_slabs.get(key)
        if slab is not None and slab[0] is places.owner and slab[1] is places.version:
            slab[6] = self.m_frame
            return slab[2], slab[3], slab[4]
        worlds = places.worlds()
        records = instance_records(worlds, deform=places.deform, is_terrain_adapted=self.m_is_terrain_adapted)
        bones = bone_rows(worlds, joints)
        count = len(worlds)
        if slab is not None and slab[0] is places.owner and slab[4] == count:  # edited in place: the rows that differ
            self.m_records.write(slab[2], records, is_diffed=True)
            self.m_bones.write(slab[3], bones, is_diffed=True)
        else:
            if slab is not None:
                self._give_back(slab)
            slab = [places.owner, None, self.m_records.take(count), self.m_bones.take(len(bones)), count, joints, 0]
            self.m_records.write(slab[2], records)
            self.m_bones.write(slab[3], bones)
            self._m_slabs[key] = slab
        slab[1], slab[6] = places.version, self.m_frame
        return slab[2], slab[3], slab[4]

    def _give_back(self, slab: list) -> None:
        self.m_records.give_back(slab[2], slab[4])
        self.m_bones.give_back(slab[3], slab[4] * max(1, slab[5]))

    def positions(self, record: int, local: np.ndarray) -> np.ndarray:
        """The places ([n, 3] float32) of a slab's instances ``local`` (their records from ``record`` on)."""
        return self.m_records.m_rows[record + np.asarray(local, np.int64), 0:3].view(np.float32)

    def end_frame(self) -> None:
        self.m_frame += 1

    def drop_unused(self) -> None:
        """The slabs not drawn for ``KEEP_FRAMES`` let go, and the rows packed again when over half lie free (every
        buffer then takes every row)."""
        for key in [key for key, slab in self._m_slabs.items() if self.m_frame - slab[6] > KEEP_FRAMES]:
            self._give_back(self._m_slabs.pop(key))
        if 2 * (self.m_records.free_count() + self.m_bones.free_count()) > max(
            4096, self.m_records.m_top + self.m_bones.m_top
        ):
            self._pack()

    def _pack(self) -> None:
        records, bones = CRows(np.uint32), CRows(np.float32)
        for slab in self._m_slabs.values():
            bone_count = slab[4] * max(1, slab[5])
            record_start, bone_start = records.take(slab[4]), bones.take(bone_count)
            records.m_rows[record_start : record_start + slab[4]] = self.m_records.m_rows[slab[2] : slab[2] + slab[4]]
            bones.m_rows[bone_start : bone_start + bone_count] = self.m_bones.m_rows[slab[3] : slab[3] + bone_count]
            slab[2], slab[3] = record_start, bone_start
        records._m_pending, bones._m_pending = self.m_records._m_pending, self.m_bones._m_pending
        self.m_records, self.m_bones = records, bones
        self.m_records.mark_all()
        self.m_bones.mark_all()

    def clear(self) -> None:
        """Every slab let go and every row dropped: each buffer takes the next rows whole."""
        self.m_records, self.m_bones, self._m_slabs = CRows(np.uint32), CRows(np.float32), {}
