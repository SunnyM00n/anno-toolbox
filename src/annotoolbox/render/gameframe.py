"""117's frame on the QRhi device, as far as it runs: the G-buffer in the engine's formats and depth (1 near, 0 far),
the models drawn into it by the game's own shaders (``gamedraw``), the game's deferred shading over it (``gamelight``,
a compute pass) or a stand-in light of ours, and a composite into the view's target.

``CGameFrame(ctx)`` is the entry: ``render(target, size_px, models, camera)`` records the passes; the composite writes
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
from .gamelight import CDeferredShading, SExtras, SSky
from .nativepass import CComposite, draw_models
from .rhidevice import CTexture

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
class SLight:
    """The stand-in light's sun (towards it), its colour, the sky's and the background's; the exposure of the
    composite's own finish."""

    sun: tuple = (0.4, 0.8, 0.3)
    sun_color: tuple = (2.2, 2.1, 1.9)
    sky: tuple = (0.35, 0.42, 0.55)
    background: tuple = (0.18, 0.2, 0.24)
    exposure: float = 1.0


LEFT_HANDED = np.diag([1.0, 1.0, -1.0, 1.0])  # our right-handed view into the engine's: +z ahead


@dataclass
class SPasses:
    """The frame's passes besides the G-buffer: the shadow casters (``gamedraw.CGameModel``s of the effects' shadow
    technique) with the scene's bounds (world lo, hi) the cascades are fitted to."""

    casters: list = field(default_factory=list)
    box: tuple | None = None
    lights: list = field(default_factory=list)  # lightclusters' lights, in the scene's axes
    fit: object = None  # shadowfit.CShadowFit: the game's analysis fits the cascades (None: the CPU fits them)
    transparent: list = field(default_factory=list)  # gamedraw.CGameModel of the effects' Transparent techniques
    sky: object = None  # gamesky.CSkyPasses: the game's sky passes light the frame (None: the look's light)
    water: object = None  # gamewater.CSceneCopies: the copies the water reads, made before the transparent pass
    atmosphere: object = None  # gameatmosphere.CAtmosphere: the visible sky and haze (needs ``sky``)
    look: object = None  # gameatmosphere.SLook: its blur and fade
    clouds: object = None  # gameclouds.CClouds: the clouds in the sky and their shadow (needs ``atmosphere``)
    sea: object = None  # gamesea.CSea: the sea by the game's ocean shader, the transparent pass's first draw
    inland: object = None  # gameinland.CInlandWater: rivers and lakes by the game's shader, drawn before the sea
    ground: SGround | None = None  # the session's terrain the models' terrain adaptation reads (None: none)
    # the native backend's draws, recorded first in the G-buffer's and the shadow map's passes: ``gbuffer(command_buffer, camera,
    # size)``, ``shadow(command_buffer, camera, cascades, info, side)``, ``casters`` (any to draw) (``terrainnative.SFrameDraws``)
    native: object = None
    # the objects by the native backend, recorded last in both passes: like ``native`` (``objectsnative.SFrameDraws``)
    objects: object = None
    # the water and the transparent parts by the native backend, each run of them recorded in its place of the
    # transparent pass (``waternative.SFrameWater``)
    native_water: object = None
    # the shading, sky, haze and clouds by the native backend (``shadingnative.CNativeShading``; its cascades' fit is
    # ``fit``): ``shade`` and ``atmosphere`` in place of ``_shading`` and the atmosphere's run
    shading: object = None


@dataclass
class SGround:
    """The session's terrain as the terrain, the models and the decals read it beyond their own: its size, its maps
    by the shaders' names (the coarse heights and displacement, the grit and cliff coverage and textures), the
    frame's values that go with them (the grit's) (rendering/island-117-view.md "Phase 5")."""

    size: float
    maps: dict
    values: dict = field(default_factory=dict)

    def fill(self):
        """The pass's fill writing ``cbPerFrame``'s terrain values, keyed so a still frame skips it (``stamp_of``);
        worked out once per layout and copied into every buffer of it (a moving camera fills every draw's)."""
        ground_size = self.size
        values = {
            **self.values,
            "cTerrainSize": [ground_size, ground_size, 1 / ground_size, 1 / ground_size],
            "cTerrainHeightTexIsValid": 1,
        }
        token = (ground_size, str(self.values))

        def write(constants: gs.CConstants) -> None:
            for name, value in values.items():
                constants.set(name, value)

        def fill(constants: gs.CConstants) -> None:
            gs.replay(constants, "ground", token, write)

        fill.key = fill.common_key = (
            "ground",
            ground_size,
            tuple(id(map_texture) for map_texture in self.maps.values()),  # the fill writes size and values only
            token[1],
        )
        return fill


