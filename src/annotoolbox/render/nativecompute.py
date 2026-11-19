"""The game's compute shaders and the bake's draw on the native backend's own command lists, and the module's textures
handed to QRhi so that the phases still on QRhi read them.

``Kernel.compute(device, shader, reflection)`` is one of the game's compute shaders as it ships (``Kernel.graphics``
the stages of a draw): its binding plan, layouts, samplers and pipeline, its constant buffers written per dispatch
(NVRHI's volatile ones), ``dispatch(list, named, fill, groups)`` records it. ``Handover(device, rhi)`` gives a module
texture to QRhi (``QRhiTexture::createFrom``) and hands its state over both ways (``take_back``, ``handed_back``).
Users: ``pagesnative``, ``bakenative``; docs/rendering/native-backend.md section 10.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ..infra.dxc import ConstantBuffer, Variable
from . import gameshader as gs
from . import nativegpu, nativelayout

# NVRHI's permanent state of the module's textures between its lists (ShaderResource:
# D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE | PIXEL_SHADER_RESOURCE): QRhi takes them over in it
SHADER_READ = 0x40 | 0x80
CONSTANT_SLOT = 256  # a constant buffer's bytes are rounded up to this (Direct3D 12's alignment)
SETS = 64  # binding sets a kernel keeps for the resources it was handed last [ours]
COMPUTE_STAGE = "cs"


def records(element: ConstantBuffer, count: int) -> ConstantBuffer:
    """A structured buffer of ``count`` records (the reflection's element type, ``$Element``) as a buffer of one
    array named as the buffer, so its records are written by path (``cSplatsBuffer[3].MatID``)."""
    one = element.variables[0]
    array = Variable(element.name, 0, element.size * count, one.type, elements=count, members=one.members)
    return ConstantBuffer(element.name, element.size * count, [array])


def pointer(obj) -> int:
    import shiboken6

    return int(shiboken6.getCppPointer(obj)[0])


class Kernel:
    """A compute shader or a draw's stages on the module (module docstring). ``named`` (shader name -> the module's
    texture, buffer or descriptor table) binds them by name; the kernel's own constant buffers join by their names."""

    def __init__(self, device, plan: nativelayout.BindingPlan, cbuffers: dict, name: str) -> None:
        self.device, self.plan, self.name = device, plan, name
        self.layouts = nativegpu.layouts(device, plan)
        self.samplers = nativegpu.samplers(device, plan)
        volatile = {i.name for spec in plan.layouts for i in spec.items if i.kind == "volatile"}
        self.cbuffers = {n: cb for n, cb in cbuffers.items() if n in volatile}
        self.buffers = {n: device.buffer(-(-max(cb.size, 16) // CONSTANT_SLOT) * CONSTANT_SLOT, "volatile", name=n)
                        for n, cb in self.cbuffers.items()}  # fmt: skip
        self.pipeline = None  # a compute kernel's; a draw's go by its vertex layout (``pipelines``)
        self.pipelines: dict = {}
        self.stages: dict = {}  # a draw's shaders by stage, its vertex shader's bytecode and its fixed state
        self.vs_bytecode = b""
        self.state: dict = {}
        self._sets: OrderedDict[tuple, tuple] = OrderedDict()

    @classmethod
    def compute(cls, device, shader, reflection) -> Kernel:
        """One compute shader (``shadercache.CachedShader``-like: ``bytecode``, ``name``) and its reflection."""
        plan = nativelayout.BindingPlan.of([reflection], volatile=True)
        kernel = cls(device, plan, {cb.name: cb for cb in reflection.cbuffers}, shader.name)
        made = device.shader(COMPUTE_STAGE, shader.bytecode, shader.name)
        kernel.pipeline = device.compute_pipeline(made, kernel.layouts)
        return kernel

    @classmethod
    def graphics(cls, device, stages: dict, state: dict, bindless: tuple = ()) -> Kernel:
        """A draw's stages (``{"vs": (shader, reflection), "ps": ...}``) with the pipeline's fixed ``state``; its
        pipelines are made per vertex layout (``pipeline_for``). ``bindless``: the arrays bound as descriptor
        tables."""
        reflections = [r for _s, r in stages.values()]
        plan = nativelayout.BindingPlan.of(reflections, bindless=bindless, volatile=True)
        kernel = cls(device, plan, {cb.name: cb for r in reflections for cb in r.cbuffers}, stages["vs"][0].name)
        kernel.stages = {k: device.shader(k, s.bytecode, s.name) for k, (s, _r) in stages.items()}
        kernel.vs_bytecode = stages["vs"][0].bytecode
        kernel.state = state
        return kernel

    def pipeline_for(self, elements: tuple, stride: int) -> tuple:
        """(pipeline, the constant vertex of the inputs the mesh lacks) for a mesh's stored ``elements``."""
        key = (tuple(map(tuple, elements)), stride)
        found = self.pipelines.get(key)
        if found is None:
            inputs = nativelayout.signature(self.vs_bytecode)
            laid, fill = nativelayout.input_elements(elements, inputs, stride)
            s = self.stages
            pipe = self.device.graphics_pipeline(s["vs"], s.get("ps"), self.device.input_layout(laid), self.layouts,
                                                 self.state)  # fmt: skip
            found = self.pipelines[key] = (pipe, fill)
        return found

    def write(self, lst, fill, camera=None) -> dict:
        """Every constant buffer of the kernel written for the next dispatch or draw: the frame's values for
        ``camera`` (None: none), then ``fill(c)``; by name, for ``named``."""
        for name, cb in self.cbuffers.items():
            c = gs.Constants(cb)
            if camera is not None:
                gs.fill_frame(c, camera)
            if fill is not None:
                fill(c)
            lst.write_buffer(self.buffers[name], bytes(c.data))
        return self.buffers

    def sets(self, named: dict) -> list:
        """The sets and tables of the kernel's layouts in its pipeline's order (a set made when what it binds
        changed; the last ``SETS`` kept)."""
        out = []
        for index, spec in enumerate(self.plan.layouts):
            if spec.bindless:
                out.append(named[spec.items[0].name])
                continue
            held = tuple(named.get(i.name) for i in spec.items)
            key = (index, *map(id, held))
            found = self._sets.get(key)
            if found is None:
                made = nativegpu.binding_set(self.device, self.layouts[index], spec, named, self.samplers)
                found = self._sets[key] = (held, made)  # the resources held: their ids stay theirs
                while len(self._sets) > SETS:
                    self._sets.popitem(last=False)[1][1].release()
            else:
                self._sets.move_to_end(key)
            out.append(found[1])
        return out

    def dispatch(self, lst, named: dict, fill, groups: tuple[int, int, int]) -> None:
        """One dispatch of a compute kernel: its constants (``fill(c)``) written, its resources bound by name."""
        buffers = self.write(lst, fill)
        lst.dispatch(self.pipeline, self.sets({**named, **buffers}), *groups)

    def release(self) -> None:
        for _held, made in self._sets.values():
            made.release()
        for b in self.buffers.values():
            b.release()
        for pipe, _fill in self.pipelines.values():
            pipe.release()
        if self.pipeline is not None:
            self.pipeline.release()
        for layout in self.layouts:
            layout.release()
        self._sets, self.buffers, self.pipelines, self.pipeline = OrderedDict(), {}, {}, None


class Told:
    """With the debug layer: its and NVRHI's messages while a part records and sends its list (``messages``) and
    those before (``foreign``: the other parts' on the same device), for ``scripts/native_compare.py``."""

    device: object
    messages: list
    foreign: list

    def _told_before(self) -> None:
        if self.device.debug:
            self.foreign += self.device.messages()

    def _told_after(self) -> None:
        if self.device.debug:
            self.messages += self.device.messages()


class Handover:
    """The module's textures handed to QRhi: a QRhi texture over the module's resource (``give``), QRhi's states
    of them taken back before a list of the module's reads or writes them (``take_back``, recorded ahead of
    NVRHI's own barriers: NVRHI assumes its permanent state) and told to QRhi after (``handed_back``); let go with
    QRhi's wrapper first (``let_go``). Their work goes to the queue before QRhi's frame, so the states QRhi tracks
    then are those its last frame left (docs/rendering/native-backend.md 10.2)."""

    def __init__(self, device, rhi) -> None:
        self.device, self.rhi, self.module = device, rhi, nativegpu.load()
        self.given: dict[int, tuple] = {}  # id(QRhi texture) -> (QRhi texture, module texture)
        self._moved: list = []

    def give(self, tex, fmt, size: tuple[int, int], layers: int = 0, flags=None):
        """A QRhi texture of ``fmt`` (QRhi's) over the module texture ``tex``, tracked from ``SHADER_READ``."""
        rhi, flags = self.rhi, flags if flags is not None else QRhiTexture.Flag(0)
        q = rhi.newTextureArray(fmt, layers, QSize(*size), 1, flags) if layers else rhi.newTexture(fmt, QSize(*size),
                                                                                                    1, flags)  # fmt: skip
        if not self.module.qrhi_create_from(pointer(q), tex.native, SHADER_READ):
            raise RuntimeError(f"QRhi did not take the module's texture over ({fmt}, {size})")
        self.given[id(q)] = (q, tex)
        return q

    def native(self, q):
        """The module texture behind a QRhi texture ``give`` made, None for any other."""
        found = self.given.get(id(q)) if q is not None else None
        return found[1] if found is not None else None

    def take_back(self, lst, textures) -> None:
        """Before ``lst`` (the module's, open) uses ``textures`` (QRhi's, given here): each from the state QRhi
        left it in into ``SHADER_READ``."""
        items = []
        for q in textures:
            if q is None or id(q) not in self.given or any(m is q for m in self._moved):
                continue
            resource, state = self.module.qrhi_texture(pointer(q))
            if state != SHADER_READ:
                items.append((resource, state, SHADER_READ))
            self._moved.append(q)
        if items:
            lst.transitions(items)

    def handed_back(self) -> None:
        """After the list went to the queue: QRhi tracks what it took back in ``SHADER_READ`` again (NVRHI leaves
        its textures there at a list's end)."""
        for q in self._moved:
            q.setNativeLayout(SHADER_READ)
        self._moved = []

    def let_go(self, ctx, q) -> None:
        """QRhi's wrapper after the frame in flight, the module's texture some frames late (``Device.keep_frames``)."""
        found = self.given.pop(id(q), None)
        if found is None:
            return
        ctx.later(q)
        found[1].release()

    def drop(self, q) -> None:
        """QRhi's wrapper destroyed now (its owner goes, no frame of it follows), the module's texture late."""
        found = self.given.pop(id(q), None) if q is not None else None
        if found is not None:
            q.destroy()
            found[1].release()

    def release(self) -> None:
        for q, _tex in list(self.given.values()):
            self.drop(q)
        self._moved = []
