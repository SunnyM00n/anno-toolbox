"""How a pass of the game's compiled shaders binds on the native backend, worked out from the shaders alone: the
binding layouts by register space (as the engine lays out its tables: shader-compile.md section 4), the bindless
ranges, the root constants, and the vertex input from the shader's own input signature. No GPU and no module here:
the result is the description ``nativegpu`` hands to the module.

``SBindingPlan.of(reflections)`` is the entry (one pass's stages, or a compute shader); ``signature(container)`` reads
a shader's input or output signature, ``input_elements`` lays it over a mesh's stored elements. The design:
docs/rendering/native-backend.md, "Step 1".
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..formats.fxbind import ROOT_SPACE
from ..infra.dxc import container_parts
from ..models.fxstage import sampler_named

MAX_LAYOUTS = 8  # NVRHI's binding layouts a pipeline takes
PUSH_BYTES = 128  # NVRHI's push constants (the root constants of one buffer)
MAX_ANISOTROPY = 16  # the anisotropic samplers' level [I]: the engine's own is not read
# a reflection's binding kind -> NVRHI's resource kind (module: kind_named)
KINDS = {"cbuffer": "cbuffer", "tbuffer": "typed", "texture": "texture", "sampler": "sampler",
         "uav_rwtyped": "rwtexture", "structured": "structured", "uav_rwstructured": "rwstructured",
         "byteaddress": "raw", "uav_rwbyteaddress": "rwraw", "uav_append_structured": "rwstructured",
         "uav_consume_structured": "rwstructured", "uav_rwstructured_with_counter": "rwstructured",
         "rtaccelerationstructure": "raw", "uav_feedbacktexture": "rwtexture"}  # fmt: skip


@dataclass(frozen=True)
class SItem:
    """One binding of a layout: NVRHI's ``kind``, the shader's register, how many (an array) and the shader's name."""

    kind: str
    slot: int
    count: int = 1
    name: str = ""


@dataclass
class SLayoutSpec:
    """One binding layout: a register space's bounded bindings, or one unbounded range of it (``bindless``)."""

    space: int
    items: list[SItem] = field(default_factory=list)
    bindless: bool = False

    def tuples(self) -> list[tuple[str, int, int]]:
        return [(i.kind, i.slot, i.count) for i in self.items]


