"""A world file (``.www``) or a random-map template (``.tww``) of Anno 2070, 1404 or 1701 as the same editable record
a map template is: its size, its islands as fixed islands (a random-map template's placeholders as random slots of
the game's edges), its start places (2070's Ark slots, 1701's player positions) as start positions.

``read(data, game)`` gives a ``World``; ``render()`` writes the tag tree back (``isd2070``: byte for byte when nothing
changed), its island and start lists rebuilt from the elements in their order. The field names per game:
docs/map-editor.md "Anno 2070, 1404 and 1701: world files" and "random-map templates".
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import isd2070
from .template import KIND_FIXED, KIND_SLOT, KIND_START, Element

NEW = {"x": "PositionX", "z": "PositionZ", "w": "Width", "h": "Height", "rot": "Direction", "file": "Filename",
       "name": "Name", "type": "Type"}  # fmt: skip
OLD = {"x": "m_XPosition", "z": "m_ZPosition", "w": "m_Width", "h": "m_Height", "rot": "m_IslandDirection",
       "file": "m_GOPFilename", "name": "m_Name", "type": ""}  # fmt: skip
STARTS = ("ArkSlots", "PlayerPositions")  # 2070's and 1701's start places, lists of x, y
BITS_1701 = {"cultures": "Cultures", "fertilities": "Fertilities", "resources": "Resources"}  # field -> tag
RANDOM = "RandomMapTemplate"
# the placeholders' edges of each game's random-map templates (every vanilla one is square, read 2026-11-13)
RANDOM_EDGES = {
    "anno2070": (96, 128, 144, 160, 208, 256),
    "anno1404": (112, 160, 208),
    "anno1701": (64, 96, 128, 160),
}


@dataclass
class World:
    game: str
    root: isd2070.Node = field(repr=False)
    size: tuple[int, int] = (0, 0)
    elements: list[Element] = field(default_factory=list)
    names: dict = field(default_factory=lambda: dict(NEW), repr=False)
    extents: dict[int, tuple[int, int]] = field(default_factory=dict, repr=False)  # id(node) -> Width, Height
    entries: dict[int, tuple] = field(default_factory=dict, repr=False)  # id(node) -> (key, value) of its entry
    random: bool = False  # a random-map template (.tww)
    edge_of: Callable[[Element], int] | None = field(default=None, repr=False)  # an island file's edge

    @property
    def config(self) -> isd2070.Node:
        return next(n for n in self.root.iter() if n.name in ("WorldConfig", RANDOM))

    @property
    def playable(self) -> tuple[int, int, int, int]:
        return (0, 0, *self.size)

    initial = None

    @property
    def edges(self) -> tuple[int, ...]:
        return RANDOM_EDGES.get(self.game, (128,)) if self.random else ()

    def extent(self, e: Element) -> int:
        """The edge of an element's square: as read while its file (or size) is the one read, else 0 for a fixed
        island (the caller asks the file) and the game's edge for a placeholder."""
        w, h = self.extents.get(id(e.tag), (0, 0))
        same = e.read_as is not None and id(e.tag) in self.extents
        if e.kind == KIND_SLOT and self.random:
            if same and e.size == e.read_as["size"]:
                return max(w, h)
            return self.edges[min(e.size, len(self.edges) - 1)]
        if e.kind == KIND_FIXED and same and e.map_file_path == e.read_as["map_file_path"]:
            return max(w, h)
        return 0

    def render(self) -> bytes:
        cfg = self.config
        size_names = ("m_Width", "m_Height") if self.names is OLD else ("Width", "Height")
        for name, value in zip(size_names, self.size, strict=True):
            _set(cfg, name, value)
        islands = _islands_node(cfg)
        if islands is not None:
            self._rebuild_islands(islands)
        elif any(e.kind != KIND_START for e in self.elements):
            raise ValueError("this map has no island list to add to")
        self._rebuild_starts(_starts_node(cfg))
        return isd2070.serialize(self.root)

    # the island list
    def _rebuild_islands(self, islands: isd2070.Node) -> None:
        kind = KIND_SLOT if self.random else KIND_FIXED
        listed = []
        for e in (e for e in self.elements if e.kind == kind):
            if e.tag is None or id(e.tag) not in self.entries:
                self._new_entry(e, islands)
                if self.random and not e.clime:  # a new placeholder takes the climate of the one it copies
                    e.clime = e.tag.int("Climes") or e.tag.int("Clime")
                for name, tag in BITS_1701.items() if self.random else ():  # and its bits
                    setattr(e, name, getattr(e, name) or e.tag.int(tag))
            key, value = self.entries[id(e.tag)]
            (self._sync_placeholder if self.random else self._sync_island)(e)
            listed += [key, value] if key is not None else [value]
        held = {id(n) for pair in self.entries.values() for n in pair if n is not None}
        first = next((i for i, c in enumerate(islands.children) if id(c) in held), len(islands.children))
        rest = [c for c in islands.children if id(c) not in held]
        islands.children = rest[:first] + listed + rest[first:]

    def _new_entry(self, e: Element, islands: isd2070.Node) -> None:
        """An entry for a new element: a copy of the last entry (with the element's own node when it is a copy of
        another element's) under the next free key."""
        if not self.entries:
            raise ValueError("this map has no island to copy the new one's record from")
        key, value = list(self.entries.values())[-1]
        value = copy.deepcopy(value)
        inner = _inner(value, self.random)
        if e.tag is not None:  # nodes compare by value: find the copied one by identity
            parent = next(n for n in value.iter() if any(c is inner for c in n.children))
            parent.children = [e.tag if c is inner else c for c in parent.children]
        else:
            e.tag = inner
        if key is not None:
            key = copy.deepcopy(key)
            key.text = str(
                max((_number(k.text) or 0 for k, _v in self.entries.values() if k is not None), default=0) + 1
            )
        self.entries[id(e.tag)] = (key, value)

    def _sync_island(self, e: Element) -> None:
        n = self.names
        _set(e.tag, n["x"], e.x)
        _set(e.tag, n["z"], e.z)
        _set(e.tag, n["rot"], e.rotation90 % 4)
        new_file = e.read_as is None or e.map_file_path != e.read_as.get("map_file_path")
        if (
            e.map_file_path
            and new_file
            and e.tag.value(n["file"], "").replace("\\", "/").lower() != e.map_file_path.lower()
        ):
            _set(e.tag, n["file"], e.map_file_path)
            if self.names is OLD and e.tag.find("m_TerrainFilename") is not None:
                _set(e.tag, "m_TerrainFilename", e.map_file_path)
        edge = self.edge_of(e) if new_file and self.edge_of is not None else 0
        if edge:  # a new or swapped island takes its own size (every vanilla entry is square)
            _set(e.tag, n["w"], edge)
            _set(e.tag, n["h"], edge)
            self.extents[id(e.tag)] = (edge, edge)
        elif new_file:
            self.extents.pop(id(e.tag), None)
        if e.island_label and e.tag.value(n["name"]) != e.island_label:
            _set(e.tag, n["name"], e.island_label)
        if n["type"] and (e.tag.find(n["type"]) is not None or e.type):
            _set(e.tag, n["type"], e.type)
        e.read_as = e.values()

    def _sync_placeholder(self, e: Element) -> None:
        edge = self.extent(e)
        rect = e.tag.find("Rect")
        for name, value in (("left", e.x), ("top", e.z), ("right", e.x + edge), ("bottom", e.z + edge)):
            _set(rect, name, value)
        self.extents[id(e.tag)] = (edge, edge)
        if e.tag.find("Type") is not None or e.type:
            _set(e.tag, "Type", e.type)
        clime = "Clime" if e.tag.find("Clime") is not None else "Climes"
        if e.tag.find(clime) is not None or e.clime:
            _set(e.tag, clime, e.clime)
        for name, tag in BITS_1701.items():
            if e.tag.find(tag) is not None or getattr(e, name):
                _set(e.tag, tag, getattr(e, name))
        e.read_as = e.values()

    # the start list
    def _rebuild_starts(self, starts: isd2070.Node | None) -> None:
        mine = [e for e in self.elements if e.kind == KIND_START]
        if starts is None:
            if mine:
                raise ValueError("this map has no start places")
            return
        for e in mine:
            if e.tag is None:
                e.tag = _new_start(starts)
            _set(e.tag, "x", e.x)
            _set(e.tag, "y", e.z)
        first = next((i for i, c in enumerate(starts.children) if c.name == "i"), len(starts.children))
        rest = [c for c in starts.children if c.name != "i"]
        starts.children = rest[:first] + [e.tag for e in mine] + rest[first:]


