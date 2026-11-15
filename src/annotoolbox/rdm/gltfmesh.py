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
from ..formats.rdmread import Mesh, Submesh, VertexElement
from ..formats.rdmwrite import pack_direction
from ..models import meshxform, tangents
from . import rigmath
from .gltfdoc import Doc, GltfImportError, tags
from .gltfskin import Skin


@dataclass
class _Vertices:
    """The primitives of a mesh as one vertex set: attribute name -> values, the triangles, the submesh ranges."""

    values: dict[str, list]
    kinds: dict[str, tuple[int, bool]]  # attribute -> (component type, normalized)
    indices: list[int]
    ranges: list[tuple[int, int, int]]  # first index, count, material

    @property
    def count(self) -> int:
        return len(self.values["POSITION"])


def mesh(d: Doc, node: int, materials: list[int], skins: list[Skin]) -> Mesh:
    """The RDM mesh of mesh node ``node``; ``materials`` are the file's glTF materials in RDM order."""
    n = d.node(node)
    gm = d.g.items("meshes")[n["mesh"]]
    t = tags(gm)
    name = n.get("name") or gm.get("name") or t.get("name", "")
    who = f"mesh {name!r}"
    skin = next((s for s in skins if s.gltf_skin == n["skin"]), None) if "skin" in n else None
    v = _gather(d, gm.get("primitives", []), materials, who)
    space = rigmath.MIRROR_X if d.mirror else rigmath.IDENTITY
    if skin is None and d.opts.transform:
        space = rigmath.multiply(space, d.world(node))
    elements, from_tags = _layout(d, t, v, skin is not None, name, who)
    linear = tuple(tuple(space[i][j] for j in range(3)) for i in range(3))
    indices = meshxform.reversed_winding(v.indices) if meshxform.mirrored(linear) else v.indices
    cols = _Columns(d, v, elements, from_tags, t, space, indices, skin, who)
    rows = [cols.element(k) for k in range(len(elements))]
    vertices = b"".join(b"".join(rows[k][i] for k in range(len(elements))) for i in range(v.count))
    _dropped(d, v, elements, t, who)
    stride = rdmlayout.stride_of(elements)
    positions = [
        rdmlayout.decode(elements[0], vertices[i * stride : i * stride + elements[0].size]) for i in range(v.count)
    ]
    box = _box(positions)
    if "box" in t and t.get("positions_crc") == zlib.crc32(repr(positions).encode("ascii")):
        box = tuple(t["box"])  # unchanged geometry: the stored box, which may be looser than the vertices'
    submeshes = [Submesh(*r) for r in v.ranges] if t.get("submeshes", True) else []
    return Mesh(
        name=name,
        elements=elements,
        bones_per_vertex=_bones(d, t, elements, rows, name),
        index_type=_index_type(t, v.count),
        vertex_count=v.count,
        stride=stride,
        vertices=vertices,
        indices=indices,
        submeshes=submeshes,
        skeleton=skins.index(skin) if skin is not None else None,
        bbox=box,
        record_size=0x5C,
    )


def _gather(d: Doc, prims: list[dict], materials: list[int], who: str) -> _Vertices:
    """The primitives as one vertex set: shared attributes once, separate ones appended with their indices moved."""
    if not prims:
        raise GltfImportError(f"{who}: no primitives")
    shared = all(p.get("attributes") == prims[0].get("attributes") for p in prims)
    names = set(prims[0].get("attributes", {}))
    if "POSITION" not in names:
        raise GltfImportError(f"{who}: no POSITION")
    values: dict[str, list] = {a: [] for a in names}
    kinds = {}
    indices: list[int] = []
    ranges = []
    for k, p in enumerate(prims):
        if p.get("mode", 4) != 4:
            raise GltfImportError(f"{who}, primitive {k}: not a triangle list (mode {p.get('mode')})")
        attrs = p.get("attributes", {})
        if set(attrs) != names:
            raise GltfImportError(f"{who}, primitive {k}: other attributes than the first primitive's")
        base = 0 if shared else len(values["POSITION"])
        if not shared or k == 0:
            for a, acc in attrs.items():
                values[a] += d.g.values(acc, normalize=False)
                kinds[a] = (
                    d.g.items("accessors")[acc]["componentType"],
                    bool(d.g.items("accessors")[acc].get("normalized")),
                )
        count = d.g.items("accessors")[attrs["POSITION"]]["count"]
        tri = d.g.values(p["indices"]) if "indices" in p else list(range(count))
        if len(tri) % 3:
            raise GltfImportError(f"{who}, primitive {k}: indices are not whole triangles")
        ranges.append((len(indices), len(tri), materials.index(p["material"]) if p.get("material") in materials else 0))
        indices += [i + base for i in tri]
    return _Vertices(values, kinds, indices, ranges)


