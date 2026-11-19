"""BC7 on the graphics card: texconv's DirectCompute encoder for the large pictures (docs/texture-pipeline.md, "GPU
encoder").

Its bytes depend on the input alone: an NVIDIA and an AMD adapter and WARP (Windows' software adapter) write the
same file, so WARP is the fallback when the card fails and a texture never changes with the machine's state.
``adapters()`` probes once and publishes the result in the environment, so every worker a run starts shares it. At
most ``jobs()`` encodes run on the machine at once (lock files in the cache); a worker waits for a slot rather than
encode on the CPU, which would write other bytes.
"""

from __future__ import annotations

import contextlib
import os
import re
import struct
import tempfile
import time
from pathlib import Path

from ..infra import buildcache
from ..infra.tools import find_tool, run_tool
from . import texbackend

ADAPTERS_ENV = "ANNO_BC7_GPU"  # texconv adapter numbers to try in order ("0,2"), or "none"; set by the probe
JOBS_ENV = "ANNO_BC7_GPU_JOBS"  # GPU encodes at once on the machine
DEFAULT_JOBS = 8  # throughput levels off at 12 concurrent encodes (texture-pipeline "GPU encoder")
MIN_TEXELS = 1024 * 1024  # smaller levels encode faster on the CPU than texconv starts
SETTINGS_VERSION = 1  # bumped when the arguments or the header handling change what comes out
WARP = "Microsoft Basic Render Driver"
MAX_ADAPTERS = 8
SLOT_WAIT = 0.05
_USING = re.compile(r'Using DirectCompute [\d.]+ on "([^"]*)"')


class GpuError(RuntimeError):
    """Every adapter failed on a texture."""


def _texconv() -> Path | None:
    return find_tool("texconv") if os.name == "nt" and texbackend.mode() != "builtin" else None


def _dds_in(width: int, height: int, data: bytes, srgb: bool) -> bytes:
    """One uncompressed R8G8B8A8 level as a DX10 DDS (what the probe encodes)."""
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x100F, height, width, width * 4, 1, 1) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return head + struct.pack("<5I", 29 if srgb else 28, 3, 0, 1, 0) + data


def run_encode(texconv: Path, index: int, dds: bytes, srgb: bool) -> tuple[str | None, bytes | None]:
    """``dds`` (uncompressed, its mips included) as texconv's BC7 on adapter ``index``: the adapter's name and the
    file, or None for each when the encode did not run on that adapter (texconv silently takes its CPU codec)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_gpu_") as tmp:
        src, out = Path(tmp) / "levels.dds", Path(tmp) / "out"
        out.mkdir()
        src.write_bytes(dds)
        fmt = "BC7_UNORM_SRGB" if srgb else "BC7_UNORM"
        res = run_tool(
            texconv, ["-gpu", str(index), "-f", fmt, "-ft", "dds", "-o", str(out), "-y", "-nologo", str(src)]
        )
        produced = out / "levels.dds"
        used = _USING.search(res.stdout or "")
        if not res.ok or used is None or not produced.is_file():
            return None, None
        return used.group(1), produced.read_bytes()


def _probe_image() -> bytes:
    """A fixed 64x64 picture with every BC7 mode's case in it (edges, gradients, alpha), from integers only."""
    out = bytearray()
    for y in range(64):
        for x in range(64):
            out += bytes(((x * 37 + y * 11) & 255, (x * y * 5) & 255, (255 - 4 * y) & 255, 255 if x < 48 else 4 * x))
    return bytes(out)


def probe(texconv: Path | None = None) -> str:
    """The adapters to use, in the ``ADAPTERS_ENV`` form: the first hardware adapter, then WARP; "none" when there
    is no texconv or hardware adapter, or when the hardware adapter's bytes differ between two runs or from WARP's."""
    texconv = texconv or _texconv()
    if texconv is None:
        return "none"
    dds = _dds_in(64, 64, _probe_image(), False)
    names: dict[int, tuple[str, bytes]] = {}
    for index in range(MAX_ADAPTERS):
        name, data = run_encode(texconv, index, dds, False)
        if name is None:
            break
        names[index] = (name, data)
    hardware = next((i for i, (n, _) in names.items() if n != WARP), None)
    warp = next((i for i, (n, _) in names.items() if n == WARP), None)
    if hardware is None:
        return "none"  # WARP alone is slower than the CPU encoder
    first = names[hardware][1]
    if run_encode(texconv, hardware, dds, False)[1] != first or (warp is not None and names[warp][1] != first):
        return "none"
    return ",".join(str(i) for i in (hardware, warp) if i is not None)


def adapters() -> list[int]:
    """The adapters this run encodes on (empty: no GPU), probed once and kept in the environment for the workers."""
    value = os.environ.get(ADAPTERS_ENV)
    if value is None:
        value = probe()
        os.environ[ADAPTERS_ENV] = value
    if value.strip().lower() in ("", "none", "off"):
        return []
    try:
        return [int(v) for v in value.split(",") if v.strip()]
    except ValueError:
        return []


def identity() -> str:
    """What decides the GPU encoder's bytes: the texconv build, the size limit and the settings (not the adapter)."""
    texconv = _texconv()
    digest = buildcache.file_digest(texconv)[:16] if texconv else "?"
    return f"texconv-dc-{digest}-min{MIN_TEXELS}-s{SETTINGS_VERSION}"


def jobs() -> int:
    try:
        return max(1, int(os.environ.get(JOBS_ENV, DEFAULT_JOBS)))
    except ValueError:
        return DEFAULT_JOBS


def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


def slot_folder() -> Path:
    from ..sources.overlay import cache_base

    return cache_base() / "gpu-slots"


@contextlib.contextmanager
def slot():
    """One of the machine's ``jobs()`` GPU slots, waited for; the operating system frees it when a process dies."""
    folder = slot_folder()
    folder.mkdir(parents=True, exist_ok=True)
    while True:
        for i in range(jobs()):
            handle = open(folder / f"slot{i}.lock", "a+b")  # noqa: SIM115 - held for the encode
            if _try_lock(handle):
                try:
                    yield i
                finally:
                    _unlock(handle)
                    handle.close()
                return
            handle.close()
        time.sleep(SLOT_WAIT)


def wanted(width: int, height: int) -> bool:
    """Whether a picture of this top level goes to the GPU (vector data never does: texture-pipeline "GPU encoder")."""
    return width * height >= MIN_TEXELS and bool(adapters())


def encode(dds: bytes, srgb: bool) -> bytes:
    """``dds`` (our uncompressed mip levels) as a BC7 file, on the first adapter that runs it. The header is the
    tail texconv writes after its own: the alpha mode it notes is left at 0, as the CPU encoder writes it."""
    from .bc7encode import dds_header

    texconv = _texconv()
    if texconv is None:
        raise GpuError("texconv is not there")
    height, width = struct.unpack_from("<II", dds, 12)
    mips = struct.unpack_from("<I", dds, 28)[0]
    with slot():
        for index in adapters():
            _name, data = run_encode(texconv, index, dds, srgb)
            if data is not None and data[84:88] == b"DX10" and len(data) > 148:
                return dds_header(width, height, max(1, mips), srgb) + data[148:]
    raise GpuError(f"no adapter of {adapters()} encoded the texture")
