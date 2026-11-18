"""The texture layer: 2070's DDS read to pixels, mip chains built, BC7 written in-process, the maps 117's PBR shader
wants synthesised, the formats of 117's own textures matched (docs/texture-pipeline.md; engine-notes "Texture formats":
which kind gets which BC7 format and what the channels hold). ``texturerefs`` decides the names in the mod; this
module makes the files. The large pictures go to the graphics card (``bc7gpu``). texconv is the fallback for a DDS
numpy does not read and for a machine without the encoder.
"""

from __future__ import annotations

import contextlib
import contextvars
import math
import os
import struct
import tempfile
import warnings
from pathlib import Path

from ..infra import buildcache
from ..infra.tools import require_tool, run_tool
from . import bc7encode, bc7gpu, ddsdecode, mipchain

# "cpu": no GPU encoder; "texconv": texconv on the CPU for every texture even when the in-process encoder is installed
ENCODER_ENV = "ANNO_BC7_ENCODER"
_VECTORS = contextvars.ContextVar("bc7_vectors", default=False)
CACHE: Path | None = None  # another folder for the BC7 results (the tests); the run's own is cache_base() / "bc7"
SHARED_METAL = "shared/dielectric_metal"  # the one metal map every material points at (known-issues, engine-notes)


class TextureConversionError(RuntimeError):
    pass


def dds_to_png(src: Path, out_dir: Path) -> Path:
    """``src`` as a PNG of the stored values: numpy decodes what 2070 ships, texconv anything else."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / (src.stem + ".png")
    try:
        from PIL import Image

        Image.fromarray(ddsdecode.read_dds(src.read_bytes()), "RGBA").save(png)
        return png
    except (ddsdecode.UnsupportedDds, OSError):
        pass
    texconv = require_tool("texconv")
    res = run_tool(texconv, ["-ft", "png", "-o", str(out_dir), "-y", "-nologo", str(src)])
    if not res.ok or not png.is_file():
        raise TextureConversionError(f"texconv failed on {src}:\n{res.stderr or res.stdout}")
    return png


def in_process() -> bool:
    """Whether BC7 is written by the in-process encoder (else by texconv on the CPU)."""
    return os.environ.get(ENCODER_ENV, "") != "texconv" and bc7encode.available()


def gpu_on() -> bool:
    """Whether this run sends its large pictures to the graphics card (``bc7gpu``; ``ANNO_BC7_ENCODER=cpu`` not)."""
    return os.environ.get(ENCODER_ENV, "") not in ("cpu", "texconv") and bool(bc7gpu.adapters())


def encoder_id() -> str:
    """What the cache keys hold of the encoder: its build and settings, or the texconv build, and the GPU encoder's
    when it takes the large pictures (a texture's bytes depend on which encoder ran: never one key for both)."""
    if in_process():
        cpu = bc7encode.identity()
    else:
        cpu = f"texconv-cpu-{buildcache.file_digest(require_tool('texconv'))[:16]}-s{bc7encode.SETTINGS_VERSION}"
    return f"{cpu}+{bc7gpu.identity()}" if gpu_on() else cpu


@contextlib.contextmanager
def vector_data():
    """The textures written inside hold vectors (normal maps, ripples), not a picture: always the CPU encoder, whose
    error is 4 to 10 dB lower on them (texture-pipeline "GPU encoder")."""
    token = _VECTORS.set(True)
    try:
        yield
    finally:
        _VECTORS.reset(token)


def _cache_file(key: str) -> Path:
    from ..sources.overlay import cache_base

    return (CACHE or cache_base() / "bc7") / key[:2] / f"{key}.dds"


def _cache_key(kind: str, source: bytes, srgb: bool, srgb_in: bool, mips: int) -> str:
    """By the source bytes, the format, the mip count, the encoder and the filter version (build-cache.md)."""
    return buildcache.digest("bc7", kind, encoder_id(), srgb, srgb_in, mips, mipchain.FILTER_VERSION, source)


def _rgba_dds(levels, srgb: bool) -> bytes:
    """The mip levels as an uncompressed R8G8B8A8 DDS (texconv keeps the levels it is given)."""
    h, w = levels[0].shape[:2]
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8 | (0x20000 if len(levels) > 1 else 0)
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, w * 4, 1, len(levels)) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if len(levels) > 1 else 0), 0, 0, 0, 0)
    head += struct.pack("<5I", 29 if srgb else 28, 3, 0, 1, 0)  # R8G8B8A8_UNORM(_SRGB)
    return head + b"".join(level.tobytes() for level in levels)


