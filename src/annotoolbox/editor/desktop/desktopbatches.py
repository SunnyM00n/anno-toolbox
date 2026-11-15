"""The island view's models on the GPU (``desktoprender``): one ``Batch`` per model config (or marker kind) with its
entries' instance matrices, and per pass (the main pass, the shadow pass) the instances it draws sorted by level of
detail, one instance buffer per level (``InstanceSets``), as 117 picks the level by screen size
(``render.cull.CellTable.codes``; island-117-view.md "Speed").

``level_meshes`` says which meshes a part draws at each level: its own coarser levels where its mesh file has them
(``_lodN``), its finest where it has none.
"""

from __future__ import annotations

import numpy as np

from ...render.cull import InstanceCells

PASSES = ("model", "depth")  # the main pass and the sun's shadow pass draw by level; picking draws every instance


class InstanceSets:
    """One pass's instances of a batch: per level a buffer holding the instances drawn at that level, in cell order
    (``update``); ``counts`` per level, ``total`` of them."""

    def __init__(self, ctx, count: int, levels: int) -> None:
        size = max(64, count * 64)
        self.buffers = [ctx.buffer(reserve=size, dynamic=True) for _ in range(max(1, levels))]
        self.counts = [0] * len(self.buffers)
        self.key: bytes | None = None

    @property
    def total(self) -> int:
        return sum(self.counts)

    def update(self, cells: InstanceCells, codes: np.ndarray) -> int:
        """The instances of the cells by their ``codes`` (per cell its level; below 0: not drawn; a level past the
        last draws at the last), each level's written when the codes changed; how many in all."""
        codes = np.minimum(codes, len(self.buffers) - 1)
        key = codes.astype(np.int8).tobytes()
        if key == self.key:
            return self.total
        self.key = key
        for level, buffer in enumerate(self.buffers):
            rows = cells.gather(codes == level)
            if len(rows):
                buffer.write(rows.tobytes())
            self.counts[level] = len(rows)
        return self.total

    def release(self) -> None:
        for buffer in self.buffers:
            buffer.release()
        self.buffers, self.counts, self.key = [], [], None


class Batch:
    """One model (or marker kind) on the GPU: its entries' instance matrices, its parts' meshes and VAOs, the
    instances each pass draws."""

    def __init__(self, key: tuple, entries: list) -> None:
        self.key, self.entries = key, entries
        self.inst = None  # every instance, in the entries' order (picking)
        self.sets: dict[str, InstanceSets] = {}  # per pass (``PASSES``) the instances it draws, by level
        self.cells: InstanceCells | None = None
        self.reach = float("inf")
        self.count = 0
        self.parts: list | None = None  # (part dict, its finest mesh's VAO specs) per mesh; None until the model is in
        self.lean = False  # its parts are their boxes alone: the game's look draws the model (picking reads them)
        self.lods: dict[int, list] = {}  # id of a part dict -> per coarser level its meshes' VAO specs
        self.levels = 1  # the levels of detail its parts have, the finest included
        self.lods_waiting: list = []  # the parts whose coarser levels are still loading
        self.vaos: list = []  # per mesh of ``parts``: (part, {pass: per level the VAOs it draws, "pick": VAO})
        self.dirty = True
        self.box = None  # the model's own box (Anno axes)
        self.mats: np.ndarray | None = None  # every instance's matrix (the view's space): where its decals lie
        self.version = 0  # counts the instance writes (the decals are laid again)
        self.radius = 4.0

    @property
    def shown(self) -> int:
        """The instances the main pass draws."""
        sets = self.sets.get("model")
        return sets.total if sets is not None else 0

    def release_draws(self) -> None:
        """Its instance sets and VAOs (made again by the renderer when the model, its levels or its count change; the
        meshes' buffers are the renderer's, shared)."""
        for _p, vaos in self.vaos:
            for vao in _each_vao(vaos):
                vao.release()
        for sets in self.sets.values():
            sets.release()
        self.sets, self.vaos = {}, []


def _each_vao(vaos: dict):
    """The VAOs of one mesh's ``Batch.vaos`` entry: picking's and per pass and level."""
    for name, got in vaos.items():
        if name == "pick":
            yield got
        else:
            yield from (vao for per in got for vao in per)


def level_meshes(parts: list, lods: dict[int, list], levels: int) -> list[list[list]]:
    """Per mesh of ``parts`` ((part, spec) as ``Batch.parts``), per level, the specs it draws: at level 0 its own;
    at a coarser level the first mesh of a part draws all of that part's meshes of its coarsest level up to that one
    (``lods``: per part its coarser levels), the others none; a part without coarser levels draws its finest mesh at
    every level."""
    out, first_seen = [], set()
    for p, spec in parts:
        own = lods.get(id(p)) or []
        first = id(p) not in first_seen
        first_seen.add(id(p))
        per = [[spec]]
        for level in range(1, levels):
            if not own:
                per.append([spec])
            else:
                per.append(list(own[min(level, len(own)) - 1]) if first else [])
        out.append(per)
    return out
