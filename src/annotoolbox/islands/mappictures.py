"""The map pictures as data: the province picture's channels, the underwater look, PNG and BC7 files, and the redraw of
an island's pictures after its ground was edited.

The island build draws them from 2070's ground (``islandmaps``); ``redraw`` brings the pictures an island already has
in line with edited heights.
"""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

from .islandgrid import HEIGHT_UNITS
from .islandground import stroke_cover, stroke_material

MAP_IMAGE = 512  # mapimage.png is always this size
SEA_COLOR = (16, 40, 64)
PROVINCE_HEIGHT_SCALE = 0.5  # province picture green per heightmap unit (vanilla: two levels per world unit)
# 2070's minimap colours for an underwater island (properties.xml MinimapUnderwaterIslandColor, ...BorderColor)
UNDERWATER_COLOR = (49, 110, 133)
UNDERWATER_BORDER = (158, 224, 255)


def province_picture(heights, alpha):
    """The province picture's pixels as vanilla's carry them, [H, W, 4] uint8: red the slope (the Sobel magnitude
    of green), green the height over the land's floor (``PROVINCE_HEIGHT_SCALE``), both 0 at sea; blue the distance
    to the coast (``coast_distance``), which the strategy map shades by; alpha the land. ``heights``: heightmap
    units over ``land_floor``; ``alpha``: land 0..1, the land itself where it is at least a half."""
    import numpy as np

    land = np.asarray(alpha) >= 0.5
    green = np.where(land, np.clip(np.asarray(heights, np.float64) * PROVINCE_HEIGHT_SCALE, 0, 255), 0.0)
    p = np.pad(green, 1, mode="edge")
    smooth_z = p[:-2] + 2 * p[1:-1] + p[2:]  # the Sobel kernels: [1 2 1] across, [-1 0 1] along
    smooth_x = p[:, :-2] + 2 * p[:, 1:-1] + p[:, 2:]
    sx, sz = smooth_z[:, 2:] - smooth_z[:, :-2], smooth_x[2:] - smooth_x[:-2]
    red = np.where(land, np.clip(np.hypot(sx, sz), 0, 255), 0.0)
    a = np.clip(np.asarray(alpha, np.float64) * 255, 0, 255)
    return np.dstack([np.round(red), np.round(green), coast_distance(land), np.round(a)]).astype(np.uint8)


def coast_distance(land):
    """Blue of vanilla's province pictures, [H, W] uint8: the distance to the coast in pixels, at sea to the nearest
    land pixel, on land to the nearest coast pixel (land with sea among its eight neighbours; the frame is not a
    coast), along eight-neighbour steps (1 straight, sqrt 2 diagonal), floored, at most 255. Matches every vanilla
    province picture pixel for pixel."""
    import numpy as np

    land = np.asarray(land, bool)
    h, w = land.shape
    framed = np.pad(land, 1, constant_values=True)
    sea_near = np.zeros_like(land)
    for dz in (0, 1, 2):
        for dx in (0, 1, 2):
            sea_near |= ~framed[dz : dz + h, dx : dx + w]
    d = np.where(land, _chamfer(land & sea_near), _chamfer(land))
    return np.clip(np.floor(d + 1e-6), 0, 255).astype(np.uint8)


def _chamfer(seed):
    """Eight-neighbour (1, sqrt 2) distance of every pixel to the nearest ``seed`` pixel: one pass down, one up, each
    row passed over both ways after taking the row before it (a shortest path of these steps never turns back)."""
    import numpy as np

    far = 1e9
    d = np.where(seed, 0.0, far)
    xs = np.arange(d.shape[1], dtype=np.float64)
    diag = np.sqrt(2.0)

    def along(row):
        row = np.minimum.accumulate(row - xs) + xs  # min over k of row[x - k] + k
        return np.minimum.accumulate((row + xs)[::-1])[::-1] - xs  # and of row[x + k] + k

    for rows in (range(d.shape[0]), range(d.shape[0] - 1, -1, -1)):
        prev = None
        for y in rows:
            row = d[y]
            if prev is not None:
                row = np.minimum(row, prev + 1)
                row[1:] = np.minimum(row[1:], prev[:-1] + diag)
                row[:-1] = np.minimum(row[:-1], prev[1:] + diag)
            d[y] = prev = along(row)
    return d


