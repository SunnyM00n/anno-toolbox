"""Anno 1800's screen-space reflections on its model water by its own shaders: the water's ``ScreenSpaceReflect``
traced at half size over the last frame's copies into a colour and a gloss target, ``posteffect_ssr.fx``'s
``SSRBlurX`` and ``SSRBlurY_TAA`` (with the last result) over them, read by the water's colour pass as
``cScreenReflectMap``, and ``SSRCopy`` copying the frame's colour and depth for the next frame at its end.

``compile_stages(shaders)`` runs in the background; ``Ssr1800(ctx, stages, dummies)`` is the part: ``trace(models,
cam, values, maps)`` before the frame's passes, ``copy(color, depth, cam)`` after them. docs/rendering/shader-compile.md
section 11, "1800's screen-space reflections".
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import FxcDraw, SharedBuffers, screen_pass
from .gamecompute import ComputeStage, groups, record
from .gamedraw import Program, screen_triangle
from .nativepass import draw_models

EFFECT = "data/shaders/fx/posteffect_ssr.fx"
TECHNIQUE = "ScreenSpaceReflect"  # the model water's trace
BLUR_X, BLUR_Y, COPY = "SSRBlurX", "SSRBlurY_TAA", "SSRCopy"
FACTOR = 0.5  # cScreenReflectResFactor (read in the executable)
GROUP = 16  # csSSRCopyDefault's thread group
F = QRhiTexture.Format


def compile_stages(shaders) -> dict:
    """In the background: the two blurs' (vertex, pixel) stages and states, the copy's compute stage."""
    out: dict = {tech: (shaders.effect_pass(EFFECT, (), tech), shaders.technique_state(EFFECT, (), tech))
                 for tech in (BLUR_X, BLUR_Y)}  # fmt: skip
    out[COPY] = shaders.call_stage(EFFECT, (), COPY)
    return out


def half(size: tuple[int, int]) -> tuple[int, int]:
    """The reflection's targets' size: the picture's times ``FACTOR``."""
    return (max(1, int(size[0] * FACTOR)), max(1, int(size[1] * FACTOR)))


def previous_values(cam: gs.Camera) -> dict:
    """``cbCamera``'s last-frame members for the camera the copies were made with (view space z ahead, as the
    frame's ``cMtxView``)."""
    lh = gs.left_handed(cam)
    return {"cMtxViewPrev": lh.view, "cMtxProjPrev": lh.proj, "cMtxViewProjPrev": cam.view @ cam.proj}


def same_camera(a: gs.Camera, b: gs.Camera) -> bool:
    return tuple(a.size) == tuple(b.size) and np.array_equal(a.view, b.view) and np.array_equal(a.proj, b.proj)


