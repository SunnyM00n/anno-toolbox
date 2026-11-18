"""The open map: a template of Anno 117 or 1800, a world or random-map template of 2070, 1404 or 1701, its elements'
squares, complexes and pictures, the checks, and one undo stack over every edit (move, turn, mirror, add, remove,
values, size, playable area, growth).

``open_map(tree, game, path, maps)`` is the entry; the window edits through ``MapDoc`` so every change can be undone.
docs/map-editor.md "The window".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..infra.i18n import tr
from . import grow, placing, pool, rules, template, worlds
from .references import KEPT, MOVED, Report
from .template import KIND_FIXED, KIND_SLOT, Element, MapTemplate

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
    "mine_slot_activation": (),
    "fertility_set_guids": (),
    "fertilities_per_area_index": (),
}  # they name the old island's objects


def map_paths(tree, game: str) -> list[str]:
    """The maps of a game the app opens: templates (117, 1800), worlds and random-map templates (2070, 1404, 1701),
    sessions (2205)."""
    want = SUFFIXES.get(game, ())
    return [p for p in tree.paths if want and p.endswith(want)]


@dataclass
class _State:
    elements: list[tuple[Element, dict]]
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    initial: tuple[int, int, int, int] | None
    grown: object


@dataclass
class MapDoc:
    game: str
    path: str
    record: MapTemplate | worlds.World
    original: bytes
    maps: pool.GameMaps
    tree: object = field(repr=False)
    grown: grow.Grown | None = None  # the last growth's files and report
    undos: list[_State] = field(default_factory=list, repr=False)
    redos: list[_State] = field(default_factory=list, repr=False)
    extra_files: dict[str, pool.IslandInfo] = field(default_factory=dict)  # island files from outside the game
    seams: set[tuple] = field(default_factory=set)  # the complexes' seams as the maps had them (``placing.seams``)

    @property
    def is_template(self) -> bool:
        return isinstance(self.record, MapTemplate)

    @property
    def elements(self) -> list[Element]:
        return self.record.elements

    @property
    def size(self) -> tuple[int, int]:
        return self.record.size

    @property
    def playable(self) -> tuple[int, int, int, int]:
        return self.record.playable

    @property
    def initial(self) -> tuple[int, int, int, int] | None:
        return self.record.initial if self.is_template else None

    def size_names(self) -> list[str]:
        """What a slot's size index means here, as shown: the size classes, or a random-map template's edges."""
        if not self.is_template and self.record.random:
            return [tr("maps.edge_tiles", edge=edge) for edge in self.record.edges]
        return [tr(s) for s in template.SIZES]

    def size_index(self, e: Element) -> int:
        """The size class shown for an element: a slot's stored one; a fixed island stores none, so its own (a
        continental by its file, else its pool entry's, else the smallest class its grid fits)."""
        if e.kind != KIND_FIXED or (not self.is_template and self.record.random):
            return e.size
        if rules.is_continental(e):
            return template.SIZE_CONTINENTAL
        key = e.map_file_path.replace("\\", "/").lower()
        pooled = next((p.size_class for p in self.maps.pool if p.path == key), None)
        if pooled is not None:
            return pooled
        edge = self.extent(e)
        fits = [c for c, most in sorted(self.maps.classes.items()) if edge <= most]
        return fits[0] if fits else max(self.maps.classes, default=template.SIZE_SMALL)

    # the squares, complexes and pictures
    def extent(self, e: Element) -> int:
        """The edge of an element's grid: a fixed island's file, a slot's size class, 0 for a start position."""
        if e.kind == template.KIND_START:
            return 0
        own = self.record.extent(e) if not self.is_template else 0
        if e.kind == KIND_FIXED:
            return own or self.info(e.map_file_path).size or self.maps.classes.get(0, 256)
        return own or self.maps.classes.get(e.size, self.maps.classes.get(0, 256))

    def info(self, path: str) -> pool.IslandInfo:
        key = path.replace("\\", "/").lower()
        return self.extra_files.get(key) or self.maps.island(self.tree, key)

    def land(self, e: Element) -> tuple[int, int, int, int] | None:
        """The part of the map an element takes (a fixed island's active area turned with it, a slot's square), in
        tiles; None for a start position."""
        if e.kind == template.KIND_START:
            return None
        edge = self.extent(e)
        active = self.info(e.map_file_path).active if e.kind == KIND_FIXED else None
        if active is None:
            return placing.grid(e, edge)
        x0, z0, x1, z1 = placing.turned(active, edge, e.rotation90)
        return (e.x + x0, e.z + z0, e.x + x1, e.z + z1)

    def complexes(self) -> list[list[int]]:
        return placing.complexes(self.elements, self.extent)

    def learn_seams(self, elements: list[Element] | None = None) -> None:
        """Take the complexes of ``elements`` (default the map's own) as designed: their islands may meet."""
        self.seams |= placing.seams(self.elements if elements is None else elements, self.extent)

    def joined(self, elements: list[Element]):
        """Whether two of ``elements`` (by index) meet at a designed seam."""

        def test(i: int, j: int) -> bool:
            a, b = elements[i], elements[j]
            return placing.seam(a, b, self.extent(a), self.extent(b)) in self.seams

        return test

    def picture(self, e: Element) -> str:
        return pool.picture_path(self.tree, e.map_file_path) if e.kind == KIND_FIXED and e.map_file_path else ""

    # the undo stack
    def _state(self) -> _State:
        r = self.record
        return _State([(e, e.values()) for e in r.elements], r.size, r.playable, self.initial, self.grown)

    def _restore(self, s: _State) -> None:
        r = self.record
        r.elements[:] = [e for e, _v in s.elements]
        for e, values in s.elements:
            for key, value in values.items():
                setattr(e, key, value)
        r.size = s.size
        if self.is_template:  # a world's playable area is the world
            r.playable, r.initial = s.playable, s.initial
        self.grown = s.grown

    def edit(self, change) -> None:
        """Run ``change(record)`` as one undoable step."""
        self.undos.append(self._state())
        del self.undos[:-UNDO_DEPTH]
        self.redos.clear()
        change(self.record)

    def undo(self) -> bool:
        if not self.undos:
            return False
        self.redos.append(self._state())
        self._restore(self.undos.pop())
        return True

    def redo(self) -> bool:
        if not self.redos:
            return False
        self.undos.append(self._state())
        self._restore(self.redos.pop())
        return True

    # edits
    def move(self, indexes: list[int], dx: int, dz: int) -> None:
        def change(r):
            for i in indexes:
                r.elements[i].x += dx
                r.elements[i].z += dz

        self.edit(change)

    def rotate(self, indexes: list[int]) -> None:
        """A quarter turn: one fixed island in place, several elements as a group about their middle."""
        chosen = [self.elements[i] for i in indexes]
        if len(chosen) > 1:
            self.edit(lambda _r: placing.turn_group(chosen, self.extent))
        elif chosen and chosen[0].kind == KIND_FIXED:
            self.edit(lambda _r: setattr(chosen[0], "rotation90", (chosen[0].rotation90 + 1) % 4))

    def remove(self, indexes: list[int]) -> None:
        gone = set(indexes)
        self.edit(lambda r: r.elements.__setitem__(slice(None), [e for i, e in enumerate(r.elements) if i not in gone]))

    def add(self, element: Element) -> int:
        return self.add_all([element])[0]

    def add_all(self, elements: list[Element]) -> list[int]:
        """Elements added as one step; their indexes."""
        first = len(self.elements)
        self.edit(lambda r: r.elements.extend(elements))
        return list(range(first, first + len(elements)))

    def duplicate(self, indexes: list[int]) -> list[int]:
        copies = [self.elements[i].copy() for i in indexes]
        for c in copies:
            c.x += COPY_OFFSET
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
            for e, m in zip(chosen, made, strict=True):
                e.x, e.z, e.rotation90 = m.x, m.z, m.rotation90

        self.edit(change)
        return list(indexes)

    def set_values(self, index: int, values: dict) -> None:
        """The fields of one element a person may change (a slot: place, size, type, climate, 1701's bits; a fixed
        island: place, MapFilePath, Rotation90, IslandLabel, type, FertilityGuids, RandomizeFertilities, mine
        slots, areas); a new island file clears the old one's
        slots."""
        e = self.elements[index]
        slot = {"size", "type", "clime", "cultures", "fertilities", "resources"}
        keep = {"x", "z"} | (slot if e.kind == KIND_SLOT else set())
        if e.kind == KIND_FIXED:
            keep |= {
                "type",
                "rotation90",
                "map_file_path",
                "island_label",
                "fertility_guids",
                "randomize_fertilities",
                "mine_slot_activation",
                "fertility_set_guids",
            }
            keep |= {"fertilities_per_area_index"}
        changed = {k: v for k, v in values.items() if k in keep and getattr(e, k) != v}
        if _same_file(changed.get("map_file_path"), e.map_file_path):  # the same island, spelled another way
            del changed["map_file_path"]
        if "map_file_path" in changed:
            changed = {**CLEARED_WITH_FILE, **changed}
        if changed:
            self.edit(lambda _r: [setattr(e, k, v) for k, v in changed.items()])

    def set_area(self, size: tuple[int, int] | None = None, playable=None, initial=None) -> None:
        """The map's size and, for a template, its playable areas (None: kept) as one step; none when all are kept."""
        new_size = self.size if size is None else tuple(size)
        areas = self.is_template  # a world's playable area is the world
        new_playable = tuple(playable) if areas and playable is not None else self.playable
        new_initial = tuple(initial) if areas and initial is not None else self.initial
        if (new_size, new_playable, new_initial) == (tuple(self.size), tuple(self.playable), self.initial):
            return

        def change(r):
            r.size = new_size
            if areas:
                r.playable, r.initial = new_playable, new_initial

        self.edit(change)

    def grow(self, sides: tuple[int, int, int, int], widen: bool = True, scripts: dict | None = None) -> grow.Grown:
        """Grow the map (templates; a world only by its size and positions) as one undoable step."""
        grow.check_sides(sides)
        if not self.is_template:
            return self._grow_world(sides)
        parts = self._grow_parts(widen, scripts or {})
        before, previous = self._state(), self.grown
        try:
            result = grow.grow_map(parts, sides)
        except Exception:  # the template is grown first: a part that fails leaves the map as it was
            self._restore(before)
            raise
        if previous is not None and not result.asset_ops:  # an east or north growth: the earlier move still holds
            result.asset_ops = list(previous.asset_ops)
        self.undos.append(before)
        del self.undos[:-UNDO_DEPTH]
        self.redos.clear()
        self.grown = result
        return result

    def is_enlarged(self) -> bool:
        """Whether the map is its ``MapTemplate`` asset's enlarged template (117's DLC maps)."""
        asset = self.maps.asset_of(self.path)
        return bool(asset and asset.enlarged and pool.stem_of(asset.enlarged) == pool.stem_of(self.path))

    def _grow_parts(self, widen: bool, scripts: dict) -> grow.GrowParts:
        stem = self.path.rsplit(".", 1)[0]
        session = self.grown.files.get(".a7t") if self.grown else None
        level = self.grown.files.get(".a7te") if self.grown else None
        if session is None and self.tree.exists(stem + ".a7t"):
            session = self.tree.read(stem + ".a7t")
        if level is None and self.tree.exists(stem + ".a7te"):
            level = self.tree.read(stem + ".a7te")
        asset = self.maps.asset_of(self.path)
        text = (self.grown.asset_text if self.grown and self.grown.asset_text else asset.text) if asset else ""
        earlier = self.grown.moved_assets if self.grown else {}
        return grow.GrowParts(
            self.record, self.path, session, level, text, asset.guid if asset else 0, scripts, widen,
            self.is_enlarged(), self._asset_positions(), frozenset(t.guid for t in self.maps.templates), earlier,
        )  # fmt: skip

    def _asset_positions(self) -> grow.AssetPositions:
        """The position blocks of the game's asset lists, read on the first growth and kept with the pool."""
        if self.maps.positions is None:
            lists = (self.tree.read(p).decode("utf-8", "replace") for p in self.tree.paths if p.endswith("assets.xml"))
            self.maps.positions = grow.scan_assets(lists)
        return self.maps.positions

    def _grow_world(self, sides: tuple[int, int, int, int]) -> grow.Grown:
        west, south, east, north = sides
        report = Report(sides)

        def change(r):
            r.size = (r.size[0] + west + east, r.size[1] + south + north)
            for e in r.elements:
                e.x, e.z = e.x + west, e.z + south

        self.edit(change)
        report.size = self.size
        report.add(self.path, "Width, Height", MOVED, str(self.size))
        report.add(self.path, "islands and start places", MOVED, "x and z", len(self.elements))
        report.add(self.path, "Areas", KEPT, "script areas are not moved")
        return grow.Grown({}, [], report)

    # checks and output
    def facts(self, elements: list[Element] | None = None) -> rules.Facts:
        """What the checks look at, for the map's elements or others of the same map (a derived difficulty)."""
        counts: Counter = Counter()
        region = self._region()
        names = {v: k for k, v in template.TYPE_KEYS.items()}
        for p in self.maps.pool:
            if not region or p.region == region:
                counts[(names.get(p.type, 0), p.size_class)] += 1
        exists = self.tree.exists
        elements = self.elements if elements is None else elements
        return rules.Facts(
            self.game,
            self.size,
            self.playable,
            elements,
            self.land,
            lambda p: exists(p.lower()) or p.lower() in self.extra_files,
            counts,
            rules.is_continental,
            self.joined(elements),
        )

    def _region(self) -> str:
        asset = self.maps.asset_of(self.path) if self.is_template else None
        return asset.region if asset else ""

    def check(self) -> list[rules.Finding]:
        return rules.check(self.facts())

    def render(self) -> bytes:
        return self.record.render()

    def changed(self) -> bool:
        return self.grown is not None or self.render() != self.original


def _same_file(a: str | None, b: str) -> bool:
    """Whether two game paths name one file (the games read them without case, with either slash)."""
    return a is not None and a.replace("\\", "/").lower() == b.replace("\\", "/").lower()


def open_map(tree, game: str, path: str, maps: pool.GameMaps) -> MapDoc:
    """Read a map of ``game`` at the game path ``path``; ``ValueError`` when it is not one this app reads."""
    data = tree.read(path)
    if game in TEMPLATE_GAMES and path.endswith(".a7tinfo"):
        record = template.read(data)
    elif game in WORLD_GAMES and path.endswith((".www", ".tww")):
        record = worlds.read(data, game)
    else:
        raise ValueError(f"{path}: not a map the map editor opens")
    doc = MapDoc(game, path, record, data, maps, tree)
    if not doc.is_template:
        record.edge_of = lambda e: doc.info(e.map_file_path).size
    doc.learn_seams()
    return doc
