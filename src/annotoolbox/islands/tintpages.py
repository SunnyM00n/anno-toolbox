"""The tint pages of an island (``_gamedata/<island>/tintmaps/<level>/<x>x<z>.dds``) and the tint strokes laid on them.

A page is a 512 x 512 BC1 image of ``16 << level`` tiles; a texel stores a colour as YCC (``r`` Cb, ``g`` Y, ``b`` Cr)
which the terrain shader turns into ``(2 rgb) ^ 2.2``, the factor the albedo is multiplied with. ``apply`` lays the edit
file's ``tint`` strokes (``color``: the factor, ``radius``, ``strength``, ``points``) on every page they reach, at every
level (the levels are independent images); island-editor "Tint pages" has the measurements and the rules.
"""

from __future__ import annotations

import re

import numpy as np

from ..infra.i18n import tr
from ..models import bc1encode, ddsdecode
from .islandground import stroke_cover, stroke_dabs

PAGE = re.compile(r"tintmaps/(\d+)/(\d+)x(\d+)\.dds$")
NODE_TILES = 16  # the tiles a level-0 node (and its page) covers; each level doubles it
GAMMA = 2.2  # the shader's exponent: tint = (2 rgb) ^ GAMMA
MAX_FACTOR = 4.0  # (2 * 1) ^ 2.2 is 4.59: the factors a page can hold stay below it
MIN_COVER = 1e-3  # a texel a stroke covers less than this keeps its bytes
NEUTRAL_BLOCK = bytes.fromhex("1084108400000000")  # one BC1 block, both colours grey 128: no tint (vanilla 125-132)