class Ssr1800:
    """The blurs, the copy and the half-size targets (module docstring); ``pending``: the last frame's camera was
    another, so the view draws once more."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.ctx, mesh = ctx, screen_triangle()

        def screen(tech: str) -> FxcDraw:
            (vs, ps), state = stages[tech]
            draw = FxcDraw(ctx, Program(vs, ps), mesh, state, dummies)
            draw.fallbacks = {"cScreenReflectLastFrameBlur": (0, 0, 0, 0)}  # no last result yet: none to mix in
            return draw

        self.blur_x = screen(BLUR_X)
        self.blur_y = [screen(BLUR_Y), screen(BLUR_Y)]  # one per result: the ping-pong keeps each one's bindings
        self.copier = ComputeStage(ctx.rhi, stages[COPY])
        self.shared = SharedBuffers(ctx)  # the trace's frame buffers (its viewport is the half size's)
        self.size, self.targets, self._made = None, {}, []
        self.counter = 0  # cScreenReflectFrameCounter
        self.last: gs.Camera | None = None  # the camera of the copies (None: none yet)
        self.results = 0  # the results the blur wrote since the targets were made
        self.pending = False

    @property
    def error(self) -> str:
        return self.copier.error or next((d.error for d in (self.blur_x, *self.blur_y) if d.error), "")

    def _targets(self, size: tuple[int, int]) -> dict:
        if self.size == size:
            return self.targets
        self._drop()
        ctx, small, made = self.ctx, half(size), self._made

        def kept(resource):  # released in the reverse order: a framebuffer before its textures
            made.append(resource)
            return resource

        def tex(fmt=F.RGBA8, srgb=False, storage=False):
            return kept(ctx.native_texture(small, fmt, srgb=srgb, storage=storage))

        def blurred():  # Blur0 and the results: sRGB, a framebuffer each
            out = tex(srgb=True)
            return out, kept(ctx.framebuffer([out]))

        color, gloss, depth = tex(srgb=True), tex(), kept(ctx.depth_renderbuffer(small))
        self.targets = {"color": color, "gloss": gloss, "depth": depth,
                        "trace": kept(ctx.framebuffer([color, gloss], depth)), "blur0": blurred(),
                        "results": (blurred(), blurred()), "copy_color": tex(storage=True),
                        "copy_depth": tex(F.R16, storage=True)}  # fmt: skip
        self.copier.res.invalidate()
        self.size = size
        return self.targets

    def frame_values(self) -> dict:
        """``cbPerFrame``'s reflection members while it is on."""
        return {"cScreenReflectEnabled": 1.0, "cScreenReflectResFactor": FACTOR,
                "cScreenReflectFrameCounter": self.counter}  # fmt: skip

    def trace(self, models: list, cam: gs.Camera, values: dict, maps: dict) -> tuple[dict, dict] | None:
        """The trace of ``models`` (``frameforward.ForwardModel``s with their ``ssr`` draw) and the blurs: the colour
        pass's values and maps, None while there are no copies of a last frame of this size (the water reflects
        none)."""
        size = (int(cam.size[0]), int(cam.size[1]))
        t, last = self._targets(size), self.last
        self.pending = last is None or not same_camera(last, cam)
        if last is None:
            return None
        small = replace(cam, size=half(size))
        frame = {**values, **self.frame_values(), **previous_values(last)}
        copies = {"cLastFrameColorMap": t["copy_color"].native, "cLastFrameDepthMap": t["copy_depth"].native}
        self.shared.begin()
        for m in models:
            m.ssr.shared = self.shared
        draw_models(self.ctx, t["trace"], models, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, small, frame,
                    {**maps, **copies}, m.ssr), lambda m, cb: m.ssr.draw(cb, small.size), clear=True, targets=2)  # fmt: skip
        result = self._blur(small, frame)
        return self.frame_values(), {"cScreenReflectMap": result, "cScreenReflectNormalMap": t["gloss"].native}

    def _blur(self, small: gs.Camera, frame: dict):
        """``SSRBlurX`` into Blur0, ``SSRBlurY_TAA`` into this frame's result with the other one (last frame's)."""
        t, k = self.targets, self.counter & 1
        w, h = small.size
        values = {**frame, **gs.camera_values(small), "cViewport": [0, 0, w, h], "cViewportCropFactor": [1.0, 1.0]}
        inputs = {"cScreenReflectColorMap": t["color"].native, "cScreenReflectNormalMap": t["gloss"].native,
                  "cScreenReflectDepthMap": t["depth"].native}  # fmt: skip
        screen_pass(self.ctx, self.blur_x, t["blur0"][1], inputs, values, small.size)
        inputs["cScreenReflectFirstBlurPass"] = t["blur0"][0].native
        if self.results:
            inputs["cScreenReflectLastFrameBlur"] = t["results"][1 - k][0].native
        screen_pass(self.ctx, self.blur_y[k], t["results"][k][1], inputs, values, small.size)
        self.results += 1
        return t["results"][k][0].native

    def copy(self, color, depth, cam: gs.Camera) -> None:
        """``SSRCopy``: the frame's ``color`` and ``depth`` (device textures of ``cam.size``) for the next frame, at
        this frame's pixel offset; ``cam`` is then the last frame's and the counter goes on."""
        t = self._targets((int(cam.size[0]), int(cam.size[1])))
        w, h = half(self.size)
        inputs = {"cDefaultMRT": color, "cDefaultMRTDepth": depth, "cScreenReflectColorCopy": t["copy_color"].native,
                  "cScreenReflectDepthCopy": t["copy_depth"].native}  # fmt: skip
        values = self.frame_values()
        record(
            self.ctx, self.copier, inputs, lambda c: gs.set_values([c], values), (groups(w, GROUP), groups(h, GROUP), 1)
        )
        self.last, self.counter = cam, self.counter + 1

    def _drop(self) -> None:
        for resource in reversed(self._made):
            resource.release()
        self._made.clear()
        self.targets, self.size, self.last, self.results = {}, None, None, 0

    def release(self) -> None:
        self._drop()
        for d in (self.blur_x, *self.blur_y):
            d.release()
        self.copier.release()
        self.shared.release()
