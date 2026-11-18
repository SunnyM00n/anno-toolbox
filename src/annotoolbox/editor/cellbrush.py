"""The grids of cells an editing session edits with a brush, on 117 and 1800 islands: the on-off grids (``CellEdits``:
the active map, the ford grid, the construction area) and the byte grids (``ValueEdits``: the sea map's coast waves, the environment zones).

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


def _unpack_bits(text: str, n: int) -> np.ndarray:
    flat = np.unpackbits(np.frombuffer(base64.b64decode(text), np.uint8), bitorder="little")
    return flat[: n * n].reshape(n, n).astype(bool)


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
class CellGrid:
    """What names a grid: its key in the island JSON and in the edit file, the undo step's label (marked with
    ``N_``, translated when used), and how the brush reaches a cell: ``by_square`` the cell's square (a cell larger
    than the brush), else its centre."""

    island_key: str
    edit_key: str
    label: str
    by_square: bool


class _GridEdits:
    """What the on-off and the byte grids of a session share: the island's own cells (``base``, None when it has no
    such grid), the current ``cells``, the edit file's cells (``_edits``), and the end of a stroke, one undo step."""

    GRID: ClassVar[CellGrid | ValueGrid]

    def __init__(self, session) -> None:
        self.s = session
        self.base: np.ndarray | None = None
        self.cells: np.ndarray | None = None

    def _start(self) -> None:
        """The current cells: the island's own with the edit file's on them."""
        self.cells = self.base.copy() if self.base is not None else None
        for key, value in self.s.edits.get(self.GRID.edit_key, {}).items():
            self._set(key, value)

    @property
    def n(self) -> int:
        return 0 if self.cells is None else len(self.cells)

    @property
    def _edits(self) -> dict:
        return self.s.edits.setdefault(self.GRID.edit_key, {})

    def _cast(self, value):
        """The cell's value for an edit's, None when it is none a cell can hold (a hand-edited file)."""
        raise NotImplementedError

    def _set(self, key: str, value) -> None:
        """One edit onto the cells; a key off the grid or a value no cell holds is left out."""
        cell = cell_key(key, self.n)
        value = self._cast(value)
        if cell is not None and value is not None:
            self.cells[cell[1], cell[0]] = value

    def end(self) -> None:
        s = self.s
        b, s._brush = s._brush, None
        if not b or b["kind"] != self.GRID.edit_key:
            return
        before, after = b["before"], dict(self._edits)
        if before == after:
            return

        def put(changes: dict) -> None:
            s.edits[self.GRID.edit_key] = dict(changes)
            self._start()
            s.blockers_changed()

        s._push(tr(self.GRID.label), lambda: put(before), lambda: put(after))