def neutral(raw: bytes) -> bytes:
    """A tint page or a far tint picture's blocks (``raw``: a DDS file, else bare BC1 blocks) all grey: no tint."""
    header = (148 if raw[84:88] == b"DX10" else 128) if raw[:4] == b"DDS " else 0
    return raw[:header] + NEUTRAL_BLOCK * ((len(raw) - header) // 8)


def far_picture(island_rd3d) -> np.ndarray | None:
    """An island's far tint picture (rd3d ``TintMapLevels/BaseTextureData``: ``width`` blocks across, BC1) as stored
    bytes ``(h, w, 3)``, None without one."""
    node = island_rd3d.path("Terrain/TintMapLevels/BaseTextureData")
    if node is None or node.find("map") is None:
        return None
    width, height = node.find("width").u32(), node.find("height").u32()
    blocks = np.frombuffer(node.find("map").data, np.uint8).reshape(-1, 8)
    return bc1encode.from_blocks(bc1encode.decode_blocks(blocks), height * 4, width * 4)


def land_spread(stored: np.ndarray, land: np.ndarray) -> tuple[float, float] | None:
    """The mean and spread of the stored luminance byte over the land texels of a tint picture (``land``: a mask of
    its shape), None without land."""
    y = stored[..., 1][land].astype(np.float64)
    return (float(y.mean()), float(y.std())) if y.size else None


def fit_blocks(raw: bytes, source_range: tuple[float, float], target_range: tuple[float, float]) -> bytes:
    """BC1 blocks (``raw``: a DDS file or bare blocks) whose tint keeps its pattern in another range: the luminance
    byte moved from ``src``'s mean to ``dst``'s and its spread narrowed to ``dst``'s (never widened), the chroma's
    distance from grey narrowed alike. The map is affine, so it is applied to each block's two end colours and
    every texel between them follows; a block whose end colours change order is turned back (BC1's mode)."""
    header = (148 if raw[84:88] == b"DX10" else 128) if raw[:4] == b"DDS " else 0
    blocks = np.frombuffer(raw, np.uint8, (len(raw) - header) // 8 * 8, header).reshape(-1, 8).copy()
    narrowing = min(1.0, target_range[1] / source_range[1]) if source_range[1] > 0 else 1.0
    words = blocks[:, :4].view("<u2").astype(np.int64)  # the two end colours, 565 (r Cb, g Y, b Cr)
    rgb = np.stack([(words >> 11) & 31, (words >> 5) & 63, words & 31], -1).astype(np.float64)
    rgb *= np.array([255 / 31, 255 / 63, 255 / 31])
    rgb[..., 1] = target_range[0] + (rgb[..., 1] - source_range[0]) * narrowing
    rgb[..., 0::2] = 128 + (rgb[..., 0::2] - 128) * narrowing
    quantised = np.clip(np.rint(rgb * np.array([31 / 255, 63 / 255, 31 / 255])), 0, [31, 63, 31]).astype(np.int64)
    new = (quantised[..., 0] << 11) | (quantised[..., 1] << 5) | quantised[..., 2]
    four = words[:, 0] > words[:, 1]  # the four-colour mode, as it was
    flip = four & (new[:, 0] < new[:, 1])
    new[flip] = new[flip][:, ::-1]
    bits = blocks[:, 4:].view("<u4").copy()
    bits[flip] ^= np.uint32(0x55555555)  # index 0 <-> 1 and 2 <-> 3: the same colours with the ends swapped
    same = four & (new[:, 0] == new[:, 1])
    bits[same] = 0  # one colour: the three-colour mode would read index 3 as black
    three = ~four & (new[:, 0] > new[:, 1])  # a three-colour block kept in its mode: ends swapped, 0 <-> 1 only
    new[three] = new[three][:, ::-1]
    low = ~(bits[three] >> np.uint32(1)) & np.uint32(0x55555555)
    bits[three] ^= low
    blocks[:, :4] = new.astype("<u2").view(np.uint8).reshape(-1, 4)
    blocks[:, 4:] = bits.view(np.uint8).reshape(-1, 4)
    return raw[:header] + blocks.tobytes() + raw[header + blocks.size :]


def to_rgb(stored: np.ndarray) -> np.ndarray:
    """The decoded colour (0..1, floats) of stored texels (``(h, w, 3)``, bytes r Cb, g Y, b Cr), as the shader decodes."""
    chroma_blue, y, chroma_red = (stored[..., k].astype(np.float64) / 255 for k in range(3))
    chroma_blue, chroma_red = (
        (2 * signed_value - 1) * np.abs(2 * signed_value - 1) * 0.5 + 0.5 for signed_value in (chroma_blue, chroma_red)
    )  # the signed square
    rgb = np.stack(
        [
            y + 1.402 * (chroma_red - 0.5),
            y - 0.344 * (chroma_blue - 0.5) - 0.714 * (chroma_red - 0.5),
            y + 1.772 * (chroma_blue - 0.5),
        ],
        axis=-1,
    )
    return np.maximum(rgb, 0)


def to_stored(rgb: np.ndarray) -> np.ndarray:
    """``to_rgb`` backwards: the texel bytes (``(h, w, 3)`` uint8) of decoded colours."""
    red, green, blue = (rgb[..., k] for k in range(3))
    y = 0.299 * red + 0.587 * green + 0.114 * blue
    chroma = [np.clip((blue - y) / 1.772 + 0.5, 0, 1), np.clip((red - y) / 1.402 + 0.5, 0, 1)]
    chroma_blue, chroma_red = (
        0.5 * (np.sign(centred) * np.sqrt(np.abs(centred)) + 1) for centred in (2 * channel - 1 for channel in chroma)
    )
    return np.clip(np.rint(np.stack([chroma_blue, np.clip(y, 0, 1), chroma_red], axis=-1) * 255), 0, 255).astype(
        np.uint8
    )


def valid(stroke: dict) -> bool:
    """Whether a stroke of the edit file can be laid: three factors, a radius, points."""
    try:
        color = [float(factor) for factor in stroke["color"]]
        return len(color) == 3 and float(stroke.get("radius", 4)) > 0 and bool(stroke["points"])
    except (KeyError, TypeError, ValueError):
        return False


def gain(strokes: list[dict], origin_x: float, origin_z: float, span_tiles: float, side: int) -> np.ndarray | None:
    """What the strokes multiply the decoded colour of a ``side`` x ``side`` page with (``(side, side, 3)``; page corner
    tile (``x0``, ``z0``), ``span`` tiles wide), None when none of them reaches the page."""
    total = None
    for stroke in strokes:
        moved = {**stroke, "points": [[point[0] - origin_x, point[1] - origin_z] for point in stroke["points"]]}
        cover = stroke_cover(moved, side, side / span_tiles)[..., None]
        if cover.max() < MIN_COVER:
            continue
        factor = np.clip(np.asarray([float(value) for value in stroke["color"]]), 0.0, MAX_FACTOR)
        step = (1 + cover * (factor - 1)) ** (1 / GAMMA)  # the factor blended in by the cover, in the albedo's terms
        total = step if total is None else total * step
    return total


def lay_page(raw: bytes, level: int, page_x: int, page_z: int, strokes: list[dict]) -> bytes | None:
    """One page's file with the strokes laid on it, None when no block of it changes."""
    header = 148 if raw[84:88] == b"DX10" else 128
    old = ddsdecode.read_dds(raw)[..., :3]
    side = old.shape[0]
    if old.shape[1] != side or side % 4 or len(raw) < header + side * side // 2:
        return None
    span = NODE_TILES << level
    factor = gain(strokes, page_x * span, page_z * span, span, side)
    if factor is None:
        return None
    new = np.where((np.abs(factor - 1) > 1e-4).any(axis=-1)[..., None], to_stored(to_rgb(old) * factor), old)
    old_blocks, new_blocks = bc1encode.to_blocks(old), bc1encode.to_blocks(new)
    hit = (new_blocks != old_blocks).any(axis=(1, 2))
    if not hit.any():
        return None
    top = np.frombuffer(raw, np.uint8, side * side // 2, header).reshape(-1, 8).copy()
    top[hit] = bc1encode.encode_blocks(new_blocks[hit])
    return raw[:header] + top.tobytes() + raw[header + top.size :]


def dabs(stroke: dict) -> np.ndarray:
    """The points a stroke's brush is set down at (``(n, 2)`` tiles), as ``stroke_cover`` lays it."""
    return np.asarray(stroke_dabs(stroke)).reshape(-1, 2)


def touches(points: np.ndarray, radius_tiles: float, level: int, page_x: int, page_z: int) -> bool:
    """Whether a brush of ``radius_tiles`` set down at ``points`` reaches the page ``px`` x ``pz`` of ``level``."""
    span = NODE_TILES << level
    origin_x, origin_z = page_x * span, page_z * span
    inside = (origin_x - radius_tiles <= points[:, 0]) & (points[:, 0] <= origin_x + span + radius_tiles)
    return bool(
        (inside & (origin_z - radius_tiles <= points[:, 1]) & (points[:, 1] <= origin_z + span + radius_tiles)).any()
    )


def apply(read, names: list[str], strokes: list[dict]) -> tuple[dict[str, bytes], dict]:
    """Lay ``strokes`` on the pages among ``names`` (paths inside ``_gamedata/<island>/``; ``read`` gives a file's
    current bytes). Returns the changed pages and the report: ``pages``, ``strokes`` laid and ``unmatched`` (a stroke no
    page of the island lies under, or one the format cannot hold)."""
    usable = [
        (k, stroke, dabs(stroke), float(stroke.get("radius", 4))) for k, stroke in enumerate(strokes) if valid(stroke)
    ]
    pages = [(name, tuple(int(number) for number in found.groups())) for name in names if (found := PAGE.search(name))]
    out: dict[str, bytes] = {}
    laid: set[int] = set()
    for name, (level, page_x, page_z) in pages:
        mine = [(k, stroke) for k, stroke, points, radius in usable if touches(points, radius, level, page_x, page_z)]
        if not mine:
            continue
        laid |= {k for k, _ in mine}
        data = lay_page(read(name), level, page_x, page_z, [stroke for _, stroke in mine])
        if data is not None:
            out[name] = data
    unmatched = [tr("common.tint_stroke_k", k=k) for k in range(len(strokes)) if k not in laid]
    return out, {"pages": len(out), "strokes": len(laid), "unmatched": unmatched}
