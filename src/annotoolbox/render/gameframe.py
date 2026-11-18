"""117's frame on the QRhi device, as far as it runs: the G-buffer in the engine's formats and depth (1 near, 0 far),
the models drawn into it by the game's own shaders (``gamedraw``), the game's deferred shading over it (``gamelight``,
a compute pass) or a stand-in light of ours, and a composite into the view's target.

``GameFrame(ctx)`` is the entry: ``render(target, size, models, camera)`` records the passes; the composite writes
the G-buffer's depth so that what a view draws itself (a ground, particles) meets the models. The G-buffer:
``gbuffer.fxh``; the frame: docs/rendering/frame-117.md; the plan: docs/tool-suite.md section 21.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import gameshadow, lightclusters
from .camera import gl, look_at, perspective
from .gamecompute import storage_buffer
from .gamelight import DeferredShading, Extras, Sky
from .nativepass import Composite, draw_models
from .rhidevice import Texture

# the engine's targets of the G-buffer pass (gbuffer.fxh): normal, albedo, gloss / metal / self-shadow, velocity
GBUFFER = (QRhiTexture.Format.RGB10A2, QRhiTexture.Format.RGBA8, QRhiTexture.Format.RGBA8, QRhiTexture.Format.RGBA16F)
ALBEDO_SRGB = True  # assumed: the albedo target stores sRGB like the maps it is read from (not read off the engine)

# the composite: the deferred shading's colour (``lit``) or the stand-in light (the sun and a flat sky); ``finish``:
# the editor's (ACES, sRGB) and the background where nothing was drawn, else the linear colour for the view's finish
LIGHT_FS = """
#version 330
uniform sampler2D gNormal; uniform sampler2D gAlbedo; uniform sampler2D gMaterial; uniform sampler2D gDepth;
uniform sampler2D gLit; uniform bool lit; uniform bool litSky; uniform mat4 invViewProj;
uniform vec3 sunDir; uniform vec3 sunColor; uniform vec3 sky; uniform vec3 background; uniform vec3 viewDir;
uniform bool finish; uniform float exposure; uniform vec3 planes;
in vec2 vNdc; out vec4 color;
vec3 srgb(vec3 c) { return mix(12.92 * c, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }
vec3 aces(vec3 v) { return clamp(v * (2.51 * v + 0.03) / (v * (2.43 * v + 0.59) + 0.14), 0.0, 1.0); }
// the G-buffer's depth (1 near, 0 at the game's far plane planes.z) as the view's own (0 near, 1 at planes.y)
float viewDepth(float d) {
    float t = planes.x * planes.z / (d * (planes.z - planes.x) + planes.x);
    return planes.y * (t - planes.x) / (t * (planes.y - planes.x));
}
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    float depth = texture(gDepth, uv).r;
    vec4 a = invViewProj * vec4(vNdc, -1.0, 1.0), b = invViewProj * vec4(vNdc, 1.0, 1.0);
    bool up = (b.y / b.w - a.y / a.w) > 0.0;
    if (depth <= 0.0 && lit && litSky && up) {  // the game's sky where nothing was drawn, above the horizon
        vec3 shaded = texture(gLit, uv).rgb;
        color = vec4(finish ? srgb(aces(shaded * exposure)) : shaded, 1.0);
        gl_FragDepth = 1.0;
        return;
    }
    if (depth <= 0.0) {
        if (!finish) discard;
        color = vec4(srgb(background), 1.0);
        gl_FragDepth = 1.0;
        return;
    }
    if (lit) {
        vec3 shaded = texture(gLit, uv).rgb;
        color = vec4(finish ? srgb(aces(shaded * exposure)) : shaded, 1.0);
        gl_FragDepth = viewDepth(depth);
        return;
    }
    vec4 e = texture(gNormal, uv);
    vec3 n = normalize(e.rgb * 2.0 - 1.0);
    vec3 albedo = texture(gAlbedo, uv).rgb;
    vec4 m = texture(gMaterial, uv);  // gloss, metal, self-shadow
    float ndl = max(dot(n, sunDir), 0.0) * m.b;
    vec3 h = normalize(sunDir + viewDir);
    float spec = pow(max(dot(n, h), 0.0), mix(4.0, 256.0, m.r)) * m.r * ndl;
    vec3 diffuse = albedo * (1.0 - m.g);
    vec3 c = diffuse * (sky * (0.6 + 0.4 * n.y) + sunColor * ndl) + mix(vec3(0.04), albedo, m.g) * sunColor * spec;
    color = vec4(finish ? srgb(aces(c * exposure)) : c, 1.0);
    gl_FragDepth = viewDepth(depth);
}
"""


@dataclass(frozen=True)
class Light:
    """The stand-in light's sun (towards it), its colour, the sky's and the background's; the exposure of the
    composite's own finish."""

    sun: tuple = (0.4, 0.8, 0.3)
    sun_color: tuple = (2.2, 2.1, 1.9)
    sky: tuple = (0.35, 0.42, 0.55)
    background: tuple = (0.18, 0.2, 0.24)
    exposure: float = 1.0


