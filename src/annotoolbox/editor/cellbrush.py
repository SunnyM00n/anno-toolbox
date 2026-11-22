"""The grids of cells an editing session edits with a brush, on 117 and 1800 islands: the on-off grids (``CCellEdits``:
the active map, the ford grid, the construction area) and the byte grids (``CValueEdits``: the sea map's coast waves, the environment zones).

Each follows the island's own cells (``base``) and keeps in the edit file only the cells that differ (``edits[key]``:
``"cx,cz"`` -> 1 on / 0 off, or the byte). The build writes them: ``islands.islandinfo`` (active map),
``islands.surfmap`` (waves), ``editor.islandgrids`` (ford, zones); island-editor "Full spec".
"""

from __future__ import annotations

import base64
import math
import zlib
from dataclasses import dataclass
from typing import ClassVar

import numpy as np

from ..infra.i18n import N_, tr
from ..islands.islandgrid import AREA_ISLAND, AREA_NONE, cell_key


def _pack_bits(cells: np.ndarray) -> str:
    return base64.b64encode(np.packbits(cells.astype(bool).ravel(), bitorder="little").tobytes()).decode()


def _unpack_bits(text: str, cells_per_side: int) -> np.ndarray:
    flat = np.unpackbits(np.frombuffer(base64.b64decode(text), np.uint8), bitorder="little")
    return flat[: cells_per_side * cells_per_side].reshape(cells_per_side, cells_per_side).astype(bool)


def pack(cells: np.ndarray, allowed: np.ndarray | None = None) -> dict:
    """An island's cells as the island JSON holds them: ``n`` cells a side and their ``bits``; ``allowed``, the cells
    the brush may switch on (the ford's river tiles), as ``allowed`` bits."""
    out = {"n": int(cells.shape[0]), "bits": _pack_bits(cells)}
    if allowed is not None:
        out["allowed"] = _pack_bits(allowed)
    return out


AREA_KINDS = (
    (0x4001, N_("islands.area_0x4001")),
    (0x6001, N_("islands.area_0x6001")),
    (0x8001, N_("islands.area_0x8001")),
    (0xA001, N_("islands.area_0xa001")),
    (0xC001, N_("islands.area_0xc001")),
    (0xE001, N_("islands.area_0xe001")),
)  # the ids the shipped islands use besides the construction area's 0x2001
AREA_KIND_IDS = tuple(i for i, _name in AREA_KINDS)


def pack_area(ids: np.ndarray) -> dict:
    """An island's ``AreaIDs`` as the island JSON holds them: the construction area's cells and the cells it may be
    laid on (``pack``), and when the island has areas of other ids their ``kinds``: ``ids`` and ``map``, a zlib-packed
    byte per tile, 0 or the index + 1 of the tile's id."""
    out = pack(ids == AREA_ISLAND, (ids == AREA_ISLAND) | (ids == AREA_NONE))
    found = sorted(int(i) for i in np.unique(ids) if i not in (0, AREA_NONE, AREA_ISLAND))
    if found and len(found) < 256:
        index = np.zeros(ids.shape, np.uint8)
        for k, kind in enumerate(found, 1):
            index[ids == kind] = k
        out["kinds"] = {"ids": found, "map": base64.b64encode(zlib.compress(index.tobytes())).decode()}
    return out


@dataclass(frozen=True)
class SCellGrid:
    """What names a grid: its key in the island JSON and in the edit file, the undo step's label (marked with
    ``N_``, translated when used), and how the brush reaches a cell: ``is_by_square`` the cell's square (a cell larger
    than the brush), else its centre."""

    island_key: str
    edit_key: str
    label: str
    is_by_square: bool


