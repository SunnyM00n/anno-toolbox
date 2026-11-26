"""Anno 117's own shaders on QRhi: the engine's binding model rewritten into the one QRhi builds, the shader's inputs
filled by name from its reflection, a material's maps uploaded as they are stored, and the draw into a G-buffer.

``adapt(hlsl, entry)`` rewrites a pass shader's text before it is compiled (the caller's, by the game's compiler);
``CConstants`` is one constant buffer written by member path, ``fill_*`` the engine's inputs; ``gamedraw`` draws with
them. No Qt here. docs/tool-suite.md section 21, "The game-shader path"; the engine's bindings:
docs/rendering/shader-compile.md section 4.
"""

from __future__ import annotations

import functools
import hashlib
import math
import re
import struct
from dataclasses import dataclass, field, replace

import numpy as np

from ..formats import fxeffect
from . import rawbuffers

# texture arrays the bindless table is spread over: one per size and format of a draw's maps, or one per map file
# (``gamemaps``: an object part names up to 7 files; the switch over them compiles to a branch, not 16 samples)
CLASSES = 16
SLOTS = 256  # bindless indices a draw can use
# a structured buffer becomes a constant buffer of this many elements (64 KB at most), by element type
# (the instance data at 256: four times fewer draws than 64, larger ones were slower on the GPU; island-117-view.md
# "Speed"; a cloth's simulated vertices at 2048, 64 KB: render.clothmesh)
LENGTHS = {"CameraData": 3, "float4x3": 256, "float3x4": 256, "uint": 256, "uint4": 256, "ModelInstanceData": 256,
           "LastModelOrientation": 256, "TerrainBakeSplat": 32, "ClothSimVertex": 2048}  # fmt: skip
DEFAULT_LENGTH = 16
# read-only structured buffers read as raw buffers (``rawbuffers``; every read-write one is): the light clusters
# (32 x 16 x 16 cells) and the point lights, too large for a constant buffer, and the cascades the shadow analysis
# writes on the GPU, the light the sky passes write (gamesky), the eye adaptation's histograms and exposure (eyeadapt),
# the island's coarse heights the terrain's upload reads (terrainpages)
RAW_BUFFERS = (
    "cUploadDataSrc8",
    "cLightClusterBuffer",
    "cPointLights",
    "cShadowInformation",
    "cShadowInfoLastFrame",
    "cLightBuffer",
    "cPerTileHistogramsIn",
    "cMergedHistogramIn",
    "cExposureBuffer",
)
# the model shaders' instance data as the engine feeds it, one buffer a frame for all draws (``adapt(is_frame=True)``:
# raw buffers; the island view, ``gameinstances.CFrameInstances``; island-117-view.md "Speed", part 5)
FRAME_INSTANCES = ("cModelPerFrameDataBuffer", "cModelInstanceDataBuffer", "cCurrBoneMatrixBuffer",
                   "cLastBoneMatrixBuffer", "cModelGPUCulledInstanceData")  # fmt: skip
SYSTEM_INPUTS = ("SV_VertexID", "SV_InstanceID", "SV_PrimitiveID")
# the arrays of textures a shader indexes, read through the bindless stand-in: the engine's table and the terrain
# bake's splat maps (``bindless Texture2D cTrnBakeSplatTextures[96]``; QRhi's Python binding binds no texture arrays)
BINDLESS_TABLES = ("cBindlessTextures", "cTrnBakeSplatTextures")
TEXTURE2D = r"\bTexture2D\b(?!\s*<\s*(?:u?int|bool))(?:\s*<\s*\w+\s*>)?(?!\s*Array|MS)"  # integer ones stay


def _bindless_text() -> str:
    """The stand-in for the engine's ``Texture2D cBindlessTextures[]``: texture arrays per class, a slot table
    (index -> class << 16 | layer), and a structure with the methods the engine's code calls on a texture."""
    arrays = "".join(f"Texture2DArray cBindless{k};\n" for k in range(CLASSES))

    def each(call: str, result: str = "return") -> str:
        cases = "".join(f" case {k}: {result} cBindless{k}.{call};" for k in range(CLASSES))
        return f"switch (Class) {{{cases} }}"

    coordinates = "float3(uv, Layer)"
    methods = [
        f"float4 Sample(SamplerState s, float2 uv) {{ {each(f'Sample(s, {coordinates})')} return 0; }}",
        f"float4 SampleLevel(SamplerState s, float2 uv, float l) {{ {each(f'SampleLevel(s, {coordinates}, l)')} return 0; }}",
        f"float4 SampleBias(SamplerState s, float2 uv, float b) {{ {each(f'SampleBias(s, {coordinates}, b)')} return 0; }}",
        f"float4 SampleGrad(SamplerState s, float2 uv, float2 x, float2 y) {{ {each(f'SampleGrad(s, {coordinates}, x, y)')} "
        "return 0; }",
        *(
            f"float4 Gather{channel}(SamplerState s, float2 uv) {{ {each(f'Gather{channel}(s, {coordinates})')} return 0; }}"
            for channel in ("", "Red", "Green", "Blue", "Alpha")
        ),
        f"float4 Load(int3 p) {{ {each('Load(int4(p.xy, Layer, p.z))')} return 0; }}",
        "float CalculateLevelOfDetailUnclamped(SamplerState s, float2 uv) "
        f"{{ {each('CalculateLevelOfDetailUnclamped(s, uv)')} return 0; }}",
        "float CalculateLevelOfDetail(SamplerState s, float2 uv) "
        f"{{ {each('CalculateLevelOfDetail(s, uv)')} return 0; }}",
        f"void GetDimensions(out uint w, out uint h) {{ uint e; {each('GetDimensions(w, h, e)', '')} }}",
        f"void GetDimensions(out float w, out float h) {{ float e; {each('GetDimensions(w, h, e)', '')} }}",
        "void GetDimensions(uint mip, out uint w, out uint h, out uint n) "
        f"{{ uint e; {each('GetDimensions(mip, w, h, e, n)', '')} }}",
    ]
    return (
        arrays
        + f"cbuffer cb_cBindlessSlots {{ uint4 cBindlessSlots[{SLOTS // 4}]; }};\n"
        + "struct BindlessTex\n{\n    uint Class;\n    uint Layer;\n    "
        + "\n    ".join(methods)
        + "\n};\n"
        + "BindlessTex BindlessAt(uint i) { uint v = cBindlessSlots[i >> 2][i & 3]; "
        "BindlessTex t; t.Class = v >> 16; t.Layer = v & 0xFFFF; return t; }\n"
    )