LEFT_HANDED = np.diag([1.0, 1.0, -1.0, 1.0])  # our right-handed view into the engine's: +z ahead


@dataclass
class Passes:
    """The frame's passes besides the G-buffer: the shadow casters (``gamedraw.GameModel``s of the effects' shadow
    technique) with the scene's bounds (world lo, hi) the cascades are fitted to."""

    casters: list = field(default_factory=list)
    box: tuple | None = None
    lights: list = field(default_factory=list)  # lightclusters' lights, in the scene's axes
    fit: object = None  # shadowfit.ShadowFit: the game's analysis fits the cascades (None: the CPU fits them)
    transparent: list = field(default_factory=list)  # gamedraw.GameModel of the effects' Transparent techniques
    sky: object = None  # gamesky.SkyPasses: the game's sky passes light the frame (None: the look's light)
    water: object = None  # gamewater.SceneCopies: the copies the water reads, made before the transparent pass
    atmosphere: object = None  # gameatmosphere.Atmosphere: the visible sky and haze (needs ``sky``)
    look: object = None  # gameatmosphere.Look: its blur and fade
    clouds: object = None  # gameclouds.Clouds: the clouds in the sky and their shadow (needs ``atmosphere``)
    sea: object = None  # gamesea.Sea: the sea by the game's ocean shader, the transparent pass's first draw
    inland: object = None  # gameinland.InlandWater: rivers and lakes by the game's shader, drawn before the sea
    ground: Ground | None = None  # the session's terrain the models' terrain adaptation reads (None: none)
    # the native backend's draws, recorded first in the G-buffer's and the shadow map's passes: ``gbuffer(cb, camera,
    # size)``, ``shadow(cb, camera, cascades, info, side)``, ``casters`` (any to draw) (``terrainnative.FrameDraws``)
    native: object = None


@dataclass
class Ground:
    """The session's terrain as the terrain, the models and the decals read it beyond their own: its size, its maps
    by the shaders' names (the coarse heights and displacement, the grit and cliff coverage and textures), the
    frame's values that go with them (the grit's) (rendering/island-117-view.md "Phase 5")."""

    size: float
    maps: dict
    values: dict = field(default_factory=dict)

    def fill(self):
        """The pass's fill writing ``cbPerFrame``'s terrain values, keyed so a still frame skips it (``stamp_of``);
        worked out once per layout and copied into every buffer of it (a moving camera fills every draw's)."""
        s = self.size
        values = {**self.values, "cTerrainSize": [s, s, 1 / s, 1 / s], "cTerrainHeightTexIsValid": 1}
        token = (s, str(self.values))

        def write(c: gs.Constants) -> None:
            for name, value in values.items():
                c.set(name, value)

        def fill(c: gs.Constants) -> None:
            gs.replay(c, "ground", token, write)

        fill.key = fill.common_key = ("ground", s, tuple(id(m) for m in self.maps.values()), token[1])
        return fill


