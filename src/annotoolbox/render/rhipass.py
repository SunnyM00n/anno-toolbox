"""The QRhi device's recording: draws gathered into render passes per target and run at the frame's end, with the
pipelines, resource sets, samplers and render targets they need made once and kept.

``Recorder`` is the base of ``rhidevice.RhiContext`` (the state a renderer sets, ``_draw``, ``_clear``,
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

GP, B = QRhiGraphicsPipeline, QRhiShaderResourceBinding
AF = QRhiVertexInputAttribute.Format
_BLEND, _DEPTH, _CULL = 1, 2, 4  # moderngl's flag bits (rhidevice)
_TOKEN = re.compile(r"(\d*)([fiux])(\d*)")
_ATTRIBUTE = {
    ("f", 4): (AF.Float, AF.Float2, AF.Float3, AF.Float4), ("f", 2): (AF.Half, AF.Half2, AF.Half3, AF.Half4),
    ("f", 1): (AF.UNormByte, AF.UNormByte2, None, AF.UNormByte4), ("i", 4): (AF.SInt, AF.SInt2, AF.SInt3, AF.SInt4),
    ("u", 4): (AF.UInt, AF.UInt2, AF.UInt3, AF.UInt4),
}  # fmt: skip
_TOPOLOGY = {0: GP.Topology.Points, 1: GP.Topology.Lines, 3: GP.Topology.LineStrip, 4: GP.Topology.Triangles,
             5: GP.Topology.TriangleStrip, 6: GP.Topology.TriangleFan}  # fmt: skip
_FACTOR = {0: GP.BlendFactor.Zero, 1: GP.BlendFactor.One, 0x300: GP.BlendFactor.SrcColor,
           0x301: GP.BlendFactor.OneMinusSrcColor, 0x302: GP.BlendFactor.SrcAlpha,
           0x303: GP.BlendFactor.OneMinusSrcAlpha, 0x304: GP.BlendFactor.DstAlpha,
           0x305: GP.BlendFactor.OneMinusDstAlpha, 0x306: GP.BlendFactor.DstColor,
           0x307: GP.BlendFactor.OneMinusDstColor}  # fmt: skip
_COMPARE = {"<": GP.CompareOp.Less, "<=": GP.CompareOp.LessOrEqual, "==": GP.CompareOp.Equal,
            ">=": GP.CompareOp.GreaterOrEqual, ">": GP.CompareOp.Greater, "!=": GP.CompareOp.NotEqual,
            "0": GP.CompareOp.Never, "1": GP.CompareOp.Always}  # fmt: skip
_SAMPLER_COMPARE = {k: getattr(QRhiSampler.CompareOp, v.name) for k, v in _COMPARE.items()}
_LOCATIONS = {"mat3": 3, "mat4": 4}
_serial = itertools.count(1)


def vertex_layout(attributes: dict[str, glslport.Variable], content, skip_errors: bool):
    """(buffers as (buffer, stride, per instance), layout) of a vertex array: the layout is ((stride, per
    instance) per binding, (binding, location, format, offset, matrix slice) per attribute), plain tuples for keying.
    A matrix input takes a location per column, each also marked as its slice (the Direct3D backends name them so)."""
    buffers, bindings, attrs = [], [], []
    for binding, (buf, fmt, *names) in enumerate(content):
        spec, _, step = fmt.partition("/")
        offset, names = 0, list(names)
        for token in spec.split():
            m = _TOKEN.fullmatch(token)
            if not m:
                raise ValueError(f"cannot read the vertex format {fmt!r}")
            count, kind = int(m.group(1) or 1), m.group(2)
            size = int(m.group(3) or (1 if kind == "x" else 4))
            if kind == "x":
                offset += count * size
                continue
            name = names.pop(0)
            var = attributes.get(name)
            if var is None and not skip_errors:
                raise KeyError(f"the program has no input {name}")
            if var is not None:
                columns = _LOCATIONS.get(var.type, 1)
                per = count // columns
                for c in range(columns):
                    fmt_c = _ATTRIBUTE[(kind, size)][per - 1]
                    attrs.append((binding, var.slot + c, fmt_c, offset + c * per * size, c if columns > 1 else -1))
            offset += count * size
        bindings.append((offset, step == "i"))
        buffers.append((buf, offset, step == "i"))
    return buffers, (tuple(bindings), tuple(attrs))


def layout_key(layout) -> tuple:
    return tuple(layout[0]), tuple((b, loc, int(f.value), off, m) for b, loc, f, off, m in layout[1])


@dataclass
class _Draw:
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
class _Pass:
    target: object
    clear: tuple | None  # (rgba, depth)
    uploads: list
    draws: list = field(default_factory=list)
    used: set = field(default_factory=set)
    resolve: object = None
    native: object = None  # a pass recorded by its owner: fn(cb, batch), the batch given to its beginPass


class _NativeCommands:
    """The command buffer as a native pass sees it: the recorder learns which kind of pass it began (one that fails
    is ended by that kind, one that never began is not ended: QRhi has no check and would fall over) and drops the
    batch a begin or a ``resourceUpdate`` took over from its open ones (released again at the frame's end, QRhi's
    pool counted it free twice)."""

    def __init__(self, cb, recorder: Recorder) -> None:
        self._cb, self._rec = cb, recorder

    def __getattr__(self, name: str):
        return getattr(self._cb, name)

    def _taken(self, batch) -> None:
        """QRhi took ``batch`` over (and lets it go itself): no longer one of the recorder's to release."""
        if batch is not None and any(batch is u for u in self._rec._open_batches):
            self._rec._open_batches = [u for u in self._rec._open_batches if u is not batch]

    def _began(self, kind: str, batch) -> None:
        self._rec._begun = kind
        self._taken(batch)

    def resourceUpdate(self, batch) -> None:  # uploads handed on without a pass (QRhiCommandBuffer's name)
        self._cb.resourceUpdate(batch)
        self._taken(batch)

    def beginPass(self, *args) -> None:  # QRhiCommandBuffer's names
        self._cb.beginPass(*args)
        self._began("render", args[3] if len(args) > 3 else None)

    def beginComputePass(self, *args) -> None:
        self._cb.beginComputePass(*args)
        self._began("compute", args[0] if args else None)

    def endPass(self, *args) -> None:
        self._rec._begun = ""
        self._cb.endPass(*args)

    def endComputePass(self, *args) -> None:
        self._rec._begun = ""
        self._cb.endComputePass(*args)


