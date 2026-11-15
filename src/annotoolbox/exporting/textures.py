"""Textures (DDS) exported as pictures: PNG (8 or 16 bit), OpenEXR or Radiance HDR, per mip level, face and slice.

``export(job)`` is the entry. The decoding is ``models.ddsdecode`` (BC1 to BC7, BC6H, the uncompressed and float
layouts, cubes and arrays; numpy needed). A float texture's 8-bit PNG is the values at the options' exposure in sRGB;
its 16-bit PNG the linear values clipped to 0..1; an 8-bit texture's float forms are its bytes over 255. A volume
texture is exported as stored only. Naming: docs/exporting.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from .run import Job, Made

CUBE = 0x200  # the cube-map bit of a DDS header's caps2 word
HALF_KINDS = ("BC6H", "RGBA16F", "RG16F", "R16F")  # layouts whose values are half floats: the EXR keeps half


def _levels(job: Job, info, cube: bool) -> list[tuple[int, int, str]]:
    """(mip, face, name tag) of every level the options ask for."""
    mips = range(info.mips) if job.options.all_mips else range(1)
    every = job.options.all_faces if cube else job.options.all_slices
    faces = range(info.faces) if every else range(1)
    kind = "face" if cube else "slice"
    return [
        (m, f, (f"_{kind}{f}" if info.faces > 1 and every else "") + (f"_mip{m}" if job.options.all_mips else ""))
        for f in faces
        for m in mips
    ]


def radiance_pixels(rgb) -> bytes:
    """Linear float RGB (h, w, 3) as Radiance's RGBE bytes (shared exponent), vectorised."""
    import numpy as np

    rgb = np.maximum(rgb.astype(np.float64), 0.0)
    top = rgb.max(axis=2)
    mantissa, exponent = np.frexp(np.where(top < 1e-32, 1.0, top))
    scale = np.where(top < 1e-32, 0.0, mantissa * 256.0 / np.where(top < 1e-32, 1.0, top))
    out = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = (rgb * scale[..., None]).astype(np.uint8)
    out[..., 3] = np.where(top < 1e-32, 0, exponent + 128).astype(np.uint8)
    return out.tobytes()


def _planes(values, dtype: str) -> dict[str, bytes]:
    import numpy as np

    return {n: np.ascontiguousarray(values[..., k]).astype(dtype).tobytes() for k, n in enumerate("RGBA")}


@dataclass(frozen=True)
class Levels:
    """What every level of one texture is exported as: the decoder, the forms, the float forms and the exposure."""

    dds: object
    want: set[str] | frozenset[str]
    float_forms: list[str]
    exposure: float


def _level_files(job: Job, levels: Levels, mip: int, face: int, tag: str, made: Made) -> None:
    """The files of one level (mip and face or slice) in the forms asked."""
    import numpy as np

    from ..formats import imagewrite
    from ..models.mipchain import exposed_srgb8

    dds, want, float_forms = levels.dds, levels.want, levels.float_forms
    hdr, half = dds.hdr, dds.kind in HALF_KINDS
    width, height = dds.size_of(mip)
    values = None
    if want & {"png", "png16", "exr", "hdr"}:
        values = dds.level_float(mip, face) if (hdr or float_forms or "png16" in want) else None
    if "png" in want:
        if hdr:
            pixels = exposed_srgb8(values, levels.exposure).tobytes()
            made.notes.append(f"png of a float texture: sRGB at exposure {levels.exposure:+g} stops")
        else:
            pixels = dds.level(mip, face).tobytes()
        made.files[job.name(".png", tag)] = imagewrite.png_bytes(width, height, pixels)
    if "png16" in want:
        wide = (np.clip(values, 0.0, 1.0) * 65535.0 + 0.5).astype("<u2")
        name = job.name(".png", tag + ("_16" if "png" in want else ""))
        made.files[name] = imagewrite.png_bytes(width, height, wide.tobytes(), 4, 16)
        if hdr:
            made.notes.append("16-bit png: linear values clipped to 0..1")
    if "exr" in float_forms:
        planes = _planes(values, "<f2" if half else "<f4")
        made.files[job.name(".exr", tag)] = imagewrite.exr_bytes(width, height, planes, "half" if half else "float")
    if "hdr" in float_forms:
        made.files[job.name(".hdr", tag)] = imagewrite.radiance_bytes(width, height, radiance_pixels(values[..., :3]))


def export(job: Job) -> Made:
    """The forms of ``job.formats`` (png, png16, exr, hdr) of a DDS, one file per level the options ask for."""
    try:
        import numpy  # noqa: F401  # the decoders need it

        from ..models.ddsdecode import DdsFile, UnsupportedDds
    except ImportError:
        return Made(notes=["textures need numpy (pip install numpy); the file is exported as stored"], status="failed")
    try:
        dds = DdsFile(job.data)
    except UnsupportedDds as exc:
        return Made(notes=[f"not decoded ({exc}); exported as stored"], status="failed")
    made, info, want = Made(), dds.info, job.formats
    cube = bool(int.from_bytes(job.data[112:116], "little") & CUBE)
    hdr = dds.hdr
    if hdr:
        made.notes.append(f"{info.format}: float texels{', decoded from BC6H' if dds.kind == 'BC6H' else ''}")
    if job.options.png_depth == 16 and "png" in want and "png16" not in want:
        want = (want - {"png"}) | {"png16"}
        made.notes.append("png_depth 16: the png is written as the 16-bit form")
    float_forms = [k for k in ("exr", "hdr") if k in want]
    if len(float_forms) == 2:
        float_forms = [job.options.hdr_form if job.options.hdr_form in ("exr", "hdr") else "exr"]
        made.notes.append(f"exr and hdr both asked: only {float_forms[0]} written (options: hdr_form)")
    levels = Levels(dds, want, float_forms, job.options.exposure)
    for mip, face, tag in _levels(job, info, cube):
        try:
            _level_files(job, levels, mip, face, tag, made)
        except (UnsupportedDds, IndexError) as exc:
            made.notes.append(f"mip {mip} face {face}: {exc}")
            made.status = "failed"
    if float_forms and not hdr:
        made.notes.append("float forms of an 8-bit texture: its bytes over 255")
    if info.faces > 1 and not (job.options.all_faces if cube else job.options.all_slices):
        made.notes.append(f"only the first of {info.faces} {'faces' if cube else 'slices'} (options)")
    made.notes = list(dict.fromkeys(made.notes))
    return made
