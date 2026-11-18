"""The terrain's nodes and their shadow drawn by the game's shipped shaders (``terrain.fx`` as the install's cache holds
it, bound as it is) through the native backend, recorded on QRhi's command list inside the G-buffer's and the shadow
map's passes: the same nodes, meshes and inputs as ``terraindraw``'s, without the rewrite and its compile.

``NativeTerrain(device, natives)`` is the entry, one per view: ``gbuffer(cb, models, camera, ground)`` and
``shadow(cb, casters, camera, cascades, info, ground)`` record the draws (between ``beginExternal`` and
``endExternal``; ``nativepass.draw_pass``'s ``external``). What they read of QRhi's (the pages, the baked arrays, the
ground's maps, the cascades) is QRhi's own resources, wrapped (``Bridge``). docs/rendering/native-backend.md section 9.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtGui import QRhiBuffer, QRhiTexture

from ..infra.dxc import ConstantBuffer, Variable
from . import gameshader as gs
from . import gameshadow, nativegpu, nativelayout, terraindraw

ENV = "ANNO_NATIVE_TERRAIN"  # "0": the terrain by the current path (the rewrite), to hold the two against each other
EFFECT = "terrain"
# terrain.fx's techniques as the view draws them: per stage the entry and the literal arguments the technique passes
# (as the cache file stores them); the state: the stage set's
TECHNIQUES = {
    "TerrainDeferred": {"vs": ("vsTerrain", "2true"), "ps": ("psTerrainDeferred", "0falsefalsefloat3(1,1,1)2")},
    "TerrainDeferredDetailed": {"vs": ("vsTerrain", "2false"), "hs": ("hsTerrain", ""), "ds": ("dsTerrain", "2"),
                                "ps": ("psTerrainDeferred", "0truefalsefloat3(1,1,1)2")},
    "TerrainShadow": {"vs": ("vsTerrainShadow", "true"), "ps": ("psTerrainShadow", "")},
    "TerrainShadowDetailed": {"vs": ("vsTerrainShadow", "false"), "hs": ("hsTerrainShadow", ""),
                              "ds": ("dsTerrainShadow", ""), "ps": ("psTerrainShadow", "")},
}  # fmt: skip
CAMERAS = gs.CAMERAS  # cCameraBuffer's elements: the main, reflection and jittered cameras (all one here)
SHADER_READ = 0x40 | 0x80  # D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE | PIXEL_SHADER_RESOURCE
UNORDERED = 0x8  # D3D12_RESOURCE_STATE_UNORDERED_ACCESS: a storage buffer after QRhi's compute pass wrote it
CONSTANT_SLOT = 256  # a constant buffer's bytes are rounded up to this (Direct3D 12's constant buffer alignment)
KEEP_FRAMES = 4  # frames a wrapped resource or a node's data outlives its last use (QRhi's frames in flight) [ours]
F = QRhiTexture.Format
# QRhi's texture formats by NVRHI's names (sRGB: the RGBA8 one with ``QRhiTexture.Flag.sRGB``)
FORMATS = {F.RGBA8: "RGBA8_UNORM", F.BGRA8: "BGRA8_UNORM", F.R8: "R8_UNORM", F.RG8: "RG8_UNORM", F.R16: "R16_UNORM",
           F.RG16: "RG16_UNORM", F.RGBA16F: "RGBA16_FLOAT", F.RGBA32F: "RGBA32_FLOAT", F.R16F: "R16_FLOAT",
           F.R32F: "R32_FLOAT", F.RGB10A2: "R10G10B10A2_UNORM", F.R32UI: "R32_UINT", F.RG32UI: "RG32_UINT",
           F.RGBA32UI: "RGBA32_UINT", F.BC1: "BC1_UNORM", F.BC3: "BC3_UNORM", F.BC4: "BC4_UNORM", F.BC5: "BC5_UNORM",
           F.BC7: "BC7_UNORM", F.D32F: "D32", F.D32FS8: "D32S8"}  # fmt: skip
GBUFFER_FORMATS = ["R10G10B10A2_UNORM", "SRGBA8_UNORM", "RGBA8_UNORM", "RGBA16_FLOAT"]  # gameframe.GBUFFER
# a declared texture kind -> the module's texture kind of its zeroed stand-in (a plain 2D one stands in as white)
DIMENSIONS = {"Texture3D": "3d", "Texture2DArray": "2darray", "TextureCube": "cube", "TextureCubeArray": "cubearray"}
COPY_SOURCE = 0x800  # D3D12_RESOURCE_STATE_COPY_SOURCE: a buffer QRhi read back this frame


def enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.available()


def shaders(natives) -> dict:
    """Every shader of ``TECHNIQUES`` read from the shipped variant and reflected (in the background: the island's
    loader), by technique; a missing one raises (the terrain then draws by the current path)."""
    v = natives.variant(EFFECT)
    out = {}
    for name, stages in TECHNIQUES.items():
        out[name] = {k: natives.call(v, *spec) for k, spec in stages.items()}
        for s in out[name].values():
            natives.reflection(s)
    return out


@dataclass
class FrameDraws:
    """One frame's native terrain as ``gameframe.Passes.native`` takes it: the nodes, their casters, the ground and
    the current path's tessellated program (which tells a node's technique). A draw that raises turns the native
    terrain off (``NativeTerrain.error``): the next frame draws it by the current path."""

    terrain: NativeTerrain
    models: list
    caster_list: list
    ground: object
    detailed: object

    @property
    def casters(self) -> bool:
        return bool(self.caster_list)

    def gbuffer(self, cb, camera, size) -> None:
        self._guarded(self.terrain.gbuffer, cb, self.models, camera, self.ground, size, self.detailed)

    def shadow(self, cb, camera, cascades, info, side: int) -> None:
        if self.caster_list:
            self._guarded(self.terrain.shadow, cb, self.caster_list, camera, cascades, info, self.ground, (side, side),
                          self.detailed)  # fmt: skip

    def _guarded(self, draw, *args) -> None:
        if self.terrain.error:
            return
        try:
            draw(*args)
        except Exception as exc:  # said once by the view's look; the current path draws from the next frame
            self.terrain.error = f"{type(exc).__name__}: {exc}"


def qrhi_object(obj):
    """The QRhi object behind one of the view's resources (an ``rhidevice`` wrapper's ``native``)."""
    return obj if isinstance(obj, (QRhiTexture, QRhiBuffer)) else getattr(obj, "native", obj)


class Bridge:
    """QRhi's textures and buffers as the module's, wrapped once per resource and let go some frames after their
    last use; ``reads`` are the transitions a pass needs into shader reads (and back: QRhi tracks their state)."""

    def __init__(self, device) -> None:
        self.device, self.module = device, nativegpu.load()
        self.held: dict[tuple, list] = {}  # (id, resource) -> [wrapped, last frame]
        self.frame = 0
        self.reads: dict[int, int] = {}  # this pass's QRhi resources read -> their state now

    def texture(self, obj):
        """(wrapped texture, its resource) of a QRhi texture, read in this pass."""
        import shiboken6

        tex = qrhi_object(obj)
        resource, state = self.module.qrhi_texture(int(shiboken6.getCppPointer(tex)[0]))
        self.reads[resource] = state
        key = (id(tex), resource)
        held = self.held.get(key)
        if held is None:
            fmt = FORMATS.get(tex.format())
            if fmt is None:
                raise RuntimeError(f"no native format for QRhi's {tex.format()}")
            if fmt == "RGBA8_UNORM" and tex.flags() & QRhiTexture.Flag.sRGB:
                fmt = "SRGBA8_UNORM"
            kind = "cube" if tex.flags() & QRhiTexture.Flag.CubeMap else "2darray" if tex.arraySize() else ""
            held = self.held[key] = [self.device.wrap_texture(resource, fmt, kind, ""), 0]
        held[1] = self.frame
        return held[0], resource

    def buffer(self, obj, stride: int, state: int = UNORDERED):
        """(wrapped buffer, its resource) of a QRhi storage buffer, in ``state`` when the pass reads it."""
        import shiboken6

        buf = qrhi_object(obj)
        resource = self.module.qrhi_buffer(int(shiboken6.getCppPointer(buf)[0]))
        self.reads[resource] = state
        key = (id(buf), resource)
        held = self.held.get(key)
        if held is None:
            held = self.held[key] = [self.device.wrap_buffer(resource, stride, "", ""), 0]
        held[1] = self.frame
        return held[0], resource

    def before(self) -> list:
        return [(r, s, SHADER_READ) for r, s in self.reads.items() if s != SHADER_READ]

    def after(self) -> list:
        return [(r, SHADER_READ, s) for r, s in self.reads.items() if s != SHADER_READ]

    def end_frame(self) -> None:
        self.frame += 1
        for key in [k for k, (_w, last) in self.held.items() if self.frame - last > KEEP_FRAMES]:
            self.held.pop(key)[0].release()

    def release(self) -> None:
        for wrapped, _last in self.held.values():
            wrapped.release()
        self.held = {}


@dataclass
class Technique:
    """One of ``TECHNIQUES`` on the device: its shaders, binding plan, layouts, samplers and pipeline."""

    name: str
    plan: nativelayout.BindingPlan
    layouts: list
    pipeline: object
    cbuffers: dict  # name -> the reflection's ConstantBuffer
    tessellated: bool = False


def technique(device, natives, name: str, state: dict) -> Technique:
    """A technique of ``TECHNIQUES`` from the shipped variant (the effect's first: its constants are the engine's)."""
    v = natives.variant(EFFECT)
    stages = {k: natives.call(v, *spec) for k, spec in TECHNIQUES[name].items()}
    reflections = {k: natives.reflection(s) for k, s in stages.items()}
    plan = nativelayout.BindingPlan.of(list(reflections.values()))
    layouts = nativegpu.layouts(device, plan)
    shaders = {k: device.shader(k, s.bytecode, s.name) for k, s in stages.items()}
    elements, _fill = nativelayout.input_elements(terraindraw.ELEMENTS, nativelayout.signature(stages["vs"].bytecode),
                                                  terraindraw.STRIDE)  # fmt: skip
    tessellated = "hs" in stages
    state = {**state, "topology": "patch4" if tessellated else "triangles"}
    pipeline = device.graphics_pipeline(shaders["vs"], shaders.get("ps"), device.input_layout(elements), layouts,
                                        state, hs=shaders.get("hs"), ds=shaders.get("ds"))  # fmt: skip
    cbuffers = {cb.name: cb for r in reflections.values() for cb in r.cbuffers}
    return Technique(name, plan, layouts, pipeline, cbuffers, tessellated)


def camera_buffer(element: ConstantBuffer) -> ConstantBuffer:
    """``cCameraBuffer`` (a structured buffer of ``CAMERAS`` camera records) as a buffer of one array, so the frame's
    camera is written by the paths ``gameshader.fill_frame`` writes (``cCameraBuffer[i].MtxView``)."""
    one = element.variables[0]
    array = Variable("cCameraBuffer", 0, element.size * CAMERAS, one.type, elements=CAMERAS, members=one.members)
    return ConstantBuffer("cCameraBuffer", element.size * CAMERAS, [array])


@dataclass
class NodeData:
    """A node's (or caster's) vertices, indices and ``cbTerrainNode`` on the device, and its sets by technique."""

    vertices: object
    indices: object
    count: int
    node: object
    sets: dict = field(default_factory=dict)  # technique -> (key, set of the node's space)
    last: int = 0

    def release(self) -> None:
        for r in (self.vertices, self.indices, self.node, *(s for _k, s in self.sets.values())):
            r.release()


class NativeTerrain:
    """The terrain's draws on the native backend (module docstring)."""

    def __init__(self, device, natives, stencil: bool = True) -> None:
        self.device, self.natives = device, natives
        self.module = nativegpu.load()
        self.bridge = Bridge(device)
        depth = "D32S8" if stencil else "D32"
        gbuffer = {"cull": "none", "front_ccw": True, "colors": GBUFFER_FORMATS, "depth": depth}
        shadow = {"cull": "none", "front_ccw": True, "colors": [], "depth": depth}
        self.techniques = {name: technique(device, natives, name, shadow if "Shadow" in name else gbuffer)
                           for name in TECHNIQUES}  # fmt: skip
        self.list = device.command_list()
        self.list.open()
        self.nodes: dict[int, tuple] = {}  # id(model) -> (model, NodeData)
        self.buffers: dict[str, object] = {}  # the frame's constant and structured buffers by name
        self.sent: dict[str, bytes] = {}
        self.sets: dict[tuple, tuple] = {}  # (technique, space) -> (key, set)
        self.white = device.texture(4, 4, "RGBA8_UNORM", name="white")
        self.stand_ins: dict = {}  # zeros by kind (``_zeros``)
        self.list.write_texture(self.white, bytes([255]) * 64, 16)
        self.samplers = {name: nativegpu.samplers(device, t.plan) for name, t in self.techniques.items()}
        cull = np.arange(gameshadow.COUNT, dtype=np.uint32)  # instance i casts into cascade i [ours: no culling]
        self.cull = self._static("cTerrainShadowCullResultBuffer", cull.tobytes(), "structured", 4)
        self.frame = 0
        self.error = ""
        # with the debug layer: its messages while the terrain records (``messages``) and those before (``foreign``:
        # the current path's passes on the same device)
        self.messages: list[str] = []
        self.foreign: list[str] = []

    def _static(self, name: str, data: bytes, usage: str, stride: int = 0):
        buf = self.device.buffer(max(len(data), 16), usage, stride=stride, name=name)
        self.list.write_buffer(buf, data)
        return buf

    def _constant(self, name: str, data: bytes, usage: str = "constant", stride: int = 0):
        """The frame's buffer ``name`` holding ``data`` (written on the module's list when it changed)."""
        buf = self.buffers.get(name)
        size = -(-max(len(data), 16) // CONSTANT_SLOT) * CONSTANT_SLOT
        if buf is None or buf.size < size:
            if buf is not None:
                buf.release()
            buf = self.buffers[name] = self.device.buffer(size, usage, stride=stride, name=name)
            self.sent.pop(name, None)
        if self.sent.get(name) != data:
            self.list.write_buffer(buf, data)
            self.sent[name] = data
        return buf

    def _values(self, cb: ConstantBuffer, camera, writes) -> bytes:
        c = gs.Constants(cb)
        gs.fill_frame(c, camera)
        for write in writes:
            write(c)
        return bytes(c.data)

    def _node(self, model) -> NodeData:
        """The device's copy of a node's mesh and constants, made on first use."""
        held = self.nodes.get(id(model))
        if held is not None and held[0] is model:
            held[1].last = self.frame
            return held[1]
        mesh = model.mesh
        indices = np.ascontiguousarray(mesh.indices, "<u4").tobytes()
        cb = gs.Constants(self.techniques["TerrainDeferred"].cbuffers[terraindraw.NODE_BUFFER])
        for name, value in model.node_values.items():
            cb.set(name, value)  # the island's values go to the frame's buffers: only the node's land here
        data = NodeData(self._static("vertices", mesh.data, "vertex"), self._static("indices", indices, "index"),
                        len(mesh.indices), self._static("cbTerrainNode", bytes(cb.data), "constant"))  # fmt: skip
        data.last = self.frame
        self.nodes[id(model)] = (model, data)
        return data

    def _set(self, tech: Technique, index: int, named: dict):
        """The set of a technique's layout ``index`` for ``named`` (shader name -> the module's resource), made when
        what it binds changed."""
        spec = tech.plan.layouts[index]
        key = tuple(id(named.get(i.name)) for i in spec.items)
        return key, (lambda: nativegpu.binding_set(self.device, tech.layouts[index], spec, named,
                                                   self.samplers[tech.name]))  # fmt: skip

    def _textures(self, names: dict, spec, declared: dict) -> dict:
        """The module's textures for a layout's items: QRhi's wrapped, white for a plain map none hands and zeros of
        another declared kind, as the current path's ``GameModel._texture`` stands in (every item bound: NVRHI's
        validation refuses a set that leaves one out)."""
        out = {}
        for item in spec.items:
            if item.kind != "texture":
                continue
            found, kind = names.get(item.name), declared.get(item.name, "Texture2D")
            if found is not None:
                out[item.name] = self.bridge.texture(found)[0]
            elif kind in ("Texture2D", "Texture2D<float4>"):
                out[item.name] = self.white
            else:
                out[item.name] = self._zeros(DIMENSIONS.get(kind.split("<")[0], "2d"))
        return out

    def _zeros(self, kind: str):
        """A zeroed 4 x 4 texture of a kind (``DIMENSIONS``' values), made once."""
        found = self.stand_ins.get(kind)
        if found is None:
            layers = 6 if kind.startswith("cube") else 4 if kind == "3d" else 1
            found = self.stand_ins[kind] = self.device.texture(4, 4, "RGBA8_UNORM", depth=layers, kind=kind,
                                                               name=f"zeros {kind}")  # fmt: skip
            for layer in range(1 if kind == "3d" else layers):
                self.list.write_texture(found, bytes(64 * (layers if kind == "3d" else 1)), 16, layer=layer,
                                        depth_pitch=64)  # fmt: skip
        return found

    def _shared_set(self, tech: Technique, index: int, named: dict):
        key, make = self._set(tech, index, named)
        held = self.sets.get((tech.name, index))
        if held is None or held[0] != key:
            if held is not None:
                held[1].release()
            held = self.sets[(tech.name, index)] = (key, make())
        return held[1]

    def _bindings(self, tech: Technique, model, frame: dict, maps: dict, data: NodeData, made: dict) -> list:
        """The sets of a draw in its pipeline's layout order: the frame's spaces shared (made once a pass: ``made``),
        the node's own."""
        out = []
        p = model.program  # the current path's: its shaders' declared texture kinds (the stand-ins)
        declared = {k: v for s in (p.vs, p.hs, p.ds, p.ps) if s is not None for k, v in s.types.items()}
        for index, spec in enumerate(tech.plan.layouts):
            mine = spec.space == 2
            if not mine and (tech.name, index) in made:
                out.append(made[(tech.name, index)])
                continue
            # the ground's maps last, as the current path's ``TerrainModel.prepare``: its grit over the stand-in
            named = {**frame, **self._textures({**model.textures, **model.own, **maps}, spec, declared)}
            if mine:
                named[terraindraw.NODE_BUFFER] = data.node
                key, make = self._set(tech, index, named)
                held = data.sets.get(tech.name)
                if held is None or held[0] != key:
                    if held is not None:
                        held[1].release()
                    held = data.sets[tech.name] = (key, make())
                out.append(held[1])
            else:
                out.append(made.setdefault((tech.name, index), self._shared_set(tech, index, named)))
        return out

    def _record(self, cb, draws: list, size: tuple[int, int]) -> None:
        """The draws on QRhi's list (inside ``beginExternal``), the QRhi resources they read moved into shader reads
        and back; the module's list (the uploads, the frame's constants) goes to the queue first."""
        self.list.execute()
        list_ptr = nativegpu.command_list(cb)
        if self.device.debug:  # the debug layer checks bindings as they are recorded: what comes now is ours
            self.foreign += self.device.messages()
        self.module.transitions(list_ptr, self.bridge.before())
        viewport = (0.0, 0.0, float(size[0]), float(size[1]))
        for tech, sets, data, instances, push in draws:
            self.module.record_external(self.device, list_ptr, tech.pipeline, sets, data.count, viewport,
                                        [data.vertices], data.indices, True, instances, 0, push)  # fmt: skip
        self.module.transitions(list_ptr, self.bridge.after())
        self.bridge.reads = {}
        if self.device.debug:
            self.messages += self.device.messages()

    def _frame_buffers(self, tech: Technique, camera, writes, prefix: str) -> dict:
        """The frame's constant buffers a technique reads, by name (``prefix``: the pass's own copy)."""
        out = {}
        for name, cb in tech.cbuffers.items():
            if name in (
                terraindraw.NODE_BUFFER,
                "cCameraBuffer",
                "cShadowInformation",
                "cTerrainShadowCullResultBuffer",
            ):
                continue
            if tech.plan.push is not None and name == tech.plan.push.name:
                continue
            out[name] = self._constant(f"{prefix}{name}", self._values(cb, camera, writes))
        cams = tech.cbuffers.get("cCameraBuffer")
        if cams is not None:
            raw = self._values(camera_buffer(cams), camera, ())
            out["cCameraBuffer"] = self._constant("cCameraBuffer", raw, "structured", cams.size)
        return out

    def gbuffer(self, cb, models: list, camera, ground, size: tuple[int, int], detailed) -> None:
        """The nodes into the G-buffer QRhi has bound (``detailed``: the current path's tessellated program, which
        tells a model's technique); ``ground``: ``gameframe.Ground`` (its maps and frame values)."""
        maps = dict(ground.maps) if ground is not None else {}
        writes = self._writes(models, ground)
        draws, frames, made = [], {}, {}
        for m in models:
            name = "TerrainDeferredDetailed" if m.program is detailed and detailed is not None else "TerrainDeferred"
            tech = self.techniques[name]
            data = self._node(m)
            if name not in frames:
                frames[name] = self._frame_buffers(tech, camera, writes, "gbuffer:")
            draws.append((tech, self._bindings(tech, m, frames[name], maps, data, made), data, 1, b""))
        self._record(cb, draws, size)

    def shadow(self, cb, casters: list, camera, cascades, info, ground, size: tuple[int, int], detailed) -> None:
        """The casters into the cascades of the shadow map QRhi has bound, four instances each (one per cascade);
        ``info``: (QRhi's ``cShadowInformation``, whether QRhi read it back this frame): written by the game's analysis
        on the GPU, else by the CPU's fit, written here."""
        maps = dict(ground.maps) if ground is not None else {}
        light = [*cascades.light, 0.0]
        writes = [*self._writes(casters, ground), lambda c: gameshadow.fill(c, cascades),
                  lambda c: c.set("cLgtDirection", light)]  # fmt: skip
        buffer, read = info
        if buffer is not None and cascades.gpu:  # the analysis's last pass left it, or the read-back's copy
            state = COPY_SOURCE if read else UNORDERED
            information = self.bridge.buffer(buffer, gameshadow.INFO_BYTES, state)[0]
        else:
            information = self._constant("cShadowInformation", gameshadow.info_bytes(cascades), "structured",
                                          gameshadow.INFO_BYTES)  # fmt: skip
        draws, frames, made = [], {}, {}
        for m in casters:
            node = m.share if m.share is not None else m
            name = "TerrainShadowDetailed" if node.program is detailed and detailed is not None else "TerrainShadow"
            tech = self.techniques[name]
            data = self._node(node)
            if name not in frames:
                frames[name] = {**self._frame_buffers(tech, camera, writes, "shadow:"),
                                "cShadowInformation": information, "cTerrainShadowCullResultBuffer": self.cull}  # fmt: skip
            push = bytes(tech.plan.push.count) if tech.plan.push is not None else b""
            sets = self._bindings(tech, node, frames[name], maps, data, made)
            draws.append((tech, sets, data, gameshadow.COUNT, push))
        self._record(cb, draws, size)

    @staticmethod
    def _writes(models: list, ground) -> list:
        """What every frame buffer gets besides the camera: the island's values (the same for every node) and the
        ground's frame values."""
        island = (
            {k: v for k, v in models[0].node_values.items() if k in terraindraw.island_values(1.0)} if models else {}
        )
        fill = ground.fill() if ground is not None else None

        def values(c) -> None:
            for name, value in island.items():
                c.set(name, value)

        return [values, fill] if fill is not None else [values]

    def end_frame(self) -> None:
        """One frame done: the nodes not drawn for a few frames let go, the wrapped QRhi resources likewise."""
        self.frame += 1
        for key in [k for k, (_m, d) in self.nodes.items() if self.frame - d.last > KEEP_FRAMES]:
            self.nodes.pop(key)[1].release()
        self.bridge.end_frame()
        self.device.end_frame()

    def release(self) -> None:
        for _m, d in self.nodes.values():
            d.release()
        for _k, s in self.sets.values():
            s.release()
        for b in (*self.buffers.values(), *self.stand_ins.values(), self.white, self.cull):
            b.release()
        self.bridge.release()
        self.nodes, self.sets, self.buffers = {}, {}, {}
