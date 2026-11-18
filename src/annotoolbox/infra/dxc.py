"""Microsoft's DirectX shader compiler (DXC) through its C API, loaded at run time: compile HLSL to DXIL, preprocess,
and read a compiled shader's bindings and signatures (docs/rendering/shader-compile.md, "The DXC API").

``Dxc.find(anno117=...)`` is the entry: ``ANNO_DXC_DIR``, else the compiler the game ships in its ``Bin/Win64`` folder
(never copied, never shipped), else ``tools/`` (Microsoft's open-source release in ``tools/dxc/``). ``Dxc.compile`` returns a
``Compiled`` with the bytecode and the error text; ``Dxc.reflect`` a shader's ``Reflection``. Windows only (the
compiler is a Windows DLL); elsewhere ``find`` raises ``DxcMissing``.
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .com import HRESULT, POINTER, Guid, blob_bytes, c_void_p, file_version, method, release
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


class CompilerMissing(RuntimeError):
    """No shader compiler that loads: its text is in the interface language (a greyed-out look shows it as the
    reason), ``english`` the same sentence for a log or the command line."""

    def __init__(self, text: str, english: str = "") -> None:
        super().__init__(text)
        self.english = english or text

    @classmethod
    def of(cls, ident: str, where: str, failed: list[Exception] | tuple = (), **fields: object) -> CompilerMissing:
        """The sentence of the string id ``ident`` with the sentence ``where`` (an id) that says where the compiler is
        from, and why each copy ``failed`` to load."""

        def said(text, in_english: bool) -> str:
            whys = "".join(f"; {getattr(e, 'english', e) if in_english else e}" for e in failed)
            return text(ident, where=text(where), failed=whys, **fields)

        return cls(said(tr, False), said(english, True))


class DxcMissing(CompilerMissing):
    pass


CLSID_COMPILER = Guid.of("73e22d93-e6ce-47f3-b5bf-f0664f39c1b0")
CLSID_UTILS = Guid.of("6245d6af-66e0-48fd-80b4-4d271796748c")
IID_COMPILER3 = Guid.of("228b4687-5a6a-4730-900c-9702b2203f54")
IID_UTILS = Guid.of("4605c4cb-2019-492a-ada4-65f20bb7d67f")
IID_RESULT = Guid.of("58346cda-dde7-4497-9461-6f87af5e0659")
IID_BLOB = Guid.of("8ba5fb08-5195-40e2-ac58-0d989c3a0102")
IID_BLOB_UTF8 = Guid.of("3da636c9-ba71-4024-a301-30cbf125305b")
IID_REFLECTION = Guid.of("5a58797d-a72c-478d-8ba2-efc6b0efe88e")  # ID3D12ShaderReflection


class _Buffer(ctypes.Structure):
    _fields_ = [("ptr", c_void_p), ("size", ctypes.c_size_t), ("encoding", ctypes.c_uint)]


class _ShaderDesc(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("creator", ctypes.c_char_p), ("flags", ctypes.c_uint)] + [
        (n, ctypes.c_uint)
        for n in ("cbuffers", "resources", "inputs", "outputs")
        + tuple(f"_u{i}" for i in range(33))  # the instruction counts and the tessellation fields
    ]


class _BindDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p)] + [
        (n, ctypes.c_uint)
        for n in ("type", "bind_point", "bind_count", "flags", "return_type", "dimension", "samples", "space", "id")
    ]


class _SignatureDesc(ctypes.Structure):
    _fields_ = [("semantic", ctypes.c_char_p), ("index", ctypes.c_uint), ("register", ctypes.c_uint)] + [
        ("system_value", ctypes.c_uint), ("component_type", ctypes.c_uint), ("mask", ctypes.c_ubyte),
        ("rw_mask", ctypes.c_ubyte), ("stream", ctypes.c_uint), ("min_precision", ctypes.c_uint),
    ]  # fmt: skip


class _BufferDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("type", ctypes.c_uint), ("variables", ctypes.c_uint)] + [
        ("size", ctypes.c_uint), ("flags", ctypes.c_uint),
    ]  # fmt: skip


class _VariableDesc(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("offset", ctypes.c_uint), ("size", ctypes.c_uint)] + [
        ("flags", ctypes.c_uint), ("default", c_void_p), ("start_texture", ctypes.c_uint),
        ("texture_size", ctypes.c_uint), ("start_sampler", ctypes.c_uint), ("sampler_size", ctypes.c_uint),
    ]  # fmt: skip


@dataclass
class Compiled:
    """One compile: the bytecode (empty when it failed) and DXC's messages."""

    ok: bool
    object: bytes
    messages: str


