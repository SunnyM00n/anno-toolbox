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
from typing import TYPE_CHECKING

from ..infra import buildcache
from ..infra.tools import require_tool, run_tool
from . import bc7encode, bc7gpu, bc7simple, ddsdecode, mipchain, texbackend

if TYPE_CHECKING:
    import numpy as np

# "cpu": no GPU encoder; "texconv": texconv on the CPU for every texture even when the in-process encoder is installed;
# "simple": the numpy mode-6 encoder even when etcpak is installed
ENCODER_ENV = "ANNO_BC7_ENCODER"
_VECTORS = contextvars.ContextVar("bc7_vectors", default=False)
CACHE: Path | None = None  # another folder for the BC7 results (the tests); the run's own is cache_base() / "bc7"
SHARED_METAL = "shared/dielectric_metal"  # the one metal map every material points at (known-issues, engine-notes)


class CTextureConversionError(RuntimeError):
    pass


def read_pixels(data: bytes) -> np.ndarray:
    """The top mip of any DDS the in-process decoders read (2070's, the DX10 formats BC7 and BC6H, cubes' first face)
    as ``(h, w, 4)`` uint8; ``ddsdecode.CUnsupportedDds`` for the rest."""
    try:
        return ddsdecode.read_dds(data)
    except ddsdecode.CUnsupportedDds:
        try:
            return ddsdecode.CDdsFile(data).level(0)
        except IndexError as exc:
            raise ddsdecode.CUnsupportedDds(str(exc)) from exc


def dds_to_png(source: Path, out_dir: Path) -> Path:
    """``source`` as a PNG of the stored values: numpy decodes every format it reads (``read_pixels``), texconv the rest
    (``CTextureConversionError`` when texconv is not there either)."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / (source.stem + ".png")
    try:
        Image.fromarray(read_pixels(source.read_bytes()), "RGBA").save(png)
        return png
    except (ddsdecode.CUnsupportedDds, OSError) as exc:
        reason = exc
    texconv = texbackend.texconv()
    if texconv is None:
        raise CTextureConversionError(f"{source}: a DDS the built-in decoder does not read ({reason}) and no texconv")
    result = run_tool(texconv, ["-ft", "png", "-o", str(out_dir), "-y", "-nologo", str(source)])
    if not result.ok or not png.is_file():
        raise CTextureConversionError(f"texconv failed on {source}:\n{result.stderr or result.stdout}")
    return png


def bc7_engine() -> str:
    """Which encoder writes BC7: ``etcpak`` (installed), ``texconv`` (forced, or etcpak is missing and texconv is
    found) or ``simple`` (``bc7simple``, numpy only). ``ANNO_BC7_ENCODER`` and ``ANNO_TEXTURE_BACKEND`` force one."""
    choice = os.environ.get(ENCODER_ENV, "")
    if choice in ("texconv", "simple"):
        return choice
    if texbackend.mode() == "texconv":
        return "texconv"
    if bc7encode.available():
        return "etcpak"
    return "texconv" if texbackend.texconv() else "simple"


def is_gpu_enabled() -> bool:
    """Whether this run sends its large pictures to the graphics card (``bc7gpu``; ``ANNO_BC7_ENCODER`` set to
    anything else, or no texconv, not)."""
    allowed = not os.environ.get(ENCODER_ENV) and texbackend.mode() == "auto"
    return allowed and bool(bc7gpu.adapters())


def encoder_id() -> str:
    """What the cache keys hold of the encoder: its build and settings, or the texconv build, and the GPU encoder's
    when it takes the large pictures (a texture's bytes depend on which encoder ran: never one key for both)."""
    engine = bc7_engine()
    if engine == "etcpak":
        cpu = bc7encode.identity()
    elif engine == "simple":
        cpu = bc7simple.identity()
    else:
        cpu = f"texconv-cpu-{buildcache.file_digest(require_tool('texconv'))[:16]}-s{bc7encode.SETTINGS_VERSION}"
    return f"{cpu}+{bc7gpu.identity()}" if is_gpu_enabled() else cpu


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


def _cache_key(kind: str, source: bytes, is_srgb: bool, is_srgb_input: bool, mip_count: int) -> str:
    """By the source bytes, the format, the mip count, the encoder and the filter version (build-cache.md)."""
    return buildcache.digest(
        "bc7", kind, encoder_id(), is_srgb, is_srgb_input, mip_count, mipchain.FILTER_VERSION, source
    )


def _rgba_dds(levels, is_srgb: bool) -> bytes:
    """The mip levels as an uncompressed R8G8B8A8 DDS (texconv keeps the levels it is given)."""
    height, width = levels[0].shape[:2]
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8 | (0x20000 if len(levels) > 1 else 0)
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, width * 4, 1, len(levels)) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if len(levels) > 1 else 0), 0, 0, 0, 0)
    head += struct.pack("<5I", 29 if is_srgb else 28, 3, 0, 1, 0)  # R8G8B8A8_UNORM(_SRGB)
    return head + b"".join(level.tobytes() for level in levels)


