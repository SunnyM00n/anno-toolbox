"""Anno 2205's and 1800's models drawn forward-lit by the game's own shaders on QRhi (Direct3D 11): the sun's shadow
map by each material's shadow technique (``shadowforward``), then the opaque materials, then the blended ones far to
near, into an offscreen target composited over the view's picture; the engine's buffers filled by name, stand-ins
for what a model viewer has no frame for (the light clusters, the terrain).

``CForwardModel(ctx, draw)`` is one mesh group with its material's fields (``fields``) and maps (``maps``);
``CForwardFrame.render`` draws a list of them with a ``SCamera`` (``gameshader.direct_camera``). docs/rendering/shader-compile.md
section 11; the frame order the game uses is in section 9.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import gameshadow, shadowforward
from .fxcdraw import CFxcDraw, CSharedBuffers
from .nativepass import CComposite, draw_models

TextureFormat = QRhiTexture.Format
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


def fill_fields(constants: dict, fields: dict) -> None:
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
            gs.set_values(constants.values(), {name: value})
    gs.set_values(constants.values(), vectors)


def fill_frame(constants: dict, camera: gs.SCamera, light: dict) -> None:
    """The per-frame buffer: the camera and its matrices (row vectors, as the shaders' ``MUL(v, M)`` takes them; view
    space left-handed, as the soft fade and 1800's cascade choice read its depth), the screen (2205's ``cViewPort``,
    1800's ``cBackBufferSize`` and ``cViewport``: its hull's factors, its shadow's fade) and ``light`` (members by
    name: ``ambientforward.light_values``)."""
    # the values are worked out and packed once per layout while the camera and the light's items stay the same
    # objects (a frame builds them once, and a draw's buffers all share them), then their bytes are copied
    token = (camera, *light.keys(), *light.values())
    values: dict | None = None

    def write(buffer) -> None:
        nonlocal values
        if values is None:
            values = frame_values(camera, light)
        gs.set_values([buffer], values, ROW_ARRAYS)

    for buffer in constants.values():
        gs.replay(buffer, "forward frame", token, write)


def frame_values(camera: gs.SCamera, light: dict) -> dict:
    """The members ``fill_frame`` writes, by name."""
    hand = gs.left_handed_values(camera)
    width_px, height_px = (max(1, int(extent)) for extent in camera.size_px)
    return {
        **gs.camera_values(camera), **hand, "cMtxViewProjUnmirrored": camera.view @ camera.projection, "cMtxViewUnmirrored": hand["cMtxView"],
        "cMtxViewProjTex": camera.view @ camera.projection @ TEXTURE,
        "cViewportCropFactor": [1, 1, 0, 0], **ENGINE_OFF, **light, "cViewPort": [0, 0, width_px, height_px],
        "cBackBufferSize": [width_px, height_px], "cBackBufferSizeRec": [1 / width_px, 1 / height_px], "cViewport": [0, 0, width_px, height_px],
    }  # fmt: skip


def matrix_rows(matrices: list) -> np.ndarray:
    """[3, n, 4] floats: row r of each matrix (column vectors), as 2205's three row textures hold them."""
    return np.array([[matrix[row] for matrix in matrices] for row in range(3)], "<f4")


