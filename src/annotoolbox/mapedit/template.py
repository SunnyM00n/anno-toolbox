"""A map template of Anno 117 or Anno 1800 (``.a7tinfo``) as an editable record: its size, playable areas and elements
(fixed islands with their mine slots and per-area fertilities, random slots, start positions), written back byte for
byte when nothing changed.

``read(data)`` gives a ``MapTemplate``; edit its ``elements`` and fields, then ``render()``. Only the values that
differ from what was read are written into the document, so every field the record does not model stays as it was.
docs/map-editor.md "The files per game" and "Mine slots and fertilities".
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass, field

from ..formats import filedb
from ..infra.i18n import N_

KIND_FIXED, KIND_SLOT, KIND_START = 0, 1, 2
# a slot's stored Size: absent is Small; Continental only on fixed islands of 117's DLC (docs/map-editor.md)
SIZES = (N_("Small"), N_("Medium"), N_("Large"), N_("XL"), N_("Continental"))
SIZE_SMALL, SIZE_MEDIUM, SIZE_LARGE, SIZE_XL, SIZE_CONTINENTAL = range(5)
# IslandType ids, the same in both games
TYPES = {
    0: N_("Normal"),
    1: N_("Starter"),
    2: N_("Decoration"),
    3: N_("Third party"),
    4: N_("Pirate"),
    5: N_("Cliff island"),
    6: N_("Reserved for a player"),
    7: N_("Volcanic"),
}
TYPE_KEYS = {0: "Normal", 1: "Starter", 2: "Decoration", 3: "ThirdParty", 4: "PirateIsland", 5: "CliffIsland",
             6: "ReservedForPlayer", 7: "VolcanicIsland"}  # fmt: skip  # the names the asset list uses
TYPE_STARTER = 1
SNAP = 8  # vanilla positions lie on 8 tiles
_ELEMENT = "TemplateElement"
NONE = "#32768"  # the name of a list's unnamed attributes
# a fixed island's maps, each a list alternating key and value (docs/map-editor.md "Mine slots and fertilities")
MINE_SLOT_ACTIVATION, MINE_SLOT_MAPPING = "MineSlotActivation", "MineSlotMapping"
MINE_VALUE = {MINE_SLOT_ACTIVATION: "<B", MINE_SLOT_MAPPING: "<I"}  # 117: active 1 / 0; 1800: the Slot asset (0 random)
AREA_SETS, AREA_FERTILITIES = "FertilitySetGUIDs", "FertilitiesPerAreaIndex"


@dataclass
class Element:
    """One template element. ``x``, ``z``: its grid's corner (a start position: the point), in tiles."""

    kind: int
    x: int
    z: int
    size: int = 0  # a slot's size class (``SIZES``; a random-map template's: an index into its game's edges)
    type: int = 0  # ``TYPES``: a slot's Config/Type, a fixed island's RandomIslandConfig type
    map_file_path: str = ""  # a fixed island's file
    rotation90: int = 0  # a fixed island's quarter turns
    island_label: str = ""
    fertility_guids: tuple[int, ...] = ()
    randomize_fertilities: bool = False  # a fixed island's RandomizeFertilities
    mine_slot_activation: tuple[
        tuple[int, int], ...
    ] = ()  # (slot object id, value): 117's MineSlotActivation, 1800's MineSlotMapping
    fertility_set_guids: tuple[tuple[int, int], ...] = ()  # (area index, FertilitySet GUID)
    fertilities_per_area_index: tuple[tuple[int, tuple[int, ...]], ...] = ()  # (area index, fertility GUIDs)
    clime: int = 0  # an older game's random-map placeholder: its climate bits
    cultures: int = 0  # 1701's random-map placeholder: its ``Cultures``, ``Fertilities`` and ``Resources`` bits
    fertilities: int = 0
    resources: int = 0
    tag: filedb.Tag | None = field(default=None, repr=False, compare=False)
    read_as: dict | None = field(default=None, repr=False, compare=False)  # the values its node holds

    def values(self) -> dict:
        return {
            "kind": self.kind,
            "x": self.x,
            "z": self.z,
            "size": self.size,
            "type": self.type,
            "map_file_path": self.map_file_path,
            "rotation90": self.rotation90,
            "island_label": self.island_label,
            "fertility_guids": tuple(self.fertility_guids),
            "randomize_fertilities": self.randomize_fertilities,
            "mine_slot_activation": tuple(self.mine_slot_activation),
            "fertility_set_guids": tuple(self.fertility_set_guids),
            "fertilities_per_area_index": tuple(self.fertilities_per_area_index),
            "clime": self.clime,
            "cultures": self.cultures,
            "fertilities": self.fertilities,
            "resources": self.resources,
        }

    def copy(self) -> Element:
        """A new element with the same values and its own copy of the node: only what changes later is written."""
        other = copy.copy(self)
        other.tag = copy.deepcopy(self.tag) if self.tag is not None else None
        other.read_as = dict(self.read_as) if self.read_as is not None and self.tag is not None else None
        return other