def _texconv_bc7(levels, srgb: bool) -> bytes:
    """The fallback: our mip levels compressed by texconv on the CPU (the GPU path is not deterministic)."""
    texconv = require_tool("texconv")
    with tempfile.TemporaryDirectory(prefix="a2070_tex_") as tmp:
        src, out = Path(tmp) / "levels.dds", Path(tmp) / "out"
        out.mkdir()
        src.write_bytes(_rgba_dds(levels, srgb))
        fmt = "BC7_UNORM_SRGB" if srgb else "BC7_UNORM"
        args = ["-nogpu", "-f", fmt, *([] if srgb else ["-bc", "u"]), "-ft", "dds", "-o", str(out), "-y", "-nologo"]
        res = run_tool(texconv, [*args, str(src)])
        produced = out / "levels.dds"
        if not res.ok or not produced.is_file():
            raise TextureConversionError(f"texconv failed:\n{res.stderr or res.stdout}")
        return produced.read_bytes()


def rgba_to_bc7(px, srgb: bool = False, srgb_in: bool = False, mips: int = 0) -> bytes:
    """``px`` (``(h, w, 4)`` uint8) as a BC7 DDS with ``mips`` levels (0: the full chain). With ``srgb`` the DDS is
    BC7_UNORM_SRGB; ``srgb_in`` says ``px`` already holds the stored sRGB values (filtered in linear light), else
    ``px`` is linear and stored gamma-encoded (120 reads back as 183). UI textures are sRGB (always with
    ``srgb_in``), the world's maps and particles are UNORM (engine-notes "Texture formats")."""
    return _encode(px, srgb, srgb_in, mips)[0]


def _encode(px, srgb: bool, srgb_in: bool, mips: int) -> tuple[bytes, bool]:
    """``rgba_to_bc7``'s bytes, and whether they are what this run's encoder writes (False: the GPU failed on every
    adapter and the CPU encoder stood in, a file no cache may keep under the run's key)."""
    levels = mipchain.mip_chain(px, mips, gamma=srgb and srgb_in, to_srgb=srgb and not srgb_in)
    height, width = levels[0].shape[:2]
    if not _VECTORS.get() and gpu_on() and bc7gpu.wanted(width, height):
        try:
            return bc7gpu.encode(_rgba_dds(levels, srgb), srgb), True
        except bc7gpu.GpuError as exc:
            warnings.warn(f"BC7 on the CPU for a {width}x{height} texture: {exc}", RuntimeWarning, stacklevel=2)
            return _cpu_bc7(levels, srgb), False
    return _cpu_bc7(levels, srgb), True


def _cpu_bc7(levels, srgb: bool) -> bytes:
    if in_process():
        return bc7encode.encode_dds(levels, srgb, perceptual=srgb)
    return _texconv_bc7(levels, srgb)


def _cached_bc7(kind: str, source: bytes, make, dst: Path, srgb: bool, srgb_in: bool, mips: int, cache: bool) -> Path:
    """``make()`` (the BC7 bytes and whether the run's encoder wrote them, ``_encode``) written to ``dst``, kept in
    the BC7 cache by the key of its source. Both whole or not at all: the callers skip a ``dst`` that exists, so a run
    stopped mid-write must not leave half a texture."""
    cached = _cache_file(_cache_key(kind, source, srgb, srgb_in, mips)) if cache and buildcache.enabled() else None
    if cached is not None and cached.is_file():
        buildcache.write_atomic(dst, cached.read_bytes())
        return dst
    data, exact = make()
    buildcache.write_atomic(dst, data)
    if cached is not None and exact:
        buildcache.write_atomic(cached, data)
    return dst


def png_to_bc7(
    src: Path, dst: Path, srgb: bool = False, cache: bool = True, mips: int = 0, srgb_in: bool = False
) -> Path:
    """``src`` (a PNG) as a BC7 DDS at ``dst`` (``rgba_to_bc7`` says how ``srgb`` and ``srgb_in`` read the values)."""

    def make() -> tuple[bytes, bool]:
        import numpy as np
        from PIL import Image

        try:
            with Image.open(src) as img:
                px = np.asarray(img.convert("RGBA"), np.uint8)
        except OSError as exc:
            raise TextureConversionError(f"{src} is not an image: {exc}") from exc
        return _encode(px, srgb, srgb_in, mips)

    return _cached_bc7("png", src.read_bytes(), make, dst, srgb, srgb_in, mips, cache)


