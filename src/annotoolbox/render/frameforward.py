"""Anno 2205's and 1800's models drawn forward-lit by the game's own shaders on QRhi (Direct3D 11): the sun's shadow
map by each material's shadow technique (``shadowforward``), then the opaque materials, then the blended ones far to
near, into an offscreen target composited over the view's picture; the engine's buffers filled by name, stand-ins
for what a model viewer has no frame for (the light clusters, the terrain).

``ForwardModel(ctx, draw)`` is one mesh group with its material's fields (``fields``) and maps (``maps``);
``ForwardFrame.render`` draws a list of them with a ``Camera`` (``gameshader.direct_camera``). docs/rendering/shader-compile.md
section 11; the frame order the game uses is in section 9.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import gameshadow, shadowforward
from .fxcdraw import FxcDraw, SharedBuffers
from .nativepass import Composite, draw_models

F = QRhiTexture.Format
COMPOSITE_FS = """
#version 330
uniform sampler2D gColor; uniform sampler2D gDepth; uniform bool flip; uniform float scale;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    float depth = texture(gDepth, uv).r;
    vec4 c = texture(gColor, uv);
    if (depth >= 1.0 && c.a <= 0.0 && max(c.r, max(c.g, c.b)) <= 0.0) discard;
    c.rgb *= scale;  // premultiplied: the colour alone
    // the depth holds the opaque models only (the blended ones write none here): where one is, the pixel is covered;
    // elsewhere the blended layers' premultiplied colour goes over the view's picture (additive light: alpha 0)
    color = depth < 1.0 ? vec4(c.rgb, 1.0) : c;
    // a blended layer alone (glass, sprites) has no depth here: it goes over the view's own ground and sky
    gl_FragDepth = depth < 1.0 ? depth : 0.0;
}
"""
# the opaque depth into the particles' read-only copy, texel for texel (shader-compile.md section 11 "Soft particles")
DEPTH_COPY_FS = """
#version 330
uniform sampler2D gDepth;
in vec2 vNdc; out vec4 color;
void main() { color = vec4(texelFetch(gDepth, ivec2(gl_FragCoord.xy), 0).r, 0.0, 0.0, 1.0); }
"""
LIT_COPY_FS = """
#version 330
uniform sampler2D gLit;
in vec2 vNdc; out vec4 color;
void main() { color = texelFetch(gLit, ivec2(gl_FragCoord.xy), 0); }
"""
TEXTURE = np.array([[0.5, 0, 0, 0], [0, -0.5, 0, 0], [0, 0, 1, 0], [0.5, 0.5, 0, 1]])  # clip space to texture space
WATER_COPIES = ("cColorBufferCopy", "cDepthBufferCopy")  # 2205's model water reads the scene behind it by these
READ_DEPTH = "cReadOnlyDepthBuffer"  # the copy as particle.fx reads it (a Texture2DMS)
READ_DEPTH_SAMPLES = 2  # Direct3D 11 views only a multisampled texture as Texture2DMS
# frame arrays, written element by element
ROW_ARRAYS = ("cPointLights", "cSpotLights", "cLightClusterBuffer", *shadowforward.CASCADE_ROWS)
NO_SHADOW = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0.5, 0.5, 0, 1]]  # every point at the map's middle: lit
NO_TERRAIN_ADAPT = 1 << 7  # 1800's instance word y: the model keeps its heights (a viewer has no terrain)
MATRIX_BUFFER = "cb_cModelMatrixBuffer"  # 1800's structured buffer of matrices, read as a constant buffer
MATRIX_WIDTH = 4096  # the matrix textures' width (the shaders index them as idx & 4095, idx >> 12)
# the stand-in a shader's texture reads by name when the material gives none: colour (rgba bytes)
FALLBACKS = {"cModelDiffTex": (255, 255, 255, 255), "cModelNormalTex": (128, 128, 255, 255),
             "cModelMetallicTex": (0, 0, 0, 255), "cLightClusterTex": (0, 0, 0, 0), "cLightIndexTex": (0, 0, 0, 0),
             "cIrrRedProbeTex": (0, 0, 0, 0), "cIrrGreenProbeTex": (0, 0, 0, 0), "cIrrBlueProbeTex": (0, 0, 0, 0),
             "cUndiscoveredMask": (0, 0, 0, 0), "cBlueNoiseTex": (128, 128, 128, 128),
             "cNoiseTex2": (128, 128, 128, 128),  # 1800's foam noise: the engine's file not read
             "cSmallWaveGradientMap": (0, 0, 0, 0),  # 1800's small waves off or loading: no gradient, still water
             # no cloud noise while the clouds load or are off: white would be a cloud over every point (no sun)
             "cCloudTex": (0, 0, 0, 0),
             # the sky cubes while the setting's own load, without them or switched off: a quarter grey (2205's sky
             # box as the sky and the environment, 1800's prefiltered cubes)
             "cSkyRenderTarget": (64, 64, 64, 255), "cEnvironmentCubeTex": (64, 64, 64, 255),
             "cPrefilteredSpecularCube0": (64, 64, 64, 255), "cPrefilteredSpecularCube1": (64, 64, 64, 255)}  # fmt: skip
# 1800's frame parts a model viewer has none of, switched off by their own factors: no probes (global light,
# large-scale occlusion), no screen-space occlusion, no shadow cascades, the environment cube unturned
ENGINE_OFF = {"cProbeFading": [0.0, 1.0], "cGIFactor": 0.0, "cLSAOFactor": 0.0, "cLSAOMaximum": 1.0,
              "cAOIntensity": 1.0, "cSOIntensity": 1.0, "cSunAO": 0.0, "cMtxEnvironmentRotation": np.identity(4),
              "cPrefilteredSpecCubeSizeLog2": 1.0, "cPrefilteredCubeMapLerpFac": 0.0, "cUsedShadowPartitions": 0,
              "cSunCamAmbientFactor": 1.0, "cSunCamAngleSharpness": 1.0, "cProbeTexSizeRec": [1.0, 1.0, 1.0],
              "cSSAOOffset": 1.0, "cUpscaleMipBiasGrad": 1.0, "cScreenReflectEnabled": 0.0}  # fmt: skip
# a material field written into a buffer by its name; "cDiffuseColor.r" is the first member of cDiffuseColor
COMPONENTS = {"r": 0, "g": 1, "b": 2, "a": 3, "x": 0, "y": 1, "z": 2, "w": 3}


def fill_fields(c: dict, fields: dict) -> None:
    """The material's fields into every buffer that has a member of that name (a vector's ``name.r`` one lane)."""
    vectors: dict[str, list] = {}
    for key, raw in fields.items():
        name, _, lane = key.partition(".")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if lane in COMPONENTS:
            vectors.setdefault(name, [1.0, 1.0, 1.0, 1.0])[COMPONENTS[lane]] = value
        else:
            gs.set_values(c.values(), {name: value})
    gs.set_values(c.values(), vectors)


def fill_frame(c: dict, cam: gs.Camera, light: dict) -> None:
    """The per-frame buffer: the camera and its matrices (row vectors, as the shaders' ``MUL(v, M)`` takes them; view
    space left-handed, as the soft fade and 1800's cascade choice read its depth), the screen (2205's ``cViewPort``,
    1800's ``cBackBufferSize`` and ``cViewport``: its hull's factors, its shadow's fade) and ``light`` (members by
    name: ``ambientforward.light_values``)."""
    hand = gs.left_handed_values(cam)
    w, h = (max(1, int(v)) for v in cam.size)
    values = {
        **gs.camera_values(cam), **hand, "cMtxViewProjUnmirrored": cam.view @ cam.proj, "cMtxViewUnmirrored": hand["cMtxView"],
        "cMtxViewProjTex": cam.view @ cam.proj @ TEXTURE,
        "cViewportCropFactor": [1, 1, 0, 0], **ENGINE_OFF, **light, "cViewPort": [0, 0, w, h],
        "cBackBufferSize": [w, h], "cBackBufferSizeRec": [1 / w, 1 / h], "cViewport": [0, 0, w, h],
    }  # fmt: skip
    gs.set_values(c.values(), values, ROW_ARRAYS)


def matrix_rows(mats: list) -> np.ndarray:
    """[3, n, 4] floats: row r of each matrix (column vectors), as 2205's three row textures hold them."""
    return np.array([[m[r] for m in mats] for r in range(3)], "<f4")


