"""The COM plumbing the Windows libraries we load at run time share (the shader compilers, the Direct3D 12 probe): call
the ``index``-th function of an object's table, release an object, read a blob, a GUID from its text, a library's version.

``method`` is the entry; the rest follows what the compilers' C interfaces do (docs/rendering/shader-compile.md).
Windows only: calling any of it elsewhere raises ``AttributeError`` from ``ctypes``.
"""

from __future__ import annotations

import ctypes
import uuid
from pathlib import Path

HRESULT = ctypes.c_long
c_void_p = ctypes.c_void_p
POINTER = ctypes.POINTER
IUNKNOWN_RELEASE, BLOB_POINTER, BLOB_SIZE = 2, 3, 4  # places in a table: the COM release, ID3DBlob's two getters


class Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> Guid:
        """The GUID written as ``8d536ca1-0cca-4956-a837-786963755584``."""
        g = cls()
        ctypes.memmove(g.bytes, uuid.UUID(text).bytes_le, 16)
        return g


def method(obj: int, index: int, *argtypes, restype=HRESULT):
    """The ``index``-th function of a COM object's table, callable with the object first."""
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def _release_call(obj):
    return method(obj, IUNKNOWN_RELEASE, restype=ctypes.c_ulong)(obj)


def release(obj) -> None:
    """Release a COM object (nothing for a null object)."""
    if obj:
        _release_call(obj)


def blob_bytes(blob, release: bool = True) -> bytes:
    """An ``ID3DBlob``'s (or ``IDxcBlob``'s) bytes; the blob is released unless ``release`` is False. Empty for a null
    blob."""
    if not blob:
        return b""
    ptr = method(blob, BLOB_POINTER, restype=c_void_p)(blob)
    size = method(blob, BLOB_SIZE, restype=ctypes.c_size_t)(blob)
    out = ctypes.string_at(ptr, size) if ptr and size else b""
    if release:
        _release_call(blob)
    return out


def file_version(path: Path) -> str:
    """A library's or executable's file version from its version resource (``1.8.2405.17``), "" when it has none."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    ver.GetFileVersionInfoW(str(path), 0, size, data)
    ptr, length = c_void_p(), ctypes.c_uint()
    if not ver.VerQueryValueW(data, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return ""
    ms, ls = ctypes.cast(ptr.value + 8, POINTER(ctypes.c_uint32 * 2)).contents
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