def solid_bc7(dst: Path, rgba: tuple[int, int, int, int], size: int = 4) -> Path:
    """A one-colour BC7 texture of ``size`` squared with its full mip chain: an exact block where the colour has a
    7-bit form (no encoder, no cache needed), else encoded."""
    exact = bc7encode.solid_dds(rgba, size)
    if exact is not None:
        buildcache.write_atomic(dst, exact)
        return dst
    import numpy as np

    px = np.empty((size, size, 4), np.uint8)
    px[:] = rgba
    return _cached_bc7("solid", px.tobytes(), lambda: _encode(px, False, False, 0), dst, False, False, 0, True)


# 2070's normal-map alpha m is a specular strength: its highlight adds 10 m (N.H)^100 to the lerp between ambient and
# sun, tinted by the albedo. Setting 117's peak (F Vis N.L / (4 r^4) with r = 1 - gloss) equal to it gives
# gloss = 1 - (F Vis N.L / (40 A m))^(1/4) (rendering-2070 7.4), on a roof under 2070's sun and camera: the sun's
# elevation of the North set (ambientmap.xml, 47.9 degrees), the near camera step's angle (camerapositions.xml,
# 0.95557 rad), the mean building albedo 0.104 (linear), a dielectric F, Vis at r = 0.4.
GLOSS_P = 0.25
SUN_ELEVATION, VIEW_ELEVATION = math.radians(47.9), 0.95557
_F, _ALBEDO, _R = 0.04, 0.104, 0.4
_NL, _NV = math.sin(SUN_ELEVATION), math.sin(VIEW_ELEVATION)
_K = (_R + 1) ** 2 / 8
GLOSS_K = _F * _NL / (40 * _ALBEDO * (_NL * (1 - _K) + _K) * (_NV * (1 - _K) + _K))  # 0.0101
ENV_STRENGTH = 1.5  # 2070's model shader: lerp(albedo, albedo x reflection x 1.5, mask red)


def gloss_from_spec(spec):
    """117 gloss for 2070's specular mask (0..1, a float or a numpy array): 0 below ``GLOSS_K``, 0.68 at 1."""
    import numpy as np

    s = np.maximum(np.asarray(spec, np.float64), 1e-6)
    return np.clip(1.0 - (GLOSS_K / s) ** GLOSS_P, 0.0, 1.0)


def spec_to_gloss_png(normal_png: Path, dst: Path) -> Path:
    """A 2070 normal map with its alpha (specular mask) turned into 117's gloss; RGB as stored."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as img:
        px = np.asarray(img.convert("RGBA"), np.uint8).copy()
    px[..., 3] = np.round(gloss_from_spec(px[..., 3] / 255.0) * 255).astype(np.uint8)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, "RGBA").save(dst)
    return dst


def cube_mean(data: bytes) -> tuple[float, float, float]:
    """The mean stored colour (0..1) over the six faces of an uncompressed R8G8B8A8 cube DDS's first mip."""
    import numpy as np

    height, width = struct.unpack_from("<II", data, 12)
    offset = 148 if data[84:88] == b"DX10" else 128
    count = width * height * 4
    if len(data) < offset + 6 * count:
        raise TextureConversionError("not a six-face R8G8B8A8 cube")
    faces = np.frombuffer(data, np.uint8, 6 * count, offset).reshape(-1, 4)
    r, g, b = (faces[:, :3].mean(axis=0) / 255.0).tolist()
    return r, g, b


def env_cube_mean(src: Path) -> tuple[float, float, float]:
    """The mean colour of a 2070 environment cube (2070 draws it unfiltered, as stored values)."""
    texconv = require_tool("texconv")
    with tempfile.TemporaryDirectory(prefix="a2070_env_") as tmp:
        args = ["-f", "R8G8B8A8_UNORM", "-m", "1", "-ft", "dds", "-o", tmp, "-y", "-nologo", str(src)]
        res = run_tool(texconv, args)
        out = Path(tmp) / (src.stem + ".dds")
        if not res.ok or not out.is_file():
            raise TextureConversionError(f"texconv failed on {src}:\n{res.stderr or res.stdout}")
        return cube_mean(out.read_bytes())


def _resized_mask(mask_png: Path, size: tuple[int, int]):
    """The mask map as float RGBA at ``size`` (width, height)."""
    import numpy as np
    from PIL import Image

    with Image.open(mask_png) as img:
        return np.asarray(img.convert("RGBA").resize(size, Image.BILINEAR), np.float64)