def underwater_look(rgb, alpha):
    """2070's underwater island on its minimap: the teal fill, a little of the hill shade kept, the light border on
    the land's edge (two pixels)."""
    import numpy as np

    lum = rgb.mean(axis=2)
    shade = 0.85 + 0.3 * (lum - lum.mean()) / max(float(lum.std()), 1e-3) * 0.25
    fill = np.asarray(UNDERWATER_COLOR, np.float32) / 255.0 * np.clip(shade, 0.7, 1.15)[..., None]
    inside = alpha > 0.5
    edge = np.zeros_like(inside)
    for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2)):
        edge |= inside & ~np.roll(np.roll(inside, dz, axis=0), dx, axis=1)
    border = np.asarray(UNDERWATER_BORDER, np.float32) / 255.0
    return np.clip(np.where(edge[..., None], border, fill), 0, 1)


def png_bytes(img) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def to_dds(pngs: dict[str, bytes]) -> dict[str, bytes]:
    """The BC7 ``_0.dds`` beside each picture (the game reads those; texconv does the work)."""
    from ..models.textures import png_to_bc7

    out: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_maps_") as tmp:
        for name, data in pngs.items():
            src = Path(tmp) / name
            src.write_bytes(data)
            dst = src.with_name(src.stem + "_0.dds")
            try:
                png_to_bc7(src, dst)  # BC7_UNORM with the PNG's values, as 117's own map pictures
            except Exception as exc:
                warnings.warn(f"map picture {name} ships without its _0.dds: {exc}", RuntimeWarning, stacklevel=2)
                continue
            out[dst.name] = dst.read_bytes()
    return out


# Redrawing an island's pictures after a ground edit (island-editor "Map pictures")
LIGHT = (-0.5, 0.8, -0.35)  # the hill shade's light, as the island build draws it
FLAT = 8.0  # the shade's normal is (-slope x, FLAT, -slope z), the slope per tile in heightmap units
PICTURES = ("mapimage.png", "gamemapimage.png", "provincemapimage.png", "activemapimage.png")


def _sample(heights, xs, zs):
    """``heights`` (two samples a tile, world units) bilinear at tile positions ``xs`` (columns) and ``zs`` (rows)."""
    import numpy as np

    n = heights.shape[0]
    fx, fz = np.clip(np.asarray(xs) * 2, 0, n - 1.001), np.clip(np.asarray(zs) * 2, 0, n - 1.001)
    x0, z0 = fx.astype(int), fz.astype(int)
    tx, tz = (fx - x0)[None, :], (fz - z0)[:, None]
    row0, row1 = heights[np.ix_(z0, x0)], heights[np.ix_(z0 + 1, x0)]
    row0r, row1r = heights[np.ix_(z0, x0 + 1)], heights[np.ix_(z0 + 1, x0 + 1)]
    return (row0 * (1 - tx) + row0r * tx) * (1 - tz) + (row1 * (1 - tx) + row1r * tx) * tz


def pixel_heights(heights, window: tuple[float, float, float, float], width: int, height: int):
    """The heights (heightmap units) at the pixel centres of a picture of ``width`` x ``height`` pixels over the tiles
    ``window`` (x0, z0, x1, z1), row 0 the far edge (largest z) as the pictures are; ``heights``: two samples a tile,
    world units."""
    import numpy as np

    x0, z0, x1, z1 = window
    xs = x0 + (np.arange(width) + 0.5) * (x1 - x0) / width
    zs = z1 - (np.arange(height) + 0.5) * (z1 - z0) / height
    return _sample(heights, xs, zs) * HEIGHT_UNITS