def _structured(hlsl: str, has_frame_instances: bool = False) -> str:
    """Each ``StructuredBuffer<T>`` as a constant buffer of ``T[n]`` (QRhi binds no structured views), a function
    parameter of that type as the same array; ``RAW_BUFFERS`` and the read-write ones (and with
    ``has_frame_instances`` the instance data, ``FRAME_INSTANCES``) as raw buffers."""

    def length(type_name: str) -> int:
        return LENGTHS.get(type_name, DEFAULT_LENGTH)

    hlsl = rawbuffers.rewrite(hlsl, RAW_BUFFERS + (FRAME_INSTANCES if has_frame_instances else ()))
    # a read-only raw buffer (the planar clouds') read and written alike: QRhi binds storage as unordered access
    hlsl = re.sub(r"(?<!\w)ByteAddressBuffer(\s+\w+\s*;)", r"RWByteAddressBuffer\1", hlsl)

    hlsl = re.sub(
        r"(?m)^\s*StructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)\s*;",
        lambda match: (
            f"cbuffer cb_{match.group(2)} {{ {match.group(1)} {match.group(2)}[{length(match.group(1))}]; }};"
        ),
        hlsl,
    )
    return re.sub(
        r"\bStructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)(?=\s*[,)])",
        lambda match: f"{match.group(1)} {match.group(2)}[{length(match.group(1))}]",
        hlsl,
    )


def _bindless(hlsl: str) -> str:
    """The bindless table as ``BindlessTex`` (above): its declaration, its indexing, and a copy of every function
    that takes a ``Texture2D`` taking the stand-in instead (HLSL picks the copy by the argument's type)."""
    for table in BINDLESS_TABLES:
        hlsl = re.sub(rf"(?m)^\s*Texture2D(?:\s*<\s*\w+\s*>)?\s+{table}\s*\[\s*\d*\s*\]\s*;", "", hlsl)
        while (table_start := hlsl.find(table)) >= 0:
            bracket = hlsl.index("[", table_start)
            close = fxeffect.matching(hlsl, bracket)
            hlsl = hlsl[:table_start] + "BindlessAt(" + hlsl[bracket + 1 : close] + ")" + hlsl[close + 1 :]
    hlsl = re.sub(TEXTURE2D + r"(\s+\w+\s*=\s*BindlessAt\()", r"BindlessTex\1", hlsl)
    # a structure's texture member given a table entry (the bake's SplatTextures) holds the stand-in
    for member in set(re.findall(r"\b\w+\.(\w+)\s*=\s*BindlessAt\(", hlsl)):
        hlsl = re.sub(TEXTURE2D + rf"(\s+{member}\s*;)", r"BindlessTex\1", hlsl)
    out, position, made = [], 0, set()
    header = re.compile(r"(?m)^[\w][\w<>, ]*\s+(\w+)\s*\(([^()]*" + TEXTURE2D + r"[^()]*)\)\s*(;|\n\{)")
    for match in header.finditer(hlsl):
        if match.start() < position:
            continue
        end = match.end() if match.group(3) == ";" else fxeffect.matching(hlsl, match.end() - 1, "{}") + 1
        original = hlsl[match.start() : end]
        parameters = re.sub(TEXTURE2D, "BindlessTex", match.group(2))
        copy = original[: match.start(2) - match.start()] + parameters + original[match.end(2) - match.start() :]
        # overloads by the texture's element type (TexelFetch's float .. float4) give one copy: HLSL cannot
        # overload by return type alone
        signature = (match.group(1), re.sub(r"\s+|\b\w+\s*(?=[,)]|$)", "", parameters), match.group(3))
        out += [hlsl[position:end]] + (["\n", copy] if signature not in made else [])
        made.add(signature)
        position = end
    out.append(hlsl[position:])
    return _bindless_text() + "".join(out)


