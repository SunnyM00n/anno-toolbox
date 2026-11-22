"""A BC7 decoder in numpy: blocks of 16 bytes into RGBA, every mode, partition and rotation of the format, so a DDS in
BC7 (117's and 1800's textures) reads without an extra package.

``decode_bc7(data, width, height)`` is the entry; it decodes whole blocks (the mode in the lowest set bit, then the
partition, the endpoints with their shared or unique bits, the indices with their anchor texels) a chunk at a time,
all blocks of one mode together. The format: the public BC7 specification (Khronos Data Format, "BC7"); checked
byte for byte against another decoder on real files (``tests/models/test_bc7decode.py``).
"""

from __future__ import annotations

import numpy as np

# fmt: off
# mode -> subsets, partition bits, rotation bits, index selection bit, colour bits, alpha bits, endpoint p-bits,
# shared p-bits, index bits, second index bits
MODES = {
    0: (3, 4, 0, 0, 4, 0, 1, 0, 3, 0), 1: (2, 6, 0, 0, 6, 0, 0, 1, 3, 0), 2: (3, 6, 0, 0, 5, 0, 0, 0, 2, 0),
    3: (2, 6, 0, 0, 7, 0, 1, 0, 2, 0), 4: (1, 0, 2, 1, 5, 6, 0, 0, 2, 3), 5: (1, 0, 2, 0, 7, 8, 0, 0, 2, 2),
    6: (1, 0, 0, 0, 7, 7, 1, 0, 4, 0), 7: (2, 6, 0, 0, 5, 5, 1, 0, 2, 0),
}
WEIGHTS = {
    2: (0, 21, 43, 64),
    3: (0, 9, 18, 27, 37, 46, 55, 64),
    4: (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64),
}
PARTITIONS2 = (
    "0011001100110011", "0001000100010001", "0111011101110111", "0001001100110111", "0000000100010011",
    "0011011101111111", "0001001101111111", "0000000100110111", "0000000000010011", "0011011111111111",
    "0000000101111111", "0000000000010111", "0001011111111111", "0000000011111111", "0000111111111111",
    "0000000000001111", "0000100011101111", "0111000100000000", "0000000010001110", "0111001100010000",
    "0011000100000000", "0000100011001110", "0000000010001100", "0111001100110001", "0011000100010000",
    "0000100010001100", "0110011001100110", "0011011001101100", "0001011111101000", "0000111111110000",
    "0111000110001110", "0011100110011100", "0101010101010101", "0000111100001111", "0101101001011010",
    "0011001111001100", "0011110000111100", "0101010110101010", "0110100101101001", "0101101010100101",
    "0111001111001110", "0001001111001000", "0011001001001100", "0011101111011100", "0110100110010110",
    "0011110011000011", "0110011010011001", "0000011001100000", "0100111001000000", "0010011100100000",
    "0000001001110010", "0000010011100100", "0110110010010011", "0011011011001001", "0110001110011100",
    "0011100111000110", "0110110011001001", "0110001100111001", "0111111010000001", "0001100011100111",
    "0000111100110011", "0011001111110000", "0010001011101110", "0100010001110111",
)
PARTITIONS3 = (
    "0011001102212222", "0001001122112221", "0000200122112211", "0222002200110111", "0000000011221122",
    "0011001100220022", "0022002211111111", "0011001122112211", "0000000011112222", "0000111111112222",
    "0000111122222222", "0012001200120012", "0112011201120112", "0122012201220122", "0011011211221222",
    "0011200122002220", "0001001101121122", "0111001120012200", "0000112211221122", "0022002200221111",
    "0111011102220222", "0001000122212221", "0000001101220122", "0000110022102210", "0122012200110000",
    "0012001211222222", "0110122112210110", "0000011012211221", "0022110211020022", "0110011020022222",
    "0011012201220011", "0000200022112221", "0000000211221222", "0222002200120011", "0011001200220222",
    "0120012001200120", "0000111122220000", "0120120120120120", "0120201212010120", "0011220011220011",
    "0011112222000011", "0101010122222222", "0000000021212121", "0022112200221122", "0022001100220011",
    "0220122102201221", "0101222222220101", "0000212121212121", "0101010101012222", "0222011102220111",
    "0002111200021112", "0000211221122112", "0222011101110222", "0002111211120002", "0110011001102222",
    "0000000021122112", "0110011022222222", "0022001100110022", "0022112211220022", "0000000000002112",
    "0002000100020001", "0222122202221222", "0101222222222222", "0111201122012220",
)
ANCHOR2 = (
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 2, 8, 2, 2, 8, 8, 15, 2, 8, 2, 2, 8, 8, 2, 2,
    15, 15, 6, 8, 2, 8, 15, 15, 2, 8, 2, 2, 2, 15, 15, 6, 6, 2, 6, 8, 15, 15, 2, 2, 15, 15, 15, 15, 15, 2, 2, 15,
)
ANCHOR3A = (
    3, 3, 15, 15, 8, 3, 15, 15, 8, 8, 6, 6, 6, 5, 3, 3, 3, 3, 8, 15, 3, 3, 6, 10, 5, 8, 8, 6, 8, 5, 15, 15,
    8, 15, 3, 5, 6, 10, 8, 15, 15, 3, 15, 5, 15, 15, 15, 15, 3, 15, 5, 5, 5, 8, 5, 10, 5, 10, 8, 13, 15, 12, 3, 3,
)
ANCHOR3B = (
    15, 8, 8, 3, 15, 15, 3, 8, 15, 15, 15, 15, 15, 15, 15, 8, 15, 8, 15, 3, 15, 8, 15, 8, 3, 15, 6, 10, 15, 15, 10, 8,
    15, 3, 15, 10, 10, 8, 9, 10, 6, 15, 8, 15, 3, 6, 6, 8, 15, 3, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 3, 15, 15, 8,
)
# fmt: on
CHUNK = 1 << 14  # blocks decoded at once: 16 MB of bits


