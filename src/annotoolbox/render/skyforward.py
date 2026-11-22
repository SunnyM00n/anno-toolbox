"""Anno 2205's and 1800's visible sky by their own ``sky.fx`` (``SkyBoxAtmosphere``): the haze's sky colour under the
setting's sky box and its night box, drawn on a sphere around the camera into a target of its own and put into the
view at the far plane before the models' composite, so the view's own ground stays in front of it.

``CSkyForward(ctx, stages, state, dummies)`` is the draw (``render(target, size_px, camera, values, maps)``); ``box_maps`` names the
setting's cube files by the shaders' texture names, ``sky_values`` the sky's own constants. What is the game's and
what is chosen: docs/rendering/shader-compile.md section 11, "The visible sky".
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .ambientforward import SLight, cube_turn, environment_turn
from .frameforward import fill_frame
from .fxcdraw import CFxcDraw, mirrored_turn
from .gamedraw import CProgram
from .nativepass import CComposite, draw_pass
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


def box_maps(light: SLight, game: str) -> dict:
    """The setting's cube files by the texture names the game's sky reads: 2205's day box, 1800's sparse and dense
    cloud boxes (the dense one the sparse where the setting names none), the night box."""
    day = {"cSkyBox": light.skybox} if game == "anno2205" else {
        "cCloudBoxSparse": light.skybox, "cCloudBoxDense": light.cloud_dense or light.skybox}  # fmt: skip
    return {texture_name: cube for texture_name, cube in {**day, "cSkyBoxNight": light.skybox_night}.items() if cube}


def sky_values(light: SLight, clouds: bool = True, mirrored: bool = False) -> dict:
    """The sky's own constants: the boxes' turn (the prefiltered cubes', ``cube_turn``; ``mirrored``: boxes uploaded
    mirrored, the setting's turn mirrored, ``fxcdraw.mirrored_turn``) and their visibility (2205's
    ``cSkyBoxVisibility``, 1800's ``cSkyCloudDensity``; 0 with the clouds off)."""
    turn = mirrored_turn(environment_turn(light)) if mirrored else cube_turn(light)
    return {"cMtxSkyRotDay": turn, "cMtxSkyRotNight": turn, "cSkyBoxVisibility": 1.0 if clouds else 0.0,
            "cSkyCloudDensity": light.sky_cloud_density if clouds else 0.0}  # fmt: skip


class CSkyForward:
    """The sphere drawn by the technique's shaders (``stages``: its vertex and pixel stage) into a half-float target
    of the frame's size, and the composite that puts it into the view (module docstring)."""

    def __init__(self, ctx, stages, state, dummies) -> None:
        self.m_ctx = ctx
        # no depth of its own (the models go over it in the composite); both faces: the view's mirror turns the winding
        self.m_draw_call = CFxcDraw(
            ctx, CProgram(*stages), dome(), replace(state, cull="none", depth_test=False), dummies
        )
        # a box still loading (or none named) reads clear: the haze's sky alone, not a white box
        self.m_draw_call.m_fallbacks = dict.fromkeys(BOXES, (0, 0, 0, 0))
        self.m_composite = CComposite(ctx, COMPOSITE_FS)
        self.m_scale = 1.0  # the composite's factor: 4 for 1800's sky without its post chain (``postforward.STORED``)
        self.m_size_px = self.m_texture = self.m_fbo = None

    def _target(self, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        self.m_texture = self.m_ctx.native_texture(size_px, QRhiTexture.Format.RGBA16F)
        self.m_fbo = self.m_ctx.framebuffer([self.m_texture])
        self.m_size_px = size_px

    def render(self, target, size_px: tuple[int, int], camera: gs.SCamera, values: dict, maps: dict) -> None:
        """The sky with the frame's constants ``values`` (``ambientforward``'s light and haze, ``sky_values``) and its
        boxes ``maps`` (texture name -> cube), then into ``target`` behind what the view drew."""
        self._target(size_px)
        draw = self.m_draw_call

        def fill(constants: dict) -> None:
            fill_frame(constants, camera, values)

        draw_pass(self.m_ctx, self.m_fbo, lambda render_pass, signature, batch: draw.prepare(render_pass, signature, batch, maps, fill),
                  lambda command_buffer: draw.draw(command_buffer, size_px))  # fmt: skip
        flags = {"flip": bool(self.m_ctx.m_flip_offscreen), "scale": float(self.m_scale)}
        self.m_composite.draw(target, {"gSky": self.m_texture}, flags, depth_func="<=")

    def _drop(self) -> None:
        for texture in (self.m_fbo, self.m_texture):
            if texture is not None:
                texture.release()
        self.m_size_px = self.m_texture = self.m_fbo = None

    def release(self) -> None:
        self._drop()
        self.m_draw_call.release()
        self.m_composite.release()