class CForwardModel:
    """One mesh group: ``draw`` (a ``CFxcDraw``), ``fields`` (the material's values by name), ``maps`` (the shader's
    texture name -> a QRhi texture or None while it loads), ``world`` (column vectors), ``palette`` per frame."""

    def __init__(self, ctx, draw: CFxcDraw) -> None:
        self.m_ctx, self.m_draw_call = ctx, draw
        self.m_fields: dict = {}
        self.m_maps: dict = {}
        self.m_world, self.m_palette, self.m_bones = np.identity(4), None, 0
        draw.m_fallbacks = dict(FALLBACKS)
        self.m_shadow: CFxcDraw | None = None  # the material's Obj2Shadow pass (a caster), None for none
        self.m_blended = False  # drawn in the blended pass, after the opaque ones
        self.m_water = False  # 2205's model water: the blended pass reads the copies of the opaque scene (SceneCopies)
        self.m_later: list[CForwardModel] = []  # the technique's further passes, drawn after this one (the planet)
        self.m_prepass: CFxcDraw | None = None  # 1800's depth and face normals pass (its ambient occlusion reads them)
        self.m_ssr: CFxcDraw | None = None  # 1800's model water: its screen-space reflection's trace (ssrforward)
        self.m_rows = [self._row_texture() for _ in range(3)]
        self._m_written: list = [None] * 3  # per row texture: (texels, bytes) as last uploaded

    def _row_texture(self):
        return self.m_ctx.texture((MATRIX_WIDTH, 1), 4, np.zeros((MATRIX_WIDTH, 4), "<f4").tobytes(), dtype="f4")

    def update_matrices(self) -> None:
        """Write the matrices into the row textures when they changed, only the texels in use (recorded with the
        frame, before its passes)."""
        rows = matrix_rows(self._matrices())
        count = rows.shape[1]
        for row, texture in enumerate(self.m_rows):
            raw = np.ascontiguousarray(rows[row]).tobytes()
            if self._m_written[row] != (count, raw):
                texture.write(raw, viewport=(0, 0, count, 1))
                self._m_written[row] = (count, raw)

    def _matrices(self) -> list:
        """The rigid matrix, or one per bone (column vectors)."""
        world = np.asarray(self.m_world, float)
        return (
            [world] * max(1, self.m_bones)
            if self.m_palette is None
            else [world @ bone_matrix for bone_matrix in self.m_palette]
        )

    def textures(self) -> dict:
        """The shader's textures by name this frame (the maps loaded so far, the matrix rows)."""
        maps = {k: texture for k, texture in self.m_maps.items() if texture is not None}
        maps.update({f"cModelConstantsMatrix{row}": row_texture.native for row, row_texture in enumerate(self.m_rows)})
        return maps

    def fill_model(self, constants: dict) -> None:
        """The model's own values: its material's fields, its instance word, its matrices."""
        fill_fields(constants, self.m_fields)
        if MATRIX_BUFFER in constants:  # 1800: the matrices in a buffer; white, dye white, no terrain to adapt to
            constants[MATRIX_BUFFER].set_rows("cModelMatrixBuffer", [matrix.T for matrix in self._matrices()])
            if "cbModel" in constants:  # the cascaded shadow pass reads its word elsewhere (fill_caster)
                constants["cbModel"].set("cModelInstanceData[0]", [0x0FFFFFFF, NO_TERRAIN_ADAPT, 0, 0])
        elif "cbModel" in constants:  # 2205: white, no damage, matrix 0 of the row textures
            constants["cbModel"].set("cModelInstanceData[0]", [-1, 0, 0, 0])

    def fill_caster(self, constants: dict) -> None:
        """The model's values in 1800's cascaded shadow pass: an instance per cascade, its index in bits 30-31 of the
        word the engine's culling writes (shader-compile.md section 11, "1800's shadow cascades")."""
        self.fill_model(constants)
        rows = [[(i << 30) | 0x0FFFFFFF, NO_TERRAIN_ADAPT, 0, 0] for i in range(gameshadow.COUNT)]
        gs.set_values(constants.values(), {"cModelBatchDataOffset": 0})
        gs.set_values(constants.values(), {"cModelGPUCulledInstanceData": rows}, ("cModelGPUCulledInstanceData",))

    def corners(self) -> np.ndarray:
        """The corners of the mesh's box where the model stands (the shadow's fit)."""
        return shadowforward.corners(self.m_draw_call.box(), self.m_world)

    def prepare(self, render_pass, signature, batch, camera: gs.SCamera, light: dict, extra: dict | None = None,
                draw: CFxcDraw | None = None) -> None:  # fmt: skip
        """Make what is missing and write this frame's constants (``extra``: textures of the frame by name) for the
        colour pass, or ``draw`` (the pre-pass)."""

        def fill(constants: dict) -> None:
            fill_frame(constants, camera, light)
            self.fill_model(constants)

        (draw or self.m_draw_call).prepare(render_pass, signature, batch, {**self.textures(), **(extra or {})}, fill)

    def draw(self, command_buffer, size_px) -> None:
        self.m_draw_call.draw(command_buffer, size_px)

    def release(self) -> None:
        self.m_draw_call.release()
        for call in (self.m_shadow, self.m_prepass, self.m_ssr):
            if call is not None:
                call.release()
        for model in self.m_later:
            model.release()
        for texture in self.m_rows:
            texture.release()
        self.m_rows = []


