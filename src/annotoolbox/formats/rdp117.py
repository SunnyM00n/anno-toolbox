"""117's baked particle files (``.rdp``): read, write and quantise (engine-particles "Particles").

An ``.rdp`` is tagged text (``<Tag>value</>``) with binary blobs (``CDATA[<u32 length><bytes>]``): per
particle its first and last frame (inclusive, wrapping past the end of a looped effect) and material, per
tick of every particle (its frames in order) a position, a rotation, a scale and a colour. The engine
never simulates: it replays what is stored, so a writer bakes its own simulation into it.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

CRLF = b"\r\n"
BLOBS = ("P_Starts", "P_Ends", "P_Materials", "T_Positions", "T_Rotations", "T_Scales", "T_Colors")
FRAME_TIME_MS = 33.333336  # 30 frames a second, as every vanilla file
MAX_FRAMES = 256  # the engine refuses more
MAX_MATERIALS = 8
POSITION_RANGE = 32767  # a position is a signed 16 bit fraction of the box: -32767..32767 maps to -1..1
ANGLE_STEPS = 255  # a rotation byte is a fraction of a turn in 255ths (0 and 255 are the same angle)


@dataclass
class RdpMaterial:
    """One material of the file: how its particles are drawn and which part of the texture they show."""

    view_aligned: int = 1  # 0 through the emitter's matrix, 1 faces the camera, 2 upright (cylindrical)
    order: int = 0  # draw order of the batches (a byte)
    color_boost: int = 0  # a byte; the engine lifts the colour by 1 + (boost * 0.07) ^ 2.2
    additive: bool = False
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # u, v, width, height of the texture
    refractive: int = 0
    wave: int = 0
    opaque_with_depth: int = 0  # cut at alpha 0.1, written to depth, drawn in the early pass
    line_type: int = 0
    line_length: float = 1.0  # a marching line's step along its own ages; a line only, as vanilla's trails
    line_tiling: float = 1.0  # how often the texture repeats along a line
    movie_texture: str = ""  # vanilla files name a material's texture here (a flipbook sheet), not in the header
    movie_fps: float = 0.0


@dataclass
class Rdp:
    """A parsed file as stored: quantised ticks, particle by particle in file order."""

    frame_count: int
    looped: bool
    max_scale: float
    center: tuple[float, float, float]
    extents: tuple[float, float, float]
    texture: str
    materials: list[RdpMaterial]
    starts: list[int]
    ends: list[int]
    particle_materials: list[int]
    positions: list[tuple[int, int, int]]  # signed: centre + extents * value / 32767
    rotations: list[tuple[int, int, int]]  # bytes about x, y, z, a turn in 255ths
    scales: list[tuple[int, int, int]]  # bytes: size / max_scale / 255 per axis
    colors: list[tuple[int, int, int, int]]  # bytes in file order: blue, green, red, alpha
    frame_time: float = FRAME_TIME_MS
    self_occlusion: bool = False
    lighting: bool = False
    do_sort: bool = False
    invincible: bool = False
    extra: dict[str, str] = field(default_factory=dict)  # header tags the reader does not model

    def ticks_of(self, i: int) -> int:
        """How many ticks particle ``i`` lives: its frames start..end inclusive, wrapped past the end."""
        s, e = self.starts[i], self.ends[i]
        return e - s + 1 if e >= s else e - s + self.frame_count + 1


def texture_file(path: str) -> str:
    """The file the engine loads for a texture reference: ``x.dds`` as it is, any other extension as
    ``x_0.dds`` (vanilla ships ``fire_01_diff_0.dds`` for a ``.psd`` reference)."""
    stem, dot, ext = path.rpartition(".")
    if dot and ext.lower() == "dds":
        return path
    return f"{stem if dot else path}_0.dds"


def validate(r: Rdp) -> None:
    """Raise ``ValueError`` for a file the engine would refuse or read wrongly."""
    n = len(r.starts)
    if not 1 <= r.frame_count <= MAX_FRAMES:
        raise ValueError(f"frame count {r.frame_count} outside 1..{MAX_FRAMES}")
    if not 1 <= len(r.materials) <= MAX_MATERIALS:
        raise ValueError(f"{len(r.materials)} materials; the engine wants 1..{MAX_MATERIALS}")
    for m in r.materials:
        if not (0 <= m.order <= 255 and 0 <= m.color_boost <= 255 and 0 <= m.line_type <= 255):
            raise ValueError("a material's Order, ColorBoost and LineType are bytes")
        if m.view_aligned not in (0, 1, 2):
            raise ValueError(f"ViewAligned {m.view_aligned} is not 0, 1 or 2")
    if not r.texture and not all(m.movie_texture for m in r.materials):
        raise ValueError("no texture path, in the header or on every material")
    if not (len(r.ends) == len(r.particle_materials) == n):
        raise ValueError("starts, ends and materials differ in length")
    if any(not 0 <= v < r.frame_count for v in (*r.starts, *r.ends)):
        raise ValueError("a particle starts or ends outside the frames")
    if any(not 0 <= m < len(r.materials) for m in r.particle_materials):
        raise ValueError("a particle names a material the file does not have")
    if any(a > b for a, b in zip(r.particle_materials, r.particle_materials[1:], strict=False)):
        raise ValueError("particles are not sorted by material; the engine sorts them and misreads the ticks")
    total = sum(r.ticks_of(i) for i in range(n))
    if not (total == len(r.positions) == len(r.rotations) == len(r.scales) == len(r.colors)):
        raise ValueError("the tick tracks do not match the particles' frame ranges")
    if any(not -POSITION_RANGE <= v <= POSITION_RANGE for p in r.positions for v in p):
        raise ValueError("a position is outside the signed 16 bit range")


def _f(x: float) -> str:
    return f"{x:.6f}"


def _vec(name: str, v: tuple[float, ...], comps: str) -> bytes:
    inner = "".join(f"<{c}>{_f(x)}</>\r\n" for c, x in zip(comps, v, strict=True))
    return f"<{name}>{inner}</>\r\n".encode()


def _blob(name: str, data: bytes) -> bytes:
    return b"<" + name.encode() + b">CDATA[" + struct.pack("<I", len(data)) + data + b"]</>\r\n"


def _material(m: RdpMaterial) -> bytes:
    u, v, w, h = m.crop
    movie = m.movie_texture or m.movie_fps
    line = m.line_type or (m.line_length, m.line_tiling) != (1.0, 1.0)  # vanilla quads carry them too
    return (
        f"<Material><ViewAligned>{m.view_aligned}</>\r\n<Order>{m.order}</>\r\n<ColorBoost>{m.color_boost}</>\r\n"
        f"<Refractive>{m.refractive}</>\r\n<WaveParticle>{m.wave}</>\r\n"
        + ("<OpaqueWithDepth>1</>\r\n" if m.opaque_with_depth else "")
        + f"<LineType>{m.line_type}</>\r\n"
        + (f"<LineLength>{_f(m.line_length)}</>\r\n<LineTiling>{_f(m.line_tiling)}</>\r\n" if line else "")
        + (f"<MovieFPS>{_f(m.movie_fps)}</>\r\n<MovieTexPath>{m.movie_texture}</>\r\n" if movie else "")
        + f"<cropUVWH><w>{_f(h)}</>\r\n<x>{_f(u)}</>\r\n<y>{_f(v)}</>\r\n<z>{_f(w)}</>\r\n</>\r\n"
        f"<Additive>{1 if m.additive else 0}</>\r\n</>\r\n"
    ).encode()


def write_rdp(r: Rdp) -> bytes:
    """The file's bytes: the header with every field the engine requires (frame count, frame time, looped,
    self occlusion, lighting, max scale, texture and at least one material with its order and view
    alignment), the materials, the per-particle blobs, the tick count and the four tick tracks."""
    validate(r)
    n = len(r.starts)
    total = sum(r.ticks_of(i) for i in range(n))
    head = (
        f"<FrameCount>{r.frame_count}</>\r\n<FrameTime>{_f(r.frame_time)}</>\r\n<Looped>{1 if r.looped else 0}</>\r\n"
        f"<ParticleCount>{n}</>\r\n<SelfOcclusion>{1 if r.self_occlusion else 0}</>\r\n"
        f"<Lighting>{1 if r.lighting else 0}</>\r\n<DoSort>{1 if r.do_sort else 0}</>\r\n"
        f"<Invincible>{1 if r.invincible else 0}</>\r\n<MaxScale>{_f(r.max_scale)}</>\r\n"
    ).encode()
    head += _vec("Center", r.center, "xyz") + _vec("Extents", r.extents, "xyz")
    head += "".join(f"<{k}>{v}</>\r\n" for k, v in r.extra.items()).encode("latin1")
    if r.texture:  # 289 of 117's 417 files name their textures on the materials only
        head += f"<TexturePath>{r.texture}</>\r\n".encode()
    head += b"<Materials>" + b"".join(_material(m) for m in r.materials) + b"</>\r\n"
    out = [head]
    out.append(_blob("P_Starts", struct.pack(f"<{n}h", *r.starts)))
    out.append(_blob("P_Ends", struct.pack(f"<{n}h", *r.ends)))
    out.append(_blob("P_Materials", bytes(r.particle_materials)))
    out.append(f"<TotalTickCount>{total}</>\r\n".encode())
    out.append(_blob("T_Positions", b"".join(struct.pack("<3h", *p) for p in r.positions)))
    out.append(_blob("T_Rotations", bytes(b for t in r.rotations for b in t)))
    out.append(_blob("T_Scales", bytes(b for t in r.scales for b in t)))
    out.append(_blob("T_Colors", bytes(b for t in r.colors for b in t)))
    return b"".join(out)


_TAG = re.compile(rb"<(\w+)>([^<]*)</>")
_COMPONENT = re.compile(rb"<(\w)>([^<]*)</>")  # <x>1.0</> inside a vector or crop block
_BLOB_START = re.compile(rb"<(" + b"|".join(b.encode() for b in BLOBS) + rb")>CDATA\[")
_MATERIAL = re.compile(rb"<Material>(.*?)\r\n</>\r\n(?=<Material>|</>)", re.S)
_HEADER_TAGS = {  # the tags ``Rdp`` models; the rest is kept in ``extra``
    "FrameCount",
    "FrameTime",
    "Looped",
    "ParticleCount",
    "SelfOcclusion",
    "Lighting",
    "DoSort",
    "Invincible",
    "MaxScale",
    "TexturePath",
    "TotalTickCount",
}


def _split_blobs(data: bytes) -> tuple[bytes, dict[str, bytes]]:
    """The text of the file with the ``CDATA`` blobs cut out, and the blobs by tag name."""
    blobs: dict[str, bytes] = {}
    pos = 0
    text = bytearray()
    for m in _BLOB_START.finditer(data):
        if m.start() < pos:
            continue
        (length,) = struct.unpack_from("<I", data, m.end())
        start = m.end() + 4
        if data[start + length : start + length + 1] != b"]":
            raise ValueError(f"{m.group(1).decode()}: blob does not end at its length")
        blobs[m.group(1).decode()] = data[start : start + length]
        text += data[pos : m.start()]
        pos = start + length + 1
    text += data[pos:]
    missing = [name for name in BLOBS if name not in blobs]
    if missing:
        raise ValueError(f"not an .rdp file: no {', '.join(missing)} blob")
    return bytes(text), blobs


def _tags(text: bytes) -> dict[str, str]:
    return {m.group(1).decode(): m.group(2).decode("latin1") for m in _TAG.finditer(text)}


def _vec3(text: bytes, name: str) -> tuple[float, float, float]:
    """The ``<name><x>..</><y>..</><z>..</></>`` block of ``text`` (zeros when absent)."""
    m = re.search(rb"<" + name.encode() + rb">(.*?)\r\n</>\r\n", text, re.S)
    vals = dict(_COMPONENT.findall(m.group(1))) if m else {}
    x, y, z = (float(vals.get(c, b"0")) for c in (b"x", b"y", b"z"))
    return (x, y, z)


def _parse_material(block: bytes) -> RdpMaterial:
    t = _tags(block)
    crop = re.search(rb"<cropUVWH>(.*?)\r\n</>\r\n", block, re.S)
    c = dict(_COMPONENT.findall(crop.group(1))) if crop else {}
    return RdpMaterial(
        view_aligned=int(float(t.get("ViewAligned", "1"))),
        order=int(float(t.get("Order", "0"))),
        color_boost=int(float(t.get("ColorBoost", "0"))),
        additive=t.get("Additive", "0") == "1",
        crop=(float(c.get(b"x", b"0")), float(c.get(b"y", b"0")), float(c.get(b"z", b"1")), float(c.get(b"w", b"1"))),
        refractive=int(float(t.get("Refractive", "0"))),
        wave=int(float(t.get("WaveParticle", "0"))),
        opaque_with_depth=int(float(t.get("OpaqueWithDepth", "0"))),
        line_type=int(float(t.get("LineType", "0"))),
        line_length=float(t.get("LineLength", "1")),
        line_tiling=float(t.get("LineTiling", "1")),
        movie_texture=t.get("MovieTexPath", ""),
        movie_fps=float(t.get("MovieFPS", "0")),
    )


def _shorts(blob: bytes) -> list[int]:
    return [v for (v,) in struct.iter_unpack("<h", blob[: len(blob) // 2 * 2])]


def _triples(blob: bytes, fmt: str, width: int) -> list[tuple[int, int, int]]:
    return [(a, b, c) for a, b, c in struct.iter_unpack(fmt, blob[: len(blob) // width * width])]


def _quads(blob: bytes) -> list[tuple[int, int, int, int]]:
    return [(a, b, c, d) for a, b, c, d in struct.iter_unpack("<4B", blob[: len(blob) // 4 * 4])]


def parse_rdp(data: bytes) -> Rdp:
    """Read a text ``.rdp`` (every vanilla and every written file is one; the RDM container form is not)."""
    text, blobs = _split_blobs(data)
    mat_at = text.find(b"<Materials>")
    head, mats = (text[:mat_at], text[mat_at:]) if mat_at >= 0 else (text, b"")
    flat = _tags(head)
    n = int(flat.get("ParticleCount", "0"))
    return Rdp(
        frame_count=int(flat["FrameCount"]),
        frame_time=float(flat.get("FrameTime", FRAME_TIME_MS)),
        looped=flat.get("Looped") == "1",
        max_scale=float(flat.get("MaxScale", "1")),
        center=_vec3(head, "Center"),
        extents=_vec3(head, "Extents"),
        texture=flat.get("TexturePath", ""),
        materials=[_parse_material(block) for block in _MATERIAL.findall(mats)],
        starts=_shorts(blobs["P_Starts"])[:n],
        ends=_shorts(blobs["P_Ends"])[:n],
        particle_materials=list(blobs["P_Materials"][:n]),
        positions=_triples(blobs["T_Positions"], "<3h", 6),
        rotations=_triples(blobs["T_Rotations"], "<3B", 3),
        scales=_triples(blobs["T_Scales"], "<3B", 3),
        colors=_quads(blobs["T_Colors"]),
        self_occlusion=flat.get("SelfOcclusion") == "1",
        lighting=flat.get("Lighting") == "1",
        do_sort=flat.get("DoSort") == "1",
        invincible=flat.get("Invincible") == "1",
        extra={k: v for k, v in flat.items() if k not in _HEADER_TAGS and len(k) > 1},  # not a vector's x, y, z
    )


@dataclass
class BakedParticle:
    """One particle of a baked effect in real units: the frames it lives (inclusive, wrapping when looped),
    its material and, per tick, position (object space, metres), size (3 axes, metres), rotation (radians
    about x, y, z; the engine applies x, then z, then y, each negated) and colour (0..1 rgba)."""

    start: int
    end: int
    material: int
    pos: list[tuple[float, float, float]]
    size: list[tuple[float, float, float]]
    rot: list[tuple[float, float, float]]
    color: list[tuple[float, float, float, float]]


def _q_position(v: float, center: float, extent: float) -> int:
    """A coordinate as the signed 16 bit fraction of the box the engine reads back as ``center + extent * q / 32767``."""
    return max(-POSITION_RANGE, min(POSITION_RANGE, round((v - center) / extent * POSITION_RANGE)))


def _byte(v: float) -> int:
    return max(0, min(255, round(v * 255)))


def _angle_byte(a: float) -> int:
    return round((a % (2 * math.pi)) / (2 * math.pi) * ANGLE_STEPS) % ANGLE_STEPS


def quantise(
    particles: list[BakedParticle],
    materials: list[RdpMaterial],
    texture: str,
    frame_count: int,
    looped: bool,
) -> Rdp:
    """Pack baked particles into a file the engine reads right.

    The particles are written sorted by material (stable): the engine sorts its records but reads the tick
    blobs in file order. The position box is the tight box of all positions, a scale byte is the size over
    the biggest size, and the first and last tick of every particle get alpha 0, as vanilla's do: the
    shader blends the last tick into a dead column, so a particle that ends visible would flash."""
    particles = sorted(particles, key=lambda p: p.material)
    pts = [p for part in particles for p in part.pos] or [(0.0, 0.0, 0.0)]
    lo = [min(p[a] for p in pts) for a in range(3)]
    hi = [max(p[a] for p in pts) for a in range(3)]
    center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
    extents = tuple(max((b - a) / 2, 1e-6) for a, b in zip(lo, hi, strict=True))
    max_scale = max((s for part in particles for t in part.size for s in t), default=1.0) or 1.0
    positions: list[tuple[int, int, int]] = []
    rotations: list[tuple[int, int, int]] = []
    scales: list[tuple[int, int, int]] = []
    colors: list[tuple[int, int, int, int]] = []
    for part in particles:
        last = len(part.pos) - 1
        for k, (p, s, r, c) in enumerate(zip(part.pos, part.size, part.rot, part.color, strict=True)):
            x, y, z = (_q_position(p[a], center[a], extents[a]) for a in range(3))
            positions.append((x, y, z))
            scales.append((_byte(s[0] / max_scale), _byte(s[1] / max_scale), _byte(s[2] / max_scale)))
            rotations.append((_angle_byte(r[0]), _angle_byte(r[1]), _angle_byte(r[2])))
            alpha = 0 if k in (0, last) else _byte(c[3])
            colors.append((_byte(c[2]), _byte(c[1]), _byte(c[0]), alpha))  # file order: blue, green, red, alpha
    return Rdp(
        frame_count=frame_count,
        looped=looped,
        max_scale=max_scale,
        center=center,
        extents=(extents[0], extents[1], extents[2]),
        texture=texture,
        materials=materials,
        starts=[p.start for p in particles],
        ends=[p.end for p in particles],
        particle_materials=[p.material for p in particles],
        positions=positions,
        rotations=rotations,
        scales=scales,
        colors=colors,
    )


def dequantise(r: Rdp) -> list[BakedParticle]:
    """The particles of a file in real units, as the engine reads them (the inverse of :func:`quantise`)."""

    def position(q: tuple[int, int, int]) -> tuple[float, float, float]:
        x, y, z = (r.center[a] + r.extents[a] * q[a] / POSITION_RANGE for a in range(3))
        return (x, y, z)

    def size(s: tuple[int, int, int]) -> tuple[float, float, float]:
        return (s[0] / 255 * r.max_scale, s[1] / 255 * r.max_scale, s[2] / 255 * r.max_scale)

    def angles(t: tuple[int, int, int]) -> tuple[float, float, float]:
        step = 2 * math.pi / ANGLE_STEPS
        return (t[0] * step, t[1] * step, t[2] * step)

    out = []
    at = 0
    for i in range(len(r.starts)):
        n = r.ticks_of(i)
        sl = slice(at, at + n)
        at += n
        out.append(
            BakedParticle(
                start=r.starts[i],
                end=r.ends[i],
                material=r.particle_materials[i],
                pos=[position(q) for q in r.positions[sl]],
                size=[size(s) for s in r.scales[sl]],
                rot=[angles(t) for t in r.rotations[sl]],
                color=[(c[2] / 255, c[1] / 255, c[0] / 255, c[3] / 255) for c in r.colors[sl]],
            )
        )
    return out