@dataclass
class SBindingPlan:
    """The layouts a pass binds with, in the order its pipeline takes them (spaces ascending, a space's bindless
    ranges after its table, the root constants last), and the samplers it reads by name."""

    layouts: list[SLayoutSpec]
    push: SItem | None = None  # the root constants buffer that becomes NVRHI's push constants
    samplers: dict[tuple[int, int], str] = field(default_factory=dict)  # (space, register) -> name

    @classmethod
    def of(cls, reflections: list, bindless: tuple = (), volatile: bool = False, rooted: tuple = (),
           root_views: tuple = ()) -> SBindingPlan:  # fmt: skip
        """The union of every stage's bindings (a register two stages share is bound once). ``bindless``: arrays
        the engine declares bindless, bound as unbounded ranges (a descriptor table); ``volatile``: the constant
        buffers written per dispatch on the module's own lists (NVRHI's volatile ones); ``rooted``: root constant
        buffers that stay root views, never push constants (``cbModel``, a draw's slot of one buffer);
        ``root_views``: constant buffers of other spaces bound as root views in their space's layout
        (``cbDistanceCluster``)."""
        seen: dict[tuple[int, str, int], tuple[str, int]] = {}
        sizes = {
            constant_buffer.name: constant_buffer.size
            for reflection in reflections
            for constant_buffer in reflection.cbuffers
        }
        samplers: dict[tuple[int, int], str] = {}
        for reflection in reflections:
            for binding in reflection.bindings:
                kind = KINDS.get(binding.kind, "texture")
                if kind == "sampler":
                    samplers.setdefault((binding.space, binding.register), binding.name)
                if kind == "cbuffer" and binding.space != ROOT_SPACE and (volatile or binding.name in root_views):
                    kind = "volatile"
                seen.setdefault(
                    (binding.space, kind, binding.register),
                    (binding.name, 0 if binding.name in bindless else binding.count),
                )
        layouts: dict[int, SLayoutSpec] = {}
        open_ranges: list[SLayoutSpec] = []
        roots: list[SItem] = []
        for (space, kind, register), (name, count) in sorted(seen.items()):
            if space == ROOT_SPACE and kind == "cbuffer":
                roots.append(SItem("volatile", register, sizes.get(name, 16), name))
            elif count == 0:  # unbounded: a bindless layout of its own, bound by a descriptor table
                open_ranges.append(SLayoutSpec(space, [SItem(kind, register, 0, name)], bindless=True))
            else:
                layouts.setdefault(space, SLayoutSpec(space)).items.append(SItem(kind, register, count, name))
        ordered = []
        for space in sorted({*layouts, *(open_range.space for open_range in open_ranges)}):
            if space in layouts:
                ordered.append(layouts[space])
            ordered += [open_range for open_range in open_ranges if open_range.space == space]
        push = _push([root for root in roots if root.name not in rooted])
        if roots:
            items = [SItem("push", push.slot, push.count, push.name) if root is push else SItem("volatile", root.slot, 1, root.name)
                     for root in roots]  # fmt: skip
            ordered.append(SLayoutSpec(ROOT_SPACE, items))
        if len(ordered) > MAX_LAYOUTS:
            raise ValueError(f"{len(ordered)} binding layouts: NVRHI takes {MAX_LAYOUTS}")
        return cls(ordered, push, samplers)

    def index(self, space: int, bindless_slot: int | None = None) -> int:
        """The place of a space's table (or of its bindless range at ``bindless_slot``) among the layouts."""
        for i, spec in enumerate(self.layouts):
            if spec.space == space and spec.bindless == (bindless_slot is not None):
                if bindless_slot is None or spec.items[0].slot == bindless_slot:
                    return i
        raise KeyError((space, bindless_slot))


def _push(roots: list[SItem]) -> SItem | None:
    """The root constants buffer that becomes push constants: the one a draw changes most (the draw's index, the
    smallest that fits), the others go in as root constant buffers."""
    fits = [root for root in roots if root.count <= PUSH_BYTES]
    if not fits:
        return None
    named = [root for root in fits if "DrawCall" in root.name]
    return named[0] if named else min(fits, key=lambda root: (root.count, root.slot))


def sampler_state(name: str) -> dict:
    """One of the engine's fixed samplers by the words of its name (``fxstage.sampler_named``) as the module's
    ``Device.sampler`` takes it: ``Aniso`` anisotropic, ``Cmp`` a comparison by 117's reversed depth."""
    state = sampler_named(name)
    if "Aniso" in name:
        filt = "aniso"
    else:
        filt = {"MIN_MAG_POINT_MIP_LINEAR": "point_mip_linear", "MIN_MAG_MIP_POINT": "point"}.get(
            state.filter, "linear"
        )
    return {"filter": filt, "address": state.address_u.lower(), "compare": "greater_equal" if state.compare else "",
            "anisotropy": float(MAX_ANISOTROPY)}  # fmt: skip


# an element's (RDM type, count) -> NVRHI's format by the shader input's component type (float, uint, int):
# halves, floats, a packed four-byte vector (unsigned bytes, read as 0..1 or as integers), four u16, u32s
_FORMATS = {(6, 2): "RG16_FLOAT", (6, 4): "RGBA16_FLOAT", (7, 1): "R32_FLOAT", (7, 2): "RG32_FLOAT",
            (7, 3): "RGB32_FLOAT", (7, 4): "RGBA32_FLOAT"}  # fmt: skip
_PACKED = {"float": "RGBA8_UNORM", "uint": "RGBA8_UINT", "int": "RGBA8_SINT"}
_INTEGERS = {(2, 4): "RGBA16_UINT", (4, 1): "R32_UINT", (4, 4): "RGBA32_UINT"}
# a semantic's RDM element usage (rdm.md "Vertices": PNGBTCWI)
USAGES = {"SV_POSITION": 0, "POSITION": 0, "NORMAL": 1, "TANGENT": 2, "BINORMAL": 3, "TEXCOORD": 4, "COLOR": 5,
          "BLENDWEIGHT": 6, "BLENDINDICES": 7}  # fmt: skip
