"""Microsoft's legacy shader compiler (FXC, ``d3dcompiler_47.dll``) through its C API, loaded at run time: compile HLSL
to DXBC for shader models 4 and 5, preprocess, disassemble, and read a compiled shader's bindings and signatures - the
compiler of the games that draw on Direct3D 10 and 11 (docs/rendering/shader-compile.md section 9).

``Fxc.find(game_folder)`` is the entry: ``ANNO_FXC_DIR`` (folders or files), else the compiler a game ships in its
``Bin/Win64`` folder (never copied, never shipped), else Windows' own ``System32`` copy. ``Fxc.compile`` returns a ``dxc.Compiled``,
``Fxc.reflect`` a ``dxc.Reflection`` (the same records as the DXC loader's). Includes are read through a callback
(``include``: name -> text or None). Windows only; elsewhere ``find`` raises ``FxcMissing``.
"""

from __future__ import annotations

import ctypes
import functools
import os
from collections.abc import Callable
from pathlib import Path

from .com import POINTER, Guid, blob_bytes, c_void_p, file_version, release
from .dxc import Compiled, Reflection, read_reflection
from .tools import candidates

FXC_ENV = "ANNO_FXC_DIR"
DLL = "d3dcompiler_47.dll"
GAME_DIRS = (Path("Bin") / "Win64", Path("bin"), Path("."))
WHERE_TO_GET = (
    "d3dcompiler_47.dll: the game's own Bin/Win64 when it ships one, else Windows' System32 copy (part of Windows "
    "since 8.1), or a folder named in ANNO_FXC_DIR"
)
# D3DCOMPILE_* flags (d3dcompiler.h)
DEBUG, SKIP_OPTIMIZATION, ROW_MAJOR, COLUMN_MAJOR = 0x1, 0x4, 0x8, 0x10
NO_PRESHADER, PREFER_FLOW_CONTROL, BACKWARDS_COMPATIBILITY = 0x100, 0x400, 0x1000
OPTIMIZATION_LEVEL3, ALL_RESOURCES_BOUND = 0x8000, 0x200000
# read off 1800's and 2205's shipped shaders: their flag word also has NO_PRESHADER, which the compiler writes for
# shader model 4 and 5 itself and refuses as an argument ("Invalid flags specified")
GAME_FLAGS = OPTIMIZATION_LEVEL3 | COLUMN_MAJOR
IID_REFLECTION11 = "8d536ca1-0cca-4956-a837-786963755584"  # ID3D11ShaderReflection
IID_REFLECTION11_LEGACY = "0a233719-3960-4578-9d7c-203b8b1d9cc1"  # the same interface in d3dcompiler_43


class FxcMissing(RuntimeError):
    pass


class _Macro(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("definition", ctypes.c_char_p)]


@functools.cache
def _callback_types() -> tuple[type, type]:
    """The include interface's open and close callbacks; made on first use, as ``WINFUNCTYPE`` exists on Windows only."""
    opened = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_char_p, c_void_p, POINTER(c_void_p),
                                POINTER(ctypes.c_uint))  # fmt: skip
    return opened, ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p)


class _Include:
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

    def _on_open(self, _this, _kind, name, parent, data, size) -> int:
        try:
            text = self.read(name.decode("utf-8", "replace"), self.parents.get(parent or 0, ""))
        except Exception as exc:  # an exception must not cross into the compiler, which would read garbage
            text = None
            self.errors.append(f"include {name.decode('utf-8', 'replace')}: {type(exc).__name__}: {exc}")
        if text is None:
            return 0x80004005  # E_FAIL: the compiler reports the missing file
        buf = ctypes.create_string_buffer(text, len(text))
        self.held[ctypes.addressof(buf)] = buf
        self.parents[ctypes.addressof(buf)] = name.decode("utf-8", "replace")
        data[0], size[0] = ctypes.addressof(buf), len(text)
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
    items = [(str(n).encode(), str(v).encode()) for n, v in defines] + [(None, None)]
    return (_Macro * len(items))(*[_Macro(n, v) for n, v in items])


