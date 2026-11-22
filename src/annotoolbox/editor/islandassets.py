"""The island editor's models as files: a mesh as glTF, a texture as PNG, an effect's texture, a banner's cards.

``CIslandAssets`` is the mixin ``IslandEditor`` takes these from; it needs the editor's ``files``, ``texture_file``,
``texture_failure`` store and its cache (``_cached``, ``_lock``). Meshes and textures are converted once and kept in
the cache (``docs/island-editor.md``).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ..infra.buildcache import write_atomic
from ..models import fileresolve
from ..render.particlefx import load_rdp


@contextmanager
def whole_file(out: Path) -> Iterator[Path]:
    """A scratch path to make a cached file at: ``out`` appears whole when the block ends, so a killed editor never
    leaves half a file that later reads take for a finished one."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_cache_") as temp_dir:
        scratch = Path(temp_dir) / out.name
        yield scratch
        write_atomic(out, scratch.read_bytes())


class CIslandAssets:
    """The island editor's models as files (see the module)."""

    def banner(self, rdm: str) -> dict:
        """A banner-leaf mesh's cards (``rdmbanner.read_banner``): the view turns them to the camera."""
        from ..formats.rdmbanner import read_banner

        return read_banner(self.m_files.read(rdm))

    def mesh_glb(self, rdm: str, material: int | None = None) -> bytes:
        """The mesh as binary glTF, converted once (the key names the writer: an older cache holds other files);
        with ``material``, only its submeshes drawn with that material number."""
        out = self._cached(rdm + "|rdmglb" + (f"|{material}" if material is not None else ""), ".glb")
        with self._lock(out):
            if not out.is_file():
                self._convert_mesh(rdm, out, material)
        return out.read_bytes()

    def _convert_mesh(self, rdm: str, out: Path, material: int | None = None) -> None:
        from ..formats.rdmglb import rdm_to_glb
        from ..formats.rdmread import CRdmReadError

        try:
            write_atomic(out, rdm_to_glb(self.m_files.read(rdm), material))
        except CRdmReadError as error:
            raise FileNotFoundError(f"cannot read the mesh {rdm}: {error}") from error

    def texture_png(self, psd: str, is_normal: bool = False, is_opaque: bool = False, use_alpha: bool = False) -> bytes:
        """A texture as PNG; ``is_normal``: as a plain normal map (``full_normal``); ``is_opaque``: without
        its alpha (a ground texture's alpha is a blend weight, not transparency); ``use_alpha``: only that
        alpha, as grey (the game's blend mask, rendering/terrain-materials 1.3)."""
        out = self._cached(
            psd + ("|normal" if is_normal else "|opaque" if is_opaque else "|alpha" if use_alpha else ""), ".png"
        )
        with self._lock(out):
            if not out.is_file():
                try:
                    with whole_file(out) as scratch:
                        self._convert_texture(psd, scratch)
                        finish_png(scratch, is_normal, is_opaque, use_alpha)
                except (OSError, KeyError, ValueError) as error:  # listed in the Checks tab, drawn as missing
                    self.texture_failure(psd, error)
                    raise
        return out.read_bytes()

    def texture_failure(self, name: str, error: Exception) -> None:
        """Keep why a texture did not load (``file_problems``)."""
        self.__dict__.setdefault("m_texture_failures", {})[name] = f"{type(error).__name__}: {error}"

    def effect(self, path: str) -> list:
        """The effects of a baked particle file (``particlefx``), read from the mod first, then the game."""
        return [load_rdp(self.m_files.read(path), path)]

    def effect_texture_png(self, dds: str) -> bytes:
        """The texture of an effect (the file the game loads for its reference) as PNG, made once."""
        out = self._cached(dds + "|effect", ".png")
        with self._lock(out):
            if not out.is_file():
                try:
                    with whole_file(out) as scratch:
                        self._dds_to_png(self.texture_file(dds), scratch)
                except (OSError, KeyError, ValueError) as error:
                    self.texture_failure(dds, error)
                    raise
        return out.read_bytes()

    def _convert_texture(self, psd: str, out: Path) -> None:
        self._dds_to_png(self.texture_file(psd), out)

    def _dds_to_png(self, dds: str, out: Path) -> None:
        """A texture file as PNG, decoded in process (``fileresolve.decode``: every format the games use); texconv
        only for a file those decoders do not read."""
        from PIL import Image

        from ..models.textures import dds_to_png

        data = self.m_files.read(dds)
        try:
            Image.fromarray(fileresolve.decode(data), "RGBA").save(out, "PNG")
            return
        except fileresolve.CDecodeError:
            pass
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="a2070_editor_") as temp_dir:
            source = Path(temp_dir) / Path(dds).name
            source.write_bytes(data)
            dds_to_png(source, Path(temp_dir)).replace(out)


def finish_png(png: Path, is_normal: bool, is_opaque: bool, use_alpha: bool = False) -> None:
    from PIL import Image

    if is_normal or is_opaque or use_alpha:
        with Image.open(png) as image:
            done = finish_image(image, is_normal, is_opaque, use_alpha)
        done.save(png, "PNG")


def finish_image(image, is_normal: bool, is_opaque: bool, use_alpha: bool = False):
    """``finish_png`` on a PIL image in memory: a plain normal map (``full_normal``), opaque RGB or the alpha as RGB."""
    if is_normal:
        return full_normal(image)
    if is_opaque:
        return image.convert("RGB")
    if use_alpha:
        return image.convert("RGBA").getchannel("A").convert("RGB")
    return image


def full_normal(image):
    """An Anno normal map (RG normal, B empty, A gloss) as a plain one: B rebuilt, A opaque. An image
    decoded with premultiplied alpha blacked out the whole normal where the gloss was near 0."""
    from PIL import Image

    rgba = np.asarray(image.convert("RGBA"), np.float32)
    normal_xy = rgba[..., :2] / 127.5 - 1
    z = np.sqrt(np.clip(1 - (normal_xy**2).sum(axis=-1), 0, 1))
    rgb = np.dstack([rgba[..., :2], (z + 1) * 127.5])
    return Image.fromarray(np.clip(np.round(rgb), 0, 255).astype(np.uint8), "RGB")
