"""Anno 117's post chain by the game's own pixel shaders: the colour grade (``posteffect_colorgrade.fx``) and the tone
map (``posteffect_tonemap.fx``) over the view's HDR colour, each a full-screen triangle into a half-float target.

``CGamePost(rhi, stages)`` is the entry (the passes' vertex and pixel shaders, ``STAGES``); ``render(ctx,
source, size, inputs, exposure)`` records both passes and returns the tone-mapped (linear, 0..1) texture the view's
final pass encodes as sRGB (times the eye adaptation's exposure when handed one). What is filled:
docs/rendering/frame-117-viewer.md sections 5 and 6.
"""

from __future__ import annotations

from functools import partial

import numpy as np
from PySide6.QtGui import (
    QRhiBuffer,
    QRhiGraphicsPipeline,
    QRhiTexture,
    QRhiVertexInputAttribute,
    QRhiVertexInputLayout,
    QRhiViewport,
)

from . import gameshader as gs
from .eyeadapt import GCF_EYE_ADAPT
from .gamebind import CResources
from .gamedraw import CProgram, immutable_buffer
from .gamepipeline import new_pipeline, target_blends, vertex_layout
from .nativepass import draw_pass
from .renderstates import SPassState

PASSES = ("grade", "tonemap")
# "<pass>.vs" / "<pass>.ps" -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles
STAGES = {"grade.vs": ("data/shaders/fx/posteffect_colorgrade.fx", "ColorGrade", "vsColorGrade"),
          "grade.ps": ("data/shaders/fx/posteffect_colorgrade.fx", "ColorGrade", "psColorGrade"),
          "tonemap.vs": ("data/shaders/fx/posteffect_tonemap.fx", "Tonemap", "vsTonemap"),
          "tonemap.ps": ("data/shaders/fx/posteffect_tonemap.fx", "Tonemap", "psTonemap")}  # fmt: skip
# the full-screen triangle: clip position, texture coordinate (Direct3D's: v down, row 0 at the top)
TRIANGLE = {"float3": [(-1, -1, 0), (3, -1, 0), (-1, 3, 0)], "float2": [(0, 1), (2, 1), (0, -1)]}
# the values of the ambient and globals.xml (rendering/lighting.md 6); the grade cube's weight is the engine's [E]
CONSTANTS = {"cExposure": 0.55, "cTonemapLowEdge": 0.1, "cTonemapLowPower": 0.7, "cUsedLUTTextures": 1,
             "cLUTWeights": (1.0, 0.0, 0.0), "cHDRExposure": 0.0, "cViewportCropFactor": (1.0, 1.0),
             "cUpscaleFactor": 1.0, "cUpscaleMipBiasGrad": 1.0}  # fmt: skip


class CPostPass:
    """One of the game's full-screen pixel shaders into a target (its resources by name from ``inputs``): a
    triangle of ``PostEffectVertex``, or, for a vertex shader without inputs (``vsCommonFullScreenQuad``), a strip of
    four vertices from their ids. ``state``: the technique's blends per target and depth (None: one target, no
    blend, no depth), ``depth_always``: the depth written whatever it was (``ZFunc ALWAYS``)."""

    def __init__(self, rhi, program: CProgram, state: SPassState | None = None, depth_always: bool = False) -> None:
        self.m_rhi, self.m_program, self.m_state, self.m_depth_always = rhi, program, state, depth_always
        self.m_constants = {name: gs.CConstants(cbuffer) for name, cbuffer in program.m_cbuffers.items()}
        self.m_res = CResources(rhi, program.m_numbers, program.m_stages.__getitem__, self.m_constants,
                             lambda name: program.m_vertex_stage.types.get(name) or program.m_pixel_stage.types.get(name) or "")  # fmt: skip
        self.m_pipeline = self.m_vbuf = None
        self.m_strip = not program.m_vertex_stage.inputs
        self.m_layout = QRhiVertexInputLayout()
        self.m_error = ""

    def _vertices(self, batch) -> None:
        data, attributes, offset = [], [], 0
        for inp in self.m_program.m_vertex_stage.inputs:
            values = TRIANGLE.get(inp.type, TRIANGLE["float2"])
            data.append(np.asarray(values, np.float32))
            vertex_format = getattr(QRhiVertexInputAttribute.Format, f"Float{len(values[0])}")
            attributes.append(QRhiVertexInputAttribute(0, inp.location, vertex_format, offset))
            offset += 4 * len(values[0])
        raw = np.concatenate(data, axis=1).tobytes()
        self.m_vbuf = immutable_buffer(self.m_rhi, batch, QRhiBuffer.UsageFlag.VertexBuffer, raw)
        self.m_res.m_keep.append(self.m_vbuf)
        self.m_layout = vertex_layout([offset], attributes)

    def prepare(self, batch, render_pass, inputs: dict, fill, targets: int = 1) -> None:
        """Bind ``inputs``, make the pipeline for ``render_pass`` (``targets`` colour targets) once, write the constants."""
        if self.m_vbuf is None and not self.m_strip:
            self._vertices(batch)
        self.m_res.rebind(batch, inputs)
        if self.m_pipeline is None and not self.m_error:
            blends = target_blends(self.m_state.blends if self.m_state is not None else (), targets)
            graphics_pipeline = new_pipeline(
                self.m_rhi, self.m_program, self.m_layout, self.m_res.m_srb, render_pass, 1, blends
            )
            if self.m_strip:
                graphics_pipeline.setTopology(QRhiGraphicsPipeline.Topology.TriangleStrip)
            if self.m_depth_always:
                graphics_pipeline.setDepthTest(True)
                graphics_pipeline.setDepthWrite(True)
                graphics_pipeline.setDepthOp(QRhiGraphicsPipeline.CompareOp.Always)
            if graphics_pipeline.create():
                self.m_pipeline = graphics_pipeline
                self.m_res.m_keep.append(graphics_pipeline)
            else:
                self.m_error = "the backend refused a post effect's pipeline"
        self.m_res.write(batch, fill)

    def draw(self, command_buffer, size_px) -> None:
        if self.m_pipeline is None:
            return
        command_buffer.setGraphicsPipeline(self.m_pipeline)
        command_buffer.setViewport(QRhiViewport(0, 0, *size_px))
        command_buffer.setShaderResources(self.m_res.m_srb)
        if self.m_strip:
            command_buffer.draw(4)
            return
        command_buffer.setVertexInput(0, [(self.m_vbuf, 0)])
        command_buffer.draw(3)

    def release(self) -> None:
        self.m_res.release()
        self.m_pipeline = self.m_vbuf = None