@dataclass
class Binding:
    name: str
    kind: str  # INPUT_TYPES
    register: int
    count: int  # 0: an unbounded array
    space: int
    flags: int = 0  # D3D_SHADER_INPUT_FLAGS: 2 a comparison sampler


@dataclass
class Variable:
    """A constant buffer's variable, or a structure's member (``offset`` from its parent's start)."""

    name: str
    offset: int
    size: int
    type: str = ""  # the HLSL type name (float4x4, CameraData, ...)
    rows: int = 0
    columns: int = 0
    elements: int = 0  # an array's length, 0 for one value
    row_major: bool = False  # a matrix stored by rows (D3D_SVC_MATRIX_ROWS)
    members: list[Variable] = field(default_factory=list)


@dataclass
class ConstantBuffer:
    name: str
    size: int
    variables: list[Variable] = field(default_factory=list)


@dataclass
class Reflection:
    """What a compiled shader binds and passes: its resources, constant buffers and signatures."""

    version: int  # the shader model: (type << 16) | (major << 4) | minor
    bindings: list[Binding]
    cbuffers: list[ConstantBuffer]
    inputs: list[tuple[str, int, int]]  # (semantic, index, register)
    outputs: list[tuple[str, int, int]]

    @property
    def model(self) -> str:
        return f"{(self.version >> 4) & 0xF}_{self.version & 0xF}"


def reflection_json(refl: Reflection) -> bytes:
    """A reflection as JSON, to keep beside its bytecode (``reflection_of_json`` reads it back equal)."""
    return json.dumps(asdict(refl), separators=(",", ":")).encode("utf-8")


def _variable(d: dict) -> Variable:
    return Variable(**{**d, "members": [_variable(m) for m in d["members"]]})


def reflection_of_json(data: bytes) -> Reflection:
    """The reflection ``reflection_json`` kept, equal to the one the compiler gives (its signatures as tuples)."""
    d = json.loads(data)
    return Reflection(
        d["version"],
        [Binding(**b) for b in d["bindings"]],
        [ConstantBuffer(c["name"], c["size"], [_variable(v) for v in c["variables"]]) for c in d["cbuffers"]],
        [tuple(s) for s in d["inputs"]],
        [tuple(s) for s in d["outputs"]],
    )


_threads = threading.local()  # per thread: folder -> its own ``Dxc`` (``Dxc.for_thread``)