class CellEdits(_GridEdits):
    """The cells of one grid of an ``EditSession``; ``cells`` is the current state, ``base`` the island's own (None
    when the island has no such grid), ``cell`` the tiles a cell is wide."""

    GRID: ClassVar[CellGrid]

    def __init__(self, session) -> None:
        super().__init__(session)
        data = session.island.get(self.GRID.island_key)
        self.allowed: np.ndarray | None = None
        self.cell = 8
        if data:
            n = int(data["n"])
            self.base = _unpack_bits(data["bits"], n)
            self.allowed = _unpack_bits(data["allowed"], n) if "allowed" in data else None
            self.cell = max(1, session.size // n)
        self._start()

    def _cast(self, value):
        return bool(value)

    def begin(self, on: bool, radius: float, x: float, z: float, **stroke) -> None:
        """Start a stroke; ``stroke``: what a kind of grid adds to it (``AreaEdits``)."""
        s = self.s
        if s.readonly or self.base is None:
            return
        s._brush = {"kind": self.GRID.edit_key, "on": on, "radius": radius, "before": dict(self._edits), **stroke}
        self.to(x, z)

    def _reached(self, x: int, z: int, cx: float, cz: float, r: float) -> bool:
        per = self.cell
        if self.GRID.by_square:  # the brush touches the cell's square
            near_x, near_z = min(max(cx, x * per), (x + 1) * per), min(max(cz, z * per), (z + 1) * per)
            return math.hypot(near_x - cx, near_z - cz) <= r
        return math.hypot((x + 0.5) * per - cx, (z + 0.5) * per - cz) <= r

    def to(self, cx: float, cz: float) -> None:
        s, b = self.s, self.s._brush
        if not b or b["kind"] != self.GRID.edit_key:
            return
        r, per = b["radius"], self.cell
        for z in range(max(0, math.floor((cz - r) / per)), min(self.n, math.floor((cz + r) / per) + 1)):
            for x in range(max(0, math.floor((cx - r) / per)), min(self.n, math.floor((cx + r) / per) + 1)):
                if self._reached(x, z, cx, cz, r):
                    self._stamp(x, z, b)
        s.blockers_changed()

    def _stamp(self, x: int, z: int, b: dict) -> None:
        """One cell the brush reaches takes the brush's state (on or off), unless it may not be switched on."""
        if b["on"] and self.allowed is not None and not self.allowed[z, x]:
            return
        key = f"{x},{z}"
        if bool(self.base[z, x]) == b["on"]:
            self._edits.pop(key, None)
        else:
            self._edits[key] = 1 if b["on"] else 0
        self.cells[z, x] = b["on"]

    def tiles(self) -> int:
        """The tiles the cells on cover now."""
        return 0 if self.cells is None else int(self.cells.sum()) * self.cell * self.cell


class ActiveMapEdits(CellEdits):
    """The active map: the cells of 8 tiles the game draws and copies into the session."""

    GRID = CellGrid("active_map", "active", N_("islands.active_map_lower"), by_square=True)


class AreaEdits(CellEdits):
    """The construction area: the tiles ``AreaIDs`` marks as the island's; a tile of another area is none
    the brush may touch (``allowed``), except to paint another area's id over it (``begin`` with a number above 1:
    ``kind_ids``, ``kind_cells``; island-editor "Area kinds")."""

    GRID = CellGrid("area_grid", "area", N_("islands.construction_area_lower"), by_square=False)

    def __init__(self, session) -> None:
        data = session.island.get(self.GRID.island_key) or {}
        kinds = data.get("kinds") or {}
        self.kind_ids: list[int] = [int(i) for i in kinds.get("ids", [])]
        self.kind_base: np.ndarray | None = None  # per tile: 0, else the index + 1 in ``kind_ids`` of its area id
        if self.kind_ids:
            raw = zlib.decompress(base64.b64decode(kinds["map"]))
            n = int(data["n"])
            self.kind_base = np.frombuffer(raw, np.uint8)[: n * n].reshape(n, n).copy()
        self.kind_cells: np.ndarray | None = None
        super().__init__(session)

    def _start(self) -> None:
        self.kind_cells = self.kind_base.copy() if self.kind_base is not None else None
        super()._start()

    def _set(self, key: str, value) -> None:
        """An edit onto the cells: 1 or 0 the construction area on or off, the id of one of the island's areas that
        area; anything else is left out (the build takes no other)."""
        cell = cell_key(key, self.n)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        if cell is None or self.cells is None:
            return
        x, z = cell
        if value in (0, 1):
            self.cells[z, x], kind = bool(value), 0
        elif value in self.kind_ids:
            self.cells[z, x], kind = False, self.kind_ids.index(value) + 1
        else:
            return
        if self.kind_cells is not None:
            self.kind_cells[z, x] = kind

    def begin(self, on, radius: float, x: float, z: float) -> None:
        """Start a stroke: ``on`` True or False the construction area, a number above 1 the area with that id."""
        kind = int(on) if not isinstance(on, bool) and int(on) > 1 else None
        if kind is not None and kind not in self.kind_ids:
            return
        super().begin(kind is None and bool(on), radius, x, z, paint=kind)

    def _stamp(self, x: int, z: int, b: dict) -> None:
        paint = b.get("paint")
        was = int(self.kind_base[z, x]) if self.kind_base is not None else 0
        if paint is None and not was:
            super()._stamp(x, z, b)
            if self.kind_cells is not None:
                self.kind_cells[z, x] = 0  # a kind the edits painted here is taken back
            return
        if paint is None or not (was or self.allowed[z, x]):
            return  # a tile that is an area of its own in the island stays, and so does one outside any area
        index = self.kind_ids.index(paint) + 1
        key = f"{x},{z}"
        if was == index:
            self._edits.pop(key, None)
        else:
            self._edits[key] = paint
        self.cells[z, x], self.kind_cells[z, x] = False, index


class HerdEdits(CellEdits):
    """The herd grid: the tiles the island's herds roam; only land that is no river is offered (``allowed``)."""

    GRID = CellGrid("herd_grid", "herd", N_("islands.herd_area_lower"), by_square=False)


class FordEdits(CellEdits):
    """The ford grid: the river tiles units may cross; a ford is only laid on a river tile."""

    GRID = CellGrid("ford_grid", "ford", N_("islands.ford_lower"), by_square=False)


FULL = 255  # the largest byte of a value grid: full coast waves


def pack_values(cells: np.ndarray) -> dict:
    """An island's byte cells as the island JSON holds them: ``n`` cells a side and their ``values``."""
    return {"n": int(cells.shape[0]), "values": base64.b64encode(np.asarray(cells, np.uint8).tobytes()).decode()}


@dataclass(frozen=True)
class ValueGrid:
    """What names a byte grid: its keys in the island JSON and the edit file and the undo step's label (``N_``)."""

    island_key: str
    edit_key: str
    label: str


class ValueEdits(_GridEdits):
    """The byte cells of one ``EditSession``; ``cells`` is the current state, ``base`` the island's own (None when the
    island has no such grid), ``per`` the tiles a cell covers. A stroke sets the cells the brush's centre reaches."""

    GRID: ClassVar[ValueGrid]

    def __init__(self, session) -> None:
        super().__init__(session)
        data = session.island.get(self.GRID.island_key)
        self.per = 2.0
        if data:
            n = int(data["n"])
            self.base = np.frombuffer(base64.b64decode(data["values"]), np.uint8)[: n * n].reshape(n, n).copy()
            self.per = session.size / n
        self._start()

    def _cast(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= FULL else None

    def begin(self, value: int, radius: float, x: float, z: float) -> None:
        s = self.s
        if s.readonly or self.base is None:
            return
        s._brush = {
            "kind": self.GRID.edit_key,
            "value": max(0, min(FULL, int(value))),
            "radius": radius,
            "before": dict(self._edits),
        }
        self.to(x, z)

    def to(self, cx: float, cz: float) -> None:
        s, b = self.s, self.s._brush
        if not b or b["kind"] != self.GRID.edit_key:
            return
        r, per = b["radius"], self.per
        for z in range(max(0, math.floor((cz - r) / per)), min(self.n, math.floor((cz + r) / per) + 1)):
            for x in range(max(0, math.floor((cx - r) / per)), min(self.n, math.floor((cx + r) / per) + 1)):
                if math.hypot((x + 0.5) * per - cx, (z + 0.5) * per - cz) > r:
                    continue
                key = f"{x},{z}"
                if int(self.base[z, x]) == b["value"]:
                    self._edits.pop(key, None)
                else:
                    self._edits[key] = b["value"]
                self.cells[z, x] = b["value"]
        s.blockers_changed()

    def changed(self) -> np.ndarray:
        """The cells (z, x) the edits changed, as a boolean array."""
        return np.zeros((0, 0), bool) if self.cells is None else self.cells != self.base


class SurfEdits(ValueEdits):
    """The sea map's coast waves: 0 calm water up to 255 full waves, a cell per 2 x 2 tiles."""

    GRID = ValueGrid("surf_map", "surf", N_("islands.surf_lower"))


class EnvironmentEdits(ValueEdits):
    """The environment grid: the zone (the game's ``EnvironmentType``) of each 4 x 4 tiles."""

    GRID = ValueGrid("environment_map", "environment", N_("islands.environment_lower"))