def declared_types(hlsl: str) -> dict[str, str]:
    """The texture and storage-texture declarations of a shader's text, name -> type (``Texture2D<uint>``,
    ``TextureCube``, ``RWTexture2D<float3>``): the reflection does not say a texture's kind or element type."""
    found = re.findall(r"(?m)^\s*((?:RW)?Texture(?:2D|3D|Cube|2DArray)(?:\s*<\s*\w+\s*>)?)\s+(\w+)\s*;", hlsl)
    return {name: re.sub(r"\s+", "", kind) for kind, name in found}


@dataclass(frozen=True)
class SInput:
    """One vertex input of the entry: its member name, HLSL type and the engine's semantic; ``location`` is where
    QRhi feeds it (the Direct3D backends name every attribute ``TEXCOORD<location>``)."""

    name: str
    type: str
    semantic: str
    location: int


@dataclass
class SAdapted:
    """The text ``adapt`` returns and the vertex inputs it found (empty for any shader but a vertex one)."""

    hlsl: str
    inputs: list[SInput] = field(default_factory=list)


def _input_structs(hlsl: str, entry: str) -> list[str]:
    """The structures the entry takes as inputs, in order (2205's cloth reads three vertex streams)."""
    match = re.search(rf"\b{re.escape(entry)}\s*\(", hlsl)
    if not match:
        return []
    parameters = hlsl[match.end() : fxeffect.matching(hlsl, match.end() - 1, "()")]
    found = re.findall(r"(?:^|,)\s*(?:in\s+)?(\w+)\s+\w+", parameters)
    return [
        struct_name
        for struct_name in found
        if struct_name not in ("out", "inout", "uniform") and re.search(rf"\bstruct\s+{struct_name}\s*\{{", hlsl)
    ]


def vertex_inputs(hlsl: str, entry: str) -> tuple[str, list[SInput]]:
    """The text with the entry's input structures under the semantics QRhi feeds (``TEXCOORD<location>``, numbered
    over all of them), and the inputs with the semantics they had."""
    inputs: list[SInput] = []

    def rename(match: re.Match) -> str:
        if match.group(3) in SYSTEM_INPUTS:
            return match.group(0)
        inputs.append(SInput(match.group(2), match.group(1), match.group(3), len(inputs)))
        return f"{match.group(1)} {match.group(2)} : TEXCOORD{len(inputs) - 1}"

    for name in _input_structs(hlsl, entry):
        struct_match = re.search(rf"\bstruct\s+{name}\s*\{{", hlsl)
        close = fxeffect.matching(hlsl, struct_match.end() - 1, "{}")
        body = re.sub(r"\b(\w+)\s+(\w+)\s*:\s*(\w+)", rename, hlsl[struct_match.end() : close])
        hlsl = hlsl[: struct_match.end()] + body + hlsl[close:]
    return hlsl, inputs


def adapt(hlsl: str, entry: str, is_vertex: bool = False, is_frame: bool = False) -> SAdapted:
    """One pass shader's text bound the way QRhi binds: every register in space 0 and placed by the compiler (the
    reflection says where), root constants as constant buffers, structured buffers as constant-buffer arrays (with
    ``is_frame`` the instance data as the frame's raw buffers), the bindless table as texture arrays, unbounded arrays
    sized, and (``is_vertex``) the entry's input structure under the semantics QRhi feeds."""
    hlsl = re.sub(r":\s*register\s*\(\s*[btu]\d+\s*,\s*space\d+\s*\)", "", hlsl)
    hlsl = re.sub(r":\s*register\s*\(\s*b\d+\s*\)", "", hlsl)
    hlsl = _structured(hlsl, is_frame)
    hlsl = _bindless(hlsl)
    hlsl = re.sub(r"(?m)^(\s*\w+(?:<[^>;]*>)?\s+\w+)\s*\[\s*\]\s*;", r"\1[1];", hlsl)
    inputs: list[SInput] = []
    if is_vertex:
        hlsl, inputs = vertex_inputs(hlsl, entry)
    return SAdapted(hlsl, inputs)


# the inputs by name


def _one_size(variable) -> int:
    """The bytes one element of a variable takes in a constant buffer: a structure up to its last member's end, a
    matrix as its columns (rows when ``row_major``) of 16 bytes but the last, a vector its components."""
    if variable.members:
        return max((member.offset + size_of(member) for member in variable.members), default=0)
    rows, columns = max(variable.rows, 1), max(variable.columns, 1)
    if rows > 1:
        lines, component_count = (rows, columns) if getattr(variable, "row_major", False) else (columns, rows)
        return (lines - 1) * 16 + component_count * 4
    return columns * 4


def size_of(variable) -> int:
    """A variable's size: the reflection's for a buffer's variable, worked out by the packing rules for a
    structure's member (the reflection gives a member none)."""
    if variable.size:
        return variable.size
    one = _one_size(variable)
    return (variable.elements - 1) * math.ceil(one / 16) * 16 + one if variable.elements else one


def _stride(variable) -> int:
    if not variable.elements:
        return size_of(variable)
    if not variable.size:  # a member array: each element starts on a 16-byte row
        return max(16, math.ceil(_one_size(variable) / 16) * 16)
    return max(16, math.ceil(variable.size / variable.elements / 16) * 16)