def _table(rows) -> np.ndarray:
    return np.array([[int(digit) for digit in row] for row in rows], np.int64)


_SUBSETS = {1: np.zeros((1, 16), np.int64), 2: _table(PARTITIONS2), 3: _table(PARTITIONS3)}
_TEXEL = np.arange(16)
_ANCHORS = {
    1: np.zeros((1, 16), bool),
    2: (_TEXEL[None] == 0) | (_TEXEL[None] == np.array(ANCHOR2)[:, None]),
    3: (_TEXEL[None] == 0)
    | (_TEXEL[None] == np.array(ANCHOR3A)[:, None])
    | (_TEXEL[None] == np.array(ANCHOR3B)[:, None]),
}
_ANCHORS[1][0, 0] = True


def decode_bc7(data: bytes, width_px: int, height_px: int, offset: int = 0) -> np.ndarray:
    """``(height, width, 4)`` uint8 RGBA of the BC7 blocks at ``offset`` of ``data`` (rows of blocks, the edges cut
    off)."""
    block_columns, block_rows = -(-width_px // 4), -(-height_px // 4)
    blocks = np.frombuffer(data, np.uint8, block_columns * block_rows * 16, offset).reshape(-1, 16)
    texels = np.empty((len(blocks), 16, 4), np.uint8)
    for start in range(0, len(blocks), CHUNK):
        texels[start : start + CHUNK] = decode_blocks(blocks[start : start + CHUNK])
    image = (
        texels.reshape(block_rows, block_columns, 4, 4, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(block_rows * 4, block_columns * 4, 4)
    )
    return np.ascontiguousarray(image[:height_px, :width_px])


def decode_blocks(blocks: np.ndarray) -> np.ndarray:
    """``(n, 16)`` uint8 blocks as ``(n, 16, 4)`` RGBA texels in row order; a block of no mode (all eight mode bits
    clear) is transparent black, as the specification says."""
    bits = np.unpackbits(blocks, axis=1, bitorder="little").astype(np.int64)
    head = bits[:, :8]
    mode = np.where(head.any(axis=1), head.argmax(axis=1), 8)
    out = np.zeros((len(blocks), 16, 4), np.uint8)
    for mode_spec in MODES:
        chosen = mode == mode_spec
        if chosen.any():
            out[chosen] = _mode(mode_spec, bits[chosen])
    return out


def _field(bits: np.ndarray, bit_offset: int, count: int) -> np.ndarray:
    """An unsigned field of ``count`` bits at bit ``at`` of every block (least significant bit first)."""
    if not count:
        return np.zeros(len(bits), np.int64)
    return bits[:, bit_offset : bit_offset + count] @ (1 << np.arange(count, dtype=np.int64))


def _indices(bits: np.ndarray, bit_offset: int, size_bits: int, anchors: np.ndarray) -> tuple[np.ndarray, int]:
    """Sixteen indices of ``size_bits`` bits each from ``bit_offset``, an anchor texel's one bit shorter; (indices, next bit)."""
    widths = size_bits - anchors.astype(np.int64)
    starts = bit_offset + np.cumsum(widths, axis=1) - widths
    k = np.arange(size_bits)
    where = np.clip(starts[..., None] + k, 0, 127)
    taken = bits[np.arange(len(bits))[:, None, None], where]
    taken = np.where(k < widths[..., None], taken, 0)
    return taken @ (1 << k), bit_offset + 16 * size_bits - int(anchors[0].sum())


def _unquantize(values: np.ndarray, bits: int) -> np.ndarray:
    return (values << (8 - bits)) | (values >> (2 * bits - 8)) if bits < 8 else values


def _endpoints(bits: np.ndarray, bit_offset: int, spec: tuple) -> tuple[np.ndarray, int]:
    """The endpoints [n, 2 x subsets, 4] expanded to 8 bits (a missing alpha 255); (endpoints, next bit)."""
    subset_count, _pb, _rb, _isb, colour_bits, alpha_bits, endpoint_pbits, shared_pbits, _ib, _ib2 = spec
    count, block_count = 2 * subset_count, len(bits)
    ends = np.zeros((block_count, count, 4), np.int64)
    for channel in range(4 if alpha_bits else 3):
        size = alpha_bits if channel == 3 else colour_bits
        for endpoint_index in range(count):
            ends[:, endpoint_index, channel] = _field(bits, bit_offset, size)
            bit_offset += size
    if endpoint_pbits or shared_pbits:
        endpoints = np.zeros((block_count, count), np.int64)
        for endpoint_index in range(count if endpoint_pbits else subset_count):
            got = _field(bits, bit_offset, 1)
            bit_offset += 1
            if endpoint_pbits:
                endpoints[:, endpoint_index] = got
            else:
                endpoints[:, 2 * endpoint_index], endpoints[:, 2 * endpoint_index + 1] = got, got
        ends = (ends << 1) | endpoints[..., None]
        colour_bits, alpha_bits = colour_bits + 1, alpha_bits + 1 if alpha_bits else 0
    ends[..., :3] = _unquantize(ends[..., :3], colour_bits)
    ends[..., 3] = _unquantize(ends[..., 3], alpha_bits) if alpha_bits else 255
    return ends, bit_offset


def _blend(endpoint_start: np.ndarray, endpoint_end: np.ndarray, index: np.ndarray, size_bits: int) -> np.ndarray:
    weight = np.asarray(WEIGHTS[size_bits], np.int64)[index]
    return ((64 - weight) * endpoint_start + weight * endpoint_end + 32) >> 6


def _mode(mode: int, bits: np.ndarray) -> np.ndarray:
    spec = MODES[mode]
    (
        subset_count,
        partition_bits,
        rotation_bits,
        index_selection_bits,
        _cb,
        alpha_bits,
        _epb,
        _spb,
        index_bits,
        second_index_bits,
    ) = spec
    bit_offset = mode + 1
    partition = _field(bits, bit_offset, partition_bits)
    rotation = _field(bits, bit_offset + partition_bits, rotation_bits)
    select = _field(bits, bit_offset + partition_bits + rotation_bits, index_selection_bits)
    ends, bit_offset = _endpoints(bits, bit_offset + partition_bits + rotation_bits + index_selection_bits, spec)
    subset = _SUBSETS[subset_count][partition] if subset_count > 1 else np.zeros((len(bits), 16), np.int64)
    anchors = _ANCHORS[subset_count][partition] if subset_count > 1 else np.repeat(_ANCHORS[1], len(bits), axis=0)
    first, bit_offset = _indices(bits, bit_offset, index_bits, anchors)
    rows = np.arange(len(bits))[:, None]
    endpoint_start, endpoint_end = ends[rows, 2 * subset], ends[rows, 2 * subset + 1]
    if not second_index_bits:
        texels = _blend(endpoint_start, endpoint_end, first[..., None], index_bits)
        if not alpha_bits:
            texels[..., 3] = 255
    else:  # modes 4 and 5: colour and alpha by index sets of their own; mode 4's selection bit swaps the two
        second, _end = _indices(bits, bit_offset, second_index_bits, np.repeat(_ANCHORS[1], len(bits), axis=0))
        swap = select == 1
        colour = np.where(
            swap[:, None, None],
            _blend(endpoint_start[..., :3], endpoint_end[..., :3], second[..., None], second_index_bits),
            _blend(endpoint_start[..., :3], endpoint_end[..., :3], first[..., None], index_bits),
        )
        alpha = np.where(
            swap[:, None],
            _blend(endpoint_start[..., 3], endpoint_end[..., 3], first, index_bits),
            _blend(endpoint_start[..., 3], endpoint_end[..., 3], second, second_index_bits),
        )
        texels = np.concatenate([colour, alpha[..., None]], axis=2)
    if rotation_bits:
        for swap_channel in (1, 2, 3):  # 1 swaps alpha and red, 2 green, 3 blue
            hit = rotation == swap_channel
            if hit.any():
                texels[hit] = texels[hit][..., _swap(swap_channel - 1)]
    return texels.astype(np.uint8)


def _swap(channel: int) -> list[int]:
    order = [0, 1, 2, 3]
    order[channel], order[3] = 3, channel
    return order