def full_screen(
    ctx, post: CPostPass, framebuffer, size_px, named: dict, fill, *, targets: int = 1, clear: bool = True
) -> None:
    """``post`` drawn over the whole of ``framebuffer`` (``size_px``) as a pass of its own, its resources by name from ``named``;
    ``clear`` False: over what the target holds."""

    def prepare(render_pass, _signature, batch) -> None:
        post.prepare(batch, render_pass, named, fill, targets)

    draw_pass(ctx, framebuffer, prepare, lambda command_buffer: post.draw(command_buffer, size_px), clear=clear)


class CGamePost:
    """The grade and the tone map (module docstring); ``targets`` are the two half-float results."""

    def __init__(self, rhi, stages: dict) -> None:
        self.m_passes = {key: CPostPass(rhi, CProgram(stages[f"{key}.vs"], stages[f"{key}.ps"])) for key in PASSES}
        self.m_targets: list = []
        self.m_fbos: list = []
        self.m_size_px = None

    @property
    def m_error(self) -> str:
        return next((post_pass.m_error for post_pass in self.m_passes.values() if post_pass.m_error), "")

    def _targets(self, ctx, size_px) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        self.m_targets = [ctx.native_texture(size_px, QRhiTexture.Format.RGBA16F) for _ in range(2)]
        self.m_fbos = [ctx.framebuffer([texture]) for texture in self.m_targets]
        self.m_size_px = size_px

    def render(self, ctx, source, size_px: tuple[int, int], inputs: dict, exposure=None):
        """The grade of ``source`` (a device texture: the view's resolved HDR colour) and its tone map, recorded as
        two passes; returns the tone-mapped device texture. ``inputs``: the grade cube (``cLUTTexture0``), the
        blue noise, by name; ``exposure``: the eye adaptation's buffer (``GCF_EYE_ADAPT`` set), None for none."""
        self._targets(ctx, size_px)
        flags = GCF_EYE_ADAPT if exposure is not None else 0
        extra = {"cExposureBuffer": exposure} if exposure is not None else {}
        for key, target_texture, k in (("grade", source, 0), ("tonemap", self.m_targets[0], 1)):
            named = {**inputs, **extra, "cTexture0": target_texture.native}
            full_screen(ctx, self.m_passes[key], self.m_fbos[k], size_px, named, partial(_fill, flags=flags))
        return self.m_targets[1]

    def _drop(self) -> None:
        for resource in (*self.m_fbos, *self.m_targets):
            resource.release()
        self.m_fbos, self.m_targets, self.m_size_px = [], [], None

    def release(self) -> None:
        self._drop()
        for post_pass in self.m_passes.values():
            post_pass.release()


def _fill(constants: gs.CConstants, flags: int = 0) -> None:
    for key, value in CONSTANTS.items():
        constants.set(key, value)
    constants.set("cGlobalFlags", flags)