class Dxc:
    """A loaded compiler: ``compile`` and ``preprocess`` HLSL, ``reflect`` DXIL."""

    def __init__(self, folder: Path) -> None:
        if os.name != "nt":
            raise DxcMissing.of(N_("common.dxc_windows_dll_here"), WHERE_TO_GET)
        self.folder = folder
        try:  # dxcompiler loads dxil.dll by name: loaded first, from the same folder, it is found
            self._dxil = ctypes.WinDLL(str(folder / "dxil.dll"))
            self._dll = ctypes.WinDLL(str(folder / "dxcompiler.dll"))
        except OSError as e:
            raise DxcMissing(f"{folder}: {e}") from e
        create = self._dll.DxcCreateInstance
        create.argtypes = [POINTER(Guid), POINTER(Guid), POINTER(c_void_p)]
        create.restype = HRESULT
        self._compiler, self._utils = c_void_p(), c_void_p()
        if create(CLSID_COMPILER, IID_COMPILER3, ctypes.byref(self._compiler)) or create(
            CLSID_UTILS, IID_UTILS, ctypes.byref(self._utils)
        ):
            raise DxcMissing.of(N_("common.folder_dxc_create_instance_failed"), WHERE_TO_GET, folder=folder)

    @classmethod
    def find(cls, anno117: str | Path | None = None) -> Dxc:
        """The compiler from ``ANNO_DXC_DIR`` (folders), else the game's when ``anno117`` is given and has one, else
        the one in ``tools/``; it needs ``dxil.dll`` beside it, and a copy that does not load leaves the next to try."""
        found = candidates(
            ["dxcompiler.dll"], env=DXC_ENV, roots=[anno117] if anno117 else [], subdirs=[GAME_DXC_DIR],
            search_repo_tools=True, accept=lambda p: p.is_file() and (p.parent / "dxil.dll").is_file(),
        )  # fmt: skip
        failed = []
        for path in found:
            try:
                return cls(path.parent)
            except DxcMissing as e:
                failed.append(e)
        raise DxcMissing.of(N_("common.no_dxc_found"), WHERE_TO_GET, failed)

    def version(self) -> str:
        """The compiler's file version, as Windows reports it (read once)."""
        if getattr(self, "_version", None) is None:
            self._version = file_version(self.folder / "dxcompiler.dll")
        return self._version

    def for_thread(self) -> Dxc:
        """This thread's instance of the same compiler, made once per thread: DXC's objects serve one thread at a
        time, so workers that each hold their own compile side by side (shader-compile.md 6)."""
        own = getattr(_threads, "compilers", None)
        if own is None:
            own = _threads.compilers = {}
        found = own.get(self.folder)
        if found is None:
            found = own[self.folder] = Dxc(self.folder)
        return found

    def _run(self, source: str, args: list[str]) -> tuple[int, int]:
        """Compile with DXC's arguments; returns (status, result object), the caller releases the result."""
        data = source.encode("utf-8")
        buf = _Buffer(ctypes.cast(ctypes.c_char_p(data), c_void_p), len(data), 65001)
        argv = (ctypes.c_wchar_p * len(args))(*args)
        result = c_void_p()
        call = method(self._compiler, 3, POINTER(_Buffer), c_void_p, ctypes.c_uint32, c_void_p, POINTER(Guid), c_void_p)
        hr = call(self._compiler, ctypes.byref(buf), argv, len(args), None, IID_RESULT, ctypes.byref(result))
        if hr or not result:
            raise RuntimeError(f"DXC Compile failed: 0x{hr & 0xFFFFFFFF:08x}")
        status = HRESULT()
        method(result, 3, POINTER(HRESULT))(result, ctypes.byref(status))
        return status.value, result.value

    def _output(self, result: int, kind: int) -> bytes:
        blob = c_void_p()
        get = method(result, 7, ctypes.c_int, POINTER(Guid), POINTER(c_void_p), c_void_p)
        if get(result, kind, IID_BLOB, ctypes.byref(blob), None) or not blob:
            return b""
        try:
            return blob_bytes(blob.value, release=False)
        finally:
            release(blob.value)

    def compile(self, source: str, args: list[str], name: str = "shader.hlsl") -> Compiled:
        """Compile ``source`` with DXC's command-line ``args`` (``-E``, ``-T``, options; no include handler: the
        source must be whole, as the game passes it)."""
        status, result = self._run(source, [name, *args])
        try:
            messages = self._output(result, OUT_ERRORS).decode("utf-8", "replace").rstrip("\0")
            obj = self._output(result, OUT_OBJECT) if status == 0 else b""
        finally:
            release(result)
        return Compiled(status == 0 and bool(obj), obj, messages)

    def preprocess(self, source: str, args: list[str], name: str = "shader.hlsl") -> tuple[str, str]:
        """``source`` run through DXC's preprocessor (``-P``): the text and the messages (empty text on an error)."""
        status, result = self._run(source, [name, "-P", *args])
        try:
            messages = self._output(result, OUT_ERRORS).decode("utf-8", "replace").rstrip("\0")
            text = self._output(result, OUT_HLSL).decode("utf-8", "replace").rstrip("\0") if status == 0 else ""
        finally:
            release(result)
        return text, messages

    def reflect(self, dxil: bytes) -> Reflection:
        """The bindings, constant buffers and signatures of a compiled shader (a DXBC container with DXIL)."""
        holder = ctypes.create_string_buffer(dxil, len(dxil))
        buf = _Buffer(ctypes.cast(holder, c_void_p), len(dxil), 0)
        refl = c_void_p()
        create = method(self._utils, 13, POINTER(_Buffer), POINTER(Guid), POINTER(c_void_p))
        hr = create(self._utils, ctypes.byref(buf), IID_REFLECTION, ctypes.byref(refl))
        if hr or not refl:
            raise RuntimeError(f"DXC CreateReflection failed: 0x{hr & 0xFFFFFFFF:08x}")
        try:
            return read_reflection(refl.value)
        finally:
            release(refl.value)


