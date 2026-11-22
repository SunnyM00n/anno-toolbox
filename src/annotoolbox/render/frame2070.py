"""Anno 2070's frame on the QRhi device (Direct3D 11) as far as a model viewer needs it: the models drawn into the
G-buffer by the game's own shaders (``Deferred``), the sun's shadow map by their ``Obj2Shadow`` technique, the
game's deferred lighting over it (``DeferredShadingStandardShadow``, without casters ``...NoShadow``), the sky dome
and the point and spot lights into the lit scene, and a composite into the view's target with the G-buffer's depth.

``CFrame2070(ctx, stages, dummies)`` is the entry: ``render(target, size_px, models, camera, sun, lamps)`` records the
passes; ``SParts2070`` holds the parts beyond the lighting; ``CModel2070`` is one mesh with its maps. The G-buffer and
the light: docs/rendering/frame-2070.md; the compile and the parts: docs/rendering/shader-compile.md section 10.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader, ocean2070, shadowforward
from .frameforward import DEPTH_COPY_FS, LIT_COPY_FS, NO_SHADOW, TEXTURE
from .fxcdraw import CDummies, CFxcDraw
from .gamedraw import CProgram, screen_triangle
from .nativepass import CComposite, draw_models, draw_pass
from .ocean2070 import SSea
from .sky2070 import sky_values

TextureFormat = QRhiTexture.Format
G_BUFFER = (
    TextureFormat.RGBA8,
    TextureFormat.RGBA8,
    TextureFormat.RGBA8,
)  # albedo (gamma values) and coverage, normal, glow / specular / environment
LIT = TextureFormat.RGBA16F  # the lit scene, linear and halved (LOW_QUALITY_HDR_ENABLED): range 0 to 2
SLOT_NAMES = {"diffuse": "cTexture0", "normal": "cTexture1", "environment": "cTexture2", "height": "cTexture3",
              "mask": "cTexture4", "ripples": "cTexture5"}  # fmt: skip
# what a map the material lacks reads as: a flat normal without specular, no reflection, no pollution
FALLBACKS = {"cTexture1": (128, 128, 255, 0), "cTexture4": (0, 0, 0, 255), "cPollutionMap": (0, 0, 0, 255),
             "cTexture2": (128, 128, 128, 255)}  # fmt: skip
FX = "data/system/shaders/fx/deferredshading.fx"
SHADING = (FX, "DeferredShadingNoShadow")
SHADOWED = "DeferredShadingStandardShadow"  # shadow method 0, the one map fitted round the casters
SHADOW_BIAS = 0.002  # cShadowBias.y in the fitted range's units (ours: the engine's values were not read)

COMPOSITE_FS = """
#version 330
uniform sampler2D gLit; uniform sampler2D gDepth; uniform bool flip; uniform bool sky;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    float depth = texture(gDepth, uv).r;
    if (depth >= 1.0) {
        if (sky) {  // the game's sky, where the view drew nothing nearer (tested less or equal at the far plane)
            color = vec4(texture(gLit, uv).rgb * 2.0, 1.0);
            gl_FragDepth = 1.0;
            return;
        }
        discard;
    }
    color = vec4(texture(gLit, uv).rgb * 2.0, 1.0);
    gl_FragDepth = depth;
}
"""
# the blended pass's layer (premultiplied, the scene's halved units) over the composited view; it goes over the view's
# own ground too, which the G-buffer's depth does not hold
BLEND_FS = """
#version 330
uniform sampler2D gBlend; uniform bool flip;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    vec4 c = texture(gBlend, uv);
    color = vec4(c.rgb * 2.0, c.a);
}
"""
CLEAR_FS = """
#version 330
in vec2 vNdc; out vec4 color;
void main() { color = vec4(0.0); }
"""


@dataclass(frozen=True)
class SSun2070:
    """The light of a frame as the engine's constants hold it: the colours in gamma values (``ambientmap.xml``,
    colour times power), the direction towards the sun in the view's space, the cloud shadow factor, the sky's
    clouds (and the seconds they turned by) and the post's mood colour and blur share."""

    direction: tuple[float, float, float]
    diffuse: tuple[float, float, float]
    ambient: tuple[float, float, float]
    inv_cloud: float = 0.6
    scatter_start: float = 0.095
    horizon_fall_off: float = 9.8
    distance_fall_off: float = 1.5
    zenith: tuple = (0.246, 0.464, 0.594)
    horizon: tuple = (0.672, 1.301, 1.483)
    cloud_color: tuple = (0.918, 0.929, 0.933)
    cloud_density: float = 0.08
    mood: tuple = (0.877, 0.867, 0.819, 0.2)
    blur: float = 1.0  # cPostBlur.x: 1 the bloom, 0 the menus' blur
    seconds: float = 0.0  # the view's time: the clouds' turn (sky2070.cloud_turns)


