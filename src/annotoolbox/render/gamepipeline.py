"""The graphics pipelines of the game's shaders on QRhi: the vertex layout, the targets' blends, a pipeline's common
part, and ``CPipelineUser``, the part of ``render.gamedraw.CGameModel`` that makes and counts its pipeline.

A model's pipeline is one per program, pass and vertex layout, shared by every model that draws alike (the program
keeps them in ``m_pipelines`` with their users). ``new_pipeline(rhi, program, layout, srb, render_pass, samples,
blends)`` is what ``fxcdraw`` and ``gamepost`` build theirs from; the caller sets depth and culling and creates it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import (
    QRhiGraphicsPipeline,
    QRhiShaderStage,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
)

from .renderstates import stencil_state

if TYPE_CHECKING:
    from .gamedraw import CProgram


def vertex_layout(strides: list[int], attributes: list) -> QRhiVertexInputLayout:
    """A vertex input layout of one binding per stride (a mesh: its stored vertices, the per-vertex constants)."""
    layout = QRhiVertexInputLayout()
    layout.setBindings([QRhiVertexInputBinding(stride) for stride in strides])
    layout.setAttributes(attributes)
    return layout


def target_blends(blends: tuple, targets: int) -> list:
    """QRhi's blend of each of ``targets`` colour targets: ``blends[i]`` (colour source and destination, alpha source
    and destination factors; None or missing: off)."""
    out = []
    for i in range(targets):
        target_blend = QRhiGraphicsPipeline.TargetBlend()
        found = blends[i] if i < len(blends) else None
        if found is not None:
            target_blend.enable = True
            target_blend.srcColor, target_blend.dstColor, target_blend.srcAlpha, target_blend.dstAlpha = found
        out.append(target_blend)
    return out


CULL_MODES = {"back": QRhiGraphicsPipeline.CullMode.Back, "front": QRhiGraphicsPipeline.CullMode.Front,
              "none": QRhiGraphicsPipeline.CullMode.None_}  # fmt: skip


def _pass_format(render_pass) -> tuple:
    """What a pipeline made for the pass ``render_pass`` depends on of it (its targets' formats; the object where QRhi says
    nothing)."""
    try:
        return tuple(render_pass.serializedFormat())
    except (AttributeError, TypeError):
        return (id(render_pass),)


def new_pipeline(rhi, program: CProgram, layout, resource_bindings, render_pass, samples: int, blends: list):
    """A graphics pipeline of ``program``'s stages (its geometry shader, or its hull and domain shader on patches of
    four points, too) for the pass ``render_pass``: its vertex layout, binding set, sample count and target blends set; the
    caller sets depth and culling (and the topology of an untessellated one), then ``create``s."""
    pipeline = rhi.newGraphicsPipeline()
    vs_shader, ps_shader = program.m_shaders
    stages = [QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs_shader)]
    if program.m_geometry is not None:  # interior mapping: face normals from its geometry shader
        stages.append(QRhiShaderStage(QRhiShaderStage.Type.Geometry, program.m_geometry))
    if program.m_patches is not None:  # the terrain's detailed nodes: quad patches
        hull, domain = program.m_patches
        stages += [QRhiShaderStage(QRhiShaderStage.Type.TessellationControl, hull),
                   QRhiShaderStage(QRhiShaderStage.Type.TessellationEvaluation, domain)]  # fmt: skip
        pipeline.setTopology(QRhiGraphicsPipeline.Topology.Patches)
        pipeline.setPatchControlPointCount(4)
    pipeline.setShaderStages([*stages, QRhiShaderStage(QRhiShaderStage.Type.Fragment, ps_shader)])
    pipeline.setVertexInputLayout(layout)
    pipeline.setShaderResourceBindings(resource_bindings)
    pipeline.setRenderPassDescriptor(render_pass)
    pipeline.setSampleCount(max(1, samples))
    pipeline.setTargetBlends(blends)
    return pipeline


class CPipelineUser:
    """A draw's pipeline of its program, shared and counted. The class that mixes it in has ``m_rhi``, ``m_program``,
    ``m_state`` (``renderstates.SPassState``), ``m_srb``, ``m_error``, ``m_attrs``, ``m_mesh`` and ``m_fill_stride``
    (``CGameModel``)."""

    def _make_pipeline(self, render_pass, signature) -> None:
        """The model's pipeline: one per program, pass and vertex layout, shared by every model that draws alike (its
        own binding set is layout-compatible): a pipeline of the game's shaders holds megabytes of the driver's
        memory, and a continental island's nodes and parts made thousands of them (island-117-view.md "Speed")."""
        self.m_pipeline, self._m_pipeline_key = self._shared_pipeline(render_pass, signature, self.m_attrs, self.m_mesh.m_stride,
                                                                  self.m_fill_stride)  # fmt: skip

    def _shared_pipeline(self, render_pass, signature, attributes: list, stride: int, fill_stride: int) -> tuple:
        """(pipeline, its key in ``program.pipelines``) for a vertex layout: the program's shared one, made when
        new (None and no key when the backend refused it: ``error`` says so)."""
        targets, samples = signature
        layout_key = tuple((a.binding(), a.location(), a.format(), a.offset()) for a in attributes)
        key = (_pass_format(render_pass), targets, samples, stride, max(4, fill_stride), layout_key, self.m_state)
        shared = self.m_program.m_pipelines.get(key)
        if shared is not None:
            shared[1] += 1
            return shared[0], key
        pipeline = self._new_pipeline(render_pass, signature, vertex_layout([stride, max(4, fill_stride)], attributes))
        if pipeline is None:
            return None, None
        self.m_program.m_pipelines[key] = [pipeline, 1]  # the program's: destroyed with its last user
        return pipeline, key

    def _new_pipeline(self, render_pass, signature, layout):
        targets, samples = signature
        blends = target_blends(self.m_state.blends, targets)
        for target_index, target_blend in enumerate(blends):
            mask = self.m_state.writes[target_index] if target_index < len(self.m_state.writes) else None
            if not self.m_state.colour or mask is not None:  # QRhi's mask bits are D3D's
                target_blend.colorWrite = QRhiGraphicsPipeline.ColorMaskComponent(
                    0 if not self.m_state.colour else mask
                )
        pipeline = new_pipeline(self.m_rhi, self.m_program, layout, self.m_srb, render_pass, samples, blends)
        pipeline.setDepthTest(self.m_state.depth_test)
        pipeline.setDepthWrite(self.m_state.depth_write)
        compare_op = QRhiGraphicsPipeline.CompareOp
        depth_op = (
            compare_op.Always
            if self.m_state.depth_always
            else compare_op.Equal
            if self.m_state.depth_equal
            else compare_op.GreaterOrEqual
        )
        pipeline.setDepthOp(depth_op)  # the engine's depth: 1 near, 0 far
        pipeline.setCullMode(CULL_MODES[self.m_state.cull])
        stencil = self.m_state.stencil
        if stencil is not None:  # the reference is set per draw (``CMesh.draw``)
            pipeline.setStencilTest(True)
            pipeline.setStencilFront(stencil_state(stencil))
            pipeline.setStencilBack(stencil_state(stencil))
            pipeline.setStencilReadMask(stencil.mask)
            pipeline.setStencilWriteMask(stencil.write_mask)
            pipeline.setFlags(pipeline.flags() | QRhiGraphicsPipeline.Flag.UsesStencilRef)
        if not pipeline.create():
            self.m_error = "the backend refused the game shader's pipeline"
            pipeline.destroy()
            return None
        return pipeline

    def _give_back(self, key) -> None:
        """One user fewer of the program's pipeline ``key``; the last destroys it."""
        shared = self.m_program.m_pipelines.get(key) if key is not None else None
        if shared is not None:
            shared[1] -= 1
            if shared[1] <= 0:
                shared[0].destroy()
                del self.m_program.m_pipelines[key]
