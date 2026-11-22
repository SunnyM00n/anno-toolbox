"""Microsoft's DirectX shader compiler (DXC) through its C API, loaded at run time: compile HLSL to DXIL, preprocess,
and read a compiled shader's bindings and signatures (docs/rendering/shader-compile.md, "The DXC API").

``CDxc.find(anno117=...)`` is the entry: ``ANNO_DXC_DIR``, else the compiler the game ships in its ``Bin/Win64`` folder
(never copied, never shipped), else ``tools/`` (Microsoft's open-source release in ``tools/dxc/``). ``CDxc.compile`` returns a
``SCompiled`` with the bytecode and the error text; ``CDxc.reflect`` a shader's ``SReflection``. Windows only (the
compiler is a Windows DLL); elsewhere ``find`` raises ``CDxcMissing``.
"""

from __future__ import annotations

import ctypes
import json
import os
import struct
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import shadertrack
from .com import HRESULT, POINTER, SGuid, blob_bytes, c_void_p, file_version, method, release
from .i18n import N_, english, tr
from .tools import candidates

DXC_ENV = "ANNO_DXC_DIR"
GAME_DXC_DIR = Path("Bin") / "Win64"
WHERE_TO_GET = N_("common.dxc_where_get")  # the id of the sentence that says where they are from
# DXC_OUT_KIND
OUT_OBJECT, OUT_ERRORS, OUT_HLSL, OUT_REFLECTION = 1, 2, 6, 8
# D3D_SHADER_INPUT_TYPE, by number
INPUT_TYPES = (
    "cbuffer", "tbuffer", "texture", "sampler", "uav_rwtyped", "structured", "uav_rwstructured", "byteaddress",
    "uav_rwbyteaddress", "uav_append_structured", "uav_consume_structured", "uav_rwstructured_with_counter",
    "rtaccelerationstructure", "uav_feedbacktexture",
)  # fmt: skip


class CCompilerMissing(RuntimeError):
    """No shader compiler that loads: its text is in the interface language (a greyed-out look shows it as the
    reason), ``english`` the same sentence for a log or the command line."""

    def __init__(self, text: str, english: str = "") -> None:
        super().__init__(text)
        self.m_english = english or text

    @classmethod
    def of(cls, ident: str, where: str, failed: list[Exception] | tuple = (), **fields: object) -> CCompilerMissing:
        """The sentence of the string id ``ident`` with the sentence ``where`` (an id) that says where the compiler is
        from, and why each copy ``failed`` to load."""

        def said(text, in_english: bool) -> str:
            whys = "".join(f"; {getattr(error, 'english', error) if in_english else error}" for error in failed)
            return text(ident, where=text(where), failed=whys, **fields)

        return cls(said(tr, False), said(english, True))


class CDxcMissing(CCompilerMissing):
    pass


CLSID_COMPILER = SGuid.of("73e22d93-e6ce-47f3-b5bf-f0664f39c1b0")
CLSID_UTILS = SGuid.of("6245d6af-66e0-48fd-80b4-4d271796748c")
IID_COMPILER3 = SGuid.of("228b4687-5a6a-4730-900c-9702b2203f54")
IID_UTILS = SGuid.of("4605c4cb-2019-492a-ada4-65f20bb7d67f")
IID_RESULT = SGuid.of("58346cda-dde7-4497-9461-6f87af5e0659")
IID_BLOB = SGuid.of("8ba5fb08-5195-40e2-ac58-0d989c3a0102")
IID_REFLECTION = SGuid.of("5a58797d-a72c-478d-8ba2-efc6b0efe88e")  # ID3D12ShaderReflection


class _SBuffer(ctypes.Structure):
    _fields_ = [("ptr", c_void_p), ("size", ctypes.c_size_t), ("encoding", ctypes.c_uint)]


class _SShaderDesc(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("creator", ctypes.c_char_p), ("flags", ctypes.c_uint)] + [
        (n, ctypes.c_uint)
        for n in ("cbuffers", "resources", "inputs", "outputs")
        + tuple(f"_u{i}" for i in range(33))  # the instruction counts and the tessellation fields
    ]