class _CGridEdits:
    """What the on-off and the byte grids of a session share: the island's own cells (``base``, None when it has no
    such grid), the current ``cells``, the edit file's cells (``_edits``), and the end of a stroke, one undo step."""

    GRID: ClassVar[SCellGrid | SValueGrid]

    def __init__(self, session) -> None:
        self.m_session = session
        self.m_base: np.ndarray | None = None
        self.m_cells: np.ndarray | None = None

    def _start(self) -> None:
        """The current cells: the island's own with the edit file's on them."""
        self.m_cells = self.m_base.copy() if self.m_base is not None else None
        for key, value in self.m_session.m_edits.get(self.GRID.edit_key, {}).items():
            self._set(key, value)

    @property
    def cell_count(self) -> int:
        return 0 if self.m_cells is None else len(self.m_cells)

    @property
    def _edits(self) -> dict:
        return self.m_session.m_edits.setdefault(self.GRID.edit_key, {})

    def _cast(self, value):
        """The cell's value for an edit's, None when it is none a cell can hold (a hand-edited file)."""
        raise NotImplementedError

    def _set(self, key: str, value) -> None:
        """One edit onto the cells; a key off the grid or a value no cell holds is left out."""
        cell = cell_key(key, self.cell_count)
        value = self._cast(value)
        if cell is not None and value is not None:
            self.m_cells[cell[1], cell[0]] = value

    def end(self) -> None:
        session = self.m_session
        brush, session._m_brush = session._m_brush, None
        if not brush or brush["kind"] != self.GRID.edit_key:
            return
        before, after = brush["before"], dict(self._edits)
        if before == after:
            return

        def put(changes: dict) -> None:
            session.m_edits[self.GRID.edit_key] = dict(changes)
            self._start()
            session.blockers_changed()

        session._push(tr(self.GRID.label), lambda: put(before), lambda: put(after))