class ForwardModel:
    """One mesh group: ``draw`` (a ``FxcDraw``), ``fields`` (the material's values by name), ``maps`` (the shader's
    texture name -> a QRhi texture or None while it loads), ``world`` (column vectors), ``palette`` per frame."""

    def __init__(self, ctx, draw: FxcDraw) -> None:
        self.ctx, self.draw_call = ctx, draw
        self.fields: dict = {}
        self.maps: dict = {}
        self.world, self.palette, self.bones = np.identity(4), None, 0
        draw.fallbacks = dict(FALLBACKS)
        self.shadow: FxcDraw | None = None  # the material's Obj2Shadow pass (a caster), None for none
        self.blended = False  # drawn in the blended pass, after the opaque ones
        self.water = False  # 2205's model water: the blended pass reads the copies of the opaque scene (SceneCopies)
        self.later: list[ForwardModel] = []  # the technique's further passes, drawn after this one (the planet)
        self.prepass: FxcDraw | None = None  # 1800's depth and face normals pass (its ambient occlusion reads them)
        self.ssr: FxcDraw | None = None  # 1800's model water: its screen-space reflection's trace (ssrforward)
        self.rows = [self._row_texture() for _ in range(3)]
        self._written: list = [None] * 3  # per row texture: (texels, bytes) as last uploaded

    def _row_texture(self):
        return self.ctx.texture((MATRIX_WIDTH, 1), 4, np.zeros((MATRIX_WIDTH, 4), "<f4").tobytes(), dtype="f4")

    def update_matrices(self) -> None:
        """Write the matrices into the row textures when they changed, only the texels in use (recorded with the
        frame, before its passes)."""
        rows = matrix_rows(self._matrices())
        count = rows.shape[1]
        for r, tex in enumerate(self.rows):
            raw = np.ascontiguousarray(rows[r]).tobytes()
            if self._written[r] != (count, raw):
                tex.write(raw, viewport=(0, 0, count, 1))
                self._written[r] = (count, raw)

    def _matrices(self) -> list:
        """The rigid matrix, or one per bone (column vectors)."""
        w = np.asarray(self.world, float)
        return [w] * max(1, self.bones) if self.palette is None else [w @ m for m in self.palette]

    def textures(self) -> dict:
        """The shader's textures by name this frame (the maps loaded so far, the matrix rows)."""
        maps = {k: v for k, v in self.maps.items() if v is not None}
        maps.update({f"cModelConstantsMatrix{r}": tex.native for r, tex in enumerate(self.rows)})
        return maps

    def fill_model(self, c: dict) -> None:
        """The model's own values: its material's fields, its instance word, its matrices."""
        fill_fields(c, self.fields)
        if MATRIX_BUFFER in c:  # 1800: the matrices in a buffer; white, dye white, no terrain to adapt to
            c[MATRIX_BUFFER].set_rows("cModelMatrixBuffer", [m.T for m in self._matrices()])
            if "cbModel" in c:  # the cascaded shadow pass reads its word elsewhere (fill_caster)
                c["cbModel"].set("cModelInstanceData[0]", [0x0FFFFFFF, NO_TERRAIN_ADAPT, 0, 0])
        elif "cbModel" in c:  # 2205: white, no damage, matrix 0 of the row textures
            c["cbModel"].set("cModelInstanceData[0]", [-1, 0, 0, 0])

    def fill_caster(self, c: dict) -> None:
        """The model's values in 1800's cascaded shadow pass: an instance per cascade, its index in bits 30-31 of the
        word the engine's culling writes (shader-compile.md section 11, "1800's shadow cascades")."""
        self.fill_model(c)
        rows = [[(i << 30) | 0x0FFFFFFF, NO_TERRAIN_ADAPT, 0, 0] for i in range(gameshadow.COUNT)]
        gs.set_values(c.values(), {"cModelBatchDataOffset": 0})
        gs.set_values(c.values(), {"cModelGPUCulledInstanceData": rows}, ("cModelGPUCulledInstanceData",))

    def corners(self) -> np.ndarray:
        """The corners of the mesh's box where the model stands (the shadow's fit)."""
        return shadowforward.corners(self.draw_call.box(), self.world)

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, extra: dict | None = None,
                draw: FxcDraw | None = None) -> None:  # fmt: skip
        """Make what is missing and write this frame's constants (``extra``: textures of the frame by name) for the
        colour pass, or ``draw`` (the pre-pass)."""

        def fill(c: dict) -> None:
            fill_frame(c, cam, light)
            self.fill_model(c)

        (draw or self.draw_call).prepare(rp, signature, batch, {**self.textures(), **(extra or {})}, fill)

    def draw(self, cb, size) -> None:
        self.draw_call.draw(cb, size)

    def release(self) -> None:
        self.draw_call.release()
        for d in (self.shadow, self.prepass, self.ssr):
            if d is not None:
                d.release()
        for m in self.later:
            m.release()
        for tex in self.rows:
            tex.release()
        self.rows = []