class GameFrame:
    """The G-buffer of one view and the passes over it (module docstring)."""

    def __init__(self, ctx, stencil: bool = False) -> None:
        self.ctx = ctx
        self.stencil = stencil  # the depth target holds the engine's stencil bits too (the cut-outs, the water's)
        self.size: tuple[int, int] | None = None
        self.targets: list = []
        self.lit: list = []
        self.depth = self.fbo = self.lit_fbo = None
        self.shadow: gameshadow.ShadowMap | None = None
        self.shadow_size = gameshadow.SIZE  # the map's side (``set_quality``)
        self.shadow_cascades = gameshadow.COUNT  # the cascades drawn and read (``set_quality``)
        self.cascades: gameshadow.Cascades | None = None
        self.info = None  # cShadowInformation: the cascades as the casters and the shading read them
        self.ray = np.identity(4)  # our camera's inverse view-projection: which way a pixel looks (the composite)
        self.planes = (0.1, 100.0, 100.0)  # near, the view's far plane, the G-buffer's (the composite's depth)
        self.composite = Composite(ctx, LIGHT_FS)

    def set_quality(self, q) -> None:
        """The rendering quality's shadow map side and cascade count (``render.quality.Quality``; None: the best),
        from the next frame (the map is made again when its side changed); the render scale is the caller's: it
        hands the scaled size to ``camera`` and ``render``."""
        side = q.shadow_size if q is not None else gameshadow.SIZE
        if side != self.shadow_size and self.shadow is not None:
            self.shadow.release()
            self.shadow = None
        self.shadow_size = side
        self.shadow_cascades = q.shadow_cascades if q is not None else gameshadow.COUNT

    def _gbuffer(self, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        ctx = self.ctx
        self.targets = [ctx.native_texture(size, f, srgb=ALBEDO_SRGB and k == 1) for k, f in enumerate(GBUFFER)]
        if self.stencil:
            self.depth = self._depth_stencil(size)
        else:
            self.depth = ctx.depth_renderbuffer(size)
        self.fbo = ctx.framebuffer(self.targets, self.depth)
        formats = (QRhiTexture.Format.RGBA16F, QRhiTexture.Format.R8)
        self.lit = [ctx.native_texture(size, f, storage=True) for f in formats]
        self.lit_fbo = ctx.framebuffer(self.lit, self.depth)  # RTT_DEFAULT: the transparent pass draws into it
        self.size = size

    def _depth_stencil(self, size) -> Texture:
        depth = Texture(self.ctx, tuple(size), 1, "f4", "depth", qformat=QRhiTexture.Format.D32FS8)
        depth.create()
        return depth

    def camera(self, size: tuple[int, int], eye, target, fov: float, near: float, far: float,
               game_far: float = 0.0) -> gs.Camera:  # fmt: skip
        """The engine's camera for ``size``: its view space left-handed (+z ahead, as the shadow cascades and the
        light clusters read view depth), its y turned over where the device stores images upside down against
        OpenGL, so the G-buffer reads as our own targets do (rhipass, "the right way up"). ``game_far``: the
        engine's far plane when it lies beyond the view's ``far`` (the clouds' rays end there; the cascades still
        end at ``far``, the composite writes the view's own depth)."""
        self.planes = (near, far, max(far, game_far))
        proj = LEFT_HANDED @ gs.perspective(fov, size[0] / max(1, size[1]), near, self.planes[2], reverse=True)
        if self.ctx.flip_offscreen:
            proj = proj @ np.diag([1.0, -1.0, 1.0, 1.0])
        view = gs.look_at(np.asarray(eye, float), np.asarray(target, float)) @ LEFT_HANDED
        self.ray = np.linalg.inv(perspective(fov, size[0] / max(1, size[1]), near, far) @ look_at(eye, target))
        return gs.Camera(np.asarray(eye, float), view, proj, near, self.planes[2], size, far)

    def render(self, target, size: tuple[int, int], models: list, camera: gs.Camera, light: Light | None = None,
               finish: bool = True, shading: tuple[DeferredShading, Sky] | None = None,
               passes: Passes | None = None) -> None:  # fmt: skip
        """The G-buffer pass with ``models`` (``gamedraw.GameModel``), the sun's shadow map when ``passes`` has
        casters, the game's deferred shading when ``shading`` is given, then the composite into ``target`` (a
        framebuffer of the device; ``finish``: the whole picture, else the lit models over what it holds)."""
        light = light or Light()
        passes = passes or Passes()
        self._gbuffer(size)
        ground = passes.ground
        native = passes.native
        external = (lambda cb: native.gbuffer(cb, camera, size)) if native is not None else None
        self._record(self.fbo, models, size, camera, len(GBUFFER), ground and ground.fill(), ground and ground.maps,
                     external=external)  # fmt: skip
        self.cascades = None
        casters = passes.casters or (native is not None and native.casters)
        if shading is not None and casters and passes.box is not None:
            self._shadow(size, camera, shading[1].sun, passes)
        sky_drawn = self._lit(size, camera, shading, passes) if shading is not None else False
        textures = dict(zip(("gNormal", "gAlbedo", "gMaterial", "gDepth", "gLit"),
                            (*self.targets[:3], self.depth, self.lit[0]), strict=True))  # fmt: skip
        d = np.asarray(light.sun, float)
        uniforms = {
            "lit": shading is not None, "litSky": sky_drawn, "invViewProj": gl(self.ray),
            "sunDir": tuple(d / np.linalg.norm(d)), "sunColor": tuple(light.sun_color), "sky": tuple(light.sky),
            "background": tuple(light.background), "viewDir": tuple(-float(v) for v in camera.view[:3, 2]),
            "finish": finish, "exposure": light.exposure, "planes": self.planes,
        }  # fmt: skip
        # the game's sky at the far plane meets the cleared depth (<=); the whole picture is drawn untested
        self.composite.draw(target, textures, uniforms, depth_test=not finish, depth_func="<=")

    def _record(self, fbo, models: list, size, camera: gs.Camera, targets: int, fill=None, shared=None,
                clear: bool = True, external=None) -> None:  # fmt: skip
        """One pass of ``models`` (``gamedraw.GameModel``) into ``fbo``, its depth cleared to the engine's far (0)."""

        draw_models(self.ctx, fbo, models, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, camera, fill, shared),
                    lambda m, cb: m.draw(cb, size), targets=targets, depth=0.0, clear=clear,
                    external=external)  # fmt: skip

    def _lit(self, size, camera: gs.Camera, shading: tuple, passes: Passes) -> bool:
        """The deferred shading, the copies and the transparent pass, then the visible sky and haze over the lit
        colour (``passes.atmosphere``, with the sky passes' tables); True when the sky was drawn so."""
        clusters = lightclusters.build(passes.lights, camera.view, camera.proj, camera.far) if passes.lights else None
        clouds = passes.clouds if passes.clouds is not None and not passes.clouds.error else None
        inputs, extras = self._shading(size, camera, *shading, clusters, passes.sky, clouds)
        sea = passes.sea.model(camera) if passes.sea is not None else None
        inland = passes.inland.models() if passes.inland is not None else []
        if passes.transparent or sea is not None or inland:
            water = passes.water if passes.water is not None and not passes.water.error else None
            if water is not None:
                water.run(self.ctx, self.lit[0], self.depth, size, camera)
            if sea is not None and water is not None:  # the coast waves' step over the depth just drawn
                passes.sea.simulate(self.ctx, {**water.inputs(), "cDepthBuffer": self.depth.native}, camera, water.fill)
            # rivers and lakes first: their depth keeps the sea under them (the engine's cut-outs and stencil)
            first = [*inland, *([sea] if sea is not None else [])]
            self._transparent(size, camera, shading, (inputs, extras), passes, water, first)
        atmosphere, sky = passes.atmosphere, passes.sky
        if atmosphere is None or atmosphere.error or sky is None or sky.error:
            return False
        tables = {"cAtmosphereTransmittanceLUT": sky.transmittance, "cAtmosphereMultiscatLUT": sky.multiscat}
        named = {**shading[0].shared(inputs), **tables, "cDepthBuffer": self.depth.native}
        atmosphere.run(self.ctx, self.lit_fbo, size, camera, shading[1], named, passes.look, clouds)
        return True

    def _shadow(self, size, camera: gs.Camera, sun, passes: Passes) -> None:
        """The cascades fitted (by the game's analysis over the depth just drawn, else on the CPU to the scene's
        bounds) and the casters drawn into the shadow map."""
        if self.shadow is None:  # with stencil the cut-out volumes count in it (``CutOutShadow``'s bits)
            side = self.shadow_size
            own = self._depth_stencil((side, side)) if self.stencil else None
            self.shadow = gameshadow.ShadowMap(self.ctx, side, own)
        if self.info is None:
            self.info = storage_buffer(self.ctx.rhi, gameshadow.INFO_BYTES * gameshadow.COUNT)
        fit = passes.fit if passes.fit is not None and not passes.fit.error else None
        side, used = self.shadow.size, self.shadow_cascades
        read = False  # the analysis copies its result out this frame: the buffer is left as a copy's source
        if fit is not None:
            read = fit.read_back
            fit.run(self.ctx, self.depth, size, camera, sun, self.info)
            self.cascades = gameshadow.Cascades((), gs.light_travel(sun), camera.shadow_distance, True, side, used)
        else:
            self.cascades = replace(gameshadow.fit(camera, passes.box, sun), size=side, used=used)
        native, cascades, info = passes.native, self.cascades, (self.info, read)
        external = (lambda cb: native.shadow(cb, camera, cascades, info, side)) if native is not None else None
        self.shadow.render(passes.casters, camera, self.cascades, self.info, passes.ground, external)

    def _shading(self, size, camera: gs.Camera, shading: DeferredShading, sky: Sky, clusters=None, passes=None,
                 clouds=None):  # fmt: skip
        """The deferred shading's compute pass from the G-buffer into ``lit``, lit by the game's sky passes when
        ``passes`` (``gamesky.SkyPasses``) is given, the clouds' shadow over it with ``clouds``; returns its inputs
        and extras (the transparent pass's)."""
        names = ("cGBuffer0", "cGBuffer1", "cGBuffer2")
        inputs = {**{n: t.native for n, t in zip(names, self.targets, strict=False)}, "cDepth": self.depth.native,
                  "cDstColor": self.lit[0].native, "cDstAlpha": self.lit[1].native}  # fmt: skip
        if self.cascades is not None:
            inputs["cShadowMap"] = self.shadow.texture.native
            inputs["cShadowInformation"] = self.info
        if passes is not None and not passes.error:
            passes.run(self.ctx, sky, clouds)  # only when the light or the clouds changed: the results stay
            inputs["cLightBuffer"], inputs["cPrefilteredSpecularCube"] = passes.light, passes.reflection
        if clouds is not None:
            clouds.shadowed = False
            if self.cascades is not None:  # the clouds' transmittance along the sun, in the cascades' parts
                clouds.shadow(self.ctx, camera, sky, self.cascades, {**shading.shared(inputs), **inputs})
            inputs.update(clouds.inputs())
        extras = Extras(self.cascades, clusters, clouds)

        def compute_pass(cb, batch) -> None:
            shading.prepare(batch, inputs, camera, sky, extras)
            cb.beginComputePass(batch)
            shading.dispatch(cb, shading.tiles(size))
            cb.endComputePass()

        self.ctx.native_pass(compute_pass)
        return inputs, extras

    def _transparent(self, size, camera: gs.Camera, shading: tuple, lit: tuple, passes: Passes, water=None,
                     first: list = ()) -> None:  # fmt: skip
        """The transparent techniques (``passes.transparent``) into the lit colour and alpha over the G-buffer's depth,
        ``first`` (rivers, lakes, the sea) then the rest farthest first, lit by the shading's inputs and extras
        (``lit``), with the scene's copies the water reads (frame-117-viewer.md 3 and 7), the sea's maps the inland
        water reads and the ground's."""
        lighting, sky = shading
        inputs, extras = lit
        models, ground = passes.transparent, passes.ground
        lent = {**(passes.sea.maps() if passes.sea is not None else {}), **(ground.maps if ground is not None else {})}
        shared = {**lighting.shared(inputs), **(water.inputs() if water is not None else {}),
                  **(extras.clouds.inputs() if extras.clouds is not None else {}), **lent}  # fmt: skip

        ground_fill = ground.fill() if ground is not None else None

        def fill(c: gs.Constants) -> None:
            lighting.fill(c, camera, sky, extras)
            if water is not None:
                water.fill(c)
            if ground_fill is not None:
                ground_fill(c)

        def distance(m) -> float:
            lo, hi = m.box()
            middle = np.asarray(m.world, float) @ np.r_[(lo + hi) / 2, 1.0]
            return float(np.linalg.norm(middle[:3] - camera.eye))

        ordered = [*first, *sorted(models, key=distance, reverse=True)]
        self._record(self.lit_fbo, ordered, size, camera, len(self.lit), fill, shared, clear=False)

    def _drop(self) -> None:
        # a framebuffer releases only its render targets: every attachment is the owner's to release
        for t in (*self.targets, *self.lit, self.depth, self.fbo, self.lit_fbo):
            if t is not None:
                t.release()
        self.targets, self.lit, self.depth, self.fbo, self.lit_fbo = [], [], None, None, None

    def release(self) -> None:
        self._drop()
        self.composite.release()
        if self.shadow is not None:
            self.shadow.release()
            self.shadow = None
        if self.info is not None:
            self.info.destroy()
            self.info = None
        self.targets, self.lit, self.size = [], [], None
