"""Pictures (PNG, TGA, JPEG, BMP, GIF): as stored, and as PNG for the ones that are not.

A PNG is already a PNG, so its ``png`` form is the stored file. A TGA is read here (``formats.imagewrite``); the
others need Pillow, and the note says so when it is missing. docs/exporting.md.
"""

from __future__ import annotations

from ..formats import imagewrite
from .run import Job, Made


def export(job: Job) -> Made:
    """The PNG of a picture file."""
    if "png" not in job.formats:
        return Made()
    if job.data[:8] == imagewrite.PNG_MAGIC:
        return Made(notes=["already a PNG: the stored file is the PNG"])
    try:
        if job.data[:2] == b"\xff\xd8" or job.data[:3] == b"GIF" or job.data[:2] == b"BM":
            return _with_pillow(job)
        width, height, rgba = imagewrite.read_tga(job.data)
    except imagewrite.ImageError as exc:
        return Made(notes=[f"not turned into a PNG: {exc}"], status="failed")
    return Made({job.name(".png"): imagewrite.png_bytes(width, height, rgba)}, ["TGA decoded to RGBA"])


def _with_pillow(job: Job) -> Made:
    import io

    try:
        from PIL import Image
    except ImportError:
        return Made(
            notes=["JPEG, GIF and BMP need Pillow (pip install Pillow) for a PNG; exported as stored"], status="failed"
        )
    image = Image.open(io.BytesIO(job.data)).convert("RGBA")
    return Made(
        {job.name(".png"): imagewrite.png_bytes(image.width, image.height, image.tobytes())}, ["decoded with Pillow"]
    )
