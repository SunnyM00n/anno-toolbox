"""Our shaders baked for every QRhi backend by Qt's shader baker (``qsb``, shipped with PySide6): SPIR-V for Vulkan,
HLSL for Direct3D 11 and 12 (compiled by the system's D3D compiler when the pipeline is built), GLSL for OpenGL and
MSL for Metal, in one ``.qsb`` package per stage, with the baker's own reflection of the uniform block.

``bake(ported)`` is the entry (a ``glslport.Ported`` in, a ``Baked`` out); results are kept in the build cache by
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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..infra import buildcache
from .glslport import Ported

ENV_QSB = "ANNO_QSB"  # the baker's path, when it is not beside PySide6
TARGETS = ("--glsl", "330", "--hlsl", "50", "--msl", "12")  # SPIR-V is always in
COMPUTE_TARGETS = ("--glsl", "430", "--hlsl", "50", "--msl", "12")  # OpenGL has compute from 4.3
NAMESPACE = "qsb"
VERSION = 1  # bump when the baking or the reflection kept changes


class BakeError(RuntimeError):
    """The baker refused a shader; the message is its output, the ported source's line numbers."""


@dataclass(frozen=True)
class Member:
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
class Baked:
    """The two stages' ``.qsb`` packages and the block layout (name -> member, the block's size)."""

    vertex: bytes
    fragment: bytes
    members: dict[str, Member]
    block_size: int


_SCALARS = {"float": np.float32, "int": np.int32, "uint": np.uint32, "bool": np.int32}
_VECTORS = {"vec": "float", "ivec": "int", "uvec": "uint", "bvec": "bool"}


@functools.lru_cache(maxsize=256)
def _shape(gtype: str) -> tuple[type, int, int]:
    """(numpy type, columns, rows) of a GLSL type; a vector is one column."""
    if gtype in _SCALARS:
        return _SCALARS[gtype], 1, 1
    m = re.fullmatch(r"(vec|ivec|uvec|bvec)(\d)", gtype)
    if m:
        return _SCALARS[_VECTORS[m.group(1)]], 1, int(m.group(2))
    m = re.fullmatch(r"mat(\d)(?:x(\d))?", gtype)
    if m:
        return np.float32, int(m.group(1)), int(m.group(2) or m.group(1))
    raise ValueError(f"no std140 packing for {gtype}")


def pack_member(block: bytearray, member: Member, values) -> None:
    """Write ``values`` (scalars in GLSL's column order, as moderngl takes them) into ``member`` of a std140 block."""
    kind, cols, rows = _shape(member.type)
    flat = np.asarray(values, dtype=np.float64).ravel().astype(kind)
    per = cols * rows
    column_stride = 16 if (cols > 1 or member.count) else rows * 4
    element_stride = member.stride if member.count else cols * column_stride
    if column_stride == rows * 4 and element_stride == cols * column_stride:  # tightly laid (a vec4, a mat4): one copy
        part = flat[: max(member.count, 1) * per]
        block[member.offset : member.offset + part.nbytes] = part.tobytes()
        return
    for e in range(min(max(member.count, 1), (len(flat) + per - 1) // per)):
        for c in range(cols):
            part = flat[e * per + c * rows : e * per + (c + 1) * rows]
            at = member.offset + e * element_stride + c * column_stride
            block[at : at + part.nbytes] = part.tobytes()


def write_member(block: bytearray, member: Member, raw: bytes) -> None:
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


def _run(qsb: Path, args: list[str]) -> str:
    done = subprocess.run(
        [str(qsb), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False
    )
    if done.returncode != 0:
        raise BakeError((done.stdout + done.stderr).strip())
    return done.stdout


def reflection(dump: str) -> dict:
    """The reflection JSON of ``qsb -d`` output."""
    at = dump.find("Reflection info:")
    if at < 0:
        raise BakeError("the baker printed no reflection")
    return json.JSONDecoder().raw_decode(dump[dump.index("{", at) :])[0]


def _layout(info: dict) -> tuple[dict[str, Member], int]:
    for block in info.get("uniformBlocks", []):
        if block.get("binding") == 0:
            members = {
                m["name"]: Member(m["name"], m["type"], m["offset"], m["size"], (m.get("arrayDims") or [0])[0])
                for m in block["members"]
            }
            return members, block["size"]
    return {}, 0


def _bake_stage(qsb: Path, source: str, suffix: str, scratch: Path) -> tuple[bytes, dict]:
    src, out = scratch / f"shader.{suffix}", scratch / f"shader.{suffix}.qsb"
    src.write_text(source, encoding="utf-8", newline="\n")
    _run(qsb, [*TARGETS, "-o", str(out), str(src)])
    return out.read_bytes(), reflection(_run(qsb, ["-d", str(out)]))


def bake(ported: Ported) -> Baked:
    """Both stages baked (or read from the cache); raises ``BakeError`` when the baker is missing or refuses."""
    qsb = find_qsb()
    if qsb is None:
        raise BakeError("Qt's shader baker (qsb) was not found: install PySide6 or set ANNO_QSB")
    key = buildcache.digest(VERSION, " ".join(TARGETS), ported.vertex, ported.fragment)
    files = buildcache.get_files(NAMESPACE, key)
    if files is None:
        with tempfile.TemporaryDirectory(prefix="qsb-") as tmp:
            vert, vinfo = _bake_stage(qsb, ported.vertex, "vert", Path(tmp))
            frag, _ = _bake_stage(qsb, ported.fragment, "frag", Path(tmp))
        files = {"vert": vert, "frag": frag, "layout": json.dumps(vinfo).encode("utf-8")}
        buildcache.put_files(NAMESPACE, key, files)
    members, size = _layout(json.loads(files["layout"]))
    return Baked(files["vert"], files["frag"], members, size)


def bake_compute(source: str) -> bytes:
    """A compute shader of ours (Vulkan GLSL) baked for every backend (or read from the cache): the ``.qsb``."""
    qsb = find_qsb()
    if qsb is None:
        raise BakeError("Qt's shader baker (qsb) was not found: install PySide6 or set ANNO_QSB")
    key = buildcache.digest(VERSION, " ".join(COMPUTE_TARGETS), source)
    files = buildcache.get_files(NAMESPACE, key)
    if files is None:
        with tempfile.TemporaryDirectory(prefix="qsb-") as tmp:
            src, out = Path(tmp) / "shader.comp", Path(tmp) / "shader.comp.qsb"
            src.write_text(source, encoding="utf-8", newline="\n")
            _run(qsb, [*COMPUTE_TARGETS, "-o", str(out), str(src)])
            files = {"comp": out.read_bytes()}
        buildcache.put_files(NAMESPACE, key, files)
    return files["comp"]
