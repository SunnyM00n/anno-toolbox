"""One glTF mesh node as an RDM mesh: its primitives as submeshes, its attributes written into a vertex layout.

``mesh`` is the entry (``gltfrdm`` calls it per mesh node). The layout comes from ``--layout``, the extras, the
``like`` file or the attributes; an element the layout needs and the document lacks is computed where the format
allows it (normals and tangent frames, noted) and refused where it does not (texture coordinates, colours, bones).
An attribute the layout has no place for is dropped and noted (docs/rdm-gltf.md "Import").
"""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass

from ..formats import gltf, rdmlayout
from ..formats.rdmread import SMesh, SSubmesh, SVertexElement
from ..formats.rdmwrite import pack_direction
from ..models import meshxform, tangents
from . import rigmath
from .gltfdoc import CGltfImportError, SDoc, tags
from .gltfskin import SSkin


@dataclass
class _SVertices:
    """The primitives of a mesh as one vertex set: attribute name -> values, the triangles, the submesh ranges."""

    values: dict[str, list]
    kinds: dict[str, tuple[int, bool]]  # attribute -> (component type, normalized)
    indices: list[int]
    ranges: list[tuple[int, int, int]]  # first index, count, material

    @property
    def count(self) -> int:
        return len(self.values["POSITION"])


def mesh(document: SDoc, node: int, materials: list[int], skins: list[SSkin]) -> SMesh:
    """The RDM mesh of mesh node ``node``; ``materials`` are the file's glTF materials in RDM order."""
    gltf_node = document.node(node)
    gltf_mesh = document.gltf.items("meshes")[gltf_node["mesh"]]
    mesh_tags = tags(gltf_mesh)
    name = gltf_node.get("name") or gltf_mesh.get("name") or mesh_tags.get("name", "")
    who = f"mesh {name!r}"
    skin = (
        next((candidate for candidate in skins if candidate.gltf_skin == gltf_node["skin"]), None)
        if "skin" in gltf_node
        else None
    )
    gathered = _gather(document, gltf_mesh.get("primitives", []), materials, who)
    space = rigmath.MIRROR_X if document.mirror else rigmath.IDENTITY
    if skin is None and document.options.transform:
        space = rigmath.multiply(space, document.world(node))
    elements, from_tags = _layout(document, mesh_tags, gathered, skin is not None, name, who)
    linear = tuple(tuple(space[i][j] for j in range(3)) for i in range(3))
    indices = meshxform.reversed_winding(gathered.indices) if meshxform.mirrored(linear) else gathered.indices
    columns = _CColumns(document, gathered, elements, from_tags, mesh_tags, space, indices, skin, who)
    rows = [columns.element(k) for k in range(len(elements))]
    vertices = b"".join(b"".join(rows[k][i] for k in range(len(elements))) for i in range(gathered.count))
    _dropped(document, gathered, elements, mesh_tags, who)
    stride = rdmlayout.stride_of(elements)
    positions = [
        rdmlayout.decode(elements[0], vertices[i * stride : i * stride + elements[0].size])
        for i in range(gathered.count)
    ]
    box = _box(positions)
    if "box" in mesh_tags and mesh_tags.get("positions_crc") == zlib.crc32(repr(positions).encode("ascii")):
        box = tuple(mesh_tags["box"])  # unchanged geometry: the stored box, which may be looser than the vertices'
    submeshes = (
        [SSubmesh(*submesh_range) for submesh_range in gathered.ranges] if mesh_tags.get("submeshes", True) else []
    )
    return SMesh(
        name=name,
        elements=elements,
        bones_per_vertex=_bones(document, mesh_tags, elements, rows, name),
        index_type=_index_type(mesh_tags, gathered.count),
        vertex_count=gathered.count,
        stride=stride,
        vertices=vertices,
        indices=indices,
        submeshes=submeshes,
        skeleton=skins.index(skin) if skin is not None else None,
        bbox=box,
        record_size=0x5C,
    )


