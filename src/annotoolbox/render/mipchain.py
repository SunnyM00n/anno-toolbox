"""A map's mip chain completed to the full chain a mipmapped QRhi texture has: QRhi makes every level down to one
texel and has no way to stop the sampler at the last level a file stores, so the levels a file leaves out must be
written, or the sampler reads what the memory held (island-1800-2205-view.md 19, "The coloured tips").

``full_levels(info, stored)`` is the entry for a file's levels, ``full_array_levels`` for decoded arrays and
volumes. A missing level is the last stored one as Direct3D samples it there (its LOD clamped), at the new texels'
centres: plain texels and BC1 to BC5 bilinearly (the blocks encoded again, ``encode_blocks``), BC6H and BC7 (and a
last level larger than ``ENCODE_MOST``) as the stored block under each new block's centre.
"""

from __future__ import annotations

import numpy as np

from ..models import ddsdecode

ENCODED = ("DXT1", "DXT3", "DXT5", "ATI1", "ATI2")  # the formats this module decodes and encodes again
ENCODE_MOST = 256  # a larger last level is copied by block too: decoding it would hold the window's thread [ours]
_TO5, _TO6 = 31 / 255, 63 / 255


def chain_length(*sides: int) -> int:
    """The levels a mipmapped texture of these sides has (QRhi's and Direct3D's: down to one texel)."""
    return max(1, *(int(side) for side in sides)).bit_length()


def level_size(width_px: int, height_px: int, level: int) -> tuple[int, int]:
    return max(1, width_px >> level), max(1, height_px >> level)


def full_levels(dds_info, stored: list[bytes]) -> list[bytes]:
    """``stored`` (the levels as the file holds them, ``info``: its ``SDdsInfo``) and the levels past them down to
    1 x 1; as they are where nothing is missing or the file has one level (no mipmapped texture is made of it)."""
    total = chain_length(dds_info.width_px, dds_info.height_px)
    if dds_info.mips <= 1:
        return list(stored)
    if dds_info.format in ddsdecode.PLAIN:
        return plain_levels(stored, (dds_info.width_px, dds_info.height_px), *ddsdecode.PLAIN[dds_info.format][:2])
    if len(stored) >= total or dds_info.format not in ddsdecode.BLOCKS:
        return list(stored)
    last = len(stored) - 1
    size = level_size(dds_info.width_px, dds_info.height_px, last)
    out = list(stored)
    if dds_info.format in ENCODED and max(size) <= ENCODE_MOST:
        texels = ddsdecode._compressed(dds_info.format.encode("latin-1"), bytes(stored[last]), *size, 0).astype(
            np.float32
        )
        for level in range(last + 1, total):
            out.append(
                encode_blocks(
                    dds_info.format, sample_level(texels, level_size(dds_info.width_px, dds_info.height_px, level))
                )
            )
    else:
        blocks = np.frombuffer(bytes(stored[last]), np.uint8).reshape(-1, ddsdecode.BLOCKS[dds_info.format])
        for level in range(last + 1, total):
            out.append(copy_blocks(blocks, size, level_size(dds_info.width_px, dds_info.height_px, level)))
    return out