def _texconv_bc7(levels, is_srgb: bool) -> bytes:
    """The fallback: our mip levels compressed by texconv on the CPU (the GPU path is not deterministic)."""
    texconv = require_tool("texconv")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_tex_") as temp_folder:
        levels_dds, out = Path(temp_folder) / "levels.dds", Path(temp_folder) / "out"
        out.mkdir()
        levels_dds.write_bytes(_rgba_dds(levels, is_srgb))
        texconv_format = "BC7_UNORM_SRGB" if is_srgb else "BC7_UNORM"
        arguments = [
            "-nogpu",
            "-f",
            texconv_format,
            *([] if is_srgb else ["-bc", "u"]),
            "-ft",
            "dds",
            "-o",
            str(out),
            "-y",
            "-nologo",
        ]
        result = run_tool(texconv, [*arguments, str(levels_dds)])
        produced = out / "levels.dds"
        if not result.ok or not produced.is_file():
            raise CTextureConversionError(f"texconv failed:\n{result.stderr or result.stdout}")
        return produced.read_bytes()


def rgba_to_bc7(pixels, is_srgb: bool = False, is_srgb_input: bool = False, mip_count: int = 0) -> bytes:
    """``pixels`` (``(h, w, 4)`` uint8) as a BC7 DDS with ``mip_count`` levels (0: the full chain). With ``is_srgb``
    the DDS is BC7_UNORM_SRGB; ``is_srgb_input`` says ``pixels`` already holds the stored sRGB values (filtered in
    linear light), else ``pixels`` is linear and stored gamma-encoded (120 reads back as 183). UI textures are sRGB
    (always with ``is_srgb_input``), the world's maps and particles are UNORM (engine-notes "Texture formats")."""
    return _encode(pixels, is_srgb, is_srgb_input, mip_count)[0]


def _encode(pixels, is_srgb: bool, is_srgb_input: bool, mip_count: int) -> tuple[bytes, bool]:
    """``rgba_to_bc7``'s bytes, and whether they are what this run's encoder writes (False: the GPU failed on every
    adapter and the CPU encoder stood in, a file no cache may keep under the run's key)."""
    levels = mipchain.mip_chain(
        pixels, mip_count, gamma=is_srgb and is_srgb_input, to_srgb=is_srgb and not is_srgb_input
    )
    height, width = levels[0].shape[:2]
    if not _VECTORS.get() and is_gpu_enabled() and bc7gpu.wanted(width, height):
        try:
            return bc7gpu.encode(_rgba_dds(levels, is_srgb), is_srgb), True
        except bc7gpu.CGpuError as exc:
            warnings.warn(f"BC7 on the CPU for a {width}x{height} texture: {exc}", RuntimeWarning, stacklevel=2)
            return _cpu_bc7(levels, is_srgb), False
    return _cpu_bc7(levels, is_srgb), True


def _cpu_bc7(levels, is_srgb: bool) -> bytes:
    engine = bc7_engine()
    if engine == "etcpak":
        return bc7encode.encode_dds(levels, is_srgb, perceptual=is_srgb)
    if engine == "simple":
        return bc7simple.encode_dds(levels, is_srgb)
    return _texconv_bc7(levels, is_srgb)