def _layout(
    d: Doc, t: dict, v: _Vertices, skinned: bool, name: str, who: str
) -> tuple[tuple[VertexElement, ...], bool]:
    """The layout and whether it is the one the extras recorded (only then do the ``_RDM_<n>`` attributes apply)."""
    if d.opts.layout:
        return rdmlayout.parse(d.opts.layout), False
    if isinstance(t.get("elements"), list):
        return tuple(VertexElement(*e) for e in t["elements"]), True
    like = next((m for m in (d.opts.like.meshes if d.opts.like else []) if m.name == name), None)
    if like is not None:
        return like.elements, False
    a = v.values
    if "NORMAL" not in a and "TEXCOORD_0" not in a:
        return rdmlayout.parse("P4h"), False
    if "TEXCOORD_0" not in a:
        raise GltfImportError(f"{who}: no texture coordinates; name a layout without T (--layout)")
    base = "P4h_N4b_G4b_B4b_T2h"
    if skinned:
        return rdmlayout.parse(base + "_I4b_W4b"), False
    colours = sum(1 for k in range(2) if f"COLOR_{k}" in a)
    return rdmlayout.parse(base + "_C4b" * colours), False


def _dropped(d: Doc, v: _Vertices, elements, t: dict, who: str) -> None:
    taken = Counter(e.usage for e in elements)
    keep = {"POSITION"}
    keep |= {"NORMAL"} if taken[1] else set()
    keep |= {"TANGENT", "_BITANGENT"} if taken[2] or taken[3] else set()
    keep |= {f"TEXCOORD_{k}" for k in range(taken[4])} | {f"COLOR_{k}" for k in range(taken[5])}
    keep |= {"JOINTS_0", "WEIGHTS_0"} if taken[7] else set()
    keep |= set(t.get("implied", []))  # glTF's own, marked by the export
    keep |= {a for a in v.values if a.startswith("_RDM_")} if isinstance(t.get("elements"), list) else set()
    layout = rdmlayout.name_of(elements)
    for a in sorted(set(v.values) - keep):
        d.notes.append(f"{who}: dropped attribute {a}: layout {layout} has no place for it")


def _box(positions: list[tuple]) -> tuple:
    lo = [min(p[k] for p in positions) for k in range(3)]
    hi = [max(p[k] for p in positions) for k in range(3)]
    return (*lo, *hi)


def _index_type(t: dict, count: int) -> int:
    stored = t.get("index_type")
    if stored == 3 and count <= 0x10000:
        return 3
    if stored == 5:
        return 5
    return 5 if count > 0x10000 else 3


def _bones(d: Doc, t: dict, elements, rows: list[list[bytes]], name: str) -> int:
    if isinstance(t.get("bones_per_vertex"), int):
        return t["bones_per_vertex"]
    like = next((m for m in (d.opts.like.meshes if d.opts.like else []) if m.name == name), None)
    if like is not None:
        return like.bones_per_vertex
    usages = [e.usage for e in elements]
    if 7 not in usages:
        return 0
    if 6 not in usages:
        return 1
    weights = rows[usages.index(6)]
    return 4 if any(w[3] for w in weights) else 3