def hill_shade(h, pixels_per_tile: float | tuple[float, float]):
    """The shade (0.45 to 1.15) the picture's colours are multiplied with: the ground's slope against ``LIGHT``;
    ``pixels_per_tile`` a number, or (columns, rows) for a picture whose window is not square."""
    import numpy as np

    per_x, per_z = (pixels_per_tile, pixels_per_tile) if np.isscalar(pixels_per_tile) else pixels_per_tile
    gz, gx = np.gradient(h, 1.0 / per_z, 1.0 / per_x)
    normal = np.dstack([-gx, np.full_like(h, FLAT), -gz])
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    light = np.asarray(LIGHT) / np.linalg.norm(LIGHT)
    return np.clip(normal @ light, 0, 1) * 0.7 + 0.45


def underwater_floor(heights) -> float | None:
    """The height (world units) an underwater island's pictures count their land from: a world unit below its plateau
    (the most common height of its upper half, a quarter unit apart), as the island build counts it (``islandmaps.
    land_floor``); None for an island that rises above the sea."""
    import numpy as np

    h = np.asarray(heights, np.float64)
    top = float(h.max())
    if top >= 0:
        return None
    upper = h[h >= (float(h.min()) + top) / 2]
    levels, counts = np.unique(np.round(upper * 4) / 4, return_counts=True)
    return float(levels[np.argmax(counts)]) - 1.0


def _unshaded(rgb, shade, drawn):
    """The picture's colours without the hill shade; the pixels the picture did not draw (``drawn`` false) take those
    of the nearest drawn pixel one step inside the land, where no sea blends in."""
    import numpy as np
    from scipy import ndimage

    base = rgb / shade[..., None]
    if drawn.all() or not drawn.any():
        return base
    inner = ndimage.binary_erosion(drawn)
    source = inner if inner.any() else drawn
    near = ndimage.distance_transform_edt(~source, return_distances=False, return_indices=True)
    return np.where(drawn[..., None], base, base[near[0], near[1]])


def _laid(base, strokes, colours, box, ppt: float):
    """``base`` ([h, w, 3] floats, the picture without hill shade) with the painted ``strokes`` laid over it in their
    materials' colours (``colours``: material ID -> rgb), as the island build lays them (``stroke_cover``); ``box``: the
    tiles the picture covers, row 0 its far edge."""
    import numpy as np

    h, w = base.shape[:2]
    for st in strokes:
        colour = colours.get(stroke_material(st))
        if colour is None:
            continue
        moved = {**st, "points": [[p[0] - box[0], p[1] - box[1]] for p in st["points"]]}
        cover = stroke_cover(moved, max(h, w), ppt)[:h, :w][::-1, :, None]
        base = base * (1 - cover) + cover * (np.asarray(colour, np.float32) / 255.0)
    return base


def redraw(
    pictures: dict[str, bytes],
    old,
    new,
    window: tuple[int, int, int, int],
    size: int,
    strokes: list[dict] = (),
    colours: dict[int, tuple] | None = None,
) -> dict[str, bytes]:
    """The island's pictures (PNG bytes by file name, ``PICTURES``) drawn again for its edited ground: ``old`` and
    ``new`` the heights before and after (two samples a tile, world units), ``window`` the active rectangle, ``size``
    the grid. The province picture is data and is made from the new heights; the others keep their colours, lose the
    old hill shade for the new one, take the new land and sea, the painted ``strokes`` in their materials' ``colours``,
    and a coast that grew takes the colour of the land next to it. An underwater island (``underwater_floor``) counts
    its land from below its plateau and is drawn in 2070's underwater look. A picture the island does not have is not
    made."""
    import io

    import numpy as np
    from PIL import Image

    out: dict[str, bytes] = {}
    floor_old, floor_new = underwater_floor(old), underwater_floor(new)
    low_old, low_new = ((f or 0.0) * HEIGHT_UNITS for f in (floor_old, floor_new))  # heightmap units, as the pixels
    for name in PICTURES:
        if name not in pictures:
            continue
        img = Image.open(io.BytesIO(pictures[name])).convert("RGBA" if name != "mapimage.png" else "RGB")
        w, h = img.size
        box = (0, 0, size, size) if name == "mapimage.png" else window
        ppt = (w / (box[2] - box[0]), h / (box[3] - box[1]))
        h_old, h_new = (pixel_heights(g, box, w, h) for g in (old, new))
        land_old, land_new = np.clip(h_old - low_old, 0, 1), np.clip(h_new - low_new, 0, 1)
        if name.startswith("province"):
            pixels = province_picture(h_new - low_new, land_new)
        else:
            pixels = np.asarray(img, np.float32)
            rgb, drawn = (
                pixels[..., :3] / 255.0,
                (land_old >= 0.5) & (pixels[..., -1] >= 128 if pixels.shape[2] == 4 else True),
            )
            base = _laid(_unshaded(rgb, hill_shade(h_old, ppt), drawn), strokes, colours or {}, box, ppt[0])
            shaded = np.clip(base * hill_shade(h_new, ppt)[..., None], 0, 1)
            pixels = _finished(name, shaded, land_new, floor_new is not None)
        out[name] = png_bytes(Image.fromarray(pixels, "RGB" if name == "mapimage.png" else "RGBA"))
    return out


