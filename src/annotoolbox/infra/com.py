"""The COM plumbing the Windows libraries we load at run time share (the shader compilers, the Direct3D 12 probe): call
the ``index``-th function of an object's table, release an object, read a blob, a GUID from its text, a library's version.

``method`` is the entry; the rest follows what the compilers' C interfaces do (docs/rendering/shader-compile.md).
Windows only: calling any of it elsewhere raises ``AttributeError`` from ``ctypes``.
"""

from __future__ import annotations

import ctypes
import functools
import uuid
from pathlib import Path

HRESULT = ctypes.c_long
c_void_p = ctypes.c_void_p
POINTER = ctypes.POINTER
IUNKNOWN_RELEASE, BLOB_POINTER, BLOB_SIZE = 2, 3, 4  # places in a table: the COM release, ID3DBlob's two getters


class SGuid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> SGuid:
        """The GUID written as ``8d536ca1-0cca-4956-a837-786963755584``."""
        guid_value = cls()
        ctypes.memmove(guid_value.bytes, uuid.UUID(text).bytes_le, 16)
        return guid_value


def method(com_object: int, index: int, *argtypes, restype=HRESULT, held: bool = False):
    """The ``index``-th function of a COM object's table, callable with the object first. ``held``: the call keeps
    Python's lock, for the many short getters of a reflection: a call that lets it go waits to take it back while other
    threads run Python, which made each of those microsecond calls take milliseconds on a busy view."""
    table = ctypes.cast(com_object, POINTER(POINTER(c_void_p))).contents
    kind = _held_type(restype, (c_void_p, *argtypes)) if held else ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return kind(table[index])


@functools.cache
def _held_type(restype, argtypes: tuple):
    """A function type like ``WINFUNCTYPE``'s that does not let Python's lock go during the call."""

    class CHeld(ctypes._CFuncPtr):
        _argtypes_, _restype_ = argtypes, restype
        _flags_ = ctypes._FUNCFLAG_STDCALL | ctypes._FUNCFLAG_PYTHONAPI

    return CHeld


def _release_call(com_object):
    return method(com_object, IUNKNOWN_RELEASE, restype=ctypes.c_ulong)(com_object)


def release(com_object) -> None:
    """Release a COM object (nothing for a null object)."""
    if com_object:
        _release_call(com_object)


def blob_bytes(blob, release: bool = True) -> bytes:
    """An ``ID3DBlob``'s (or ``IDxcBlob``'s) bytes; the blob is released unless ``release`` is False. Empty for a null
    blob."""
    if not blob:
        return b""
    pointer = method(blob, BLOB_POINTER, restype=c_void_p)(blob)
    size_bytes = method(blob, BLOB_SIZE, restype=ctypes.c_size_t)(blob)
    out = ctypes.string_at(pointer, size_bytes) if pointer and size_bytes else b""
    if release:
        _release_call(blob)
    return out


def file_version(path: Path) -> str:
    """A library's or executable's file version from its version resource (``1.8.2405.17``), "" when it has none."""
    version_dll = ctypes.windll.version
    size_bytes = version_dll.GetFileVersionInfoSizeW(str(path), None)
    if not size_bytes:
        return ""
    data = ctypes.create_string_buffer(size_bytes)
    version_dll.GetFileVersionInfoW(str(path), 0, size_bytes, data)
    pointer, length_bytes = c_void_p(), ctypes.c_uint()
    if not version_dll.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length_bytes)):
        return ""
    ms, ls = ctypes.cast(pointer.value + 8, POINTER(ctypes.c_uint32 * 2)).contents
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