class Fxc:
    """A loaded ``d3dcompiler_47``: ``compile``, ``preprocess``, ``disassemble`` and ``reflect``."""

    def __init__(self, path: Path) -> None:
        if os.name != "nt":
            raise FxcMissing("FXC is a Windows DLL: " + WHERE_TO_GET)
        self.path = Path(path)
        try:
            self._dll = ctypes.WinDLL(str(self.path))
        except OSError as e:
            raise FxcMissing(f"{path}: {e}") from e
        d = self._dll
        d.D3DCompile.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_char_p, c_void_p, c_void_p, ctypes.c_char_p,
                                 ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint, POINTER(c_void_p), POINTER(c_void_p)]  # fmt: skip
        d.D3DPreprocess.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_char_p, c_void_p, c_void_p, POINTER(c_void_p),
                                    POINTER(c_void_p)]  # fmt: skip
        d.D3DReflect.argtypes = [c_void_p, ctypes.c_size_t, c_void_p, POINTER(c_void_p)]
        d.D3DDisassemble.argtypes = [c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p, POINTER(c_void_p)]
        for f in (d.D3DCompile, d.D3DPreprocess, d.D3DReflect, d.D3DDisassemble):
            f.restype = ctypes.c_long

    @classmethod
    def find(cls, game_folder: str | Path | None = None, library: str = "") -> Fxc:
        """The compiler from ``ANNO_FXC_DIR``, else the game's own when ``game_folder`` ships one, else Windows'
        copy; ``library`` names an older build (2070's ``d3dcompiler_43`` is part of the DirectX runtime in
        ``System32``). A copy that does not load (another architecture, a blocked file) leaves the next one to try."""
        dll = f"{library}.dll" if library else DLL
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        places = candidates(
            [dll], env=FXC_ENV, roots=[game_folder] if game_folder else [], subdirs=GAME_DIRS, extra=[system]
        )
        failed = []
        for path in places:
            try:
                return cls(path)
            except FxcMissing as e:
                failed.append(str(e))
        raise FxcMissing(f"no {dll} found: " + WHERE_TO_GET + "".join(f"; {why}" for why in failed))

    def version(self) -> str:
        return file_version(self.path)

    def is_games(self) -> bool:
        """Whether this is a game's own copy (not Windows' or a folder named by the user)."""
        return "system32" not in str(self.path).lower() and not os.environ.get(FXC_ENV)

    def compile(self, source: str, entry: str, profile: str, defines=(), flags: int = GAME_FLAGS,
                include: Callable[[str, str], bytes | None] | None = None, name: str = "shader.hlsl") -> Compiled:  # fmt: skip
        """``source`` compiled for ``entry`` with ``profile`` (``vs_5_0``, ``ps_4_0``, ...), ``defines`` as (name,
        value) pairs, ``flags`` the ``D3DCOMPILE_*`` bits; ``include(name, parent)`` gives an included file's text."""
        data = source.encode("utf-8")
        code, errors = c_void_p(), c_void_p()
        handler = _Include(include) if include else None
        hr = self._dll.D3DCompile(data, len(data), name.encode(), ctypes.cast(_macros(defines), c_void_p),
                                  handler.pointer if handler else None, entry.encode(), profile.encode(), flags, 0,
                                  ctypes.byref(code), ctypes.byref(errors))  # fmt: skip
        messages = blob_bytes(errors.value).decode("utf-8", "replace").rstrip("\0")
        messages = handler.report(messages) if handler else messages
        obj = blob_bytes(code.value)
        return Compiled(hr == 0 and bool(obj), obj if hr == 0 else b"", messages)

    def preprocess(self, source: str, defines=(), include=None, name: str = "shader.hlsl") -> tuple[str, str]:
        """``source`` through FXC's preprocessor: the text and the messages (empty text on an error)."""
        data = source.encode("utf-8")
        code, errors = c_void_p(), c_void_p()
        handler = _Include(include) if include else None
        hr = self._dll.D3DPreprocess(data, len(data), name.encode(), ctypes.cast(_macros(defines), c_void_p),
                                     handler.pointer if handler else None, ctypes.byref(code), ctypes.byref(errors))  # fmt: skip
        messages = blob_bytes(errors.value).decode("utf-8", "replace").rstrip("\0")
        messages = handler.report(messages) if handler else messages
        text = blob_bytes(code.value).decode("utf-8", "replace").rstrip("\0")
        return (text if hr == 0 else ""), messages

    def disassemble(self, dxbc: bytes) -> str:
        out = c_void_p()
        if self._dll.D3DDisassemble(dxbc, len(dxbc), 0, None, ctypes.byref(out)):
            return ""
        return blob_bytes(out.value).decode("utf-8", "replace").rstrip("\0")

    def reflect(self, dxbc: bytes) -> Reflection:
        """The bindings, constant buffers and signatures of a DXBC shader (shader model 4 or 5)."""
        refl = c_void_p()
        for guid in (IID_REFLECTION11, IID_REFLECTION11_LEGACY):  # the older builds know the interface by its first id
            iid = Guid.of(guid)
            hr = self._dll.D3DReflect(dxbc, len(dxbc), ctypes.byref(iid), ctypes.byref(refl))
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