def _cached_bc7(
    kind: str,
    source: bytes,
    make,
    destination: Path,
    is_srgb: bool,
    is_srgb_input: bool,
    mip_count: int,
    use_cache: bool,
) -> Path:
    """``make()`` (the BC7 bytes and whether the run's encoder wrote them, ``_encode``) written to ``destination``,
    kept in the BC7 cache by the key of its source. Both whole or not at all: the callers skip a ``destination`` that
    exists, so a run stopped mid-write must not leave half a texture."""
    cached = (
        _cache_file(_cache_key(kind, source, is_srgb, is_srgb_input, mip_count))
        if use_cache and buildcache.enabled()
        else None
    )
    if cached is not None and cached.is_file():
        buildcache.write_atomic(destination, cached.read_bytes())
        return destination
    data, exact = make()
    buildcache.write_atomic(destination, data)
    if cached is not None and exact:
        buildcache.write_atomic(cached, data)
    return destination


def png_to_bc7(
    source: Path,
    destination: Path,
    is_srgb: bool = False,
    use_cache: bool = True,
    mip_count: int = 0,
    is_srgb_input: bool = False,
) -> Path:
    """``source`` (a PNG) as a BC7 DDS at ``destination`` (``rgba_to_bc7`` says how ``is_srgb`` and ``is_srgb_input``
    read the values)."""

    def make() -> tuple[bytes, bool]:
        import numpy as np
        from PIL import Image

        try:
            with Image.open(source) as image:
                pixels = np.asarray(image.convert("RGBA"), np.uint8)
        except OSError as exc:
            raise CTextureConversionError(f"{source} is not an image: {exc}") from exc
        return _encode(pixels, is_srgb, is_srgb_input, mip_count)

    return _cached_bc7("png", source.read_bytes(), make, destination, is_srgb, is_srgb_input, mip_count, use_cache)


def solid_bc7(destination: Path, rgba: tuple[int, int, int, int], size_px: int = 4) -> Path:
    """A one-colour BC7 texture of ``size_px`` squared with its full mip chain: an exact block where the colour has a
    7-bit form (no encoder, no cache needed), else encoded."""
    exact = bc7encode.solid_dds(rgba, size_px)
    if exact is not None:
        buildcache.write_atomic(destination, exact)
        return destination
    import numpy as np

    pixels = np.empty((size_px, size_px, 4), np.uint8)
    pixels[:] = rgba
    return _cached_bc7(
        "solid", pixels.tobytes(), lambda: _encode(pixels, False, False, 0), destination, False, False, 0, True
    )


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

    strength = np.maximum(np.asarray(spec, np.float64), 1e-6)
    return np.clip(1.0 - (GLOSS_K / strength) ** GLOSS_P, 0.0, 1.0)


def spec_to_gloss_png(normal_png: Path, destination: Path) -> Path:
    """A 2070 normal map with its alpha (specular mask) turned into 117's gloss; RGB as stored."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as image:
        pixels = np.asarray(image.convert("RGBA"), np.uint8).copy()
    pixels[..., 3] = np.round(gloss_from_spec(pixels[..., 3] / 255.0) * 255).astype(np.uint8)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, "RGBA").save(destination)
    return destination


def cube_mean(data: bytes) -> tuple[float, float, float]:
    """The mean stored colour (0..1) over the six faces of an uncompressed R8G8B8A8 cube DDS's first mip."""
    import numpy as np

    height, width = struct.unpack_from("<II", data, 12)
    offset = 148 if data[84:88] == b"DX10" else 128
    count = width * height * 4
    if len(data) < offset + 6 * count:
        raise CTextureConversionError("not a six-face R8G8B8A8 cube")
    faces = np.frombuffer(data, np.uint8, 6 * count, offset).reshape(-1, 4)
    red, green, blue = (faces[:, :3].mean(axis=0) / 255.0).tolist()
    return red, green, blue