@dataclass
class MapTemplate:
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    initial: tuple[int, int, int, int] | None  # None: the file has no InitialPlayableArea
    elements: list[Element]
    doc: filedb.Tag = field(repr=False)
    ids: tuple[dict, dict] = field(repr=False)
    version: int = 3
    read_as: dict = field(default_factory=dict, repr=False)
    originals: list[Element] = field(default_factory=list, repr=False)  # the elements as read: models of new ones

    @property
    def root(self) -> filedb.Tag:
        return self.doc.find("MapTemplate")

    def value(self, name: str) -> bytes | None:
        """A field of the template the record does not model (``EnlargementOffset``, ``IsEnlargedTemplate``)."""
        node = self.root.find(name)
        return node.data if isinstance(node, filedb.Attrib) else None

    def render(self) -> bytes:
        """The ``.a7tinfo`` with the record's values: fields whose value the document already holds keep their bytes
        (``read_as`` follows what the document holds, so a later render writes only what changed since)."""
        root, held = self.root, self.read_as
        put(root, "Size", struct.pack("<2i", *self.size), held.get("size") != self.size)
        put(root, "PlayableArea", struct.pack("<4i", *self.playable), held.get("playable") != self.playable)
        if self.initial is not None:
            put(root, "InitialPlayableArea", struct.pack("<4i", *self.initial), held.get("initial") != self.initial)
        tags = [_sync(e, self) for e in self.elements]
        first = next((i for i, c in enumerate(root.children) if c.name == _ELEMENT), len(root.children))
        rest = [c for c in root.children if c.name != _ELEMENT]
        root.children = rest[:first] + tags + rest[first:]
        if len(self.elements) != held.get("count"):
            put(root, "ElementCount", struct.pack("<I", len(self.elements)))
        held.update(size=self.size, playable=self.playable, initial=self.initial, count=len(self.elements))
        return filedb.write(self.doc, *self.ids, version=self.version)

    def model_of(self, kind: int, sized: bool = False) -> filedb.Tag | None:
        """The first element of ``kind`` of this template (else of those read), as the model of a new one: its game's
        own fields; ``sized``: a slot that holds a ``Size`` first, so a written size keeps the vanilla field order."""
        tags = [e.tag for e in (*self.elements, *self.originals) if e.kind == kind and e.tag is not None]
        if sized:
            tags.sort(key=lambda t: t.path("Element/Size") is None)
        return tags[0] if tags else None

    @property
    def mine_field(self) -> str:
        """The name the template's islands keep their mine slots under (117's or 1800's), "" when none has one."""
        for e in self.elements:
            el = e.tag.find("Element") if e.tag is not None else None
            for name in MINE_VALUE:
                if el is not None and el.find(name) is not None:
                    return name
        return ""


def _u16(node) -> int:
    return struct.unpack("<H", node.data[:2])[0] if node is not None and len(node.data) >= 2 else 0


