"""A BC6H decoder in numpy: blocks of 16 bytes into half-float RGB, all fourteen modes, unsigned and signed, so an HDR
texture (117's sky and light maps) reads without an extra package and with its values unclipped.

``decode_bc6h(data, width, height, offset, signed)`` is the entry, ``(height, width, 3)`` float32. A block holds a
mode (2 or 5 bits), one or two subsets of two RGB endpoints (the later ones stored as signed deltas from the first in
the transformed modes) and 3- or 4-bit indices; the endpoints are unquantized to 16 bits, blended with the format's
weights and halved into half-float bits. The format: the public BC6H specification (Khronos Data Format, "BC6H");
checked byte for byte against another decoder (``tests/models/test_bc6hdecode.py``).
"""

from __future__ import annotations

import numpy as np

from .bc7decode import ANCHOR2, PARTITIONS2

# Stream order of every mode's fields, "name[high:low]" with the first stream bit the low one, a reversed range
# ("rw[10:15]") with the first stream bit the high one. rw gw bw / rx gx bx / ry gy by / rz gz bz are the four
# endpoints' channels (the 2nd subset's are y and z), d the partition, m the mode.
# fmt: off
LAYOUTS = {
    1: "m[1:0] gy[4] by[4] bz[4] rw[9:0] gw[9:0] bw[9:0] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[4:0] bz[1] "
       "by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]",
    2: "m[1:0] gy[5] gz[4] gz[5] rw[6:0] bz[0] bz[1] by[4] gw[6:0] by[5] bz[2] gy[4] bw[6:0] bz[3] bz[5] bz[4] "
       "rx[5:0] gy[3:0] gx[5:0] gz[3:0] bx[5:0] by[3:0] ry[5:0] rz[5:0] d[4:0]",
    3: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[4:0] rw[10] gy[3:0] gx[3:0] gw[10] bz[0] gz[3:0] bx[3:0] bw[10] bz[1] "
       "by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]",
    4: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10] gz[4] gy[3:0] gx[4:0] gw[10] gz[3:0] bx[3:0] bw[10] bz[1] "
       "by[3:0] ry[3:0] bz[0] bz[2] rz[3:0] gy[4] bz[3] d[4:0]",
    5: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10] by[4] gy[3:0] gx[3:0] gw[10] bz[0] gz[3:0] bx[4:0] bw[10] "
       "by[3:0] ry[3:0] bz[1] bz[2] rz[3:0] bz[4] bz[3] d[4:0]",
    6: "m[4:0] rw[8:0] by[4] gw[8:0] gy[4] bw[8:0] bz[4] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] gz[3:0] bx[4:0] bz[1] "
       "by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]",
    7: "m[4:0] rw[7:0] gz[4] by[4] gw[7:0] bz[2] gy[4] bw[7:0] bz[3] bz[4] rx[5:0] gy[3:0] gx[4:0] bz[0] gz[3:0] "
       "bx[4:0] bz[1] by[3:0] ry[5:0] rz[5:0] d[4:0]",
    8: "m[4:0] rw[7:0] bz[0] by[4] gw[7:0] gy[5] gy[4] bw[7:0] gz[5] bz[4] rx[4:0] gz[4] gy[3:0] gx[5:0] gz[3:0] "
       "bx[4:0] bz[1] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]",
    9: "m[4:0] rw[7:0] bz[1] by[4] gw[7:0] by[5] gy[4] bw[7:0] bz[5] bz[4] rx[4:0] gz[4] gy[3:0] gx[4:0] bz[0] "
       "gz[3:0] bx[5:0] by[3:0] ry[4:0] bz[2] rz[4:0] bz[3] d[4:0]",
    10: "m[4:0] rw[5:0] gz[4] bz[0] bz[1] by[4] gw[5:0] gy[5] by[5] bz[2] gy[4] bw[5:0] gz[5] bz[3] bz[5] bz[4] "
        "rx[5:0] gy[3:0] gx[5:0] gz[3:0] bx[5:0] by[3:0] ry[5:0] rz[5:0] d[4:0]",
    11: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[9:0] gx[9:0] bx[9:0]",
    12: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[8:0] rw[10] gx[8:0] gw[10] bx[8:0] bw[10]",
    13: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[7:0] rw[10:11] gx[7:0] gw[10:11] bx[7:0] bw[10:11]",
    14: "m[4:0] rw[9:0] gw[9:0] bw[9:0] rx[3:0] rw[10:15] gx[3:0] gw[10:15] bx[3:0] bw[10:15]",
}
# mode bits -> mode number; the first two modes have two mode bits, the rest five
MODE_OF = {0: 1, 1: 2, 2: 3, 6: 4, 10: 5, 14: 6, 18: 7, 22: 8, 26: 9, 30: 10, 3: 11, 7: 12, 11: 13, 15: 14}
# mode -> bits of an endpoint's base, bits of a delta (None: stored whole, not a delta) for R, G, B
PRECISION = {
    1: (10, (5, 5, 5)), 2: (7, (6, 6, 6)), 3: (11, (5, 4, 4)), 4: (11, (4, 5, 4)), 5: (11, (4, 4, 5)),
    6: (9, (5, 5, 5)), 7: (8, (6, 5, 5)), 8: (8, (5, 6, 5)), 9: (8, (5, 5, 6)), 10: (6, None),
    11: (10, None), 12: (11, (9, 9, 9)), 13: (12, (8, 8, 8)), 14: (16, (4, 4, 4)),
}
# fmt: on
WEIGHTS3 = (0, 9, 18, 27, 37, 46, 55, 64)
WEIGHTS4 = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)
CHUNK = 1 << 14  # blocks decoded at once
_TEXEL = np.arange(16)
_SUBSET = np.array([[int(c) for c in row] for row in PARTITIONS2[:32]], np.int64)
_ANCHOR = np.array(ANCHOR2[:32])
_NAMES = ("rw", "gw", "bw", "rx", "gx", "bx", "ry", "gy", "by", "rz", "gz", "bz", "d")


