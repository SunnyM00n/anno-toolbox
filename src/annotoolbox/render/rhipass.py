"""The QRhi device's recording: draws gathered into render passes per target and run at the frame's end, with the
pipelines, resource sets, samplers and render targets they need made once and kept.

``CRecorder`` is the base of ``rhidevice.CRhiContext`` (the state a renderer sets, ``_draw``, ``_clear``,
``_upload``, ``copy_framebuffer``, ``_run``); ``vertex_layout`` reads moderngl's format strings. Why draws are
recorded and how the image is kept the right way up on every backend: docs/tool-suite.md section 21, "The device
layer". A frame that raises is cleared and rotated all the same (the error stays in ``errors``); a native pass that
raises is ended by the kind of pass it began, if any, and the frame goes on (docs/viewer.md "When a pass fails").
"""

from __future__ import annotations

import itertools
import re
import traceback
from dataclasses import dataclass, field

from PySide6.QtGui import (
    QColor,
    QRhiBuffer,
    QRhiColorAttachment,
    QRhiDepthStencilClearValue,
    QRhiGraphicsPipeline,
    QRhiReadbackDescription,
    QRhiReadbackResult,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTextureRenderTarget,
    QRhiTextureRenderTargetDescription,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
)

from . import glslport
from .shaderbake import pack_member

Pipeline, Binding = QRhiGraphicsPipeline, QRhiShaderResourceBinding
AttributeFormat = QRhiVertexInputAttribute.Format
_BLEND, _DEPTH, _CULL = 1, 2, 4  # moderngl's flag bits (rhidevice)
_TOKEN = re.compile(r"(\d*)([fiux])(\d*)")
_ATTRIBUTE = {
    ("f", 4): (AttributeFormat.Float, AttributeFormat.Float2, AttributeFormat.Float3, AttributeFormat.Float4),
    ("f", 2): (AttributeFormat.Half, AttributeFormat.Half2, AttributeFormat.Half3, AttributeFormat.Half4),
    ("f", 1): (AttributeFormat.UNormByte, AttributeFormat.UNormByte2, None, AttributeFormat.UNormByte4),
    ("i", 4): (AttributeFormat.SInt, AttributeFormat.SInt2, AttributeFormat.SInt3, AttributeFormat.SInt4),
    ("u", 4): (AttributeFormat.UInt, AttributeFormat.UInt2, AttributeFormat.UInt3, AttributeFormat.UInt4),
}
_TOPOLOGY = {
    0: Pipeline.Topology.Points,
    1: Pipeline.Topology.Lines,
    3: Pipeline.Topology.LineStrip,
    4: Pipeline.Topology.Triangles,
    5: Pipeline.Topology.TriangleStrip,
    6: Pipeline.Topology.TriangleFan,
}
_FACTOR = {
    0: Pipeline.BlendFactor.Zero,
    1: Pipeline.BlendFactor.One,
    0x300: Pipeline.BlendFactor.SrcColor,
    0x301: Pipeline.BlendFactor.OneMinusSrcColor,
    0x302: Pipeline.BlendFactor.SrcAlpha,
    0x303: Pipeline.BlendFactor.OneMinusSrcAlpha,
    0x304: Pipeline.BlendFactor.DstAlpha,
    0x305: Pipeline.BlendFactor.OneMinusDstAlpha,
    0x306: Pipeline.BlendFactor.DstColor,
    0x307: Pipeline.BlendFactor.OneMinusDstColor,
}
_COMPARE = {
    "<": Pipeline.CompareOp.Less,
    "<=": Pipeline.CompareOp.LessOrEqual,
    "==": Pipeline.CompareOp.Equal,
    ">=": Pipeline.CompareOp.GreaterOrEqual,
    ">": Pipeline.CompareOp.Greater,
    "!=": Pipeline.CompareOp.NotEqual,
    "0": Pipeline.CompareOp.Never,
    "1": Pipeline.CompareOp.Always,
}
_SAMPLER_COMPARE = {k: getattr(QRhiSampler.CompareOp, v.name) for k, v in _COMPARE.items()}
_LOCATIONS = {"mat3": 3, "mat4": 4}
_serial = itertools.count(1)