class CSceneCopies:
    """Copies of a frame's colour and depth that water sheets refract (1404's and 2205's model water), made with the
    first sheet and again with the size; ``take(color, depth, size_px)`` copies and returns (colour, depth) as the
    shaders bind them."""

    def __init__(self, ctx) -> None:
        self.m_ctx, self.m_size_px = ctx, None
        self.m_color = self.m_depth = self.m_color_fbo = self.m_depth_fbo = None
        self.m_passes: tuple[CComposite, CComposite] | None = None

    def take(self, color, depth, size_px: tuple[int, int]) -> tuple:
        if self.m_size_px != size_px:
            self._drop()
            self.m_color = self.m_ctx.native_texture(size_px, TextureFormat.RGBA16F)
            self.m_color_fbo = self.m_ctx.framebuffer([self.m_color])
            self.m_depth = self.m_ctx.native_texture(size_px, TextureFormat.R32F)
            self.m_depth_fbo = self.m_ctx.framebuffer([self.m_depth])
            self.m_size_px = size_px
        self.m_passes = self.m_passes or (CComposite(self.m_ctx, DEPTH_COPY_FS), CComposite(self.m_ctx, LIT_COPY_FS))
        self.m_passes[0].draw(self.m_depth_fbo, {"gDepth": depth}, {}, depth_test=False)
        self.m_passes[1].draw(self.m_color_fbo, {"gLit": color}, {}, depth_test=False)
        return self.m_color.native, self.m_depth.native

    def _drop(self) -> None:
        for texture in (self.m_color_fbo, self.m_color, self.m_depth_fbo, self.m_depth):
            if texture is not None:
                texture.release()
        self.m_color = self.m_depth = self.m_color_fbo = self.m_depth_fbo = self.m_size_px = None

    def release(self) -> None:
        self._drop()
        for copy_pass in self.m_passes or ():
            copy_pass.release()
        self.m_passes = None


class SPasses(NamedTuple):
    """A frame's passes besides the models': the particle ``effects`` (``particleforward.CForwardEffect``), whether
    the sun's shadow map is made (View > Rendering's switch), 1800's ambient occlusion (``ssaoforward.CSsao1800``)
    over the opaque models' pre-pass and its water's screen-space reflections (``ssrforward.CSsr1800``), None for
    none."""

    effects: tuple | list = ()
    shadows: bool = True
    ssao: object = None
    ssr: object = None


MODELS_ONLY = SPasses()


