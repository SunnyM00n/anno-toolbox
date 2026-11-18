"""What the machine's Direct3D 12 adapter supports, asked through the Direct3D 12 runtime with ``ctypes``: its highest feature
level and its ray-tracing tier (what the games' ray-traced light would need; QRhi itself has no ray-tracing API).

``probe()`` is the entry: a ``Support`` record, or one saying why nothing was read (another OS, no Direct3D 12).
``video_memory()`` is what this process holds of the adapter's memory, as the graphics kernel counts it (the
island view's memory, docs/rendering/island-117-view.md "Speed", part 9). docs/viewer.md, "The renderer".
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from .com import Guid, method, release
from .i18n import tr

LEVELS = (0xC200, 0xC100, 0xC000, 0xB100, 0xB000)  # D3D_FEATURE_LEVEL 12_2 .. 11_0
FEATURE_LEVELS, OPTIONS5 = 2, 27  # D3D12_FEATURE
IID_DEVICE = "189819f1-1db6-4b57-be54-1821339b85f7"  # ID3D12Device
CHECK_FEATURE_SUPPORT = 13  # its place in ID3D12Device's table
IID_FACTORY1 = "770aae78-f26f-4dba-a829-253c83d1b387"  # IDXGIFactory1
IID_ADAPTER3 = "645967a4-1392-4310-a798-8053ce3e93fd"  # IDXGIAdapter3
ENUM_ADAPTERS1, QUERY_INTERFACE, QUERY_VIDEO_MEMORY = 12, 0, 14  # their places in the factory's and adapter's tables
LOCAL, SHARED = 0, 1  # DXGI_MEMORY_SEGMENT_GROUP: the card's own memory, the system memory it maps


@dataclass(frozen=True)
class VideoMemory:
    """This process's use of the default adapter's memory and the budgets the system grants it, bytes."""

    local: int = 0
    local_budget: int = 0
    shared: int = 0  # system memory the adapter maps for the process (upload heaps, what did not fit)
    shared_budget: int = 0


class _MemoryInfo(ctypes.Structure):  # DXGI_QUERY_VIDEO_MEMORY_INFO
    _fields_ = [(n, ctypes.c_uint64) for n in ("budget", "usage", "reservable", "reserved")]


def video_memory() -> VideoMemory | None:
    """This process's use of the default adapter's memory (``VideoMemory``), None where it cannot be asked."""
    if os.name != "nt":
        return None
    try:
        dxgi = ctypes.WinDLL("dxgi")
        create = dxgi.CreateDXGIFactory1
        create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        factory, adapter, adapter3 = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        if create(ctypes.byref(Guid.of(IID_FACTORY1)), ctypes.byref(factory)) or not factory:
            return None
    except (AttributeError, OSError):
        return None
    try:
        enum = method(factory.value, ENUM_ADAPTERS1, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        if enum(factory.value, 0, ctypes.byref(adapter)) or not adapter:
            return None
        ask = method(adapter.value, QUERY_INTERFACE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        if ask(adapter.value, ctypes.byref(Guid.of(IID_ADAPTER3)), ctypes.byref(adapter3)) or not adapter3:
            return None
        query = method(adapter3.value, QUERY_VIDEO_MEMORY, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(_MemoryInfo))
        got = []
        for group in (LOCAL, SHARED):
            info = _MemoryInfo()
            if query(adapter3.value, 0, group, ctypes.byref(info)):
                return None
            got += [info.usage, info.budget]
        return VideoMemory(*got)
    finally:
        for obj in (adapter3, adapter, factory):
            release(obj.value)


@dataclass(frozen=True)
class Support:
    feature_level: str = ""  # "12_2"
    ray_tracing: str = ""  # "1.1"; "" none
    why: str = ""  # why nothing was read

    def text(self) -> str:
        if self.why:
            return self.why
        if self.ray_tracing:
            return tr("common.feature_level_ray_tracing_tier", level=self.feature_level, tier=self.ray_tracing)
        return tr("common.feature_level_no_ray_tracing", level=self.feature_level)


class _Levels(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint), ("levels", ctypes.POINTER(ctypes.c_uint)), ("max", ctypes.c_uint)]


class _Options5(ctypes.Structure):
    _fields_ = [("srv_tier3", ctypes.c_int), ("render_pass", ctypes.c_int), ("ray_tracing", ctypes.c_int)]


def _check(device: int, feature: int, data) -> bool:
    call = method(device, CHECK_FEATURE_SUPPORT, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    return call(device, feature, ctypes.byref(data), ctypes.sizeof(data)) == 0


def probe() -> Support:
    """The default adapter's Direct3D 12 support (the one Qt's views draw with unless told otherwise)."""
    if os.name != "nt":
        return Support(why=tr("common.direct3d_12_windows_only"))
    try:
        d3d12 = ctypes.WinDLL("d3d12")  # Windows adds the extension
    except OSError as e:
        return Support(why=tr("common.no_direct3d_12_error", error=str(e)))
    iid = Guid.of(IID_DEVICE)
    device = ctypes.c_void_p()
    create = d3d12.D3D12CreateDevice
    create.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    if create(None, 0xB000, ctypes.byref(iid), ctypes.byref(device)) or not device:
        return Support(why=tr("common.no_direct3d_12_device"))
    try:
        wanted = (ctypes.c_uint * len(LEVELS))(*LEVELS)
        levels = _Levels(len(LEVELS), wanted, 0)
        level = f"{levels.max >> 12}_{(levels.max >> 8) & 0xF}" if _check(device.value, FEATURE_LEVELS, levels) else ""
        options = _Options5()
        tier = options.ray_tracing if _check(device.value, OPTIONS5, options) else 0
        return Support(level, f"{tier // 10}.{tier % 10}" if tier else "")  # D3D12_RAYTRACING_TIER: 10 is 1.0
    finally:
        release(device.value)
