"""The open map: a template of Anno 117 or 1800, a world or random-map template of 2070, 1404 or 1701, its elements'
squares, complexes and pictures, the checks, and one undo stack over every edit (move, turn, mirror, add, remove,
values, size, playable area, growth).

``open_map(tree, game, path, maps)`` is the entry; the window edits through ``CMapDoc`` so every change can be undone.
docs/map-editor.md "The window".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..infra.i18n import tr
from . import grow, placing, pool, rules, template, worlds
from .references import KEPT, MOVED, CReport
from .template import KIND_FIXED, KIND_SLOT, SElement, SMapTemplate

TEMPLATE_GAMES = ("anno117", "anno1800")
WORLD_GAMES = ("anno2070", "anno1404", "anno1701")
SESSION_GAMES = ("anno2205",)  # one baked map per session: shown, not placed
SUFFIXES = {
    **dict.fromkeys(TEMPLATE_GAMES, (".a7tinfo",)),
    **dict.fromkeys(WORLD_GAMES, (".www", ".tww")),
    "anno2205": (".a6m",),
}
UNDO_DEPTH = 200
COPY_OFFSET = template.SNAP * 4  # a duplicate lies this far east of what it copies
CLEARED_WITH_FILE = {
    "MineSlotActivation": (),
    "FertilitySetGUIDs": (),
    "FertilitiesPerAreaIndex": (),
}  # they name the old island's objects


EDITABLE = {  # the fields of an element a person may change, per kind
    KIND_SLOT: frozenset({"x", "z", "size", "type", "clime", "cultures", "fertilities", "resources"}),
    KIND_FIXED: frozenset({
        "x", "z", "type", "Rotation90", "MapFilePath", "IslandLabel", "FertilityGuids", "RandomizeFertilities",
        "MineSlotActivation", "FertilitySetGUIDs", "FertilitiesPerAreaIndex",
    }),
    template.KIND_START: frozenset({"x", "z"}),
}  # fmt: skip


def map_paths(tree, game: str) -> list[str]:
    """The maps of a game the app opens: templates (117, 1800), worlds and random-map templates (2070, 1404, 1701),
    sessions (2205)."""
    want = SUFFIXES.get(game, ())
    return [path for path in tree.m_paths if want and path.endswith(want)]


@dataclass
class _SState:
    elements: list[tuple[SElement, dict]]
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    initial: tuple[int, int, int, int] | None
    grown: object


@dataclass
class CMapDoc:
    m_game: str
    m_path: str
    m_record: SMapTemplate | worlds.CWorld
    m_original: bytes
    m_maps: pool.CGameMaps
    m_tree: object = field(repr=False)
    m_grown: grow.SGrown | None = None  # the last growth's files and report
    m_undos: list[_SState] = field(default_factory=list, repr=False)
    m_redos: list[_SState] = field(default_factory=list, repr=False)
    m_extra_files: dict[str, pool.SIslandInfo] = field(default_factory=dict)  # island files from outside the game
    m_seams: set[tuple] = field(default_factory=set)  # the complexes' seams as the maps had them (``placing.seams``)

    @property
    def is_template(self) -> bool:
        return isinstance(self.m_record, SMapTemplate)

    @property
    def elements(self) -> list[SElement]:
        return self.m_record.elements

    @property
    def size(self) -> tuple[int, int]:
        return self.m_record.size

    @property
    def playable(self) -> tuple[int, int, int, int]:
        return self.m_record.playable

    @property
    def initial(self) -> tuple[int, int, int, int] | None:
        return self.m_record.initial if self.is_template else None

    def size_names(self) -> list[str]:
        """What a slot's size index means here, as shown: the size classes, or a random-map template's edges."""
        if not self.is_template and self.m_record.random:
            return [tr("maps.edge_tiles", edge=edge) for edge in self.m_record.edges]
        return [tr(size_name) for size_name in template.SIZES]

    def size_index(self, element: SElement) -> int:
        """The size class shown for an element: a slot's stored one; a fixed island stores none, so its own (a
        continental by its file, else its pool entry's, else the smallest class its grid fits)."""
        if element.kind != KIND_FIXED or (not self.is_template and self.m_record.random):
            return element.size
        if rules.is_continental(element):
            return template.SIZE_CONTINENTAL
        key = element.MapFilePath.replace("\\", "/").lower()
        pooled = next((island.size_class for island in self.m_maps.m_pool if island.path == key), None)
        if pooled is not None:
            return pooled
        edge = self.extent(element)
        fits = [size_class for size_class, most in sorted(self.m_maps.m_classes.items()) if edge <= most]
        return fits[0] if fits else max(self.m_maps.m_classes, default=template.SIZE_SMALL)

    # the squares, complexes and pictures
    def extent(self, element: SElement) -> int:
        """The edge of an element's grid: a fixed island's file, a slot's size class, 0 for a start position."""
        if element.kind == template.KIND_START:
            return 0
        own = self.m_record.extent(element) if not self.is_template else 0
        if element.kind == KIND_FIXED:
            return own or self.info(element.MapFilePath).size or self.m_maps.m_classes.get(0, 256)
        return own or self.m_maps.m_classes.get(element.size, self.m_maps.m_classes.get(0, 256))

    def info(self, path: str) -> pool.SIslandInfo:
        key = path.replace("\\", "/").lower()
        return self.m_extra_files.get(key) or self.m_maps.island(self.m_tree, key)

    def land(self, element: SElement) -> tuple[int, int, int, int] | None:
        """The part of the map an element takes (a fixed island's active area turned with it, a slot's square), in
        tiles; None for a start position."""
        if element.kind == template.KIND_START:
            return None
        edge = self.extent(element)
        active = self.info(element.MapFilePath).active if element.kind == KIND_FIXED else None
        if active is None:
            return placing.grid(element, edge)
        x0, z0, x1, z1 = placing.turned(active, edge, element.Rotation90)
        return (element.x + x0, element.z + z0, element.x + x1, element.z + z1)

    def complexes(self) -> list[list[int]]:
        return placing.complexes(self.elements, self.extent)

    def learn_seams(self, elements: list[SElement] | None = None) -> None:
        """Take the complexes of ``elements`` (default the map's own) as designed: their islands may meet."""
        self.m_seams |= placing.seams(self.elements if elements is None else elements, self.extent)

    def joined(self, elements: list[SElement]):
        """Whether two of ``elements`` (by index) meet at a designed seam."""

        def test(i: int, j: int) -> bool:
            first, second = elements[i], elements[j]
            return placing.seam(first, second, self.extent(first), self.extent(second)) in self.m_seams

        return test

    def picture(self, element: SElement) -> str:
        return (
            pool.picture_path(self.m_tree, element.MapFilePath)
            if element.kind == KIND_FIXED and element.MapFilePath
            else ""
        )

    # the undo stack
    def _state(self) -> _SState:
        record = self.m_record
        return _SState(
            [(element, element.values()) for element in record.elements],
            record.size,
            record.playable,
            self.initial,
            self.m_grown,
        )

    def _restore(self, state: _SState) -> None:
        record = self.m_record
        record.elements[:] = [element for element, _v in state.elements]
        for element, values in state.elements:
            for key, value in values.items():
                setattr(element, key, value)
        record.size = state.size
        if self.is_template:  # a world's playable area is the world
            record.playable, record.initial = state.playable, state.initial
        self.m_grown = state.grown

    def edit(self, change) -> None:
        """Run ``change(record)`` as one undoable step."""
        self.m_undos.append(self._state())
        del self.m_undos[:-UNDO_DEPTH]
        self.m_redos.clear()
        change(self.m_record)

    def undo(self) -> bool:
        if not self.m_undos:
            return False
        self.m_redos.append(self._state())
        self._restore(self.m_undos.pop())
        return True

    def redo(self) -> bool:
        if not self.m_redos:
            return False
        self.m_undos.append(self._state())
        self._restore(self.m_redos.pop())
        return True

    # edits
    def move(self, indexes: list[int], dx: int, dz: int) -> None:
        def change(record):
            for i in indexes:
                record.elements[i].x += dx
                record.elements[i].z += dz

        self.edit(change)

    def rotate(self, indexes: list[int]) -> None:
        """A quarter turn: one fixed island in place, several elements as a group about their middle."""
        chosen = [self.elements[i] for i in indexes]
        if len(chosen) > 1:
            self.edit(lambda _r: placing.turn_group(chosen, self.extent))
        elif chosen and chosen[0].kind == KIND_FIXED:
            self.edit(lambda _r: setattr(chosen[0], "Rotation90", (chosen[0].Rotation90 + 1) % 4))

    def remove(self, indexes: list[int]) -> None:
        gone = set(indexes)
        self.edit(
            lambda record: record.elements.__setitem__(
                slice(None), [element for i, element in enumerate(record.elements) if i not in gone]
            )
        )

    def add(self, element: SElement) -> int:
        return self.add_all([element])[0]

    def add_all(self, elements: list[SElement]) -> list[int]:
        """Elements added as one step; their indexes."""
        first = len(self.elements)
        self.edit(lambda record: record.elements.extend(elements))
        return list(range(first, first + len(elements)))

    def duplicate(self, indexes: list[int]) -> list[int]:
        copies = [self.elements[i].copy() for i in indexes]
        for copy in copies:
            copy.x += COPY_OFFSET
        return self.add_all(copies)

    def mirror(self, indexes: list[int], mode: str, copies: bool = True) -> list[int]:
        """Mirrored copies of the elements across the playable area's centre (``placing.MIRRORS``), or the elements
        themselves mirrored in place (not for "xz"); the indexes of what was made or moved."""
        x0, z0, x1, z1 = self.playable
        centre = ((x0 + x1) / 2, (z0 + z1) / 2)
        chosen = [self.elements[i] for i in indexes]
        made = placing.mirror_copies(chosen, mode, centre, self.extent)
        if copies or mode == "xz":
            return self.add_all(made)

        def change(_r):
            for element, mirrored in zip(chosen, made, strict=True):
                element.x, element.z, element.Rotation90 = mirrored.x, mirrored.z, mirrored.Rotation90

        self.edit(change)
        return list(indexes)

    def set_values(self, index: int, values: dict) -> None:
        """The fields of one element a person may change (a slot: place, size, type, climate, 1701's bits; a fixed
        island: place, MapFilePath, Rotation90, IslandLabel, type, FertilityGuids, RandomizeFertilities, mine
        slots, areas); a new island file clears the old one's
        slots."""
        element = self.elements[index]
        keep = EDITABLE.get(element.kind, EDITABLE[template.KIND_START])
        changed = {k: value for k, value in values.items() if k in keep and getattr(element, k) != value}
        if _same_file(changed.get("MapFilePath"), element.MapFilePath):  # the same island, spelled another way
            del changed["MapFilePath"]
        if "MapFilePath" in changed:
            changed = {**CLEARED_WITH_FILE, **changed}
        if changed:
            self.edit(lambda _r: [setattr(element, k, value) for k, value in changed.items()])

    def set_area(self, size_tiles: tuple[int, int] | None = None, playable=None, initial=None) -> None:
        """The map's size and, for a template, its playable areas (None: kept) as one step; none when all are kept."""
        new_size = self.size if size_tiles is None else tuple(size_tiles)
        areas = self.is_template  # a world's playable area is the world
        new_playable = tuple(playable) if areas and playable is not None else self.playable
        new_initial = tuple(initial) if areas and initial is not None else self.initial
        if (new_size, new_playable, new_initial) == (tuple(self.size), tuple(self.playable), self.initial):
            return

        def change(record):
            record.size = new_size
            if areas:
                record.playable, record.initial = new_playable, new_initial

        self.edit(change)

    def grow(self, sides: tuple[int, int, int, int], widen: bool = True, scripts: dict | None = None) -> grow.SGrown:
        """Grow the map (templates; a world only by its size and positions) as one undoable step."""
        grow.check_sides(sides)
        if not self.is_template:
            return self._grow_world(sides)
        parts = self._grow_parts(widen, scripts or {})
        before, previous = self._state(), self.m_grown
        try:
            result = grow.grow_map(parts, sides)
        except Exception:  # the template is grown first: a part that fails leaves the map as it was
            self._restore(before)
            raise
        if previous is not None and not result.asset_ops:  # an east or north growth: the earlier move still holds
            result.asset_ops = list(previous.asset_ops)
        self.m_undos.append(before)
        del self.m_undos[:-UNDO_DEPTH]
        self.m_redos.clear()
        self.m_grown = result
        return result

    def is_enlarged(self) -> bool:
        """Whether the map is its ``SMapTemplate`` asset's enlarged template (117's DLC maps)."""
        asset = self.m_maps.asset_of(self.m_path)
        return bool(asset and asset.enlarged and pool.stem_of(asset.enlarged) == pool.stem_of(self.m_path))

    def _grow_parts(self, widen: bool, scripts: dict) -> grow.SGrowParts:
        stem = self.m_path.rsplit(".", 1)[0]
        session = self.m_grown.files.get(".a7t") if self.m_grown else None
        level = self.m_grown.files.get(".a7te") if self.m_grown else None
        if session is None and self.m_tree.exists(stem + ".a7t"):
            session = self.m_tree.read(stem + ".a7t")
        if level is None and self.m_tree.exists(stem + ".a7te"):
            level = self.m_tree.read(stem + ".a7te")
        asset = self.m_maps.asset_of(self.m_path)
        text = (self.m_grown.asset_text if self.m_grown and self.m_grown.asset_text else asset.text) if asset else ""
        earlier = self.m_grown.moved_assets if self.m_grown else {}
        return grow.SGrowParts(
            self.m_record, self.m_path, session, level, text, asset.guid if asset else 0, scripts, widen,
            self.is_enlarged(), self._asset_positions(), frozenset(map_template.guid for map_template in self.m_maps.m_templates), earlier,
        )  # fmt: skip

    def _asset_positions(self) -> grow.SAssetPositions:
        """The position blocks of the game's asset lists, read on the first growth and kept with the pool."""
        if self.m_maps.m_positions is None:
            lists = (
                self.m_tree.read(path).decode("utf-8", "replace")
                for path in self.m_tree.m_paths
                if path.endswith("assets.xml")
            )
            self.m_maps.m_positions = grow.scan_assets(lists)
        return self.m_maps.m_positions

    def _grow_world(self, sides: tuple[int, int, int, int]) -> grow.SGrown:
        west, south, east, north = sides
        report = CReport(sides)

        def change(record):
            record.size = (record.size[0] + west + east, record.size[1] + south + north)
            for element in record.elements:
                element.x, element.z = element.x + west, element.z + south

        self.edit(change)
        report.m_size = self.size
        report.add(self.m_path, "Width, Height", MOVED, str(self.size))
        report.add(self.m_path, "islands and start places", MOVED, "x and z", len(self.elements))
        report.add(self.m_path, "Areas", KEPT, "script areas are not moved")
        return grow.SGrown({}, [], report)

    # checks and output
    def facts(self, elements: list[SElement] | None = None) -> rules.SFacts:
        """What the checks look at, for the map's elements or others of the same map (a derived difficulty)."""
        counts: Counter = Counter()
        region = self._region()
        names = {value: k for k, value in template.TYPE_KEYS.items()}
        for island in self.m_maps.m_pool:
            if not region or island.region == region:
                counts[(names.get(island.type, 0), island.size_class)] += 1
        exists = self.m_tree.exists
        elements = self.elements if elements is None else elements
        return rules.SFacts(
            self.m_game,
            self.size,
            self.playable,
            elements,
            self.land,
            lambda island: exists(island.lower()) or island.lower() in self.m_extra_files,
            counts,
            rules.is_continental,
            self.joined(elements),
        )

    def _region(self) -> str:
        asset = self.m_maps.asset_of(self.m_path) if self.is_template else None
        return asset.region if asset else ""

    def check(self) -> list[rules.SFinding]:
        return rules.check(self.facts())

    def render(self) -> bytes:
        return self.m_record.render()

    def changed(self) -> bool:
        return self.m_grown is not None or self.render() != self.m_original


def _same_file(first: str | None, second: str) -> bool:
    """Whether two game paths name one file (the games read them without case, with either slash)."""
    return first is not None and first.replace("\\", "/").lower() == second.replace("\\", "/").lower()


def open_map(tree, game: str, path: str, maps: pool.CGameMaps) -> CMapDoc:
    """Read a map of ``game`` at the game path ``path``; ``ValueError`` when it is not one this app reads."""
    data = tree.read(path)
    if game in TEMPLATE_GAMES and path.endswith(".a7tinfo"):
        record = template.read(data)
    elif game in WORLD_GAMES and path.endswith((".www", ".tww")):
        record = worlds.read(data, game)
    else:
        raise ValueError(f"{path}: not a map the map editor opens")
    doc = CMapDoc(game, path, record, data, maps, tree)
    if not doc.is_template:
        record.edge_of = lambda element: doc.info(element.MapFilePath).size
    doc.learn_seams()
    return doc
