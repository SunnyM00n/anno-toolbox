"""The terrain's nodes and their shadow drawn by the game's shipped shaders (``terrain.fx`` as the install's cache holds
it, bound as it is) through the native backend, recorded on QRhi's command list inside the G-buffer's and the shadow
map's passes: the same nodes, meshes and inputs as ``terraindraw``'s, without the rewrite and its compile.

``CNativeTerrain(device, natives)`` is the entry, one per view: ``gbuffer(command_buffer, models, camera, ground)`` and
``shadow(command_buffer, casters, camera, cascades, info, ground)`` record the draws (between ``beginExternal`` and
``endExternal``; ``nativepass.draw_pass``'s ``external``). What they read of QRhi's (the pages, the baked arrays, the
ground's maps, the cascades) is QRhi's own resources, wrapped (``CBridge``). docs/rendering/native-backend.md section 9.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtGui import QRhiBuffer, QRhiTexture

from ..infra.dxc import SConstantBuffer
from . import gameshader, gameshadow, nativecompute, nativegpu, nativelayout, terraindraw

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
CAMERAS = gameshader.CAMERAS  # cCameraBuffer's elements: the main, reflection and jittered cameras (all one here)
SHADER_READ = 0x40 | 0x80  # D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE | PIXEL_SHADER_RESOURCE
UNORDERED = 0x8  # D3D12_RESOURCE_STATE_UNORDERED_ACCESS: a storage buffer after QRhi's compute pass wrote it
CONSTANT_SLOT = 256  # a constant buffer's bytes are rounded up to this (Direct3D 12's constant buffer alignment)
KEEP_FRAMES = 4  # frames a wrapped resource or a node's data outlives its last use (QRhi's frames in flight) [ours]
Format = QRhiTexture.Format
# QRhi's texture formats by NVRHI's names (sRGB: the RGBA8 one with ``QRhiTexture.Flag.sRGB``)
FORMATS = {Format.RGBA8: "RGBA8_UNORM", Format.BGRA8: "BGRA8_UNORM", Format.R8: "R8_UNORM", Format.RG8: "RG8_UNORM", Format.R16: "R16_UNORM",
           Format.RG16: "RG16_UNORM", Format.RGBA16F: "RGBA16_FLOAT", Format.RGBA32F: "RGBA32_FLOAT", Format.R16F: "R16_FLOAT",
           Format.R32F: "R32_FLOAT", Format.RGB10A2: "R10G10B10A2_UNORM", Format.R32UI: "R32_UINT", Format.RG32UI: "RG32_UINT",
           Format.RGBA32UI: "RGBA32_UINT", Format.BC1: "BC1_UNORM", Format.BC3: "BC3_UNORM", Format.BC4: "BC4_UNORM", Format.BC5: "BC5_UNORM",
           Format.BC6H: "BC6H_UFLOAT", Format.BC7: "BC7_UNORM", Format.D32F: "D32", Format.D32FS8: "D32S8"}  # fmt: skip
GBUFFER_FORMATS = ["R10G10B10A2_UNORM", "SRGBA8_UNORM", "RGBA8_UNORM", "RGBA16_FLOAT"]  # gameframe.GBUFFER
LIT_FORMATS = ["RGBA16_FLOAT", "R8_UNORM"]  # the transparent pass's targets: gameframe's lit colour and alpha
# a declared texture kind -> the module's texture kind of its zeroed stand-in (a plain 2D one stands in as white)
DIMENSIONS = {"Texture3D": "3d", "Texture2DArray": "2darray", "TextureCube": "cube", "TextureCubeArray": "cubearray"}
COPY_SOURCE = 0x800  # D3D12_RESOURCE_STATE_COPY_SOURCE: a buffer QRhi read back this frame


def is_enabled() -> bool:
    """Whether the terrain draws on the native backend: not switched off by ``ENV``, and the module loads."""
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """Every shader of ``TECHNIQUES`` read from the shipped variant and reflected (in the background: the island's
    loader), by technique; a missing one raises (the terrain then draws by the current path)."""
    variant = natives.variant(EFFECT)
    out = {}
    for name, stages in TECHNIQUES.items():
        out[name] = {stage: natives.call(variant, *spec) for stage, spec in stages.items()}
        for shader in out[name].values():
            natives.reflection(shader)
    return out


@dataclass
class SFrameDraws:
    """One frame's native terrain as ``gameframe.SPasses.native`` takes it: the nodes, their casters, the ground and
    the current path's tessellated program (which tells a node's technique). A draw that raises turns the native
    terrain off (``CNativeTerrain.error``): the next frame draws it by the current path."""

    terrain: CNativeTerrain
    models: list
    caster_list: list
    ground: object
    detailed: object

    @property
    def casters(self) -> bool:
        return bool(self.caster_list)

    def gbuffer(self, command_buffer, camera, size_px) -> None:
        """Record the nodes into the G-buffer."""
        nativecompute.guarded(
            self.terrain, self.terrain.gbuffer, command_buffer, self.models, camera, self.ground, size_px, self.detailed
        )

    def shadow(self, command_buffer, camera, cascades, shadow_information, side_px: int) -> None:
        if self.caster_list:
            nativecompute.guarded(
                self.terrain,
                self.terrain.shadow,
                command_buffer,
                self.caster_list,
                camera,
                cascades,
                shadow_information,
                self.ground,
                (side_px, side_px),
                self.detailed,
            )


def qrhi_object(resource):
    """The QRhi object behind one of the view's resources (an ``rhidevice`` wrapper's ``native``)."""
    return resource if isinstance(resource, (QRhiTexture, QRhiBuffer)) else getattr(resource, "native", resource)


class CBridge:
    """QRhi's textures and buffers as the module's, wrapped once per resource and let go some frames after their
    last use; ``reads`` are the transitions a pass needs into shader reads (and back: QRhi tracks their state)."""

    def __init__(self, device) -> None:
        self.m_device, self.m_module = device, nativegpu.load()
        self.m_held: dict[tuple, list] = {}  # (id, resource) -> [wrapped, last frame]
        self.m_frame = 0
        self.m_reads: dict[int, int] = {}  # this pass's QRhi resources read -> their state now
        self.m_writes: dict[int, int] = {}  # and written (``target``; its user clears them with ``reads``)

    def texture(self, qrhi_resource):
        """(wrapped texture, its resource) of a QRhi texture, read in this pass."""
        import shiboken6

        qrhi_texture = qrhi_object(qrhi_resource)
        resource, state = self.m_module.qrhi_texture(int(shiboken6.getCppPointer(qrhi_texture)[0]))
        self.m_reads[resource] = state
        key = (id(qrhi_texture), resource)
        held = self.m_held.get(key)
        if held is None:
            native_format = FORMATS.get(qrhi_texture.format())
            if native_format is None:
                raise RuntimeError(f"no native format for QRhi's {qrhi_texture.format()}")
            if native_format == "RGBA8_UNORM" and qrhi_texture.flags() & QRhiTexture.Flag.sRGB:
                native_format = "SRGBA8_UNORM"
            kind = (
                "cube"
                if qrhi_texture.flags() & QRhiTexture.Flag.CubeMap
                else "2darray"
                if qrhi_texture.arraySize()
                else ""
            )
            held = self.m_held[key] = [self.m_device.wrap_texture(resource, native_format, kind, ""), 0]
        held[1] = self.m_frame
        return held[0], resource

    def buffer(self, qrhi_resource, stride: int, state: int = UNORDERED, usage: str = ""):
        """(wrapped buffer, its resource) of a QRhi storage buffer, in ``state`` when the pass reads it (``usage``:
        "raw" for a raw view)."""
        import shiboken6

        storage_buffer = qrhi_object(qrhi_resource)
        resource = self.m_module.qrhi_buffer(int(shiboken6.getCppPointer(storage_buffer)[0]))
        self.m_reads[resource] = state
        key = (id(storage_buffer), resource, usage)
        held = self.m_held.get(key)
        if held is None:
            held = self.m_held[key] = [self.m_device.wrap_buffer(resource, stride, usage, ""), 0]
        held[1] = self.m_frame
        return held[0], resource

    def target(self, qrhi_resource):
        """(wrapped texture, its resource) of a QRhi storage texture the pass writes: in unordered access then."""
        wrapped, resource = self.texture(qrhi_resource)
        self.m_writes[resource] = self.m_reads.pop(resource)
        return wrapped, resource

    def before(self) -> list:
        writes = [(resource, state, UNORDERED) for resource, state in self.m_writes.items() if state != UNORDERED]
        return [
            (resource, state, SHADER_READ) for resource, state in self.m_reads.items() if state != SHADER_READ
        ] + writes

    def after(self) -> list:
        writes = [(resource, UNORDERED, state) for resource, state in self.m_writes.items() if state != UNORDERED]
        return [
            (resource, SHADER_READ, state) for resource, state in self.m_reads.items() if state != SHADER_READ
        ] + writes

    def send(self, module_list, command_buffer, record, sent=None) -> None:
        """The module's list ``lst`` to the queue (then ``sent()``), and ``record(list)`` on QRhi's (inside
        ``beginExternal``) between the moves of the QRhi resources this pass reads and writes and back to the
        states QRhi tracks: back also when ``record`` raises (QRhi records the rest of its frame from them)."""
        module_list.execute()
        if sent is not None:
            sent()
        qrhi_list = nativegpu.command_list(command_buffer)
        self.m_module.transitions(qrhi_list, self.before())
        try:
            record(qrhi_list)
        finally:
            self.m_module.transitions(qrhi_list, self.after())
            self.m_reads, self.m_writes = {}, {}

    def end_frame(self) -> None:
        self.m_frame += 1
        for key in [held_key for held_key, (_w, last) in self.m_held.items() if self.m_frame - last > KEEP_FRAMES]:
            self.m_held.pop(key)[0].release()

    def release(self) -> None:
        for wrapped, _last in self.m_held.values():
            wrapped.release()
        self.m_held = {}


@dataclass
class STechnique:
    """One of ``TECHNIQUES`` on the device: its shaders, binding plan, layouts, samplers and pipeline."""

    name: str
    plan: nativelayout.SBindingPlan
    layouts: list
    pipeline: object
    cbuffers: dict  # name -> the reflection's ConstantBuffer
    tessellated: bool = False


def technique(device, natives, name: str, state: dict) -> STechnique:
    """A technique of ``TECHNIQUES`` from the shipped variant (the effect's first: its constants are the engine's)."""
    variant = natives.variant(EFFECT)
    stages = {stage: natives.call(variant, *spec) for stage, spec in TECHNIQUES[name].items()}
    reflections = {stage: natives.reflection(shader_stage) for stage, shader_stage in stages.items()}
    plan = nativelayout.SBindingPlan.of(list(reflections.values()))
    layouts = nativegpu.layouts(device, plan)
    shaders = {
        stage: device.shader(stage, shader_stage.bytecode, shader_stage.name) for stage, shader_stage in stages.items()
    }
    elements, _fill = nativelayout.input_elements(terraindraw.ELEMENTS, nativelayout.signature(stages["vs"].bytecode),
                                                  terraindraw.STRIDE)  # fmt: skip
    tessellated = "hs" in stages
    state = {**state, "topology": "patch4" if tessellated else "triangles"}
    pipeline = device.graphics_pipeline(shaders["vs"], shaders.get("ps"), device.input_layout(elements), layouts,
                                        state, hs=shaders.get("hs"), ds=shaders.get("ds"))  # fmt: skip
    cbuffers = {
        constant_buffer.name: constant_buffer
        for reflection in reflections.values()
        for constant_buffer in reflection.cbuffers
    }
    return STechnique(name, plan, layouts, pipeline, cbuffers, tessellated)


def camera_buffer(element: SConstantBuffer) -> SConstantBuffer:
    """``cCameraBuffer`` (a structured buffer of ``CAMERAS`` camera records) as a buffer of one array, so the frame's
    camera is written by the paths ``gameshader.fill_frame`` writes (``cCameraBuffer[i].MtxView``)."""
    return nativecompute.records(element, CAMERAS)


@dataclass
class SNodeData:
    """A node's (or caster's) vertices, indices and ``cbTerrainNode`` on the device, and its sets by technique."""

    vertices: object
    indices: object
    count: int
    node: object
    sets: dict = field(default_factory=dict)  # technique -> (key, set of the node's space)
    last: int = 0

    def release(self) -> None:
        for resource in (
            self.vertices,
            self.indices,
            self.node,
            *(binding_set for _k, binding_set in self.sets.values()),
        ):
            resource.release()


class CNativeTerrain:
    """The terrain's draws on the native backend (module docstring)."""

    def __init__(self, device, natives, has_stencil: bool = True) -> None:
        self.m_device, self.m_natives = device, natives
        self.m_module = nativegpu.load()
        self.m_bridge = CBridge(device)
        depth = "D32S8" if has_stencil else "D32"
        gbuffer = {"cull": "none", "front_ccw": True, "colors": GBUFFER_FORMATS, "depth": depth}
        shadow = {"cull": "none", "front_ccw": True, "colors": [], "depth": depth}
        self.m_techniques = {name: technique(device, natives, name, shadow if "Shadow" in name else gbuffer)
                           for name in TECHNIQUES}  # fmt: skip
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_nodes: dict[int, tuple] = {}  # id(model) -> (model, SNodeData)
        self.m_buffers = nativegpu.CKeptBuffers(device, CONSTANT_SLOT)  # the frame's constant and structured buffers
        self.m_sets: dict[tuple, tuple] = {}  # (technique, space) -> (key, set)
        self.m_white = device.texture(4, 4, "RGBA8_UNORM", name="white")
        self.m_stand_ins: dict = {}  # zeros by kind (``_zeros``)
        self.m_list.write_texture(self.m_white, bytes([255]) * 64, 16)
        self.m_samplers = {name: nativegpu.samplers(device, built.plan) for name, built in self.m_techniques.items()}
        cull = np.arange(gameshadow.COUNT, dtype=np.uint32)  # instance i casts into cascade i [ours: no culling]
        self.m_cull = self._static("cTerrainShadowCullResultBuffer", cull.tobytes(), "structured", 4)
        self.m_frame = 0
        self.m_error = ""
        # with the debug layer: its messages while the terrain records (``messages``) and those before (``foreign``:
        # the current path's passes on the same device)
        self.m_messages: list[str] = []
        self.m_foreign: list[str] = []

    def _static(self, name: str, data: bytes, usage: str, stride: int = 0):
        buffer = self.m_device.buffer(max(len(data), 16), usage, stride=stride, name=name)
        self.m_list.write_buffer(buffer, data)
        return buffer

    def _constant(self, name: str, data: bytes, usage: str = "constant", stride: int = 0):
        """The frame's buffer ``name`` holding ``data`` (written on the module's list when it changed)."""
        return self.m_buffers.get(self.m_list, name, data, usage, stride, name)

    def _values(self, reflection: SConstantBuffer, camera, writes) -> bytes:
        constants = gameshader.CConstants(reflection)
        gameshader.fill_frame(constants, camera)
        for write in writes:
            write(constants)
        return bytes(constants.m_data)

    def _node(self, model) -> SNodeData:
        """The device's copy of a node's mesh and constants, made on first use."""
        held = self.m_nodes.get(id(model))  # pattern: ok (the entry holds the model: its id stays its own)
        if held is not None and held[0] is model:
            held[1].last = self.m_frame
            return held[1]
        mesh = model.m_mesh
        indices = np.ascontiguousarray(mesh.m_indices, "<u4").tobytes()
        node_constants = gameshader.CConstants(self.m_techniques["TerrainDeferred"].cbuffers[terraindraw.NODE_BUFFER])
        for name, value in model.m_node_values.items():
            node_constants.set(name, value)  # the island's values go to the frame's buffers: only the node's land here
        data = SNodeData(self._static("vertices", mesh.m_data, "vertex"), self._static("indices", indices, "index"),
                        len(mesh.m_indices), self._static("cbTerrainNode", bytes(node_constants.m_data), "constant"))  # fmt: skip
        data.last = self.m_frame
        self.m_nodes[id(model)] = (model, data)  # pattern: ok (the model is kept in the entry)
        return data

    def _set(self, tech: STechnique, index: int, named: dict):
        """The set of a technique's layout ``index`` for ``named`` (shader name -> the module's resource), made when
        what it binds changed."""
        spec = tech.plan.layouts[index]
        key = tuple(id(named.get(item.name)) for item in spec.items)
        return key, (lambda: nativegpu.binding_set(self.m_device, tech.layouts[index], spec, named,
                                                   self.m_samplers[tech.name]))  # fmt: skip

    def _textures(self, names: dict, spec, declared: dict) -> dict:
        """The module's textures for a layout's items: QRhi's wrapped, white for a plain map none hands and zeros of
        another declared kind, as the current path's ``CGameModel._texture`` stands in (every item bound: NVRHI's
        validation refuses a set that leaves one out)."""
        out = {}
        for item in spec.items:
            if item.kind != "texture":
                continue
            found, kind = names.get(item.name), declared.get(item.name, "Texture2D")
            if found is not None:
                out[item.name] = self.m_bridge.texture(found)[0]
            elif kind in ("Texture2D", "Texture2D<float4>"):
                out[item.name] = self.m_white
            else:
                out[item.name] = self._zeros(DIMENSIONS.get(kind.split("<")[0], "2d"))
        return out

    def _zeros(self, kind: str):
        """A zeroed 4 x 4 texture of a kind (``DIMENSIONS``' values), made once."""
        found = self.m_stand_ins.get(kind)
        if found is None:
            layers = 6 if kind.startswith("cube") else 4 if kind == "3d" else 1
            found = self.m_stand_ins[kind] = self.m_device.texture(4, 4, "RGBA8_UNORM", depth=layers, kind=kind,
                                                               name=f"zeros {kind}")  # fmt: skip
            for layer in range(1 if kind == "3d" else layers):
                self.m_list.write_texture(found, bytes(64 * (layers if kind == "3d" else 1)), 16, layer=layer,
                                        depth_pitch=64)  # fmt: skip
        return found

    def _shared_set(self, tech: STechnique, index: int, named: dict):
        key, make = self._set(tech, index, named)
        held = self.m_sets.get((tech.name, index))
        if held is None or held[0] != key:
            if held is not None:
                held[1].release()
            held = self.m_sets[(tech.name, index)] = (key, make())
        return held[1]

    def _bindings(self, tech: STechnique, model, frame: dict, maps: dict, data: SNodeData, made: dict) -> list:
        """The sets of a draw in its pipeline's layout order: the frame's spaces shared (made once a pass: ``made``),
        the node's own."""
        out = []
        program = model.m_program  # the current path's: its shaders' declared texture kinds (the stand-ins)
        declared = {
            name: kind
            for stage in (program.m_vertex_stage, program.m_hull_stage, program.m_domain_stage, program.m_pixel_stage)
            if stage is not None
            for name, kind in stage.types.items()
        }
        for index, spec in enumerate(tech.plan.layouts):
            mine = spec.space == 2
            if not mine and (tech.name, index) in made:
                out.append(made[(tech.name, index)])
                continue
            # the ground's maps last, as the current path's ``CTerrainModel.prepare``: its grit over the stand-in
            named = {**frame, **self._textures({**model.m_textures, **model.m_own, **maps}, spec, declared)}
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

    def _record(self, command_buffer, draws: list, size_px: tuple[int, int]) -> None:
        """The draws on QRhi's list (inside ``beginExternal``), the QRhi resources they read moved into shader reads
        and back; the module's list (the uploads, the frame's constants) goes to the queue first."""
        viewport = (0.0, 0.0, float(size_px[0]), float(size_px[1]))

        def sent() -> None:  # the debug layer checks bindings as they are recorded: what comes now is ours
            if self.m_device.debug:
                self.m_foreign += self.m_device.messages()

        def record(list_ptr) -> None:
            for tech, sets, data, instances, push in draws:
                self.m_module.record_external(self.m_device, list_ptr, tech.pipeline, sets, data.count, viewport,
                                            [data.vertices], data.indices, True, instances, 0, push)  # fmt: skip

        self.m_bridge.send(self.m_list, command_buffer, record, sent)
        if self.m_device.debug:
            self.m_messages += self.m_device.messages()

    def _frame_buffers(self, tech: STechnique, camera, writes, prefix: str) -> dict:
        """The frame's constant buffers a technique reads, by name (``prefix``: the pass's own copy)."""
        out = {}
        for name, reflection in tech.cbuffers.items():
            if name in (
                terraindraw.NODE_BUFFER,
                "cCameraBuffer",
                "cShadowInformation",
                "cTerrainShadowCullResultBuffer",
            ):
                continue
            if tech.plan.push is not None and name == tech.plan.push.name:
                continue
            out[name] = self._constant(f"{prefix}{name}", self._values(reflection, camera, writes))
        camera_reflection = tech.cbuffers.get("cCameraBuffer")
        if camera_reflection is not None:
            raw = self._values(camera_buffer(camera_reflection), camera, ())
            out["cCameraBuffer"] = self._constant("cCameraBuffer", raw, "structured", camera_reflection.size)
        return out

    def gbuffer(self, command_buffer, models: list, camera, ground, size_px: tuple[int, int], detailed) -> None:
        """The nodes into the G-buffer QRhi has bound (``detailed``: the current path's tessellated program, which
        tells a model's technique); ``ground``: ``gameframe.SGround`` (its maps and frame values)."""
        maps = dict(ground.maps) if ground is not None else {}
        writes = self._writes(models, ground)
        draws, frames, made = [], {}, {}
        for model in models:
            name = (
                "TerrainDeferredDetailed" if model.m_program is detailed and detailed is not None else "TerrainDeferred"
            )
            tech = self.m_techniques[name]
            data = self._node(model)
            if name not in frames:
                frames[name] = self._frame_buffers(tech, camera, writes, "gbuffer:")
            draws.append((tech, self._bindings(tech, model, frames[name], maps, data, made), data, 1, b""))
        self._record(command_buffer, draws, size_px)

    def shadow(
        self,
        command_buffer,
        casters: list,
        camera,
        cascades,
        shadow_information,
        ground,
        size_px: tuple[int, int],
        detailed,
    ) -> None:
        """The casters into the cascades of the shadow map QRhi has bound, four instances each (one per cascade);
        ``info``: (QRhi's ``cShadowInformation``, whether QRhi read it back this frame): written by the game's analysis
        on the GPU, else by the CPU's fit, written here."""
        maps = dict(ground.maps) if ground is not None else {}
        light = [*cascades.light, 0.0]
        writes = [*self._writes(casters, ground), lambda constants: gameshadow.fill(constants, cascades),
                  lambda constants: constants.set("cLgtDirection", light)]  # fmt: skip
        buffer, read = shadow_information
        if buffer is not None and cascades.gpu:  # the analysis's last pass left it, or the read-back's copy
            state = COPY_SOURCE if read else UNORDERED
            information = self.m_bridge.buffer(buffer, gameshadow.INFO_BYTES, state)[0]
        else:
            information = self._constant("cShadowInformation", gameshadow.info_bytes(cascades), "structured",
                                          gameshadow.INFO_BYTES)  # fmt: skip
        draws, frames, made = [], {}, {}
        for caster in casters:
            node = caster.m_share if caster.m_share is not None else caster
            name = "TerrainShadowDetailed" if node.m_program is detailed and detailed is not None else "TerrainShadow"
            tech = self.m_techniques[name]
            data = self._node(node)
            if name not in frames:
                frames[name] = {**self._frame_buffers(tech, camera, writes, "shadow:"),
                                "cShadowInformation": information, "cTerrainShadowCullResultBuffer": self.m_cull}  # fmt: skip
            push = bytes(tech.plan.push.count) if tech.plan.push is not None else b""
            sets = self._bindings(tech, node, frames[name], maps, data, made)
            draws.append((tech, sets, data, gameshadow.COUNT, push))
        self._record(command_buffer, draws, size_px)

    @staticmethod
    def _writes(models: list, ground) -> list:
        """What every frame buffer gets besides the camera: the island's values (the same for every node) and the
        ground's frame values."""
        island = (
            {
                island_key: island_value
                for island_key, island_value in models[0].m_node_values.items()
                if island_key in terraindraw.island_values(1.0)
            }
            if models
            else {}
        )
        fill = ground.fill() if ground is not None else None

        def values(constants) -> None:
            for name, value in island.items():
                constants.set(name, value)

        return [values, fill] if fill is not None else [values]

    def end_frame(self) -> None:
        """One frame done: the nodes not drawn for a few frames let go, the wrapped QRhi resources likewise (the
        device's own frame is its owner's to end, once for all its native parts)."""
        self.m_frame += 1
        for key in [
            node_key for node_key, (_model, data) in self.m_nodes.items() if self.m_frame - data.last > KEEP_FRAMES
        ]:
            self.m_nodes.pop(key)[1].release()
        self.m_bridge.end_frame()

    def release(self) -> None:
        for _model, data in self.m_nodes.values():
            data.release()
        for _key, binding_set in self.m_sets.values():
            binding_set.release()
        for resource in (*self.m_stand_ins.values(), self.m_white, self.m_cull):
            resource.release()
        self.m_buffers.release()
        self.m_bridge.release()
        self.m_list.close()
        self.m_nodes, self.m_sets = {}, {}