def reflection_baked_png(
    diffuse_png: Path,
    mask_png: Path,
    env_mean: tuple[float, float, float] | None,
    dst: Path,
    dye: bool = False,
) -> Path:
    """The diffuse as 2070's model shader has it before the light (materials-2070 5): with ``env_mean`` the
    environment reflection baked in at the cube's mean colour where the mask's red says (the view-dependent part is
    left to 117's gloss); with ``dye`` the Dye shader's hue rule: ``lerp(c, mean(c), saturate(2 g))`` by the mask's
    green, which the dye mask then multiplies the owner's colour onto."""
    import numpy as np
    from PIL import Image

    with Image.open(diffuse_png) as img:
        px = np.asarray(img.convert("RGBA"), np.float64)
    mask = _resized_mask(mask_png, (px.shape[1], px.shape[0]))
    if env_mean is not None:
        factor = 1.0 + (ENV_STRENGTH * np.asarray(env_mean) - 1.0) * (mask[..., :1] / 255.0)
        px[..., :3] = np.clip(px[..., :3] * factor, 0, 255)
    if dye:
        share = np.clip(mask[..., 1:2] / 255.0 * 2, 0, 1)
        px[..., :3] += (px[..., :3].mean(-1, keepdims=True) - px[..., :3]) * share
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(px).astype(np.uint8), "RGBA").save(dst)
    return dst


def glow_with_dye_png(normal_png: Path, mask_png: Path, dst: Path) -> Path:
    """The normal map with the Dye shader's second glow term added to its blue: ``saturate(2 (g - 0.5))`` of the
    mask's green (materials-2070 5; 117 reads the blue as glow)."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as img:
        px = np.asarray(img.convert("RGBA"), np.float64)
    mask = _resized_mask(mask_png, (px.shape[1], px.shape[0]))
    px[..., 2] = np.clip(px[..., 2] + np.clip((mask[..., 1] / 255.0 - 0.5) * 2, 0, 1) * 255, 0, 255)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(px).astype(np.uint8), "RGBA").save(dst)
    return dst


def dye_mask_png(mask_png: Path, dst: Path) -> Path | None:
    """117's dye mask from a 2070 mask: grey ``saturate(2 g)`` (117 reads red). 2070's green 0 to 0.5
    is the player colour's share, above 0.5 a glow 117 does not take from here. None if no texel
    takes the colour (engine-rendering "Player colour")."""
    import numpy as np
    from PIL import Image

    with Image.open(mask_png) as img:
        green = np.asarray(img.convert("RGBA"), np.uint16)[..., 1]
    share = np.minimum(green * 2, 255).astype(np.uint8)
    if not share.any():
        return None
    rgba = np.dstack([share, share, share, np.full_like(share, 255)])
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(dst)
    return dst


# Derived maps (texture-pipeline "Derived material maps")

PARALLAX_RANGE = 0.01  # 117's displacement over a height map's full range, in UV, at cParallaxScale 1
# the strength the look test starts from: vanilla's own default (2,171 of 3,266 sampled); 2070's maps imply 1.8 to 20
PARALLAX_MAX = 1.0
HEIGHT_BAND = 32  # relief wider than the map's longest side over this is left to the mesh (a Gaussian high pass)
HEIGHT_CLIP = 0.1  # percent of the texels furthest from the mean that may clip
METAL_MAX = 512  # texels a side of a metal map with a reflection share
AO_MAX = 256  # texels a side of an occlusion-only metal map
AO_FLAT = 0.97  # occlusion at or above this everywhere is no file of its own
FLAT_GLOSS = "shared/flat_gloss08"  # a flat normal map with the gloss of a Blinn exponent 8 lobe (icons)


def blinn_gloss(exponent: float) -> float:
    """117's gloss for a Blinn-Phong lobe: GGX alpha sqrt(2 / (n + 2)), roughness its square root (materials-2070 9)."""
    return 1.0 - math.sqrt(math.sqrt(2.0 / (exponent + 2.0)))


def _mirrored(a, odd_x: bool, odd_y: bool):
    """``a`` extended to twice its size by mirroring, a slope's sign flipped along its own axis."""
    import numpy as np

    sx, sy = (-1 if odd_x else 1), (-1 if odd_y else 1)
    top = np.concatenate([a, sx * a[:, ::-1]], axis=1)
    return np.concatenate([top, sy * top[::-1, :]], axis=0)


