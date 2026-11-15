"""Picking in the island view: every shown instance drawn as its number into the one pixel under the cursor, the
ground hiding what lies behind it; a model the game's look draws is picked by its parts' boxes (island-117-view.md
"Speed", part 6).

A part of ``Renderer`` (``desktoprender``), as ``desktopframe``: it uses the renderer's programs and batches.
"""

from __future__ import annotations

import numpy as np

from ...render.camera import OrbitCamera, gl
from ...render.modellibrary import BANNER_SHADER


class Picking:
    """The id picture (``pick_draw``) and the entry it names (``picked``) of ``Renderer``."""

    def pick(self, camera: OrbitCamera, size: tuple[int, int], x: float, y: float, opts):
        """The entry under pixel (x, y) (from the top left) of a ``size`` frame, or None (``pick_draw``)."""
        tex, table = self.pick_draw(camera, size, x, y, opts)
        return self.picked(tex.read(), table)

    def pick_draw(self, camera: OrbitCamera, size: tuple[int, int], x: float, y: float, opts):
        """Each instance drawn as its number into one pixel: (that texture, the number ranges) for ``picked``."""
        ctx = self.ctx
        if self._pick is None:
            tex = ctx.texture((1, 1), 4)
            self._pick = (tex, ctx.framebuffer([tex], ctx.depth_renderbuffer((1, 1))))
        (tex, fbo), (w, h) = self._pick, size
        nx, ny = 2 * (x + 0.5) / w - 1, 1 - 2 * (y + 0.5) / h
        zoom = np.identity(4, "f4")
        zoom[0, 0], zoom[1, 1], zoom[0, 3], zoom[1, 3] = w, h, -nx * w, -ny * h
        view = camera.view()
        view_proj = zoom @ camera.proj(w / max(1, h)) @ view
        fbo.use()
        ctx.viewport = (0, 0, 1, 1)
        fbo.clear(0, 0, 0, 0, depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        prog = self.prog["terrain"]  # the ground hides what lies behind it (it writes 0)
        prog["viewProj"].write(gl(view_proj))
        fbo.color_mask = (False, False, False, False)
        self.terrain_vao.render()
        fbo.color_mask = (True, True, True, True)
        table, base = [], 0
        for b in self.batches.values():
            if not b.count or not b.parts or not self._layer_on(b, opts):
                continue
            table.append((base, b))
            for p, vaos in b.vaos:
                self._pick_part(b, p, vaos, view, view_proj, base)
            base += b.count
        return tex, table

    def _pick_part(self, b, p: dict, vaos: dict, view, view_proj, base: int) -> None:
        """One part's instances as their numbers; a box (the game's look draws the model) without its map: the map's
        cut-outs are not on a box, and reading it would load the map the game's look does not need."""
        pp = self.prog["pick_" + ("banner" if p["shader"] == BANNER_SHADER else "model")]
        pp["viewProj"].write(gl(view_proj))
        if "view" in self._names(pp):
            pp["view"].write(gl(view))
        pp["part"].write(gl(p["matrix"]))
        pp["base"].value = base
        t = self._gpu_texture("d:" + p["diffuse"], True) if p.get("diffuse") and not b.lean else None
        pp["textured"].value = t is not None
        (t or self._dummy).use(0)
        pp["diffuseTex"].value = 0
        vaos["pick"].render(instances=b.count)

    @staticmethod
    def picked(px, table: list):
        """The entry a read id pixel (RGBA bytes) names in ``table`` (``pick_draw``), or None."""
        got = px[0] | px[1] << 8 | px[2] << 16
        start, b = next(((start, b) for start, b in reversed(table) if got > start), (0, None))
        return b.entries[got - start - 1] if b is not None and 0 < got - start <= len(b.entries) else None