class CForwardFrame:
    """The offscreen colour (half float, linear) and depth the models are drawn into, and the composite of both into the
    view's target (it writes the depth, so what the view draws itself meets the models). The view resolves its own
    target after the last pass that draws into it, so a pass of ours cannot draw there directly."""

    def __init__(self, ctx, cascaded: bool = False) -> None:
        self.m_ctx, self.m_size_px, self.m_targets = ctx, None, []
        self.m_fbo = None
        self.m_cascaded = cascaded  # 1800: the sun's shadow in cascades
        self.m_shadow_size: int | None = None  # the cascades' map side by the rendering quality (None: gameshadow's)
        self.m_shadow_cascades = gameshadow.COUNT  # the cascades drawn and read (the rendering quality's)
        # the composite's factor on the models' colour: 4 for 1800's without its post chain (``postforward.STORED``)
        self.m_scale = 1.0
        self.m_shadow: shadowforward.CShadowMap | None = None
        self.m_composite = CComposite(ctx, COMPOSITE_FS)
        self.m_depth_copy: CComposite | None = None  # made with the first frame that draws particles
        self.m_read_depth = self.m_read_fbo = None
        self.m_copies: CSceneCopies | None = None  # made with the first frame that draws model water
        self.m_prez: tuple | None = None  # 1800's pre-pass targets: (normals, depth, framebuffer) of ``prez_size``
        self.m_prez_size = None
        # the colour passes' and the pre-pass's frame buffers, each written once a frame for all models (apart:
        # the pre-pass's values have no occlusion yet)
        self.m_shared, self.m_shared_prepass = CSharedBuffers(ctx), CSharedBuffers(ctx)

    def _targets(self, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self.release_targets()
        self.m_color = self.m_ctx.native_texture(size_px, TextureFormat.RGBA16F)
        self.m_depth = self.m_ctx.depth_renderbuffer(size_px)
        self.m_fbo = self.m_ctx.framebuffer([self.m_color], self.m_depth)
        self.m_read_depth = self.m_ctx.native_texture(size_px, TextureFormat.R32F, samples=READ_DEPTH_SAMPLES)
        self.m_read_fbo = self.m_ctx.framebuffer([self.m_read_depth])
        self.m_targets, self.m_size_px = [self.m_color, self.m_depth, self.m_read_depth], size_px

    def _copy_depth(self) -> dict:
        """The opaque models' depth into the particles' read-only copy; the copy by the name the shaders read."""
        self.m_depth_copy = self.m_depth_copy or CComposite(self.m_ctx, DEPTH_COPY_FS)
        self.m_depth_copy.draw(self.m_read_fbo, {"gDepth": self.m_depth}, {}, depth_test=False)
        return {READ_DEPTH: self.m_read_depth.native}

    def _shadow(self, models: list, light: dict, camera: gs.SCamera, is_enabled: bool = True) -> tuple[dict, dict]:
        """The sun's shadow map of the casters: the shadow buffer's values and the map by name (none without a
        caster or with ``on`` False: every point lit); ``cascaded`` (1800): four cascades seen by ``camera``, else
        2205's one map."""
        casters = [model for model in models if model.m_shadow is not None] if is_enabled else []
        if not casters or "cLgtDirection" not in light:
            return {"cShadowMethod": 0, "cMtxShadowVPT0": NO_SHADOW}, {}
        boxes = np.concatenate([model.corners() for model in casters])
        if self.m_cascaded:
            size_texels = self.m_shadow_size or gameshadow.SIZE
            values = shadowforward.cascades(camera, light["cLgtDirection"], boxes, size_texels, self.m_shadow_cascades)
            if self.m_shadow is not None and self.m_shadow.m_size_px != size_texels:  # the rendering quality changed
                self.m_shadow.release()
                self.m_shadow = None
            self.m_shadow = self.m_shadow or shadowforward.CShadowMap(self.m_ctx, size_texels, shared=True)
            for model in casters:  # a copy per cascade read: the ones past them are not drawn
                model.m_shadow.m_instances = values["cUsedShadowPartitions"]
            self.m_shadow.render(casters, {**light, **values}, lambda model, constants: model.fill_caster(constants))
            return values, {"cShadowMap": self.m_shadow.m_native}
        # water that casts nothing (2205's sea, a sheet) reads lit past the casters: the depth reaches over it too
        sheets = [model.corners() for model in models if model.m_shadow is None and getattr(model, "m_water", False)]
        found = shadowforward.fit(light["cLgtDirection"], boxes, np.concatenate(sheets) if sheets else None)
        self.m_shadow = self.m_shadow or shadowforward.CShadowMap(self.m_ctx, shared=True)
        self.m_shadow.render(casters, {**light, **found.values}, lambda model, constants: model.fill_model(constants))
        return found.values, {"cShadowMap": self.m_shadow.m_native}

    def render(self, target, size_px: tuple[int, int], models: list, camera: gs.SCamera, light: dict,
               maps: dict | None = None, passes: SPasses = MODELS_ONLY) -> None:  # fmt: skip
        """The shadow map, the opaque models, the blended ones far to near, then (after a copy of the opaque depth
        for their soft fade) the particle effects far to near into the offscreen target, then the composite over
        ``target``; ``light``: the frame's constants by name (``ambientforward``), ``maps`` the frame's textures by
        name (sky cubes, clouds); ``passes``: the optional ones."""
        effects, shadows, ssao, ssr = passes
        self._targets(size_px)
        ctx = self.m_ctx
        self.m_shared.begin()
        self.m_shared_prepass.begin()
        shadow, shadow_maps = self._shadow(models, light, camera, shadows)
        values, frame_maps = {**light, **shadow}, {**(maps or {}), **shadow_maps}
        occlusion = self._occlusion(ssao, models, size_px, camera, values, frame_maps)
        if occlusion is not None:  # the colour pass reads it (shader-compile.md 11 "1800's ambient occlusion")
            values, frame_maps = {**values, "cSSAOOffset": 0.0}, {**frame_maps, "cSSAOTex": occlusion}
        tracing = (
            [model for model in models if model.m_ssr is not None] if ssr is not None else []
        )  # the island view hands none
        reflected = ssr.trace(tracing, camera, values, frame_maps) if tracing else None
        if reflected is not None:  # shader-compile.md 11 "1800's screen-space reflections"
            values, frame_maps = {**values, **reflected[0]}, {**frame_maps, **reflected[1]}
        eye = np.asarray(camera.eye, float)
        order = [model for model in models if not model.m_blended]
        blended = sorted(
            (model for model in models if model.m_blended),
            key=lambda model: -float(np.linalg.norm(model.m_world[:3, 3] - eye)),
        )
        if any(model.m_water for model in blended):  # the water refracts the opaque scene (shader-compile.md 11)
            self._draw(order, size_px, camera, values, frame_maps, clear=True)
            self.m_copies = self.m_copies or CSceneCopies(ctx)
            copies = dict(zip(WATER_COPIES, self.m_copies.take(self.m_color, self.m_depth, size_px), strict=True))
            self._draw(blended, size_px, camera, values, {**frame_maps, **copies}, clear=False)
        else:
            self._draw(order + blended, size_px, camera, values, frame_maps, clear=True)
        if effects:
            read = self._copy_depth()
            far_first = sorted(effects, key=lambda model: -float(np.linalg.norm(model.m_world[:3, 3] - eye)))
            self._draw(far_first, size_px, camera, values, {**frame_maps, **read}, clear=False)
        if tracing:  # the frame's last pass: what the next frame's trace reads
            ssr.copy(self.m_color.native, self.m_depth.native, camera)
        self.m_composite.draw(target, {"gColor": self.m_color, "gDepth": self.m_depth},
                            {"flip": bool(ctx.m_flip_offscreen), "scale": float(self.m_scale)},
                            blend=(ctx.ONE, ctx.ONE_MINUS_SRC_ALPHA))  # fmt: skip

    def _occlusion(self, ssao, models: list, size_px, camera: gs.SCamera, values: dict, maps: dict):
        """1800's pre-pass of the opaque models (their depth and face normals) and the ambient occlusion over it: the
        texture the colour pass reads, None without ``ssao`` or a model to draw."""
        drawn = (
            [model for model in models if not model.m_blended and model.m_prepass is not None]
            if ssao is not None
            else []
        )
        if not drawn:
            return None
        if self.m_prez_size != size_px:
            self.drop_prepass()
            normals, depth = (
                self.m_ctx.native_texture(size_px, TextureFormat.RGBA8),
                self.m_ctx.depth_renderbuffer(size_px),
            )
            self.m_prez, self.m_prez_size = (normals, depth, self.m_ctx.framebuffer([normals], depth)), size_px
        normals, depth, fbo = self.m_prez
        for model in drawn:
            model.m_prepass.m_shared = self.m_shared_prepass
        draw_models(self.m_ctx, fbo, drawn, lambda model, render_pass, signature, batch: model.prepare(render_pass, signature, batch, camera, values, maps,
                    model.m_prepass), lambda model, command_buffer: model.m_prepass.draw(command_buffer, size_px), clear=True)  # fmt: skip
        return ssao.run(self.m_ctx, depth.native, normals.native, camera)

    def drop_prepass(self) -> None:
        """Release 1800's pre-pass targets (made again with the next frame that draws the ambient occlusion)."""
        for texture in self.m_prez or ():
            texture.release()
        self.m_prez, self.m_prez_size = None, None

    def _draw(self, order: list, size_px, camera: gs.SCamera, values: dict, maps: dict, *, clear: bool) -> None:
        """One pass of ``order`` into the offscreen target (``clear`` False: over what the last pass left)."""
        for model in order:  # the models' draws share the frame's buffers, the particles keep theirs
            if isinstance(model, CForwardModel):
                model.m_draw_call.m_shared = self.m_shared
        draw_models(
            self.m_ctx,
            self.m_fbo,
            order,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, camera, values, maps
            ),
            lambda model, command_buffer: model.draw(command_buffer, size_px),
            clear=clear,
        )

    def release_targets(self) -> None:
        # a framebuffer releases only its render targets: its attachments are released here too
        for texture in (*self.m_targets, self.m_fbo, self.m_read_fbo):
            if texture is not None:
                texture.release()
        self.m_targets, self.m_size_px, self.m_fbo, self.m_read_fbo, self.m_read_depth = [], None, None, None, None

    def release(self) -> None:
        self.release_targets()
        self.drop_prepass()
        if self.m_shadow is not None:
            self.m_shadow.release()
            self.m_shadow = None
        self.m_composite.release()
        for holder in (self.m_shared, self.m_shared_prepass):
            holder.release()
        for part in (self.m_depth_copy, self.m_copies):
            if part is not None:
                part.release()
        self.m_depth_copy = self.m_copies = None