_COMPONENT = {1: "uint", 2: "int", 3: "float", 4: "uint", 5: "int", 6: "float"}  # D3D_REGISTER_COMPONENT_TYPE
SYSTEM_VALUES = ("SV_VERTEXID", "SV_INSTANCEID")  # inputs the input assembler makes, not the mesh
FILL_SLOT = 1  # the stream of an input the mesh lacks: one constant vertex (a colour white, the rest zero)


@dataclass(frozen=True)
class SSignatureElement:
    semantic: str
    index: int
    register: int
    system_value: int
    component: str  # float, uint, int
    mask: int

    @property
    def width(self) -> int:
        """How many components the shader reads."""
        return bin(self.mask).count("1")


def signature(container: bytes, part: str = "ISG1") -> list[SSignatureElement]:
    """A shader's input (``ISG1``) or output (``OSG1``) signature, read from its DXBC container; ``ValueError`` on a
    container or signature that is cut short."""
    try:
        data = container_parts(container).get(part)
        if not data:
            return []
        count, offset = struct.unpack_from("<II", data, 0)
        out = []
        for i in range(count):
            _stream, name_at, index, sv, comp, reg, mask = struct.unpack_from("<6IB", data, offset + 32 * i)
            name = data[name_at : data.index(b"\0", name_at)].decode("ascii")
            out.append(SSignatureElement(name, index, reg, sv, _COMPONENT.get(comp, "float"), mask))
    except struct.error as exc:
        raise ValueError(f"the shader's {part} signature is cut short: {exc}") from exc
    return out


# (semantic, semantic index, NVRHI format, stream, offset, stride, per instance): the module's input_layout
InputElement = tuple[str, int, str, int, int, int, bool]


def input_elements(elements, inputs: list[SSignatureElement], stride: int) -> tuple[list[InputElement], bytes]:
    """The shader's inputs laid out over a mesh's stored ``elements`` ((usage, type, count, offset), as
    ``gamedraw.CMesh`` keeps them, ``stride`` bytes a vertex): each semantic finds its element by usage and index;
    one the mesh lacks reads a constant from ``FILL_SLOT``, whose one vertex's bytes come back with the layout."""
    out: list[InputElement] = []
    fill = bytearray()
    for inp in inputs:
        if inp.semantic.upper() in SYSTEM_VALUES:
            continue
        usage = USAGES.get(re.sub(r"\d+$", "", inp.semantic.upper()))
        found = [element for element in elements if element[0] == usage]
        element = found[inp.index] if inp.index < len(found) else None
        fmt = _format(element, inp.component) if element is not None else None
        if fmt is not None:
            out.append((inp.semantic, inp.index, fmt, 0, element[3], stride, False))
            continue
        white = 255 if usage == 5 else 0
        out.append((inp.semantic, inp.index, _PACKED[inp.component], FILL_SLOT, len(fill), 0, False))
        fill += bytes((white if inp.component == "float" else 0,) * 4)
    return out, bytes(fill)


def _format(element, component: str) -> str | None:
    _usage, kind, count = element[0], element[1], element[2]
    if kind == 5:
        return _PACKED[component]
    if component != "float" and (kind, count) in _INTEGERS:
        return _INTEGERS[(kind, count)]
    return _FORMATS.get((kind, count))


def float_elements(inputs: list[SSignatureElement]) -> tuple[list[InputElement], int]:
    """A vertex of 32-bit floats for each input as wide as the shader reads it, in signature order (a full-screen
    triangle's: ``gamepost.TRIANGLE``), and the vertex's stride."""
    names = {1: "R32_FLOAT", 2: "RG32_FLOAT", 3: "RGB32_FLOAT", 4: "RGBA32_FLOAT"}
    out, at = [], 0
    for inp in inputs:
        if inp.semantic.upper() in SYSTEM_VALUES:
            continue
        out.append((inp.semantic, inp.index, names[inp.width], 0, at, 0, False))
        at += 4 * inp.width
    return [(*element[:5], at, element[6]) for element in out], at
