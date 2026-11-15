"""DDS files read to 8-bit RGBA in numpy, without texconv or a PNG in between (docs/texture-pipeline.md).

``read_dds`` reads the top mip of what 2070 ships: DXT1 / DXT3 / DXT5, ATI1 / ATI2 (BC4 / BC5) and the uncompressed
8, 16, 24 and 32 bit layouts. Anything else (BC7, cubes, volumes, float formats) raises ``UnsupportedDds`` and the caller falls
back to texconv. All integer arithmetic, so the result is the same on every machine. ``DdsFile`` reads any level and
face of the newer games' files too (the DX10 header; BC7 by ``bc7decode``, BC6H by ``bc6hdecode``),
``level_float`` an HDR layout's values unclipped.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .bc6hdecode import decode_bc6h
from .bc7decode import decode_bc7

DDPF_FOURCC, DDPF_RGB, DDPF_LUMINANCE, DDPF_ALPHA = 0x4, 0x40, 0x20000, 0x2
CAPS2_CUBE, CAPS2_VOLUME = 0x200, 0x200000


class UnsupportedDds(ValueError):
    """A DDS this module does not read; texconv does."""


def expand565(c: np.ndarray) -> np.ndarray:
    """RGB565 words to 8-bit RGB (bits replicated, as the D3D reference does)."""
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1).astype(np.int32)


def _bc1_blocks(blocks: np.ndarray, four_colour_only: bool) -> np.ndarray:
    """``(n, 8)`` BC1 colour blocks to ``(n, 16, 4)`` RGBA (int32). BC2/3 always use the four-colour mode."""
    c0 = blocks[:, 0].astype(np.int32) | (blocks[:, 1].astype(np.int32) << 8)
    c1 = blocks[:, 2].astype(np.int32) | (blocks[:, 3].astype(np.int32) << 8)
    e0, e1 = expand565(c0), expand565(c1)
    four = np.ones(len(blocks), bool) if four_colour_only else c0 > c1
    palette = np.zeros((len(blocks), 4, 4), np.int32)
    palette[:, :, 3] = 255
    palette[:, 0, :3], palette[:, 1, :3] = e0, e1
    palette[:, 2, :3] = np.where(four[:, None], (2 * e0 + e1) // 3, (e0 + e1) // 2)
    palette[:, 3, :3] = np.where(four[:, None], (e0 + 2 * e1) // 3, 0)
    palette[:, 3, 3] = np.where(four, 255, 0)
    bits = np.ascontiguousarray(blocks[:, 4:8]).view("<u4")[:, 0]
    index = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3
    return np.take_along_axis(palette, index[:, :, None].astype(np.intp), axis=1)


def _bc4_blocks(blocks: np.ndarray) -> np.ndarray:
    """``(n, 8)`` BC4 / BC3-alpha blocks to ``(n, 16)`` uint8."""
    a0, a1 = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
    eight = a0 > a1
    palette = np.zeros((len(blocks), 8), np.int32)
    palette[:, 0], palette[:, 1] = a0, a1
    for i in range(1, 7):
        palette[:, i + 1] = np.where(eight, ((7 - i) * a0 + i * a1) // 7, ((5 - i) * a0 + i * a1) // 5 if i < 5 else 0)
    palette[:, 7] = np.where(eight, palette[:, 7], 255)
    raw = np.zeros((len(blocks), 8), np.uint8)
    raw[:, :6] = blocks[:, 2:8]
    bits = raw.view("<u8")[:, 0]
    index = (bits[:, None] >> (3 * np.arange(16, dtype=np.uint64))[None, :]) & np.uint64(7)
    return np.take_along_axis(palette, index.astype(np.intp), axis=1).astype(np.uint8)


def _tile(texels: np.ndarray, width: int, height: int) -> np.ndarray:
    """``(blocks, 16, c)`` in block order to a ``(height, width, c)`` image, the block padding cropped."""
    bw, bh = -(-width // 4), -(-height // 4)
    image = texels.reshape(bh, bw, 4, 4, -1).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, -1)
    return np.ascontiguousarray(image[:height, :width])


def _block_bytes(data: bytes, offset: int, count: int, size: int) -> np.ndarray:
    if len(data) < offset + count * size:
        raise UnsupportedDds("the file is shorter than its header says")
    return np.frombuffer(data, np.uint8, count * size, offset).reshape(count, size)


def _compressed(fourcc: bytes, data: bytes, width: int, height: int, offset: int = 128) -> np.ndarray:
    count = -(-width // 4) * -(-height // 4)
    if fourcc == b"DXT1":
        return _tile(_bc1_blocks(_block_bytes(data, offset, count, 8), False), width, height).astype(np.uint8)
    if fourcc in (b"DXT3", b"DXT5"):
        blocks = _block_bytes(data, offset, count, 16)
        rgba = _bc1_blocks(blocks[:, 8:], True)
        if fourcc == b"DXT5":
            alpha = _bc4_blocks(blocks[:, :8])
        else:  # explicit 4-bit alpha, 16 nibbles
            nibbles = np.stack([blocks[:, :8] & 15, blocks[:, :8] >> 4], axis=-1).reshape(count, 16)
            alpha = (nibbles * 17).astype(np.uint8)
        rgba[:, :, 3] = alpha
        return _tile(rgba, width, height).astype(np.uint8)
    if fourcc in (b"ATI1", b"BC4U"):
        grey = _bc4_blocks(_block_bytes(data, offset, count, 8))
        zero = np.zeros_like(grey)
        return _tile(np.stack([grey, zero, zero, zero + 255], axis=-1), width, height)
    if fourcc in (b"ATI2", b"BC5U"):
        blocks = _block_bytes(data, offset, count, 16)
        red, green = _bc4_blocks(blocks[:, :8]), _bc4_blocks(blocks[:, 8:])
        zero = np.zeros_like(red)
        return _tile(np.stack([red, green, zero, zero + 255], axis=-1), width, height)
    raise UnsupportedDds(f"format {fourcc!r}")


def _channel(words: np.ndarray, mask: int, default: int) -> np.ndarray:
    """The channel a bit mask picks from the texel words, scaled to 0..255."""
    if mask == 0:
        return np.full(words.shape, default, np.uint8)
    shift = (mask & -mask).bit_length() - 1
    top = mask >> shift
    if top == 255:
        return ((words & mask) >> shift).astype(np.uint8)
    return ((((words & mask) >> shift).astype(np.uint32) * 255 + top // 2) // top).astype(np.uint8)


def _uncompressed(data: bytes, width: int, height: int, flags: int, pf: tuple, offset: int = 128) -> np.ndarray:
    bits, rmask, gmask, bmask, amask = pf
    if bits not in (8, 16, 24, 32):
        raise UnsupportedDds(f"{bits} bits per texel")
    count = width * height
    if len(data) < offset + count * (bits // 8):
        raise UnsupportedDds("the file is shorter than its header says")
    if bits == 24:
        raw = np.frombuffer(data, np.uint8, count * 3, offset).reshape(count, 3).astype(np.uint32)
        words = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
    else:
        words = np.frombuffer(data, f"<u{bits // 8}", count, offset).astype(np.uint32)
    if flags & DDPF_LUMINANCE:
        lum = _channel(words, rmask or (1 << bits) - 1, 0)
        px = [lum, lum, lum, _channel(words, amask, 255)]
    elif flags & DDPF_ALPHA and not flags & DDPF_RGB:
        px = [np.full(count, 255, np.uint8)] * 3 + [_channel(words, amask or (1 << bits) - 1, 255)]
    else:
        px = [_channel(words, rmask, 0), _channel(words, gmask, 0), _channel(words, bmask, 0)]
        px.append(_channel(words, amask, 255))
    return np.stack(px, axis=-1).reshape(height, width, 4)


def read_dds(data: bytes) -> np.ndarray:
    """The top mip of a DDS as ``(height, width, 4)`` uint8 RGBA, the stored values (no gamma step)."""
    if len(data) < 128 or data[:4] != b"DDS ":
        raise UnsupportedDds("not a DDS")
    height, width = struct.unpack_from("<II", data, 12)
    pf_size, pf_flags, fourcc = struct.unpack_from("<II4s", data, 76)
    pf = struct.unpack_from("<5I", data, 88)
    caps2 = struct.unpack_from("<I", data, 112)[0]
    if caps2 & (CAPS2_CUBE | CAPS2_VOLUME) or not width or not height or pf_size != 32:
        raise UnsupportedDds("cube, volume or malformed header")
    if pf_flags & DDPF_FOURCC:
        return _compressed(fourcc, data, width, height)
    return _uncompressed(data, width, height, pf_flags, pf)


# Any level and face, the DX10 header included

DDSD_MIPMAPCOUNT, DX10_CUBE, DXGI_BC6H_SF16 = 0x20000, 0x4, 96
# DXGI format -> (kind, sRGB): a block kind (``BLOCKS``) or an uncompressed layout (``PLAIN``)
# fmt: off
DXGI = {
    71: ("DXT1", False), 72: ("DXT1", True), 74: ("DXT3", False), 75: ("DXT3", True), 77: ("DXT5", False),
    78: ("DXT5", True), 80: ("ATI1", False), 83: ("ATI2", False), 95: ("BC6H", False), 96: ("BC6H", False),
    98: ("BC7", False), 99: ("BC7", True), 28: ("RGBA8", False), 29: ("RGBA8", True), 87: ("BGRA8", False),
    91: ("BGRA8", True), 88: ("BGRX8", False), 93: ("BGRX8", True), 61: ("R8", False), 49: ("RG8", False),
    56: ("R16", False), 34: ("RG16F", False), 35: ("RG16", False), 54: ("R16F", False), 41: ("R32F", False),
    10: ("RGBA16F", False), 2: ("RGBA32F", False),
    # the typeless and integer twins of those: the same bytes (117 ships R16G16_UINT flow and offset maps)
    70: ("DXT1", False), 73: ("DXT3", False), 76: ("DXT5", False), 79: ("ATI1", False), 82: ("ATI2", False),
    94: ("BC6H", False), 97: ("BC7", False), 27: ("RGBA8", False), 30: ("RGBA8", False), 90: ("BGRA8", False),
    92: ("BGRX8", False), 60: ("R8", False), 62: ("R8", False), 48: ("RG8", False), 50: ("RG8", False),
    53: ("R16", False), 57: ("R16", False), 33: ("RG16", False), 36: ("RG16", False), 11: ("RGBA16", False),
    12: ("RGBA16", False), 9: ("RGBA16", False), 16: ("RG32F", False), 65: ("A8", False), 51: ("RG8S", False),
}
# layout -> (numpy type, channels, which RGBA channels they fill, scale to 0..1); float layouts are clipped
PLAIN = {
    "RGBA8": ("u1", 4, (0, 1, 2, 3), 255), "BGRA8": ("u1", 4, (2, 1, 0, 3), 255), "BGRX8": ("u1", 4, (2, 1, 0), 255),
    "R8": ("u1", 1, (0,), 255), "RG8": ("u1", 2, (0, 1), 255), "R16": ("<u2", 1, (0,), 65535),
    "RG16": ("<u2", 2, (0, 1), 65535), "R16F": ("<f2", 1, (0,), 1), "RG16F": ("<f2", 2, (0, 1), 1),
    "R32F": ("<f4", 1, (0,), 1), "RGBA16F": ("<f2", 4, (0, 1, 2, 3), 1), "RGBA32F": ("<f4", 4, (0, 1, 2, 3), 1),
    "RGBA16": ("<u2", 4, (0, 1, 2, 3), 65535), "RG32F": ("<f4", 2, (0, 1), 1), "A8": ("u1", 1, (3,), 255),
    "RG8S": ("i1", 2, (0, 1), 127),
}
# fmt: on
BLOCKS = {"DXT1": 8, "DXT3": 16, "DXT5": 16, "ATI1": 8, "BC4U": 8, "ATI2": 16, "BC5U": 16, "BC6H": 16, "BC7": 16}
# the legacy header's FourCC fields that hold a D3DFORMAT number instead of letters (2070's wind.dds: 114)
_LEGACY = {b"BC4U": "ATI1", b"BC5U": "ATI2", bytes([36, 0, 0, 0]): "RGBA16", bytes([111, 0, 0, 0]): "R16F",
           bytes([112, 0, 0, 0]): "RG16F", bytes([113, 0, 0, 0]): "RGBA16F", bytes([114, 0, 0, 0]): "R32F",
           bytes([115, 0, 0, 0]): "RG32F", bytes([116, 0, 0, 0]): "RGBA32F"}  # fmt: skip


@dataclass(frozen=True)
class DdsInfo:
    """What a DDS holds: its size, mip levels, faces (6 for a cube), the texel format, whether it is sRGB and a
    volume's depth (its slices: ``level`` gives the first)."""

    width: int
    height: int
    mips: int
    faces: int
    format: str
    srgb: bool = False
    depth: int = 1