class _SBindDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p)] + [
        (n, ctypes.c_uint)
        for n in ("type", "bind_point", "bind_count", "flags", "return_type", "dimension", "samples", "space", "id")
    ]


class _SSignatureDesc(ctypes.Structure):
    _fields_ = [("semantic", ctypes.c_char_p), ("index", ctypes.c_uint), ("register", ctypes.c_uint)] + [
        ("system_value", ctypes.c_uint), ("component_type", ctypes.c_uint), ("mask", ctypes.c_ubyte),
        ("rw_mask", ctypes.c_ubyte), ("stream", ctypes.c_uint), ("min_precision", ctypes.c_uint),
    ]  # fmt: skip


class _SBufferDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("type", ctypes.c_uint), ("variables", ctypes.c_uint)] + [
        ("size", ctypes.c_uint), ("flags", ctypes.c_uint),
    ]  # fmt: skip


class _SVariableDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("offset", ctypes.c_uint), ("size", ctypes.c_uint)] + [
        ("flags", ctypes.c_uint), ("default", c_void_p), ("start_texture", ctypes.c_uint),
        ("texture_size", ctypes.c_uint), ("start_sampler", ctypes.c_uint), ("sampler_size", ctypes.c_uint),
    ]  # fmt: skip


@dataclass
class SCompiled:
    """One compile: the bytecode (empty when it failed) and DXC's messages."""

    ok: bool
    object: bytes
    messages: str


@dataclass
class SBinding:
    name: str
    kind: str  # INPUT_TYPES
    register: int
    count: int  # 0: an unbounded array
    space: int
    flags: int = 0  # D3D_SHADER_INPUT_FLAGS: 2 a comparison sampler
    dimension: int = 0  # D3D_SRV_DIMENSION: 4 a 2D texture, 5 an array, 8 3D, 9 a cube (0: a reflection kept before)
    returns: int = 0  # D3D_RESOURCE_RETURN_TYPE of a texture: 3 int, 4 uint, 5 float (0: not known)


@dataclass
class SVariable:
    """A constant buffer's variable, or a structure's member (``offset`` from its parent's start)."""

    name: str
    offset: int
    size: int
    type: str = ""  # the HLSL type name (float4x4, CameraData, ...)
    rows: int = 0
    columns: int = 0
    elements: int = 0  # an array's length, 0 for one value
    row_major: bool = False  # a matrix stored by rows (D3D_SVC_MATRIX_ROWS)
    members: list[SVariable] = field(default_factory=list)


@dataclass
class SConstantBuffer:
    name: str
    size: int
    variables: list[SVariable] = field(default_factory=list)


@dataclass
class SReflection:
    """What a compiled shader binds and passes: its resources, constant buffers and signatures."""

    version: int  # the shader model: (type << 16) | (major << 4) | minor
    bindings: list[SBinding]
    cbuffers: list[SConstantBuffer]
    inputs: list[tuple[str, int, int]]  # (semantic, index, register)
    outputs: list[tuple[str, int, int]]

    @property
    def model(self) -> str:
        return f"{(self.version >> 4) & 0xF}_{self.version & 0xF}"


def reflection_json(reflection: SReflection) -> bytes:
    """A reflection as JSON, to keep beside its bytecode (``reflection_of_json`` reads it back equal)."""
    return json.dumps(asdict(reflection), separators=(",", ":")).encode("utf-8")


def _variable(data: dict) -> SVariable:
    return SVariable(**{**data, "members": [_variable(member) for member in data["members"]]})


def reflection_of_json(data: bytes) -> SReflection:
    """The reflection ``reflection_json`` kept, equal to the one the compiler gives (its signatures as tuples)."""
    document = json.loads(data)
    return SReflection(
        document["version"],
        [SBinding(**binding) for binding in document["bindings"]],
        [
            SConstantBuffer(
                constant_buffer["name"],
                constant_buffer["size"],
                [_variable(variable) for variable in constant_buffer["variables"]],
            )
            for constant_buffer in document["cbuffers"]
        ],
        [tuple(element) for element in document["inputs"]],
        [tuple(element) for element in document["outputs"]],
    )