def sun_of(setting, direction, seconds: float, has_clouds: bool = True) -> SSun2070:
    """The frame's light of an ambient set (``sources.ambient2070``'s setting), the sun towards ``direction`` (the
    pass's axes) at the view's time; without ``clouds`` their density is 0 (``sky.fx`` then draws none)."""
    atmosphere = setting.atmosphere
    cloud = tuple(channel * setting.cloud_color_power for channel in atmosphere.cloud_color[:3])
    mood = (*setting.mood, setting.mood_color[3] if len(setting.mood_color) > 3 else 0.0)
    density = atmosphere.cloud_density if has_clouds else 0.0
    return SSun2070(
        tuple(direction), setting.sun, setting.ambient, setting.inv_cloud_shadow_fac, atmosphere.scatter_start,
        atmosphere.horizon_fall_off, atmosphere.distance_fall_off, setting.zenith, setting.horizon, cloud, density,
        mood, seconds=float(seconds),
    )  # fmt: skip


@dataclass
class SParts2070:
    """The frame's parts beyond the lighting, each None when it is off or did not compile: the shadowed lighting
    draw, the sky (``sky2070.CSkyDome``), the lights (``lights2070.CLightVolumes``), the sea (``ocean2070.COcean2070``)."""

    shadowed: CFxcDraw | None = None
    sky: object = None
    lights: object = None
    ocean: object = None

    def release(self) -> None:
        for part in (self.shadowed, self.sky, self.lights, self.ocean):
            if part is not None:
                part.release()


def fill_camera(constants: dict, camera: gameshader.SCamera, seconds: float = 0.0) -> None:
    """The camera buffers by name (every buffer the shader reads of them; the rest is left alone); ``cTimer`` the
    view's time as the engine's timers (in-game, wind and real time alike; no delta: a still frame)."""
    values = gameshader.camera_values(camera, gameshader.as_columns)
    values.update({"cMtxViewProjInvOrigin": values["cMtxViewProjInv"], "cVecViewPort": [0, 0, *camera.size_px],
                   "cFOV": [0.75, 1 / 0.75, 0.38, 0], "cTimer": [seconds, 0.0, seconds, seconds],
                   "cMtxViewProjTex": gameshader.as_columns(camera.view @ camera.projection @ TEXTURE)})  # fmt: skip
    gameshader.set_values(constants.values(), values)


def fill_light(constants: dict, sun: SSun2070, camera: gameshader.SCamera) -> None:
    """The light and scattering buffers: the sun and ambient colours, the haze from the set's atmosphere."""
    toward = np.asarray(sun.direction, float)
    values = {
        "cLgtAmbient": [*sun.ambient, 1], "cLgtDiffuse": [*sun.diffuse, 1], "cLgtDirection": [*-toward, 0],
        "cLgtDirectionInv": [*toward, 0], "cLgtInvCloudShadows": [sun.inv_cloud, 0, 0, 0],
        "cHorizonColor": list(sun.horizon), "cZenithColor": list(sun.zenith), "cSunColor": [0.133, 0.5, 0.575],
        "cHorizonFallOff": [sun.horizon_fall_off] * 3, "cSunFallOff": [8.5] * 3, "cDistanceFallOff": [sun.distance_fall_off] * 3,
        "cAtmosphericData0": [-0.2, 1.25, 0.665, 1 / 0.335],
        "cAtmosphericData1": [sun.scatter_start * camera.far, camera.far, 1 / (camera.far * (1 - sun.scatter_start)), 0.08],
    }  # fmt: skip
    gameshader.set_values(constants.values(), values)