def _gather(document: SDoc, primitives: list[dict], materials: list[int], who: str) -> _SVertices:
    """The primitives as one vertex set: shared attributes once, separate ones appended with their indices moved."""
    if not primitives:
        raise CGltfImportError(f"{who}: no primitives")
    shared = all(primitive.get("attributes") == primitives[0].get("attributes") for primitive in primitives)
    names = set(primitives[0].get("attributes", {}))
    if "POSITION" not in names:
        raise CGltfImportError(f"{who}: no POSITION")
    values: dict[str, list] = {attribute: [] for attribute in names}
    kinds = {}
    indices: list[int] = []
    ranges = []
    for k, primitive in enumerate(primitives):
        if primitive.get("mode", 4) != 4:
            raise CGltfImportError(f"{who}, primitive {k}: not a triangle list (mode {primitive.get('mode')})")
        attributes = primitive.get("attributes", {})
        if set(attributes) != names:
            raise CGltfImportError(f"{who}, primitive {k}: other attributes than the first primitive's")
        base = 0 if shared else len(values["POSITION"])
        if not shared or k == 0:
            for attribute, accessor in attributes.items():
                values[attribute] += document.gltf.values(accessor, normalize=False)
                kinds[attribute] = (
                    document.gltf.items("accessors")[accessor]["componentType"],
                    bool(document.gltf.items("accessors")[accessor].get("normalized")),
                )
        count = document.gltf.items("accessors")[attributes["POSITION"]]["count"]
        primitive_indices = document.gltf.values(primitive["indices"]) if "indices" in primitive else list(range(count))
        if len(primitive_indices) % 3:
            raise CGltfImportError(f"{who}, primitive {k}: indices are not whole triangles")
        ranges.append(
            (
                len(indices),
                len(primitive_indices),
                materials.index(primitive["material"]) if primitive.get("material") in materials else 0,
            )
        )
        indices += [i + base for i in primitive_indices]
    return _SVertices(values, kinds, indices, ranges)


def _layout(
    document: SDoc, mesh_tags: dict, gathered: _SVertices, skinned: bool, name: str, who: str
) -> tuple[tuple[SVertexElement, ...], bool]:
    """The layout and whether it is the one the extras recorded (only then do the ``_RDM_<n>`` attributes apply)."""
    if document.options.layout:
        return rdmlayout.parse(document.options.layout), False
    if isinstance(mesh_tags.get("elements"), list):
        return tuple(SVertexElement(*element) for element in mesh_tags["elements"]), True
    like = next(
        (
            candidate
            for candidate in (document.options.like.meshes if document.options.like else [])
            if candidate.name == name
        ),
        None,
    )
    if like is not None:
        return like.elements, False
    attributes = gathered.values
    if "NORMAL" not in attributes and "TEXCOORD_0" not in attributes:
        return rdmlayout.parse("P4h"), False
    if "TEXCOORD_0" not in attributes:
        raise CGltfImportError(f"{who}: no texture coordinates; name a layout without T (--layout)")
    base = "P4h_N4b_G4b_B4b_T2h"
    if skinned:
        return rdmlayout.parse(base + "_I4b_W4b"), False
    colours = sum(1 for k in range(2) if f"COLOR_{k}" in attributes)
    return rdmlayout.parse(base + "_C4b" * colours), False


def _dropped(document: SDoc, gathered: _SVertices, elements, mesh_tags: dict, who: str) -> None:
    taken = Counter(element.usage for element in elements)
    keep = {"POSITION"}
    keep |= {"NORMAL"} if taken[1] else set()
    keep |= {"TANGENT", "_BITANGENT"} if taken[2] or taken[3] else set()
    keep |= {f"TEXCOORD_{k}" for k in range(taken[4])} | {f"COLOR_{k}" for k in range(taken[5])}
    keep |= {"JOINTS_0", "WEIGHTS_0"} if taken[7] else set()
    keep |= set(mesh_tags.get("implied", []))  # glTF's own, marked by the export
    keep |= (
        {attribute for attribute in gathered.values if attribute.startswith("_RDM_")}
        if isinstance(mesh_tags.get("elements"), list)
        else set()
    )
    layout = rdmlayout.name_of(elements)
    for attribute in sorted(set(gathered.values) - keep):
        document.notes.append(f"{who}: dropped attribute {attribute}: layout {layout} has no place for it")


def _box(positions: list[tuple]) -> tuple:
    low = [min(position[k] for position in positions) for k in range(3)]
    high = [max(position[k] for position in positions) for k in range(3)]
    return (*low, *high)


def _index_type(mesh_tags: dict, count: int) -> int:
    stored = mesh_tags.get("index_type")
    if stored == 3 and count <= 0x10000:
        return 3
    if stored == 5:
        return 5
    return 5 if count > 0x10000 else 3


def _bones(document: SDoc, mesh_tags: dict, elements, rows: list[list[bytes]], name: str) -> int:
    if isinstance(mesh_tags.get("bones_per_vertex"), int):
        return mesh_tags["bones_per_vertex"]
    like = next(
        (
            candidate
            for candidate in (document.options.like.meshes if document.options.like else [])
            if candidate.name == name
        ),
        None,
    )
    if like is not None:
        return like.bones_per_vertex
    usages = [element.usage for element in elements]
    if 7 not in usages:
        return 0
    if 6 not in usages:
        return 1
    weights = rows[usages.index(6)]
    return 4 if any(weight[3] for weight in weights) else 3


