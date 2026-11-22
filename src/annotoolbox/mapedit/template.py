"""A map template of Anno 117 or Anno 1800 (``.a7tinfo``) as an editable record: its size, playable areas and elements
(fixed islands with their mine slots and per-area fertilities, random slots, start positions), written back byte for
byte when nothing changed.

``read(data)`` gives a ``SMapTemplate``; edit its ``elements`` and fields, then ``render()``. Only the values that
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
SIZES = (N_("islands.small"), N_("islands.medium"), N_("islands.large"), N_("maps.xl"), N_("islands.continental"))
SIZE_SMALL, SIZE_MEDIUM, SIZE_LARGE, SIZE_XL, SIZE_CONTINENTAL = range(5)
# IslandType ids, the same in both games
TYPES = {
    0: N_("maps.normal"),
    1: N_("maps.starter"),
    2: N_("maps.decoration"),
    3: N_("maps.third_party"),
    4: N_("maps.pirate"),
    5: N_("maps.cliff_island"),
    6: N_("maps.reserved_player"),
    7: N_("maps.volcanic"),
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
class SElement:
    """One template element. ``x``, ``z``: its grid's corner (a start position: the point), in tiles."""

    kind: int
    x: int
    z: int
    size: int = 0  # a slot's size class (``SIZES``; a random-map template's: an index into its game's edges)
    type: int = 0  # ``TYPES``: a slot's Config/Type, a fixed island's RandomIslandConfig type
    MapFilePath: str = ""  # a fixed island's file
    Rotation90: int = 0  # a fixed island's quarter turns
    IslandLabel: str = ""
    FertilityGuids: tuple[int, ...] = ()
    RandomizeFertilities: bool = False  # a fixed island's RandomizeFertilities
    MineSlotActivation: tuple[
        tuple[int, int], ...
    ] = ()  # (slot object id, value): 117's MineSlotActivation, 1800's MineSlotMapping
    FertilitySetGUIDs: tuple[tuple[int, int], ...] = ()  # (area index, FertilitySet GUID)
    FertilitiesPerAreaIndex: tuple[tuple[int, tuple[int, ...]], ...] = ()  # (area index, fertility GUIDs)
    clime: int = 0  # an older game's random-map placeholder: its climate bits
    cultures: int = 0  # 1701's random-map placeholder: its ``Cultures``, ``Fertilities`` and ``Resources`` bits
    fertilities: int = 0
    resources: int = 0
    tag: filedb.STag | None = field(default=None, repr=False, compare=False)
    read_as: dict | None = field(default=None, repr=False, compare=False)  # the values its node holds

    def values(self) -> dict:
        return {
            "kind": self.kind,
            "x": self.x,
            "z": self.z,
            "size": self.size,
            "type": self.type,
            "MapFilePath": self.MapFilePath,
            "Rotation90": self.Rotation90,
            "IslandLabel": self.IslandLabel,
            "FertilityGuids": tuple(self.FertilityGuids),
            "RandomizeFertilities": self.RandomizeFertilities,
            "MineSlotActivation": tuple(self.MineSlotActivation),
            "FertilitySetGUIDs": tuple(self.FertilitySetGUIDs),
            "FertilitiesPerAreaIndex": tuple(self.FertilitiesPerAreaIndex),
            "clime": self.clime,
            "cultures": self.cultures,
            "fertilities": self.fertilities,
            "resources": self.resources,
        }

    def copy(self) -> SElement:
        """A new element with the same values and its own copy of the node: only what changes later is written."""
        other = copy.copy(self)
        other.tag = copy.deepcopy(self.tag) if self.tag is not None else None
        other.read_as = dict(self.read_as) if self.read_as is not None and self.tag is not None else None
        return other