_g_threads = threading.local()  # per thread: folder -> its own ``CDxc`` (``CDxc.for_thread``)


class CDxc:
    """A loaded compiler: ``compile`` and ``preprocess`` HLSL, ``reflect`` DXIL."""

    def __init__(self, folder: Path) -> None:
        if os.name != "nt":
            raise CDxcMissing.of(N_("common.dxc_windows_dll_here"), WHERE_TO_GET)
        self.m_folder = folder
        try:  # dxcompiler loads dxil.dll by name: loaded first, from the same folder, it is found
            self._m_dxil = ctypes.WinDLL(str(folder / "dxil.dll"))
            self._m_dll = ctypes.WinDLL(str(folder / "dxcompiler.dll"))
        except OSError as error:
            raise CDxcMissing(f"{folder}: {error}") from error
        create = self._m_dll.DxcCreateInstance
        create.argtypes = [POINTER(SGuid), POINTER(SGuid), POINTER(c_void_p)]
        create.restype = HRESULT
        self._m_compiler, self._m_utils = c_void_p(), c_void_p()
        self._m_version: str | None = None
        if create(CLSID_COMPILER, IID_COMPILER3, ctypes.byref(self._m_compiler)) or create(
            CLSID_UTILS, IID_UTILS, ctypes.byref(self._m_utils)
        ):
            release(self._m_compiler.value)  # the one that was made is not kept by a refused instance
            release(self._m_utils.value)
            raise CDxcMissing.of(N_("common.folder_dxc_create_instance_failed"), WHERE_TO_GET, folder=folder)

    @classmethod
    def find(cls, anno117: str | Path | None = None) -> CDxc:
        """The compiler from ``ANNO_DXC_DIR`` (folders), else the game's when ``anno117`` is given and has one, else
        the one in ``tools/``; it needs ``dxil.dll`` beside it, and a copy that does not load leaves the next to try."""
        found = candidates(
            ["dxcompiler.dll"], env=DXC_ENV, roots=[anno117] if anno117 else [], subdirs=[GAME_DXC_DIR],
            search_repo_tools=True, accept=lambda candidate: candidate.is_file() and (candidate.parent / "dxil.dll").is_file(),
        )  # fmt: skip
        failed = []
        for path in found:
            try:
                return cls(path.parent)
            except CDxcMissing as error:
                failed.append(error)
        raise CDxcMissing.of(N_("common.no_dxc_found"), WHERE_TO_GET, failed)

    def version(self) -> str:
        """The compiler's file version, as Windows reports it (read once)."""
        if self._m_version is None:
            self._m_version = file_version(self.m_folder / "dxcompiler.dll")
        return self._m_version

    def for_thread(self) -> CDxc:
        """This thread's instance of the same compiler, made once per thread: DXC's objects serve one thread at a
        time, so workers that each hold their own compile side by side (shader-compile.md 6)."""
        own = getattr(_g_threads, "compilers", None)
        if own is None:
            own = _g_threads.compilers = {}
        found = own.get(self.m_folder)
        if found is None:
            found = own[self.m_folder] = CDxc(self.m_folder)
        return found

    def _run(self, source: str, arguments: list[str]) -> tuple[int, int]:
        """Compile with DXC's arguments; returns (status, result object), the caller releases the result."""
        data = source.encode("utf-8")
        buffer = _SBuffer(ctypes.cast(ctypes.c_char_p(data), c_void_p), len(data), 65001)
        argv = (ctypes.c_wchar_p * len(arguments))(*arguments)
        result = c_void_p()
        call = method(
            self._m_compiler, 3, POINTER(_SBuffer), c_void_p, ctypes.c_uint32, c_void_p, POINTER(SGuid), c_void_p
        )
        hr = call(self._m_compiler, ctypes.byref(buffer), argv, len(arguments), None, IID_RESULT, ctypes.byref(result))
        if hr or not result:
            raise RuntimeError(f"DXC Compile failed: 0x{hr & 0xFFFFFFFF:08x}")
        status = HRESULT()
        method(result, 3, POINTER(HRESULT))(result, ctypes.byref(status))
        return status.value, result.value

    def _output(self, result: int, kind: int) -> bytes:
        blob = c_void_p()
        get = method(result, 7, ctypes.c_int, POINTER(SGuid), POINTER(c_void_p), c_void_p)
        if get(result, kind, IID_BLOB, ctypes.byref(blob), None) or not blob:
            return b""
        try:
            return blob_bytes(blob.value, release=False)
        finally:
            release(blob.value)

    def _text(self, result: int, kind: int) -> str:
        return self._output(result, kind).decode("utf-8", "replace").rstrip("\0")

    def compile(self, source: str, arguments: list[str], name: str = "shader.hlsl") -> SCompiled:
        """Compile ``source`` with DXC's command-line ``args`` (``-E``, ``-T``, options; no include handler: the
        source must be whole, as the game passes it). Shown while it runs (``shadertrack``)."""
        entry = arguments[arguments.index("-E") + 1] if "-E" in arguments[:-1] else ""
        with shadertrack.compiling(f"{name} {entry}".strip()):
            status, result = self._run(source, [name, *arguments])
        try:
            messages = self._text(result, OUT_ERRORS)
            object_code = self._output(result, OUT_OBJECT) if status == 0 else b""
        finally:
            release(result)
        return SCompiled(status == 0 and bool(object_code), object_code, messages)

    def preprocess(self, source: str, arguments: list[str], name: str = "shader.hlsl") -> tuple[str, str]:
        """``source`` run through DXC's preprocessor (``-P``): the text and the messages (empty text on an error)."""
        status, result = self._run(source, [name, "-P", *arguments])
        try:
            messages = self._text(result, OUT_ERRORS)
            text = self._text(result, OUT_HLSL) if status == 0 else ""
        finally:
            release(result)
        return text, messages

    def reflect(self, dxil: bytes) -> SReflection:
        """The bindings, constant buffers and signatures of a compiled shader (a DXBC container with DXIL)."""
        holder = ctypes.create_string_buffer(dxil, len(dxil))
        buffer = _SBuffer(ctypes.cast(holder, c_void_p), len(dxil), 0)
        refl = c_void_p()
        create = method(self._m_utils, 13, POINTER(_SBuffer), POINTER(SGuid), POINTER(c_void_p))
        hr = create(self._m_utils, ctypes.byref(buffer), IID_REFLECTION, ctypes.byref(refl))
        if hr or not refl:
            raise RuntimeError(f"DXC CreateReflection failed: 0x{hr & 0xFFFFFFFF:08x}")
        try:
            return read_reflection(refl.value)
        finally:
            release(refl.value)