def env_cube_mean(source: Path) -> tuple[float, float, float]:
    """The mean colour of a 2070 environment cube (2070 draws it unfiltered, as stored values): texconv's
    R8G8B8A8 copy when texconv is there, else the cube's faces read in numpy."""
    texconv = texbackend.texconv()
    if texconv is None:
        return _cube_mean_faces(source.read_bytes())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_env_") as temp_folder:
        arguments = ["-f", "R8G8B8A8_UNORM", "-m", "1", "-ft", "dds", "-o", temp_folder, "-y", "-nologo", str(source)]
        result = run_tool(texconv, arguments)
        out = Path(temp_folder) / (source.stem + ".dds")
        if not result.ok or not out.is_file():
            raise CTextureConversionError(f"texconv failed on {source}:\n{result.stderr or result.stdout}")
        return cube_mean(out.read_bytes())


def _cube_mean_faces(data: bytes) -> tuple[float, float, float]:
    """``env_cube_mean`` without texconv: the mean stored colour over the first mip of the six faces."""
    import numpy as np

    try:
        dds = ddsdecode.CDdsFile(data)
        if dds.m_info.faces != 6:
            raise CTextureConversionError("not a six-face cube")
        faces = np.concatenate([dds.level(0, face)[..., :3].reshape(-1, 3) for face in range(6)])
    except (ddsdecode.CUnsupportedDds, IndexError) as exc:
        raise CTextureConversionError(f"a cube the built-in decoder does not read: {exc}") from exc
    red, green, blue = (faces.mean(axis=0, dtype=np.float64) / 255.0).tolist()
    return red, green, blue


def _resized_mask(mask_png: Path, size_px: tuple[int, int]):
    """The mask map as float RGBA at ``size_px`` (width, height)."""
    import numpy as np
    from PIL import Image

    with Image.open(mask_png) as image:
        return np.asarray(image.convert("RGBA").resize(size_px, Image.BILINEAR), np.float64)


def reflection_baked_png(
    diffuse_png: Path,
    mask_png: Path,
    env_mean: tuple[float, float, float] | None,
    destination: Path,
    use_dye: bool = False,
) -> Path:
    """The diffuse as 2070's model shader has it before the light (materials-2070 5): with ``env_mean`` the
    environment reflection baked in at the cube's mean colour where the mask's red says (the view-dependent part is
    left to 117's gloss); with ``use_dye`` the Dye shader's hue rule: ``lerp(c, mean(c), saturate(2 g))`` by the mask's
    green, which the dye mask then multiplies the owner's colour onto."""
    import numpy as np
    from PIL import Image

    with Image.open(diffuse_png) as image:
        pixels = np.asarray(image.convert("RGBA"), np.float64)
    mask = _resized_mask(mask_png, (pixels.shape[1], pixels.shape[0]))
    if env_mean is not None:
        factor = 1.0 + (ENV_STRENGTH * np.asarray(env_mean) - 1.0) * (mask[..., :1] / 255.0)
        pixels[..., :3] = np.clip(pixels[..., :3] * factor, 0, 255)
    if use_dye:
        share = np.clip(mask[..., 1:2] / 255.0 * 2, 0, 1)
        pixels[..., :3] += (pixels[..., :3].mean(-1, keepdims=True) - pixels[..., :3]) * share
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(pixels).astype(np.uint8), "RGBA").save(destination)
    return destination