def vertex_layout(attributes: dict[str, glslport.SVariable], content, allow_missing_inputs: bool):
    """(buffers as (buffer, stride, per instance), layout) of a vertex array: the layout is ((stride, per
    instance) per binding, (binding, location, format, offset, matrix slice) per attribute), plain tuples for keying.
    A matrix input takes a location per column, each also marked as its slice (the Direct3D backends name them so)."""
    buffers, bindings, attribute_layout = [], [], []
    for binding, (buffer, format_text, *names) in enumerate(content):
        spec, _, step = format_text.partition("/")
        offset, names = 0, list(names)
        for token in spec.split():
            match = _TOKEN.fullmatch(token)
            if not match:
                raise ValueError(f"cannot read the vertex format {format_text!r}")
            count, kind = int(match.group(1) or 1), match.group(2)
            size_bytes = int(match.group(3) or (1 if kind == "x" else 4))
            if kind == "x":
                offset += count * size_bytes
                continue
            name = names.pop(0)
            attribute = attributes.get(name)
            if attribute is None and not allow_missing_inputs:
                raise KeyError(f"the program has no input {name}")
            if attribute is not None:
                columns = _LOCATIONS.get(attribute.type, 1)
                per_column = count // columns
                for column in range(columns):
                    column_format = _ATTRIBUTE[(kind, size_bytes)][per_column - 1]
                    attribute_layout.append(
                        (
                            binding,
                            attribute.slot + column,
                            column_format,
                            offset + column * per_column * size_bytes,
                            column if columns > 1 else -1,
                        )
                    )
            offset += count * size_bytes
        bindings.append((offset, step == "i"))
        buffers.append((buffer, offset, step == "i"))
    return buffers, (tuple(bindings), tuple(attribute_layout))


def layout_key(layout) -> tuple:
    return tuple(layout[0]), tuple(
        (binding, location, int(format_code.value), offset, matrix_slice)
        for binding, location, format_code, offset, matrix_slice in layout[1]
    )


@dataclass
class _SDraw:
    vao: object
    mode: int
    count: int
    first: int
    instances: int
    data: bytes
    textures: tuple  # per sampler of the program: the texture bound at its unit (None: a stand-in)
    state: tuple
    viewport: tuple


@dataclass
class _SPass:
    target: object
    clear: tuple | None  # (rgba, depth)
    uploads: list
    draws: list = field(default_factory=list)
    used: set = field(default_factory=set)
    resolve: object = None
    native: object = None  # a pass recorded by its owner: record(buffer, batch), the batch given to its beginPass


class _CNativeCommands:
    """The command buffer as a native pass sees it: the recorder learns which kind of pass it began (one that fails
    is ended by that kind, one that never began is not ended: QRhi has no check and would fall over) and drops the
    batch a begin or a ``resourceUpdate`` took over from its open ones (released again at the frame's end, QRhi's
    pool counted it free twice)."""

    def __init__(self, command_buffer, recorder: CRecorder) -> None:
        self._m_command_buffer, self._m_recorder = command_buffer, recorder

    def __getattr__(self, name: str):
        return getattr(self._m_command_buffer, name)

    def _taken(self, batch) -> None:
        """QRhi took ``batch`` over (and lets it go itself): no longer one of the recorder's to release."""
        if batch is not None and any(batch is open_batch for open_batch in self._m_recorder._m_open_batches):
            self._m_recorder._m_open_batches = [
                open_batch for open_batch in self._m_recorder._m_open_batches if open_batch is not batch
            ]

    def _began(self, kind: str, batch) -> None:
        self._m_recorder._m_begun = kind
        self._taken(batch)

    def resourceUpdate(self, batch) -> None:  # uploads handed on without a pass (QRhiCommandBuffer's name)
        self._m_command_buffer.resourceUpdate(batch)
        self._taken(batch)

    def beginPass(self, *arguments) -> None:  # QRhiCommandBuffer's names
        self._m_command_buffer.beginPass(*arguments)
        self._began("render", arguments[3] if len(arguments) > 3 else None)

    def beginComputePass(self, *arguments) -> None:
        self._m_command_buffer.beginComputePass(*arguments)
        self._began("compute", arguments[0] if arguments else None)

    def endPass(self, *arguments) -> None:
        self._m_recorder._m_begun = ""
        self._m_command_buffer.endPass(*arguments)

    def endComputePass(self, *arguments) -> None:
        self._m_recorder._m_begun = ""
        self._m_command_buffer.endComputePass(*arguments)


