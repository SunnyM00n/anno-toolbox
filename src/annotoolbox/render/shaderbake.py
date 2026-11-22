"""Our shaders baked for every QRhi backend by Qt's shader baker (``qsb``, shipped with PySide6): SPIR-V for Vulkan,
HLSL for Direct3D 11 and 12 (compiled by the system's D3D compiler when the pipeline is built), GLSL for OpenGL and
MSL for Metal, in one ``.qsb`` package per stage, with the baker's own reflection of the uniform block.

``bake(ported)`` is the entry (a ``glslport.SPorted`` in, a ``SBaked`` out); results are kept in the build cache by
the sources' hash, so a shader is baked once per change. ``find_qsb`` says where the baker is. docs/tool-suite.md
section 21, "Our own shaders".
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..infra import buildcache
from ..infra.tools import NO_WINDOW
from .glslport import SPorted

ENV_QSB = "ANNO_QSB"  # the baker's path, when it is not beside PySide6
TARGETS = ("--glsl", "330", "--hlsl", "50", "--msl", "12")  # SPIR-V is always in
COMPUTE_TARGETS = ("--glsl", "430", "--hlsl", "50", "--msl", "12")  # OpenGL has compute from 4.3
NAMESPACE = "qsb"
VERSION = 1  # bump when the baking or the reflection kept changes


class CBakeError(RuntimeError):
    """The baker refused a shader; the message is its output, the ported source's line numbers."""


@dataclass(frozen=True)
class SMember:
    """One member of the uniform block as the baker laid it out (std140)."""

    name: str
    type: str
    offset: int
    size: int
    count: int = 0  # array length, 0: not an array

    @property
    def stride(self) -> int:
        return self.size // self.count if self.count else self.size


@dataclass(frozen=True)
class SBaked:
    """The two stages' ``.qsb`` packages and the block layout (name -> member, the block's size)."""

    vertex: bytes
    fragment: bytes
    members: dict[str, SMember]
    block_size: int


_SCALARS = {"float": np.float32, "int": np.int32, "uint": np.uint32, "bool": np.int32}
_VECTORS = {"vec": "float", "ivec": "int", "uvec": "uint", "bvec": "bool"}


@functools.lru_cache(maxsize=256)
def _shape(gtype: str) -> tuple[type, int, int]:
    """(numpy type, columns, rows) of a GLSL type; a vector is one column."""
    if gtype in _SCALARS:
        return _SCALARS[gtype], 1, 1
    match = re.fullmatch(r"(vec|ivec|uvec|bvec)(\d)", gtype)
    if match:
        return _SCALARS[_VECTORS[match.group(1)]], 1, int(match.group(2))
    match = re.fullmatch(r"mat(\d)(?:x(\d))?", gtype)
    if match:
        return np.float32, int(match.group(1)), int(match.group(2) or match.group(1))
    raise ValueError(f"no std140 packing for {gtype}")