class CGameFrame:
    """The G-buffer of one view and the passes over it (module docstring)."""

    def __init__(self, ctx, has_stencil: bool = False) -> None:
        self.m_ctx = ctx
        # the depth target holds the engine's stencil bits too (the cut-outs, the water's)
        self.m_has_stencil = has_stencil
        self.m_size_px: tuple[int, int] | None = None
        self.m_targets: list = []
        self.m_lit: list = []
        self.m_depth = self.m_fbo = self.m_lit_fbo = None
        self.m_shadow: gameshadow.CShadowMap | None = None
        self.m_shadow_size = gameshadow.SIZE  # the map's side (``set_quality``)
        self.m_shadow_cascades = gameshadow.COUNT  # the cascades drawn and read (``set_quality``)
        self.m_cascades: gameshadow.SCascades | None = None
        self.m_info = None  # cShadowInformation: the cascades as the casters and the shading read them
        self.m_ray = np.identity(4)  # our camera's inverse view-projection: which way a pixel looks (the composite)
        self.m_planes = (0.1, 100.0, 100.0)  # near, the view's far plane, the G-buffer's (the composite's depth)
        self.m_composite = CComposite(ctx, LIGHT_FS)

    def set_quality(self, quality) -> None:
        """The rendering quality's shadow map side and cascade count (``render.quality.SQuality``; None: the best),
        from the next frame (the map is made again when its side changed); the render scale is the caller's: it
        hands the scaled size to ``camera`` and ``render``."""
        side = quality.shadow_size if quality is not None else gameshadow.SIZE
        if side != self.m_shadow_size and self.m_shadow is not None:
            self.m_shadow.release()
            self.m_shadow = None
        self.m_shadow_size = side
        self.m_shadow_cascades = quality.shadow_cascades if quality is not None else gameshadow.COUNT

    def _gbuffer(self, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        ctx = self.m_ctx
        self.m_targets = [
            ctx.native_texture(size_px, texture_format, srgb=ALBEDO_SRGB and k == 1)
            for k, texture_format in enumerate(GBUFFER)
        ]
        if self.m_has_stencil:
            self.m_depth = self._depth_stencil(size_px)
        else:
            self.m_depth = ctx.depth_renderbuffer(size_px)
        self.m_fbo = ctx.framebuffer(self.m_targets, self.m_depth)
        formats = (QRhiTexture.Format.RGBA16F, QRhiTexture.Format.R8)
        self.m_lit = [ctx.native_texture(size_px, texture_format, storage=True) for texture_format in formats]
        self.m_lit_fbo = ctx.framebuffer(self.m_lit, self.m_depth)  # RTT_DEFAULT: the transparent pass draws into it
        self.m_size_px = size_px

    def _depth_stencil(self, size_px) -> CTexture:
        depth = CTexture(self.m_ctx, tuple(size_px), 1, "f4", "depth", m_qformat=QRhiTexture.Format.D32FS8)
        depth.create()
        return depth

    def camera(self, size_px: tuple[int, int], eye, target, fov_rad: float, near: float, far: float,
               game_far: float = 0.0) -> gs.SCamera:  # fmt: skip
        """The engine's camera for ``size_px``: its view space left-handed (+z ahead, as the shadow cascades and the
        light clusters read view depth), its y turned over where the device stores images upside down against
        OpenGL, so the G-buffer reads as our own targets do (rhipass, "the right way up"). ``game_far``: the
        engine's far plane when it lies beyond the view's ``far`` (the clouds' rays end there; the cascades still
        end at ``far``, the composite writes the view's own depth)."""
        self.m_planes = (near, far, max(far, game_far))
        proj = LEFT_HANDED @ gs.perspective(
            fov_rad, size_px[0] / max(1, size_px[1]), near, self.m_planes[2], is_reversed=True
        )
        if self.m_ctx.m_flip_offscreen:
            proj = proj @ np.diag([1.0, -1.0, 1.0, 1.0])
        view = gs.look_at(np.asarray(eye, float), np.asarray(target, float)) @ LEFT_HANDED
        self.m_ray = np.linalg.inv(
            perspective(fov_rad, size_px[0] / max(1, size_px[1]), near, far) @ look_at(eye, target)
        )
        return gs.SCamera(np.asarray(eye, float), view, proj, near, self.m_planes[2], size_px, far)

    def render(self, target, size_px: tuple[int, int], models: list, camera: gs.SCamera, light: SLight | None = None,
               finish: bool = True, shading: tuple[CDeferredShading, SSky] | None = None,
               passes: SPasses | None = None) -> None:  # fmt: skip
        """The G-buffer pass with ``models`` (``gamedraw.CGameModel``), the sun's shadow map when ``passes`` has
        casters, the game's deferred shading when ``shading`` is given, then the composite into ``target`` (a
        framebuffer of the device; ``finish``: the whole picture, else the lit models over what it holds)."""
        light = light or SLight()
        passes = passes or SPasses()
        self._gbuffer(size_px)
        ground = passes.ground
        native, objects = passes.native, passes.objects
        external = None
        if native is not None:

            def external(
                command_buffer,
            ) -> None:  # the cut-out volumes right after the terrain (``FrameDraws.cutouts``)
                native.gbuffer(command_buffer, camera, size_px)
                if objects is not None:
                    objects.cutouts(command_buffer, camera, size_px)

        after = (
            (lambda command_buffer: objects.gbuffer(command_buffer, camera, size_px)) if objects is not None else None
        )
        self._record(self.m_fbo, models, size_px, camera, len(GBUFFER), ground and ground.fill(), ground and ground.maps,
                     external=external, after=after)  # fmt: skip
        self.m_cascades = None
        casters = passes.casters or any(draw_pass is not None and draw_pass.casters for draw_pass in (native, objects))
        if shading is not None and casters and passes.box is not None:
            self._shadow(size_px, camera, shading[1].sun, passes)
        sky_drawn = self._lit(size_px, camera, shading, passes) if shading is not None else False
        textures = dict(zip(("gNormal", "gAlbedo", "gMaterial", "gDepth", "gLit"),
                            (*self.m_targets[:3], self.m_depth, self.m_lit[0]), strict=True))  # fmt: skip
        sun_direction = np.asarray(light.sun, float)
        uniforms = {
            "lit": shading is not None, "litSky": sky_drawn, "invViewProj": gl(self.m_ray),
            "sunDir": tuple(sun_direction / np.linalg.norm(sun_direction)), "sunColor": tuple(light.sun_color), "sky": tuple(light.sky),
            "background": tuple(light.background), "viewDir": tuple(-float(component) for component in camera.view[:3, 2]),
            "finish": finish, "exposure": light.exposure, "planes": self.m_planes,
        }  # fmt: skip
        # the game's sky at the far plane meets the cleared depth (<=); the whole picture is drawn untested
        self.m_composite.draw(target, textures, uniforms, depth_test=not finish, depth_func="<=")

    def _record(self, framebuffer, models: list, size_px, camera: gs.SCamera, targets: int, fill=None, shared=None,
                clear: bool = True, external=None, after=None, **native) -> None:  # fmt: skip
        """One pass of ``models`` (``gamedraw.CGameModel``) into ``framebuffer``, its depth cleared to the engine's far (0);
        ``native``: ``draw_models``' ``native`` and ``before``."""

        draw_models(
            self.m_ctx,
            framebuffer,
            models,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, camera, fill, shared
            ),
            lambda model, command_buffer: model.draw(command_buffer, size_px),
            targets=targets,
            depth=0.0,
            clear=clear,
            external=external,
            after=after,
            **native,
        )

    def _lit(self, size_px, camera: gs.SCamera, shading: tuple, passes: SPasses) -> bool:
        """The deferred shading, the copies and the transparent pass, then the visible sky and haze over the lit
        colour (``passes.atmosphere``, with the sky passes' tables); True when the sky was drawn so."""
        clusters = (
            lightclusters.build(passes.lights, camera.view, camera.projection, camera.far) if passes.lights else None
        )
        clouds = passes.clouds if passes.clouds is not None and not passes.clouds.m_error else None
        native = passes.shading if passes.shading is not None and not passes.shading.m_error else None
        if native is not None:
            inputs, extras = native.shade(self.m_ctx, self, size_px, camera, *shading, clusters, clouds)
            if passes.native_water is not None:
                passes.native_water.overlay = native.overlay(self)
        else:
            inputs, extras = self._shading(size_px, camera, *shading, clusters, passes.sky, clouds)
        sea = passes.sea.model(camera) if passes.sea is not None else None
        inland = passes.inland.models() if passes.inland is not None else []
        if passes.transparent or sea is not None or inland:
            water = passes.water if passes.water is not None and not passes.water.m_error else None
            if water is not None:
                water.run(self.m_ctx, self.m_lit[0], self.m_depth, size_px, camera)
            if sea is not None and water is not None:  # the coast waves' step over the depth just drawn
                passes.sea.simulate(
                    self.m_ctx, {**water.inputs(), "cDepthBuffer": self.m_depth.native}, camera, water.fill
                )
            # rivers and lakes first: their depth keeps the sea under them (the engine's cut-outs and stencil)
            first = [*inland, *([sea] if sea is not None else [])]
            if passes.native_water is not None:  # drawn by the shipped shaders, in the same places
                first = passes.native_water.runs(first)
            self._transparent(size_px, camera, shading, (inputs, extras), passes, water, first)
        if native is not None:
            return native.atmosphere(self.m_ctx, self, size_px, camera, shading[1], passes, clouds)
        atmosphere, sky = passes.atmosphere, passes.sky
        if atmosphere is None or atmosphere.m_error or sky is None or sky.m_error:
            return False
        tables = {"cAtmosphereTransmittanceLUT": sky.m_transmittance, "cAtmosphereMultiscatLUT": sky.m_multiscat}
        named = {**shading[0].shared(inputs), **tables, "cDepthBuffer": self.m_depth.native}
        atmosphere.run(self.m_ctx, self.m_lit_fbo, size_px, camera, shading[1], named, passes.look, clouds)
        return True

    def _shadow(self, size_px, camera: gs.SCamera, sun, passes: SPasses) -> None:
        """The cascades fitted (by the game's analysis over the depth just drawn, else on the CPU to the scene's
        bounds) and the casters drawn into the shadow map."""
        if self.m_shadow is None:  # with stencil the cut-out volumes count in it (``CutOutShadow``'s bits)
            side = self.m_shadow_size
            own = self._depth_stencil((side, side)) if self.m_has_stencil else None
            self.m_shadow = gameshadow.CShadowMap(self.m_ctx, side, own)
        if self.m_info is None:
            self.m_info = storage_buffer(self.m_ctx.m_rhi, gameshadow.INFO_BYTES * gameshadow.COUNT)
        fit = passes.fit if passes.fit is not None and not passes.fit.m_error else None
        side, used = self.m_shadow.m_size_px, self.m_shadow_cascades
        read = False  # the analysis copies its result out this frame: the buffer is left as a copy's source
        if fit is not None:
            read = fit.m_read_back or getattr(fit, "m_pinned", False)  # the native fit leaves it a copy's source
            fit.run(self.m_ctx, self.m_depth, size_px, camera, sun, self.m_info)
            self.m_cascades = gameshadow.SCascades((), gs.light_travel(sun), camera.shadow_distance, True, side, used)
        else:
            self.m_cascades = replace(gameshadow.fit(camera, passes.box, sun), size=side, used=used)
        native, objects, cascades, info_target = passes.native, passes.objects, self.m_cascades, (self.m_info, read)
        external = after = None
        if native is not None:  # every native caster before QRhi's: the information's state is the analysis's

            def external(
                command_buffer,
            ) -> None:  # the terrain's shadow cleared inside the cut-outs before any model casts
                native.shadow(command_buffer, camera, cascades, info_target, side)
                if objects is not None:
                    objects.cutout_shadow(command_buffer, camera, cascades, info_target, side)
                    objects.shadow(command_buffer, camera, cascades, info_target, side)

        elif objects is not None:  # after QRhi's terrain and cut-outs, which bind the information as a storage buffer
            # and leave it in unordered access (a draw only made ahead binds nothing: ``desktopgameobjects.CAheadRun``)
            late = (self.m_info, read and not any(not getattr(caster, "ahead", False) for caster in passes.casters))

            def after(command_buffer) -> None:
                objects.shadow(command_buffer, camera, cascades, late, side)

        self.m_shadow.render(passes.casters, camera, self.m_cascades, self.m_info, passes.ground, external, after)

    def _shading(self, size_px, camera: gs.SCamera, shading: CDeferredShading, sky: SSky, clusters=None, passes=None,
                 clouds=None):  # fmt: skip
        """The deferred shading's compute pass from the G-buffer into ``lit``, lit by the game's sky passes when
        ``passes`` (``gamesky.CSkyPasses``) is given, the clouds' shadow over it with ``clouds``; returns its inputs
        and extras (the transparent pass's)."""
        names = ("cGBuffer0", "cGBuffer1", "cGBuffer2")
        inputs = {**{name: texture.native for name, texture in zip(names, self.m_targets, strict=False)}, "cDepth": self.m_depth.native,
                  "cDstColor": self.m_lit[0].native, "cDstAlpha": self.m_lit[1].native}  # fmt: skip
        if self.m_cascades is not None:
            inputs["cShadowMap"] = self.m_shadow.m_texture.native
            inputs["cShadowInformation"] = self.m_info
        if passes is not None and not passes.m_error:
            passes.run(self.m_ctx, sky, clouds)  # only when the light or the clouds changed: the results stay
            inputs["cLightBuffer"], inputs["cPrefilteredSpecularCube"] = passes.m_light, passes.m_reflection
        if clouds is not None:
            clouds.m_shadowed = False
            if self.m_cascades is not None:  # the clouds' transmittance along the sun, in the cascades' parts
                clouds.shadow(self.m_ctx, camera, sky, self.m_cascades, {**shading.shared(inputs), **inputs})
            inputs.update(clouds.inputs())
        extras = SExtras(self.m_cascades, clusters, clouds)

        def compute_pass(command_buffer, batch) -> None:
            shading.prepare(batch, inputs, camera, sky, extras)
            command_buffer.beginComputePass(batch)
            shading.dispatch(command_buffer, shading.tiles(size_px))
            command_buffer.endComputePass()

        self.m_ctx.native_pass(compute_pass)
        return inputs, extras

    def _transparent(self, size_px, camera: gs.SCamera, shading: tuple, lit: tuple, passes: SPasses, water=None,
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

        def fill(constants: gs.CConstants) -> None:
            lighting.fill(constants, camera, sky, extras)
            if water is not None:
                water.fill(constants)
            if ground_fill is not None:
                ground_fill(constants)

        def distance(model) -> float:
            low, high = model.box()
            middle = np.asarray(model.m_world, float) @ np.r_[(low + high) / 2, 1.0]
            return float(np.linalg.norm(middle[:3] - camera.eye))

        ordered = [*first, *sorted(models, key=distance, reverse=True)]
        native = {}
        if passes.native_water is not None:
            frame = passes.native_water
            native = {"native": lambda command_buffer, run: frame.record(command_buffer, run, camera, [fill], shared, size_px),
                      "before": lambda batch: frame.upload(batch, ordered)}  # fmt: skip
        self._record(self.m_lit_fbo, ordered, size_px, camera, len(self.m_lit), fill, shared, clear=False, **native)

    def _drop(self) -> None:
        # a framebuffer releases only its render targets: every attachment is the owner's to release
        for texture in (*self.m_targets, *self.m_lit, self.m_depth, self.m_fbo, self.m_lit_fbo):
            if texture is not None:
                texture.release()
        self.m_targets, self.m_lit, self.m_depth, self.m_fbo, self.m_lit_fbo = [], [], None, None, None

    def release(self) -> None:
        self._drop()
        self.m_composite.release()
        if self.m_shadow is not None:
            self.m_shadow.release()
            self.m_shadow = None
        if self.m_info is not None:
            self.m_info.destroy()
            self.m_info = None
        self.m_targets, self.m_lit, self.m_size_px = [], [], None