class CCellEdits(_CGridEdits):
    """The cells of one grid of an ``CEditSession``; ``cells`` is the current state, ``base`` the island's own (None
    when the island has no such grid), ``cell_tiles`` the tiles a cell is wide."""

    GRID: ClassVar[SCellGrid]

    def __init__(self, session) -> None:
        super().__init__(session)
        data = session.m_island.get(self.GRID.island_key)
        self.m_allowed: np.ndarray | None = None
        self.m_cell_tiles = 8
        if data:
            cells_per_side = int(data["n"])
            self.m_base = _unpack_bits(data["bits"], cells_per_side)
            self.m_allowed = _unpack_bits(data["allowed"], cells_per_side) if "allowed" in data else None
            self.m_cell_tiles = max(1, session.m_size_tiles // cells_per_side)
        self._start()

    def _cast(self, value):
        return bool(value)

    def begin(self, is_on: bool, radius_tiles: float, x: float, z: float, **stroke) -> None:
        """Start a stroke; ``stroke``: what a kind of grid adds to it (``CAreaEdits``)."""
        session = self.m_session
        if session.m_readonly or self.m_base is None:
            return
        session._m_brush = {
            "kind": self.GRID.edit_key,
            "on": is_on,
            "radius": radius_tiles,
            "before": dict(self._edits),
            **stroke,
        }
        self.to(x, z)

    def _reached(self, x: int, z: int, center_x: float, center_z: float, radius_tiles: float) -> bool:
        tiles_per_cell = self.m_cell_tiles
        if self.GRID.is_by_square:  # the brush touches the cell's square
            near_x, near_z = (
                min(max(center_x, x * tiles_per_cell), (x + 1) * tiles_per_cell),
                min(max(center_z, z * tiles_per_cell), (z + 1) * tiles_per_cell),
            )
            return math.hypot(near_x - center_x, near_z - center_z) <= radius_tiles
        return math.hypot((x + 0.5) * tiles_per_cell - center_x, (z + 0.5) * tiles_per_cell - center_z) <= radius_tiles

    def to(self, center_x: float, center_z: float) -> None:
        session, brush = self.m_session, self.m_session._m_brush
        if not brush or brush["kind"] != self.GRID.edit_key:
            return
        radius_tiles, tiles_per_cell = brush["radius"], self.m_cell_tiles
        for z in range(
            max(0, math.floor((center_z - radius_tiles) / tiles_per_cell)),
            min(self.cell_count, math.floor((center_z + radius_tiles) / tiles_per_cell) + 1),
        ):
            for x in range(
                max(0, math.floor((center_x - radius_tiles) / tiles_per_cell)),
                min(self.cell_count, math.floor((center_x + radius_tiles) / tiles_per_cell) + 1),
            ):
                if self._reached(x, z, center_x, center_z, radius_tiles):
                    self._stamp(x, z, brush)
        session.blockers_changed()

    def _stamp(self, x: int, z: int, brush: dict) -> None:
        """One cell the brush reaches takes the brush's state (on or off), unless it may not be switched on."""
        if brush["on"] and self.m_allowed is not None and not self.m_allowed[z, x]:
            return
        key = f"{x},{z}"
        if bool(self.m_base[z, x]) == brush["on"]:
            self._edits.pop(key, None)
        else:
            self._edits[key] = 1 if brush["on"] else 0
        self.m_cells[z, x] = brush["on"]

    def tiles(self) -> int:
        """The tiles the cells on cover now."""
        return 0 if self.m_cells is None else int(self.m_cells.sum()) * self.m_cell_tiles * self.m_cell_tiles


class CActiveMapEdits(CCellEdits):
    """The active map: the cells of 8 tiles the game draws and copies into the session."""

    GRID = SCellGrid("active_map", "active", N_("islands.active_map_lower"), is_by_square=True)


class CAreaEdits(CCellEdits):
    """The construction area: the tiles ``AreaIDs`` marks as the island's; a tile of another area is none
    the brush may touch (``allowed``), except to paint another area's id over it (``begin`` with a number above 1:
    ``kind_ids``, ``kind_cells``; island-editor "Area kinds")."""

    GRID = SCellGrid("area_grid", "area", N_("islands.construction_area_lower"), is_by_square=False)

    def __init__(self, session) -> None:
        data = session.m_island.get(self.GRID.island_key) or {}
        kinds = data.get("kinds") or {}
        self.m_kind_ids: list[int] = [int(i) for i in kinds.get("ids", [])]
        self.m_kind_base: np.ndarray | None = None  # per tile: 0, else the index + 1 in ``kind_ids`` of its area id
        if self.m_kind_ids:
            raw = zlib.decompress(base64.b64decode(kinds["map"]))
            cells_per_side = int(data["n"])
            self.m_kind_base = (
                np.frombuffer(raw, np.uint8)[: cells_per_side * cells_per_side]
                .reshape(cells_per_side, cells_per_side)
                .copy()
            )
        self.m_kind_cells: np.ndarray | None = None
        super().__init__(session)

    def _start(self) -> None:
        self.m_kind_cells = self.m_kind_base.copy() if self.m_kind_base is not None else None
        super()._start()

    def _set(self, key: str, value) -> None:
        """An edit onto the cells: 1 or 0 the construction area on or off, the id of one of the island's areas that
        area; anything else is left out (the build takes no other)."""
        cell = cell_key(key, self.cell_count)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        if cell is None or self.m_cells is None:
            return
        x, z = cell
        if value in (0, 1):
            self.m_cells[z, x], kind = bool(value), 0
        elif value in self.m_kind_ids:
            self.m_cells[z, x], kind = False, self.m_kind_ids.index(value) + 1
        else:
            return
        if self.m_kind_cells is not None:
            self.m_kind_cells[z, x] = kind

    def begin(self, state, radius_tiles: float, x: float, z: float) -> None:
        """Start a stroke: ``on`` True or False the construction area, a number above 1 the area with that id."""
        kind = int(state) if not isinstance(state, bool) and int(state) > 1 else None
        if kind is not None and kind not in self.m_kind_ids:
            return
        super().begin(kind is None and bool(state), radius_tiles, x, z, paint=kind)

    def _stamp(self, x: int, z: int, brush: dict) -> None:
        paint = brush.get("paint")
        was = int(self.m_kind_base[z, x]) if self.m_kind_base is not None else 0
        if paint is None and not was:
            super()._stamp(x, z, brush)
            if self.m_kind_cells is not None:
                self.m_kind_cells[z, x] = 0  # a kind the edits painted here is taken back
            return
        if paint is None or not (was or self.m_allowed[z, x]):
            return  # a tile that is an area of its own in the island stays, and so does one outside any area
        index = self.m_kind_ids.index(paint) + 1
        key = f"{x},{z}"
        if was == index:
            self._edits.pop(key, None)
        else:
            self._edits[key] = paint
        self.m_cells[z, x], self.m_kind_cells[z, x] = False, index


class CHerdEdits(CCellEdits):
    """The herd grid: the tiles the island's herds roam; only land that is no river is offered (``allowed``)."""

    GRID = SCellGrid("herd_grid", "herd", N_("islands.herd_area_lower"), is_by_square=False)


class CFordEdits(CCellEdits):
    """The ford grid: the river tiles units may cross; a ford is only laid on a river tile."""

    GRID = SCellGrid("ford_grid", "ford", N_("islands.ford_lower"), is_by_square=False)


FULL = 255  # the largest byte of a value grid: full coast waves


def pack_values(cells: np.ndarray) -> dict:
    """An island's byte cells as the island JSON holds them: ``n`` cells a side and their ``values``."""
    return {"n": int(cells.shape[0]), "values": base64.b64encode(np.asarray(cells, np.uint8).tobytes()).decode()}


@dataclass(frozen=True)
class SValueGrid:
    """What names a byte grid: its keys in the island JSON and the edit file and the undo step's label (``N_``)."""

    island_key: str
    edit_key: str
    label: str


class CValueEdits(_CGridEdits):
    """The byte cells of one ``CEditSession``; ``cells`` is the current state, ``base`` the island's own (None when the
    island has no such grid), ``tiles_per_cell`` the tiles a cell covers. A stroke sets the cells the brush's centre reaches."""

    GRID: ClassVar[SValueGrid]

    def __init__(self, session) -> None:
        super().__init__(session)
        data = session.m_island.get(self.GRID.island_key)
        self.m_tiles_per_cell = 2.0
        if data:
            cells_per_side = int(data["n"])
            self.m_base = (
                np.frombuffer(base64.b64decode(data["values"]), np.uint8)[: cells_per_side * cells_per_side]
                .reshape(cells_per_side, cells_per_side)
                .copy()
            )
            self.m_tiles_per_cell = session.m_size_tiles / cells_per_side
        self._start()

    def _cast(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= FULL else None

    def begin(self, value: int, radius_tiles: float, x: float, z: float) -> None:
        session = self.m_session
        if session.m_readonly or self.m_base is None:
            return
        session._m_brush = {
            "kind": self.GRID.edit_key,
            "value": max(0, min(FULL, int(value))),
            "radius": radius_tiles,
            "before": dict(self._edits),
        }
        self.to(x, z)

    def to(self, center_x: float, center_z: float) -> None:
        session, brush = self.m_session, self.m_session._m_brush
        if not brush or brush["kind"] != self.GRID.edit_key:
            return
        radius_tiles, tiles_per_cell = brush["radius"], self.m_tiles_per_cell
        for z in range(
            max(0, math.floor((center_z - radius_tiles) / tiles_per_cell)),
            min(self.cell_count, math.floor((center_z + radius_tiles) / tiles_per_cell) + 1),
        ):
            for x in range(
                max(0, math.floor((center_x - radius_tiles) / tiles_per_cell)),
                min(self.cell_count, math.floor((center_x + radius_tiles) / tiles_per_cell) + 1),
            ):
                if (
                    math.hypot((x + 0.5) * tiles_per_cell - center_x, (z + 0.5) * tiles_per_cell - center_z)
                    > radius_tiles
                ):
                    continue
                key = f"{x},{z}"
                if int(self.m_base[z, x]) == brush["value"]:
                    self._edits.pop(key, None)
                else:
                    self._edits[key] = brush["value"]
                self.m_cells[z, x] = brush["value"]
        session.blockers_changed()

    def changed(self) -> np.ndarray:
        """The cells (z, x) the edits changed, as a boolean array."""
        return np.zeros((0, 0), bool) if self.m_cells is None else self.m_cells != self.m_base


class CSurfEdits(CValueEdits):
    """The sea map's coast waves: 0 calm water up to 255 full waves, a cell per 2 x 2 tiles."""

    GRID = SValueGrid("surf_map", "surf", N_("islands.surf_lower"))


class CEnvironmentEdits(CValueEdits):
    """The environment grid: the zone (the game's ``EnvironmentType``) of each 4 x 4 tiles."""

    GRID = SValueGrid("environment_map", "environment", N_("islands.environment_lower"))
