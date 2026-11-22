"""Whether this machine has a usable Vulkan: the loader, an instance and at least one physical device.

``probe()`` answers once a session as a ``SProbe`` (``ok``, ``devices``, ``why`` in the interface language); no Qt.
It is the test behind ``rhiview.available("vulkan")``, so the Vulkan backend is offered only where it can start
(docs/linux.md "Phase 3 done").
"""

from __future__ import annotations

import ctypes
import functools
import sys
from dataclasses import dataclass

from ..infra.i18n import tr

LIBRARIES = {"darwin": ("libvulkan.1.dylib", "libMoltenVK.dylib"), "win32": ()}  # Windows: found by name below
STRUCTURE_INSTANCE_INFO = 1  # VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO


@dataclass(frozen=True)
class SProbe:
    ok: bool
    devices: int = 0
    why: str = ""


class _SInstanceInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_int32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.c_void_p),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


def _load() -> ctypes.CDLL | None:
    import ctypes.util  # here, not at import: on Windows ctypes.util loads kernel32 (test_windows_only_imports)

    names = LIBRARIES.get(sys.platform, ("libvulkan.so.1", "libvulkan.so"))
    found = ctypes.util.find_library("vulkan-1" if sys.platform == "win32" else "vulkan")
    for name in (*names, *([found] if found else [])):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def _count_devices(library: ctypes.CDLL) -> tuple[int, str]:
    """The physical devices of a fresh instance (0 and the reason when it cannot be made)."""
    instance_info = _SInstanceInfo(sType=STRUCTURE_INSTANCE_INFO)
    instance = ctypes.c_void_p()
    code = library.vkCreateInstance(ctypes.byref(instance_info), None, ctypes.byref(instance))
    if code != 0 or not instance.value:
        return 0, tr("common.vulkan_no_instance", code=code)
    try:
        count = ctypes.c_uint32(0)
        code = library.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)
        if code != 0:
            return 0, tr("common.vulkan_no_instance", code=code)
        return int(count.value), ""
    finally:
        library.vkDestroyInstance(instance, None)


@functools.cache
def probe() -> SProbe:
    library = _load()
    if library is None:
        return SProbe(False, why=tr("common.vulkan_loader_missing"))
    try:
        count, why = _count_devices(library)
    except Exception as exc:  # pattern: ok (a boundary: a broken driver fails in many ways; says why, no crash)
        return SProbe(False, why=tr("common.vulkan_probe_failed", error=exc))
    if count < 1:
        return SProbe(False, why=why or tr("common.vulkan_no_device"))
    return SProbe(True, count)
