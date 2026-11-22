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
class SRdpMaterial:
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
class SRdp:
    """A parsed file as stored: quantised ticks, particle by particle in file order."""

    frame_count: int
    looped: bool
    max_scale: float
    center: tuple[float, float, float]
    extents: tuple[float, float, float]
    texture: str
    materials: list[SRdpMaterial]
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
        start, end = self.starts[i], self.ends[i]
        return end - start + 1 if end >= start else end - start + self.frame_count + 1


def texture_file(path: str) -> str:
    """The file the engine loads for a texture reference: ``x.dds`` as it is, any other extension as
    ``x_0.dds`` (vanilla ships ``fire_01_diff_0.dds`` for a ``.psd`` reference)."""
    stem, dot, ext = path.rpartition(".")
    if dot and ext.lower() == "dds":
        return path
    return f"{stem if dot else path}_0.dds"


def validate(rdp: SRdp) -> None:
    """Raise ``ValueError`` for a file the engine would refuse or read wrongly."""
    particle_count = len(rdp.starts)
    if not 1 <= rdp.frame_count <= MAX_FRAMES:
        raise ValueError(f"frame count {rdp.frame_count} outside 1..{MAX_FRAMES}")
    if not 1 <= len(rdp.materials) <= MAX_MATERIALS:
        raise ValueError(f"{len(rdp.materials)} materials; the engine wants 1..{MAX_MATERIALS}")
    for material in rdp.materials:
        if not (0 <= material.order <= 255 and 0 <= material.color_boost <= 255 and 0 <= material.line_type <= 255):
            raise ValueError("a material's Order, ColorBoost and LineType are bytes")
        if material.view_aligned not in (0, 1, 2):
            raise ValueError(f"ViewAligned {material.view_aligned} is not 0, 1 or 2")
    if not rdp.texture and not all(material.movie_texture for material in rdp.materials):
        raise ValueError("no texture path, in the header or on every material")
    if not (len(rdp.ends) == len(rdp.particle_materials) == particle_count):
        raise ValueError("starts, ends and materials differ in length")
    if any(not 0 <= frame < rdp.frame_count for frame in (*rdp.starts, *rdp.ends)):
        raise ValueError("a particle starts or ends outside the frames")
    if any(not 0 <= material < len(rdp.materials) for material in rdp.particle_materials):
        raise ValueError("a particle names a material the file does not have")
    if any(
        previous > following
        for previous, following in zip(rdp.particle_materials, rdp.particle_materials[1:], strict=False)
    ):
        raise ValueError("particles are not sorted by material; the engine sorts them and misreads the ticks")
    total = sum(rdp.ticks_of(i) for i in range(particle_count))
    if not (total == len(rdp.positions) == len(rdp.rotations) == len(rdp.scales) == len(rdp.colors)):
        raise ValueError("the tick tracks do not match the particles' frame ranges")
    if any(not -POSITION_RANGE <= value <= POSITION_RANGE for position in rdp.positions for value in position):
        raise ValueError("a position is outside the signed 16 bit range")


def _float_text(value: float) -> str:
    return f"{value:.6f}"


def _vector(name: str, values: tuple[float, ...], components: str) -> bytes:
    inner = "".join(f"<{component}>{_float_text(x)}</>\r\n" for component, x in zip(components, values, strict=True))
    return f"<{name}>{inner}</>\r\n".encode()


def _blob(name: str, data: bytes) -> bytes:
    return b"<" + name.encode() + b">CDATA[" + struct.pack("<I", len(data)) + data + b"]</>\r\n"


def _material(material: SRdpMaterial) -> bytes:
    crop_u, crop_v, crop_width, crop_height = material.crop
    movie = material.movie_texture or material.movie_fps
    line = material.line_type or (material.line_length, material.line_tiling) != (
        1.0,
        1.0,
    )  # vanilla quads carry them too
    return (
        f"<Material><ViewAligned>{material.view_aligned}</>\r\n<Order>{material.order}</>\r\n<ColorBoost>{material.color_boost}</>\r\n"
        f"<Refractive>{material.refractive}</>\r\n<WaveParticle>{material.wave}</>\r\n"
        + ("<OpaqueWithDepth>1</>\r\n" if material.opaque_with_depth else "")
        + f"<LineType>{material.line_type}</>\r\n"
        + (
            f"<LineLength>{_float_text(material.line_length)}</>\r\n<LineTiling>{_float_text(material.line_tiling)}</>\r\n"
            if line
            else ""
        )
        + (
            f"<MovieFPS>{_float_text(material.movie_fps)}</>\r\n<MovieTexPath>{material.movie_texture}</>\r\n"
            if movie
            else ""
        )
        + f"<cropUVWH><w>{_float_text(crop_height)}</>\r\n<x>{_float_text(crop_u)}</>\r\n"
        f"<y>{_float_text(crop_v)}</>\r\n<z>{_float_text(crop_width)}</>\r\n</>\r\n"
        f"<Additive>{1 if material.additive else 0}</>\r\n</>\r\n"
    ).encode()