def _pairs(node, key: str, value: str | None) -> tuple:
    """A list of alternating key and value attributes as pairs (``struct`` formats; ``value`` None: a run of uint32
    as a tuple); () for no list."""
    raw = [c.data for c in node.children if isinstance(c, filedb.Attrib)] if isinstance(node, filedb.Tag) else []
    out = []
    for k, v in zip(raw[0::2], raw[1::2], strict=False):
        run = struct.unpack(f"<{len(v) // 4}I", v[: len(v) // 4 * 4]) if value is None else None
        out.append((struct.unpack_from(key, k)[0], run if value is None else struct.unpack_from(value, v)[0]))
    return tuple(out)


def _fixed(out: Element, e: filedb.Tag) -> None:
    path = e.find("MapFilePath")
    out.map_file_path = path.data.decode("utf-16-le", "replace").rstrip("\0") if path is not None else ""
    rot = e.find("Rotation90")
    out.rotation90 = rot.data[0] % 4 if rot is not None and rot.data else 0
    label = e.find("IslandLabel")
    out.island_label = label.data.decode("latin-1").rstrip("\0") if label is not None else ""
    fert = e.find("FertilityGuids")
    if fert is not None:
        out.fertility_guids = struct.unpack(f"<{len(fert.data) // 4}I", fert.data[: len(fert.data) // 4 * 4])
    rnd = e.find("RandomizeFertilities")
    out.randomize_fertilities = bool(rnd.data and rnd.data[0]) if rnd is not None else False
    out.type = _u16(e.path("RandomIslandConfig/value/Type/id"))
    for name, value in MINE_VALUE.items():
        if e.find(name) is not None:
            out.mine_slot_activation = _pairs(e.find(name), "<Q", value)
    out.fertility_set_guids = _pairs(e.find(AREA_SETS), "<h", "<I")
    out.fertilities_per_area_index = _pairs(e.find(AREA_FERTILITIES), "<h", None)


def _element(te: filedb.Tag) -> Element:
    et = te.find("ElementType")
    kind = et.u32() if isinstance(et, filedb.Attrib) else KIND_FIXED
    e = te.find("Element")
    if not isinstance(e, filedb.Tag):
        e = filedb.Tag("Element")
    pos = e.find("Position")
    x, z = struct.unpack("<2i", pos.data[:8]) if pos is not None else (0, 0)
    out = Element(kind, x, z, tag=te)
    if kind == KIND_SLOT:
        out.size = _u16(e.find("Size"))
        out.type = _u16(e.path("Config/Type/id"))
    elif kind == KIND_FIXED:
        _fixed(out, e)
    out.read_as = out.values()
    return out


def _ints(root: filedb.Tag, name: str, count: int, default: tuple) -> tuple:
    node = root.find(name)
    ok = isinstance(node, filedb.Attrib) and len(node.data) >= 4 * count
    return struct.unpack(f"<{count}i", node.data[: 4 * count]) if ok else default


def read(data: bytes) -> MapTemplate:
    """A template's ``.a7tinfo`` bytes as a record; ``ValueError`` when it holds no ``MapTemplate``."""
    doc = filedb.read(data)
    root = doc.find("MapTemplate")
    if not isinstance(root, filedb.Tag):
        raise ValueError("not a map template: no MapTemplate in the file")
    size = _ints(root, "Size", 2, (0, 0))
    playable = _ints(root, "PlayableArea", 4, (0, 0, *size))
    initial = _ints(root, "InitialPlayableArea", 4, None) if root.find("InitialPlayableArea") is not None else None
    elements = [_element(te) for te in root.findall(_ELEMENT)]
    t = MapTemplate(size, playable, initial, elements, doc, filedb.name_ids(data), filedb.version(data))
    t.read_as = {"size": size, "playable": playable, "initial": initial, "count": len(elements)}
    t.originals = list(elements)
    return t


def put(tag: filedb.Tag, path: str, data: bytes, changed: bool = True) -> None:
    """The attribute at ``path`` set to ``data`` when ``changed``, made with the tags above it where missing."""
    if not changed:
        return
    *tags, name = path.split("/")
    for part in tags:
        child = tag.find(part)
        if not isinstance(child, filedb.Tag):
            child = filedb.Tag(part, [])
            tag.children.append(child)
        tag = child
    node = tag.find(name)
    if isinstance(node, filedb.Attrib):
        node.data = data
    else:
        tag.children.append(filedb.Attrib(name, data))


def _drop(tag: filedb.Tag | None, name: str) -> None:
    if tag is not None:
        tag.children = [c for c in tag.children if c.name != name]


def _put_pairs(tag: filedb.Tag, name: str, pairs: tuple, key: str, value: str | None) -> None:
    """``_pairs`` written back under ``name``; a list the element lacks is only made when it holds something."""
    node = tag.find(name)
    if not isinstance(node, filedb.Tag):
        if not pairs:
            return
        node = filedb.Tag(name, [])
        tag.children.append(node)
    node.children = []
    for k, v in pairs:
        data = struct.pack(f"<{len(v)}I", *v) if value is None else struct.pack(value, v)
        node.children += [filedb.Attrib(NONE, struct.pack(key, k)), filedb.Attrib(NONE, data)]


def _new_tag(e: Element, t: MapTemplate) -> filedb.Tag:
    """A node for a new element: a copy of one of the template's own of that kind, else the vanilla shape."""
    model = t.model_of(e.kind, e.kind == KIND_SLOT and bool(e.size))
    if model is not None:
        return copy.deepcopy(model)
    if e.kind == KIND_START:
        return filedb.Tag(_ELEMENT, [filedb.Attrib("ElementType", struct.pack("<I", 2)), filedb.Tag("Element", [])])
    if e.kind == KIND_SLOT:
        config = filedb.Tag("Config", [filedb.Tag("Type", []), filedb.Tag("Difficulty", [])])
        element = filedb.Tag("Element", [filedb.Tag("Difficulty", []), config])
        return filedb.Tag(_ELEMENT, [filedb.Attrib("ElementType", struct.pack("<I", 1)), element])
    config = filedb.Tag(
        "RandomIslandConfig", [filedb.Tag("value", [filedb.Tag("Type", []), filedb.Tag("Difficulty", [])])]
    )
    return filedb.Tag(_ELEMENT, [filedb.Tag("Element", [config])])


def _sync_slot(el: filedb.Tag, e: Element, differs) -> None:
    if differs("size"):
        if e.size:
            put(el, "Size", struct.pack("<H", e.size))
        else:
            _drop(el, "Size")
    if differs("type"):
        _drop(el.path("Config/Type"), "id")
        if e.type:
            put(el, "Config/Type/id", struct.pack("<H", e.type))


def _sync_fixed(el: filedb.Tag, e: Element, differs, made: bool) -> None:
    put(el, "MapFilePath", e.map_file_path.encode("utf-16-le"), differs("map_file_path"))
    put(el, "Rotation90", bytes([e.rotation90 % 4]), differs("rotation90"))
    put(el, "IslandLabel", e.island_label.encode("latin-1", "replace"), differs("island_label"))
    fert = struct.pack(f"<{len(e.fertility_guids)}I", *e.fertility_guids)
    put(el, "FertilityGuids", fert, differs("fertility_guids"))
    put(el, "RandomizeFertilities", b"\x01" if e.randomize_fertilities else b"\x00", differs("randomize_fertilities"))
    put(el, "RandomIslandConfig/value/Type/id", struct.pack("<H", e.type), differs("type"))
    if made:
        put(el, "RandomIslandConfig/value/Difficulty/id", struct.pack("<H", 0))
    name = next((n for n in MINE_VALUE if el.find(n) is not None), "")
    if name and differs("mine_slot_activation"):
        _put_pairs(el, name, e.mine_slot_activation, "<Q", MINE_VALUE[name])
    if differs("fertility_set_guids"):
        _put_pairs(el, AREA_SETS, e.fertility_set_guids, "<h", "<I")
    if differs("fertilities_per_area_index"):
        _put_pairs(el, AREA_FERTILITIES, e.fertilities_per_area_index, "<h", None)


def _sync(e: Element, t: MapTemplate) -> filedb.Tag:
    """The element's node with every value that differs from what was read written into it."""
    made = e.tag is None
    fresh = made or e.read_as is None
    if made:
        e.tag = _new_tag(e, t)
    was = {} if fresh else e.read_as
    now = e.values()

    def differs(key: str) -> bool:
        return fresh or was.get(key) != now[key]

    el = e.tag.find("Element")
    if not isinstance(el, filedb.Tag):
        el = filedb.Tag("Element", [])
        e.tag.children.append(el)
    put(el, "Position", struct.pack("<2i", e.x, e.z), differs("x") or differs("z"))
    if e.kind == KIND_SLOT:
        _sync_slot(el, e, differs)
    elif e.kind == KIND_FIXED:
        _sync_fixed(el, e, differs, made)
    e.read_as = now
    return e.tag


def new_element(kind: int, x: int, z: int, **values) -> Element:
    """An element to add (its node is made from the template's own when it is written)."""
    return Element(kind, snap(x), snap(z), **values)


def snap(v: float) -> int:
    return int(round(v / SNAP)) * SNAP