def read_reflection(refl: int) -> Reflection:
    desc = _ShaderDesc()
    method(refl, 3, POINTER(_ShaderDesc), held=True)(refl, ctypes.byref(desc))
    bindings = []
    for i in range(desc.resources):
        b = _BindDesc()
        method(refl, 6, ctypes.c_uint, POINTER(_BindDesc), held=True)(refl, i, ctypes.byref(b))
        kind = INPUT_TYPES[b.type] if b.type < len(INPUT_TYPES) else str(b.type)
        bindings.append(Binding(b.name.decode(), kind, b.bind_point, b.bind_count, b.space, b.flags))
    cbuffers = []
    for i in range(desc.cbuffers):
        cb = method(refl, 4, ctypes.c_uint, restype=c_void_p, held=True)(refl, i)
        bd = _BufferDesc()
        method(cb, 0, POINTER(_BufferDesc), held=True)(cb, ctypes.byref(bd))
        buffer = ConstantBuffer(bd.name.decode(), bd.size)
        for j in range(bd.variables):
            var = method(cb, 1, ctypes.c_uint, restype=c_void_p, held=True)(cb, j)
            vd = _VariableDesc()
            method(var, 0, POINTER(_VariableDesc), held=True)(var, ctypes.byref(vd))
            variable = Variable(vd.name.decode(), vd.offset, vd.size)
            _fill_type(variable, method(var, 1, restype=c_void_p, held=True)(var))
            buffer.variables.append(variable)
        cbuffers.append(buffer)
    return Reflection(
        desc.version, bindings, cbuffers, _signature(refl, 7, desc.inputs), _signature(refl, 8, desc.outputs)
    )


class _TypeDesc(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("cls", "type", "rows", "columns", "elements", "members", "offset")] + [
        ("name", ctypes.c_char_p)
    ]


def _fill_type(variable: Variable, rtype: int, depth: int = 0) -> None:
    """A variable's type and, for a structure, its members (ID3D12ShaderReflectionType)."""
    td = _TypeDesc()
    if not rtype or method(rtype, 0, POINTER(_TypeDesc), held=True)(rtype, ctypes.byref(td)) or depth > 8:
        return
    variable.type = (td.name or b"").decode()
    variable.rows, variable.columns, variable.elements = td.rows, td.columns, td.elements
    variable.row_major = td.cls == 2
    for i in range(td.members):
        sub = method(rtype, 1, ctypes.c_uint, restype=c_void_p, held=True)(rtype, i)
        name = method(rtype, 3, ctypes.c_uint, restype=ctypes.c_char_p, held=True)(rtype, i)
        sd = _TypeDesc()
        method(sub, 0, POINTER(_TypeDesc), held=True)(sub, ctypes.byref(sd))
        member = Variable((name or b"").decode(), sd.offset, 0)
        _fill_type(member, sub, depth + 1)
        variable.members.append(member)


def _signature(refl: int, index: int, count: int) -> list[tuple[str, int, int]]:
    out = []
    for i in range(count):
        p = _SignatureDesc()
        method(refl, index, ctypes.c_uint, POINTER(_SignatureDesc), held=True)(refl, i, ctypes.byref(p))
        out.append((p.semantic.decode(), p.index, p.register))
    return out


def container_parts(blob: bytes) -> dict[str, bytes]:
    """The parts of a DXBC container by their four-letter name (``DXIL``, ``PSV0``, ``HASH``, ...)."""
    import struct

    if blob[:4] != b"DXBC":
        raise ValueError("not a DXBC container")
    count = struct.unpack_from("<I", blob, 28)[0]
    parts = {}
    for i in range(count):
        offset = struct.unpack_from("<I", blob, 32 + 4 * i)[0]
        size = struct.unpack_from("<I", blob, offset + 4)[0]
        parts[blob[offset : offset + 4].decode("ascii", "replace")] = blob[offset + 8 : offset + 8 + size]
    return parts
