"""The sea, its coast waves and the rivers and lakes drawn by the game's shipped water shaders (``water_ocean.fx``,
``water_physical_waves.fx``, ``water_inland_water.fx`` as the install's cache holds them, bound as they are) through
the native backend with the objects' transparent parts (``objectsnative``), recorded on QRhi's list where the current
path draws them, from its own draws (``gamesea``, ``gameinland``: meshes, values, states, maps).

``shaders(natives)`` reads them (in the background); ``CNativeWater(device, natives)`` is the device side, one per view:
``coast_waves`` is the coast waves' step (``gamesea.CCoastWaves.native``), ``record`` draws water; ``SFrameWater`` is one
frame's transparent pass as ``gameframe.SPasses.native_water`` takes it. docs/rendering/native-backend.md section 12.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import gameshader as gs
from . import nativegpu, nativelayout
from .nativecompute import CTold, guarded
from .nativematerials import CStandIns
from .objectsnative import WATER_ENV, CNativeError, pass_state
from .terrainnative import CONSTANT_SLOT, LIT_FORMATS, UNORDERED, CBridge, camera_buffer

ENV = WATER_ENV  # "0": the water and the transparent parts by the current path, to hold the two against each other
SEA, INLAND, WAVES, FLOW = "water_ocean", "water_inland_water", "water_physical_waves", "water_cluster_flow"
WAVES_ENTRY = "csPhysicalWaves"
# a water draw's pass shaders (the current path's entries) -> the effect and the technique the engine draws them by
TECHNIQUES = {("vsOcean", "psOcean"): (SEA, "PatchInner"), ("vsRiver", "psRiver"): (INLAND, "RiverDefault"),
              ("vsRiver", "psRiverDepthOnly"): (INLAND, "RiverDefault"), ("vsLake", "psLake"): (INLAND, "LakeDefault"),
              ("vsRiver", "psRiverPreventOcean"): (INLAND, "RiverPreventOcean"),
              ("vsClusterFlow", "psClusterFlow"): (FLOW, "ClusterFlow")}  # fmt: skip
PHYSICAL, EGYPT = (("PHYSICAL_WAVES_ENABLED", ""),), (("EGYPT_INLAND_WATER", ""),)
# the variants the current path compiles (``gamesea.compile_stages``, ``gameinland.compile_stages``), by technique
VARIANTS = {"PatchInner": ((), PHYSICAL), "RiverDefault": ((), EGYPT), "LakeDefault": ((), EGYPT),
            "RiverPreventOcean": (EGYPT,)}  # fmt: skip
KEEP_FRAMES = 4  # frames a mesh's or a draw's buffers outlive their last use (QRhi's frames in flight) [ours]
SETS = 256  # binding sets kept for the resources they were made with [ours]
SKIPPED = ("sampler", "push")


def is_enabled() -> bool:
    """Switched on and a module that records dispatches and stencil states (one built before step 6 has neither)."""
    return os.environ.get(ENV, "") != "0" and nativegpu.on() and hasattr(nativegpu.load(), "record_dispatch")


def pass_shaders(natives, stem: str, switches: tuple, technique: str, entries: tuple) -> tuple:
    """The shipped shaders a technique of an effect's variant draws ``entries`` (vertex, pixel) with, each by the
    literal arguments the technique passes it (``NativeShaders.techniques``). The cache keeps a function once per
    distinct compile and says not which technique's it is (``vsRiver`` three times), so the vertex shader is the
    one whose bindings agree with the pixel shader's: a technique's stages share one table."""
    variant = natives.variant(stem, "", tuple(switches))
    calls = natives.techniques(stem)[technique].calls
    found = []
    for entry in entries:
        arguments = next((call[2] for call in calls if call[1] == entry), None)
        if arguments is None:
            raise CNativeError(f"{stem} {technique}: no {entry}")
        same = [
            shader
            for shader in natives.shaders(variant).values()
            if shader.name == entry and "".join(shader.arguments).replace(" ", "") == arguments
        ]
        found.append(same or [natives.call(variant, entry, arguments)])
    pixel_shader = found[-1][0]
    table = _table(natives.reflection(pixel_shader))
    vertex_shader = next((shader for shader in found[0] if _agrees(_table(natives.reflection(shader)), table)), None)
    if vertex_shader is None:
        raise CNativeError(f"{stem} {technique}: no {entries[0]} that binds as {entries[-1]} does")
    return (vertex_shader, pixel_shader)


def _table(reflection) -> dict:
    return {
        (binding.space, nativelayout.KINDS.get(binding.kind, "texture"), binding.register): binding.name
        for binding in reflection.bindings
    }


def _agrees(first: dict, second: dict) -> bool:
    return all(second.get(where, name) == name for where, name in first.items())


def shaders(natives) -> None:
    """Every water shader of ``TECHNIQUES`` in the variants the current path draws, and the coast waves', read and
    reflected (in the background: the island's loader); a missing one raises."""
    for entries, (stem, technique) in TECHNIQUES.items():
        for switches in VARIANTS.get(technique, ()):  # the flow map's: its own variant, read when it is made
            for shader in pass_shaders(natives, stem, switches, technique, entries):
                natives.reflection(shader)
    natives.reflection(natives.call(natives.variant(WAVES), WAVES_ENTRY))


@dataclass
class STechnique:
    """One technique on the device: its plan, layouts, samplers, shaders, the reflections' bindings and constant
    buffers by name, its fixed state and its pipelines by vertex layout (a compute one under ``"compute"``)."""

    plan: nativelayout.SBindingPlan
    layouts: list
    samplers: dict
    shaders: tuple
    bytecode: bytes
    bindings: dict
    cbuffers: dict
    state: dict = field(default_factory=dict)
    pipelines: dict = field(default_factory=dict)


class CWaterRun:
    """A water draw of the current path's (``gamesea.CSeaModel``, ``gameinland.CInlandModel``) recorded by the backend
    in its place of the transparent pass (``nativepass.draw_models``' ``native``)."""

    m_native = True

    def __init__(self, model) -> None:
        self.m_model = model

    @property
    def m_world(self):
        return self.m_model.m_world

    def box(self):
        return self.m_model.box()


class CNativeWater(CTold):
    """The water's draws and the coast waves' step on the native backend (module docstring)."""

    def __init__(self, device, natives, has_stencil: bool = True) -> None:
        self.m_device, self.m_natives = device, natives
        self.m_module = nativegpu.load()
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_stand_ins = CStandIns(device, self.m_list)
        self.m_depth = "D32S8" if has_stencil else "D32"
        self.m_formats = LIT_FORMATS  # the targets its draws go into (the flow map's raster: its own, no depth)
        self._m_techniques: dict[tuple, STechnique] = {}
        self._m_meshes: dict[int, list] = {}  # id of a mesh -> [mesh, vertices, indices, last frame]
        self._m_buffers = nativegpu.CKeptBuffers(device, CONSTANT_SLOT)  # the draws' constant buffers
        self._m_sets = nativegpu.CSetCache(SETS)
        self._m_coast: STechnique | None = None
        self.m_frame = 0
        self.m_error = ""
        self.m_drawn = 0  # the native water draws of the last frame
        self.m_messages: list[str] = []
        self.m_foreign: list[str] = []

    # what the draws share: techniques, pipelines, meshes, buffers, sets

    def technique(self, model) -> STechnique:
        """A water draw's technique by its pass shaders' entries, its variant's switches and its state."""
        entries = (model.m_program.m_vertex_stage.entry, model.m_program.m_pixel_stage.entry)
        found = TECHNIQUES.get(entries)
        if found is None:
            raise CNativeError(f"no water technique draws {entries}")
        key = (*found, tuple(model.m_switches), entries, model.m_state)
        compiled = self._m_techniques.get(key)
        if compiled is None:
            vertex_shader, pixel_shader = pass_shaders(self.m_natives, found[0], model.m_switches, found[1], entries)
            compiled = self._m_techniques[key] = self._make((vertex_shader, pixel_shader), ("vs", "ps"))
            compiled.state = pass_state(model.m_state, self.m_formats, self.m_depth)
        return compiled

    def _make(self, shaders: tuple, stages: tuple) -> STechnique:
        reflections = [self.m_natives.reflection(shader) for shader in shaders]
        plan = nativelayout.SBindingPlan.of(reflections)
        if any(spec.space == nativelayout.ROOT_SPACE for spec in plan.layouts):
            raise CNativeError("root constants in a water shader: not native yet")
        made = tuple(
            self.m_device.shader(k, shader.bytecode, shader.name) for k, shader in zip(stages, shaders, strict=True)
        )
        return STechnique(plan, nativegpu.layouts(self.m_device, plan), nativegpu.samplers(self.m_device, plan), made,
                         shaders[0].bytecode, {binding.name: binding for reflection in reflections for binding in reflection.bindings},
                         {constant_buffer.name: constant_buffer for reflection in reflections for constant_buffer in reflection.cbuffers})  # fmt: skip

    def pipeline(self, technique: STechnique, mesh) -> tuple:
        """(pipeline, the constant vertex buffer of the inputs the mesh lacks) for a mesh's stored layout."""
        key = (tuple(map(tuple, mesh.m_elements)), mesh.m_stride)
        if key not in technique.pipelines:
            technique.pipelines[key] = nativegpu.mesh_pipeline(self.m_device, self.m_list, technique.shaders, technique.bytecode,
                                                          technique.layouts, technique.state, mesh)  # fmt: skip
        return technique.pipelines[key]

    def _static(self, data: bytes, usage: str, name: str):
        return nativegpu.static_buffer(self.m_device, self.m_list, data, usage, name)

    def mesh(self, mesh) -> list:
        """A mesh's vertex and index buffers on the device, made once (the current path keeps its own copy)."""
        held = self._m_meshes.get(id(mesh))
        if held is None or held[0] is not mesh:
            if held is not None:
                held[1].release()
                held[2].release()
            indices = mesh.m_indices.astype("<u4").tobytes()
            held = self._m_meshes[id(mesh)] = [mesh, self._static(mesh.m_data, "vertex", "water vertices"),
                                             self._static(indices, "index", "water indices"), 0]  # fmt: skip
        held[3] = self.m_frame
        return held

    def _buffer(self, key: tuple, data: bytes, usage: str = "constant", stride: int = 0):
        """The buffer of ``key`` holding ``data`` (written on the module's list when it changed)."""
        return self._m_buffers.get(self.m_list, key, data, usage, stride, str(key[-1]), self.m_frame)

    def _constants(
        self, key, technique: STechnique, camera, fills: list, values: dict | None = None, model=None
    ) -> dict:
        """The constant buffers and the camera records a draw's technique reads, by name: the frame's values, the
        model's place, the pass's ``fills``, then the draw's own ``values`` (as ``CGameModel._write`` writes them)."""
        out = {}
        for name, constant_buffer in technique.cbuffers.items():
            binding = technique.bindings.get(name)
            if binding is None:
                continue
            if binding.kind == "cbuffer":
                constants = gs.CConstants(constant_buffer)
                gs.fill_frame(constants, camera)
                if model is not None:
                    gs.fill_instance(constants, model.m_world, model.m_palette)
                for fill in fills:
                    fill(constants)
                for path, value in (values or {}).items():
                    constants.set(path, value)
                out[name] = self._buffer((key, name), bytes(constants.m_data))
            elif name == "cCameraBuffer":
                raw = gs.CConstants(camera_buffer(constant_buffer))
                gs.fill_frame(raw, camera)
                out[name] = self._buffer(("camera", name), bytes(raw.m_data), "structured", constant_buffer.size)
        return out

    def _resources(self, technique: STechnique, lookup, own: dict) -> dict:
        """Every item a technique's sets bind, by name: ``own`` (its buffers), else ``lookup(name)`` (QRhi's textures
        and storage buffers wrapped, the module's as they are), else a stand-in of the declared kind."""
        out = dict(own)
        for spec in technique.plan.layouts:
            for item in spec.items:
                if item.kind in SKIPPED or item.name in out:
                    continue
                found = lookup(item.name)
                if item.kind == "rwtexture":
                    out[item.name] = self.m_bridge.target(found)[0]
                elif item.kind == "texture":
                    if found is None:
                        out[item.name] = self.m_stand_ins.texture(technique.bindings[item.name])
                    else:
                        out[item.name] = (
                            found if isinstance(found, self.m_module.Texture) else self.m_bridge.texture(found)[0]
                        )
                elif item.kind in ("structured", "raw"):
                    constant_buffer = technique.cbuffers.get(item.name)
                    stride = constant_buffer.size if constant_buffer is not None and item.kind == "structured" else 0
                    if found is not None and not isinstance(found, self.m_module.Buffer):  # QRhi's, after its passes
                        found = self.m_bridge.buffer(found, stride, UNORDERED, "raw" if item.kind == "raw" else "")[0]
                    stand_in = self.m_stand_ins.structured(stride or 16) if stride else self.m_stand_ins.raw(False)
                    out[item.name] = found if found is not None else stand_in
                else:
                    raise CNativeError(f"nothing to bind for {item.name} ({item.kind})")
        return out

    def _bind(self, technique: STechnique, named: dict) -> list:
        """The technique's sets for ``named`` in its pipeline's order, made when what they bind changed."""
        out = []
        for index, spec in enumerate(technique.plan.layouts):
            held = tuple(named.get(i.name) for i in spec.items if i.kind not in SKIPPED)
            layout = technique.layouts[index]
            out.append(self._m_sets.get(layout, held, lambda layout=layout, spec=spec: nativegpu.binding_set(
                self.m_device, layout, spec, named, technique.samplers)))  # fmt: skip
        return out

    # the work

    def record(self, command_buffer, models: list, camera, fills: list, shared: dict, size_px: tuple[int, int]) -> None:
        """``models`` (the current path's water draws) on QRhi's list inside the transparent pass, into the targets it
        has bound: the module's list (the meshes, the draws' constants) goes to the queue first."""
        self._told_before()
        items = []
        for model in models:
            technique = self.technique(model)
            _mesh, vertices, indices, _last = self.mesh(model.m_mesh)
            pipe, constant = self.pipeline(technique, model.m_mesh)
            own = self._constants(id(model), technique, camera, fills, model.m_values, model)
            named = self._resources(technique, lambda name, item_model=model: item_model.lookup(name, shared), own)
            items.append(self.m_module.draw_item(pipe, self._bind(technique, named), len(model.m_mesh.m_indices),
                                               [vertices, constant], indices, True, b""))  # fmt: skip
        self._send(command_buffer, lambda commands: self.m_module.record_draws(self.m_device, commands, items, [1] * len(items),
                                                            [0] * len(items), (0.0, 0.0, *map(float, size_px))))  # fmt: skip
        self.m_drawn += len(items)

    def coast_waves(self, command_buffer, named: dict, fill, camera, groups: tuple[int, int, int]) -> None:
        """One step of the coast waves (``gamesea.CCoastWaves.run``'s inputs and targets by name, ``fill(c)`` its
        constants) on QRhi's list inside a compute pass of its; a step that raises turns the native water off."""
        if self.m_error:
            return
        try:
            technique = self._m_coast
            if technique is None:
                shader = self.m_natives.call(self.m_natives.variant(WAVES), WAVES_ENTRY)
                technique = self._m_coast = self._make((shader,), ("cs",))
                technique.pipelines["compute"] = self.m_device.compute_pipeline(technique.shaders[0], technique.layouts)
            self._told_before()
            own = self._constants("coast waves", technique, camera, [fill])
            sets = self._bind(technique, self._resources(technique, named.get, own))
            pipe = technique.pipelines["compute"]
            self._send(
                command_buffer,
                lambda commands: self.m_module.record_dispatch(self.m_device, commands, pipe, sets, groups),
            )
        except Exception as error:  # said once by the view's look; the current path's from the next frame
            self.m_error = f"{type(error).__name__}: {error}"

    def _send(self, command_buffer, record) -> None:
        """``record(list)`` on QRhi's list after the module's (``CBridge.send``)."""
        self.m_bridge.send(self.m_list, command_buffer, record)
        self._told_after()

    def end_frame(self) -> None:
        """One frame done: meshes and buffers not used for a few frames let go, the wrapped resources likewise."""
        self.m_frame += 1
        for key in [k for k, held in self._m_meshes.items() if self.m_frame - held[3] > KEEP_FRAMES]:
            _mesh, vertices, indices, _last = self._m_meshes.pop(key)
            vertices.release()
            indices.release()
        self._m_buffers.let_go(self.m_frame - KEEP_FRAMES)
        self.m_bridge.end_frame()
        self.m_drawn = 0

    def release(self) -> None:
        for technique in (*self._m_techniques.values(), *([self._m_coast] if self._m_coast is not None else [])):
            for found in technique.pipelines.values():
                for item in found if isinstance(found, tuple) else (found,):
                    item.release()
            for item in (*technique.layouts, *technique.shaders):
                item.release()
        for held in self._m_meshes.values():
            held[1].release()
            held[2].release()
        for part in (self._m_sets, self._m_buffers, self.m_stand_ins, self.m_bridge):
            part.release()
        self.m_list.close()
        self._m_techniques, self._m_meshes, self._m_coast = {}, {}, None


@dataclass
class SFrameWater:
    """One frame's transparent pass as ``gameframe.SPasses.native_water`` takes it: the water draws wrapped to be
    recorded natively (``runs``; none when ``water`` is None or failed), and every run of native draws in the pass's
    order recorded by its owner: the water's here, the transparent parts' by ``objects``
    (``objectsnative.CNativeObjects``). A recording that raises turns its owner off (its ``error``)."""

    water: CNativeWater | None
    objects: object = None
    # the module's resources the native draws bind in place of QRhi's of those names (``shadingnative``'s lights,
    # light buffer and cascades' information: QRhi's are left in copy states where native step 5 runs)
    overlay: dict = field(default_factory=dict)

    def runs(self, models: list) -> list:
        if self.water is None or self.water.m_error:
            return models
        return [CWaterRun(model) for model in models]

    def upload(self, batch, ordered: list) -> None:
        """The water draws' own maps into the batch the pass begins with (their first frame)."""
        for run in ordered:
            if isinstance(run, CWaterRun):
                run.m_model.upload(batch)

    def record(self, command_buffer, run: list, camera, fills: list, shared: dict, size_px: tuple[int, int]) -> None:
        """A run of consecutive native draws (``nativepass.draw_models``), each owner's in turn."""
        shared = {**shared, **self.overlay}
        group: list = []
        for item in [*run, None]:  # None ends the last group
            if group and (item is None or isinstance(item, CWaterRun) != isinstance(group[0], CWaterRun)):
                water = isinstance(group[0], CWaterRun)
                owner = self.water if water else self.objects
                if water:
                    guarded(
                        owner,
                        owner.record,
                        command_buffer,
                        [grouped.m_model for grouped in group],
                        camera,
                        fills,
                        shared,
                        size_px,
                    )
                elif owner is not None:
                    guarded(owner, owner.record, command_buffer, "transparent", group, camera, fills, shared, size_px)
                group = []
            if item is not None:
                group.append(item)