def height_from_normal(normal_rgba) -> tuple[object, float] | None:
    """The height a 2070 normal map implies (``(h, w)`` float, 0.5 the surface's mean) and the ``cParallaxScale``
    that displaces by it; None below one texel of relief. Least squares over the slopes, the wide relief removed."""
    import numpy as np

    px = np.asarray(normal_rgba, np.float64) / 255.0
    nx, ny = px[..., 0] * 2 - 1, px[..., 1] * 2 - 1
    nz = np.sqrt(np.clip(1 - nx * nx - ny * ny, 0.05, 1.0))  # blue is glow: z rebuilt
    gx, gy = _mirrored(-nx / nz, True, False), _mirrored(-ny / nz, False, True)  # y runs down the rows
    h, w = nx.shape
    wy = 2 * np.pi * np.fft.fftfreq(2 * h)[:, None]
    wx = 2 * np.pi * np.fft.fftfreq(2 * w)[None, :]
    k2 = wx * wx + wy * wy
    k2[0, 0] = 1.0
    spectrum = (-1j * wx * np.fft.fft2(gx) - 1j * wy * np.fft.fft2(gy)) / k2
    sigma = max(h, w) / HEIGHT_BAND
    spectrum *= 1.0 - np.exp(-k2 * sigma * sigma / 2)  # also drops the mean
    height = np.real(np.fft.ifft2(spectrum))[:h, :w]
    mean = float(height.mean())
    reach = float(np.percentile(np.abs(height - mean), 100 - HEIGHT_CLIP))  # an atlas seam's spike does not set it
    if 2 * reach < 1.0:
        return None
    stored = np.clip(0.5 + (height - mean) / (2 * reach), 0.0, 1.0)
    return stored, min(PARALLAX_MAX, 2 * reach / (PARALLAX_RANGE * max(h, w)))


def grey_to_bc4(grey, dst: Path, half: bool = True) -> Path:
    """A ``(h, w)`` float map (0..1) as a BC4 DDS with its full mip chain, at half size with ``half``."""
    import numpy as np

    from . import bc4encode

    values = np.round(np.clip(grey, 0, 1) * 255).astype(np.uint8)
    rgba = np.repeat(values[..., None], 4, axis=2)
    levels = mipchain.mip_chain(rgba)
    if half and len(levels) > 1 and min(levels[1].shape[:2]) >= 64:
        levels = levels[1:]
    buildcache.write_atomic(dst, bc4encode.encode_dds([level[..., 0] for level in levels]))
    return dst


def metal_png(metallic, occlusion, size: tuple[int, int], dst: Path) -> Path:
    """117's metal map at ``size`` (width, height): metallic in red, green and blue (as vanilla's), occlusion in
    alpha; either may be None (0 and 1)."""
    import numpy as np
    from PIL import Image

    w, h = size
    px = np.zeros((h, w, 4), np.uint8)
    if metallic is not None:
        metal = np.asarray(Image.fromarray(np.round(np.clip(metallic, 0, 1) * 255).astype(np.uint8)).resize(size))
        px[..., :3] = metal[..., None]
    px[..., 3] = 255
    if occlusion is not None:
        ao = Image.fromarray(np.round(np.clip(occlusion, 0, 1) * 255).astype(np.uint8)).resize(size, Image.BILINEAR)
        px[..., 3] = np.asarray(ao)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, "RGBA").save(dst)
    return dst


