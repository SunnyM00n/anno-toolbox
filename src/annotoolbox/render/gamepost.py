"""Anno 117's post chain by the game's own pixel shaders: the colour grade (``posteffect_colorgrade.fx``) and the tone
map (``posteffect_tonemap.fx``) over the view's HDR colour, each a full-screen triangle into a half-float target.

``GamePost(rhi, stages)`` is the entry (the passes' vertex and pixel shaders, ``STAGES``); ``render(ctx,
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
from .gamebind import Resources
from .gamedraw import Program, immutable_buffer, new_pipeline, target_blends, vertex_layout
from .nativepass import draw_pass
from .renderstates import PassState

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


class PostPass:
    """One of the game's full-screen pixel shaders into a target (its resources by name from ``inputs``): a
    triangle of ``PostEffectVertex``, or, for a vertex shader without inputs (``vsCommonFullScreenQuad``), a strip of
    four vertices from their ids. ``state``: the technique's blends per target and depth (None: one target, no
    blend, no depth), ``depth_always``: the depth written whatever it was (``ZFunc ALWAYS``)."""

    def __init__(self, rhi, program: Program, state: PassState | None = None, depth_always: bool = False) -> None:
        self.rhi, self.program, self.state, self.depth_always = rhi, program, state, depth_always
        self.constants = {name: gs.Constants(c) for name, c in program.cbuffers.items()}
        self.res = Resources(rhi, program.numbers, program.stages.__getitem__, self.constants,
                             lambda name: program.vs.types.get(name) or program.ps.types.get(name) or "")  # fmt: skip
        self.pipeline = self.vbuf = None
        self.strip = not program.vs.inputs
        self.layout = QRhiVertexInputLayout()
        self.error = ""

    def _vertices(self, batch) -> None:
        data, attrs, at = [], [], 0
        for inp in self.program.vs.inputs:
            values = TRIANGLE.get(inp.type, TRIANGLE["float2"])
            data.append(np.asarray(values, np.float32))
            fmt = getattr(QRhiVertexInputAttribute.Format, f"Float{len(values[0])}")
            attrs.append(QRhiVertexInputAttribute(0, inp.location, fmt, at))
            at += 4 * len(values[0])
        raw = np.concatenate(data, axis=1).tobytes()
        self.vbuf = immutable_buffer(self.rhi, batch, QRhiBuffer.UsageFlag.VertexBuffer, raw)
        self.res.keep.append(self.vbuf)
        self.layout = vertex_layout([at], attrs)

    def prepare(self, batch, rp, inputs: dict, fill, targets: int = 1) -> None:
        """Bind ``inputs``, make the pipeline for ``rp`` (``targets`` colour targets) once, write the constants."""
        if self.vbuf is None and not self.strip:
            self._vertices(batch)
        self.res.rebind(batch, inputs)
        if self.pipeline is None and not self.error:
            blends = target_blends(self.state.blends if self.state is not None else (), targets)
            ps = new_pipeline(self.rhi, self.program, self.layout, self.res.srb, rp, 1, blends)
            if self.strip:
                ps.setTopology(QRhiGraphicsPipeline.Topology.TriangleStrip)
            if self.depth_always:
                ps.setDepthTest(True)
                ps.setDepthWrite(True)
                ps.setDepthOp(QRhiGraphicsPipeline.CompareOp.Always)
            if ps.create():
                self.pipeline = ps
                self.res.keep.append(ps)
            else:
                self.error = "the backend refused a post effect's pipeline"
        self.res.write(batch, fill)

    def draw(self, cb, size) -> None:
        if self.pipeline is None:
            return
        cb.setGraphicsPipeline(self.pipeline)
        cb.setViewport(QRhiViewport(0, 0, *size))
        cb.setShaderResources(self.res.srb)
        if self.strip:
            cb.draw(4)
            return
        cb.setVertexInput(0, [(self.vbuf, 0)])
        cb.draw(3)

    def release(self) -> None:
        self.res.release()
        self.pipeline = self.vbuf = None


def full_screen(ctx, post: PostPass, fbo, size, named: dict, fill, *, targets: int = 1, clear: bool = True) -> None:
    """``post`` drawn over the whole of ``fbo`` (``size``) as a pass of its own, its resources by name from ``named``;
    ``clear`` False: over what the target holds."""

    def prepare(rp, _signature, batch) -> None:
        post.prepare(batch, rp, named, fill, targets)

    draw_pass(ctx, fbo, prepare, lambda cb: post.draw(cb, size), clear=clear)


class GamePost:
    """The grade and the tone map (module docstring); ``targets`` are the two half-float results."""

    def __init__(self, rhi, stages: dict) -> None:
        self.passes = {key: PostPass(rhi, Program(stages[f"{key}.vs"], stages[f"{key}.ps"])) for key in PASSES}
        self.targets: list = []
        self.fbos: list = []
        self.size = None

    @property
    def error(self) -> str:
        return next((p.error for p in self.passes.values() if p.error), "")

    def _targets(self, ctx, size) -> None:
        if self.size == size:
            return
        self._drop()
        self.targets = [ctx.native_texture(size, QRhiTexture.Format.RGBA16F) for _ in range(2)]
        self.fbos = [ctx.framebuffer([t]) for t in self.targets]
        self.size = size

    def render(self, ctx, source, size: tuple[int, int], inputs: dict, exposure=None):
        """The grade of ``source`` (a device texture: the view's resolved HDR colour) and its tone map, recorded as
        two passes; returns the tone-mapped device texture. ``inputs``: the grade cube (``cLUTTexture0``), the
        blue noise, by name; ``exposure``: the eye adaptation's buffer (``GCF_EYE_ADAPT`` set), None for none."""
        self._targets(ctx, size)
        flags = GCF_EYE_ADAPT if exposure is not None else 0
        extra = {"cExposureBuffer": exposure} if exposure is not None else {}
        for key, src, k in (("grade", source, 0), ("tonemap", self.targets[0], 1)):
            named = {**inputs, **extra, "cTexture0": src.native}
            full_screen(ctx, self.passes[key], self.fbos[k], size, named, partial(_fill, flags=flags))
        return self.targets[1]

    def _drop(self) -> None:
        for r in (*self.fbos, *self.targets):
            r.release()
        self.fbos, self.targets, self.size = [], [], None

    def release(self) -> None:
        self._drop()
        for p in self.passes.values():
            p.release()


def _fill(c: gs.Constants, flags: int = 0) -> None:
    for key, value in CONSTANTS.items():
        c.set(key, value)
    c.set("cGlobalFlags", flags)