def glow_with_dye_png(normal_png: Path, mask_png: Path, destination: Path) -> Path:
    """The normal map with the Dye shader's second glow term added to its blue: ``saturate(2 (g - 0.5))`` of the
    mask's green (materials-2070 5; 117 reads the blue as glow)."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as image:
        pixels = np.asarray(image.convert("RGBA"), np.float64)
    mask = _resized_mask(mask_png, (pixels.shape[1], pixels.shape[0]))
    pixels[..., 2] = np.clip(pixels[..., 2] + np.clip((mask[..., 1] / 255.0 - 0.5) * 2, 0, 1) * 255, 0, 255)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(pixels).astype(np.uint8), "RGBA").save(destination)
    return destination


def dye_mask_png(mask_png: Path, destination: Path) -> Path | None:
    """117's dye mask from a 2070 mask: grey ``saturate(2 g)`` (117 reads red). 2070's green 0 to 0.5
    is the player colour's share, above 0.5 a glow 117 does not take from here. None if no texel
    takes the colour (engine-rendering "Player colour")."""
    import numpy as np
    from PIL import Image

    with Image.open(mask_png) as image:
        green = np.asarray(image.convert("RGBA"), np.uint16)[..., 1]
    share = np.minimum(green * 2, 255).astype(np.uint8)
    if not share.any():
        return None
    rgba = np.dstack([share, share, share, np.full_like(share, 255)])
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(destination)
    return destination


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


def _mirrored(grid, is_x_flipped: bool, is_y_flipped: bool):
    """``a`` extended to twice its size by mirroring, a slope's sign flipped along its own axis."""
    import numpy as np

    sign_x, sign_y = (-1 if is_x_flipped else 1), (-1 if is_y_flipped else 1)
    top = np.concatenate([grid, sign_x * grid[:, ::-1]], axis=1)
    return np.concatenate([top, sign_y * top[::-1, :]], axis=0)


def height_from_normal(normal_rgba) -> tuple[object, float] | None:
    """The height a 2070 normal map implies (``(h, w)`` float, 0.5 the surface's mean) and the ``cParallaxScale``
    that displaces by it; None below one texel of relief. Least squares over the slopes, the wide relief removed."""
    import numpy as np

    pixels = np.asarray(normal_rgba, np.float64) / 255.0
    normal_x, normal_y = pixels[..., 0] * 2 - 1, pixels[..., 1] * 2 - 1
    normal_z = np.sqrt(np.clip(1 - normal_x * normal_x - normal_y * normal_y, 0.05, 1.0))  # blue is glow: z rebuilt
    slope_x, slope_y = (
        _mirrored(-normal_x / normal_z, True, False),
        _mirrored(-normal_y / normal_z, False, True),
    )  # y runs down the rows
    rows, columns = normal_x.shape
    frequency_y = 2 * np.pi * np.fft.fftfreq(2 * rows)[:, None]
    frequency_x = 2 * np.pi * np.fft.fftfreq(2 * columns)[None, :]
    squared_frequency = frequency_x * frequency_x + frequency_y * frequency_y
    squared_frequency[0, 0] = 1.0
    spectrum = (-1j * frequency_x * np.fft.fft2(slope_x) - 1j * frequency_y * np.fft.fft2(slope_y)) / squared_frequency
    sigma = max(rows, columns) / HEIGHT_BAND
    spectrum *= 1.0 - np.exp(-squared_frequency * sigma * sigma / 2)  # also drops the mean
    height = np.real(np.fft.ifft2(spectrum))[:rows, :columns]
    mean = float(height.mean())
    reach = float(np.percentile(np.abs(height - mean), 100 - HEIGHT_CLIP))  # an atlas seam's spike does not set it
    if 2 * reach < 1.0:
        return None
    stored = np.clip(0.5 + (height - mean) / (2 * reach), 0.0, 1.0)
    return stored, min(PARALLAX_MAX, 2 * reach / (PARALLAX_RANGE * max(rows, columns)))


def grey_to_bc4(grey, destination: Path, use_half_size: bool = True) -> Path:
    """A ``(h, w)`` float map (0..1) as a BC4 DDS with its full mip chain, at half size with ``use_half_size``."""
    import numpy as np

    from . import bc4encode

    values = np.round(np.clip(grey, 0, 1) * 255).astype(np.uint8)
    rgba = np.repeat(values[..., None], 4, axis=2)
    levels = mipchain.mip_chain(rgba)
    if use_half_size and len(levels) > 1 and min(levels[1].shape[:2]) >= 64:
        levels = levels[1:]
    buildcache.write_atomic(destination, bc4encode.encode_dds([level[..., 0] for level in levels]))
    return destination


