"""The maps of many materials on the device once per file: an island's object parts name the same ``.dds`` files
over and over (a continental island's 891 parts held 13 GB of them, 3.2 GB distinct; island-117-view.md "Speed").

``MapPool(rhi)`` holds one single-layer texture array per file, made at its first use and destroyed with its last,
and until then the levels of the file it will upload, once (``pending``); ``PooledTable(material, pool)`` stands where a
``gamedraw.TextureTable`` stands for a material: each of its files is one of the bindless stand-in's arrays
(``gameshader.CLASSES``, the first the null texture), its slots point at layer 0 of their file's array; it takes the
material's maps (``material.maps`` is empty after: a part not drawn yet held its own copy of each map).
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import numpy as np

from . import gamedraw
from . import gameshader as gs

NULL = "<null>"  # the engine's null texture: white
_UNNAMED = itertools.count()  # a map of no known file: a key of its own, never given again (an id() would be)


class _Coarser:
    """A stored map from its level ``skip`` down: what the device gets when its finer levels are past the quality's
    largest side."""

    def __init__(self, dds, skip: int) -> None:
        self.dds, self.skip = dds, skip
        i = dds.info
        self.info = replace(i, width=max(1, i.width >> skip), height=max(1, i.height >> skip), mips=i.mips - skip)

    def stored(self, level: int = 0, face: int = 0) -> bytes:
        return self.dds.stored(level + self.skip, face)

    def level(self, level: int = 0, face: int = 0):
        return self.dds.level(level + self.skip, face)


class _Kept:
    """A stored map's levels from the quality's largest side down, copied out of its file (the file let go): what a
    map waits in the pool as until its array is made (at Medium a 4096 map's top two levels, 15/16 of it, are not
    uploaded, so they are not kept either)."""

    def __init__(self, dds, most: int) -> None:
        coarse = smaller(dds, most)
        self.info = coarse.info
        self.levels = [bytes(coarse.stored(k)) for k in range(self.info.mips)]

    def stored(self, level: int = 0, face: int = 0) -> bytes:
        return self.levels[level]


def smaller(dds, most: int):
    """``dds`` without the levels larger than ``most`` texels a side (0, or no coarser level to start at: as it is);
    a block-compressed level stays at least 4 texels a side."""
    if not most or dds is None:
        return dds
    i = dds.info
    skip = 0
    while (
        max(i.width >> skip, i.height >> skip) > most
        and skip + 1 < i.mips
        and min(i.width, i.height) >> (skip + 1) >= 4
    ):
        skip += 1
    return _Coarser(dds, skip) if skip else dds


class MapPool:
    """One texture array per map file on the device, counted by the tables that name it (module docstring)."""

    def __init__(self, rhi, max_size: int = 0) -> None:
        self.rhi = rhi
        self.max_size = max_size  # the largest side uploaded: coarser levels first (``render.quality``; 0: all)
        self.arrays: dict[str, object] = {}
        self.users: dict[str, int] = {}
        self.pending: dict[str, object] = {}  # path -> its ``DdsFile`` until its array is made

    def want(self, path: str, dds) -> None:
        """One more table names ``path``; its pixels kept once until its array is made."""
        self.users[path] = self.users.get(path, 0) + 1
        if path not in self.arrays and path != NULL and path not in self.pending:
            stored, _key = gamedraw.map_kind(dds)
            self.pending[path] = _Kept(dds, self.max_size) if stored else dds

    def take(self, batch, path: str) -> object:
        """The array of ``path`` (made from its pixels and uploaded in ``batch`` on first use)."""
        tex = self.arrays.get(path)
        if tex is None:
            if path == NULL:
                pixels = [np.full((4, 4, 4), 255, np.uint8)]
            else:
                dds = self.pending.pop(path)
                dds = dds if isinstance(dds, _Kept) else smaller(dds, self.max_size)
                stored, _key = gamedraw.map_kind(dds)
                pixels = [dds if stored else dds.level(0)]
            tex = self.arrays[path] = gamedraw.class_array(self.rhi, batch, pixels)
        return tex

    def give_back(self, paths) -> None:
        """One table fewer names each of ``paths``; an array (or pixels) no table names is let go."""
        for path in paths:
            left = self.users.get(path, 0) - 1
            if left > 0:
                self.users[path] = left
                continue
            self.users.pop(path, None)
            self.pending.pop(path, None)
            tex = self.arrays.pop(path, None)
            if tex is not None:
                tex.destroy()

    def __len__(self) -> int:
        return len(self.arrays)

    def release(self) -> None:
        for tex in self.arrays.values():
            tex.destroy()
        self.arrays, self.users, self.pending = {}, {}, {}


class PooledTable:
    """A material's maps as the bindless stand-in reads them from ``pool``: ``slots`` (parameter -> bindless index)
    and ``words`` (index -> array << 16, layer 0) at once, ``create`` the arrays bound (made in the pool when new);
    a material of more files than arrays reads the null texture for the rest (as ``TextureTable``)."""

    def __init__(self, material: gamedraw.Material, pool: MapPool) -> None:
        self.pool = pool
        self.slots: dict[str, int] = {}
        self.words = np.zeros(gs.SLOTS, np.uint32)
        self.files: list[str] = []  # the path of each array after the null one
        unnamed: dict[int, str] = {}  # the ids of maps alive here: a map named twice is one array
        for name, dds in material.maps.items():
            path = material.paths.get(name) or unnamed.setdefault(id(dds), f"<map {next(_UNNAMED)}>")
            if path not in self.files:
                if len(self.files) + 1 == gs.CLASSES:
                    continue  # the parameter reads the null texture
                self.files.append(path)
                pool.want(path, dds)
            slot = len(self.slots) + 1
            self.slots[name] = slot
            self.words[slot] = (self.files.index(path) + 1) << 16
        pool.want(NULL, None)
        material.maps.clear()  # the pool's now
        self.made = False

    def create(self, rhi, batch) -> list:
        """The arrays to bind (``gameshader.CLASSES`` of them: the null texture, the files, the null again)."""
        if self.made:
            raise RuntimeError("a pooled table's arrays are made once")
        null = self.pool.take(batch, NULL)
        out = [null] + [self.pool.take(batch, path) for path in self.files]
        self.made = True
        return out + [null] * (gs.CLASSES - len(out))

    def drop_pixels(self) -> None:
        """Nothing to drop: the pool holds the pixels once until the arrays are made."""

    def release(self) -> None:
        """Give the files back to the pool (the last table that names one lets it go)."""
        self.pool.give_back([NULL, *self.files])
        self.files = []