class Recorder:
    """The state and the recording of ``rhidevice.RhiContext`` (module docstring)."""

    def __init__(self, rhi) -> None:
        self.rhi = rhi
        self.flags = 0
        self.blend_func = (0x302, 0x303)
        self.depth_mask, self.depth_func, self.wireframe = True, "<", False
        self.viewport = (0, 0, 1, 1)
        self.units: dict[int, object] = {}
        self.screen = None
        self._fbo = None
        self._passes: list[_Pass] = []
        self._pass: _Pass | None = None
        self._pending: list = []
        self._doomed: list = []
        self._dying: list = []
        self._reads: list = []
        self._pipelines: dict = {}
        self._srbs: dict = {}
        self._srb_users: dict[int, set] = {}  # a texture's serial -> the keys of the resource sets that bind it
        self.released = False  # ``release`` ran: what is let go from now on is destroyed at once
        self._samplers: dict = {}
        self._dummies: dict = {}
        self.errors: list[str] = []
        self.failed_models: list[tuple[object, str]] = []  # a native pass's model that did not prepare, its error
        self.pass_errors: list[str] = []  # the native passes that raised (also in ``errors``), for their owner
        self._open_batches: list = []
        self._begun = ""  # the kind of pass ("render", "compute") that began and did not end: a failure ends it
        # where the backend's image stands upside down against OpenGL's (section 21, "The device layer")
        self.flip_offscreen = rhi.isYUpInFramebuffer() != rhi.isYUpInNDC()
        self.flip_display = not rhi.isYUpInNDC()
        self.zero_to_one = rhi.isClipDepthZeroToOne()

    # recording
    def _open(self) -> _Pass:
        if self._pass is None or self._pass.target is not self._fbo:
            self._close()
            self._pass = _Pass(self._fbo, None, self._pending)
            self._pending = []
        return self._pass

    def _close(self) -> None:
        if self._pass is not None:
            self._passes.append(self._pass)
            self._pass = None

    def later(self, native) -> None:
        """Destroy a resource after the frame in flight (None is ignored; at once after ``release``: no frame
        follows that would)."""
        if native is None:
            return
        if self.released:
            native.destroy()
        else:
            self._doomed.append(native)

    def release(self) -> None:
        """Destroy what the device made for its view and what waits to be destroyed, now: the view's QRhi goes away
        (the widget moved to another window, its window closes), and with it no frame is in flight. A renderer
        releases its own resources before or after: after, each is destroyed as it is let go."""
        self.released = True
        self._passes, self._pending, self._pass, self._reads = [], [], None, []
        pipelines = [p for p in self._pipelines.values() if p is not None]
        for native in (*self._dying, *self._doomed, *self._srbs.values(), *pipelines, *self._samplers.values()):
            native.destroy()
        self._dying, self._doomed, self._srbs, self._srb_users, self._pipelines, self._samplers = [], [], {}, {}, {}, {}
        for tex in self._dummies.values():
            tex.release()
        self._dummies = {}
        if self.screen is not None:  # its colour is the view's own
            self.screen.release()
            self.screen.depth_attachment.release()
            self.screen = None

    def _upload(self, resource, fn) -> None:
        """An upload before the draws that follow it: a pass that already reads ``resource`` ends first."""
        if self._pass is not None and id(resource) in self._pass.used:
            self._close()
        (self._pass.uploads if self._pass is not None else self._pending).append(fn)

    def _clear(self, fbo, rgba: tuple, depth: float) -> None:
        if self._pass is not None and self._pass.target is fbo and not self._pass.draws:
            self._pass.clear = (rgba, depth)
            return
        self._close()
        self._pass = _Pass(fbo, (rgba, depth), self._pending)
        self._pending = []

    def _draw(self, vao, mode: int, count: int, first: int, instances: int) -> None:
        p, prog = self._open(), vao.program
        flip = self.flip_display if p.target.display else self.flip_offscreen
        data = bytearray(prog.data)
        clip = prog.baked.members.get(glslport.CLIP)
        if clip is not None:
            pack_member(data, clip, (-1.0 if flip else 1.0, 1.0 if self.zero_to_one else 0.0))
        textures = tuple(self.units.get(prog.units[name]) for name in prog.ported.samplers)
        state = (self.flags & (_BLEND | _DEPTH | _CULL), tuple(self.blend_func), self.depth_mask, self.depth_func,
                 self.wireframe, flip)  # fmt: skip
        p.draws.append(_Draw(vao, mode, count, first, instances, bytes(data), textures, state, tuple(self.viewport)))
        p.used.update(id(b) for b, _, _ in vao.buffers)
        if vao.index_buffer is not None:
            p.used.add(id(vao.index_buffer))
        p.used.update(id(t) for t in textures if t is not None)

    def native_pass(self, fn) -> None:
        """A pass of the caller's own, run in its place among the recorded ones: ``fn(cb, batch)`` begins and ends
        it on the command buffer (a render pass or a compute pass), handing ``batch`` (the uploads before it) to its
        ``beginPass`` or ``beginComputePass`` (the game's shaders, ``gameframe``)."""
        self._close()
        self._passes.append(_Pass(None, None, self._pending, native=fn))
        self._pending = []

    def read_later(self, tex):
        """A ``QRhiReadbackResult`` that holds ``tex``'s pixels (level 0) once this frame is done on the GPU: QRhi's
        readbacks arrive after the frame, not mid-frame (picking reads the id picture this way)."""
        result = QRhiReadbackResult()
        self._close()
        self._pending.append(lambda u: u.readBackTexture(QRhiReadbackDescription(tex.native), result))
        self._reads.append(result)  # kept alive until the frame is done
        return result

    def render_target(self, fbo, clear: bool = True):
        """(QRhi render target, its pass descriptor, its signature) of a framebuffer, for a native pass."""
        return self._target(fbo, not clear, None)

    def copy_framebuffer(self, dst, src) -> None:
        """A multisampled target resolved into ``dst`` at the end of its last pass, any other copied."""
        dst_tex = dst.color_attachments[0] if hasattr(dst, "color_attachments") else dst
        if src.samples > 1:
            last = next((p for p in reversed([*self._passes, self._pass]) if p is not None and p.target is src), None)
            if last is not None:
                last.resolve = dst_tex
                if last is self._pass:
                    self._close()
            return
        if self._pass is not None and self._pass.target is src:
            self._close()
        (self._pending).append(lambda u: u.copyTexture(dst_tex.native, src.color_attachments[0].native))

    # running
    def _run(self, cb) -> None:
        """Run the recorded passes; whatever raises, the recording is cleared and the frame's resources rotate, so one
        bad pass does not replay in every later frame. The first error goes on to the caller (``errors`` keeps it)."""
        self._close()
        passes, pending = self._passes, self._pending
        self._passes, self._pending = [], []
        try:
            self._run_passes(cb, passes, pending)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._end_begun(cb)
            # what was released during the previous frame is destroyed now: Direct3D 11 runs a frame's commands at
            # its end and holds plain pointers to them (Python owns the objects: no deleteLater)
            for native in self._dying:
                native.destroy()
            self._dying, self._doomed = self._doomed, []
            self._reads = self._reads[-8:]

    def _end_begun(self, cb) -> None:
        """End the pass a failure left open, by its kind."""
        kind, self._begun = self._begun, ""
        if kind:
            try:
                (cb.endComputePass if kind == "compute" else cb.endPass)()
            except Exception:  # a pass the backend already lost: nothing more to end
                pass

    def _native(self, cb, p: _Pass) -> None:
        """A native pass with its uploads; an error is kept in ``errors`` and the frame goes on with the next pass."""
        u = self._batch()
        try:
            for fn in p.uploads:
                fn(u)
            p.native(_NativeCommands(cb, self), u)
        except Exception as exc:
            traceback.print_exc()
            self._end_begun(cb)
            self.errors.append(f"{type(exc).__name__}: {exc}")
            self.pass_errors.append(self.errors[-1])

    def _batch(self):
        """A resource update batch, kept in ``_open_batches`` until its pass hands it on."""
        u = self.rhi.nextResourceUpdateBatch()
        self._open_batches.append(u)
        return u

    def _run_passes(self, cb, passes: list, pending: list) -> None:
        counters: dict[int, int] = {}
        try:
            for p in passes:
                if p.native is not None:
                    self._native(cb, p)
                else:
                    self._run_pass(cb, p, counters)
            if pending:
                u = self._batch()
                for fn in pending:
                    fn(u)
                cb.resourceUpdate(u)
                self._open_batches.remove(u)
        finally:
            for u in self._open_batches:  # a batch no pass took over (QRhi's pool is small)
                getattr(u, "release", lambda: None)()
            self._open_batches = []

    def _run_pass(self, cb, p: _Pass, counters: dict) -> None:
        u = self._batch()
        for fn in p.uploads:
            fn(u)
        rt, rp, signature = self._target(p.target, p.clear is None, p.resolve)
        ready = []
        for d in p.draws:
            prog = d.vao.program
            k = counters.get(id(prog), 0)
            counters[id(prog)] = k + 1
            ubuf = self._uniforms(prog, k)
            u.updateDynamicBuffer(ubuf, 0, d.data)
            srb = self._srb(prog, ubuf, k, d.textures)
            pipe = self._pipeline(d, srb, rp, signature, p.target)
            if pipe is not None:
                ready.append((d, srb, pipe))
        rgba, depth = p.clear if p.clear is not None else ((0.0, 0.0, 0.0, 0.0), 1.0)
        cb.beginPass(rt, QColor.fromRgbF(*(min(max(float(c), 0.0), 1.0) for c in rgba)),
                     QRhiDepthStencilClearValue(float(depth), 0), u)  # fmt: skip
        self._begun = "render"
        self._open_batches.remove(u)
        h = p.target.size[1]
        for d, srb, pipe in ready:
            cb.setGraphicsPipeline(pipe)
            x, y, w, vh = d.viewport
            cb.setViewport(QRhiViewport(x, h - y - vh if d.state[-1] else y, w, vh))
            cb.setShaderResources(srb)
            vao = d.vao
            if vao.buffers:
                index = vao.index_buffer
                fmt = cb.IndexFormat.IndexUInt32 if vao.index_element_size == 4 else cb.IndexFormat.IndexUInt16
                cb.setVertexInput(
                    0, [(b.native, 0) for b, _, _ in vao.buffers], index.native if index else None, 0, fmt
                )
            if vao.index_buffer is not None:
                cb.drawIndexed(d.count, d.instances, d.first)
            else:
                cb.draw(d.count, d.instances, d.first)
        cb.endPass()
        self._begun = ""

    def _uniforms(self, prog, k: int):
        while len(prog.uniform_pool) <= k:
            size = (max(16, prog.baked.block_size) + 255) // 256 * 256
            buf = self.rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, size)
            buf.create()
            prog.uniform_pool.append(buf)
        return prog.uniform_pool[k]

    def _target(self, fbo, preserve: bool, resolve):
        key = (preserve, resolve.serial if resolve is not None else None)
        found = fbo.targets.get(key)
        if found is not None:
            return found
        desc = QRhiTextureRenderTargetDescription()
        colors = []
        for i, t in enumerate(fbo.color_attachments):
            a = QRhiColorAttachment(t.native)
            if i == 0 and resolve is not None:
                a.setResolveTexture(resolve.native)
            colors.append(a)
        desc.setColorAttachments(colors)
        if fbo.depth_attachment is not None:
            desc.setDepthTexture(fbo.depth_attachment.native)
        flags = QRhiTextureRenderTarget.Flag(0)
        if preserve:
            flags = QRhiTextureRenderTarget.Flag.PreserveColorContents
            flags |= QRhiTextureRenderTarget.Flag.PreserveDepthStencilContents
        rt = self.rhi.newTextureRenderTarget(desc, flags)
        rp = rt.newCompatibleRenderPassDescriptor()
        rt.setRenderPassDescriptor(rp)
        if not rt.create():
            raise RuntimeError("a render target was not created")
        signature = (tuple(int(t.native.format().value) for t in fbo.color_attachments),
                     fbo.depth_attachment is not None, fbo.samples, resolve is not None)  # fmt: skip
        fbo.targets[key] = found = (rt, rp, signature)
        return found

    def _dummy(self, gtype: str):
        """A texture for a sampler nothing is bound to (moderngl reads black there)."""
        if gtype not in self._dummies:
            kind = {"sampler3D": "3d", "sampler2DArray": "array", "sampler2DShadow": "depth"}.get(gtype, "2d")
            dtype = "u1" if gtype.startswith("usampler") else "f4" if kind == "depth" else "f1"
            comps = 1 if dtype != "f1" else 4
            size = (1, 1, 1) if kind in ("3d", "array") else (1, 1)
            self._dummies[gtype] = self._texture(size, comps, None, dtype, kind)
        return self._dummies[gtype]

    def _texture(self, size, components, data, dtype="f1", kind="2d", samples=0, srgb=False):
        raise NotImplementedError  # the device's (rhidevice.RhiContext)

    def _sampler(self, tex, gtype: str):
        integer = gtype.startswith(("usampler", "isampler"))
        key = (tex.sampler_key(), tex.mipmapped, integer, gtype.endswith("Shadow"))
        if key not in self._samplers:
            lo, hi = tex.filter
            near = QRhiSampler.Filter.Nearest
            mag = near if hi == 0x2600 or integer else QRhiSampler.Filter.Linear
            minf = near if lo in (0x2600, 0x2700, 0x2702) or integer else QRhiSampler.Filter.Linear
            mip = QRhiSampler.Filter.None_
            if tex.mipmapped and lo >= 0x2700:
                mip = near if lo in (0x2700, 0x2701) else QRhiSampler.Filter.Linear
            wrap = [QRhiSampler.AddressMode.Repeat if r else QRhiSampler.AddressMode.ClampToEdge
                    for r in (tex.repeat_x, tex.repeat_y, tex.repeat_z)]  # fmt: skip
            s = self.rhi.newSampler(mag, minf, mip, *wrap)
            if gtype.endswith("Shadow") and tex.compare_func:
                s.setTextureCompareOp(_SAMPLER_COMPARE[tex.compare_func])
            s.create()
            self._samplers[key] = s
        return self._samplers[key]

    def _srb(self, prog, ubuf, ubuf_index: int, textures: tuple):
        bound = []
        for (name, var), tex in zip(prog.ported.samplers.items(), textures, strict=True):
            tex = tex if tex is not None and tex.native is not None else self._dummy(var.type)
            bound.append((var, tex, prog.ported.sampler_stages[name]))
        key = (prog.serial, ubuf_index, tuple((t.serial, t.sampler_key()) for _, t, _ in bound))
        srb = self._srbs.get(key)
        if srb is None:
            both = B.StageFlag.VertexStage | B.StageFlag.FragmentStage
            bindings = [B.uniformBuffer(0, both, ubuf)]
            for var, tex, stages in bound:
                flag = B.StageFlag(0)
                if "v" in stages:
                    flag |= B.StageFlag.VertexStage
                if "f" in stages:
                    flag |= B.StageFlag.FragmentStage
                bindings.append(B.sampledTexture(var.slot, flag, tex.native, self._sampler(tex, var.type)))
            srb = self.rhi.newShaderResourceBindings()
            srb.setBindings(bindings)
            srb.create()
            self._srbs[key] = srb
            for t, _sampler in key[2]:
                self._srb_users.setdefault(t, set()).add(key)
        return srb

    def forget(self, serial: int) -> None:
        """Drop the resource sets that bind the texture ``serial`` (it was released or made again): looked up by
        the texture, not searched for (an island's hundreds of maps let go against thousands of sets held the
        window for seconds)."""
        for key in self._srb_users.pop(serial, ()):
            self._drop_srb(key)

    def _drop_srb(self, key: tuple) -> None:
        srb = self._srbs.pop(key, None)
        if srb is not None:
            self.later(srb)
        for t, _sampler in key[2]:
            users = self._srb_users.get(t)
            if users is not None:
                users.discard(key)
                if not users:
                    del self._srb_users[t]

    def forget_program(self, serial: int) -> None:
        """Drop the pipelines and resource sets of a program that was released (its serial is never given again)."""
        for key in [k for k in self._srbs if k[0] == serial]:
            self._drop_srb(key)
        for key in [k for k in self._pipelines if k[0] == serial]:
            self.later(self._pipelines.pop(key))

    def _pipeline(self, d: _Draw, srb, rp, signature, target):
        prog = d.vao.program
        key = (prog.serial, d.vao.key, d.mode, d.state, signature)
        if key in self._pipelines:
            return self._pipelines[key]
        flags, blend, depth_mask, depth_func, wireframe, flip = d.state
        ps = self.rhi.newGraphicsPipeline()
        vs, fs = prog.shaders
        ps.setShaderStages([QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs),
                            QRhiShaderStage(QRhiShaderStage.Type.Fragment, fs)])  # fmt: skip
        layout = QRhiVertexInputLayout()
        bindings, attrs = d.vao.layout
        step = QRhiVertexInputBinding.Classification
        layout.setBindings([QRhiVertexInputBinding(s, step.PerInstance if i else step.PerVertex) for s, i in bindings])
        layout.setAttributes([QRhiVertexInputAttribute(b, loc, f, off, m) for b, loc, f, off, m in attrs])
        ps.setVertexInputLayout(layout)
        ps.setShaderResourceBindings(srb)
        ps.setRenderPassDescriptor(rp)
        ps.setSampleCount(signature[2])
        ps.setTopology(_TOPOLOGY[d.mode])
        blends = []
        for _ in target.color_attachments:
            tb = GP.TargetBlend()
            if flags & _BLEND:
                src, dst, *alpha = blend
                tb.enable = True
                tb.srcColor, tb.dstColor = _FACTOR[src], _FACTOR[dst]
                tb.srcAlpha, tb.dstAlpha = (
                    (_FACTOR[alpha[0]], _FACTOR[alpha[1]]) if alpha else (tb.srcColor, tb.dstColor)
                )
            blends.append(tb)
        ps.setTargetBlends(blends)
        depth = bool(flags & _DEPTH) and target.depth_attachment is not None
        ps.setDepthTest(depth)
        ps.setDepthWrite(depth and depth_mask)
        ps.setDepthOp(_COMPARE[depth_func])
        if flags & _CULL:
            ps.setCullMode(GP.CullMode.Back)
            ps.setFrontFace(GP.FrontFace.CW if flip else GP.FrontFace.CCW)
        if wireframe:
            ps.setPolygonMode(GP.PolygonMode.Line)
        if not ps.create():
            self.errors.append(
                f"a pipeline was refused: inputs {sorted(prog.ported.attributes)}, samplers "
                f"{sorted(prog.ported.samplers)}, target {signature}, state {d.state}"
            )
            ps = None
        self._pipelines[key] = ps
        return ps

    @staticmethod
    def next_serial() -> int:
        return next(_serial)