class DdsFile:
    """A DDS read level by level: ``info`` from the header, ``level(mip, face)`` as ``(height, width, 4)`` uint8
    RGBA, the stored values (a volume's first slice). Raises ``UnsupportedDds`` for a format it does not read."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 128 or data[:4] != b"DDS ":
            raise UnsupportedDds("not a DDS")
        self.data = data
        flags, height, width = struct.unpack_from("<III", data, 8)
        mips = struct.unpack_from("<I", data, 28)[0] if flags & DDSD_MIPMAPCOUNT else 1
        pf_flags, fourcc = struct.unpack_from("<I4s", data, 80)
        self._pf = (pf_flags, struct.unpack_from("<5I", data, 88))
        caps2 = struct.unpack_from("<I", data, 112)[0]
        if not width or not height:
            raise UnsupportedDds("malformed header")
        self.depth = max(1, struct.unpack_from("<I", data, 24)[0]) if caps2 & CAPS2_VOLUME else 1
        faces, srgb, start, self.signed = (6 if caps2 & CAPS2_CUBE else 1), False, 128, False
        if pf_flags & DDPF_FOURCC and fourcc == b"DX10":
            if len(data) < 148:
                raise UnsupportedDds("the DX10 header is cut short")
            code, _dim, misc, array = struct.unpack_from("<IIII", data, 128)
            if code not in DXGI:
                raise UnsupportedDds(f"DXGI format {code}")
            kind, srgb = DXGI[code]
            self.signed = code == DXGI_BC6H_SF16
            faces, start = max(1, array) * (6 if misc & DX10_CUBE else 1), 148
        elif pf_flags & DDPF_FOURCC:
            kind = _LEGACY.get(fourcc, fourcc.decode("latin-1"))
            if kind not in BLOCKS and kind not in PLAIN:
                raise UnsupportedDds(f"format {fourcc!r}")
        else:
            kind = f"{self._pf[1][0]}-bit"
        self.kind, self._start = kind, start
        self.info = DdsInfo(width, height, max(1, mips), faces, kind, srgb, self.depth)
        self._offsets = []
        at = start
        for _face in range(faces):
            for mip in range(self.info.mips):
                self._offsets.append(at)
                at += self._size(*self.size_of(mip)) * self.slices(mip)

    def slices(self, mip: int) -> int:
        """A volume's slices at a mip level (1 for a flat texture): each level halves the depth too."""
        return max(1, self.depth >> mip)

    def size_of(self, mip: int) -> tuple[int, int]:
        """(width, height) of a mip level."""
        return max(1, self.info.width >> mip), max(1, self.info.height >> mip)

    def _size(self, width: int, height: int) -> int:
        if self.kind in BLOCKS:
            return -(-width // 4) * -(-height // 4) * BLOCKS[self.kind]
        if self.kind.endswith("-bit"):
            return width * height * (self._pf[1][0] // 8)
        dtype, channels, _to, _scale = PLAIN[self.kind]
        return width * height * channels * np.dtype(dtype).itemsize

    def cut_short(self) -> str:
        """Which level the file ends inside ("" when every level of every face is there)."""
        for face in range(self.info.faces):
            for mip in range(self.info.mips):
                end = self._offsets[face * self.info.mips + mip] + self._size(*self.size_of(mip)) * self.slices(mip)
                if end > len(self.data):
                    return f"level {mip} of face {face} is cut short ({len(self.data)} of {end} bytes)"
        return ""

    def stored(self, mip: int = 0, face: int = 0, z: int = 0) -> bytes:
        """One mip level of one face (of a volume: its slice ``z``) as it is stored (compressed blocks as they are),
        for a GPU that reads them."""
        offset = self._offset(mip, face, z)
        return self.data[offset : offset + self._size(*self.size_of(mip))]

    def _offset(self, mip: int, face: int, z: int) -> int:
        """Where slice ``z`` of a level starts (a volume's slices follow each other, each a flat level)."""
        if not 0 <= z < self.slices(mip):
            raise IndexError(f"no slice {z} of level {mip}")
        return self._offsets[face * self.info.mips + mip] + z * self._size(*self.size_of(mip))

    def level(self, mip: int = 0, face: int = 0, z: int = 0) -> np.ndarray:
        """One mip level of one face (of a volume: its slice ``z``) as ``(height, width, 4)`` uint8 RGBA."""
        if not (0 <= mip < self.info.mips and 0 <= face < self.info.faces):
            raise IndexError(f"no level {mip} of face {face}")
        width, height = self.size_of(mip)
        offset = self._offset(mip, face, z)
        if len(self.data) < offset + self._size(width, height):
            raise UnsupportedDds("the file is shorter than its header says")
        if self.kind == "BC7":
            return decode_bc7(self.data, width, height, offset)
        if self.kind == "BC6H":
            return to_bytes(decode_bc6h(self.data, width, height, offset, self.signed))
        if self.kind in BLOCKS:
            return _compressed(self.kind.encode("latin-1"), self.data, width, height, offset)
        if self.kind.endswith("-bit"):
            return _uncompressed(self.data, width, height, self._pf[0], self._pf[1], offset)
        return _plain(self.kind, self.data, offset, width, height)

    @property
    def hdr(self) -> bool:
        """Whether the texels are floats that may pass 1 (BC6H, the half and float layouts)."""
        return self.kind == "BC6H" or (self.kind in PLAIN and PLAIN[self.kind][0] in ("<f2", "<f4"))

    def level_float(self, mip: int = 0, face: int = 0, z: int = 0) -> np.ndarray:
        """One level (of a volume: its slice ``z``) as ``(height, width, 4)`` float32 RGBA: a float layout's own
        values (alpha 1 where it has none), anything else as its bytes over 255; BC6H as its blocks hold it (past 1,
        below 0 when signed)."""
        if self.kind == "BC6H":
            width, height = self.size_of(mip)
            rgb = decode_bc6h(self.data, width, height, self._offset(mip, face, z), self.signed)
            return np.concatenate([rgb, np.ones((height, width, 1), np.float32)], axis=2)
        if self.kind not in PLAIN or PLAIN[self.kind][0] not in ("<f2", "<f4"):
            return self.level(mip, face, z).astype(np.float32) / 255.0
        width, height = self.size_of(mip)
        dtype, channels, to, _scale = PLAIN[self.kind]
        offset = self._offset(mip, face, z)
        raw = np.frombuffer(self.data, dtype, width * height * channels, offset).reshape(height, width, channels)
        out = np.zeros((height, width, 4), np.float32)
        out[..., 3] = 1.0
        for k, channel in enumerate(to):
            out[..., channel] = np.nan_to_num(raw[..., k].astype(np.float32))
        return out


def to_bytes(rgb: np.ndarray) -> np.ndarray:
    """``(height, width, 3)`` float RGB as 8-bit RGBA: clipped to 0..1 and rounded, alpha 255."""
    out = np.empty(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.floor(np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5)
    out[..., 3] = 255
    return out


def _plain(kind: str, data: bytes, offset: int, width: int, height: int) -> np.ndarray:
    """An uncompressed layout of ``PLAIN`` as RGBA (missing colour channels 0, a missing alpha 255)."""
    dtype, channels, to, scale = PLAIN[kind]
    raw = np.frombuffer(data, dtype, width * height * channels, offset).reshape(height, width, channels)
    values = np.nan_to_num(raw.astype(np.float32) / scale)
    if dtype == "i1":  # a signed layout (-1..1, 117's clump map) shown around the middle grey
        values = (values + 1.0) / 2.0
    values = np.clip(values, 0.0, 1.0) * 255.0 + 0.5
    out = np.zeros((height, width, 4), np.uint8)
    out[..., 3] = 255
    for k, channel in enumerate(to):
        out[..., channel] = values[..., k].astype(np.uint8) if dtype != "u1" else raw[..., k]
    return out