def pack_member(block: bytearray, member: SMember, values) -> None:
    """Write ``values`` (scalars in GLSL's column order, as moderngl takes them) into ``member`` of a std140 block."""
    kind, columns, rows = _shape(member.type)
    flat = np.asarray(values, dtype=np.float64).ravel().astype(kind)
    per = columns * rows
    column_stride = 16 if (columns > 1 or member.count) else rows * 4
    element_stride = member.stride if member.count else columns * column_stride
    if (
        column_stride == rows * 4 and element_stride == columns * column_stride
    ):  # tightly laid (a vec4, a mat4): one copy
        part = flat[: max(member.count, 1) * per]
        block[member.offset : member.offset + part.nbytes] = part.tobytes()
        return
    for element in range(min(max(member.count, 1), (len(flat) + per - 1) // per)):
        for column in range(columns):
            part = flat[element * per + column * rows : element * per + (column + 1) * rows]
            at = member.offset + element * element_stride + column * column_stride
            block[at : at + part.nbytes] = part.tobytes()


def write_member(block: bytearray, member: SMember, raw: bytes) -> None:
    """``raw`` as moderngl's ``Uniform.write`` takes it (tightly packed), into the block's std140 places."""
    kind = _shape(member.type)[0]
    pack_member(block, member, np.frombuffer(raw, dtype=np.float32 if kind is np.float32 else np.int32))


def find_qsb() -> Path | None:
    """Qt's shader baker: ``ANNO_QSB``, else the one inside the installed PySide6 package."""
    env = os.environ.get(ENV_QSB)
    if env and Path(env).is_file():
        return Path(env)
    try:
        import PySide6
    except ImportError:
        return None
    folder = Path(PySide6.__file__).parent
    for name in ("qsb.exe", "qsb", "Qt/libexec/qsb", "Qt/bin/qsb"):
        if (folder / name).is_file():
            return folder / name
    return None


def _run(qsb: Path, arguments: list[str]) -> str:
    try:
        done = subprocess.run(
            [str(qsb), *arguments],
            capture_output=True,
            encoding="utf-8",
            errors="replace",  # the machine's code page would stop on a byte of a message
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
            **NO_WINDOW,  # a console tool: the windowed program would flash a console for every shader
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # it did not start or never ended: the callers' one error
        raise CBakeError(f"Qt's shader baker did not run ({type(exc).__name__}: {exc})") from exc
    if done.returncode != 0:
        raise CBakeError((done.stdout + done.stderr).strip())
    return done.stdout


def reflection(dump: str) -> dict:
    """The reflection JSON of ``qsb -d`` output."""
    at = dump.find("Reflection info:")
    if at < 0:
        raise CBakeError("the baker printed no reflection")
    return json.JSONDecoder().raw_decode(dump[dump.index("{", at) :])[0]


def _layout(reflection: dict) -> tuple[dict[str, SMember], int]:
    for block in reflection.get("uniformBlocks", []):
        if block.get("binding") == 0:
            members = {
                member["name"]: SMember(
                    member["name"],
                    member["type"],
                    member["offset"],
                    member["size"],
                    (member.get("arrayDims") or [0])[0],
                )
                for member in block["members"]
            }
            return members, block["size"]
    return {}, 0


def _bake_stage(qsb: Path, source: str, suffix: str, scratch: Path, reflect: bool = True) -> tuple[bytes, dict]:
    """One stage baked, and (``reflect``) the baker's reflection of it."""
    source_file, out = scratch / f"shader.{suffix}", scratch / f"shader.{suffix}.qsb"
    source_file.write_text(source, encoding="utf-8", newline="\n")
    _run(qsb, [*TARGETS, "-o", str(out), str(source_file)])
    return out.read_bytes(), reflection(_run(qsb, ["-d", str(out)])) if reflect else {}


def bake(ported: SPorted) -> SBaked:
    """Both stages baked (or read from the cache); raises ``CBakeError`` when the baker is missing or refuses."""
    qsb = find_qsb()
    if qsb is None:
        raise CBakeError("Qt's shader baker (qsb) was not found: install PySide6 or set ANNO_QSB")
    key = buildcache.digest(VERSION, " ".join(TARGETS), ported.vertex, ported.fragment)
    files = buildcache.get_files(NAMESPACE, key)
    if files is None:
        # the two stages side by side (the baker is a program of its own); the uniform block is the vertex
        # stage's, so only it is read back (a first start bakes every program of the view's look on its thread)
        with (
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="qsb-") as scratch_dir,
            ThreadPoolExecutor(2) as pool,
        ):
            fragment = pool.submit(_bake_stage, qsb, ported.fragment, "frag", Path(scratch_dir), False)
            vert, vinfo = _bake_stage(qsb, ported.vertex, "vert", Path(scratch_dir))
            frag = fragment.result()[0]
        files = {"vert": vert, "frag": frag, "layout": json.dumps(vinfo).encode("utf-8")}
        buildcache.put_files(NAMESPACE, key, files)
    members, size = _layout(json.loads(files["layout"]))
    return SBaked(files["vert"], files["frag"], members, size)


DXBC_FLAGS = 0  # QRhi's Direct3D backends compile a stage's HLSL with no flags (Qt 6.12)


@functools.cache
def _system_compiler():
    """Windows' own D3DCompiler 47, the one QRhi's Direct3D backends load (None where it does not load)."""
    from ..infra import fxc

    try:
        return fxc.CFxc.windows()
    except (fxc.CFxcMissing, OSError):
        return None


def dxbc(hlsl: bytes, entry: str, profile: str, wait: bool = False) -> bytes | None:
    """A baked stage's HLSL compiled to DXBC as QRhi's Direct3D backends compile it when a pipeline is made (Windows'
    compiler, ``DXBC_FLAGS``), kept in the build cache: a later start makes the pipeline without compiling. Not kept
    yet: compiled now with ``wait``, else in the background for the next start and None (QRhi compiles it itself
    this time, as before: the window's thread never compiles more than it did). None too where no compiler loads or
    it refuses the text."""
    compiler = _system_compiler()
    if compiler is None:
        return None
    key = buildcache.digest(VERSION, "dxbc", compiler.version(), profile, entry, hlsl)
    kept = buildcache.get(NAMESPACE, key)
    if kept is not None or not buildcache.enabled():
        return kept
    if not wait:
        _background().submit(_compile_dxbc, compiler, key, hlsl, entry, profile)
        return None
    return _compile_dxbc(compiler, key, hlsl, entry, profile)


@functools.cache
def _background() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(1, thread_name_prefix="dxbc")


def _compile_dxbc(compiler, key: str, hlsl: bytes, entry: str, profile: str) -> bytes | None:
    done = compiler.compile(hlsl.decode("utf-8", "replace"), entry, profile, flags=DXBC_FLAGS)
    if not done.ok:
        return None
    buildcache.put(NAMESPACE, key, done.object)
    return done.object


def bake_compute(source: str) -> bytes:
    """A compute shader of ours (Vulkan GLSL) baked for every backend (or read from the cache): the ``.qsb``."""
    qsb = find_qsb()
    if qsb is None:
        raise CBakeError("Qt's shader baker (qsb) was not found: install PySide6 or set ANNO_QSB")
    key = buildcache.digest(VERSION, " ".join(COMPUTE_TARGETS), source)
    files = buildcache.get_files(NAMESPACE, key)
    if files is None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="qsb-") as scratch_dir:
            source_file, out = Path(scratch_dir) / "shader.comp", Path(scratch_dir) / "shader.comp.qsb"
            source_file.write_text(source, encoding="utf-8", newline="\n")
            _run(qsb, [*COMPUTE_TARGETS, "-o", str(out), str(source_file)])
            files = {"comp": out.read_bytes()}
        buildcache.put_files(NAMESPACE, key, files)
    return files["comp"]