class SceneCopies:
    """Copies of a frame's colour and depth that water sheets refract (1404's and 2205's model water), made with the
    first sheet and again with the size; ``take(color, depth, size)`` copies and returns (colour, depth) as the
    shaders bind them."""

    def __init__(self, ctx) -> None:
        self.ctx, self.size = ctx, None
        self.color = self.depth = self.color_fbo = self.depth_fbo = None
        self.passes: tuple[Composite, Composite] | None = None

    def take(self, color, depth, size: tuple[int, int]) -> tuple:
        if self.size != size:
            self._drop()
            self.color = self.ctx.native_texture(size, F.RGBA16F)
            self.color_fbo = self.ctx.framebuffer([self.color])
            self.depth = self.ctx.native_texture(size, F.R32F)
            self.depth_fbo = self.ctx.framebuffer([self.depth])
            self.size = size
        self.passes = self.passes or (Composite(self.ctx, DEPTH_COPY_FS), Composite(self.ctx, LIT_COPY_FS))
        self.passes[0].draw(self.depth_fbo, {"gDepth": depth}, {}, depth_test=False)
        self.passes[1].draw(self.color_fbo, {"gLit": color}, {}, depth_test=False)
        return self.color.native, self.depth.native

    def _drop(self) -> None:
        for t in (self.color_fbo, self.color, self.depth_fbo, self.depth):
            if t is not None:
                t.release()
        self.color = self.depth = self.color_fbo = self.depth_fbo = self.size = None

    def release(self) -> None:
        self._drop()
        for p in self.passes or ():
            p.release()
        self.passes = None