def _parse(layout: str) -> dict[str, list[tuple[int, int]]]:
    """A layout as {field: [(stream bit, bit of the field)]}."""
    fields: dict[str, list[tuple[int, int]]] = {}
    bit_offset = 0
    for token in layout.split():
        name, span = token[:-1].split("[")
        high, _, low = span.partition(":")
        high, low = int(high), int(low or high)
        order = range(low, high + 1) if low <= high else range(low, high - 1, -1)
        for bit in order:
            fields.setdefault(name, []).append((bit_offset, bit))
            bit_offset += 1
    return fields


PARSED = {mode: _parse(layout) for mode, layout in LAYOUTS.items()}


def decode_bc6h(data: bytes, width_px: int, height_px: int, offset: int = 0, signed: bool = False) -> np.ndarray:
    """``(height, width, 3)`` float32 RGB of the BC6H blocks at ``offset`` of ``data`` (rows of blocks, the edges cut
    off), the values as stored: they pass 1, and a signed format's go below 0."""
    block_columns, block_rows = -(-width_px // 4), -(-height_px // 4)
    blocks = np.frombuffer(data, np.uint8, block_columns * block_rows * 16, offset).reshape(-1, 16)
    texels = np.empty((len(blocks), 16, 3), np.float32)
    for start in range(0, len(blocks), CHUNK):
        texels[start : start + CHUNK] = decode_blocks(blocks[start : start + CHUNK], signed)
    image = (
        texels.reshape(block_rows, block_columns, 4, 4, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(block_rows * 4, block_columns * 4, 3)
    )
    return np.ascontiguousarray(image[:height_px, :width_px])


def decode_blocks(blocks: np.ndarray, signed: bool = False) -> np.ndarray:
    """``(n, 16)`` uint8 blocks as ``(n, 16, 3)`` float32 texels in row order; a block of a reserved mode is black."""
    bits = np.unpackbits(blocks, axis=1, bitorder="little").astype(np.int64)
    first = bits[:, 0] | bits[:, 1] << 1
    five = bits[:, :5] @ (1 << np.arange(5))
    out = np.zeros((len(blocks), 16, 3), np.float32)
    for code, mode in MODE_OF.items():
        chosen = (first == code) if mode <= 2 else (five == code)
        if chosen.any():
            out[chosen] = _mode(mode, bits[chosen], signed)
    return out


def _fields(mode: int, bits: np.ndarray) -> dict[str, np.ndarray]:
    """Every field of the layout as an unsigned value per block (a field the mode lacks is 0)."""
    values = {name: np.zeros(len(bits), np.int64) for name in _NAMES}
    for name, places in PARSED[mode].items():
        if name in values:
            for bit_offset, bit in places:
                values[name] |= bits[:, bit_offset] << bit
    return values


def _sign_extend(values: np.ndarray, count: int) -> np.ndarray:
    return (values ^ (1 << (count - 1))) - (1 << (count - 1))


def _endpoints(mode: int, bits: np.ndarray, signed: bool) -> np.ndarray:
    """The endpoints ``(n, 4, 3)`` (subset 0 start and end, subset 1 start and end) before unquantizing; a 1-subset
    block repeats its pair."""
    base, delta = PRECISION[mode]
    fields = _fields(mode, bits)
    ends = np.zeros((len(bits), 4, 3), np.int64)
    for channel_index, channel in enumerate("rgb"):
        ends[:, 0, channel_index] = fields[channel + "w"]
        for tag_index, tag in enumerate("xyz", 1):
            ends[:, tag_index, channel_index] = fields[channel + tag]
    if signed:
        ends[:, 0] = _sign_extend(ends[:, 0], base)
    if delta:
        for channel_index in range(3):
            moved = _sign_extend(ends[:, 1:, channel_index], delta[channel_index]) + ends[:, :1, channel_index]
            ends[:, 1:, channel_index] = moved & ((1 << base) - 1)
            if signed:
                ends[:, 1:, channel_index] = _sign_extend(ends[:, 1:, channel_index], base)
    elif signed:
        ends[:, 1:] = _sign_extend(ends[:, 1:], base)
    return ends


def _unquantize(values: np.ndarray, base: int, signed: bool) -> np.ndarray:
    """Endpoint values of ``base`` bits widened to 16 (15 and a sign when signed)."""
    if not signed:
        if base >= 15:
            return values
        wide = ((values << 15) + 0x4000) >> (base - 1)
        return np.where(values == 0, 0, np.where(values == (1 << base) - 1, 0xFFFF, wide))
    if base >= 16:
        return values
    mag = np.abs(values)
    wide = ((mag << 15) + 0x4000) >> (base - 1)
    wide = np.where(mag == 0, 0, np.where(mag >= (1 << (base - 1)) - 1, 0x7FFF, wide))
    return np.where(values < 0, -wide, wide)


def _indices(bits: np.ndarray, bit_offset: int, size_bits: int, anchors: np.ndarray) -> np.ndarray:
    """Sixteen indices of ``size_bits`` bits each from ``bit_offset``, an anchor texel's one bit shorter."""
    widths = size_bits - anchors.astype(np.int64)
    starts = bit_offset + np.cumsum(widths, axis=1) - widths
    k = np.arange(size_bits)
    where = np.clip(starts[..., None] + k, 0, 127)
    taken = bits[np.arange(len(bits))[:, None, None], where]
    taken = np.where(k < widths[..., None], taken, 0)
    return taken @ (1 << k)


def _to_float(values: np.ndarray, signed: bool) -> np.ndarray:
    """Blended 16-bit values as the half floats the format means (x * 31 / 64), then as float32."""
    if signed:
        mag = np.where(values < 0, -(((-values) * 31) >> 5), (values * 31) >> 5)
        half = np.where(mag < 0, (-mag) | 0x8000, mag)
    else:
        half = (values * 31) >> 6
    return half.astype(np.uint16).view(np.float16).astype(np.float32)


def _mode(mode: int, bits: np.ndarray, signed: bool) -> np.ndarray:
    base, delta = PRECISION[mode]
    two = mode <= 10
    ends = _endpoints(mode, bits, signed)
    ends = _unquantize(ends, base, signed)
    block_count = len(bits)
    if two:
        partition = _fields(mode, bits)["d"]
        subset = _SUBSET[partition]
        anchors = (_TEXEL[None] == 0) | (_TEXEL[None] == _ANCHOR[partition][:, None])
        size, weights, bit_offset = 3, WEIGHTS3, 82
    else:
        subset = np.zeros((block_count, 16), np.int64)
        anchors = np.repeat((_TEXEL == 0)[None], block_count, axis=0)
        size, weights, bit_offset = 4, WEIGHTS4, 65
    index = _indices(bits, bit_offset, size, anchors)
    rows = np.arange(block_count)[:, None]
    endpoint_start, endpoint_end = ends[rows, 2 * subset], ends[rows, 2 * subset + 1]
    weight = np.asarray(weights, np.int64)[index][..., None]
    return _to_float(((64 - weight) * endpoint_start + weight * endpoint_end + 32) >> 6, signed)
