"""Picking in the island view: every shown instance drawn as its number into the one pixel under the cursor, the
ground hiding what lies behind it; a model the game's look draws is picked by its parts' boxes (island-117-view.md
"Speed", part 6).

A part of ``Renderer`` (``desktoprender``), as ``desktopframe``: it uses the renderer's programs and batches.
"""

from __future__ import annotations

import numpy as np

from ...render.camera import COrbitCamera, gl
from ...render.modellibrary import BANNER_SHADER


class CPicking:
    """The id picture (``pick_draw``) and the entry it names (``picked``) of ``Renderer``."""

    def pick(self, camera: COrbitCamera, size_px: tuple[int, int], x: float, y: float, options):
        """The entry under pixel (x, y) (from the top left) of a ``size_px`` frame, or None (``pick_draw``)."""
        texture, table = self.pick_draw(camera, size_px, x, y, options)
        return self.picked(texture.read(), table)

    def pick_draw(self, camera: COrbitCamera, size_px: tuple[int, int], x: float, y: float, options):
        """Each instance drawn as its number into one pixel: (that texture, the number ranges) for ``picked``."""
        ctx = self.m_ctx
        if self._m_pick is None:
            texture = ctx.texture((1, 1), 4)
            self._m_pick = (texture, ctx.framebuffer([texture], ctx.depth_renderbuffer((1, 1))))
        (texture, fbo), (width_px, height_px) = self._m_pick, size_px
        ndc_x, ndc_y = 2 * (x + 0.5) / width_px - 1, 1 - 2 * (y + 0.5) / height_px
        zoom = np.identity(4, "f4")
        zoom[0, 0], zoom[1, 1], zoom[0, 3], zoom[1, 3] = width_px, height_px, -ndc_x * width_px, -ndc_y * height_px
        view = camera.view()
        view_proj = zoom @ camera.proj(width_px / max(1, height_px)) @ view
        fbo.use()
        ctx.viewport = (0, 0, 1, 1)
        fbo.clear(0, 0, 0, 0, depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        program = self.m_prog["terrain"]  # the ground hides what lies behind it (it writes 0)
        program["viewProj"].write(gl(view_proj))
        fbo.color_mask = (False, False, False, False)
        self.m_terrain_vao.render()
        fbo.color_mask = (True, True, True, True)
        table, base = [], 0
        for batch in self.m_batches.values():
            if not batch.m_count or not batch.m_parts or not self._layer_on(batch, options):
                continue
            table.append((base, batch))
            for part, vaos in batch.m_vaos:
                self._pick_part(batch, part, vaos, view, view_proj, base)
            base += batch.m_count
        return texture, table

    def _pick_part(self, batch, part: dict, vaos: dict, view, view_proj, base: int) -> None:
        """One part's instances as their numbers; a box (the game's look draws the model) without its map: the map's
        cut-outs are not on a box, and reading it would load the map the game's look does not need."""
        program = self.m_prog["pick_" + ("banner" if part["shader"] == BANNER_SHADER else "model")]
        program["viewProj"].write(gl(view_proj))
        if "view" in self._names(program):
            program["view"].write(gl(view))
        program["part"].write(gl(part["matrix"]))
        program["base"].value = base
        texture = self._gpu_texture("d:" + part["diffuse"], True) if part.get("diffuse") and not batch.m_lean else None
        program["textured"].value = texture is not None
        (texture or self._m_dummy).use(0)
        program["diffuseTex"].value = 0
        vaos["pick"].render(instances=batch.m_count)

    @staticmethod
    def picked(pixel, table: list):
        """The entry a read id pixel (RGBA bytes) names in ``table`` (``pick_draw``), or None."""
        got = pixel[0] | pixel[1] << 8 | pixel[2] << 16
        start, batch = next(((start, batch) for start, batch in reversed(table) if got > start), (0, None))
        return (
            batch.m_entries[got - start - 1] if batch is not None and 0 < got - start <= len(batch.m_entries) else None
        )