class Passes(NamedTuple):
    """A frame's passes besides the models': the particle ``effects`` (``particleforward.ForwardEffect``), whether
    the sun's shadow map is made (View > Rendering's switch), 1800's ambient occlusion (``ssaoforward.Ssao1800``)
    over the opaque models' pre-pass and its water's screen-space reflections (``ssrforward.Ssr1800``), None for
    none."""

    effects: tuple | list = ()
    shadows: bool = True
    ssao: object = None
    ssr: object = None


MODELS_ONLY = Passes()


class ForwardFrame:
    """The offscreen colour (half float, linear) and depth the models are drawn into, and the composite of both into the
    view's target (it writes the depth, so what the view draws itself meets the models). The view resolves its own
    target after the last pass that draws into it, so a pass of ours cannot draw there directly."""

    def __init__(self, ctx, cascaded: bool = False) -> None:
        self.ctx, self.size, self.targets = ctx, None, []
        self.fbo = None
        self.cascaded = cascaded  # 1800: the sun's shadow in cascades
        self.shadow_size: int | None = None  # the cascades' map side by the rendering quality (None: gameshadow's)
        self.shadow_cascades = gameshadow.COUNT  # the cascades drawn and read (the rendering quality's)
        # the composite's factor on the models' colour: 4 for 1800's without its post chain (``postforward.STORED``)
        self.scale = 1.0
        self.shadow: shadowforward.ShadowMap | None = None
        self.composite = Composite(ctx, COMPOSITE_FS)
        self.depth_copy: Composite | None = None  # made with the first frame that draws particles
        self.read_depth = self.read_fbo = None
        self.copies: SceneCopies | None = None  # made with the first frame that draws model water
        self.prez: tuple | None = None  # 1800's pre-pass targets: (normals, depth, framebuffer) of ``prez_size``
        self.prez_size = None
        # the colour passes' and the pre-pass's frame buffers, each written once a frame for all models (apart:
        # the pre-pass's values have no occlusion yet)
        self.shared, self.shared_prepass = SharedBuffers(ctx), SharedBuffers(ctx)

    def _targets(self, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self.release_targets()
        self.color = self.ctx.native_texture(size, F.RGBA16F)
        self.depth = self.ctx.depth_renderbuffer(size)
        self.fbo = self.ctx.framebuffer([self.color], self.depth)
        self.read_depth = self.ctx.native_texture(size, F.R32F, samples=READ_DEPTH_SAMPLES)
        self.read_fbo = self.ctx.framebuffer([self.read_depth])
        self.targets, self.size = [self.color, self.depth, self.read_depth], size

    def _copy_depth(self) -> dict:
        """The opaque models' depth into the particles' read-only copy; the copy by the name the shaders read."""
        self.depth_copy = self.depth_copy or Composite(self.ctx, DEPTH_COPY_FS)
        self.depth_copy.draw(self.read_fbo, {"gDepth": self.depth}, {}, depth_test=False)
        return {READ_DEPTH: self.read_depth.native}

    def _shadow(self, models: list, light: dict, cam: gs.Camera, on: bool = True) -> tuple[dict, dict]:
        """The sun's shadow map of the casters: the shadow buffer's values and the map by name (none without a
        caster or with ``on`` False: every point lit); ``cascaded`` (1800): four cascades seen by ``cam``, else
        2205's one map."""
        casters = [m for m in models if m.shadow is not None] if on else []
        if not casters or "cLgtDirection" not in light:
            return {"cShadowMethod": 0, "cMtxShadowVPT0": NO_SHADOW}, {}
        boxes = np.concatenate([m.corners() for m in casters])
        if self.cascaded:
            size = self.shadow_size or gameshadow.SIZE
            values = shadowforward.cascades(cam, light["cLgtDirection"], boxes, size, self.shadow_cascades)
            if self.shadow is not None and self.shadow.size != size:  # the rendering quality changed
                self.shadow.release()
                self.shadow = None
            self.shadow = self.shadow or shadowforward.ShadowMap(self.ctx, size, shared=True)
            for m in casters:  # a copy per cascade read: the ones past them are not drawn
                m.shadow.instances = values["cUsedShadowPartitions"]
            self.shadow.render(casters, {**light, **values}, lambda m, c: m.fill_caster(c))
            return values, {"cShadowMap": self.shadow.native}
        # water that casts nothing (2205's sea, a sheet) reads lit past the casters: the depth reaches over it too
        sheets = [m.corners() for m in models if m.shadow is None and getattr(m, "water", False)]
        found = shadowforward.fit(light["cLgtDirection"], boxes, np.concatenate(sheets) if sheets else None)
        self.shadow = self.shadow or shadowforward.ShadowMap(self.ctx, shared=True)
        self.shadow.render(casters, {**light, **found.values}, lambda m, c: m.fill_model(c))
        return found.values, {"cShadowMap": self.shadow.native}

    def render(self, target, size: tuple[int, int], models: list, cam: gs.Camera, light: dict,
               maps: dict | None = None, passes: Passes = MODELS_ONLY) -> None:  # fmt: skip
        """The shadow map, the opaque models, the blended ones far to near, then (after a copy of the opaque depth
        for their soft fade) the particle effects far to near into the offscreen target, then the composite over
        ``target``; ``light``: the frame's constants by name (``ambientforward``), ``maps`` the frame's textures by
        name (sky cubes, clouds); ``passes``: the optional ones."""
        effects, shadows, ssao, ssr = passes
        self._targets(size)
        ctx = self.ctx
        self.shared.begin()
        self.shared_prepass.begin()
        shadow, shadow_maps = self._shadow(models, light, cam, shadows)
        values, frame_maps = {**light, **shadow}, {**(maps or {}), **shadow_maps}
        occlusion = self._occlusion(ssao, models, size, cam, values, frame_maps)
        if occlusion is not None:  # the colour pass reads it (shader-compile.md 11 "1800's ambient occlusion")
            values, frame_maps = {**values, "cSSAOOffset": 0.0}, {**frame_maps, "cSSAOTex": occlusion}
        tracing = [m for m in models if m.ssr is not None] if ssr is not None else []  # the island view hands none
        reflected = ssr.trace(tracing, cam, values, frame_maps) if tracing else None
        if reflected is not None:  # shader-compile.md 11 "1800's screen-space reflections"
            values, frame_maps = {**values, **reflected[0]}, {**frame_maps, **reflected[1]}
        eye = np.asarray(cam.eye, float)
        order = [m for m in models if not m.blended]
        blended = sorted((m for m in models if m.blended), key=lambda m: -float(np.linalg.norm(m.world[:3, 3] - eye)))
        if any(m.water for m in blended):  # the water refracts the opaque scene (shader-compile.md 11)
            self._draw(order, size, cam, values, frame_maps, clear=True)
            self.copies = self.copies or SceneCopies(ctx)
            copies = dict(zip(WATER_COPIES, self.copies.take(self.color, self.depth, size), strict=True))
            self._draw(blended, size, cam, values, {**frame_maps, **copies}, clear=False)
        else:
            self._draw(order + blended, size, cam, values, frame_maps, clear=True)
        if effects:
            read = self._copy_depth()
            far_first = sorted(effects, key=lambda m: -float(np.linalg.norm(m.world[:3, 3] - eye)))
            self._draw(far_first, size, cam, values, {**frame_maps, **read}, clear=False)
        if tracing:  # the frame's last pass: what the next frame's trace reads
            ssr.copy(self.color.native, self.depth.native, cam)
        self.composite.draw(target, {"gColor": self.color, "gDepth": self.depth},
                            {"flip": bool(ctx.flip_offscreen), "scale": float(self.scale)},
                            blend=(ctx.ONE, ctx.ONE_MINUS_SRC_ALPHA))  # fmt: skip

    def _occlusion(self, ssao, models: list, size, cam: gs.Camera, values: dict, maps: dict):
        """1800's pre-pass of the opaque models (their depth and face normals) and the ambient occlusion over it: the
        texture the colour pass reads, None without ``ssao`` or a model to draw."""
        drawn = [m for m in models if not m.blended and m.prepass is not None] if ssao is not None else []
        if not drawn:
            return None
        if self.prez_size != size:
            self.drop_prepass()
            normals, depth = self.ctx.native_texture(size, F.RGBA8), self.ctx.depth_renderbuffer(size)
            self.prez, self.prez_size = (normals, depth, self.ctx.framebuffer([normals], depth)), size
        normals, depth, fbo = self.prez
        for m in drawn:
            m.prepass.shared = self.shared_prepass
        draw_models(self.ctx, fbo, drawn, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, values, maps,
                    m.prepass), lambda m, cb: m.prepass.draw(cb, size), clear=True)  # fmt: skip
        return ssao.run(self.ctx, depth.native, normals.native, cam)

    def drop_prepass(self) -> None:
        """Release 1800's pre-pass targets (made again with the next frame that draws the ambient occlusion)."""
        for t in self.prez or ():
            t.release()
        self.prez, self.prez_size = None, None

    def _draw(self, order: list, size, cam: gs.Camera, values: dict, maps: dict, *, clear: bool) -> None:
        """One pass of ``order`` into the offscreen target (``clear`` False: over what the last pass left)."""
        for m in order:  # the models' draws share the frame's buffers, the particles keep theirs
            if isinstance(m, ForwardModel):
                m.draw_call.shared = self.shared
        draw_models(self.ctx, self.fbo, order, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, values, maps),
                    lambda m, cb: m.draw(cb, size), clear=clear)  # fmt: skip

    def release_targets(self) -> None:
        # a framebuffer releases only its render targets: its attachments are released here too
        for t in (*self.targets, self.fbo, self.read_fbo):
            if t is not None:
                t.release()
        self.targets, self.size, self.fbo, self.read_fbo, self.read_depth = [], None, None, None, None

    def release(self) -> None:
        self.release_targets()
        self.drop_prepass()
        if self.shadow is not None:
            self.shadow.release()
            self.shadow = None
        self.composite.release()
        for holder in (self.shared, self.shared_prepass):
            holder.release()
        for part in (self.depth_copy, self.copies):
            if part is not None:
                part.release()
        self.depth_copy = self.copies = None