@dataclass
class SMapTemplate:
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    initial: tuple[int, int, int, int] | None  # None: the file has no InitialPlayableArea
    elements: list[SElement]
    doc: filedb.STag = field(repr=False)
    ids: tuple[dict, dict] = field(repr=False)
    version: int = 3
    read_as: dict = field(default_factory=dict, repr=False)
    originals: list[SElement] = field(default_factory=list, repr=False)  # the elements as read: models of new ones

    @property
    def root(self) -> filedb.STag:
        return self.doc.find("MapTemplate")

    def value(self, name: str) -> bytes | None:
        """A field of the template the record does not model (``EnlargementOffset``, ``IsEnlargedTemplate``)."""
        node = self.root.find(name)
        return node.data if isinstance(node, filedb.SAttrib) else None

    def render(self) -> bytes:
        """The ``.a7tinfo`` with the record's values: fields whose value the document already holds keep their bytes
        (``read_as`` follows what the document holds, so a later render writes only what changed since)."""
        root, held = self.root, self.read_as
        put(root, "Size", struct.pack("<2i", *self.size), held.get("size") != self.size)
        put(root, "PlayableArea", struct.pack("<4i", *self.playable), held.get("playable") != self.playable)
        if self.initial is not None:
            put(root, "InitialPlayableArea", struct.pack("<4i", *self.initial), held.get("initial") != self.initial)
        tags = [_sync(element, self) for element in self.elements]
        first = next((i for i, child in enumerate(root.children) if child.name == _ELEMENT), len(root.children))
        rest = [child for child in root.children if child.name != _ELEMENT]
        root.children = rest[:first] + tags + rest[first:]
        if len(self.elements) != held.get("count"):
            put(root, "ElementCount", struct.pack("<I", len(self.elements)))
        held.update(size=self.size, playable=self.playable, initial=self.initial, count=len(self.elements))
        return filedb.write(self.doc, *self.ids, version=self.version)

    def model_of(self, kind: int, sized: bool = False) -> filedb.STag | None:
        """The first element of ``kind`` of this template (else of those read), as the model of a new one: its game's
        own fields; ``sized``: a slot that holds a ``Size`` first, so a written size keeps the vanilla field order."""
        tags = [
            element.tag
            for element in (*self.elements, *self.originals)
            if element.kind == kind and element.tag is not None
        ]
        if sized:
            tags.sort(key=lambda tag: tag.path("Element/Size") is None)
        return tags[0] if tags else None

    @property
    def mine_field(self) -> str:
        """The name the template's islands keep their mine slots under (117's or 1800's), "" when none has one."""
        for element in self.elements:
            el = element.tag.find("Element") if element.tag is not None else None
            for name in MINE_VALUE:
                if el is not None and el.find(name) is not None:
                    return name
        return ""


def _u16(node) -> int:
    return struct.unpack("<H", node.data[:2])[0] if node is not None and len(node.data) >= 2 else 0


