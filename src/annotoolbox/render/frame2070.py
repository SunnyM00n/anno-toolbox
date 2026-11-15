"""Anno 2070's frame on the QRhi device (Direct3D 11) as far as a model viewer needs it: the models drawn into the
G-buffer by the game's own shaders (``Deferred``), the sun's shadow map by their ``Obj2Shadow`` technique, the
game's deferred lighting over it (``DeferredShadingStandardShadow``, without casters ``...NoShadow``), the sky dome
and the point and spot lights into the lit scene, and a composite into the view's target with the G-buffer's depth.

``Frame2070(ctx, stages, dummies)`` is the entry: ``render(target, size, models, camera, sun, lamps)`` records the
passes; ``Parts2070`` holds the parts beyond the lighting; ``Model2070`` is one mesh with its maps. The G-buffer and
the light: docs/rendering/frame-2070.md; the compile and the parts: docs/rendering/shader-compile.md section 10.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import ocean2070, shadowforward
from .frameforward import DEPTH_COPY_FS, LIT_COPY_FS, NO_SHADOW, TEXTURE
from .fxcdraw import Dummies, FxcDraw
from .gamedraw import Program, screen_triangle
from .nativepass import Composite, draw_models, draw_pass
from .ocean2070 import Sea
from .sky2070 import sky_values

F = QRhiTexture.Format
G_BUFFER = (F.RGBA8, F.RGBA8, F.RGBA8)  # albedo (gamma values) and coverage, normal, glow / specular / environment
LIT = F.RGBA16F  # the lit scene, linear and halved (LOW_QUALITY_HDR_ENABLED): range 0 to 2
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
class Sun2070:
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


def sun_of(setting, direction, seconds: float, clouds: bool = True) -> Sun2070:
    """The frame's light of an ambient set (``sources.ambient2070``'s setting), the sun towards ``direction`` (the
    pass's axes) at the view's time; without ``clouds`` their density is 0 (``sky.fx`` then draws none)."""
    s, a = setting, setting.atmosphere
    cloud = tuple(c * s.cloud_color_power for c in a.cloud_color[:3])
    mood = (*s.mood, s.mood_color[3] if len(s.mood_color) > 3 else 0.0)
    density = a.cloud_density if clouds else 0.0
    return Sun2070(tuple(direction), s.sun, s.ambient, s.inv_cloud_shadow_fac, a.scatter_start, a.horizon_fall_off,
                   a.distance_fall_off, s.zenith, s.horizon, cloud, density, mood, seconds=float(seconds))  # fmt: skip


@dataclass
class Parts2070:
    """The frame's parts beyond the lighting, each None when it is off or did not compile: the shadowed lighting
    draw, the sky (``sky2070.SkyDome``), the lights (``lights2070.LightVolumes``), the sea (``ocean2070.Ocean2070``)."""

    shadowed: FxcDraw | None = None
    sky: object = None
    lights: object = None
    ocean: object = None

    def release(self) -> None:
        for part in (self.shadowed, self.sky, self.lights, self.ocean):
            if part is not None:
                part.release()


def fill_camera(c: dict, cam: gs.Camera, seconds: float = 0.0) -> None:
    """The camera buffers by name (every buffer the shader reads of them; the rest is left alone); ``cTimer`` the
    view's time as the engine's timers (in-game, wind and real time alike; no delta: a still frame)."""
    values = gs.camera_values(cam, gs.as_columns)
    values.update({"cMtxViewProjInvOrigin": values["cMtxViewProjInv"], "cVecViewPort": [0, 0, *cam.size],
                   "cFOV": [0.75, 1 / 0.75, 0.38, 0], "cTimer": [seconds, 0.0, seconds, seconds],
                   "cMtxViewProjTex": gs.as_columns(cam.view @ cam.proj @ TEXTURE)})  # fmt: skip
    gs.set_values(c.values(), values)


def fill_light(c: dict, sun: Sun2070, cam: gs.Camera) -> None:
    """The light and scattering buffers: the sun and ambient colours, the haze from the set's atmosphere."""
    toward = np.asarray(sun.direction, float)
    values = {
        "cLgtAmbient": [*sun.ambient, 1], "cLgtDiffuse": [*sun.diffuse, 1], "cLgtDirection": [*-toward, 0],
        "cLgtDirectionInv": [*toward, 0], "cLgtInvCloudShadows": [sun.inv_cloud, 0, 0, 0],
        "cHorizonColor": list(sun.horizon), "cZenithColor": list(sun.zenith), "cSunColor": [0.133, 0.5, 0.575],
        "cHorizonFallOff": [sun.horizon_fall_off] * 3, "cSunFallOff": [8.5] * 3, "cDistanceFallOff": [sun.distance_fall_off] * 3,
        "cAtmosphericData0": [-0.2, 1.25, 0.665, 1 / 0.335],
        "cAtmosphericData1": [sun.scatter_start * cam.far, cam.far, 1 / (cam.far * (1 - sun.scatter_start)), 0.08],
    }  # fmt: skip
    gs.set_values(c.values(), values)


def fitted(down, corners: np.ndarray, beyond=None, bias: float = SHADOW_BIAS) -> tuple:
    """One orthographic shadow map fitted round ``corners`` (``shadowforward.fit``; ``beyond``: receivers its depth
    reaches too) and ``bias`` in its depth range's units, scaled so that it stays the same distance however far the
    depth reaches: what the old engine's frames (2070, 1404, 1701) share."""
    found = shadowforward.fit(down, corners, beyond)
    if beyond is not None:
        own = shadowforward.fit(down, corners).values["cVecShadowVPDN"]
        bias *= float(np.linalg.norm(found.values["cVecShadowVPDN"][:3]) / np.linalg.norm(own[:3]))
    return found, bias


def shadow_values(down, corners: np.ndarray, matrix=gs.as_columns, beyond=None) -> dict:
    """``cbShadow`` for shadow method 0 with one orthographic map fitted round ``corners`` (``shadowforward.fit``;
    ``beyond``: receivers its depth reaches too): the pass's matrix, the lighting's matrix into the map, the plane the
    distance to the light is measured on and the bias (the same distance however far the depth reaches); ``matrix``
    turns ours into the shader's (2070's transposed, 1404's as they are)."""
    found, bias = fitted(down, corners, beyond)
    plane = found.values["cVecShadowVPDN"]
    return {"cMtxShadowVP0": matrix(found.view_proj), "cMtxShadowVPP0": matrix(found.view_proj @ TEXTURE),
            "cVecShadowVPD": plane, "cShadowBias": [0.0, bias, 0.0, 0.0], "cShadowMethod": [0.0, 0.0, 0.0, 0.0]}  # fmt: skip


def sun_shadow(ctx, shadow_map, casters: list, down, matrix=gs.as_columns, values_of=None, texture: str = "cShadowMap",
               beyond=None):  # fmt: skip
    """The casters' (models with a ``shadow`` draw, ``corners`` and ``fill_model``) shadow map drawn by their
    caster pass (the map made on first use), the colour pass's values and its map by name (``texture``); the old
    engine's frames (2070, 1404, 1701) share it. ``values_of(down, corners, beyond)`` gives the casters' and the
    colour pass's values (1701's); by default both are ``cbShadow``'s; ``beyond``: receivers that cast nothing (the
    sea)."""
    corners = np.concatenate([m.corners() for m in casters])
    if values_of:
        cast, values = values_of(down, corners, beyond)
    else:
        cast = values = shadow_values(down, corners, matrix, beyond)
    shadow_map = shadow_map or shadowforward.ShadowMap(ctx)
    shadow_map.render(casters, cast, lambda m, c: m.fill_model(c))
    return shadow_map, values, {texture: shadow_map.native}


def object_rows(world: np.ndarray, palette, bones: int = 0, color=(1.0, 1.0, 1.0)) -> tuple[list, int]:
    """The rows of ``cMtxMeshWorld`` for one object: a colour (rgb, or rgba: the player's, which the dye shader puts
    where its mask's green says; model water's colour and depth), the damage, then four rows per matrix (the place
    for a rigid mesh, place times the joint's matrix for a skin; ``bones`` copies of the place for a skinned mesh
    that plays no animation), and how many float4s that is."""
    w = np.asarray(world, float)
    mats = [w] * max(1, bones) if palette is None else [w @ m for m in palette]
    rows = [[*color, 1.0][:4], [0.0, 0.0, 0.0, 0.0]]
    for m in mats:
        rows += [list(m[i]) for i in range(4)]
    return rows, len(rows)


def unshadowed(ctx, shadow_map) -> tuple:
    """(the map, (``cbShadow`` and the map by name)) that leave every point lit, for the passes of shadow method 0
    without casters or with the shadow switched off (the blended pass, the sea): their technique always reads the map,
    and the zero matrix of an unfilled buffer divides 0 by 0 there (shader-compile.md 10, "Blended pass")."""
    shadow_map = shadow_map or shadowforward.ShadowMap(ctx)
    shadow_map.render([], {}, lambda m, c: None)  # no casters: the map cleared to the far depth
    values = {"cMtxShadowVP0": gs.as_columns(NO_SHADOW), "cMtxShadowVPP0": gs.as_columns(NO_SHADOW), "cVecShadowVPD": [0.0] * 4,
              "cShadowBias": [0.0] * 4, "cShadowMethod": [0.0] * 4}  # fmt: skip
    return shadow_map, (values, {"cShadowMap": shadow_map.native})


def ocean_waves(ocean, sea: Sea, seconds: float) -> dict:
    """The wave maps of ``ocean`` for this moment (``Ocean2070.waves`` with ``sea``'s fixed maps), by the ocean's names."""
    return ocean.waves(seconds, {name: sea.maps[path] for name, path in ocean2070.WAVES.items()})


class SeaInputs(NamedTuple):
    """What the sea's pass reads of the frame: the lit scene's and the depth's copies it refracts (``cTexture5`` and
    ``cTexture6``), the sun's shadow (its values and maps) and the wave maps made already this frame (None: made
    by the pass)."""

    copies: dict
    shadow: tuple
    waves: dict | None = None


def ocean_pass(ctx, fbo, size, cam: gs.Camera, sun: Sun2070, ocean, sea: Sea, inputs: SeaInputs) -> None:
    """The sea by ``waterocean.fx`` (``ocean``: an ``ocean2070.Ocean2070``) into ``fbo``: the wave maps and the sky
    cube made for this moment, then the chunks round ``sea``, refracting the frame's copies, lit by the sun with
    its shadow (``inputs``), tested and written against ``fbo``'s depth (shader-compile.md 10, "The ocean's
    waves")."""
    copies = inputs.copies
    waves = inputs.waves or ocean_waves(ocean, sea, sun.seconds)
    values, shadow_maps = inputs.shadow
    forward = np.asarray(cam.view, float)[:3, 2]

    def sky(c: dict) -> None:
        fill_camera(c, cam, sun.seconds)
        fill_light(c, sun, cam)
        gs.set_values(c.values(), sky_values(sun))

    maps = {**waves, "cTexture2": sea.maps[ocean2070.RIPPLES], "cTexture7": sea.maps[ocean2070.CAUSTICS],
            "tSkyEnv": ocean.sky_cube(sky), **copies, **shadow_maps}  # fmt: skip
    if sea.info is not None:
        maps["cTexture3"] = sea.info
    if sea.reflection is not None:
        maps["cTexture4"] = sea.reflection
    sea_values = ocean2070.ocean_values(ocean.water, sun.seconds, sun.direction, forward / np.linalg.norm(forward),
                                        sea.texel)  # fmt: skip
    extra = {**values, **water_values(cam, (1.0, 1.0, 1.0, 1.0)), **sea_values,
             "cWaterViewProjTex": gs.as_columns(cam.view @ cam.proj @ TEXTURE)}  # fmt: skip
    if sea.coast is not None:
        maps |= sea.coast.ocean_maps()
        extra |= sea.coast.ocean_values()
    draws = ocean.draws(ocean2070.chunk_rings(sea.centre, sea.reach, cam.eye))

    def fill(c: dict, own: dict) -> None:
        fill_camera(c, cam, sun.seconds)
        fill_light(c, sun, cam)
        gs.set_values(c.values(), {**extra, **own}, ("cWaterChunkData",))

    def prepare(rp, signature, batch) -> None:
        for d, own in draws:
            d.prepare(rp, signature, batch, maps, lambda c, own=own: fill(c, own))

    def record(cb) -> None:
        for d, _own in draws:
            d.draw(cb, size)

    draw_pass(ctx, fbo, prepare, record, clear=False)


def water_values(cam: gs.Camera, diffuse) -> dict:
    """What model water reads beyond the camera: the view depth it measures the water by, in the engine's
    left-handed view space (ahead is +z: ours looks down -z, and the depth behind the sheet would come out
    negative), and ``cMatDiffuse`` (the material's diffuse colour: its alpha is the water's clearness)."""
    lh = gs.left_handed(cam)
    return {
        "cMtxView": gs.as_columns(lh.view),
        "cMtxProjInv": gs.as_columns(np.linalg.inv(lh.proj)),
        "cMatDiffuse": list(diffuse),
    }


class MeshModel:
    """What the old engine's models (``Model2070``, ``frame1404.Model1404``) share: ``draw_call`` (the colour pass's
    ``FxcDraw``), ``maps`` (the shader's texture name -> a QRhi texture or None while it loads), ``world``."""

    draw_call: FxcDraw
    maps: dict
    world: np.ndarray

    def textures(self) -> dict:
        """The maps that are in."""
        return {k: v for k, v in self.maps.items() if v is not None}

    def corners(self) -> np.ndarray:
        """The eight corners of the mesh's box where it stands (a shadow caster's, ``sun_shadow``)."""
        return shadowforward.corners(self.draw_call.box(), self.world)


class Model2070(MeshModel):
    """One mesh group of a model with its material's maps: ``draw`` (a ``FxcDraw``), ``maps`` (the shader's texture
    name -> a QRhi texture or None while it loads), ``world`` (column vectors) and ``palette`` set per frame,
    ``shadow`` its ``Obj2Shadow`` draw (None: it casts none); ``water`` a model water sheet (drawn over the lit
    scene, not into the G-buffer) with its material's ``diffuse`` colour."""

    def __init__(self, draw: FxcDraw, shadow: FxcDraw | None = None) -> None:
        self.draw_call, self.maps, self.values, self.bones = draw, {}, {}, 0
        self.color = (1.0, 1.0, 1.0)  # the object colour: the player's on a dye material, rgba on water
        self.world, self.palette = np.identity(4), None
        self.shadow = shadow
        self.water, self.diffuse = False, (1.0, 1.0, 1.0, 1.0)
        self.blended = False  # drawn by DefaultBlend in the blended pass, after the sky
        # drawn in the game's axes, the mirror in the camera's projection: the colour pass's faces swap, the sun's
        # own view keeps them (shader-compile.md 11 "The game's own axes")
        for d, mirrored in ((draw, True), (shadow, False)):
            if d is not None:
                d.fallbacks, d.mirrored = dict(FALLBACKS), mirrored

    def fill_model(self, c: dict) -> None:
        """The object's own rows (``object_rows``) and how many float4s one object takes."""
        rows, per_object = object_rows(self.world, self.palette, self.bones, self.color)
        if "cbMeshConstants" in c:
            c["cbMeshConstants"].set_rows("cMtxMeshWorld", rows[:256])
        gs.set_values(c.values(), {"cUserConstant0": [per_object, 0, 0, 0]})

    def prepare(self, rp, signature, batch, cam: gs.Camera, sun: Sun2070, frame_maps=None, frame_values=None) -> None:
        """The draw's resources and constants; ``frame_maps`` / ``frame_values``: the frame's textures and constants
        it reads besides its own (the water's copies of the lit scene and the depth, the blended pass's shadow)."""

        def fill(c: dict) -> None:
            fill_camera(c, cam, sun.seconds)
            fill_light(c, sun, cam)
            self.fill_model(c)
            if self.water:
                gs.set_values(c.values(), water_values(cam, self.diffuse))
            if frame_values:
                gs.set_values(c.values(), frame_values)

        self.draw_call.prepare(rp, signature, batch, {**self.textures(), **(frame_maps or {})}, fill)

    def draw(self, cb, size) -> None:
        self.draw_call.draw(cb, size)

    def release(self) -> None:
        self.draw_call.release()
        if self.shadow is not None:
            self.shadow.release()


def shading_pass(ctx, stages, dummies: Dummies) -> FxcDraw:
    """The deferred lighting as a full-screen draw of the game's compiled shaders (``stages``: its ``PassStages``)."""
    program = Program(stages.vs, stages.ps)
    return FxcDraw(ctx, program, screen_triangle(), replace(stages.state, cull="none"), dummies)


class Frame2070:
    """The G-buffer of one view and the passes over it (module docstring)."""

    def __init__(self, ctx, shading_stages, dummies: Dummies) -> None:
        self.ctx = ctx
        self.shading = shading_pass(ctx, shading_stages, dummies)
        self.parts = Parts2070()
        self.size: tuple[int, int] | None = None
        self.targets: list = []
        self.lit = self.depth = self.fbo = self.lit_fbo = self.lit_depth_fbo = self.read_depth = self.read_fbo = None
        self.refraction = self.refraction_fbo = None
        self.blend = self.blend_fbo = self.blend_depth_fbo = None
        self.blend_copy = self.blend_clear = None  # the blended layer's composite and clear, made on first use
        self.composite = Composite(ctx, COMPOSITE_FS)
        self.depth_copy = Composite(ctx, DEPTH_COPY_FS)
        self.lit_copy = Composite(ctx, LIT_COPY_FS)
        self.shadow_map: shadowforward.ShadowMap | None = None

    def _targets(self, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        ctx = self.ctx
        self.targets = [ctx.native_texture(size, f) for f in G_BUFFER]
        self.depth = ctx.depth_renderbuffer(size)
        self.fbo = ctx.framebuffer(self.targets, self.depth)
        self.lit = ctx.native_texture(size, LIT)
        self.lit_fbo = ctx.framebuffer([self.lit])
        self.lit_depth_fbo = ctx.framebuffer([self.lit], self.depth)  # the sky and the lights: tested, not written
        self.read_depth = ctx.native_texture(size, F.R32F)  # the depth the lights read while it is attached
        self.read_fbo = ctx.framebuffer([self.read_depth])
        self.refraction = ctx.native_texture(size, LIT)  # the lit scene's copy the water refracts
        self.refraction_fbo = ctx.framebuffer([self.refraction])
        self.blend = ctx.native_texture(size, LIT)  # the blended pass's layer (BLEND_FS)
        self.blend_fbo = ctx.framebuffer([self.blend])
        self.blend_depth_fbo = ctx.framebuffer([self.blend], self.depth)
        self.size = size

    def _shadow(self, models: list, sun: Sun2070, sea: Sea | None = None) -> tuple[dict, dict] | None:
        """The casters' shadow map and ``cbShadow`` (None without casters or a shadowed lighting); its depth reaches
        over the sea too, which casts none (``ocean2070.corners``)."""
        casters = [m for m in models if m.shadow is not None]
        if self.parts.shadowed is None or not casters:
            return None
        beyond = ocean2070.corners(sea) if sea is not None else None
        self.shadow_map, values, maps = sun_shadow(
            self.ctx, self.shadow_map, casters, -np.asarray(sun.direction), beyond=beyond
        )
        return values, maps

    def render(self, target, size: tuple[int, int], models: list, cam: gs.Camera, sun: Sun2070, lamps=(),
               sea: Sea | None = None) -> None:  # fmt: skip
        """The G-buffer pass with ``models`` (``Model2070``), the shadow map, the lighting, the sky and the lights
        (``lamps``: ``lightsforward.Lamp`` in the view's axes), then the composite into ``target``. The sea (``sea``,
        with ``parts.ocean``) and model water draw over the lit scene before the sky and the lights, as the engine's
        water step does."""
        self._targets(size)
        sea = sea if self.parts.ocean is not None else None
        water = [m for m in models if m.water]
        blended = [m for m in models if m.blended and not m.water]
        models = [m for m in models if not m.water and not m.blended]
        draw_models(self.ctx, self.fbo, models, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, sun),
                    lambda m, cb: m.draw(cb, size), targets=len(G_BUFFER))  # fmt: skip
        shadow = self._shadow(models, sun, sea)
        maps = {"cTexture0": self.targets[0].native, "cTexture1": self.targets[1].native,
                "cTexture2": self.depth.native, "cTexture3": self.targets[2].native, **(shadow[1] if shadow else {})}  # fmt: skip
        shading = self.parts.shadowed if shadow else self.shading

        def fill(c: dict) -> None:
            fill_camera(c, cam, sun.seconds)
            fill_light(c, sun, cam)
            if shadow:
                gs.set_values(c.values(), shadow[0])

        draw_pass(self.ctx, self.lit_fbo, lambda rp, signature, batch: shading.prepare(rp, signature, batch, maps,
                  fill), lambda cb: shading.draw(cb, size))  # fmt: skip
        if water or sea is not None:
            self._water(size, cam, sun, water, sea, (shadow or self._unshadowed()) if sea is not None else None)
        self._forward(size, cam, sun, list(lamps), maps)
        if blended:
            self._blended(size, cam, sun, blended, shadow or self._unshadowed())
        uniforms = {"flip": bool(self.ctx.flip_offscreen), "sky": self.parts.sky is not None}
        self.composite.draw(target, {"gLit": self.lit, "gDepth": self.depth}, uniforms,
                            depth_func="<=" if self.parts.sky is not None else None)  # fmt: skip
        if blended:
            ctx = self.ctx
            self.blend_copy.draw(target, {"gBlend": self.blend}, {"flip": bool(ctx.flip_offscreen)}, depth_test=False,
                                 blend=(ctx.ONE, ctx.ONE_MINUS_SRC_ALPHA))  # fmt: skip

    def _unshadowed(self) -> tuple[dict, dict]:
        """``unshadowed`` with this frame's map."""
        self.shadow_map, shadow = unshadowed(self.ctx, self.shadow_map)
        return shadow

    def _blended(self, size, cam: gs.Camera, sun: Sun2070, models: list, shadow: tuple) -> None:
        """The blended pass (``DefaultBlend``) after the sky into a layer of its own: far to near, tested against the
        G-buffer's depth, not written; lit by the sun with its shadow (shader-compile.md 10, "Blended pass"). The
        layer goes over the composited view (``BLEND_FS``), so it covers the view's own ground as well."""
        self.blend_clear = self.blend_clear or Composite(self.ctx, CLEAR_FS)
        self.blend_copy = self.blend_copy or Composite(self.ctx, BLEND_FS)
        self.blend_clear.draw(self.blend_fbo, {}, {}, depth_test=False)  # the colour only: the depth is the G-buffer's
        eye = np.asarray(cam.eye, float)
        order = sorted(models, key=lambda m: -float(np.linalg.norm(np.asarray(m.world, float)[:3, 3] - eye)))
        values, maps = shadow
        draw_models(self.ctx, self.blend_depth_fbo, order,
                    lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, sun, maps, values),
                    lambda m, cb: m.draw(cb, size), clear=False)  # fmt: skip

    def _water(self, size, cam: gs.Camera, sun: Sun2070, water: list, sea: Sea | None, shadow: tuple | None) -> None:
        """The water step over the lit scene: the scene and the depth copied once, which every water surface refracts
        and measures its depth against, then the sea (with ``shadow``: its values and maps) and model water
        (``model_water.fx``), drawn tested and written against the G-buffer's depth."""
        self.depth_copy.draw(self.read_fbo, {"gDepth": self.depth}, {}, depth_test=False)
        self.lit_copy.draw(self.refraction_fbo, {"gLit": self.lit}, {}, depth_test=False)
        if sea is not None:
            copies = {"cTexture5": self.refraction.native, "cTexture6": self.read_depth.native}
            inputs = SeaInputs(copies, shadow)
            ocean_pass(self.ctx, self.lit_depth_fbo, size, cam, sun, self.parts.ocean, sea, inputs)
        maps = {"cRefractionColorBuffer": self.refraction.native, "cRefractionDepthBuffer": self.read_depth.native}
        draw_models(self.ctx, self.lit_depth_fbo, water,
                    lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, sun, maps), lambda m, cb: m.draw(cb, size),
                    clear=False)  # fmt: skip

    def _forward(self, size, cam: gs.Camera, sun: Sun2070, lamps: list, maps: dict) -> None:
        """The sky and the lights into the lit scene over the G-buffer's depth (tested, not written)."""
        sky, lights = self.parts.sky, self.parts.lights
        draws = lights.frame(lamps, cam.eye, cam.near) if lights is not None and lamps else []
        if sky is None and not draws:
            return
        for d in draws:  # the light volumes through the camera's mirrored projection: their faces swap
            d.mirrored = cam.mirror
        if draws:
            self.depth_copy.draw(self.read_fbo, {"gDepth": self.depth}, {}, depth_test=False)
        light_maps = {**maps, "cTexture2": self.read_depth.native}
        sprites = lights.sprites if lights is not None else []

        def fill(c: dict) -> None:
            fill_camera(c, cam, sun.seconds)
            fill_light(c, sun, cam)
            if sky is not None:
                gs.set_values(c.values(), sky_values(sun))
            gs.set_values(c.values(), {"cSpriteConfig": [3.0, 0.0, 0.0, 0.0]})
            if sprites:
                gs.set_values(c.values(), {"cSpriteData": sprites}, ("cSpriteData",))

        def prepare(rp, signature, batch) -> None:
            if sky is not None:
                sky.draw_call.prepare(rp, signature, batch, sky.maps(), fill)
            for d in draws:
                d.prepare(rp, signature, batch, light_maps, fill)

        def record(cb) -> None:
            if sky is not None:
                sky.draw_call.draw(cb, size)
            for d in draws:
                d.draw(cb, size)

        draw_pass(self.ctx, self.lit_depth_fbo, prepare, record, clear=False)

    def _drop(self) -> None:
        # a framebuffer releases only its render targets: every attachment is the owner's to release
        for t in (*self.targets, self.lit, self.depth, self.fbo, self.lit_fbo, self.lit_depth_fbo, self.read_depth,
                  self.read_fbo, self.refraction, self.refraction_fbo, self.blend, self.blend_fbo,
                  self.blend_depth_fbo):  # fmt: skip
            if t is not None:
                t.release()
        self.targets, self.size = [], None
        self.lit = self.depth = self.fbo = self.lit_fbo = self.lit_depth_fbo = self.read_depth = self.read_fbo = None
        self.refraction = self.refraction_fbo = self.blend = self.blend_fbo = self.blend_depth_fbo = None

    def release(self) -> None:
        self._drop()
        self.composite.release()
        self.depth_copy.release()
        self.lit_copy.release()
        for part in (self.blend_copy, self.blend_clear):
            if part is not None:
                part.release()
        self.blend_copy = self.blend_clear = None
        self.shading.release()
        self.parts.release()
        if self.shadow_map is not None:
            self.shadow_map.release()
            self.shadow_map = None