def flip_normal_png(normal_png: Path, flip_u: bool, flip_v: bool, dst: Path) -> Path:
    """A normal map cropped with a mirrored axis: the slope along that axis negated (red for u, green for v)."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as img:
        px = np.asarray(img.convert("RGBA"), np.uint8).copy()
    for flip, channel in ((flip_u, 0), (flip_v, 1)):
        if flip:
            px[..., channel] = 255 - px[..., channel]
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(px, "RGBA").save(dst)
    return dst


def painted_share(png: Path, uv_min: tuple[float, float], uv_max: tuple[float, float], chroma: float = 0.15) -> float:
    """Share of the texels in a UV rectangle (``v`` down, as 2070 stores it) whose colour spread
    (max - min of r, g, b) exceeds ``chroma``: how much of a banner is painted, not grey cloth."""
    import numpy as np
    from PIL import Image

    with Image.open(png) as img:
        rgb = np.asarray(img.convert("RGB"), np.float32) / 255
    h, w = rgb.shape[:2]
    x0, x1 = int(max(0.0, uv_min[0]) * w), int(min(1.0, uv_max[0]) * w) + 1
    y0, y1 = int(max(0.0, uv_min[1]) * h), int(min(1.0, uv_max[1]) * h) + 1
    region = rgb[y0:y1, x0:x1]
    if region.size == 0:
        return 0.0
    return float(((region.max(-1) - region.min(-1)) > chroma).mean())


DXGI_R8G8B8A8_UNORM = 28


def write_solid_dds(dst: Path, rgba: tuple[int, int, int, int], size: int = 4) -> Path:
    """An uncompressed one-colour texture (DX10 header, R8G8B8A8_UNORM, full mip chain); needs no
    texconv. 117 takes a path ending in ``.dds`` as it is."""
    mips = size.bit_length()  # 4 -> 4, 2, 1
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8  # caps, height, width, pixel format, mips, pitch
    header = struct.pack("<4sII", b"DDS ", 124, flags) + struct.pack("<IIIII", size, size, size * 4, 0, mips)
    header += b"\0" * 44 + struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)  # FOURCC
    header += struct.pack("<5I", 0x1000 | 0x8 | 0x400000, 0, 0, 0, 0)  # texture, complex, mipmap
    header += struct.pack("<5I", DXGI_R8G8B8A8_UNORM, 3, 0, 1, 0)  # 2D, one image
    pixels = b"".join(bytes(rgba) * (max(1, size >> m) ** 2) for m in range(mips))
    buildcache.write_atomic(dst, header + pixels)
    return dst


DXGI_BC7 = 98  # BC7_UNORM
DXGI_BC7_SRGB = 99  # BC7_UNORM_SRGB: 117's UI textures


def dds_info(data: bytes) -> tuple[int, int, int, int | None]:
    """Width, height, mip count and the DXGI format (None for a legacy header) of a DDS."""
    height, width = struct.unpack_from("<II", data, 12)
    mips = max(1, struct.unpack_from("<I", data, 28)[0])
    dxgi = struct.unpack_from("<I", data, 128)[0] if data[84:88] == b"DX10" else None
    return width, height, mips, dxgi


def bc7_like(px, original: bytes) -> bytes:
    """``px`` (``uint8`` RGBA) as a BC7 DDS in the format of ``original``, the 117 file it replaces: the same
    DXGI format (UNORM or sRGB), mip count and size. 117 reads its UI textures compressed; an uncompressed
    one draws as noise (known-issues-buildings "Uncompressed UI textures")."""
    from PIL import Image

    width, height, mips, dxgi = dds_info(original)
    if dxgi not in (DXGI_BC7, DXGI_BC7_SRGB):
        raise TextureConversionError(f"the original is not BC7 (DXGI format {dxgi})")
    if px.shape[:2] != (height, width):
        raise TextureConversionError(f"{px.shape[1]}x{px.shape[0]} pixels for a {width}x{height} original")
    with tempfile.TemporaryDirectory(prefix="a2070_like_") as tmp:
        png = Path(tmp) / "texture.png"
        Image.fromarray(px, "RGBA").save(png)
        srgb = dxgi == DXGI_BC7_SRGB  # the PNG holds the values the original stores: no gamma step
        return png_to_bc7(png, Path(tmp) / "texture_0.dds", srgb=srgb, srgb_in=srgb, mips=mips).read_bytes()


def format_problem(original: bytes, written: bytes) -> str | None:
    """What differs between a replacement and the file it replaces (size, mips, DXGI format), or None."""
    want, got = dds_info(original), dds_info(written)
    if want == got:
        return None
    names = ("width", "height", "mips", "DXGI format")
    return ", ".join(f"{n} {w} -> {g}" for n, w, g in zip(names, want, got, strict=True) if w != g)


def convert_dds(src: Path, dst: Path, srgb: bool = False) -> Path:
    """DDS to BC7 DDS with a full mip chain, numpy decode to encoder with no PNG in between. The decoded values are the
    stored ones (no gamma step), so an sRGB output keeps them. A DDS seen before is found by its bytes. A format numpy
    does not read goes through texconv and a PNG."""
    data = src.read_bytes()

    def make() -> tuple[bytes, bool]:
        return _encode(ddsdecode.read_dds(data), srgb, srgb, 0)

    try:
        return _cached_bc7("dds", data, make, dst, srgb, srgb, 0, True)
    except ddsdecode.UnsupportedDds:
        with tempfile.TemporaryDirectory(prefix="a2070_tex_") as tmp:
            return png_to_bc7(dds_to_png(src, Path(tmp)), dst, srgb=srgb, srgb_in=srgb)