def fitted(down, corners: np.ndarray, beyond=None, bias: float = SHADOW_BIAS) -> tuple:
    """One orthographic shadow map fitted round ``corners`` (``shadowforward.fit``; ``beyond``: receivers its depth
    reaches too) and ``bias`` in its depth range's units, scaled so that it stays the same distance however far the
    depth reaches: what the old engine's frames (2070, 1404, 1701) share."""
    found = shadowforward.fit(down, corners, beyond)
    if beyond is not None:
        own = shadowforward.fit(down, corners).values["cVecShadowVPDN"]
        bias *= float(np.linalg.norm(found.values["cVecShadowVPDN"][:3]) / np.linalg.norm(own[:3]))
    return found, bias


def shadow_values(down, corners: np.ndarray, matrix=gameshader.as_columns, beyond=None) -> dict:
    """``cbShadow`` for shadow method 0 with one orthographic map fitted round ``corners`` (``shadowforward.fit``;
    ``beyond``: receivers its depth reaches too): the pass's matrix, the lighting's matrix into the map, the plane the
    distance to the light is measured on and the bias (the same distance however far the depth reaches); ``matrix``
    turns ours into the shader's (2070's transposed, 1404's as they are)."""
    found, bias = fitted(down, corners, beyond)
    plane = found.values["cVecShadowVPDN"]
    return {"cMtxShadowVP0": matrix(found.view_proj), "cMtxShadowVPP0": matrix(found.view_proj @ TEXTURE),
            "cVecShadowVPD": plane, "cShadowBias": [0.0, bias, 0.0, 0.0], "cShadowMethod": [0.0, 0.0, 0.0, 0.0]}  # fmt: skip


def sun_shadow(ctx, shadow_map, casters: list, down, matrix=gameshader.as_columns, values_of=None, texture: str = "cShadowMap",
               beyond=None):  # fmt: skip
    """The casters' (models with a ``shadow`` draw, ``corners`` and ``fill_model``) shadow map drawn by their
    caster pass (the map made on first use), the colour pass's values and its map by name (``texture``); the old
    engine's frames (2070, 1404, 1701) share it. ``values_of(down, corners, beyond)`` gives the casters' and the
    colour pass's values (1701's); by default both are ``cbShadow``'s; ``beyond``: receivers that cast nothing (the
    sea)."""
    corners = np.concatenate([model.corners() for model in casters])
    if values_of:
        cast, values = values_of(down, corners, beyond)
    else:
        cast = values = shadow_values(down, corners, matrix, beyond)
    shadow_map = shadow_map or shadowforward.CShadowMap(ctx)
    shadow_map.render(casters, cast, lambda model, constants: model.fill_model(constants))
    return shadow_map, values, {texture: shadow_map.m_native}


def object_rows(world: np.ndarray, palette, bones: int = 0, color=(1.0, 1.0, 1.0)) -> tuple[list, int]:
    """The rows of ``cMtxMeshWorld`` for one object: a colour (rgb, or rgba: the player's, which the dye shader puts
    where its mask's green says; model water's colour and depth), the damage, then four rows per matrix (the place
    for a rigid mesh, place times the joint's matrix for a skin; ``bones`` copies of the place for a skinned mesh
    that plays no animation), and how many float4s that is."""
    world_matrix = np.asarray(world, float)
    matrices = [world_matrix] * max(1, bones) if palette is None else [world_matrix @ matrix for matrix in palette]
    rows = [[*color, 1.0][:4], [0.0, 0.0, 0.0, 0.0]]
    for matrix in matrices:
        rows += [list(matrix[i]) for i in range(4)]
    return rows, len(rows)


