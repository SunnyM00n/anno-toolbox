"""One of the game's compute shaders as a QRhi compute pipeline: its resources bound by name from what the frame
hands it (textures, storage textures, raw buffers), its constant buffers written by name, dispatched by the caller;
and what the chains of compute passes share (``record``, ``carry``, ``copy_into``, ``storage_buffer``, ``groups``).

``CComputeStage(rhi, stage)`` is the entry (a compiled ``gamedraw.SStage``): ``record`` runs its pass, or
``prepare(batch, inputs, fill)`` before and ``dispatch(command_buffer, groups)`` inside one. A frame's uniform buffer holds what
was written last: a shader dispatched several times with other constants needs a stage per dispatch (``share``: the
first's pipeline). ``CComputeChain`` is the base of ``shadowfit`` and ``gamesky``; docs/rendering/frame-117-viewer.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import (
    QRhi,
    QRhiBuffer,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTextureCopyDescription,
    QShader,
)

from . import gameshader as gs
from .gamebind import CResources
from .gamedraw import native_shader

Binding = QRhiShaderResourceBinding


STAND_IN_SAMPLER = "<compute stage sampler>"


def lone_textures_unbound(rhi) -> bool:
    """Whether the backend binds a stage's textures only next to a sampler: QRhi's Direct3D 11 (PySide6 6.12), where
    a compute shader that only ``Load``s read zeros (1800's eye adaptation and ambient occlusion until 2026-11-14)."""
    return rhi is not None and rhi.backend() == QRhi.Implementation.D3D11


def groups(thread_count: int, per: int = 8) -> int:
    """How many groups of ``per`` threads cover ``thread_count`` (at least one)."""
    return max(1, -(-thread_count // per))


def storage_buffer(rhi, size_bytes: int) -> QRhiBuffer:
    """A static buffer a shader reads and writes raw (a structured buffer, uploaded or filled on the GPU)."""
    buffer = rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, size_bytes)
    buffer.create()
    return buffer


def copy_into(rhi, destination, source, size_px: tuple[int, int], layer: int = 0, level: int = 0):
    """A resource update batch that copies ``source`` (a texture) into ``destination``'s ``layer`` and ``level``."""
    batch = rhi.nextResourceUpdateBatch()
    description = QRhiTextureCopyDescription()
    description.setDestinationLayer(layer)
    description.setDestinationLevel(level)
    description.setPixelSize(QSize(*size_px))
    description.setSourceTopLeft(QPoint(0, 0))
    batch.copyTexture(destination, source, description)
    return batch


class CComputeStage:
    """A compute shader and what it binds (module docstring). A subclass hands ``stand_in``, ``raw`` and ``address``
    on to its ``gamebind.CResources``."""

    def __init__(self, rhi, stage, share: CComputeStage | None = None, **binding) -> None:
        self.m_rhi, self.m_stage, self.m_share = rhi, stage, share
        reflection = stage.reflection
        numbers = {
            (shader_binding.name, shader_binding.kind): k for k, shader_binding in enumerate(reflection.bindings)
        }
        binding_map = {k: (shader_binding.register, -1) for k, shader_binding in enumerate(reflection.bindings)}
        kinds = {shader_binding.kind for shader_binding in reflection.bindings}
        if "texture" in kinds and "sampler" not in kinds and lone_textures_unbound(rhi):
            numbers[(STAND_IN_SAMPLER, "sampler")] = len(binding_map)  # register 0 (gamedraw's _pad_samplers)
            binding_map[len(binding_map)] = (0, -1)
        self.m_shader = native_shader(QShader.Stage.ComputeStage, stage.blob, stage.entry, binding_map)
        self.m_constants = {cbuffer.name: gs.CConstants(cbuffer) for cbuffer in reflection.cbuffers}
        self.m_res = CResources(rhi, numbers, lambda _key: Binding.StageFlag.ComputeStage, self.m_constants,
                             lambda name: stage.types.get(name, ""), **binding)  # fmt: skip
        self.m_pipeline = None
        self.m_error = ""

    def prepare(self, batch, inputs: dict, fill=None) -> None:
        """Bind ``inputs`` (name -> QRhi resource) and write the constants (``fill(c)`` per buffer) into ``batch``."""
        self.m_res.rebind(batch, inputs)
        if self.m_pipeline is None and self.m_share is not None and self.m_share.m_pipeline is not None:
            self.m_pipeline = (
                self.m_share.m_pipeline
            )  # the same shader and layout: one pipeline, the owner's to release
        if self.m_pipeline is None and not self.m_error:
            pipe = self.m_rhi.newComputePipeline()
            pipe.setShaderStage(QRhiShaderStage(QRhiShaderStage.Type.Compute, self.m_shader))
            pipe.setShaderResourceBindings(self.m_res.m_srb)
            if pipe.create():
                self.m_pipeline = pipe
                self.m_res.m_keep.append(pipe)
            else:
                self.m_error = f"the backend refused the pipeline of {self.m_stage.entry}"
        self.m_res.write(batch, fill)

    def dispatch(self, command_buffer, groups: tuple[int, int, int]) -> None:
        if self.m_pipeline is None:
            return
        command_buffer.setComputePipeline(self.m_pipeline)
        command_buffer.setShaderResources(self.m_res.m_srb)
        command_buffer.dispatch(*groups)

    def release(self) -> None:
        self.m_res.release()
        self.m_pipeline = None


def carry(command_buffer, batch, step: Callable | None = None, after=None) -> None:
    """``step(batch)`` (None: nothing) run by an empty compute pass of its own, which hands the batch on (a batch no
    pass takes is dropped) and ends with ``after`` (a batch: a copy, ``copy_into``)."""
    if step is not None:
        step(batch)
    command_buffer.beginComputePass(batch)
    command_buffer.endComputePass(after)


def record(ctx, stage: CComputeStage, inputs: dict, fill, groups: tuple[int, int, int], after: Callable | None = None,
           before: Callable | None = None) -> None:  # fmt: skip
    """One compute pass of ``stage`` as a native pass of ``ctx``: ``before(batch)`` adds uploads to its batch, ``after()``
    makes the batch the pass ends with (a copy of its result, a readback)."""

    def compute_pass(command_buffer, batch) -> None:
        if before is not None:
            before(batch)
        stage.prepare(batch, inputs, fill)
        command_buffer.beginComputePass(batch)
        stage.dispatch(command_buffer, groups)
        command_buffer.endComputePass(after() if after is not None else None)

    ctx.native_pass(compute_pass)


class CComputeChain:
    """A chain of compute stages (``stages``, by whatever the subclass keys them): the first error of any, and the
    release of all (the owners of a shared pipeline last)."""

    m_stages: dict

    @property
    def m_error(self) -> str:
        return next((stage.m_error for stage in self.m_stages.values() if stage.m_error), "")

    def release(self) -> None:
        for stage in sorted(self.m_stages.values(), key=lambda stage: stage.m_share is None):
            stage.release()