class _Columns:
    """The bytes of each element of the layout for every vertex."""

    def __init__(self, d, v, elements, from_tags, t, space, indices, skin, who) -> None:
        self.d, self.v, self.elements, self.t, self.skin, self.who = d, v, elements, t, skin, who
        self.raw = from_tags
        self.space = space
        self.linear = tuple(tuple(space[i][j] for j in range(3)) for i in range(3))
        self.exact = all(abs(self.linear[i][j]) in ((1.0 if i == j else 0.0),) for i in range(3) for j in range(3))
        self.indices = indices
        self.seen: Counter[int] = Counter()
        self._frames = None

    def element(self, k: int) -> list[bytes]:
        e = self.elements[k]
        nth = self.seen[e.usage]
        self.seen[e.usage] += 1
        raw = self.v.values.get(f"_RDM_{k}") if self.raw else None
        if e.usage == 0 and nth == 0:
            return self._positions(e, k, raw)
        if e.usage in (1, 2, 3) and nth == 0:
            return self._directions(e, k, raw)
        if e.usage == 4 and f"TEXCOORD_{nth}" in self.v.values:
            return [rdmlayout.encode(e, self._floats(f"TEXCOORD_{nth}", i)[: e.count]) for i in range(self.v.count)]
        if e.usage == 5 and f"COLOR_{nth}" in self.v.values and e.type == 5:
            return [bytes(c) for c in self._colours(f"COLOR_{nth}")]
        if e.usage == 7 and nth == 0 and "JOINTS_0" in self.v.values and e.type == 5:
            return self._joints()
        if e.usage == 6 and nth == 0 and "WEIGHTS_0" in self.v.values and e.type == 5:
            return [bytes(w) for w in self._weights()]
        if raw is not None:
            return [_raw_bytes(e, x) for x in raw]
        what = {4: "texture coordinates", 5: "a colour", 6: "weights", 7: "bone indices"}.get(e.usage, "values")
        raise GltfImportError(f"{self.who}: layout element {k} ({e.code}) needs {what} the document does not hold")

    def _floats(self, name: str, i: int) -> tuple:
        return self._floats_of(name, self.v.values[name][i])

    def _positions(self, e: VertexElement, k: int, raw) -> list[bytes]:
        fourth = self.t.get("fourth", {}).get(str(k), 0.0) if self.raw else 0.0
        out = []
        for i in range(self.v.count):
            p = self._place(self._floats("POSITION", i)[:3])
            values = (*p, raw[i][3] if raw is not None else fourth) if e.count == 4 else p[: e.count]
            out.append(rdmlayout.encode(e, values))
        return out

    def _place(self, p: tuple) -> tuple[float, float, float]:
        """A position in RDM space; the mirror alone is a sign change, which keeps a -0.0 as it is."""
        if self.exact and not any(self.space[i][3] for i in range(3)):
            return (self.linear[0][0] * p[0], self.linear[1][1] * p[1], self.linear[2][2] * p[2])
        return rigmath.apply(self.space, p)

    def frames(self):
        """Normals, tangents and bitangents computed from the triangles and the first UV set (in RDM space)."""
        if self._frames is None:
            pos = [self._place(self._floats("POSITION", i)[:3]) for i in range(self.v.count)]
            uvs = (
                [self._floats("TEXCOORD_0", i)[:2] for i in range(self.v.count)]
                if "TEXCOORD_0" in self.v.values
                else None
            )
            normals = (
                [self._vector("NORMAL", i, normal=True) for i in range(self.v.count)]
                if "NORMAL" in self.v.values
                else None
            )
            self._frames = tangents.frames(pos, uvs, self.indices, normals)
            how = "the triangles and UVs" if uvs else "the triangles (no UVs: an arbitrary frame)"
            self.d.notes.append(f"{self.who}: filled the tangent frame the document lacks from {how}")
        return self._frames

    def _vector(self, name: str, i: int, normal: bool = False) -> tuple[float, float, float]:
        v = self._floats(name, i)[:3]
        if self.exact:
            return (self.linear[0][0] * v[0], self.linear[1][1] * v[1], self.linear[2][2] * v[2])
        m = meshxform.normal_matrix(self.linear) if normal else self.linear
        out = meshxform.apply(m, v)
        n = sum(c * c for c in out) ** 0.5
        return tuple(c / n for c in out) if n > 1e-12 else out

    def _bitangent(self, i: int) -> tuple[float, float, float]:
        """glTF's bitangent, cross(normal, tangent) times the tangent's w, in RDM space."""
        n, t = self._floats("NORMAL", i)[:3], self._floats("TANGENT", i)
        w = t[3] if len(t) > 3 else 1.0
        b = (n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0])
        b = tuple(c * w for c in b)
        return meshxform.apply(self.linear, b)

    def _directions(self, e: VertexElement, k: int, raw) -> list[bytes]:
        usage = e.usage
        name = {1: "NORMAL", 2: "TANGENT", 3: "_BITANGENT"}[usage]
        have = name in self.v.values or (usage == 3 and {"NORMAL", "TANGENT"} <= set(self.v.values))
        if usage == 1 and "NORMAL" not in self.v.values:
            have = False
        fourth = int(self.t.get("fourth", {}).get(str(k), 0)) if self.raw else 0
        out = []
        for i in range(self.v.count):
            if not have:
                vec = self.frames()[usage - 1][i]
                vec = vec if usage == 1 else tangents.stored(vec)
            elif usage == 3 and name not in self.v.values:
                vec = tangents.stored(self._bitangent(i))
            else:
                vec = self._vector(name, i, normal=usage == 1)
                vec = vec if usage == 1 else tangents.stored(vec)
            out.append(self._direction_bytes(e, vec, raw[i] if raw is not None else None, fourth))
        return out

    def _direction_bytes(self, e: VertexElement, vec, raw, fourth: int) -> bytes:
        if e.type != 5:
            return rdmlayout.encode(e, vec[: e.count])
        if raw is not None and all(abs(a - b) <= 1 / 127.5 for a, b in zip(rdmlayout.direction(raw), vec, strict=True)):
            return bytes(raw)  # the stored bytes, 4th one included, where the vector moved less than a step
        return pack_direction(*vec)[:3] + bytes((fourth,))

    def _colours(self, name: str) -> list[tuple]:
        component, normalized = self.v.kinds[name]
        out = []
        for value in self.v.values[name]:
            if component == gltf.UBYTE and normalized:
                c = tuple(value)
            else:
                c = tuple(max(0, min(255, round(x * 255))) for x in self._floats_of(name, value))
            if len(c) == 3:
                c = (*c, 255)
            out.append(c)
        if len(self.v.values[name][0]) == 3:
            self.d.notes.append(f"{self.who}: {name} has no alpha; written as 255")
        return out

    def _floats_of(self, name: str, value) -> tuple:
        """An attribute's value as floats (a normalized integer one scaled into [0, 1] or [-1, 1])."""
        component, normalized = self.v.kinds[name]
        if component == gltf.FLOAT or not normalized:
            return tuple(float(c) for c in value)
        scale = {gltf.UBYTE: 255.0, gltf.USHORT: 65535.0, gltf.BYTE: 127.0, gltf.SHORT: 32767.0}[component]
        return tuple(max(c / scale, -1.0) for c in value)

    def _joints(self) -> list[bytes]:
        slots = self.skin.slot if self.skin is not None else None
        weights = self.v.values.get("WEIGHTS_0")
        out = []
        for i, j in enumerate(self.v.values["JOINTS_0"]):
            if slots is None:
                out.append(bytes(j))
                continue
            row = []
            for s, slot in enumerate(j):
                if slot not in slots and weights is not None and weights[i][s]:
                    raise GltfImportError(f"{self.who}: a vertex weighs joint slot {slot}, which the skin dropped")
                row.append(slots.get(slot, 0))
            if max(row) > 255:
                raise GltfImportError(f"{self.who}: a bone index past 255; an RDM bone index is one byte")
            out.append(bytes(row))
        return out

    def _weights(self) -> list[tuple]:
        component, normalized = self.v.kinds["WEIGHTS_0"]
        if component == gltf.UBYTE and normalized:
            return [tuple(w) for w in self.v.values["WEIGHTS_0"]]
        return [rdmlayout.weight_bytes(self._floats_of("WEIGHTS_0", value)) for value in self.v.values["WEIGHTS_0"]]


def _raw_bytes(e: VertexElement, value) -> bytes:
    values = value if isinstance(value, (tuple, list)) else (value,)
    return rdmlayout.encode(e, values) if e.type != 5 else bytes(int(c) for c in values)