def unshadowed(ctx, shadow_map) -> tuple:
    """(the map, (``cbShadow`` and the map by name)) that leave every point lit, for the passes of shadow method 0
    without casters or with the shadow switched off (the blended pass, the sea): their technique always reads the map,
    and the zero matrix of an unfilled buffer divides 0 by 0 there (shader-compile.md 10, "Blended pass")."""
    shadow_map = shadow_map or shadowforward.CShadowMap(ctx)
    shadow_map.render([], {}, lambda model, constants: None)  # no casters: the map cleared to the far depth
    values = {"cMtxShadowVP0": gameshader.as_columns(NO_SHADOW), "cMtxShadowVPP0": gameshader.as_columns(NO_SHADOW), "cVecShadowVPD": [0.0] * 4,
              "cShadowBias": [0.0] * 4, "cShadowMethod": [0.0] * 4}  # fmt: skip
    return shadow_map, (values, {"cShadowMap": shadow_map.m_native})


def ocean_waves(ocean, sea: SSea, seconds: float) -> dict:
    """The wave maps of ``ocean`` for this moment (``Ocean2070.waves`` with ``sea``'s fixed maps), by the ocean's names."""
    return ocean.waves(seconds, {name: sea.maps[path] for name, path in ocean2070.WAVES.items()})


class SSeaInputs(NamedTuple):
    """What the sea's pass reads of the frame: the lit scene's and the depth's copies it refracts (``cTexture5`` and
    ``cTexture6``), the sun's shadow (its values and maps) and the wave maps made already this frame (None: made
    by the pass)."""

    copies: dict
    shadow: tuple
    waves: dict | None = None


def ocean_pass(
    ctx, framebuffer, size_px, camera: gameshader.SCamera, sun: SSun2070, ocean, sea: SSea, inputs: SSeaInputs
) -> None:
    """The sea by ``waterocean.fx`` (``ocean``: an ``ocean2070.COcean2070``) into ``framebuffer``: the wave maps and the sky
    cube made for this moment, then the chunks round ``sea``, refracting the frame's copies, lit by the sun with
    its shadow (``inputs``), tested and written against ``framebuffer``'s depth (shader-compile.md 10, "The ocean's
    waves")."""
    copies = inputs.copies
    waves = inputs.waves or ocean_waves(ocean, sea, sun.seconds)
    values, shadow_maps = inputs.shadow
    forward = np.asarray(camera.view, float)[:3, 2]

    def sky(constants: dict) -> None:
        fill_camera(constants, camera, sun.seconds)
        fill_light(constants, sun, camera)
        gameshader.set_values(constants.values(), sky_values(sun))

    maps = {**waves, "cTexture2": sea.maps[ocean2070.RIPPLES], "cTexture7": sea.maps[ocean2070.CAUSTICS],
            "tSkyEnv": ocean.sky_cube(sky), **copies, **shadow_maps}  # fmt: skip
    if sea.info_map is not None:
        maps["cTexture3"] = sea.info_map
    if sea.reflection is not None:
        maps["cTexture4"] = sea.reflection
    sea_values = ocean2070.ocean_values(ocean.m_water, sun.seconds, sun.direction, forward / np.linalg.norm(forward),
                                        sea.texel)  # fmt: skip
    extra = {**values, **water_values(camera, (1.0, 1.0, 1.0, 1.0)), **sea_values,
             "cWaterViewProjTex": gameshader.as_columns(camera.view @ camera.projection @ TEXTURE)}  # fmt: skip
    if sea.coast is not None:
        maps |= sea.coast.ocean_maps()
        extra |= sea.coast.ocean_values()
    draws = ocean.draws(ocean2070.chunk_rings(sea.centre, sea.reach, camera.eye))

    def fill(constants: dict, chunk_values: dict) -> None:
        fill_camera(constants, camera, sun.seconds)
        fill_light(constants, sun, camera)
        gameshader.set_values(constants.values(), {**extra, **chunk_values}, ("cWaterChunkData",))

    def prepare(render_pass, signature, batch) -> None:
        for chunk_draw, chunk_values in draws:
            chunk_draw.prepare(
                render_pass,
                signature,
                batch,
                maps,
                lambda constants, chunk_values=chunk_values: fill(constants, chunk_values),
            )

    def record(command_buffer) -> None:
        for chunk_draw, _own in draws:
            chunk_draw.draw(command_buffer, size_px)

    draw_pass(ctx, framebuffer, prepare, record, clear=False)