def read_reflection(reflection_pointer: int) -> SReflection:
    """The record of an ``ID3D12ShaderReflection`` (or the D3D11 one, which has the same table) object."""
    desc = _SShaderDesc()
    method(reflection_pointer, 3, POINTER(_SShaderDesc), held=True)(reflection_pointer, ctypes.byref(desc))
    return SReflection(
        desc.version,
        _bindings(reflection_pointer, desc.resources),
        [_constant_buffer(reflection_pointer, i) for i in range(desc.cbuffers)],
        _signature(reflection_pointer, 7, desc.inputs),
        _signature(reflection_pointer, 8, desc.outputs),
    )


def _bindings(refl: int, count: int) -> list[SBinding]:
    out = []
    for i in range(count):
        bind_desc = _SBindDesc()
        method(refl, 6, ctypes.c_uint, POINTER(_SBindDesc), held=True)(refl, i, ctypes.byref(bind_desc))
        kind = INPUT_TYPES[bind_desc.type] if bind_desc.type < len(INPUT_TYPES) else str(bind_desc.type)
        out.append(SBinding(bind_desc.name.decode(), kind, bind_desc.bind_point, bind_desc.bind_count, bind_desc.space, bind_desc.flags, bind_desc.dimension,
                           bind_desc.return_type))  # fmt: skip
    return out