def png_to_bc4(source: Path, destination: Path) -> Path:
    """The red channel of a PNG as a BC4_UNORM DDS with its full mip chain (the ground materials' height maps):
    texconv when it is there, else ``bc4encode`` with our own mips and the same DX10 header."""
    texconv = texbackend.texconv()
    if texconv is not None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_bc4_") as temp_folder:
            result = run_tool(
                texconv, ["-f", "BC4_UNORM", "-m", "0", "-ft", "dds", "-o", temp_folder, "-y", "-nologo", str(source)]
            )
            produced = Path(temp_folder) / (source.stem + ".dds")
            if not result.ok or not produced.is_file():
                raise CTextureConversionError(f"texconv BC4 failed on {source}: {result.stderr or result.stdout}")
            buildcache.write_atomic(destination, produced.read_bytes())
        return destination
    import numpy as np
    from PIL import Image

    from . import bc4encode

    try:
        with Image.open(source) as image:
            pixels = np.asarray(image.convert("RGBA"), np.uint8)
    except OSError as exc:
        raise CTextureConversionError(f"{source} is not an image: {exc}") from exc
    levels = mipchain.mip_chain(pixels)
    buildcache.write_atomic(destination, bc4encode.encode_dds([level[..., 0] for level in levels], dx10=True))
    return destination


def metal_png(metallic, occlusion, size_px: tuple[int, int], destination: Path) -> Path:
    """117's metal map at ``size_px`` (width, height): metallic in red, green and blue (as vanilla's), occlusion in
    alpha; either may be None (0 and 1)."""
    import numpy as np
    from PIL import Image

    width, height = size_px
    pixels = np.zeros((height, width, 4), np.uint8)
    if metallic is not None:
        metal = np.asarray(Image.fromarray(np.round(np.clip(metallic, 0, 1) * 255).astype(np.uint8)).resize(size_px))
        pixels[..., :3] = metal[..., None]
    pixels[..., 3] = 255
    if occlusion is not None:
        occlusion_image = Image.fromarray(np.round(np.clip(occlusion, 0, 1) * 255).astype(np.uint8)).resize(
            size_px, Image.BILINEAR
        )
        pixels[..., 3] = np.asarray(occlusion_image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, "RGBA").save(destination)
    return destination


def flip_normal_png(normal_png: Path, is_u_flipped: bool, is_v_flipped: bool, destination: Path) -> Path:
    """A normal map cropped with a mirrored axis: the slope along that axis negated (red for u, green for v)."""
    import numpy as np
    from PIL import Image

    with Image.open(normal_png) as image:
        pixels = np.asarray(image.convert("RGBA"), np.uint8).copy()
    for flip, channel in ((is_u_flipped, 0), (is_v_flipped, 1)):
        if flip:
            pixels[..., channel] = 255 - pixels[..., channel]
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, "RGBA").save(destination)
    return destination


def painted_share(png: Path, min_uv: tuple[float, float], max_uv: tuple[float, float], chroma: float = 0.15) -> float:
    """Share of the texels in a UV rectangle (``v`` down, as 2070 stores it) whose colour spread
    (max - min of r, g, b) exceeds ``chroma``: how much of a banner is painted, not grey cloth."""
    import numpy as np
    from PIL import Image

    with Image.open(png) as image:
        rgb = np.asarray(image.convert("RGB"), np.float32) / 255
    height, width = rgb.shape[:2]
    min_x, max_x = int(max(0.0, min_uv[0]) * width), int(min(1.0, max_uv[0]) * width) + 1
    min_y, max_y = int(max(0.0, min_uv[1]) * height), int(min(1.0, max_uv[1]) * height) + 1
    region = rgb[min_y:max_y, min_x:max_x]
    if region.size == 0:
        return 0.0
    return float(((region.max(-1) - region.min(-1)) > chroma).mean())


DXGI_R8G8B8A8_UNORM = 28