def _pairs(node, key: str, value: str | None) -> tuple:
    """A list of alternating key and value attributes as pairs (``struct`` formats; ``value`` None: a run of uint32
    as a tuple); () for no list."""
    raw = (
        [child.data for child in node.children if isinstance(child, filedb.SAttrib)]
        if isinstance(node, filedb.STag)
        else []
    )
    out = []
    for k, raw_value in zip(raw[0::2], raw[1::2], strict=False):
        run = struct.unpack(f"<{len(raw_value) // 4}I", raw_value[: len(raw_value) // 4 * 4]) if value is None else None
        out.append((struct.unpack_from(key, k)[0], run if value is None else struct.unpack_from(value, raw_value)[0]))
    return tuple(out)


def _fixed(out: SElement, tag: filedb.STag) -> None:
    path = tag.find("MapFilePath")
    out.MapFilePath = path.data.decode("utf-16-le", "replace").rstrip("\0") if path is not None else ""
    rot = tag.find("Rotation90")
    out.Rotation90 = rot.data[0] % 4 if rot is not None and rot.data else 0
    label = tag.find("IslandLabel")
    out.IslandLabel = label.data.decode("latin-1").rstrip("\0") if label is not None else ""
    fert = tag.find("FertilityGuids")
    if fert is not None:
        out.FertilityGuids = struct.unpack(f"<{len(fert.data) // 4}I", fert.data[: len(fert.data) // 4 * 4])
    rnd = tag.find("RandomizeFertilities")
    out.RandomizeFertilities = bool(rnd.data and rnd.data[0]) if rnd is not None else False
    out.type = _u16(tag.path("RandomIslandConfig/value/Type/id"))
    for name, value in MINE_VALUE.items():
        if tag.find(name) is not None:
            out.MineSlotActivation = _pairs(tag.find(name), "<Q", value)
    out.FertilitySetGUIDs = _pairs(tag.find(AREA_SETS), "<h", "<I")
    out.FertilitiesPerAreaIndex = _pairs(tag.find(AREA_FERTILITIES), "<h", None)


def _element(te: filedb.STag) -> SElement:
    et = te.find("ElementType")
    kind = et.u32() if isinstance(et, filedb.SAttrib) else KIND_FIXED
    tag = te.find("Element")
    if not isinstance(tag, filedb.STag):
        tag = filedb.STag("Element")
    position = tag.find("Position")
    x, z = struct.unpack("<2i", position.data[:8]) if position is not None else (0, 0)
    out = SElement(kind, x, z, tag=te)
    if kind == KIND_SLOT:
        out.size = _u16(tag.find("Size"))
        out.type = _u16(tag.path("Config/Type/id"))
    elif kind == KIND_FIXED:
        _fixed(out, tag)
    out.read_as = out.values()
    return out


def _ints(root: filedb.STag, name: str, count: int, default: tuple) -> tuple:
    node = root.find(name)
    ok = isinstance(node, filedb.SAttrib) and len(node.data) >= 4 * count
    return struct.unpack(f"<{count}i", node.data[: 4 * count]) if ok else default


def read(data: bytes) -> SMapTemplate:
    """A template's ``.a7tinfo`` bytes as a record; ``ValueError`` when it holds no ``SMapTemplate``."""
    doc = filedb.read(data)
    root = doc.find("MapTemplate")
    if not isinstance(root, filedb.STag):
        raise ValueError("not a map template: no MapTemplate in the file")
    size = _ints(root, "Size", 2, (0, 0))
    playable = _ints(root, "PlayableArea", 4, (0, 0, *size))
    initial = _ints(root, "InitialPlayableArea", 4, None) if root.find("InitialPlayableArea") is not None else None
    elements = [_element(te) for te in root.findall(_ELEMENT)]
    map_template = SMapTemplate(size, playable, initial, elements, doc, filedb.name_ids(data), filedb.version(data))
    map_template.read_as = {"size": size, "playable": playable, "initial": initial, "count": len(elements)}
    map_template.originals = list(elements)
    return map_template


def put(tag: filedb.STag, path: str, data: bytes, changed: bool = True) -> None:
    """The attribute at ``path`` set to ``data`` when ``changed``, made with the tags above it where missing."""
    if not changed:
        return
    *tags, name = path.split("/")
    for part in tags:
        child = tag.find(part)
        if not isinstance(child, filedb.STag):
            child = filedb.STag(part, [])
            tag.children.append(child)
        tag = child
    node = tag.find(name)
    if isinstance(node, filedb.SAttrib):
        node.data = data
    else:
        tag.children.append(filedb.SAttrib(name, data))


def _drop(tag: filedb.STag | None, name: str) -> None:
    if tag is not None:
        tag.children = [child for child in tag.children if child.name != name]


def _put_pairs(tag: filedb.STag, name: str, pairs: tuple, key: str, value: str | None) -> None:
    """``_pairs`` written back under ``name``; a list the element lacks is only made when it holds something."""
    node = tag.find(name)
    if not isinstance(node, filedb.STag):
        if not pairs:
            return
        node = filedb.STag(name, [])
        tag.children.append(node)
    node.children = []
    for k, pair_value in pairs:
        data = struct.pack(f"<{len(pair_value)}I", *pair_value) if value is None else struct.pack(value, pair_value)
        node.children += [filedb.SAttrib(NONE, struct.pack(key, k)), filedb.SAttrib(NONE, data)]


def _new_tag(map_element: SElement, map_template: SMapTemplate) -> filedb.STag:
    """A node for a new element: a copy of one of the template's own of that kind, else the vanilla shape."""
    model = map_template.model_of(map_element.kind, map_element.kind == KIND_SLOT and bool(map_element.size))
    if model is not None:
        return copy.deepcopy(model)
    if map_element.kind == KIND_START:
        return filedb.STag(_ELEMENT, [filedb.SAttrib("ElementType", struct.pack("<I", 2)), filedb.STag("Element", [])])
    if map_element.kind == KIND_SLOT:
        config = filedb.STag("Config", [filedb.STag("Type", []), filedb.STag("Difficulty", [])])
        element = filedb.STag("Element", [filedb.STag("Difficulty", []), config])
        return filedb.STag(_ELEMENT, [filedb.SAttrib("ElementType", struct.pack("<I", 1)), element])
    config = filedb.STag(
        "RandomIslandConfig", [filedb.STag("value", [filedb.STag("Type", []), filedb.STag("Difficulty", [])])]
    )
    return filedb.STag(_ELEMENT, [filedb.STag("Element", [config])])


def _sync_slot(el: filedb.STag, element: SElement, differs) -> None:
    if differs("size"):
        if element.size:
            put(el, "Size", struct.pack("<H", element.size))
        else:
            _drop(el, "Size")
    if differs("type"):
        _drop(el.path("Config/Type"), "id")
        if element.type:
            put(el, "Config/Type/id", struct.pack("<H", element.type))


def _sync_fixed(el: filedb.STag, element: SElement, differs, made: bool) -> None:
    put(el, "MapFilePath", element.MapFilePath.encode("utf-16-le"), differs("MapFilePath"))
    put(el, "Rotation90", bytes([element.Rotation90 % 4]), differs("Rotation90"))
    put(el, "IslandLabel", element.IslandLabel.encode("latin-1", "replace"), differs("IslandLabel"))
    fert = struct.pack(f"<{len(element.FertilityGuids)}I", *element.FertilityGuids)
    put(el, "FertilityGuids", fert, differs("FertilityGuids"))
    put(
        el,
        "RandomizeFertilities",
        b"\x01" if element.RandomizeFertilities else b"\x00",
        differs("RandomizeFertilities"),
    )
    put(el, "RandomIslandConfig/value/Type/id", struct.pack("<H", element.type), differs("type"))
    if made:
        put(el, "RandomIslandConfig/value/Difficulty/id", struct.pack("<H", 0))
    name = next((mine_name for mine_name in MINE_VALUE if el.find(mine_name) is not None), "")
    if name and differs("MineSlotActivation"):
        _put_pairs(el, name, element.MineSlotActivation, "<Q", MINE_VALUE[name])
    if differs("FertilitySetGUIDs"):
        _put_pairs(el, AREA_SETS, element.FertilitySetGUIDs, "<h", "<I")
    if differs("FertilitiesPerAreaIndex"):
        _put_pairs(el, AREA_FERTILITIES, element.FertilitiesPerAreaIndex, "<h", None)


def _sync(element: SElement, map_template: SMapTemplate) -> filedb.STag:
    """The element's node with every value that differs from what was read written into it."""
    made = element.tag is None
    fresh = made or element.read_as is None
    if made:
        element.tag = _new_tag(element, map_template)
    was = {} if fresh else element.read_as
    now = element.values()

    def differs(key: str) -> bool:
        return fresh or was.get(key) != now[key]

    el = element.tag.find("Element")
    if not isinstance(el, filedb.STag):
        el = filedb.STag("Element", [])
        element.tag.children.append(el)
    put(el, "Position", struct.pack("<2i", element.x, element.z), differs("x") or differs("z"))
    if element.kind == KIND_SLOT:
        _sync_slot(el, element, differs)
    elif element.kind == KIND_FIXED:
        _sync_fixed(el, element, differs, made)
    element.read_as = now
    return element.tag


def new_element(kind: int, x: int, z: int, **values) -> SElement:
    """An element to add (its node is made from the template's own when it is written)."""
    return SElement(kind, snap(x), snap(z), **values)


def snap(value: float) -> int:
    return int(round(value / SNAP)) * SNAP