def _cast(values: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """Sampled floats as texels of ``dtype``: integers rounded and kept in their range."""
    if dtype.kind == "f":
        return values.astype(dtype)
    limits = np.iinfo(dtype)
    return np.clip(np.rint(values), limits.min, limits.max).astype(dtype)


def plain_levels(stored: list[bytes], size_px: tuple[int, int], dtype: str = "u1", channels: int = 4) -> list[bytes]:
    """A plain map's ``stored`` levels (``channels`` values of ``dtype`` a texel, level 0 of ``size_px``) and the
    levels past them down to 1 x 1; as they are where nothing is missing."""
    out, total, kind = list(stored), chain_length(*size_px), np.dtype(dtype)
    if len(out) >= total:
        return out
    level_width, level_height = level_size(*size_px, len(out) - 1)
    last = np.frombuffer(bytes(out[-1]), kind).reshape(level_height, level_width, channels).astype(np.float32)
    for level in range(len(out), total):
        out.append(_cast(bilinear(last, level_size(*size_px, level)), kind).tobytes())
    return out


def full_array_levels(levels: list[np.ndarray], volume: bool = False) -> list[np.ndarray]:
    """Decoded ``levels`` ((height, width[, channels]) arrays; with ``volume`` (depth, height, width[, channels]),
    each level half as deep too) and the levels past them down to one texel: the last given one sampled at the new
    texels' centres, bilinearly in a slice and linearly between slices."""
    first, last = levels[0], levels[-1]
    sides = first.shape[:3] if volume else (1, *first.shape[:2])
    out = list(levels)
    if len(out) >= chain_length(*sides):
        return out
    bare = last.ndim == (3 if volume else 2)  # no channel axis
    source = (last if volume else last[None]).astype(np.float32)
    source = source[..., None] if bare else source
    for level in range(len(out), chain_length(*sides)):
        depth, level_height, level_width = (max(1, side >> level) for side in sides)
        slices = np.stack([bilinear(slice_texels, (level_width, level_height)) for slice_texels in source])
        z_low, z_high, z_fraction = _centres(depth, len(slices))
        got = slices[z_low] * (1 - z_fraction)[:, None, None, None] + slices[z_high] * z_fraction[:, None, None, None]
        got = _cast(got[..., 0] if bare else got, last.dtype)
        out.append(got if volume else got[0])
    return out


def sample_level(texels: np.ndarray, size_px: tuple[int, int]) -> np.ndarray:
    """``texels`` ([h, w, c] float) sampled bilinearly at the centres of a level of ``size_px`` (w, h) texels, clamped
    at the edges, as a sampler held at that level reads it: [h', w', c] uint8."""
    return np.clip(np.round(bilinear(texels, size_px)), 0, 255).astype(np.uint8)


def bilinear(texels: np.ndarray, size_px: tuple[int, int]) -> np.ndarray:
    """``sample_level`` kept as floats (a half-float map's missing levels)."""
    source_height, source_width = texels.shape[:2]
    x_low, x_high, x_fraction = _centres(size_px[0], source_width)
    y_low, y_high, y_fraction = _centres(size_px[1], source_height)
    top = (
        texels[y_low][:, x_low] * (1 - x_fraction)[None, :, None] + texels[y_low][:, x_high] * x_fraction[None, :, None]
    )
    bottom = (
        texels[y_high][:, x_low] * (1 - x_fraction)[None, :, None]
        + texels[y_high][:, x_high] * x_fraction[None, :, None]
    )
    return top * (1 - y_fraction)[:, None, None] + bottom * y_fraction[:, None, None]


def _centres(n_out: int, n_in: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Along one axis, where ``n_out`` texel centres lie among ``n_in``: the texel below, the one above (clamped at
    the edges) and the share of the one above."""
    positions = (np.arange(n_out) + 0.5) * (n_in / n_out) - 0.5
    low = np.floor(positions)
    frac = (positions - low).astype(np.float32)
    low = low.astype(np.int64)
    return np.clip(low, 0, n_in - 1), np.clip(low + 1, 0, n_in - 1), frac


def to_blocks(image: np.ndarray) -> np.ndarray:
    """An [h, w, c] image as [n, 16, c] 4 x 4 blocks in block order, the last row and column repeated to fill them."""
    height, width = image.shape[:2]
    block_rows, block_columns = -(-height // 4), -(-width // 4)
    padded = np.pad(image, ((0, block_rows * 4 - height), (0, block_columns * 4 - width), (0, 0)), mode="edge")
    return (
        padded.reshape(block_rows, 4, block_columns, 4, -1)
        .transpose(0, 2, 1, 3, 4)
        .reshape(block_rows * block_columns, 16, -1)
    )


def encode_blocks(block_format: str, image: np.ndarray) -> bytes:
    """``image`` ([h, w, 4] uint8 RGBA) in ``block_format`` (one of ``ENCODED``)."""
    blocks = to_blocks(image).astype(np.int32)
    if block_format == "DXT1":
        return encode_bc1(blocks, punch=True).tobytes()
    if block_format == "ATI1":
        return encode_bc4(blocks[:, :, 0]).tobytes()
    if block_format == "ATI2":
        return np.concatenate([encode_bc4(blocks[:, :, 0]), encode_bc4(blocks[:, :, 1])], axis=1).tobytes()
    colour = encode_bc1(blocks, punch=False)
    if block_format == "DXT5":
        alpha = encode_bc4(blocks[:, :, 3])
    else:  # DXT3: explicit 4-bit alpha, two texels a byte, the low nibble first
        nibbles = np.clip((blocks[:, :, 3] + 8) // 17, 0, 15).astype(np.uint8)
        alpha = nibbles[:, 0::2] | (nibbles[:, 1::2] << 4)
    return np.concatenate([alpha, colour], axis=1).tobytes()


def _word565(rgb: np.ndarray) -> np.ndarray:
    red = np.round(rgb[..., 0] * _TO5).astype(np.int32)
    green = np.round(rgb[..., 1] * _TO6).astype(np.int32)
    blue = np.round(rgb[..., 2] * _TO5).astype(np.int32)
    return (red << 11) | (green << 5) | blue


def encode_bc1(blocks: np.ndarray, punch: bool) -> np.ndarray:
    """[n, 16, 4] RGBA blocks as BC1 colour blocks ([n, 8] uint8): the box of the block's colours as the endpoints,
    each texel the nearest of the palette ``ddsdecode`` reads back; with ``punch`` a block with a texel under half
    alpha in the three-colour mode, that texel transparent (BC2 and BC3 read the four-colour mode only)."""
    block_count = len(blocks)
    high, low = _word565(blocks[:, :, :3].max(axis=1)), _word565(blocks[:, :, :3].min(axis=1))
    clear = blocks[:, :, 3] < 128 if punch else np.zeros((block_count, 16), bool)
    three = clear.any(axis=1)
    colour_0 = np.where(three, np.minimum(high, low), np.maximum(high, low))
    colour_1 = np.where(three, np.maximum(high, low), np.minimum(high, low))
    endpoint_0, endpoint_1 = ddsdecode.expand565(colour_0), ddsdecode.expand565(colour_1)
    four = colour_0 > colour_1 if punch else np.ones(block_count, bool)
    palette = np.stack([endpoint_0, endpoint_1, np.where(four[:, None], (2 * endpoint_0 + endpoint_1) // 3, (endpoint_0 + endpoint_1) // 2),
                        np.where(four[:, None], (endpoint_0 + 2 * endpoint_1) // 3, 0)], axis=1)  # fmt: skip
    distance = ((blocks[:, :, None, :3] - palette[:, None, :, :]) ** 2).sum(axis=-1)
    distance[:, :, 3] = np.where(four[:, None], distance[:, :, 3], 1 << 30)  # index 3: black-transparent there
    index = np.where(clear, 3, distance.argmin(axis=-1)).astype(np.uint32)
    bits = (index << (2 * np.arange(16, dtype=np.uint32))[None, :]).sum(axis=1, dtype=np.uint32)
    out = np.zeros((block_count, 8), np.uint8)
    out[:, 0:2] = np.stack([colour_0 & 255, colour_0 >> 8], axis=1)
    out[:, 2:4] = np.stack([colour_1 & 255, colour_1 >> 8], axis=1)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(block_count, 4)
    return out


def encode_bc4(values: np.ndarray) -> np.ndarray:
    """[n, 16] values (0..255) as BC4 blocks ([n, 8] uint8), the eight-value mode between the block's largest and
    smallest (one value: every index 0)."""
    block_count = len(values)
    alpha_0, alpha_1 = values.max(axis=1), values.min(axis=1)
    palette = np.zeros((block_count, 8), np.int32)
    palette[:, 0], palette[:, 1] = alpha_0, alpha_1
    for i in range(1, 7):
        palette[:, i + 1] = ((7 - i) * alpha_0 + i * alpha_1) // 7
    index = np.abs(values[:, :, None] - palette[:, None, :]).argmin(axis=-1).astype(np.uint64)
    index[alpha_0 == alpha_1] = 0
    bits = (index << (3 * np.arange(16, dtype=np.uint64))[None, :]).sum(axis=1, dtype=np.uint64)
    out = np.zeros((block_count, 8), np.uint8)
    out[:, 0], out[:, 1] = alpha_0, alpha_1
    out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(block_count, 8)[:, :6]
    return out


def copy_blocks(blocks: np.ndarray, size_px: tuple[int, int], new: tuple[int, int]) -> bytes:
    """A level of ``new`` (w, h) texels made of the stored blocks (``blocks`` of a level of ``size_px``) under each new
    block's centre: the formats this module does not encode (BC6H, BC7)."""
    old_block_columns, old_block_rows = -(-size_px[0] // 4), -(-size_px[1] // 4)
    new_block_columns, new_block_rows = -(-new[0] // 4), -(-new[1] // 4)

    def under(count_new: int, texels_new: int, texels: int, count: int) -> np.ndarray:
        centre = (np.arange(count_new) * 4 + 2) * texels / max(1, texels_new)
        return np.clip((centre // 4).astype(np.int64), 0, count - 1)

    rows, columns = (
        under(new_block_rows, new[1], size_px[1], old_block_rows),
        under(new_block_columns, new[0], size_px[0], old_block_columns),
    )
    grid = blocks.reshape(old_block_rows, old_block_columns, -1)
    return np.ascontiguousarray(grid[rows][:, columns]).tobytes()