def _matrix(values: np.ndarray, rows: int, columns: int) -> np.ndarray:
    """``values`` as the ``rows`` x ``columns`` matrix a member declares: a wider value (the fills write 4x4) gives its
    top-left block instead of failing."""
    if values.size == rows * columns:
        return values.reshape(rows, columns).astype(np.float32)
    side = math.isqrt(values.size)
    square = values.reshape(side, side) if side * side == values.size else values.reshape(values.shape[0], -1)
    out = np.zeros((rows, columns), np.float32)
    block = square[:rows, :columns]
    out[: block.shape[0], : block.shape[1]] = block
    return out


@functools.lru_cache(maxsize=256)
def _matrix_shape(type_name: str) -> tuple[int, int] | None:
    match = re.match(r"(?:float|half)(\d)x(\d)", type_name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _is_integer(type_name: str) -> bool:
    return type_name.startswith(("dword", "uint", "int", "bool"))


# the numbers a write packs without numpy (most of a frame's writes: numpy's round trip was most of a write's time)
PLAIN_TYPES = frozenset({float, int, bool, np.float64, np.float32, np.int64, np.int32, np.uint32, np.bool_})
_is_plain = PLAIN_TYPES.__contains__
_g_packers: dict[tuple[int, str], struct.Struct] = {}  # (count, "f" or "I") -> its packer; at most 32 entries


def _plain(value, letter: str) -> bytes | None:
    """A scalar or a short list of plain numbers packed as ``letter`` ("f" float, "I" a 32-bit integer) without
    numpy; None for anything else, or a value numpy packs differently (out of float range)."""
    kind = type(value)
    if kind in PLAIN_TYPES:
        items = (value,)
    elif (kind is list or kind is tuple) and len(value) <= 16 and all(map(_is_plain, map(type, value))):
        items = value
    else:
        return None
    packer = _g_packers.get((len(items), letter))
    if packer is None:
        packer = _g_packers[(len(items), letter)] = struct.Struct(f"<{len(items)}{letter}")
    try:
        if letter == "I":
            return packer.pack(*[int(item) & 0xFFFFFFFF for item in items])
        return packer.pack(*items)
    except (OverflowError, ValueError, struct.error):
        return None


def _letter(type_name: str) -> str | None:
    """How ``_plain`` packs a member of ``type_name`` (None: a matrix, packed by numpy)."""
    return None if _matrix_shape(type_name) is not None else "I" if _is_integer(type_name) else "f"


def _pack(value, type_name: str, is_row_major: bool = False) -> bytes:
    shape = _matrix_shape(type_name)
    if shape is None:
        plain = _plain(value, "I" if _is_integer(type_name) else "f")
        if plain is not None:
            return plain
    array = np.asarray(value)
    if shape is not None:  # each column (each row of a row_major member) padded to four
        matrix = _matrix(array, *shape)
        lines = matrix if is_row_major else matrix.T
        out = np.zeros((lines.shape[0], 4), np.float32)
        out[:, : lines.shape[1]] = lines
        return out.tobytes()
    if _is_integer(type_name):
        return array.astype(np.int64).astype(np.uint32).tobytes()
    return array.astype(np.float32).tobytes()


_PATHS: dict[int, tuple] = {}  # a layout's id -> (its reflection, member path -> its slot): the lookups made once
_UNKNOWN = object()  # a member path not looked up yet (``CConstants.set``)
KEPT_LAYOUTS = 4096  # layouts each cache below keeps (and keeps alive): a session of many islands would hold them all


def _keep(cache: dict, key, value: tuple, limit: int | None = None) -> tuple:
    """``value`` kept under ``key``; the oldest entries go once the cache holds ``limit`` (``KEPT_LAYOUTS`` when none;
    worked out anew when asked for again)."""
    cache.pop(key, None)  # a replaced entry counts as new
    while len(cache) >= (KEPT_LAYOUTS if limit is None else limit):
        del cache[next(iter(cache))]
    cache[key] = value
    return value


class CConstants:
    """One constant buffer laid out by the shader's reflection (``infra``'s ``Reflection.cbuffers`` entry), written by
    member path: ``cCameraBuffer[2].MtxView``."""

    def __init__(self, reflection) -> None:
        self.m_reflection = reflection
        self.m_data = bytearray(max(16, reflection.size))
        kept = _PATHS.get(id(reflection))
        if kept is None or kept[0] is not reflection:
            kept = _keep(_PATHS, id(reflection), (reflection, {}))
        self._m_slots: dict = kept[1]

    def find(self, path: str):
        """(offset, variable) of a member path, or None."""
        slot = self._slot(path)
        return None if slot is None else slot[:2]

    def _slot(self, path: str):
        """(offset, variable, its size, its type, row major, how plain numbers pack: ``_letter``) of a member path,
        or None; looked up once per layout."""
        try:
            return self._m_slots[path]
        except KeyError:
            pass
        level, offset, variable, slot = self.m_reflection.variables, 0, None, None
        for part in path.replace("]", "").split("."):
            name, _, index = part.partition("[")
            variable = next((candidate for candidate in level if candidate.name == name), None)
            if variable is None:
                break
            offset += variable.offset + (int(index) * _stride(variable) if index else 0)
            level = variable.members
        else:
            row_major = getattr(variable, "row_major", False)
            slot = (offset, variable, size_of(variable), variable.type, row_major, _letter(variable.type))
        self._m_slots[path] = slot
        return slot

    def set(self, path: str, value) -> bool:
        slot = self._m_slots.get(path, _UNKNOWN)
        if slot is _UNKNOWN:
            slot = self._slot(path)
        if slot is None:
            return False
        offset, _var, size, type_name, row_major, letter = slot
        raw = _plain(value, letter) if letter is not None else None
        if raw is None:
            raw = _pack(value, type_name, row_major)
        # no further than the member: a wider value must not overwrite its neighbour
        if len(raw) > size or offset + len(raw) > len(self.m_data):
            raw = raw[: max(0, min(size, len(self.m_data) - offset))]
        self.m_data[offset : offset + len(raw)] = raw
        return True

    def set_elements(self, path: str, values: np.ndarray, member: str = "") -> int:
        """An array's elements from 0 on at once (or each element's ``member``): ``values`` [n, ...] as the bytes
        each holds (float32, uint32: one row per element, cut to the array's length); how many were written."""
        found = self.find(path)
        if found is None or (member and self.find(f"{path}[0].{member}") is None):
            return 0
        offset, variable = found
        stride = _stride(variable)
        if member:
            offset = self.find(f"{path}[0].{member}")[0]
        raw = np.ascontiguousarray(values)
        element_count = min(len(raw), max(variable.elements, 1), max(0, (len(self.m_data) - offset) // stride))
        if element_count == 0:
            return 0
        rows = raw[:element_count].reshape(element_count, -1).view(np.uint8)
        width = min(rows.shape[1], stride, len(self.m_data) - offset - (element_count - 1) * stride)
        view = np.frombuffer(self.m_data, np.uint8)
        block = np.lib.stride_tricks.as_strided(view[offset:], (element_count, width), (stride, 1), writeable=True)
        block[:] = rows[:, :width]
        return element_count

    def set_rows(self, path: str, values) -> bool:
        """An array's elements from 0 on, one value each (a bone palette)."""
        found = self.find(path)
        if found is None:
            return False
        offset, variable = found
        stride = _stride(variable)
        values = values[: max(variable.elements, 1)]
        if self._set_plain_rows(offset, variable, stride, values):
            return True
        for k, value in enumerate(values):
            raw = _pack(value, variable.type, getattr(variable, "row_major", False))[: min(stride, size_of(variable))]
            byte_offset = offset + k * stride
            self.m_data[byte_offset : byte_offset + len(raw)] = raw[: max(0, len(self.m_data) - byte_offset)]
        return True

    def _set_plain_rows(self, offset: int, variable, stride: int, values) -> bool:
        """Rows of plain numbers (the light arrays: thousands of them) packed in one numpy step, the bytes
        ``_pack`` gives each; False for anything else, which ``set_rows`` packs one by one."""
        letter = _letter(variable.type)
        if letter is None or len(values) < 2 or not isinstance(values[0], list | tuple | np.ndarray):
            return False
        try:
            rows = np.array(values, np.float64 if letter == "f" else np.int64)
        except (ValueError, TypeError, OverflowError):  # ragged rows, other types, integers past 64 bits
            return False
        if rows.ndim != 2:
            return False
        rows = np.ascontiguousarray(rows.astype(np.float32 if letter == "f" else np.uint32))
        width = min(rows.shape[1] * 4, stride, size_of(variable))
        if offset + (len(rows) - 1) * stride + width > len(self.m_data):  # the last element would be cut: the slow path
            return False
        view = np.frombuffer(self.m_data, np.uint8)
        block = np.lib.stride_tricks.as_strided(view[offset:], (len(rows), width), (stride, 1), writeable=True)
        block[:] = rows.view(np.uint8).reshape(len(rows), -1)[:, :width]
        return True


_replayed: dict[tuple, tuple] = {}  # (a layout's id, a writer's name) -> (its reflection, token, bytes' places, bytes)


def _same(first: tuple, second: tuple) -> bool:
    plain = (int, float, str, bool, type(None))
    return len(first) == len(second) and all(
        left is right or (type(left) is type(right) and isinstance(left, plain) and left == right)
        for left, right in zip(first, second, strict=True)
    )


def replay(constants: CConstants, name: str, token: tuple, write) -> None:
    """``write(c)`` worked out once per layout while ``token`` stays the same (its items compared by identity, plain
    values by value) and its bytes copied into every buffer of that layout: what many draws of a frame share."""
    key = (id(constants.m_reflection), name)
    kept = _replayed.get(key)
    if kept is None or kept[0] is not constants.m_reflection or not _same(kept[1], token):
        kept = _keep(_replayed, key, (constants.m_reflection, token, *_captured(constants.m_reflection, write)))
    if kept[3].size:
        np.frombuffer(constants.m_data, np.uint8)[kept[2]] = kept[3]


def _captured(reflection, write) -> tuple:
    """What ``write(c)`` puts into a buffer of this layout: (where, the bytes), ``where`` a slice when the writer's
    bytes lie in one run (a copy then is one block), else the byte offsets."""
    low, high = CConstants(reflection), CConstants(reflection)
    high.m_data[:] = b"\xff" * len(high.m_data)
    write(low)
    write(high)
    low_bytes, high_bytes = np.frombuffer(low.m_data, np.uint8), np.frombuffer(high.m_data, np.uint8)
    places = np.flatnonzero((low_bytes != 0) | (high_bytes != 0xFF))  # what the writer wrote, whatever was there
    if len(places) and places[-1] - places[0] + 1 == len(places):
        return slice(int(places[0]), int(places[-1]) + 1), low_bytes[places].copy()
    return places, low_bytes[places].copy()


POSES = 1024  # skin packings kept: one per layout and pose (a frame meets dozens of layouts for each skin)
_g_poses: dict[tuple, tuple] = {}  # (a layout's id, place, pose) -> (its reflection, where, the packed bone bytes)


def reset_poses() -> None:
    """Forget the kept skin packings."""
    _g_poses.clear()


def look_at(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    """A right-handed view matrix for row vectors (v' = v M), as the engine's ``MUL(v, M)`` takes it."""
    forward = (target - eye) / np.linalg.norm(target - eye)
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    matrix = np.identity(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2] = right, up, -forward
    matrix[3, :3] = [-right @ eye, -up @ eye, forward @ eye]
    return matrix


def perspective(fov_y_rad: float, aspect: float, near: float, far: float, is_reversed: bool = False) -> np.ndarray:
    """Depth 0 at the near plane, 1 at the far one (row vectors, right-handed); ``is_reversed``: 1 near and 0 far, as
    the game's own projection (rendering/camera.md; its deferred shading takes 0 as nothing drawn)."""
    y_scale = 1.0 / math.tan(fov_y_rad / 2)
    matrix = np.zeros((4, 4))
    matrix[0, 0], matrix[1, 1] = y_scale / aspect, y_scale
    matrix[2, 3] = -1.0
    if is_reversed:
        matrix[2, 2], matrix[3, 2] = near / (far - near), near * far / (far - near)
    else:
        matrix[2, 2], matrix[3, 2] = far / (near - far), near * far / (near - far)
    return matrix


@dataclass(frozen=True)
class SCamera:
    """One view for the forward games (``direct_camera`` makes it): the eye, the view and projection matrices, the
    clip planes and the image size in pixels."""

    eye: np.ndarray
    view: np.ndarray
    projection: np.ndarray
    near: float
    far: float
    size_px: tuple[int, int]
    shadow_far: float = 0.0  # where the sun's cascades end when not at the far plane (0: at it)
    mirror: bool = False  # in the game's own axes: the projection starts with the view's x mirror (``X_MIRROR``)

    @property
    def shadow_distance(self) -> float:
        """How far the sun's cascades reach (``cShadowMaxDistance``)."""
        return self.shadow_far or self.far


X_MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])  # the view's axes are the game's mirrored in x (render.camera.MIRROR)


def direct_camera(
    size_px: tuple[int, int], eye, target, fov_rad: float, near: float, far: float, use_game_axes: bool = False
) -> SCamera:
    """The forward games' and 2070's camera for ``size_px``: depth 0 near and 1 far, the image the way Direct3D draws it
    (their composites turn it over where the device stores images upside down against OpenGL). ``use_game_axes``: the
    view's eye and target mirrored back into the game's axes and the mirror put into the projection, the same clip
    positions to the bit (shader-compile.md 11, "The game's own axes")."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    projection = perspective(fov_rad, size_px[0] / max(1, size_px[1]), near, far)
    if use_game_axes:
        eye, target, projection = X_MIRROR[:3, :3] @ eye, X_MIRROR[:3, :3] @ target, X_MIRROR @ projection
    return SCamera(eye, look_at(eye, target), projection, near, far, size_px, mirror=use_game_axes)


def as_columns(matrix: np.ndarray) -> np.ndarray:
    """A matrix of ours (row vectors, ``v M``, as Direct3D's) as a shader's ``mul(M, v)`` takes it: the old engine's
    setter hands the matrix's memory over as the shader matrix's columns (2070's and 1404's effects)."""
    return np.asarray(matrix, float).T


def camera_values(camera: SCamera, matrix=lambda value: value) -> dict:
    """The camera members the FXC games' buffers share, by name; ``matrix`` turns ours (row vectors) into the
    shader's (2070's ``mul(M, v)`` takes them transposed)."""
    view_proj = camera.view @ camera.projection
    width, height = camera.size_px
    ahead = -camera.view[:3, 2]
    return {
        "cCamPosition": [*camera.eye, 1], "cCamDirection": [*ahead, 0], "cCamLookAt": [*(camera.eye + ahead), 1],
        "cMtxView": matrix(camera.view), "cMtxViewInv": matrix(np.linalg.inv(camera.view)), "cMtxProj": matrix(camera.projection),
        "cMtxProjInv": matrix(np.linalg.inv(camera.projection)), "cMtxViewProj": matrix(view_proj),
        "cMtxViewProjInv": matrix(np.linalg.inv(view_proj)), "cProjDesc": [camera.near, camera.far, 0.75, width / max(1, height)],
    }  # fmt: skip


def left_handed(camera: SCamera) -> SCamera:
    """``camera`` with a left-handed view (view space z ahead) and the same product of view and projection: what the
    forward games' shaders read view-space depth with (soft particles, 1800's cascades; shader-compile.md 11)."""
    turn = np.diag([1.0, 1.0, -1.0, 1.0])
    return replace(camera, view=camera.view @ turn, projection=turn @ camera.projection)


def left_handed_values(camera: SCamera) -> dict:
    """The view and projection members of ``left_handed(camera)``."""
    left_handed_camera = left_handed(camera)
    return {
        "cMtxView": left_handed_camera.view,
        "cMtxViewInv": np.linalg.inv(left_handed_camera.view),
        "cMtxProj": left_handed_camera.projection,
        "cMtxProjInv": np.linalg.inv(left_handed_camera.projection),
    }


def set_values(buffers, values: dict, rows=()) -> None:
    """``values`` (member name -> value) into every buffer of ``buffers`` (``CConstants``) that has a member of that
    name; a name in ``rows`` is an array written element by element (``CConstants.set_rows``)."""
    for constants in buffers:
        for name, value in values.items():
            if name in rows:
                constants.set_rows(name, value)
            else:
                constants.set(name, value)


CAMERAS = 3  # CAMERA_MAIN, CAMERA_REFLECTION, CAMERA_JITTERED


FRAME_NAMES = frozenset({"cCameraBuffer", "cRenderBufferSize", "cBackBufferSize", "cViewportCropFactor",
                         "cUpscaleFactor", "cExposure", "cGammaAdjust", "cCurrWindDirection", "cLastWindDirection",
                         "cCurrWindStrength", "cLastWindStrength"})  # fmt: skip
# what ``fill_instance`` writes: a buffer with none of these members holds no place or pose (``gamedraw``)
INSTANCE_NAMES = frozenset({"cModelPerFrameDataBuffer", "cModelInstanceDataBuffer", "cCurrBoneMatrixBuffer",
                            "cLastBoneMatrixBuffer"})  # fmt: skip
_g_framed: dict[tuple, tuple] = {}  # a frame layout -> (the camera's key, the filled bytes)


def camera_key(camera: SCamera) -> tuple:
    """What a camera's values are made of (its matrices, eye, planes, size, mirror): equal for a still camera's
    frames, worked out once per camera."""
    key = camera.__dict__.get("_key")
    if key is None:
        key = camera.__dict__["_key"] = (np.asarray(camera.view).tobytes(), np.asarray(camera.projection).tobytes(),
                                         np.asarray(camera.eye).tobytes(), camera.near, camera.far,
                                         tuple(camera.size_px), getattr(camera, "shadow_far", 0.0),
                                         getattr(camera, "mirror", False))  # fmt: skip
    return key


def value_key(value):
    """A member's value as a snapshot that compares by content (an array by its bytes, a list item by item); an
    object of another kind gives a new object, equal to nothing (the buffer is filled every time)."""
    if value is None or type(value) in (int, float, bool, str):
        return value
    if isinstance(value, np.ndarray):
        return value.dtype.str, value.shape, value.tobytes()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return tuple(value_key(item) for item in value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return object()


_frame_layouts: dict[int, tuple] = {}  # a layout's id -> (its reflection, its frame layout: the spans and members)


def _members(variable) -> tuple:
    return tuple(
        (member.name, member.offset, member.type, member.elements, _members(member)) for member in variable.members
    )


def _frame_layout(reflection) -> tuple:
    """The frame members' spans and their layout: buffers alike in it take the same frame bytes."""
    kept = _frame_layouts.get(id(reflection))
    if kept is None or kept[0] is not reflection:
        frame = [variable for variable in reflection.variables if variable.name in FRAME_NAMES]
        spans = tuple((variable.offset, variable.offset + size_of(variable)) for variable in frame)
        kept = _keep(
            _frame_layouts,
            id(reflection),
            (reflection, (spans, tuple((variable.name, variable.type, _members(variable)) for variable in frame))),
        )
    return kept[1]


def fill_frame(constants: CConstants, camera: SCamera) -> None:
    """The per-frame values and the three cameras (all one camera here): worked out once per frame layout and
    camera (by its values: a view makes a new camera each frame), copied into every buffer of that layout."""
    layout = _frame_layout(constants.m_reflection)
    if not layout[0]:
        return
    kept = _g_framed.get(layout)
    key = camera_key(camera)
    if kept is None or kept[0] != key:
        scratch = CConstants(constants.m_reflection)
        _fill_frame(scratch, camera)
        kept = _g_framed[layout] = (key, scratch.m_data)
    for start, end in layout[0]:
        constants.m_data[start:end] = kept[1][start:end]


def _fill_frame(constants: CConstants, camera: SCamera) -> None:
    identity = np.identity(4)
    fov = 2 * math.atan(1 / abs(camera.projection[1, 1]))
    aspect = abs(camera.projection[1, 1] / camera.projection[0, 0])
    ahead = np.linalg.inv(camera.view)[2, :3]  # the view's +z in the world
    view_proj = camera.view @ camera.projection
    for i in range(CAMERAS):
        buffer_path = f"cCameraBuffer[{i}]"
        constants.set(f"{buffer_path}.CamPosition", [*camera.eye, 1])
        constants.set(f"{buffer_path}.CamLookAt", [*(camera.eye + ahead), 1])
        constants.set(f"{buffer_path}.CamDirection", [*ahead, 0])
        constants.set(f"{buffer_path}.ProjDesc", [camera.near, camera.far, fov, aspect])
        constants.set(f"{buffer_path}.FOV", [fov, 1 / fov, math.tan(fov / 2), 1])
        for key, matrix in (("MtxView", camera.view), ("MtxProj", camera.projection), ("MtxViewPrev", camera.view),
                       ("MtxProjPrev", camera.projection), ("MtxProjInv", np.linalg.inv(camera.projection)),
                       ("MtxViewUnmirrored", camera.view), ("MtxViewProjUnmirrored", view_proj),
                       ("MtxViewProjPrevMotionBlur", view_proj)):  # fmt: skip
            constants.set(f"{buffer_path}.{key}", matrix)
        for key in ("MtxJitterInv", "MtxJitterInvPrev", "MtxViewProjTex", "MtxDepthProjection"):
            constants.set(f"{buffer_path}.{key}", identity)
    width, height = camera.size_px
    for key, value in (("cRenderBufferSize", [width, height]), ("cBackBufferSize", [width, height]), ("cViewportCropFactor", [1, 1]),
                       ("cUpscaleFactor", 1.0), ("cExposure", 1.0), ("cGammaAdjust", 1.0), *STILL_AIR):  # fmt: skip
        constants.set(key, value)


# the wind the plants and trees sway in: still, from +x (the tree shader normalises the direction: a zero one would
# put every vertex at NaN and the tree would vanish) [ours]
STILL_AIR = (("cCurrWindDirection", [1.0, 0.0]), ("cLastWindDirection", [1.0, 0.0]), ("cCurrWindStrength", 0.0),
             ("cLastWindStrength", 0.0))  # fmt: skip


def light_travel(sun) -> np.ndarray:
    """The way the light travels (unit): ``sun`` points towards the sun (``cLgtDirection``, ``cSunDirection``)."""
    sun = np.asarray(sun, float)
    return -sun / max(np.linalg.norm(sun), 1e-9)


def fill_sun(constants: CConstants, sun, is_night: bool) -> None:
    """The sun's constants of the sky and the shading by name: the light's travel and the day/night weights."""
    travel = light_travel(sun)
    for key, value in (("cSunDirection", travel), ("cLgtDirection", travel), ("cLgtDirectionUncapped", travel),
                       ("cDayNight", [0, 1, 0, 1] if is_night else [1, 0, 1, 0]),
                       ("cTimeOfDay", [0.0, 0, 0, 1] if is_night else [0.5, 1, 0.5, 0])):  # fmt: skip
        constants.set(key, value)


def fill_instance(constants: CConstants, world=None, palette=None, colour: int = 0xFFF) -> None:
    """One instance: the per-frame word (instance 0, alpha 15/15, bone matrices from 0), the object colour (12 bits
    of ``Data.x``; 0 is black), and its place as the engine's bone matrices hold it: ``world`` (column vectors) for
    a rigid model, ``world`` times each joint's skin matrix of ``palette`` for a skin (docs/tool-suite.md section 21)."""
    constants.set("cModelPerFrameDataBuffer[0]", [15 << 22, 0, 0, 0])
    constants.set("cModelInstanceDataBuffer[0].LastIndex", 0xFFFFFFFF)
    constants.set("cModelInstanceDataBuffer[0].Data", [colour << 4, 0, 0])
    world_matrix = np.identity(4) if world is None else np.asarray(world, float)
    if palette is None:
        _set_bones(constants, world_matrix, None)
        return
    # a skin's bones packed once per place and pose, then copied (viewer-performance.md section 4, item 4)
    key = (id(constants.m_reflection), _array_key(world_matrix), _array_key(palette))
    kept = _g_poses.get(key)
    if kept is None or kept[0] is not constants.m_reflection:
        packed = _captured(constants.m_reflection, lambda scratch: _set_bones(scratch, world_matrix, palette))
        kept = _keep(_g_poses, key, (constants.m_reflection, *packed), POSES)
    if kept[2].size:
        np.frombuffer(constants.m_data, np.uint8)[kept[1]] = kept[2]


def _set_bones(constants: CConstants, world_matrix: np.ndarray, palette) -> None:
    bones = [world_matrix] if palette is None else [world_matrix @ skin_matrix for skin_matrix in palette]
    rows = [bone.T[:, :3] for bone in bones]  # the engine's row vectors: v' = [v, 1] M
    for key in ("cCurrBoneMatrixBuffer", "cLastBoneMatrixBuffer"):
        constants.set_rows(key, rows)


def _array_key(values) -> tuple:
    array = np.ascontiguousarray(values)
    return array.shape, array.dtype.str, hashlib.blake2b(array.tobytes(), digest_size=16).digest()


def fill_material(constants: CConstants, fields: dict[str, dict[str, float | list]], slots: dict[str, int]) -> None:
    """The material's structures (``fields``: structure -> field -> value, the fxm defaults with the config over
    them; a texture field is its bindless index from ``slots``, absent: 0, the null texture) and the index table
    pointing at element 0 of each structure the shader reads."""
    for variable in constants.m_reflection.variables:
        if variable.name == "cMaterialComponentIndices":
            for member in variable.members:
                used = member.name.removesuffix("Index") in {structure.removesuffix("Struct") for structure in fields}
                constants.set(f"cMaterialComponentIndices[0].{member.name}", 0 if used else 0xFFFFFFFF)
    for structure, values in fields.items():
        name = "c" + structure.removesuffix("Struct")
        for key, value in {**values, **slots}.items():
            constants.set(f"{name}[0].{key}", value)