def _constant_buffer(refl: int, index: int) -> SConstantBuffer:
    buffer_reflection = method(refl, 4, ctypes.c_uint, restype=c_void_p, held=True)(refl, index)
    bd = _SBufferDesc()
    method(buffer_reflection, 0, POINTER(_SBufferDesc), held=True)(buffer_reflection, ctypes.byref(bd))
    buffer = SConstantBuffer(bd.name.decode(), bd.size)
    for j in range(bd.variables):
        var = method(buffer_reflection, 1, ctypes.c_uint, restype=c_void_p, held=True)(buffer_reflection, j)
        vd = _SVariableDesc()
        method(var, 0, POINTER(_SVariableDesc), held=True)(var, ctypes.byref(vd))
        variable = SVariable(vd.name.decode(), vd.offset, vd.size)
        _fill_type(variable, method(var, 1, restype=c_void_p, held=True)(var))
        buffer.variables.append(variable)
    return buffer


class _STypeDesc(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("cls", "type", "rows", "columns", "elements", "members", "offset")] + [
        ("name", ctypes.c_char_p)
    ]


def _fill_type(variable: SVariable, rtype: int, depth: int = 0) -> None:
    """A variable's type and, for a structure, its members (ID3D12ShaderReflectionType)."""
    td = _STypeDesc()
    if not rtype or method(rtype, 0, POINTER(_STypeDesc), held=True)(rtype, ctypes.byref(td)) or depth > 8:
        return
    variable.type = (td.name or b"").decode()
    variable.rows, variable.columns, variable.elements = td.rows, td.columns, td.elements
    variable.row_major = td.cls == 2
    for i in range(td.members):
        sub = method(rtype, 1, ctypes.c_uint, restype=c_void_p, held=True)(rtype, i)
        name = method(rtype, 3, ctypes.c_uint, restype=ctypes.c_char_p, held=True)(rtype, i)
        sd = _STypeDesc()
        method(sub, 0, POINTER(_STypeDesc), held=True)(sub, ctypes.byref(sd))
        member = SVariable((name or b"").decode(), sd.offset, 0)
        _fill_type(member, sub, depth + 1)
        variable.members.append(member)


def _signature(refl: int, index: int, count: int) -> list[tuple[str, int, int]]:
    out = []
    for i in range(count):
        signature_desc = _SSignatureDesc()
        method(refl, index, ctypes.c_uint, POINTER(_SSignatureDesc), held=True)(refl, i, ctypes.byref(signature_desc))
        out.append((signature_desc.semantic.decode(), signature_desc.index, signature_desc.register))
    return out


def container_parts(blob: bytes) -> dict[str, bytes]:
    """The parts of a DXBC container by their four-letter name (``DXIL``, ``PSV0``, ``HASH``, ...); ``ValueError``
    for anything that is not a whole container."""
    if blob[:4] != b"DXBC":
        raise ValueError("not a DXBC container")
    parts = {}
    try:
        count = struct.unpack_from("<I", blob, 28)[0]
        for i in range(count):
            offset_bytes = struct.unpack_from("<I", blob, 32 + 4 * i)[0]
            size_bytes = struct.unpack_from("<I", blob, offset_bytes + 4)[0]
            if offset_bytes + 8 + size_bytes > len(blob):
                raise ValueError("a part of the DXBC container runs past its end")
            parts[blob[offset_bytes : offset_bytes + 4].decode("ascii", "replace")] = blob[
                offset_bytes + 8 : offset_bytes + 8 + size_bytes
            ]
    except struct.error as failure:
        raise ValueError(f"a truncated DXBC container: {failure}") from failure
    return parts