def _finished(name: str, shaded, land, underwater: bool):
    """A picture's pixels from its shaded colours and land alpha: the underwater look, the sea in the browser picture."""
    import numpy as np

    if underwater:
        shaded = underwater_look(shaded, land)
    if name == "mapimage.png":  # the sea is part of this picture
        sea = np.asarray(SEA_COLOR, np.float32) / 255.0
        shaded = shaded * land[..., None] + sea * (1 - land[..., None])
        return np.clip(shaded * 255, 0, 255).astype(np.uint8)
    return np.dstack([np.clip(shaded * 255, 0, 255), np.clip(land * 255, 0, 255)]).astype(np.uint8)


# Pictures made from the ground itself (a ported island has no old picture to redraw)
GAME_PIXELS_PER_TILE = 2  # the game and province pictures
ACTIVE_PIXELS_PER_TILE = 4  # the active picture, at most ACTIVE_MAX pixels across
ACTIVE_MAX = 1024


def picture_shape(name: str, window: tuple[int, int, int, int]) -> tuple[int, int]:
    """The (width, height) in pixels the island build gives picture ``name`` over the active ``window``."""
    w, h = window[2] - window[0], window[3] - window[1]
    if name == "mapimage.png":
        return MAP_IMAGE, MAP_IMAGE
    ppt = (
        GAME_PIXELS_PER_TILE
        if name in ("gamemapimage.png", "provincemapimage.png")
        else max(1, round(min(ACTIVE_PIXELS_PER_TILE, ACTIVE_MAX / max(w, h))))
    )
    return w * ppt, h * ppt


def paint(heights, colours, window: tuple[int, int, int, int], size: int, shapes: dict[str, tuple[int, int]]):
    """The pictures of ``shapes`` (name -> (width, height)) drawn from the island itself: ``heights`` two samples a tile
    (world units), ``colours`` the ground's colour per texel ([N, N, 3] floats over the grid, row z), ``window`` the
    active rectangle, ``size`` the grid. Returns PNG bytes by name."""
    import numpy as np
    from PIL import Image

    floor = underwater_floor(heights)
    low = (floor or 0.0) * HEIGHT_UNITS
    out: dict[str, bytes] = {}
    for name, (w, h) in shapes.items():
        box = (0, 0, size, size) if name == "mapimage.png" else window
        ppt = (w / (box[2] - box[0]), h / (box[3] - box[1]))
        hp = pixel_heights(heights, box, w, h)
        land = np.clip(hp - low, 0, 1)
        if name.startswith("province"):
            pixels = province_picture(hp - low, land)
        else:
            n = colours.shape[0]
            xs = np.clip(((box[0] + (np.arange(w) + 0.5) / ppt[0]) * n / size).astype(int), 0, n - 1)
            zs = np.clip(((box[3] - (np.arange(h) + 0.5) / ppt[1]) * n / size).astype(int), 0, n - 1)
            shaded = np.clip(colours[np.ix_(zs, xs)] * hill_shade(hp, ppt)[..., None], 0, 1)
            pixels = _finished(name, shaded, land, floor is not None)
        out[name] = png_bytes(Image.fromarray(pixels, "RGB" if name == "mapimage.png" else "RGBA"))
    return out
