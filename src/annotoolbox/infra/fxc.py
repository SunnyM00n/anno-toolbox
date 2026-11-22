"""Microsoft's legacy shader compiler (FXC, ``d3dcompiler_47.dll``) through its C API, loaded at run time: compile HLSL
to DXBC for shader models 4 and 5, preprocess, disassemble, and read a compiled shader's bindings and signatures - the
compiler of the games that draw on Direct3D 10 and 11 (docs/rendering/shader-compile.md section 9).

``CFxc.find(game_folder)`` is the entry: ``ANNO_FXC_DIR`` (folders or files), else the compiler a game ships in its
``Bin/Win64`` folder (never copied, never shipped), else Windows' own ``System32`` copy. ``CFxc.compile`` returns a ``dxc.SCompiled``,
``CFxc.reflect`` a ``dxc.SReflection`` (the same records as the DXC loader's). Includes are read through a callback
(``include``: name -> text or None). Windows only; elsewhere ``find`` raises ``CFxcMissing``.
"""

from __future__ import annotations

import ctypes
import functools
import os
from collections.abc import Callable
from pathlib import Path

from .com import POINTER, SGuid, blob_bytes, c_void_p, file_version, release
from .dxc import CCompilerMissing, SCompiled, SReflection, read_reflection
from .i18n import N_
from .tools import candidates

FXC_ENV = "ANNO_FXC_DIR"
DLL = "d3dcompiler_47.dll"
GAME_DIRS = (Path("Bin") / "Win64", Path("bin"), Path("."))
WHERE_TO_GET = N_("common.fxc_where_get")  # the id of the sentence that says where it is from
# D3DCOMPILE_* flags (d3dcompiler.h)
DEBUG, SKIP_OPTIMIZATION, ROW_MAJOR, COLUMN_MAJOR = 0x1, 0x4, 0x8, 0x10
NO_PRESHADER, PREFER_FLOW_CONTROL, BACKWARDS_COMPATIBILITY = 0x100, 0x400, 0x1000
OPTIMIZATION_LEVEL3, ALL_RESOURCES_BOUND = 0x8000, 0x200000
# read off 1800's and 2205's shipped shaders: their flag word also has NO_PRESHADER, which the compiler writes for
# shader model 4 and 5 itself and refuses as an argument ("Invalid flags specified")
GAME_FLAGS = OPTIMIZATION_LEVEL3 | COLUMN_MAJOR
IID_REFLECTION11 = "8d536ca1-0cca-4956-a837-786963755584"  # ID3D11ShaderReflection
IID_REFLECTION11_LEGACY = "0a233719-3960-4578-9d7c-203b8b1d9cc1"  # the same interface in d3dcompiler_43


class CFxcMissing(CCompilerMissing):
    pass


class _SMacro(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("definition", ctypes.c_char_p)]


@functools.cache
def _callback_types() -> tuple[type, type]:
    """The include interface's open and close callbacks; made on first use, as ``WINFUNCTYPE`` exists on Windows only."""
    opened = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_char_p, c_void_p, POINTER(c_void_p),
                                POINTER(ctypes.c_uint))  # fmt: skip
    return opened, ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p)