def write_rdp(rdp: SRdp) -> bytes:
    """The file's bytes: the header with every field the engine requires (frame count, frame time, looped,
    self occlusion, lighting, max scale, texture and at least one material with its order and view
    alignment), the materials, the per-particle blobs, the tick count and the four tick tracks."""
    validate(rdp)
    particle_count = len(rdp.starts)
    total = sum(rdp.ticks_of(i) for i in range(particle_count))
    head = (
        f"<FrameCount>{rdp.frame_count}</>\r\n<FrameTime>{_float_text(rdp.frame_time)}</>\r\n<Looped>{1 if rdp.looped else 0}</>\r\n"
        f"<ParticleCount>{particle_count}</>\r\n<SelfOcclusion>{1 if rdp.self_occlusion else 0}</>\r\n"
        f"<Lighting>{1 if rdp.lighting else 0}</>\r\n<DoSort>{1 if rdp.do_sort else 0}</>\r\n"
        f"<Invincible>{1 if rdp.invincible else 0}</>\r\n<MaxScale>{_float_text(rdp.max_scale)}</>\r\n"
    ).encode()
    head += _vector("Center", rdp.center, "xyz") + _vector("Extents", rdp.extents, "xyz")
    head += "".join(f"<{k}>{value}</>\r\n" for k, value in rdp.extra.items()).encode("latin1")
    if rdp.texture:  # 289 of 117's 417 files name their textures on the materials only
        head += f"<TexturePath>{rdp.texture}</>\r\n".encode()
    head += b"<Materials>" + b"".join(_material(material) for material in rdp.materials) + b"</>\r\n"
    out = [head]
    out.append(_blob("P_Starts", struct.pack(f"<{particle_count}h", *rdp.starts)))
    out.append(_blob("P_Ends", struct.pack(f"<{particle_count}h", *rdp.ends)))
    out.append(_blob("P_Materials", bytes(rdp.particle_materials)))
    out.append(f"<TotalTickCount>{total}</>\r\n".encode())
    out.append(_blob("T_Positions", b"".join(struct.pack("<3h", *position) for position in rdp.positions)))
    out.append(_blob("T_Rotations", bytes(byte for rotation in rdp.rotations for byte in rotation)))
    out.append(_blob("T_Scales", bytes(byte for tick in rdp.scales for byte in tick)))
    out.append(_blob("T_Colors", bytes(byte for tick in rdp.colors for byte in tick)))
    return b"".join(out)