def water_values(camera: gameshader.SCamera, diffuse) -> dict:
    """What model water reads beyond the camera: the view depth it measures the water by, in the engine's
    left-handed view space (ahead is +z: ours looks down -z, and the depth behind the sheet would come out
    negative), and ``cMatDiffuse`` (the material's diffuse colour: its alpha is the water's clearness)."""
    left_handed_camera = gameshader.left_handed(camera)
    return {
        "cMtxView": gameshader.as_columns(left_handed_camera.view),
        "cMtxProjInv": gameshader.as_columns(np.linalg.inv(left_handed_camera.projection)),
        "cMatDiffuse": list(diffuse),
    }


class CMeshModel:
    """What the old engine's models (``CModel2070``, ``frame1404.CModel1404``) share: ``draw_call`` (the colour pass's
    ``CFxcDraw``), ``maps`` (the shader's texture name -> a QRhi texture or None while it loads), ``world``."""

    m_draw_call: CFxcDraw
    m_maps: dict
    m_world: np.ndarray

    def textures(self) -> dict:
        """The maps that are in."""
        return {name: texture for name, texture in self.m_maps.items() if texture is not None}

    def corners(self) -> np.ndarray:
        """The eight corners of the mesh's box where it stands (a shadow caster's, ``sun_shadow``)."""
        return shadowforward.corners(self.m_draw_call.box(), self.m_world)


class CModel2070(CMeshModel):
    """One mesh group of a model with its material's maps: ``draw`` (a ``CFxcDraw``), ``maps`` (the shader's texture
    name -> a QRhi texture or None while it loads), ``world`` (column vectors) and ``palette`` set per frame,
    ``shadow`` its ``Obj2Shadow`` draw (None: it casts none); ``water`` a model water sheet (drawn over the lit
    scene, not into the G-buffer) with its material's ``diffuse`` colour."""

    def __init__(self, draw: CFxcDraw, shadow: CFxcDraw | None = None) -> None:
        self.m_draw_call, self.m_maps, self.m_values, self.m_bones = draw, {}, {}, 0
        self.m_color = (1.0, 1.0, 1.0)  # the object colour: the player's on a dye material, rgba on water
        self.m_world, self.m_palette = np.identity(4), None
        self.m_shadow = shadow
        self.m_water, self.m_diffuse = False, (1.0, 1.0, 1.0, 1.0)
        self.m_blended = False  # drawn by DefaultBlend in the blended pass, after the sky
        # drawn in the game's axes, the mirror in the camera's projection: the colour pass's faces swap, the sun's
        # own view keeps them (shader-compile.md 11 "The game's own axes")
        for part, mirrored in ((draw, True), (shadow, False)):
            if part is not None:
                part.m_fallbacks, part.m_is_mirrored = dict(FALLBACKS), mirrored

    def fill_model(self, constants: dict) -> None:
        """The object's own rows (``object_rows``) and how many float4s one object takes."""
        rows, per_object = object_rows(self.m_world, self.m_palette, self.m_bones, self.m_color)
        if "cbMeshConstants" in constants:
            constants["cbMeshConstants"].set_rows("cMtxMeshWorld", rows[:256])
        gameshader.set_values(constants.values(), {"cUserConstant0": [per_object, 0, 0, 0]})

    def prepare(
        self,
        render_pass,
        signature,
        batch,
        camera: gameshader.SCamera,
        sun: SSun2070,
        frame_maps=None,
        frame_values=None,
    ) -> None:
        """The draw's resources and constants; ``frame_maps`` / ``frame_values``: the frame's textures and constants
        it reads besides its own (the water's copies of the lit scene and the depth, the blended pass's shadow)."""

        def fill(constants: dict) -> None:
            fill_camera(constants, camera, sun.seconds)
            fill_light(constants, sun, camera)
            self.fill_model(constants)
            if self.m_water:
                gameshader.set_values(constants.values(), water_values(camera, self.m_diffuse))
            if frame_values:
                gameshader.set_values(constants.values(), frame_values)

        self.m_draw_call.prepare(render_pass, signature, batch, {**self.textures(), **(frame_maps or {})}, fill)

    def draw(self, command_buffer, size_px) -> None:
        self.m_draw_call.draw(command_buffer, size_px)

    def release(self) -> None:
        self.m_draw_call.release()
        if self.m_shadow is not None:
            self.m_shadow.release()


