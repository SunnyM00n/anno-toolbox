"""Anno 2205's and 1800's visible sky by their own ``sky.fx`` (``SkyBoxAtmosphere``): the haze's sky colour under the
setting's sky box and its night box, drawn on a sphere around the camera into a target of its own and put into the
view at the far plane before the models' composite, so the view's own ground stays in front of it.

``SkyForward(ctx, stages, state, dummies)`` is the draw (``render(target, size, cam, values, maps)``); ``box_maps`` names the
setting's cube files by the shaders' texture names, ``sky_values`` the sky's own constants. What is the game's and
what is chosen: docs/rendering/shader-compile.md section 11, "The visible sky".
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .ambientforward import Light, cube_turn, environment_turn
from .frameforward import fill_frame
from .fxcdraw import FxcDraw, mirrored_turn
from .gamedraw import Program
from .nativepass import Composite, draw_pass
from .sky2070 import dome

FX = "data/shaders/fx/sky.fx"
TECHNIQUE = "SkyBoxAtmosphere"
BOXES = ("cSkyBox", "cCloudBoxSparse", "cCloudBoxDense", "cSkyBoxNight")  # the cubes the two games' skies read
# the sky's picture into the view where nothing nearer was drawn (tested less or equal at the far plane)
COMPOSITE_FS = """
#version 330
uniform sampler2D gSky; uniform bool flip; uniform float scale;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    color = vec4(texture(gSky, uv).rgb * scale, 1.0);
    gl_FragDepth = 1.0;
}
"""


def box_maps(light: Light, game: str) -> dict:
    """The setting's cube files by the texture names the game's sky reads: 2205's day box, 1800's sparse and dense
    cloud boxes (the dense one the sparse where the setting names none), the night box."""
    day = {"cSkyBox": light.skybox} if game == "anno2205" else {
        "cCloudBoxSparse": light.skybox, "cCloudBoxDense": light.cloud_dense or light.skybox}  # fmt: skip
    return {k: v for k, v in {**day, "cSkyBoxNight": light.skybox_night}.items() if v}


def sky_values(light: Light, clouds: bool = True, mirrored: bool = False) -> dict:
    """The sky's own constants: the boxes' turn (the prefiltered cubes', ``cube_turn``; ``mirrored``: boxes uploaded
    mirrored, the setting's turn mirrored, ``fxcdraw.mirrored_turn``) and their visibility (2205's
    ``cSkyBoxVisibility``, 1800's ``cSkyCloudDensity``; 0 with the clouds off)."""
    turn = mirrored_turn(environment_turn(light)) if mirrored else cube_turn(light)
    return {"cMtxSkyRotDay": turn, "cMtxSkyRotNight": turn, "cSkyBoxVisibility": 1.0 if clouds else 0.0,
            "cSkyCloudDensity": light.sky_cloud_density if clouds else 0.0}  # fmt: skip


class SkyForward:
    """The sphere drawn by the technique's shaders (``stages``: its vertex and pixel stage) into a half-float target
    of the frame's size, and the composite that puts it into the view (module docstring)."""

    def __init__(self, ctx, stages, state, dummies) -> None:
        self.ctx = ctx
        # no depth of its own (the models go over it in the composite); both faces: the view's mirror turns the winding
        self.draw_call = FxcDraw(ctx, Program(*stages), dome(), replace(state, cull="none", depth_test=False), dummies)
        # a box still loading (or none named) reads clear: the haze's sky alone, not a white box
        self.draw_call.fallbacks = dict.fromkeys(BOXES, (0, 0, 0, 0))
        self.composite = Composite(ctx, COMPOSITE_FS)
        self.scale = 1.0  # the composite's factor: 4 for 1800's sky without its post chain (``postforward.STORED``)
        self.size = self.texture = self.fbo = None

    def _target(self, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        self.texture = self.ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        self.fbo = self.ctx.framebuffer([self.texture])
        self.size = size

    def render(self, target, size: tuple[int, int], cam: gs.Camera, values: dict, maps: dict) -> None:
        """The sky with the frame's constants ``values`` (``ambientforward``'s light and haze, ``sky_values``) and its
        boxes ``maps`` (texture name -> cube), then into ``target`` behind what the view drew."""
        self._target(size)
        draw = self.draw_call

        def fill(c: dict) -> None:
            fill_frame(c, cam, values)

        draw_pass(self.ctx, self.fbo, lambda rp, signature, batch: draw.prepare(rp, signature, batch, maps, fill),
                  lambda cb: draw.draw(cb, size))  # fmt: skip
        flags = {"flip": bool(self.ctx.flip_offscreen), "scale": float(self.scale)}
        self.composite.draw(target, {"gSky": self.texture}, flags, depth_func="<=")

    def _drop(self) -> None:
        for t in (self.fbo, self.texture):
            if t is not None:
                t.release()
        self.size = self.texture = self.fbo = None

    def release(self) -> None:
        self._drop()
        self.draw_call.release()
        self.composite.release()