_TAG = re.compile(rb"<(\w+)>([^<]*)</>")
_COMPONENT = re.compile(rb"<(\w)>([^<]*)</>")  # <x>1.0</> inside a vector or crop block
_BLOB_START = re.compile(rb"<(" + b"|".join(b.encode() for b in BLOBS) + rb")>CDATA\[")
_MATERIAL = re.compile(rb"<Material>(.*?)\r\n</>\r\n(?=<Material>|</>)", re.S)
_HEADER_TAGS = {  # the tags ``SRdp`` models; the rest is kept in ``extra``
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
    position = 0
    text = bytearray()
    for match in _BLOB_START.finditer(data):
        if match.start() < position:
            continue
        if match.end() + 4 > len(data):
            raise ValueError(f"{match.group(1).decode()}: blob cut off before its length")
        (length,) = struct.unpack_from("<I", data, match.end())
        start = match.end() + 4
        if data[start + length : start + length + 1] != b"]":
            raise ValueError(f"{match.group(1).decode()}: blob does not end at its length")
        blobs[match.group(1).decode()] = data[start : start + length]
        text += data[position : match.start()]
        position = start + length + 1
    text += data[position:]
    missing = [name for name in BLOBS if name not in blobs]
    if missing:
        raise ValueError(f"not an .rdp file: no {', '.join(missing)} blob")
    return bytes(text), blobs


def _tags(text: bytes) -> dict[str, str]:
    return {match.group(1).decode(): match.group(2).decode("latin1") for match in _TAG.finditer(text)}


def _vec3(text: bytes, name: str) -> tuple[float, float, float]:
    """The ``<name><x>..</><y>..</><z>..</></>`` block of ``text`` (zeros when absent)."""
    match = re.search(rb"<" + name.encode() + rb">(.*?)\r\n</>\r\n", text, re.S)
    values = dict(_COMPONENT.findall(match.group(1))) if match else {}
    x, y, z = (float(values.get(component, b"0")) for component in (b"x", b"y", b"z"))
    return (x, y, z)


def _parse_material(block: bytes) -> SRdpMaterial:
    tags = _tags(block)
    crop = re.search(rb"<cropUVWH>(.*?)\r\n</>\r\n", block, re.S)
    components = dict(_COMPONENT.findall(crop.group(1))) if crop else {}
    return SRdpMaterial(
        view_aligned=int(float(tags.get("ViewAligned", "1"))),
        order=int(float(tags.get("Order", "0"))),
        color_boost=int(float(tags.get("ColorBoost", "0"))),
        additive=tags.get("Additive", "0") == "1",
        crop=(
            float(components.get(b"x", b"0")),
            float(components.get(b"y", b"0")),
            float(components.get(b"z", b"1")),
            float(components.get(b"w", b"1")),
        ),
        refractive=int(float(tags.get("Refractive", "0"))),
        wave=int(float(tags.get("WaveParticle", "0"))),
        opaque_with_depth=int(float(tags.get("OpaqueWithDepth", "0"))),
        line_type=int(float(tags.get("LineType", "0"))),
        line_length=float(tags.get("LineLength", "1")),
        line_tiling=float(tags.get("LineTiling", "1")),
        movie_texture=tags.get("MovieTexPath", ""),
        movie_fps=float(tags.get("MovieFPS", "0")),
    )


def _shorts(blob: bytes) -> list[int]:
    return [value for (value,) in struct.iter_unpack("<h", blob[: len(blob) // 2 * 2])]


def _triples(blob: bytes, struct_format: str, width_bytes: int) -> list[tuple[int, int, int]]:
    return [(x, y, z) for x, y, z in struct.iter_unpack(struct_format, blob[: len(blob) // width_bytes * width_bytes])]


def _quads(blob: bytes) -> list[tuple[int, int, int, int]]:
    return [
        (blue, green, red, alpha) for blue, green, red, alpha in struct.iter_unpack("<4B", blob[: len(blob) // 4 * 4])
    ]


def parse_rdp(data: bytes) -> SRdp:
    """Read a text ``.rdp`` (every vanilla and every written file is one; the RDM container form is not)."""
    text, blobs = _split_blobs(data)
    materials_start = text.find(b"<Materials>")
    head, mats = (text[:materials_start], text[materials_start:]) if materials_start >= 0 else (text, b"")
    flat = _tags(head)
    particle_count = int(flat.get("ParticleCount", "0"))
    if "FrameCount" not in flat:
        raise ValueError("not an .rdp file: no FrameCount")
    return SRdp(
        frame_count=int(flat["FrameCount"]),
        frame_time=float(flat.get("FrameTime", FRAME_TIME_MS)),
        looped=flat.get("Looped") == "1",
        max_scale=float(flat.get("MaxScale", "1")),
        center=_vec3(head, "Center"),
        extents=_vec3(head, "Extents"),
        texture=flat.get("TexturePath", ""),
        materials=[_parse_material(block) for block in _MATERIAL.findall(mats)],
        starts=_shorts(blobs["P_Starts"])[:particle_count],
        ends=_shorts(blobs["P_Ends"])[:particle_count],
        particle_materials=list(blobs["P_Materials"][:particle_count]),
        positions=_triples(blobs["T_Positions"], "<3h", 6),
        rotations=_triples(blobs["T_Rotations"], "<3B", 3),
        scales=_triples(blobs["T_Scales"], "<3B", 3),
        colors=_quads(blobs["T_Colors"]),
        self_occlusion=flat.get("SelfOcclusion") == "1",
        lighting=flat.get("Lighting") == "1",
        do_sort=flat.get("DoSort") == "1",
        invincible=flat.get("Invincible") == "1",
        extra={k: value for k, value in flat.items() if k not in _HEADER_TAGS and len(k) > 1},  # not a vector's x, y, z
    )


@dataclass
class SBakedParticle:
    """One particle of a baked effect in real units: the frames it lives (inclusive, wrapping when looped),
    its material and, per tick, position (object space, metres), size (3 axes, metres), rotation (radians
    about x, y, z; the engine applies x, then z, then y, each negated) and colour (0..1 rgba)."""

    start: int
    end: int
    material: int
    position: list[tuple[float, float, float]]
    size: list[tuple[float, float, float]]
    rotation: list[tuple[float, float, float]]
    color: list[tuple[float, float, float, float]]


def _quantised_position(coordinate: float, center: float, extent: float) -> int:
    """A coordinate as the signed 16 bit fraction of the box the engine reads back as ``center + extent * q / 32767``."""
    return max(-POSITION_RANGE, min(POSITION_RANGE, round((coordinate - center) / extent * POSITION_RANGE)))


def _byte(fraction: float) -> int:
    return max(0, min(255, round(fraction * 255)))


def _angle_byte(radians: float) -> int:
    return round((radians % (2 * math.pi)) / (2 * math.pi) * ANGLE_STEPS) % ANGLE_STEPS


def quantise(
    particles: list[SBakedParticle],
    materials: list[SRdpMaterial],
    texture: str,
    frame_count: int,
    looped: bool,
) -> SRdp:
    """Pack baked particles into a file the engine reads right.

    The particles are written sorted by material (stable): the engine sorts its records but reads the tick
    blobs in file order. The position box is the tight box of all positions, a scale byte is the size over
    the biggest size, and the first and last tick of every particle get alpha 0, as vanilla's do: the
    shader blends the last tick into a dead column, so a particle that ends visible would flash."""
    particles = sorted(particles, key=lambda particle: particle.material)
    points = [point for part in particles for point in part.position] or [(0.0, 0.0, 0.0)]
    low = [min(point[axis] for point in points) for axis in range(3)]
    high = [max(point[axis] for point in points) for axis in range(3)]
    center = ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2)
    extents = tuple(max((high_value - low_value) / 2, 1e-6) for low_value, high_value in zip(low, high, strict=True))
    max_scale = (
        max((size_value for part in particles for size_tick in part.size for size_value in size_tick), default=1.0)
        or 1.0
    )
    positions: list[tuple[int, int, int]] = []
    rotations: list[tuple[int, int, int]] = []
    scales: list[tuple[int, int, int]] = []
    colors: list[tuple[int, int, int, int]] = []
    for part in particles:
        last = len(part.position) - 1
        for k, (point, tick_size, tick_rotation, rgba) in enumerate(
            zip(part.position, part.size, part.rotation, part.color, strict=True)
        ):
            x, y, z = (_quantised_position(point[axis], center[axis], extents[axis]) for axis in range(3))
            positions.append((x, y, z))
            scales.append(
                (_byte(tick_size[0] / max_scale), _byte(tick_size[1] / max_scale), _byte(tick_size[2] / max_scale))
            )
            rotations.append(
                (_angle_byte(tick_rotation[0]), _angle_byte(tick_rotation[1]), _angle_byte(tick_rotation[2]))
            )
            alpha = 0 if k in (0, last) else _byte(rgba[3])
            colors.append(
                (_byte(rgba[2]), _byte(rgba[1]), _byte(rgba[0]), alpha)
            )  # file order: blue, green, red, alpha
    return SRdp(
        frame_count=frame_count,
        looped=looped,
        max_scale=max_scale,
        center=center,
        extents=(extents[0], extents[1], extents[2]),
        texture=texture,
        materials=materials,
        starts=[particle.start for particle in particles],
        ends=[particle.end for particle in particles],
        particle_materials=[particle.material for particle in particles],
        positions=positions,
        rotations=rotations,
        scales=scales,
        colors=colors,
    )


def dequantise(rdp: SRdp) -> list[SBakedParticle]:
    """The particles of a file in real units, as the engine reads them (the inverse of :func:`quantise`)."""

    def position(quantised: tuple[int, int, int]) -> tuple[float, float, float]:
        x, y, z = (rdp.center[axis] + rdp.extents[axis] * quantised[axis] / POSITION_RANGE for axis in range(3))
        return (x, y, z)

    def size(quantised: tuple[int, int, int]) -> tuple[float, float, float]:
        return (
            quantised[0] / 255 * rdp.max_scale,
            quantised[1] / 255 * rdp.max_scale,
            quantised[2] / 255 * rdp.max_scale,
        )

    def angles(quantised: tuple[int, int, int]) -> tuple[float, float, float]:
        step = 2 * math.pi / ANGLE_STEPS
        return (quantised[0] * step, quantised[1] * step, quantised[2] * step)

    out = []
    tick_offset = 0
    for i in range(len(rdp.starts)):
        tick_count = rdp.ticks_of(i)
        ticks = slice(tick_offset, tick_offset + tick_count)
        tick_offset += tick_count
        out.append(
            SBakedParticle(
                start=rdp.starts[i],
                end=rdp.ends[i],
                material=rdp.particle_materials[i],
                position=[position(quantised) for quantised in rdp.positions[ticks]],
                size=[size(quantised) for quantised in rdp.scales[ticks]],
                rotation=[angles(quantised) for quantised in rdp.rotations[ticks]],
                color=[(rgba[2] / 255, rgba[1] / 255, rgba[0] / 255, rgba[3] / 255) for rgba in rdp.colors[ticks]],
            )
        )
    return out
