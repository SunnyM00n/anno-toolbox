"""One of the game's compute shaders as a QRhi compute pipeline: its resources bound by name from what the frame
hands it (textures, storage textures, raw buffers), its constant buffers written by name, dispatched by the caller;
and what the chains of compute passes share (``record``, ``carry``, ``copy_into``, ``storage_buffer``, ``groups``).

``ComputeStage(rhi, stage)`` is the entry (a compiled ``gamedraw.Stage``): ``record`` runs its pass, or
``prepare(batch, inputs, fill)`` before and ``dispatch(cb, groups)`` inside one. A frame's uniform buffer holds what
was written last: a shader dispatched several times with other constants needs a stage per dispatch (``share``: the
first's pipeline). ``ComputeChain`` is the base of ``shadowfit`` and ``gamesky``; docs/rendering/frame-117-viewer.md.
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
from .gamebind import Resources
from .gamedraw import native_shader

B = QRhiShaderResourceBinding


STAND_IN_SAMPLER = "<compute stage sampler>"


def lone_textures_unbound(rhi) -> bool:
    """Whether the backend binds a stage's textures only next to a sampler: QRhi's Direct3D 11 (PySide6 6.12), where
    a compute shader that only ``Load``s read zeros (1800's eye adaptation and ambient occlusion until 2026-11-14)."""
    return rhi is not None and rhi.backend() == QRhi.Implementation.D3D11


def groups(size: int, per: int = 8) -> int:
    """How many groups of ``per`` threads cover ``size`` (at least one)."""
    return max(1, -(-size // per))


def storage_buffer(rhi, size: int) -> QRhiBuffer:
    """A static buffer a shader reads and writes raw (a structured buffer, uploaded or filled on the GPU)."""
    buf = rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, size)
    buf.create()
    return buf


def copy_into(rhi, dst, src, size: tuple[int, int], layer: int = 0, level: int = 0):
    """A resource update batch that copies ``src`` (a texture) into ``dst``'s ``layer`` and ``level``."""
    batch = rhi.nextResourceUpdateBatch()
    desc = QRhiTextureCopyDescription()
    desc.setDestinationLayer(layer)
    desc.setDestinationLevel(level)
    desc.setPixelSize(QSize(*size))
    desc.setSourceTopLeft(QPoint(0, 0))
    batch.copyTexture(dst, src, desc)
    return batch


class ComputeStage:
    """A compute shader and what it binds (module docstring). A subclass hands ``stand_in``, ``raw`` and ``address``
    on to its ``gamebind.Resources``."""

    def __init__(self, rhi, stage, share: ComputeStage | None = None, **binding) -> None:
        self.rhi, self.stage, self.share = rhi, stage, share
        refl = stage.reflection
        numbers = {(b.name, b.kind): k for k, b in enumerate(refl.bindings)}
        binding_map = {k: (b.register, -1) for k, b in enumerate(refl.bindings)}
        kinds = {b.kind for b in refl.bindings}
        if "texture" in kinds and "sampler" not in kinds and lone_textures_unbound(rhi):
            numbers[(STAND_IN_SAMPLER, "sampler")] = len(binding_map)  # register 0 (gamedraw's _pad_samplers)
            binding_map[len(binding_map)] = (0, -1)
        self.shader = native_shader(QShader.Stage.ComputeStage, stage.blob, stage.entry, binding_map)
        self.constants = {c.name: gs.Constants(c) for c in refl.cbuffers}
        self.res = Resources(rhi, numbers, lambda _key: B.StageFlag.ComputeStage, self.constants,
                             lambda name: stage.types.get(name, ""), **binding)  # fmt: skip
        self.pipeline = None
        self.error = ""

    def prepare(self, batch, inputs: dict, fill=None) -> None:
        """Bind ``inputs`` (name -> QRhi resource) and write the constants (``fill(c)`` per buffer) into ``batch``."""
        self.res.rebind(batch, inputs)
        if self.pipeline is None and self.share is not None and self.share.pipeline is not None:
            self.pipeline = self.share.pipeline  # the same shader and layout: one pipeline, the owner's to release
        if self.pipeline is None and not self.error:
            pipe = self.rhi.newComputePipeline()
            pipe.setShaderStage(QRhiShaderStage(QRhiShaderStage.Type.Compute, self.shader))
            pipe.setShaderResourceBindings(self.res.srb)
            if pipe.create():
                self.pipeline = pipe
                self.res.keep.append(pipe)
            else:
                self.error = f"the backend refused the pipeline of {self.stage.entry}"
        self.res.write(batch, fill)

    def dispatch(self, cb, groups: tuple[int, int, int]) -> None:
        if self.pipeline is None:
            return
        cb.setComputePipeline(self.pipeline)
        cb.setShaderResources(self.res.srb)
        cb.dispatch(*groups)

    def release(self) -> None:
        self.res.release()
        self.pipeline = None


def carry(cb, batch, step: Callable | None = None, after=None) -> None:
    """``step(batch)`` (None: nothing) run by an empty compute pass of its own, which hands the batch on (a batch no
    pass takes is dropped) and ends with ``after`` (a batch: a copy, ``copy_into``)."""
    if step is not None:
        step(batch)
    cb.beginComputePass(batch)
    cb.endComputePass(after)


def record(ctx, stage: ComputeStage, inputs: dict, fill, groups: tuple[int, int, int], after: Callable | None = None,
           before: Callable | None = None) -> None:  # fmt: skip
    """One compute pass of ``stage`` as a native pass of ``ctx``: ``before(batch)`` adds uploads to its batch, ``after()``
    makes the batch the pass ends with (a copy of its result, a readback)."""

    def compute_pass(cb, batch) -> None:
        if before is not None:
            before(batch)
        stage.prepare(batch, inputs, fill)
        cb.beginComputePass(batch)
        stage.dispatch(cb, groups)
        cb.endComputePass(after() if after is not None else None)

    ctx.native_pass(compute_pass)


class ComputeChain:
    """A chain of compute stages (``stages``, by whatever the subclass keys them): the first error of any, and the
    release of all (the owners of a shared pipeline last)."""

    stages: dict

    @property
    def error(self) -> str:
        return next((s.error for s in self.stages.values() if s.error), "")

    def release(self) -> None:
        for s in sorted(self.stages.values(), key=lambda s: s.share is None):
            s.release()
