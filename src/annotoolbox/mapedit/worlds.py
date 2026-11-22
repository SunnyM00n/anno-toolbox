"""A world file (``.www``) or a random-map template (``.tww``) of Anno 2070, 1404 or 1701 as the same editable record
a map template is: its size, its islands as fixed islands (a random-map template's placeholders as random slots of
the game's edges), its start places (2070's Ark slots, 1701's player positions) as start positions.

``read(data, game)`` gives a ``CWorld``; ``render()`` writes the tag tree back (``isd2070``: byte for byte when nothing
changed), its island and start lists rebuilt from the elements in their order. The field names per game:
docs/map-editor.md "Anno 2070, 1404 and 1701: world files" and "random-map templates".
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import isd2070
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SElement

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
class CWorld:
    game: str
    root: isd2070.SNode = field(repr=False)
    size: tuple[int, int] = (0, 0)
    elements: list[SElement] = field(default_factory=list)
    names: dict = field(default_factory=lambda: dict(NEW), repr=False)
    extents: dict[int, tuple[int, int]] = field(default_factory=dict, repr=False)  # id(node) -> Width, Height
    entries: dict[int, tuple] = field(default_factory=dict, repr=False)  # id(node) -> (key, value) of its entry
    random: bool = False  # a random-map template (.tww)
    edge_of: Callable[[SElement], int] | None = field(default=None, repr=False)  # an island file's edge

    @property
    def config(self) -> isd2070.SNode:
        return next(node for node in self.root.iter() if node.name in ("WorldConfig", RANDOM))

    @property
    def playable(self) -> tuple[int, int, int, int]:
        return (0, 0, *self.size)

    initial = None

    @property
    def edges(self) -> tuple[int, ...]:
        return RANDOM_EDGES.get(self.game, (128,)) if self.random else ()

    def extent(self, element: SElement) -> int:
        """The edge of an element's square: as read while its file (or size) is the one read, else 0 for a fixed
        island (the caller asks the file) and the game's edge for a placeholder."""
        width_tiles, height_tiles = self.extents.get(id(element.tag), (0, 0))
        same = element.read_as is not None and id(element.tag) in self.extents
        if element.kind == KIND_SLOT and self.random:
            if same and element.size == element.read_as["size"]:
                return max(width_tiles, height_tiles)
            return self.edges[min(element.size, len(self.edges) - 1)]
        if element.kind == KIND_FIXED and same and element.MapFilePath == element.read_as["MapFilePath"]:
            return max(width_tiles, height_tiles)
        return 0

    def render(self) -> bytes:
        cfg = self.config
        size_names = ("m_Width", "m_Height") if self.names is OLD else ("Width", "Height")
        for name, value in zip(size_names, self.size, strict=True):
            _set(cfg, name, value)
        islands = _islands_node(cfg)
        if islands is not None:
            self._rebuild_islands(islands)
        elif any(element.kind != KIND_START for element in self.elements):
            raise ValueError("this map has no island list to add to")
        self._rebuild_starts(_starts_node(cfg))
        return isd2070.serialize(self.root)

    # the island list
    def _rebuild_islands(self, islands: isd2070.SNode) -> None:
        kind = KIND_SLOT if self.random else KIND_FIXED
        listed = []
        for element in (element for element in self.elements if element.kind == kind):
            if element.tag is None or id(element.tag) not in self.entries:
                self._new_entry(element, islands)
                if self.random and not element.clime:  # a new placeholder takes the climate of the one it copies
                    element.clime = element.tag.int("Climes") or element.tag.int("Clime")
                for name, tag in BITS_1701.items() if self.random else ():  # and its bits
                    setattr(element, name, getattr(element, name) or element.tag.int(tag))
            key, value = self.entries[id(element.tag)]
            (self._sync_placeholder if self.random else self._sync_island)(element)
            listed += [key, value] if key is not None else [value]
        held = {id(node) for pair in self.entries.values() for node in pair if node is not None}
        first = next((i for i, child in enumerate(islands.children) if id(child) in held), len(islands.children))
        rest = [child for child in islands.children if id(child) not in held]
        islands.children = rest[:first] + listed + rest[first:]

    def _new_entry(self, element: SElement, islands: isd2070.SNode) -> None:
        """An entry for a new element: a copy of the last entry (with the element's own node when it is a copy of
        another element's) under the next free key."""
        if not self.entries:
            raise ValueError("this map has no island to copy the new one's record from")
        key, value = list(self.entries.values())[-1]
        value = copy.deepcopy(value)
        inner = _inner(value, self.random)
        if element.tag is not None:  # nodes compare by value: find the copied one by identity
            parent = next(node for node in value.iter() if any(child is inner for child in node.children))
            parent.children = [element.tag if child is inner else child for child in parent.children]
        else:
            element.tag = inner
        if key is not None:
            key = copy.deepcopy(key)
            key.text = str(
                max((_number(k.text) or 0 for k, _v in self.entries.values() if k is not None), default=0) + 1
            )
        self.entries[id(element.tag)] = (key, value)

    def _sync_island(self, element: SElement) -> None:
        names_map = self.names
        _set(element.tag, names_map["x"], element.x)
        _set(element.tag, names_map["z"], element.z)
        _set(element.tag, names_map["rot"], element.Rotation90 % 4)
        new_file = element.read_as is None or element.MapFilePath != element.read_as.get("MapFilePath")
        if (
            element.MapFilePath
            and new_file
            and element.tag.value(names_map["file"], "").replace("\\", "/").lower() != element.MapFilePath.lower()
        ):
            _set(element.tag, names_map["file"], element.MapFilePath)
            if self.names is OLD and element.tag.find("m_TerrainFilename") is not None:
                _set(element.tag, "m_TerrainFilename", element.MapFilePath)
        edge = self.edge_of(element) if new_file and self.edge_of is not None else 0
        if edge:  # a new or swapped island takes its own size (every vanilla entry is square)
            _set(element.tag, names_map["w"], edge)
            _set(element.tag, names_map["h"], edge)
            self.extents[id(element.tag)] = (edge, edge)
        elif new_file:
            self.extents.pop(id(element.tag), None)
        if element.IslandLabel and element.tag.value(names_map["name"]) != element.IslandLabel:
            _set(element.tag, names_map["name"], element.IslandLabel)
        if names_map["type"] and (element.tag.find(names_map["type"]) is not None or element.type):
            _set(element.tag, names_map["type"], element.type)
        element.read_as = element.values()

    def _sync_placeholder(self, element: SElement) -> None:
        edge = self.extent(element)
        rect = element.tag.find("Rect")
        for name, value in (
            ("left", element.x),
            ("top", element.z),
            ("right", element.x + edge),
            ("bottom", element.z + edge),
        ):
            _set(rect, name, value)
        self.extents[id(element.tag)] = (edge, edge)
        if element.tag.find("Type") is not None or element.type:
            _set(element.tag, "Type", element.type)
        clime = "Clime" if element.tag.find("Clime") is not None else "Climes"
        if element.tag.find(clime) is not None or element.clime:
            _set(element.tag, clime, element.clime)
        for name, tag in BITS_1701.items():
            if element.tag.find(tag) is not None or getattr(element, name):
                _set(element.tag, tag, getattr(element, name))
        element.read_as = element.values()

    # the start list
    def _rebuild_starts(self, starts: isd2070.SNode | None) -> None:
        mine = [element for element in self.elements if element.kind == KIND_START]
        if starts is None:
            if mine:
                raise ValueError("this map has no start places")
            return
        for element in mine:
            if element.tag is None:
                element.tag = _new_start(starts)
            _set(element.tag, "x", element.x)
            _set(element.tag, "y", element.z)
        first = next((i for i, child in enumerate(starts.children) if child.name == "i"), len(starts.children))
        rest = [child for child in starts.children if child.name != "i"]
        starts.children = rest[:first] + [element.tag for element in mine] + rest[first:]