class _CColumns:
    """The bytes of each element of the layout for every vertex."""

    def __init__(self, document, gathered, elements, from_tags, mesh_tags, space, indices, skin, who) -> None:
        self.document, self.gathered, self.elements, self.mesh_tags, self.skin, self.who = (
            document,
            gathered,
            elements,
            mesh_tags,
            skin,
            who,
        )
        self.raw = from_tags
        self.space = space
        self.linear = tuple(tuple(space[i][j] for j in range(3)) for i in range(3))
        self.exact = all(abs(self.linear[i][j]) in ((1.0 if i == j else 0.0),) for i in range(3) for j in range(3))
        self.indices = indices
        self.seen: Counter[int] = Counter()
        self._frames = None

    def element(self, k: int) -> list[bytes]:
        element = self.elements[k]
        nth = self.seen[element.usage]
        self.seen[element.usage] += 1
        raw = self.gathered.values.get(f"_RDM_{k}") if self.raw else None
        if element.usage == 0 and nth == 0:
            return self._positions(element, k, raw)
        if element.usage in (1, 2, 3) and nth == 0:
            return self._directions(element, k, raw)
        if element.usage == 4 and f"TEXCOORD_{nth}" in self.gathered.values:
            return [
                rdmlayout.encode(element, self._floats(f"TEXCOORD_{nth}", i)[: element.count])
                for i in range(self.gathered.count)
            ]
        if element.usage == 5 and f"COLOR_{nth}" in self.gathered.values and element.type == 5:
            return [bytes(colour) for colour in self._colours(f"COLOR_{nth}")]
        if element.usage == 7 and nth == 0 and "JOINTS_0" in self.gathered.values and element.type == 5:
            return self._joints()
        if element.usage == 6 and nth == 0 and "WEIGHTS_0" in self.gathered.values and element.type == 5:
            return [bytes(weight) for weight in self._weights()]
        if raw is not None:
            return [_raw_bytes(element, x) for x in raw]
        what = {4: "texture coordinates", 5: "a colour", 6: "weights", 7: "bone indices"}.get(element.usage, "values")
        raise CGltfImportError(
            f"{self.who}: layout element {k} ({element.code}) needs {what} the document does not hold"
        )

    def _floats(self, name: str, i: int) -> tuple:
        return self._floats_of(name, self.gathered.values[name][i])

    def _positions(self, element: SVertexElement, k: int, raw) -> list[bytes]:
        fourth = self.mesh_tags.get("fourth", {}).get(str(k), 0.0) if self.raw else 0.0
        out = []
        for i in range(self.gathered.count):
            position = self._place(self._floats("POSITION", i)[:3])
            values = (
                (*position, raw[i][3] if raw is not None else fourth)
                if element.count == 4
                else position[: element.count]
            )
            out.append(rdmlayout.encode(element, values))
        return out

    def _place(self, position: tuple) -> tuple[float, float, float]:
        """A position in RDM space; the mirror alone is a sign change, which keeps a -0.0 as it is."""
        if self.exact and not any(self.space[i][3] for i in range(3)):
            return (self.linear[0][0] * position[0], self.linear[1][1] * position[1], self.linear[2][2] * position[2])
        return rigmath.apply(self.space, position)

    def frames(self):
        """Normals, tangents and bitangents computed from the triangles and the first UV set (in RDM space)."""
        if self._frames is None:
            positions = [self._place(self._floats("POSITION", i)[:3]) for i in range(self.gathered.count)]
            uvs = (
                [self._floats("TEXCOORD_0", i)[:2] for i in range(self.gathered.count)]
                if "TEXCOORD_0" in self.gathered.values
                else None
            )
            normals = (
                [self._vector("NORMAL", i, normal=True) for i in range(self.gathered.count)]
                if "NORMAL" in self.gathered.values
                else None
            )
            self._frames = tangents.frames(positions, uvs, self.indices, normals)
            how = "the triangles and UVs" if uvs else "the triangles (no UVs: an arbitrary frame)"
            self.document.notes.append(f"{self.who}: filled the tangent frame the document lacks from {how}")
        return self._frames

    def _vector(self, name: str, i: int, normal: bool = False) -> tuple[float, float, float]:
        vector = self._floats(name, i)[:3]
        if self.exact:
            return (self.linear[0][0] * vector[0], self.linear[1][1] * vector[1], self.linear[2][2] * vector[2])
        matrix = meshxform.normal_matrix(self.linear) if normal else self.linear
        out = meshxform.apply(matrix, vector)
        length = sum(component * component for component in out) ** 0.5
        return tuple(component / length for component in out) if length > 1e-12 else out

    def _bitangent(self, i: int) -> tuple[float, float, float]:
        """glTF's bitangent, cross(normal, tangent) times the tangent's w, in RDM space."""
        normal, tangent = self._floats("NORMAL", i)[:3], self._floats("TANGENT", i)
        sign = tangent[3] if len(tangent) > 3 else 1.0
        bitangent = (
            normal[1] * tangent[2] - normal[2] * tangent[1],
            normal[2] * tangent[0] - normal[0] * tangent[2],
            normal[0] * tangent[1] - normal[1] * tangent[0],
        )
        bitangent = tuple(component * sign for component in bitangent)
        return meshxform.apply(self.linear, bitangent)

    def _directions(self, element: SVertexElement, k: int, raw) -> list[bytes]:
        usage = element.usage
        name = {1: "NORMAL", 2: "TANGENT", 3: "_BITANGENT"}[usage]
        have = name in self.gathered.values or (usage == 3 and {"NORMAL", "TANGENT"} <= set(self.gathered.values))
        if usage == 1 and "NORMAL" not in self.gathered.values:
            have = False
        fourth = int(self.mesh_tags.get("fourth", {}).get(str(k), 0)) if self.raw else 0
        out = []
        for i in range(self.gathered.count):
            if not have:
                direction = self.frames()[usage - 1][i]
                direction = direction if usage == 1 else tangents.stored(direction)
            elif usage == 3 and name not in self.gathered.values:
                direction = tangents.stored(self._bitangent(i))
            else:
                direction = self._vector(name, i, normal=usage == 1)
                direction = direction if usage == 1 else tangents.stored(direction)
            out.append(self._direction_bytes(element, direction, raw[i] if raw is not None else None, fourth))
        return out

    def _direction_bytes(self, element: SVertexElement, direction, raw, fourth: int) -> bytes:
        if element.type != 5:
            return rdmlayout.encode(element, direction[: element.count])
        if raw is not None and all(
            abs(raw_component - new_component) <= 1 / 127.5
            for raw_component, new_component in zip(rdmlayout.direction(raw), direction, strict=True)
        ):
            return bytes(raw)  # the stored bytes, 4th one included, where the vector moved less than a step
        return pack_direction(*direction)[:3] + bytes((fourth,))

    def _colours(self, name: str) -> list[tuple]:
        component, normalized = self.gathered.kinds[name]
        out = []
        for value in self.gathered.values[name]:
            if component == gltf.UBYTE and normalized:
                colour = tuple(value)
            else:
                colour = tuple(max(0, min(255, round(x * 255))) for x in self._floats_of(name, value))
            if len(colour) == 3:
                colour = (*colour, 255)
            out.append(colour)
        if len(self.gathered.values[name][0]) == 3:
            self.document.notes.append(f"{self.who}: {name} has no alpha; written as 255")
        return out

    def _floats_of(self, name: str, value) -> tuple:
        """An attribute's value as floats (a normalized integer one scaled into [0, 1] or [-1, 1])."""
        component, normalized = self.gathered.kinds[name]
        if component == gltf.FLOAT or not normalized:
            return tuple(float(number) for number in value)
        scale = {gltf.UBYTE: 255.0, gltf.USHORT: 65535.0, gltf.BYTE: 127.0, gltf.SHORT: 32767.0}[component]
        return tuple(max(number / scale, -1.0) for number in value)

    def _joints(self) -> list[bytes]:
        slots = self.skin.slot if self.skin is not None else None
        weights = self.gathered.values.get("WEIGHTS_0")
        out = []
        for i, j in enumerate(self.gathered.values["JOINTS_0"]):
            if slots is None:
                out.append(bytes(j))
                continue
            row = []
            for slot_index, slot in enumerate(j):
                if slot not in slots and weights is not None and weights[i][slot_index]:
                    raise CGltfImportError(f"{self.who}: a vertex weighs joint slot {slot}, which the skin dropped")
                row.append(slots.get(slot, 0))
            if max(row) > 255:
                raise CGltfImportError(f"{self.who}: a bone index past 255; an RDM bone index is one byte")
            out.append(bytes(row))
        return out

    def _weights(self) -> list[tuple]:
        component, normalized = self.gathered.kinds["WEIGHTS_0"]
        if component == gltf.UBYTE and normalized:
            return [tuple(weight) for weight in self.gathered.values["WEIGHTS_0"]]
        return [
            rdmlayout.weight_bytes(self._floats_of("WEIGHTS_0", value)) for value in self.gathered.values["WEIGHTS_0"]
        ]


def _raw_bytes(element: SVertexElement, value) -> bytes:
    values = value if isinstance(value, (tuple, list)) else (value,)
    return rdmlayout.encode(element, values) if element.type != 5 else bytes(int(number) for number in values)
