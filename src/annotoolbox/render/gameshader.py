"""Anno 117's own shaders on QRhi: the engine's binding model rewritten into the one QRhi builds, the shader's inputs
filled by name from its reflection, a material's maps uploaded as they are stored, and the draw into a G-buffer.

``adapt(hlsl, entry)`` rewrites a pass shader's text before it is compiled (the caller's, by the game's compiler);
``Constants`` is one constant buffer written by member path, ``fill_*`` the engine's inputs; ``gamedraw`` draws with
them. No Qt here. docs/tool-suite.md section 21, "The game-shader path"; the engine's bindings:
docs/rendering/shader-compile.md section 4.
"""

from __future__ import annotations

import functools
import math
import re
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
# the model shaders' instance data as the engine feeds it, one buffer a frame for all draws (``adapt(frame=True)``:
# raw buffers; the island view, ``gameinstances.FrameInstances``; island-117-view.md "Speed", part 5)
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

    c3 = "float3(uv, Layer)"
    methods = [
        f"float4 Sample(SamplerState s, float2 uv) {{ {each(f'Sample(s, {c3})')} return 0; }}",
        f"float4 SampleLevel(SamplerState s, float2 uv, float l) {{ {each(f'SampleLevel(s, {c3}, l)')} return 0; }}",
        f"float4 SampleBias(SamplerState s, float2 uv, float b) {{ {each(f'SampleBias(s, {c3}, b)')} return 0; }}",
        f"float4 SampleGrad(SamplerState s, float2 uv, float2 x, float2 y) {{ {each(f'SampleGrad(s, {c3}, x, y)')} "
        "return 0; }",
        *(
            f"float4 Gather{ch}(SamplerState s, float2 uv) {{ {each(f'Gather{ch}(s, {c3})')} return 0; }}"
            for ch in ("", "Red", "Green", "Blue", "Alpha")
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


def _structured(hlsl: str, frame: bool = False) -> str:
    """Each ``StructuredBuffer<T>`` as a constant buffer of ``T[n]`` (QRhi binds no structured views), a function
    parameter of that type as the same array; ``RAW_BUFFERS`` and the read-write ones (and with ``frame`` the
    instance data, ``FRAME_INSTANCES``) as raw buffers."""

    def length(t: str) -> int:
        return LENGTHS.get(t, DEFAULT_LENGTH)

    hlsl = rawbuffers.rewrite(hlsl, RAW_BUFFERS + (FRAME_INSTANCES if frame else ()))
    # a read-only raw buffer (the planar clouds') read and written alike: QRhi binds storage as unordered access
    hlsl = re.sub(r"(?<!\w)ByteAddressBuffer(\s+\w+\s*;)", r"RWByteAddressBuffer\1", hlsl)

    hlsl = re.sub(r"(?m)^\s*StructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)\s*;",
                  lambda m: f"cbuffer cb_{m.group(2)} {{ {m.group(1)} {m.group(2)}[{length(m.group(1))}]; }};", hlsl)  # fmt: skip
    return re.sub(r"\bStructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)(?=\s*[,)])",
                  lambda m: f"{m.group(1)} {m.group(2)}[{length(m.group(1))}]", hlsl)  # fmt: skip


def _bindless(hlsl: str) -> str:
    """The bindless table as ``BindlessTex`` (above): its declaration, its indexing, and a copy of every function
    that takes a ``Texture2D`` taking the stand-in instead (HLSL picks the copy by the argument's type)."""
    for table in BINDLESS_TABLES:
        hlsl = re.sub(rf"(?m)^\s*Texture2D(?:\s*<\s*\w+\s*>)?\s+{table}\s*\[\s*\d*\s*\]\s*;", "", hlsl)
        while (at := hlsl.find(table)) >= 0:
            bracket = hlsl.index("[", at)
            close = fxeffect.matching(hlsl, bracket)
            hlsl = hlsl[:at] + "BindlessAt(" + hlsl[bracket + 1 : close] + ")" + hlsl[close + 1 :]
    hlsl = re.sub(TEXTURE2D + r"(\s+\w+\s*=\s*BindlessAt\()", r"BindlessTex\1", hlsl)
    # a structure's texture member given a table entry (the bake's SplatTextures) holds the stand-in
    for member in set(re.findall(r"\b\w+\.(\w+)\s*=\s*BindlessAt\(", hlsl)):
        hlsl = re.sub(TEXTURE2D + rf"(\s+{member}\s*;)", r"BindlessTex\1", hlsl)
    out, pos, made = [], 0, set()
    header = re.compile(r"(?m)^[\w][\w<>, ]*\s+(\w+)\s*\(([^()]*" + TEXTURE2D + r"[^()]*)\)\s*(;|\n\{)")
    for m in header.finditer(hlsl):
        if m.start() < pos:
            continue
        end = m.end() if m.group(3) == ";" else fxeffect.matching(hlsl, m.end() - 1, "{}") + 1
        original = hlsl[m.start() : end]
        params = re.sub(TEXTURE2D, "BindlessTex", m.group(2))
        copy = original[: m.start(2) - m.start()] + params + original[m.end(2) - m.start() :]
        # overloads by the texture's element type (TexelFetch's float .. float4) give one copy: HLSL cannot
        # overload by return type alone
        signature = (m.group(1), re.sub(r"\s+|\b\w+\s*(?=[,)]|$)", "", params), m.group(3))
        out += [hlsl[pos:end]] + (["\n", copy] if signature not in made else [])
        made.add(signature)
        pos = end
    out.append(hlsl[pos:])
    return _bindless_text() + "".join(out)


def declared_types(hlsl: str) -> dict[str, str]:
    """The texture and storage-texture declarations of a shader's text, name -> type (``Texture2D<uint>``,
    ``TextureCube``, ``RWTexture2D<float3>``): the reflection does not say a texture's kind or element type."""
    found = re.findall(r"(?m)^\s*((?:RW)?Texture(?:2D|3D|Cube|2DArray)(?:\s*<\s*\w+\s*>)?)\s+(\w+)\s*;", hlsl)
    return {name: re.sub(r"\s+", "", kind) for kind, name in found}


@dataclass(frozen=True)
class Input:
    """One vertex input of the entry: its member name, HLSL type and the engine's semantic; ``location`` is where
    QRhi feeds it (the Direct3D backends name every attribute ``TEXCOORD<location>``)."""

    name: str
    type: str
    semantic: str
    location: int


@dataclass
class Adapted:
    hlsl: str
    inputs: list[Input] = field(default_factory=list)


def _input_structs(hlsl: str, entry: str) -> list[str]:
    """The structures the entry takes as inputs, in order (2205's cloth reads three vertex streams)."""
    m = re.search(rf"\b{re.escape(entry)}\s*\(", hlsl)
    if not m:
        return []
    params = hlsl[m.end() : fxeffect.matching(hlsl, m.end() - 1, "()")]
    found = re.findall(r"(?:^|,)\s*(?:in\s+)?(\w+)\s+\w+", params)
    return [t for t in found if t not in ("out", "inout", "uniform") and re.search(rf"\bstruct\s+{t}\s*\{{", hlsl)]


def vertex_inputs(hlsl: str, entry: str) -> tuple[str, list[Input]]:
    """The text with the entry's input structures under the semantics QRhi feeds (``TEXCOORD<location>``, numbered
    over all of them), and the inputs with the semantics they had."""
    inputs: list[Input] = []

    def rename(f: re.Match) -> str:
        if f.group(3) in SYSTEM_INPUTS:
            return f.group(0)
        inputs.append(Input(f.group(2), f.group(1), f.group(3), len(inputs)))
        return f"{f.group(1)} {f.group(2)} : TEXCOORD{len(inputs) - 1}"

    for name in _input_structs(hlsl, entry):
        s = re.search(rf"\bstruct\s+{name}\s*\{{", hlsl)
        close = fxeffect.matching(hlsl, s.end() - 1, "{}")
        body = re.sub(r"\b(\w+)\s+(\w+)\s*:\s*(\w+)", rename, hlsl[s.end() : close])
        hlsl = hlsl[: s.end()] + body + hlsl[close:]
    return hlsl, inputs


def adapt(hlsl: str, entry: str, vertex: bool = False, frame: bool = False) -> Adapted:
    """One pass shader's text bound the way QRhi binds: every register in space 0 and placed by the compiler (the
    reflection says where), root constants as constant buffers, structured buffers as constant-buffer arrays (with
    ``frame`` the instance data as the frame's raw buffers), the bindless table as texture arrays, unbounded arrays
    sized, and (``vertex``) the entry's input structure under the semantics QRhi feeds."""
    hlsl = re.sub(r":\s*register\s*\(\s*[btu]\d+\s*,\s*space\d+\s*\)", "", hlsl)
    hlsl = re.sub(r":\s*register\s*\(\s*b\d+\s*\)", "", hlsl)
    hlsl = _structured(hlsl, frame)
    hlsl = _bindless(hlsl)
    hlsl = re.sub(r"(?m)^(\s*\w+(?:<[^>;]*>)?\s+\w+)\s*\[\s*\]\s*;", r"\1[1];", hlsl)
    inputs: list[Input] = []
    if vertex:
        hlsl, inputs = vertex_inputs(hlsl, entry)
    return Adapted(hlsl, inputs)


# the inputs by name


def _one_size(var) -> int:
    """The bytes one element of a variable takes in a constant buffer: a structure up to its last member's end, a
    matrix as its columns (rows when ``row_major``) of 16 bytes but the last, a vector its components."""
    if var.members:
        return max((m.offset + size_of(m) for m in var.members), default=0)
    rows, cols = max(var.rows, 1), max(var.columns, 1)
    if rows > 1:
        lines, length = (rows, cols) if getattr(var, "row_major", False) else (cols, rows)
        return (lines - 1) * 16 + length * 4
    return cols * 4


def size_of(var) -> int:
    """A variable's size: the reflection's for a buffer's variable, worked out by the packing rules for a
    structure's member (the reflection gives a member none)."""
    if var.size:
        return var.size
    one = _one_size(var)
    return (var.elements - 1) * math.ceil(one / 16) * 16 + one if var.elements else one


def _stride(var) -> int:
    if not var.elements:
        return size_of(var)
    if not var.size:  # a member array: each element starts on a 16-byte row
        return max(16, math.ceil(_one_size(var) / 16) * 16)
    return max(16, math.ceil(var.size / var.elements / 16) * 16)


def _matrix(a: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """``a`` as the ``rows`` x ``cols`` matrix a member declares: a wider value (the fills write 4x4) gives its
    top-left block instead of failing."""
    if a.size == rows * cols:
        return a.reshape(rows, cols).astype(np.float32)
    side = math.isqrt(a.size)
    square = a.reshape(side, side) if side * side == a.size else a.reshape(a.shape[0], -1)
    out = np.zeros((rows, cols), np.float32)
    block = square[:rows, :cols]
    out[: block.shape[0], : block.shape[1]] = block
    return out


@functools.lru_cache(maxsize=256)
def _matrix_shape(type_name: str) -> tuple[int, int] | None:
    m = re.match(r"(?:float|half)(\d)x(\d)", type_name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _pack(value, type_name: str, row_major: bool = False) -> bytes:
    a = np.asarray(value)
    shape = _matrix_shape(type_name)
    if shape is not None:  # each column (each row of a row_major member) padded to four
        mat = _matrix(a, *shape)
        lines = mat if row_major else mat.T
        out = np.zeros((lines.shape[0], 4), np.float32)
        out[:, : lines.shape[1]] = lines
        return out.tobytes()
    if type_name.startswith(("dword", "uint", "int", "bool")):
        return a.astype(np.int64).astype(np.uint32).tobytes()
    return a.astype(np.float32).tobytes()


_PATHS: dict[int, tuple] = {}  # a layout's id -> (its reflection, member path -> its slot): the lookups made once
KEPT_LAYOUTS = 4096  # layouts each cache below keeps (and keeps alive): a session of many islands would hold them all


def _keep(cache: dict, key, value: tuple) -> tuple:
    """``value`` kept under ``key``; the oldest entries go once the cache holds ``KEPT_LAYOUTS`` (worked out anew
    when asked for again)."""
    cache.pop(key, None)  # a replaced entry counts as new
    while len(cache) >= KEPT_LAYOUTS:
        del cache[next(iter(cache))]
    cache[key] = value
    return value


class Constants:
    """One constant buffer laid out by the shader's reflection (``infra``'s ``Reflection.cbuffers`` entry), written by
    member path: ``cCameraBuffer[2].MtxView``."""

    def __init__(self, reflection) -> None:
        self.reflection = reflection
        self.data = bytearray(max(16, reflection.size))
        kept = _PATHS.get(id(reflection))
        if kept is None or kept[0] is not reflection:
            kept = _keep(_PATHS, id(reflection), (reflection, {}))
        self._slots: dict = kept[1]

    def find(self, path: str):
        """(offset, variable) of a member path, or None."""
        slot = self._slot(path)
        return None if slot is None else slot[:2]

    def _slot(self, path: str):
        """(offset, variable, its size, its type, row major) of a member path, or None; looked up once per layout."""
        try:
            return self._slots[path]
        except KeyError:
            pass
        level, offset, var, slot = self.reflection.variables, 0, None, None
        for part in path.replace("]", "").split("."):
            name, _, index = part.partition("[")
            var = next((v for v in level if v.name == name), None)
            if var is None:
                break
            offset += var.offset + (int(index) * _stride(var) if index else 0)
            level = var.members
        else:
            slot = (offset, var, size_of(var), var.type, getattr(var, "row_major", False))
        self._slots[path] = slot
        return slot

    def set(self, path: str, value) -> bool:
        slot = self._slot(path)
        if slot is None:
            return False
        offset, _var, size, type_name, row_major = slot
        # no further than the member: a wider value must not overwrite its neighbour
        raw = _pack(value, type_name, row_major)[: max(0, min(size, len(self.data) - offset))]
        self.data[offset : offset + len(raw)] = raw
        return True

    def set_elements(self, path: str, values: np.ndarray, member: str = "") -> int:
        """An array's elements from 0 on at once (or each element's ``member``): ``values`` [n, ...] as the bytes
        each holds (float32, uint32: one row per element, cut to the array's length); how many were written."""
        found = self.find(path)
        if found is None or (member and self.find(f"{path}[0].{member}") is None):
            return 0
        offset, var = found
        stride = _stride(var)
        if member:
            offset = self.find(f"{path}[0].{member}")[0]
        raw = np.ascontiguousarray(values)
        n = min(len(raw), max(var.elements, 1), max(0, (len(self.data) - offset) // stride))
        if n == 0:
            return 0
        rows = raw[:n].reshape(n, -1).view(np.uint8)
        width = min(rows.shape[1], stride, len(self.data) - offset - (n - 1) * stride)
        view = np.frombuffer(self.data, np.uint8)
        block = np.lib.stride_tricks.as_strided(view[offset:], (n, width), (stride, 1), writeable=True)
        block[:] = rows[:, :width]
        return n

    def set_rows(self, path: str, values) -> bool:
        """An array's elements from 0 on, one value each (a bone palette)."""
        found = self.find(path)
        if found is None:
            return False
        offset, var = found
        stride = _stride(var)
        for k, value in enumerate(values[: max(var.elements, 1)]):
            raw = _pack(value, var.type, getattr(var, "row_major", False))[: min(stride, size_of(var))]
            at = offset + k * stride
            self.data[at : at + len(raw)] = raw[: max(0, len(self.data) - at)]
        return True


_replayed: dict[tuple, tuple] = {}  # (a layout's id, a writer's name) -> (its reflection, token, bytes' places, bytes)


def _same(a: tuple, b: tuple) -> bool:
    plain = (int, float, str, bool, type(None))
    return len(a) == len(b) and all(x is y or (type(x) is type(y) and isinstance(x, plain) and x == y)
                                    for x, y in zip(a, b, strict=True))  # fmt: skip


def replay(c: Constants, name: str, token: tuple, write) -> None:
    """``write(c)`` worked out once per layout while ``token`` stays the same (its items compared by identity, plain
    values by value) and its bytes copied into every buffer of that layout: what many draws of a frame share."""
    key = (id(c.reflection), name)
    kept = _replayed.get(key)
    if kept is None or kept[0] is not c.reflection or not _same(kept[1], token):
        low, high = Constants(c.reflection), Constants(c.reflection)
        high.data[:] = b"\xff" * len(high.data)
        write(low)
        write(high)
        a, b = np.frombuffer(low.data, np.uint8), np.frombuffer(high.data, np.uint8)
        places = np.flatnonzero((a != 0) | (b != 0xFF))  # what the writer wrote, whatever was there
        kept = _keep(_replayed, key, (c.reflection, token, places, a[places].copy()))
    if len(kept[2]):
        np.frombuffer(c.data, np.uint8)[kept[2]] = kept[3]


def look_at(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    """A right-handed view matrix for row vectors (v' = v M), as the engine's ``MUL(v, M)`` takes it."""
    f = (target - eye) / np.linalg.norm(target - eye)
    r = np.cross(f, [0.0, 1.0, 0.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    m = np.identity(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = r, u, -f
    m[3, :3] = [-r @ eye, -u @ eye, f @ eye]
    return m


def perspective(fov_y: float, aspect: float, near: float, far: float, reverse: bool = False) -> np.ndarray:
    """Depth 0 at the near plane, 1 at the far one (row vectors, right-handed); ``reverse``: 1 near and 0 far, as
    the game's own projection (rendering/camera.md; its deferred shading takes 0 as nothing drawn)."""
    ys = 1.0 / math.tan(fov_y / 2)
    m = np.zeros((4, 4))
    m[0, 0], m[1, 1] = ys / aspect, ys
    m[2, 3] = -1.0
    if reverse:
        m[2, 2], m[3, 2] = near / (far - near), near * far / (far - near)
    else:
        m[2, 2], m[3, 2] = far / (near - far), near * far / (near - far)
    return m


@dataclass(frozen=True)
class Camera:
    eye: np.ndarray
    view: np.ndarray
    proj: np.ndarray
    near: float
    far: float
    size: tuple[int, int]
    shadow_far: float = 0.0  # where the sun's cascades end when not at the far plane (0: at it)
    mirror: bool = False  # in the game's own axes: the projection starts with the view's x mirror (``X_MIRROR``)

    @property
    def shadow_distance(self) -> float:
        """How far the sun's cascades reach (``cShadowMaxDistance``)."""
        return self.shadow_far or self.far


X_MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])  # the view's axes are the game's mirrored in x (render.camera.MIRROR)


def direct_camera(size: tuple[int, int], eye, target, fov: float, near: float, far: float,
                  game_axes: bool = False) -> Camera:  # fmt: skip
    """The forward games' and 2070's camera for ``size``: depth 0 near and 1 far, the image the way Direct3D draws it
    (their composites turn it over where the device stores images upside down against OpenGL). ``game_axes``: the
    view's eye and target mirrored back into the game's axes and the mirror put into the projection, the same clip
    positions to the bit (shader-compile.md 11, "The game's own axes")."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    proj = perspective(fov, size[0] / max(1, size[1]), near, far)
    if game_axes:
        eye, target, proj = X_MIRROR[:3, :3] @ eye, X_MIRROR[:3, :3] @ target, X_MIRROR @ proj
    return Camera(eye, look_at(eye, target), proj, near, far, size, mirror=game_axes)


def as_columns(m: np.ndarray) -> np.ndarray:
    """A matrix of ours (row vectors, ``v M``, as Direct3D's) as a shader's ``mul(M, v)`` takes it: the old engine's
    setter hands the matrix's memory over as the shader matrix's columns (2070's and 1404's effects)."""
    return np.asarray(m, float).T


def camera_values(cam: Camera, matrix=lambda m: m) -> dict:
    """The camera members the FXC games' buffers share, by name; ``matrix`` turns ours (row vectors) into the
    shader's (2070's ``mul(M, v)`` takes them transposed)."""
    view_proj = cam.view @ cam.proj
    w, h = cam.size
    ahead = -cam.view[:3, 2]
    return {
        "cCamPosition": [*cam.eye, 1], "cCamDirection": [*ahead, 0], "cCamLookAt": [*(cam.eye + ahead), 1],
        "cMtxView": matrix(cam.view), "cMtxViewInv": matrix(np.linalg.inv(cam.view)), "cMtxProj": matrix(cam.proj),
        "cMtxProjInv": matrix(np.linalg.inv(cam.proj)), "cMtxViewProj": matrix(view_proj),
        "cMtxViewProjInv": matrix(np.linalg.inv(view_proj)), "cProjDesc": [cam.near, cam.far, 0.75, w / max(1, h)],
    }  # fmt: skip


def left_handed(cam: Camera) -> Camera:
    """``cam`` with a left-handed view (view space z ahead) and the same product of view and projection: what the
    forward games' shaders read view-space depth with (soft particles, 1800's cascades; shader-compile.md 11)."""
    turn = np.diag([1.0, 1.0, -1.0, 1.0])
    return replace(cam, view=cam.view @ turn, proj=turn @ cam.proj)


def left_handed_values(cam: Camera) -> dict:
    """The view and projection members of ``left_handed(cam)``."""
    lh = left_handed(cam)
    return {"cMtxView": lh.view, "cMtxViewInv": np.linalg.inv(lh.view), "cMtxProj": lh.proj,
            "cMtxProjInv": np.linalg.inv(lh.proj)}  # fmt: skip


def set_values(buffers, values: dict, rows=()) -> None:
    """``values`` (member name -> value) into every buffer of ``buffers`` (``Constants``) that has a member of that
    name; a name in ``rows`` is an array written element by element (``Constants.set_rows``)."""
    for c in buffers:
        for name, value in values.items():
            if name in rows:
                c.set_rows(name, value)
            else:
                c.set(name, value)


CAMERAS = 3  # CAMERA_MAIN, CAMERA_REFLECTION, CAMERA_JITTERED


FRAME_NAMES = frozenset({"cCameraBuffer", "cRenderBufferSize", "cBackBufferSize", "cViewportCropFactor",
                         "cUpscaleFactor", "cExposure", "cGammaAdjust", "cCurrWindDirection", "cLastWindDirection",
                         "cCurrWindStrength", "cLastWindStrength"})  # fmt: skip
_framed: dict[tuple, tuple] = {}  # a frame layout -> (the camera, the filled bytes)
_frame_layouts: dict[int, tuple] = {}  # a layout's id -> (its reflection, its frame layout: the spans and members)


def _members(var) -> tuple:
    return tuple((m.name, m.offset, m.type, m.elements, _members(m)) for m in var.members)


def _frame_layout(reflection) -> tuple:
    """The frame members' spans and their layout: buffers alike in it take the same frame bytes."""
    kept = _frame_layouts.get(id(reflection))
    if kept is None or kept[0] is not reflection:
        frame = [v for v in reflection.variables if v.name in FRAME_NAMES]
        spans = tuple((v.offset, v.offset + size_of(v)) for v in frame)
        kept = _frame_layouts[id(reflection)] = (
            reflection,
            (spans, tuple((v.name, v.type, _members(v)) for v in frame)),
        )
    return kept[1]


def fill_frame(c: Constants, cam: Camera) -> None:
    """The per-frame values and the three cameras (all one camera here): worked out once per frame layout and
    camera, copied into every buffer of that layout (a frame of many draws shares one)."""
    layout = _frame_layout(c.reflection)
    if not layout[0]:
        return
    kept = _framed.get(layout)
    if kept is None or kept[0] is not cam:
        scratch = Constants(c.reflection)
        _fill_frame(scratch, cam)
        kept = _framed[layout] = (cam, scratch.data)
    for a, b in layout[0]:
        c.data[a:b] = kept[1][a:b]


def _fill_frame(c: Constants, cam: Camera) -> None:
    ident = np.identity(4)
    fov = 2 * math.atan(1 / abs(cam.proj[1, 1]))
    aspect = abs(cam.proj[1, 1] / cam.proj[0, 0])
    ahead = np.linalg.inv(cam.view)[2, :3]  # the view's +z in the world
    view_proj = cam.view @ cam.proj
    for i in range(CAMERAS):
        k = f"cCameraBuffer[{i}]"
        c.set(f"{k}.CamPosition", [*cam.eye, 1])
        c.set(f"{k}.CamLookAt", [*(cam.eye + ahead), 1])
        c.set(f"{k}.CamDirection", [*ahead, 0])
        c.set(f"{k}.ProjDesc", [cam.near, cam.far, fov, aspect])
        c.set(f"{k}.FOV", [fov, 1 / fov, math.tan(fov / 2), 1])
        for key, m in (("MtxView", cam.view), ("MtxProj", cam.proj), ("MtxViewPrev", cam.view),
                       ("MtxProjPrev", cam.proj), ("MtxProjInv", np.linalg.inv(cam.proj)),
                       ("MtxViewUnmirrored", cam.view), ("MtxViewProjUnmirrored", view_proj),
                       ("MtxViewProjPrevMotionBlur", view_proj)):  # fmt: skip
            c.set(f"{k}.{key}", m)
        for key in ("MtxJitterInv", "MtxJitterInvPrev", "MtxViewProjTex", "MtxDepthProjection"):
            c.set(f"{k}.{key}", ident)
    w, h = cam.size
    for key, value in (("cRenderBufferSize", [w, h]), ("cBackBufferSize", [w, h]), ("cViewportCropFactor", [1, 1]),
                       ("cUpscaleFactor", 1.0), ("cExposure", 1.0), ("cGammaAdjust", 1.0), *STILL_AIR):  # fmt: skip
        c.set(key, value)


# the wind the plants and trees sway in: still, from +x (the tree shader normalises the direction: a zero one would
# put every vertex at NaN and the tree would vanish) [ours]
STILL_AIR = (("cCurrWindDirection", [1.0, 0.0]), ("cLastWindDirection", [1.0, 0.0]), ("cCurrWindStrength", 0.0),
             ("cLastWindStrength", 0.0))  # fmt: skip


def light_travel(sun) -> np.ndarray:
    """The way the light travels (unit): ``sun`` points towards the sun (``cLgtDirection``, ``cSunDirection``)."""
    sun = np.asarray(sun, float)
    return -sun / max(np.linalg.norm(sun), 1e-9)


def fill_sun(c: Constants, sun, night: bool) -> None:
    """The sun's constants of the sky and the shading by name: the light's travel and the day/night weights."""
    travel = light_travel(sun)
    for key, value in (("cSunDirection", travel), ("cLgtDirection", travel), ("cLgtDirectionUncapped", travel),
                       ("cDayNight", [0, 1, 0, 1] if night else [1, 0, 1, 0]),
                       ("cTimeOfDay", [0.0, 0, 0, 1] if night else [0.5, 1, 0.5, 0])):  # fmt: skip
        c.set(key, value)


def fill_instance(c: Constants, world=None, palette=None, colour: int = 0xFFF) -> None:
    """One instance: the per-frame word (instance 0, alpha 15/15, bone matrices from 0), the object colour (12 bits
    of ``Data.x``; 0 is black), and its place as the engine's bone matrices hold it: ``world`` (column vectors) for
    a rigid model, ``world`` times each joint's skin matrix of ``palette`` for a skin (docs/tool-suite.md section 21)."""
    c.set("cModelPerFrameDataBuffer[0]", [15 << 22, 0, 0, 0])
    c.set("cModelInstanceDataBuffer[0].LastIndex", 0xFFFFFFFF)
    c.set("cModelInstanceDataBuffer[0].Data", [colour << 4, 0, 0])
    w = np.identity(4) if world is None else np.asarray(world, float)
    bones = [w] if palette is None else [w @ m for m in palette]
    rows = [b.T[:, :3] for b in bones]  # the engine's row vectors: v' = [v, 1] M
    for key in ("cCurrBoneMatrixBuffer", "cLastBoneMatrixBuffer"):
        c.set_rows(key, rows)


def fill_material(c: Constants, fields: dict[str, dict[str, float | list]], slots: dict[str, int]) -> None:
    """The material's structures (``fields``: structure -> field -> value, the fxm defaults with the config over
    them; a texture field is its bindless index from ``slots``, absent: 0, the null texture) and the index table
    pointing at element 0 of each structure the shader reads."""
    for v in c.reflection.variables:
        if v.name == "cMaterialComponentIndices":
            for m in v.members:
                used = m.name.removesuffix("Index") in {s.removesuffix("Struct") for s in fields}
                c.set(f"cMaterialComponentIndices[0].{m.name}", 0 if used else 0xFFFFFFFF)
    for struct, values in fields.items():
        name = "c" + struct.removesuffix("Struct")
        for key, value in {**values, **slots}.items():
            c.set(f"{name}[0].{key}", value)