def write_solid_dds(destination: Path, rgba: tuple[int, int, int, int], size_px: int = 4) -> Path:
    """An uncompressed one-colour texture (DX10 header, R8G8B8A8_UNORM, full mip chain); needs no
    texconv. 117 takes a path ending in ``.dds`` as it is."""
    mip_count = size_px.bit_length()  # 4 -> 4, 2, 1
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8  # caps, height, width, pixel format, mips, pitch
    header = struct.pack("<4sII", b"DDS ", 124, flags) + struct.pack(
        "<IIIII", size_px, size_px, size_px * 4, 0, mip_count
    )
    header += b"\0" * 44 + struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)  # FOURCC
    header += struct.pack("<5I", 0x1000 | 0x8 | 0x400000, 0, 0, 0, 0)  # texture, complex, mipmap
    header += struct.pack("<5I", DXGI_R8G8B8A8_UNORM, 3, 0, 1, 0)  # 2D, one image
    pixels = b"".join(bytes(rgba) * (max(1, size_px >> mip) ** 2) for mip in range(mip_count))
    buildcache.write_atomic(destination, header + pixels)
    return destination


DXGI_BC7 = 98  # BC7_UNORM
DXGI_BC7_SRGB = 99  # BC7_UNORM_SRGB: 117's UI textures


def dds_info(data: bytes) -> tuple[int, int, int, int | None]:
    """Width, height, mip count and the DXGI format (None for a legacy header) of a DDS."""
    height, width = struct.unpack_from("<II", data, 12)
    mip_count = max(1, struct.unpack_from("<I", data, 28)[0])
    dxgi = struct.unpack_from("<I", data, 128)[0] if data[84:88] == b"DX10" else None
    return width, height, mip_count, dxgi


def bc7_like(pixels, original: bytes) -> bytes:
    """``pixels`` (``uint8`` RGBA) as a BC7 DDS in the format of ``original``, the 117 file it replaces: the same
    DXGI format (UNORM or sRGB), mip count and size. 117 reads its UI textures compressed; an uncompressed
    one draws as noise (known-issues-buildings "Uncompressed UI textures")."""
    from PIL import Image

    width, height, mip_count, dxgi = dds_info(original)
    if dxgi not in (DXGI_BC7, DXGI_BC7_SRGB):
        raise CTextureConversionError(f"the original is not BC7 (DXGI format {dxgi})")
    if pixels.shape[:2] != (height, width):
        raise CTextureConversionError(f"{pixels.shape[1]}x{pixels.shape[0]} pixels for a {width}x{height} original")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_like_") as temp_folder:
        png = Path(temp_folder) / "texture.png"
        Image.fromarray(pixels, "RGBA").save(png)
        is_srgb = dxgi == DXGI_BC7_SRGB  # the PNG holds the values the original stores: no gamma step
        return png_to_bc7(
            png, Path(temp_folder) / "texture_0.dds", is_srgb=is_srgb, is_srgb_input=is_srgb, mip_count=mip_count
        ).read_bytes()


def format_problem(original: bytes, written: bytes) -> str | None:
    """What differs between a replacement and the file it replaces (size, mips, DXGI format), or None."""
    want, got = dds_info(original), dds_info(written)
    if want == got:
        return None
    names = ("width", "height", "mips", "DXGI format")
    return ", ".join(
        f"{name} {wanted_value} -> {got_value}"
        for name, wanted_value, got_value in zip(names, want, got, strict=True)
        if wanted_value != got_value
    )


def convert_dds(source: Path, destination: Path, is_srgb: bool = False) -> Path:
    """DDS to BC7 DDS with a full mip chain, numpy decode to encoder with no PNG in between. The decoded values are the
    stored ones (no gamma step), so an sRGB output keeps them. A DDS seen before is found by its bytes. A format numpy
    does not read goes through texconv and a PNG."""
    data = source.read_bytes()

    def make() -> tuple[bytes, bool]:
        return _encode(ddsdecode.read_dds(data), is_srgb, is_srgb, 0)

    try:
        return _cached_bc7("dds", data, make, destination, is_srgb, is_srgb, 0, True)
    except ddsdecode.CUnsupportedDds:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_tex_") as temp_folder:
            return png_to_bc7(
                dds_to_png(source, Path(temp_folder)), destination, is_srgb=is_srgb, is_srgb_input=is_srgb
            )