class CRecorder:
    """The state and the recording of ``rhidevice.CRhiContext`` (module docstring)."""

    def __init__(self, rhi) -> None:
        self.m_rhi = rhi
        self.m_flags = 0
        self.blend_func = (0x302, 0x303)
        self.depth_mask, self.depth_func, self.wireframe = True, "<", False
        self.viewport = (0, 0, 1, 1)
        self.m_units: dict[int, object] = {}
        self.screen = None
        self._m_fbo = None
        self._m_passes: list[_SPass] = []
        self._m_pass: _SPass | None = None
        self._m_pending: list = []
        self._m_doomed: list = []
        self._m_dying: list = []
        self._m_reads: list = []
        self._m_pipelines: dict = {}
        self._m_srbs: dict = {}
        self._m_srb_users: dict[int, set] = {}  # a texture's serial -> the keys of the resource sets that bind it
        self.m_released = False  # ``release`` ran: what is let go from now on is destroyed at once
        self._m_samplers: dict = {}
        self._m_dummies: dict = {}
        self.m_errors: list[str] = []
        self.m_failed_models: list[tuple[object, str]] = []  # a native pass's model that did not prepare, its error
        self.m_make_spent, self.m_make_left = (
            0.0,
            0,
        )  # this frame's time on parts new on the device, those left (``nativepass.prepared``)
        self.m_pass_errors: list[str] = []  # the native passes that raised (also in ``errors``), for their owner
        self._m_open_batches: list = []
        self._m_begun = ""  # the kind of pass ("render", "compute") that began and did not end: a failure ends it
        # where the backend's image stands upside down against OpenGL's (section 21, "The device layer")
        self.m_flip_offscreen = rhi.isYUpInFramebuffer() != rhi.isYUpInNDC()
        self.m_flip_display = not rhi.isYUpInNDC()
        self.m_zero_to_one = rhi.isClipDepthZeroToOne()

    # recording
    def _open(self) -> _SPass:
        if self._m_pass is None or self._m_pass.target is not self._m_fbo:
            self._close()
            self._m_pass = _SPass(self._m_fbo, None, self._m_pending)
            self._m_pending = []
        return self._m_pass

    def _close(self) -> None:
        if self._m_pass is not None:
            self._m_passes.append(self._m_pass)
            self._m_pass = None

    def later(self, native) -> None:
        """Destroy a resource after the frame in flight (None is ignored; at once after ``release``: no frame
        follows that would)."""
        if native is None:
            return
        if self.m_released:
            native.destroy()
        else:
            self._m_doomed.append(native)

    def release(self) -> None:
        """Destroy what the device made for its view and what waits to be destroyed, now: the view's QRhi goes away
        (the widget moved to another window, its window closes), and with it no frame is in flight. A renderer
        releases its own resources before or after: after, each is destroyed as it is let go."""
        self.m_released = True
        self._m_passes, self._m_pending, self._m_pass, self._m_reads = [], [], None, []
        pipelines = [pipeline for pipeline in self._m_pipelines.values() if pipeline is not None]
        for native in (*self._m_dying, *self._m_doomed, *self._m_srbs.values(), *pipelines, *self._m_samplers.values()):
            native.destroy()
        self._m_dying, self._m_doomed, self._m_srbs, self._m_srb_users, self._m_pipelines, self._m_samplers = (
            [],
            [],
            {},
            {},
            {},
            {},
        )
        for texture in self._m_dummies.values():
            texture.release()
        self._m_dummies = {}
        if self.screen is not None:  # its colour is the view's own
            self.screen.release()
            self.screen.depth_attachment.release()
            self.screen = None

    def _upload(self, resource, upload) -> None:
        """An upload before the draws that follow it: a pass that already reads ``resource`` ends first."""
        if self._m_pass is not None and id(resource) in self._m_pass.used:
            self._close()
        (self._m_pass.uploads if self._m_pass is not None else self._m_pending).append(upload)

    def _clear(self, framebuffer, rgba: tuple, depth: float) -> None:
        if self._m_pass is not None and self._m_pass.target is framebuffer and not self._m_pass.draws:
            self._m_pass.clear = (rgba, depth)
            return
        self._close()
        self._m_pass = _SPass(framebuffer, (rgba, depth), self._m_pending)
        self._m_pending = []

    def _draw(self, vertex_array, mode: int, count: int, first: int, instances: int) -> None:
        current_pass, program = self._open(), vertex_array.program
        flip = self.m_flip_display if current_pass.target.m_display else self.m_flip_offscreen
        data = bytearray(program.m_data)
        clip = program.m_baked.members.get(glslport.CLIP)
        if clip is not None:
            pack_member(data, clip, (-1.0 if flip else 1.0, 1.0 if self.m_zero_to_one else 0.0))
        textures = tuple(self.m_units.get(program.m_units[name]) for name in program.m_ported.samplers)
        state = (self.m_flags & (_BLEND | _DEPTH | _CULL), tuple(self.blend_func), self.depth_mask, self.depth_func,
                 self.wireframe, flip)  # fmt: skip
        current_pass.draws.append(
            _SDraw(vertex_array, mode, count, first, instances, bytes(data), textures, state, tuple(self.viewport))
        )
        current_pass.used.update(id(buffer) for buffer, _, _ in vertex_array.m_buffers)
        if vertex_array.index_buffer is not None:
            current_pass.used.add(id(vertex_array.index_buffer))
        current_pass.used.update(id(texture) for texture in textures if texture is not None)

    def native_pass(self, record) -> None:
        """A pass of the caller's own, run in its place among the recorded ones: ``record(buffer, batch)`` begins and
        ends it on the command buffer (a render pass or a compute pass), handing ``batch`` (the uploads before it) to its
        ``beginPass`` or ``beginComputePass`` (the game's shaders, ``gameframe``)."""
        self._close()
        self._m_passes.append(_SPass(None, None, self._m_pending, native=record))
        self._m_pending = []

    def read_later(self, texture):
        """A ``QRhiReadbackResult`` that holds ``texture``'s pixels (level 0) once this frame is done on the GPU: QRhi's
        readbacks arrive after the frame, not mid-frame (picking reads the id picture this way)."""
        result = QRhiReadbackResult()
        self._close()
        self._m_pending.append(
            lambda update_batch: update_batch.readBackTexture(QRhiReadbackDescription(texture.native), result)
        )
        self._m_reads.append(result)  # kept alive until the frame is done
        return result

    def render_target(self, framebuffer, is_cleared: bool = True):
        """(QRhi render target, its pass descriptor, its signature) of a framebuffer, for a native pass."""
        return self._target(framebuffer, not is_cleared, None)

    def copy_framebuffer(self, destination, source) -> None:
        """A multisampled target resolved into ``destination`` at the end of its last pass, any other copied."""
        dst_tex = destination.color_attachments[0] if hasattr(destination, "color_attachments") else destination
        if source.samples > 1:
            last = next(
                (
                    recorded_pass
                    for recorded_pass in reversed([*self._m_passes, self._m_pass])
                    if recorded_pass is not None and recorded_pass.target is source
                ),
                None,
            )
            if last is not None:
                last.resolve = dst_tex
                if last is self._m_pass:
                    self._close()
            return
        if self._m_pass is not None and self._m_pass.target is source:
            self._close()
        (self._m_pending).append(
            lambda update_batch: update_batch.copyTexture(dst_tex.native, source.color_attachments[0].native)
        )

    # running
    def _run(self, command_buffer) -> None:
        """Run the recorded passes; whatever raises, the recording is cleared and the frame's resources rotate, so one
        bad pass does not replay in every later frame. The first error goes on to the caller (``errors`` keeps it)."""
        self._close()
        passes, pending = self._m_passes, self._m_pending
        self._m_passes, self._m_pending = [], []
        try:
            self._run_passes(command_buffer, passes, pending)
        except Exception as exc:
            self.m_errors.append(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._end_begun(command_buffer)
            # what was released during the previous frame is destroyed now: Direct3D 11 runs a frame's commands at
            # its end and holds plain pointers to them (Python owns the objects: no deleteLater)
            for native in self._m_dying:
                native.destroy()
            self._m_dying, self._m_doomed = self._m_doomed, []
            self._m_reads = self._m_reads[-8:]

    def _end_begun(self, command_buffer) -> None:
        """End the pass a failure left open, by its kind."""
        kind, self._m_begun = self._m_begun, ""
        if kind:
            try:
                (command_buffer.endComputePass if kind == "compute" else command_buffer.endPass)()
            except Exception:  # a pass the backend already lost: nothing more to end
                pass

    def _native(self, command_buffer, recorded_pass: _SPass) -> None:
        """A native pass with its uploads; an error is kept in ``errors`` and the frame goes on with the next pass."""
        update_batch = self._batch()
        try:
            for upload in recorded_pass.uploads:
                upload(update_batch)
            recorded_pass.native(_CNativeCommands(command_buffer, self), update_batch)
        except Exception as exc:
            traceback.print_exc()
            self._end_begun(command_buffer)
            self.m_errors.append(f"{type(exc).__name__}: {exc}")
            self.m_pass_errors.append(self.m_errors[-1])

    def _batch(self):
        """A resource update batch, kept in ``_open_batches`` until its pass hands it on."""
        update_batch = self.m_rhi.nextResourceUpdateBatch()
        self._m_open_batches.append(update_batch)
        return update_batch

    def _run_passes(self, command_buffer, passes: list, pending: list) -> None:
        counters: dict[int, int] = {}
        try:
            for recorded_pass in passes:
                if recorded_pass.native is not None:
                    self._native(command_buffer, recorded_pass)
                else:
                    self._run_pass(command_buffer, recorded_pass, counters)
            if pending:
                update_batch = self._batch()
                for upload in pending:
                    upload(update_batch)
                command_buffer.resourceUpdate(update_batch)
                self._m_open_batches.remove(update_batch)
        finally:
            for update_batch in self._m_open_batches:  # a batch no pass took over (QRhi's pool is small)
                getattr(update_batch, "release", lambda: None)()
            self._m_open_batches = []

    def _run_pass(self, command_buffer, recorded_pass: _SPass, counters: dict) -> None:
        update_batch = self._batch()
        for upload in recorded_pass.uploads:
            upload(update_batch)
        render_target, render_pass, signature = self._target(
            recorded_pass.target, recorded_pass.clear is None, recorded_pass.resolve
        )
        ready = []
        for draw in recorded_pass.draws:
            program = draw.vao.program
            uniform_index = counters.get(id(program), 0)
            counters[id(program)] = uniform_index + 1
            uniform_buffer = self._uniforms(program, uniform_index)
            update_batch.updateDynamicBuffer(uniform_buffer, 0, draw.data)
            resource_bindings = self._srb(program, uniform_buffer, uniform_index, draw.textures)
            pipeline = self._pipeline(draw, resource_bindings, render_pass, signature, recorded_pass.target)
            if pipeline is not None:
                ready.append((draw, resource_bindings, pipeline))
        rgba, depth = recorded_pass.clear if recorded_pass.clear is not None else ((0.0, 0.0, 0.0, 0.0), 1.0)
        command_buffer.beginPass(render_target, QColor.fromRgbF(*(min(max(float(channel), 0.0), 1.0) for channel in rgba)),
                     QRhiDepthStencilClearValue(float(depth), 0), update_batch)  # fmt: skip
        self._m_begun = "render"
        self._m_open_batches.remove(update_batch)
        target_height = recorded_pass.target.size[1]
        for draw, resource_bindings, pipeline in ready:
            command_buffer.setGraphicsPipeline(pipeline)
            x, y, width, viewport_height = draw.viewport
            command_buffer.setViewport(
                QRhiViewport(x, target_height - y - viewport_height if draw.state[-1] else y, width, viewport_height)
            )
            command_buffer.setShaderResources(resource_bindings)
            vao = draw.vao
            if vao.m_buffers:
                index = vao.index_buffer
                index_format = (
                    command_buffer.IndexFormat.IndexUInt32
                    if vao.index_element_size == 4
                    else command_buffer.IndexFormat.IndexUInt16
                )
                command_buffer.setVertexInput(
                    0,
                    [(buffer.native, 0) for buffer, _, _ in vao.m_buffers],
                    index.native if index else None,
                    0,
                    index_format,
                )
            if vao.index_buffer is not None:
                command_buffer.drawIndexed(draw.count, draw.instances, draw.first)
            else:
                command_buffer.draw(draw.count, draw.instances, draw.first)
        command_buffer.endPass()
        self._m_begun = ""

    def _uniforms(self, program, index: int):
        while len(program.m_uniform_pool) <= index:
            size_bytes = (max(16, program.m_baked.block_size) + 255) // 256 * 256
            buffer = self.m_rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, size_bytes)
            buffer.create()
            program.m_uniform_pool.append(buffer)
        return program.m_uniform_pool[index]

    def _target(self, framebuffer, is_preserved: bool, resolve):
        key = (is_preserved, resolve.m_serial if resolve is not None else None)
        found = framebuffer.m_targets.get(key)
        if found is not None:
            return found
        description = QRhiTextureRenderTargetDescription()
        colors = []
        for i, texture in enumerate(framebuffer.color_attachments):
            attachment = QRhiColorAttachment(texture.native)
            if i == 0 and resolve is not None:
                attachment.setResolveTexture(resolve.native)
            colors.append(attachment)
        description.setColorAttachments(colors)
        if framebuffer.depth_attachment is not None:
            description.setDepthTexture(framebuffer.depth_attachment.native)
        flags = QRhiTextureRenderTarget.Flag(0)
        if is_preserved:
            flags = QRhiTextureRenderTarget.Flag.PreserveColorContents
            flags |= QRhiTextureRenderTarget.Flag.PreserveDepthStencilContents
        render_target = self.m_rhi.newTextureRenderTarget(description, flags)
        render_pass = render_target.newCompatibleRenderPassDescriptor()
        render_target.setRenderPassDescriptor(render_pass)
        if not render_target.create():
            raise RuntimeError("a render target was not created")
        signature = (tuple(int(texture.native.format().value) for texture in framebuffer.color_attachments),
                     framebuffer.depth_attachment is not None, framebuffer.samples, resolve is not None)  # fmt: skip
        framebuffer.m_targets[key] = found = (render_target, render_pass, signature)
        return found

    def _dummy(self, glsl_type: str):
        """A texture for a sampler nothing is bound to (moderngl reads black there)."""
        if glsl_type not in self._m_dummies:
            kind = {"sampler3D": "3d", "sampler2DArray": "array", "sampler2DShadow": "depth"}.get(glsl_type, "2d")
            dtype = "u1" if glsl_type.startswith("usampler") else "f4" if kind == "depth" else "f1"
            component_count = 1 if dtype != "f1" else 4
            dimensions = (1, 1, 1) if kind in ("3d", "array") else (1, 1)
            self._m_dummies[glsl_type] = self._texture(dimensions, component_count, None, dtype, kind)
        return self._m_dummies[glsl_type]

    def _texture(self, size_px, components, data, dtype="f1", kind="2d", samples=0, srgb=False):
        raise NotImplementedError  # the device's (rhidevice.CRhiContext)

    def _sampler(self, texture, glsl_type: str):
        integer = glsl_type.startswith(("usampler", "isampler"))
        key = (texture.sampler_key(), texture.m_mipmapped, integer, glsl_type.endswith("Shadow"))
        if key not in self._m_samplers:
            min_filter_code, mag_filter_code = texture.filter
            near = QRhiSampler.Filter.Nearest
            mag_filter = near if mag_filter_code == 0x2600 or integer else QRhiSampler.Filter.Linear
            min_filter = near if min_filter_code in (0x2600, 0x2700, 0x2702) or integer else QRhiSampler.Filter.Linear
            mip_filter = QRhiSampler.Filter.None_
            if texture.m_mipmapped and min_filter_code >= 0x2700:
                mip_filter = near if min_filter_code in (0x2700, 0x2701) else QRhiSampler.Filter.Linear
            wrap = [QRhiSampler.AddressMode.Repeat if is_repeated else QRhiSampler.AddressMode.ClampToEdge
                    for is_repeated in (texture.repeat_x, texture.repeat_y, texture.repeat_z)]  # fmt: skip
            sampler = self.m_rhi.newSampler(mag_filter, min_filter, mip_filter, *wrap)
            if glsl_type.endswith("Shadow") and texture.compare_func:
                sampler.setTextureCompareOp(_SAMPLER_COMPARE[texture.compare_func])
            sampler.create()
            self._m_samplers[key] = sampler
        return self._m_samplers[key]

    def _srb(self, program, uniform_buffer, uniform_index: int, textures: tuple):
        bound = []
        for (name, sampler_variable), texture in zip(program.m_ported.samplers.items(), textures, strict=True):
            texture = (
                texture if texture is not None and texture.native is not None else self._dummy(sampler_variable.type)
            )
            bound.append((sampler_variable, texture, program.m_ported.sampler_stages[name]))
        key = (
            program.m_serial,
            uniform_index,
            tuple((texture.m_serial, texture.sampler_key()) for _, texture, _ in bound),
        )
        resource_bindings = self._m_srbs.get(key)
        if resource_bindings is None:
            both = Binding.StageFlag.VertexStage | Binding.StageFlag.FragmentStage
            bindings = [Binding.uniformBuffer(0, both, uniform_buffer)]
            for sampler_variable, texture, stages in bound:
                flag = Binding.StageFlag(0)
                if "v" in stages:
                    flag |= Binding.StageFlag.VertexStage
                if "f" in stages:
                    flag |= Binding.StageFlag.FragmentStage
                bindings.append(
                    Binding.sampledTexture(
                        sampler_variable.slot, flag, texture.native, self._sampler(texture, sampler_variable.type)
                    )
                )
            resource_bindings = self.m_rhi.newShaderResourceBindings()
            resource_bindings.setBindings(bindings)
            resource_bindings.create()
            self._m_srbs[key] = resource_bindings
            for texture, _sampler in key[2]:
                self._m_srb_users.setdefault(texture, set()).add(key)
        return resource_bindings

    def forget(self, serial: int) -> None:
        """Drop the resource sets that bind the texture ``serial`` (it was released or made again): looked up by
        the texture, not searched for (an island's hundreds of maps let go against thousands of sets held the
        window for seconds)."""
        for key in self._m_srb_users.pop(serial, ()):
            self._drop_srb(key)

    def _drop_srb(self, key: tuple) -> None:
        srb = self._m_srbs.pop(key, None)
        if srb is not None:
            self.later(srb)
        for texture_serial, _sampler in key[2]:
            users = self._m_srb_users.get(texture_serial)
            if users is not None:
                users.discard(key)
                if not users:
                    del self._m_srb_users[texture_serial]

    def forget_program(self, serial: int) -> None:
        """Drop the pipelines and resource sets of a program that was released (its serial is never given again)."""
        for key in [known_key for known_key in self._m_srbs if known_key[0] == serial]:
            self._drop_srb(key)
        for key in [known_key for known_key in self._m_pipelines if known_key[0] == serial]:
            self.later(self._m_pipelines.pop(key))

    def _pipeline(self, draw: _SDraw, resource_bindings, render_pass, signature, target):
        program = draw.vao.program
        key = (program.m_serial, draw.vao.m_key, draw.mode, draw.state, signature)
        if key in self._m_pipelines:
            return self._m_pipelines[key]
        flags, blend, depth_mask, depth_func, wireframe, flip = draw.state
        pipeline = self.m_rhi.newGraphicsPipeline()
        vertex_shader, fragment_shader = program.m_shaders
        pipeline.setShaderStages([QRhiShaderStage(QRhiShaderStage.Type.Vertex, vertex_shader),
                            QRhiShaderStage(QRhiShaderStage.Type.Fragment, fragment_shader)])  # fmt: skip
        layout = QRhiVertexInputLayout()
        bindings, attributes = draw.vao.m_layout
        step = QRhiVertexInputBinding.Classification
        layout.setBindings(
            [
                QRhiVertexInputBinding(stride, step.PerInstance if is_per_instance else step.PerVertex)
                for stride, is_per_instance in bindings
            ]
        )
        layout.setAttributes(
            [
                QRhiVertexInputAttribute(binding, location, format_code, offset, matrix_slice)
                for binding, location, format_code, offset, matrix_slice in attributes
            ]
        )
        pipeline.setVertexInputLayout(layout)
        pipeline.setShaderResourceBindings(resource_bindings)
        pipeline.setRenderPassDescriptor(render_pass)
        pipeline.setSampleCount(signature[2])
        pipeline.setTopology(_TOPOLOGY[draw.mode])
        blends = []
        for _ in target.color_attachments:
            target_blend = Pipeline.TargetBlend()
            if flags & _BLEND:
                source_factor, destination_factor, *alpha = blend
                target_blend.enable = True
                target_blend.srcColor, target_blend.dstColor = _FACTOR[source_factor], _FACTOR[destination_factor]
                target_blend.srcAlpha, target_blend.dstAlpha = (
                    (_FACTOR[alpha[0]], _FACTOR[alpha[1]]) if alpha else (target_blend.srcColor, target_blend.dstColor)
                )
            blends.append(target_blend)
        pipeline.setTargetBlends(blends)
        depth = bool(flags & _DEPTH) and target.depth_attachment is not None
        pipeline.setDepthTest(depth)
        pipeline.setDepthWrite(depth and depth_mask)
        pipeline.setDepthOp(_COMPARE[depth_func])
        if flags & _CULL:
            pipeline.setCullMode(Pipeline.CullMode.Back)
            pipeline.setFrontFace(Pipeline.FrontFace.CW if flip else Pipeline.FrontFace.CCW)
        if wireframe:
            pipeline.setPolygonMode(Pipeline.PolygonMode.Line)
        if not pipeline.create():
            self.m_errors.append(
                f"a pipeline was refused: inputs {sorted(program.m_ported.attributes)}, samplers "
                f"{sorted(program.m_ported.samplers)}, target {signature}, state {draw.state}"
            )
            pipeline = None
        self._m_pipelines[key] = pipeline
        return pipeline

    @staticmethod
    def next_serial() -> int:
        return next(_serial)
