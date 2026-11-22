"""Anno 1800's screen-space reflections on its model water by its own shaders: the water's ``ScreenSpaceReflect``
traced at half size over the last frame's copies into a colour and a gloss target, ``posteffect_ssr.fx``'s
``SSRBlurX`` and ``SSRBlurY_TAA`` (with the last result) over them, read by the water's colour pass as
``cScreenReflectMap``, and ``SSRCopy`` copying the frame's colour and depth for the next frame at its end.

``compile_stages(shaders)`` runs in the background; ``CSsr1800(ctx, stages, dummies)`` is the part: ``trace(models,
camera, values, maps)`` before the frame's passes, ``copy(color, depth, camera)`` after them. docs/rendering/shader-compile.md
section 11, "1800's screen-space reflections".
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import CFxcDraw, CSharedBuffers, screen_pass
from .gamecompute import CComputeStage, groups, record
from .gamedraw import CProgram, screen_triangle
from .nativepass import draw_models

EFFECT = "data/shaders/fx/posteffect_ssr.fx"
TECHNIQUE = "ScreenSpaceReflect"  # the model water's trace
BLUR_X, BLUR_Y, COPY = "SSRBlurX", "SSRBlurY_TAA", "SSRCopy"
FACTOR = 0.5  # cScreenReflectResFactor (taken from how the game behaves)
GROUP = 16  # csSSRCopyDefault's thread group
TextureFormat = QRhiTexture.Format


def compile_stages(shaders) -> dict:
    """In the background: the two blurs' (vertex, pixel) stages and states, the copy's compute stage."""
    out: dict = {technique: (shaders.effect_pass(EFFECT, (), technique), shaders.technique_state(EFFECT, (), technique))
                 for technique in (BLUR_X, BLUR_Y)}  # fmt: skip
    out[COPY] = shaders.call_stage(EFFECT, (), COPY)
    return out


def half(size_px: tuple[int, int]) -> tuple[int, int]:
    """The reflection's targets' size: the picture's times ``FACTOR``."""
    return (max(1, int(size_px[0] * FACTOR)), max(1, int(size_px[1] * FACTOR)))


def previous_values(camera: gs.SCamera) -> dict:
    """``cbCamera``'s last-frame members for the camera the copies were made with (view space z ahead, as the
    frame's ``cMtxView``)."""
    left_handed = gs.left_handed(camera)
    return {
        "cMtxViewPrev": left_handed.view,
        "cMtxProjPrev": left_handed.projection,
        "cMtxViewProjPrev": camera.view @ camera.projection,
    }


def same_camera(first: gs.SCamera, second: gs.SCamera) -> bool:
    return (
        tuple(first.size_px) == tuple(second.size_px)
        and np.array_equal(first.view, second.view)
        and np.array_equal(first.projection, second.projection)
    )