class _CInclude:
    """An ``ID3DInclude`` (a C++ interface without ``IUnknown``) that asks ``read`` for each included file."""

    def __init__(self, read: Callable[[str, str], bytes | None]) -> None:
        self.read, self.held = read, {}
        self.parents: dict[int, str] = {}
        self.errors: list[str] = []  # what a failed read raised: the compiler only learns "failed"
        opened, closed = _callback_types()
        self._open, self._close = opened(self._on_open), closed(self._on_close)
        self._table = (c_void_p * 2)(ctypes.cast(self._open, c_void_p), ctypes.cast(self._close, c_void_p))
        self._object = (c_void_p * 1)(ctypes.addressof(self._table))
        self.pointer = ctypes.addressof(self._object)

    def _on_open(self, _this, _kind, name, parent, data, size_bytes) -> int:
        try:
            text = self.read(name.decode("utf-8", "replace"), self.parents.get(parent or 0, ""))
        except Exception as error:  # an exception must not cross into the compiler, which would read garbage
            text = None
            self.errors.append(f"include {name.decode('utf-8', 'replace')}: {type(error).__name__}: {error}")
        if text is None:
            return 0x80004005  # E_FAIL: the compiler reports the missing file
        buffer = ctypes.create_string_buffer(text, len(text))
        self.held[ctypes.addressof(buffer)] = buffer
        self.parents[ctypes.addressof(buffer)] = name.decode("utf-8", "replace")
        data[0], size_bytes[0] = ctypes.addressof(buffer), len(text)
        return 0

    def _on_close(self, _this, data) -> int:
        self.held.pop(data or 0, None)
        return 0

    def report(self, messages: str) -> str:
        """The compiler's messages with the reads that raised."""
        return "\n".join([messages, *self.errors]).strip()


def _macros(defines) -> ctypes.Array | None:
    if not defines:
        return None
    items = [(str(name).encode(), str(value).encode()) for name, value in defines] + [(None, None)]
    return (_SMacro * len(items))(*[_SMacro(name, value) for name, value in items])


def _system32() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def _messages(errors: c_void_p, handler: _CInclude | None) -> str:
    """The compiler's error blob as text, with the include reads that raised."""
    messages = blob_bytes(errors.value).decode("utf-8", "replace").rstrip("\0")
    return handler.report(messages) if handler else messages


