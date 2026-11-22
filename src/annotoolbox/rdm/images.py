"""Textures for an export: a PNG or JPEG as it is, a DDS as it is plus a PNG copy that modelling tools can show.

``texture`` is the entry (``rdm export --texture``). A DDS is decoded with ``fileresolve.decode`` (numpy; every format
``ddsdecode`` reads, the first level); without numpy the glTF carries the DDS alone (``MSFT_texture_dds``), which most
viewers cannot show. An ``.rdm`` holds no texture, so the import never writes one.
"""

from __future__ import annotations

from pathlib import Path

from ..formats import imagewrite
from .rdmgltf import STexture


def texture(path: Path, material: str | None = None) -> tuple[STexture, str]:
    """The texture of an image file and a note on what was done with it ("" when nothing needs saying)."""
    return texture_of(path.read_bytes(), path.name, material)


def texture_of(data: bytes, name: str, material: str | None = None) -> tuple[STexture, str]:
    """``texture`` for an image's bytes (``name`` is for the messages)."""
    path = Path(name)
    if data[:8] == imagewrite.PNG_MAGIC:
        return STexture(data, "image/png", material), ""
    if data[:2] == b"\xff\xd8":
        return STexture(data, "image/jpeg", material), ""
    if data[:4] != b"DDS ":
        raise ValueError(f"{path.name}: not a PNG, JPEG or DDS file")
    dds_only = STexture(data, "image/vnd-ms.dds", material)
    try:
        from ..models import fileresolve
    except ImportError:
        return dds_only, f"{path.name}: kept as DDS only (install numpy for a PNG copy)"
    try:
        rgba = fileresolve.decode(data, limit=1 << 16)
    except fileresolve.CDecodeError as error:
        return dds_only, f"{path.name}: kept as DDS only ({error})"
    copy = imagewrite.png_bytes(rgba.shape[1], rgba.shape[0], rgba.tobytes(), level=9)
    return STexture(data, "image/vnd-ms.dds", material, (copy, "image/png")), ""