def _number(text: str | None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _set(node: isd2070.Node, name: str, value) -> None:
    """A child's text set where it differs (a vanilla value keeps its spelling: ``0`` stays ``0``); a new child is
    written in the style of its neighbours (1701's short closing tags)."""
    child = node.find(name)
    text = str(value)
    if child is None:
        like = node.children[-1] if node.children else node
        node.children.append(isd2070.Node(name, text, short=like.short, tail=like.tail))
    elif child.text is None or child.text.strip() != text and _number(child.text) != value:
        child.text = text


def _new_start(starts: isd2070.Node) -> isd2070.Node:
    """A start place: a copy of the list's last one, or a new ``i`` in the list's style."""
    held = starts.findall("i")
    if held:
        return copy.deepcopy(held[-1])
    node = isd2070.Node("i", short=starts.short, tail=starts.tail)
    for name in ("x", "y"):
        node.children.append(isd2070.Node(name, "0", short=starts.short, tail=starts.tail))
    return node


def _islands_node(cfg: isd2070.Node) -> isd2070.Node | None:
    for name in ("IslandTemplates", "Islands", "m_Islands"):
        node = cfg.find(name)
        if node is not None and (name != "Islands" or cfg.name != RANDOM):
            return node
    return None


def _starts_node(cfg: isd2070.Node) -> isd2070.Node | None:
    return next((cfg.find(n) for n in STARTS if cfg.find(n) is not None), None)


def _inner(value: isd2070.Node, random: bool) -> isd2070.Node | None:
    name = "Island" if random else "IslandConfig"
    return next((n for n in value.iter() if n.name == name), None)


def _entries(islands: isd2070.Node, random: bool) -> list[tuple[isd2070.Node | None, isd2070.Node, isd2070.Node]]:
    """(key, value, the island's node) of each entry of the island map, in file order."""
    out, key = [], None
    for c in islands.children:
        if c.name.startswith("k"):
            key = c
            continue
        inner = _inner(c, random)
        if inner is not None:
            out.append((key, c, inner))
        key = None
    return out


def _read_island(world: World, c: isd2070.Node) -> Element:
    n = world.names
    e = Element(KIND_FIXED, c.int(n["x"]), c.int(n["z"]), tag=c)
    e.rotation90 = c.int(n["rot"]) % 4
    e.map_file_path = c.value(n["file"], "").replace("\\", "/").lower()
    e.island_label = c.value(n["name"], "") or ""
    e.type = c.int(n["type"]) if n["type"] else 0
    world.extents[id(c)] = (c.int(n["w"]), c.int(n["h"]))
    return e


def _read_placeholder(world: World, c: isd2070.Node) -> Element:
    r = c.find("Rect")
    x, z = (r.int("left"), r.int("top")) if r is not None else (0, 0)
    w, h = (r.int("right") - x, r.int("bottom") - z) if r is not None else (0, 0)
    edges = world.edges
    size = min(range(len(edges)), key=lambda i: abs(edges[i] - max(w, h)))
    e = Element(KIND_SLOT, x, z, size=size, type=c.int("Type"), tag=c)
    e.clime = c.int("Climes") if c.find("Climes") is not None else c.int("Clime")
    for name, tag in BITS_1701.items():
        setattr(e, name, c.int(tag))
    world.extents[id(c)] = (w, h)
    return e


def read(data: bytes, game: str) -> World:
    root = isd2070.parse(data)
    cfg = next((n for n in root.iter() if n.name in ("WorldConfig", RANDOM)), None)
    if cfg is None:
        raise ValueError("not a world file or random-map template: no WorldConfig")
    random = cfg.name == RANDOM
    names = OLD if cfg.find("m_Width") is not None else NEW
    size_names = ("m_Width", "m_Height") if names is OLD else ("Width", "Height")
    world = World(game, root, (cfg.int(size_names[0]), cfg.int(size_names[1])), names=names, random=random)
    islands = _islands_node(cfg)
    for key, value, inner in _entries(islands, random) if islands is not None else []:
        e = (_read_placeholder if random else _read_island)(world, inner)
        e.read_as = e.values()
        world.entries[id(inner)] = (key, value)
        world.elements.append(e)
    starts = _starts_node(cfg)
    for i in starts.findall("i") if starts is not None else []:
        e = Element(KIND_START, i.int("x"), i.int("y"), tag=i)
        e.read_as = e.values()
        world.elements.append(e)
    return world