class CFxc:
    """A loaded ``d3dcompiler_47``: ``compile``, ``preprocess``, ``disassemble`` and ``reflect``."""

    def __init__(self, path: Path) -> None:
        if os.name != "nt":
            raise CFxcMissing.of(N_("common.fxc_windows_dll"), WHERE_TO_GET)
        self._m_version: str | None = None
        self.m_path = Path(path)
        try:
            self._m_dll = ctypes.WinDLL(str(self.m_path))
        except OSError as error:
            raise CFxcMissing(f"{path}: {error}") from error
        dll = self._m_dll
        dll.D3DCompile.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_char_p, c_void_p, c_void_p, ctypes.c_char_p,
                                 ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, POINTER(c_void_p), POINTER(c_void_p)]  # fmt: skip
        dll.D3DPreprocess.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_char_p, c_void_p, c_void_p, POINTER(c_void_p),
                                    POINTER(c_void_p)]  # fmt: skip
        dll.D3DReflect.argtypes = [c_void_p, ctypes.c_size_t, c_void_p, POINTER(c_void_p)]
        dll.D3DDisassemble.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p, POINTER(c_void_p)]
        for function in (dll.D3DCompile, dll.D3DPreprocess, dll.D3DReflect, dll.D3DDisassemble):
            function.restype = ctypes.c_long

    @classmethod
    def find(cls, game_folder: str | Path | None = None, library: str = "") -> CFxc:
        """The compiler from ``ANNO_FXC_DIR``, else the game's own when ``game_folder`` ships one, else Windows'
        copy; ``library`` names an older build (2070's ``d3dcompiler_43`` is part of the DirectX runtime in
        ``System32``). A copy that does not load (another architecture, a blocked file) leaves the next one to try."""
        dll = f"{library}.dll" if library else DLL
        places = candidates(
            [dll], env=FXC_ENV, roots=[game_folder] if game_folder else [], subdirs=GAME_DIRS, extra=[_system32()]
        )
        failed = []
        for path in places:
            try:
                return cls(path)
            except CFxcMissing as error:
                failed.append(error)
        raise CFxcMissing.of(N_("common.no_dll_found"), WHERE_TO_GET, failed, dll=dll)

    @classmethod
    def windows(cls) -> CFxc:
        """Windows' own copy in ``System32``: the one Qt's Direct3D backends load to compile HLSL."""
        return cls(_system32() / DLL)

    @property
    def parallel(self) -> bool:
        """Whether this build compiles on several threads at once (47 does; an older one, 2070's 43, is kept to one
        compile at a time)."""
        return self.m_path.name.lower() == DLL

    def version(self) -> str:
        """The library's file version, as Windows reports it."""
        if self._m_version is None:  # read once: asked for every shader's cache key
            self._m_version = file_version(self.m_path)
        return self._m_version

    def is_games(self) -> bool:
        """Whether this is a game's own copy (not Windows' or a folder named by the user)."""
        return "system32" not in str(self.m_path).lower() and not os.environ.get(FXC_ENV)

    def compile(self, source: str, entry: str, profile: str, defines=(), flags: int = GAME_FLAGS,
                include: Callable[[str, str], bytes | None] | None = None, name: str = "shader.hlsl") -> SCompiled:  # fmt: skip
        """``source`` compiled for ``entry`` with ``profile`` (``vs_5_0``, ``ps_4_0``, ...), ``defines`` as (name,
        value) pairs, ``flags`` the ``D3DCOMPILE_*`` bits; ``include(name, parent)`` gives an included file's text."""
        data = source.encode("utf-8")
        code, errors = c_void_p(), c_void_p()
        handler = _CInclude(include) if include else None
        hr = self._m_dll.D3DCompile(data, len(data), name.encode(), ctypes.cast(_macros(defines), c_void_p),
                                  handler.pointer if handler else None, entry.encode(), profile.encode(), flags, 0,
                                  ctypes.byref(code), ctypes.byref(errors))  # fmt: skip
        messages = _messages(errors, handler)
        object_code = blob_bytes(code.value)
        return SCompiled(hr == 0 and bool(object_code), object_code if hr == 0 else b"", messages)

    def preprocess(self, source: str, defines=(), include=None, name: str = "shader.hlsl") -> tuple[str, str]:
        """``source`` through FXC's preprocessor: the text and the messages (empty text on an error)."""
        data = source.encode("utf-8")
        code, errors = c_void_p(), c_void_p()
        handler = _CInclude(include) if include else None
        hr = self._m_dll.D3DPreprocess(data, len(data), name.encode(), ctypes.cast(_macros(defines), c_void_p),
                                     handler.pointer if handler else None, ctypes.byref(code), ctypes.byref(errors))  # fmt: skip
        messages = _messages(errors, handler)
        text = blob_bytes(code.value).decode("utf-8", "replace").rstrip("\0")
        return (text if hr == 0 else ""), messages

    def disassemble(self, dxbc: bytes) -> str:
        """The shader's assembly text ("" when the compiler refuses it)."""
        out = c_void_p()
        if self._m_dll.D3DDisassemble(dxbc, len(dxbc), 0, None, ctypes.byref(out)):
            return ""
        return blob_bytes(out.value).decode("utf-8", "replace").rstrip("\0")

    def reflect(self, dxbc: bytes) -> SReflection:
        """The bindings, constant buffers and signatures of a DXBC shader (shader model 4 or 5)."""
        refl = c_void_p()
        for guid in (IID_REFLECTION11, IID_REFLECTION11_LEGACY):  # the older builds know the interface by its first id
            iid = SGuid.of(guid)
            hr = self._m_dll.D3DReflect(dxbc, len(dxbc), ctypes.byref(iid), ctypes.byref(refl))
            if not hr and refl:
                break
        if hr or not refl:
            raise RuntimeError(f"D3DReflect failed: 0x{hr & 0xFFFFFFFF:08x}")
        try:
            return read_reflection(refl.value)
        finally:
            release(refl.value)


def profile_model(profile: str) -> int:
    """``vs_5_0`` -> 50: the shader model a QRhi DXBC shader is keyed by."""
    parts = profile.split("_")
    return int(parts[1]) * 10 + int(parts[2]) if len(parts) == 3 and parts[1].isdigit() else 50