def _number(text: str | None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _set(node: isd2070.SNode, name: str, value) -> None:
    """A child's text set where it differs (a vanilla value keeps its spelling: ``0`` stays ``0``); a new child is
    written in the style of its neighbours (1701's short closing tags)."""
    child = node.find(name)
    text = str(value)
    if child is None:
        like = node.children[-1] if node.children else node
        node.children.append(isd2070.SNode(name, text, short=like.short, tail=like.tail))
    elif child.text is None or child.text.strip() != text and _number(child.text) != value:
        child.text = text


def _new_start(starts: isd2070.SNode) -> isd2070.SNode:
    """A start place: a copy of the list's last one, or a new ``i`` in the list's style."""
    held = starts.findall("i")
    if held:
        return copy.deepcopy(held[-1])
    node = isd2070.SNode("i", short=starts.short, tail=starts.tail)
    for name in ("x", "y"):
        node.children.append(isd2070.SNode(name, "0", short=starts.short, tail=starts.tail))
    return node


def _islands_node(cfg: isd2070.SNode) -> isd2070.SNode | None:
    for name in ("IslandTemplates", "Islands", "m_Islands"):
        node = cfg.find(name)
        if node is not None and (name != "Islands" or cfg.name != RANDOM):
            return node
    return None


def _starts_node(cfg: isd2070.SNode) -> isd2070.SNode | None:
    return next((cfg.find(name) for name in STARTS if cfg.find(name) is not None), None)


def _inner(value: isd2070.SNode, random: bool) -> isd2070.SNode | None:
    name = "Island" if random else "IslandConfig"
    return next((node for node in value.iter() if node.name == name), None)


def _entries(islands: isd2070.SNode, random: bool) -> list[tuple[isd2070.SNode | None, isd2070.SNode, isd2070.SNode]]:
    """(key, value, the island's node) of each entry of the island map, in file order."""
    out, key = [], None
    for child in islands.children:
        if child.name.startswith("k"):
            key = child
            continue
        inner = _inner(child, random)
        if inner is not None:
            out.append((key, child, inner))
        key = None
    return out


def _read_island(world: CWorld, node: isd2070.SNode) -> SElement:
    names_map = world.names
    element = SElement(KIND_FIXED, node.int(names_map["x"]), node.int(names_map["z"]), tag=node)
    element.Rotation90 = node.int(names_map["rot"]) % 4
    element.MapFilePath = node.value(names_map["file"], "").replace("\\", "/").lower()
    element.IslandLabel = node.value(names_map["name"], "") or ""
    element.type = node.int(names_map["type"]) if names_map["type"] else 0
    world.extents[id(node)] = (node.int(names_map["w"]), node.int(names_map["h"]))
    return element


def _read_placeholder(world: CWorld, node: isd2070.SNode) -> SElement:
    rect = node.find("Rect")
    x, z = (rect.int("left"), rect.int("top")) if rect is not None else (0, 0)
    width_tiles, height_tiles = (rect.int("right") - x, rect.int("bottom") - z) if rect is not None else (0, 0)
    edges = world.edges
    size = min(range(len(edges)), key=lambda i: abs(edges[i] - max(width_tiles, height_tiles)))
    element = SElement(KIND_SLOT, x, z, size=size, type=node.int("Type"), tag=node)
    element.clime = node.int("Climes") if node.find("Climes") is not None else node.int("Clime")
    for name, tag in BITS_1701.items():
        setattr(element, name, node.int(tag))
    world.extents[id(node)] = (width_tiles, height_tiles)
    return element


def read(data: bytes, game: str) -> CWorld:
    root = isd2070.parse(data)
    cfg = next((node for node in root.iter() if node.name in ("WorldConfig", RANDOM)), None)
    if cfg is None:
        raise ValueError("not a world file or random-map template: no WorldConfig")
    random = cfg.name == RANDOM
    names = OLD if cfg.find("m_Width") is not None else NEW
    size_names = ("m_Width", "m_Height") if names is OLD else ("Width", "Height")
    world = CWorld(game, root, (cfg.int(size_names[0]), cfg.int(size_names[1])), names=names, random=random)
    islands = _islands_node(cfg)
    for key, value, inner in _entries(islands, random) if islands is not None else []:
        element = (_read_placeholder if random else _read_island)(world, inner)
        element.read_as = element.values()
        world.entries[id(inner)] = (key, value)
        world.elements.append(element)
    starts = _starts_node(cfg)
    for i in starts.findall("i") if starts is not None else []:
        element = SElement(KIND_START, i.int("x"), i.int("y"), tag=i)
        element.read_as = element.values()
        world.elements.append(element)
    return world