def shading_pass(ctx, stages, dummies: CDummies) -> CFxcDraw:
    """The deferred lighting as a full-screen draw of the game's compiled shaders (``stages``: its ``SPassStages``)."""
    program = CProgram(stages.vertex_stage, stages.pixel_stage)
    return CFxcDraw(ctx, program, screen_triangle(), replace(stages.state, cull="none"), dummies)


class CFrame2070:
    """The G-buffer of one view and the passes over it (module docstring)."""

    def __init__(self, ctx, shading_stages, dummies: CDummies) -> None:
        self.m_ctx = ctx
        self.m_shading = shading_pass(ctx, shading_stages, dummies)
        self.m_parts = SParts2070()
        self.m_size_px: tuple[int, int] | None = None
        self.m_targets: list = []
        self.m_lit = self.m_depth = self.m_fbo = self.m_lit_fbo = self.m_lit_depth_fbo = self.m_read_depth = (
            self.m_read_fbo
        ) = None
        self.m_refraction = self.m_refraction_fbo = None
        self.m_blend = self.m_blend_fbo = self.m_blend_depth_fbo = None
        self.m_blend_copy = self.m_blend_clear = None  # the blended layer's composite and clear, made on first use
        self.m_composite = CComposite(ctx, COMPOSITE_FS)
        self.m_depth_copy = CComposite(ctx, DEPTH_COPY_FS)
        self.m_lit_copy = CComposite(ctx, LIT_COPY_FS)
        self.m_shadow_map: shadowforward.CShadowMap | None = None

    def _targets(self, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        ctx = self.m_ctx
        self.m_targets = [ctx.native_texture(size_px, texture_format) for texture_format in G_BUFFER]
        self.m_depth = ctx.depth_renderbuffer(size_px)
        self.m_fbo = ctx.framebuffer(self.m_targets, self.m_depth)
        self.m_lit = ctx.native_texture(size_px, LIT)
        self.m_lit_fbo = ctx.framebuffer([self.m_lit])
        self.m_lit_depth_fbo = ctx.framebuffer(
            [self.m_lit], self.m_depth
        )  # the sky and the lights: tested, not written
        self.m_read_depth = ctx.native_texture(
            size_px, TextureFormat.R32F
        )  # the depth the lights read while it is attached
        self.m_read_fbo = ctx.framebuffer([self.m_read_depth])
        self.m_refraction = ctx.native_texture(size_px, LIT)  # the lit scene's copy the water refracts
        self.m_refraction_fbo = ctx.framebuffer([self.m_refraction])
        self.m_blend = ctx.native_texture(size_px, LIT)  # the blended pass's layer (BLEND_FS)
        self.m_blend_fbo = ctx.framebuffer([self.m_blend])
        self.m_blend_depth_fbo = ctx.framebuffer([self.m_blend], self.m_depth)
        self.m_size_px = size_px

    def _shadow(self, models: list, sun: SSun2070, sea: SSea | None = None) -> tuple[dict, dict] | None:
        """The casters' shadow map and ``cbShadow`` (None without casters or a shadowed lighting); its depth reaches
        over the sea too, which casts none (``ocean2070.corners``)."""
        casters = [model for model in models if model.m_shadow is not None]
        if self.m_parts.shadowed is None or not casters:
            return None
        beyond = ocean2070.corners(sea) if sea is not None else None
        self.m_shadow_map, values, maps = sun_shadow(
            self.m_ctx, self.m_shadow_map, casters, -np.asarray(sun.direction), beyond=beyond
        )
        return values, maps

    def render(self, target, size_px: tuple[int, int], models: list, camera: gameshader.SCamera, sun: SSun2070, lamps=(),
               sea: SSea | None = None) -> None:  # fmt: skip
        """The G-buffer pass with ``models`` (``CModel2070``), the shadow map, the lighting, the sky and the lights
        (``lamps``: ``lightsforward.SLamp`` in the view's axes), then the composite into ``target``. The sea (``sea``,
        with ``parts.ocean``) and model water draw over the lit scene before the sky and the lights, as the engine's
        water step does."""
        self._targets(size_px)
        sea = sea if self.m_parts.ocean is not None else None
        water = [model for model in models if model.m_water]
        blended = [model for model in models if model.m_blended and not model.m_water]
        models = [model for model in models if not model.m_water and not model.m_blended]
        draw_models(self.m_ctx, self.m_fbo, models, lambda model, render_pass, signature, batch: model.prepare(render_pass, signature, batch, camera, sun),
                    lambda model, command_buffer: model.draw(command_buffer, size_px), targets=len(G_BUFFER))  # fmt: skip
        shadow = self._shadow(models, sun, sea)
        maps = {"cTexture0": self.m_targets[0].native, "cTexture1": self.m_targets[1].native,
                "cTexture2": self.m_depth.native, "cTexture3": self.m_targets[2].native, **(shadow[1] if shadow else {})}  # fmt: skip
        shading = self.m_parts.shadowed if shadow else self.m_shading

        def fill(constants: dict) -> None:
            fill_camera(constants, camera, sun.seconds)
            fill_light(constants, sun, camera)
            if shadow:
                gameshader.set_values(constants.values(), shadow[0])

        draw_pass(self.m_ctx, self.m_lit_fbo, lambda render_pass, signature, batch: shading.prepare(render_pass, signature, batch, maps,
                  fill), lambda command_buffer: shading.draw(command_buffer, size_px))  # fmt: skip
        if water or sea is not None:
            self._water(size_px, camera, sun, water, sea, (shadow or self._unshadowed()) if sea is not None else None)
        self._forward(size_px, camera, sun, list(lamps), maps)
        if blended:
            self._blended(size_px, camera, sun, blended, shadow or self._unshadowed())
        uniforms = {"flip": bool(self.m_ctx.m_flip_offscreen), "sky": self.m_parts.sky is not None}
        self.m_composite.draw(target, {"gLit": self.m_lit, "gDepth": self.m_depth}, uniforms,
                            depth_func="<=" if self.m_parts.sky is not None else None)  # fmt: skip
        if blended:
            ctx = self.m_ctx
            self.m_blend_copy.draw(target, {"gBlend": self.m_blend}, {"flip": bool(ctx.m_flip_offscreen)}, depth_test=False,
                                 blend=(ctx.ONE, ctx.ONE_MINUS_SRC_ALPHA))  # fmt: skip

    def _unshadowed(self) -> tuple[dict, dict]:
        """``unshadowed`` with this frame's map."""
        self.m_shadow_map, shadow = unshadowed(self.m_ctx, self.m_shadow_map)
        return shadow

    def _blended(self, size_px, camera: gameshader.SCamera, sun: SSun2070, models: list, shadow: tuple) -> None:
        """The blended pass (``DefaultBlend``) after the sky into a layer of its own: far to near, tested against the
        G-buffer's depth, not written; lit by the sun with its shadow (shader-compile.md 10, "Blended pass"). The
        layer goes over the composited view (``BLEND_FS``), so it covers the view's own ground as well."""
        self.m_blend_clear = self.m_blend_clear or CComposite(self.m_ctx, CLEAR_FS)
        self.m_blend_copy = self.m_blend_copy or CComposite(self.m_ctx, BLEND_FS)
        self.m_blend_clear.draw(
            self.m_blend_fbo, {}, {}, depth_test=False
        )  # the colour only: the depth is the G-buffer's
        eye = np.asarray(camera.eye, float)
        order = sorted(models, key=lambda model: -float(np.linalg.norm(np.asarray(model.m_world, float)[:3, 3] - eye)))
        values, maps = shadow
        draw_models(self.m_ctx, self.m_blend_depth_fbo, order,
                    lambda model, render_pass, signature, batch: model.prepare(render_pass, signature, batch, camera, sun, maps, values),
                    lambda model, command_buffer: model.draw(command_buffer, size_px), clear=False)  # fmt: skip

    def _water(
        self, size_px, camera: gameshader.SCamera, sun: SSun2070, water: list, sea: SSea | None, shadow: tuple | None
    ) -> None:
        """The water step over the lit scene: the scene and the depth copied once, which every water surface refracts
        and measures its depth against, then the sea (with ``shadow``: its values and maps) and model water
        (``model_water.fx``), drawn tested and written against the G-buffer's depth."""
        self.m_depth_copy.draw(self.m_read_fbo, {"gDepth": self.m_depth}, {}, depth_test=False)
        self.m_lit_copy.draw(self.m_refraction_fbo, {"gLit": self.m_lit}, {}, depth_test=False)
        if sea is not None:
            copies = {"cTexture5": self.m_refraction.native, "cTexture6": self.m_read_depth.native}
            inputs = SSeaInputs(copies, shadow)
            ocean_pass(self.m_ctx, self.m_lit_depth_fbo, size_px, camera, sun, self.m_parts.ocean, sea, inputs)
        maps = {"cRefractionColorBuffer": self.m_refraction.native, "cRefractionDepthBuffer": self.m_read_depth.native}
        draw_models(
            self.m_ctx,
            self.m_lit_depth_fbo,
            water,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, camera, sun, maps
            ),
            lambda model, command_buffer: model.draw(command_buffer, size_px),
            clear=False,
        )

    def _forward(self, size_px, camera: gameshader.SCamera, sun: SSun2070, lamps: list, maps: dict) -> None:
        """The sky and the lights into the lit scene over the G-buffer's depth (tested, not written)."""
        sky, lights = self.m_parts.sky, self.m_parts.lights
        draws = lights.frame(lamps, camera.eye, camera.near) if lights is not None and lamps else []
        if sky is None and not draws:
            return
        for light_draw in draws:  # the light volumes through the camera's mirrored projection: their faces swap
            light_draw.m_is_mirrored = camera.mirror
        if draws:
            self.m_depth_copy.draw(self.m_read_fbo, {"gDepth": self.m_depth}, {}, depth_test=False)
        light_maps = {**maps, "cTexture2": self.m_read_depth.native}
        sprites = lights.m_sprites if lights is not None else []

        def fill(constants: dict) -> None:
            fill_camera(constants, camera, sun.seconds)
            fill_light(constants, sun, camera)
            if sky is not None:
                gameshader.set_values(constants.values(), sky_values(sun))
            gameshader.set_values(constants.values(), {"cSpriteConfig": [3.0, 0.0, 0.0, 0.0]})
            if sprites:
                gameshader.set_values(constants.values(), {"cSpriteData": sprites}, ("cSpriteData",))

        def prepare(render_pass, signature, batch) -> None:
            if sky is not None:
                sky.m_draw_call.prepare(render_pass, signature, batch, sky.maps(), fill)
            for light_draw in draws:
                light_draw.prepare(render_pass, signature, batch, light_maps, fill)

        def record(command_buffer) -> None:
            if sky is not None:
                sky.m_draw_call.draw(command_buffer, size_px)
            for light_draw in draws:
                light_draw.draw(command_buffer, size_px)

        draw_pass(self.m_ctx, self.m_lit_depth_fbo, prepare, record, clear=False)

    def _drop(self) -> None:
        # a framebuffer releases only its render targets: every attachment is the owner's to release
        for target in (*self.m_targets, self.m_lit, self.m_depth, self.m_fbo, self.m_lit_fbo, self.m_lit_depth_fbo, self.m_read_depth,
                  self.m_read_fbo, self.m_refraction, self.m_refraction_fbo, self.m_blend, self.m_blend_fbo,
                  self.m_blend_depth_fbo):  # fmt: skip
            if target is not None:
                target.release()
        self.m_targets, self.m_size_px = [], None
        self.m_lit = self.m_depth = self.m_fbo = self.m_lit_fbo = self.m_lit_depth_fbo = self.m_read_depth = (
            self.m_read_fbo
        ) = None
        self.m_refraction = self.m_refraction_fbo = self.m_blend = self.m_blend_fbo = self.m_blend_depth_fbo = None

    def release(self) -> None:
        self._drop()
        self.m_composite.release()
        self.m_depth_copy.release()
        self.m_lit_copy.release()
        for part in (self.m_blend_copy, self.m_blend_clear):
            if part is not None:
                part.release()
        self.m_blend_copy = self.m_blend_clear = None
        self.m_shading.release()
        self.m_parts.release()
        if self.m_shadow_map is not None:
            self.m_shadow_map.release()
            self.m_shadow_map = None
