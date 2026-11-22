"""The island view's models on the GPU (``desktoprender``): one ``CBatch`` per model config (or marker kind) with its
entries' instance matrices, and per pass (the main pass, the shadow pass) the instances it draws sorted by level of
detail, one instance buffer per level (``CInstanceSets``), as 117 picks the level by screen size
(``render.cull.CCellTable.codes``; island-117-view.md "Speed").

``level_meshes`` says which meshes a part draws at each level: its own coarser levels where its mesh file has them
(``_lodN``), its finest where it has none.
"""

from __future__ import annotations

import numpy as np

from ...render.cull import CInstanceCells

PASSES = ("model", "depth")  # the main pass and the sun's shadow pass draw by level; picking draws every instance


class CInstanceSets:
    """One pass's instances of a batch: per level a buffer holding the instances drawn at that level, in cell order
    (``update``); ``counts`` per level, ``total`` of them."""

    def __init__(self, ctx, count: int, levels: int) -> None:
        size = max(64, count * 64)
        self.m_buffers = [ctx.buffer(reserve=size, dynamic=True) for _ in range(max(1, levels))]
        self.m_counts = [0] * len(self.m_buffers)
        self.m_key: bytes | None = None

    @property
    def total(self) -> int:
        return sum(self.m_counts)

    def update(self, cells: CInstanceCells, codes: np.ndarray) -> int:
        """The instances of the cells by their ``codes`` (per cell its level; below 0: not drawn; a level past the
        last draws at the last), each level's written when the codes changed; how many in all."""
        codes = np.minimum(codes, len(self.m_buffers) - 1)
        key = codes.astype(np.int8).tobytes()
        if key == self.m_key:
            return self.total
        self.m_key = key
        for level, buffer in enumerate(self.m_buffers):
            rows = cells.gather(codes == level)
            if len(rows):
                buffer.write(rows.tobytes())
            self.m_counts[level] = len(rows)
        return self.total

    def release(self) -> None:
        for buffer in self.m_buffers:
            buffer.release()
        self.m_buffers, self.m_counts, self.m_key = [], [], None


class CBatch:
    """One model (or marker kind) on the GPU: its entries' instance matrices, its parts' meshes and VAOs, the
    instances each pass draws."""

    def __init__(self, key: tuple, entries: list) -> None:
        self.m_key, self.m_entries = key, entries
        self.m_inst = None  # every instance, in the entries' order (picking)
        self.m_sets: dict[str, CInstanceSets] = {}  # per pass (``PASSES``) the instances it draws, by level
        self.m_cells: CInstanceCells | None = None
        self.m_reach = float("inf")
        self.m_count = 0
        self.m_parts: list | None = (
            None  # (part dict, its finest mesh's VAO specs) per mesh; None until the model is in
        )
        self.m_lean = False  # its parts are their boxes alone: the game's look draws the model (picking reads them)
        self.m_lods: dict[int, list] = {}  # id of a part dict -> per coarser level its meshes' VAO specs
        self.m_levels = 1  # the levels of detail its parts have, the finest included
        self.m_lods_waiting: list = []  # the parts whose coarser levels are still loading
        self.m_vaos: list = []  # per mesh of ``parts``: (part, {pass: per level the VAOs it draws, "pick": VAO})
        self.m_dirty = True
        self.m_box = None  # the model's own box (Anno axes)
        self.m_mats: np.ndarray | None = None  # every instance's matrix (the view's space): where its decals lie
        self.m_version = 0  # counts the instance writes (the decals are laid again)
        self.m_radius_m = 4.0

    @property
    def shown(self) -> int:
        """The instances the main pass draws."""
        sets = self.m_sets.get("model")
        return sets.total if sets is not None else 0

    def release_draws(self) -> None:
        """Its instance sets and VAOs (made again by the renderer when the model, its levels or its count change; the
        meshes' buffers are the renderer's, shared)."""
        for _part, vaos in self.m_vaos:
            for vao in _each_vao(vaos):
                vao.release()
        for sets in self.m_sets.values():
            sets.release()
        self.m_sets, self.m_vaos = {}, []


def _each_vao(vaos: dict):
    """The VAOs of one mesh's ``CBatch.vaos`` entry: picking's and per pass and level."""
    for name, got in vaos.items():
        if name == "pick":
            yield got
        else:
            yield from (vao for per in got for vao in per)


def level_meshes(parts: list, lods: dict[int, list], levels: int) -> list[list[list]]:
    """Per mesh of ``parts`` ((part, spec) as ``CBatch.parts``), per level, the specs it draws: at level 0 its own;
    at a coarser level the first mesh of a part draws all of that part's meshes of its coarsest level up to that one
    (``lods``: per part its coarser levels), the others none; a part without coarser levels draws its finest mesh at
    every level."""
    out, first_seen = [], set()
    for part, spec in parts:
        own = lods.get(id(part)) or []
        first = id(part) not in first_seen
        first_seen.add(id(part))
        per = [[spec]]
        for level in range(1, levels):
            if not own:
                per.append([spec])
            else:
                per.append(list(own[min(level, len(own)) - 1]) if first else [])
        out.append(per)
    return out