class CSsr1800:
    """The blurs, the copy and the half-size targets (module docstring); ``pending``: the last frame's camera was
    another, so the view draws once more."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.m_ctx, mesh = ctx, screen_triangle()

        def screen(technique: str) -> CFxcDraw:
            (vertex_shader, pixel_shader), state = stages[technique]
            draw = CFxcDraw(ctx, CProgram(vertex_shader, pixel_shader), mesh, state, dummies)
            draw.m_fallbacks = {"cScreenReflectLastFrameBlur": (0, 0, 0, 0)}  # no last result yet: none to mix in
            return draw

        self.m_blur_x = screen(BLUR_X)
        self.m_blur_y = [screen(BLUR_Y), screen(BLUR_Y)]  # one per result: the ping-pong keeps each one's bindings
        self.m_copier = CComputeStage(ctx.m_rhi, stages[COPY])
        self.m_shared = CSharedBuffers(ctx)  # the trace's frame buffers (its viewport is the half size's)
        self.m_size_px, self.m_targets, self._m_made = None, {}, []
        self.m_counter = 0  # cScreenReflectFrameCounter
        self.m_last: gs.SCamera | None = None  # the camera of the copies (None: none yet)
        self.m_results = 0  # the results the blur wrote since the targets were made
        self.m_pending = False

    @property
    def m_error(self) -> str:
        return self.m_copier.m_error or next(
            (draw.m_error for draw in (self.m_blur_x, *self.m_blur_y) if draw.m_error), ""
        )

    def _targets(self, size_px: tuple[int, int]) -> dict:
        if self.m_size_px == size_px:
            return self.m_targets
        self._drop()
        ctx, small, made = self.m_ctx, half(size_px), self._m_made

        def kept(resource):  # released in the reverse order: a framebuffer before its textures
            made.append(resource)
            return resource

        def tex(texture_format=TextureFormat.RGBA8, srgb=False, storage=False):
            return kept(ctx.native_texture(small, texture_format, srgb=srgb, storage=storage))

        def blurred():  # Blur0 and the results: sRGB, a framebuffer each
            out = tex(srgb=True)
            return out, kept(ctx.framebuffer([out]))

        color, gloss, depth = tex(srgb=True), tex(), kept(ctx.depth_renderbuffer(small))
        self.m_targets = {"color": color, "gloss": gloss, "depth": depth,
                        "trace": kept(ctx.framebuffer([color, gloss], depth)), "blur0": blurred(),
                        "results": (blurred(), blurred()), "copy_color": tex(storage=True),
                        "copy_depth": tex(TextureFormat.R16, storage=True)}  # fmt: skip
        self.m_copier.m_res.invalidate()
        self.m_size_px = size_px
        return self.m_targets

    def frame_values(self) -> dict:
        """``cbPerFrame``'s reflection members while it is on."""
        return {"cScreenReflectEnabled": 1.0, "cScreenReflectResFactor": FACTOR,
                "cScreenReflectFrameCounter": self.m_counter}  # fmt: skip

    def trace(self, models: list, camera: gs.SCamera, values: dict, maps: dict) -> tuple[dict, dict] | None:
        """The trace of ``models`` (``frameforward.CForwardModel``s with their ``ssr`` draw) and the blurs: the colour
        pass's values and maps, None while there are no copies of a last frame of this size (the water reflects
        none)."""
        size = (int(camera.size_px[0]), int(camera.size_px[1]))
        target_set, last = self._targets(size), self.m_last
        self.m_pending = last is None or not same_camera(last, camera)
        if last is None:
            return None
        small = replace(camera, size_px=half(size))
        frame = {**values, **self.frame_values(), **previous_values(last)}
        copies = {
            "cLastFrameColorMap": target_set["copy_color"].native,
            "cLastFrameDepthMap": target_set["copy_depth"].native,
        }
        self.m_shared.begin()
        for model in models:
            model.m_ssr.m_shared = self.m_shared
        draw_models(
            self.m_ctx,
            target_set["trace"],
            models,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, small, frame, {**maps, **copies}, model.m_ssr
            ),
            lambda model, command_buffer: model.m_ssr.draw(command_buffer, small.size_px),
            clear=True,
            targets=2,
        )
        result = self._blur(small, frame)
        return self.frame_values(), {"cScreenReflectMap": result, "cScreenReflectNormalMap": target_set["gloss"].native}

    def _blur(self, small: gs.SCamera, frame: dict):
        """``SSRBlurX`` into Blur0, ``SSRBlurY_TAA`` into this frame's result with the other one (last frame's)."""
        targets, result_index = self.m_targets, self.m_counter & 1
        width, height = small.size_px
        values = {
            **frame,
            **gs.camera_values(small),
            "cViewport": [0, 0, width, height],
            "cViewportCropFactor": [1.0, 1.0],
        }
        inputs = {"cScreenReflectColorMap": targets["color"].native, "cScreenReflectNormalMap": targets["gloss"].native,
                  "cScreenReflectDepthMap": targets["depth"].native}  # fmt: skip
        screen_pass(self.m_ctx, self.m_blur_x, targets["blur0"][1], inputs, values, small.size_px)
        inputs["cScreenReflectFirstBlurPass"] = targets["blur0"][0].native
        if self.m_results:
            inputs["cScreenReflectLastFrameBlur"] = targets["results"][1 - result_index][0].native
        screen_pass(
            self.m_ctx, self.m_blur_y[result_index], targets["results"][result_index][1], inputs, values, small.size_px
        )
        self.m_results += 1
        return targets["results"][result_index][0].native

    def copy(self, color, depth, camera: gs.SCamera) -> None:
        """``SSRCopy``: the frame's ``color`` and ``depth`` (device textures of ``camera.size_px``) for the next frame, at
        this frame's pixel offset; ``camera`` is then the last frame's and the counter goes on."""
        targets = self._targets((int(camera.size_px[0]), int(camera.size_px[1])))
        width, height = half(self.m_size_px)
        inputs = {"cDefaultMRT": color, "cDefaultMRTDepth": depth, "cScreenReflectColorCopy": targets["copy_color"].native,
                  "cScreenReflectDepthCopy": targets["copy_depth"].native}  # fmt: skip
        values = self.frame_values()
        record(
            self.m_ctx,
            self.m_copier,
            inputs,
            lambda constants: gs.set_values([constants], values),
            (groups(width, GROUP), groups(height, GROUP), 1),
        )
        self.m_last, self.m_counter = camera, self.m_counter + 1

    def _drop(self) -> None:
        for resource in reversed(self._m_made):
            resource.release()
        self._m_made.clear()
        self.m_targets, self.m_size_px, self.m_last, self.m_results = {}, None, None, 0

    def release(self) -> None:
        self._drop()
        for draw in (self.m_blur_x, *self.m_blur_y):
            draw.release()
        self.m_copier.release()
        self.m_shared.release()
