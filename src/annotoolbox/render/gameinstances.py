"""Many instances of a model drawn by Anno 117's own shaders as the engine feeds them: the frame's words, instance
records, bone matrices and culled shadow words in one buffer each for all draws, a draw naming only where its words
start (``cbModel``); the island view's objects (docs/rendering/island-117-view.md "Phase 5", "Speed" part 5).

``CFrameInstances(rhi)`` gathers a frame's instances (``place``, ``place_casters``) and uploads them once; a
``CPartPass`` is one part through one program (shaders of ``gameshader.adapt(is_frame=True)``), an ``CInstanceRun`` one
of its draws. ``CCommon`` holds the terrain nodes' buffers without node values.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import NamedTuple

import numpy as np
from PySide6.QtGui import QRhiBuffer

from . import gamedraw
from . import gameshader as gs

ALPHA = 15 << 22  # the per-frame word's alpha: 15/15
DEFORM = 1 << 1  # ``Data.x``: the config bends the model to the terrain (``DeformToTerrainHeight``)
NO_TERRAIN_ADAPT = 1 << 7  # ``Data.y``: no terrain adaptation at all (a frame without the session's terrain)
LAST_NONE = 0xFFFFFFFF  # ``LastIndex``: no last frame's orientation
SLOT = 256  # a draw's slot in the offsets' uniform buffer: the alignment of a dynamic offset
SLOT_BUFFER = "cbModel"  # the root constant of a draw: where its words start
# the frame's buffers: their element's bytes and the names the shaders read them by (the bones stand still: last
# frame's are this frame's, one buffer binds both)
KINDS = {"words": (16, ("cModelPerFrameDataBuffer",)), "records": (48, ("cModelInstanceDataBuffer",)),
         "bones": (48, ("cCurrBoneMatrixBuffer", "cLastBoneMatrixBuffer")),
         "culled": (16, ("cModelGPUCulledInstanceData",))}  # fmt: skip
SMALLEST = 1 << 16  # a frame buffer's first size in bytes (grown to the next power of two)
GROWTH = 4  # a frame buffer that grows takes this many times what it needs (part 12: fewer binding sets made again)


def bone_rows(worlds: np.ndarray, joints: int = 0) -> np.ndarray:
    """Each instance's place as the engine's bone matrices hold it ([n * max(1, joints), 12] float32: the top three
    rows of each matrix, ``v' = [v, 1] M``), the bind pose for a skin: every joint at the instance's place."""
    rows = np.asarray(worlds, np.float32)[:, :3, :4].reshape(len(worlds), 12)
    return np.repeat(rows, max(1, joints), axis=0)


def instance_records(
    worlds: np.ndarray, colour: int = 0xFFF, deform: bool = False, is_terrain_adapted: bool = True
) -> np.ndarray:
    """Each instance's ``ModelInstanceData`` ([n, 12] words, 48 bytes): its place, its object colour (12 bits; 0 is
    black), ``deform`` (bent to the terrain), its height as the terrain adaptation reads it (7.8 fixed point from -4,
    clamped as the engine packs it; ``is_terrain_adapted`` False: no adaptation, no terrain to read), no last frame's
    orientation."""
    records = np.zeros((len(worlds), 12), np.uint32)
    places = np.asarray(worlds, np.float32)[:, :3, 3]
    records[:, 0:3] = np.ascontiguousarray(places).view(np.uint32)
    height = np.clip(np.rint((places[:, 1].astype(np.float64) + 4.0) * 256), 0, 0x7FFF).astype(np.uint32)
    records[:, 4], records[:, 7] = (colour << 4) | (DEFORM if deform else 0), LAST_NONE
    records[:, 5] = (height << 17) | (0 if is_terrain_adapted else NO_TERRAIN_ADAPT)
    return records


@functools.lru_cache(maxsize=512)
def culled_words(count: int, cascades: int, joints: int = 0) -> np.ndarray:
    """The shadow pass's per-draw words (``cModelGPUCulledInstanceData``) for ``count`` instances: instance ``i`` in
    cascade ``k`` at ``i * cascades + k``, the cascade in bits 30-31 (the viewer's draws of one instance)."""
    words = cascade_words(np.ones((count, cascades), bool), joints)
    words.flags.writeable = False  # kept: one array per count
    return words


def cascade_words(mask: np.ndarray, joints: int = 0, record: int = 0, bone: int = 0) -> np.ndarray:
    """The shadow pass's words for instances drawn only into the cascades ``mask`` ([n, cascades]) marks, one per
    instance and cascade it reaches, instance ``i``'s record at ``record + i``, its first bone at ``bone``."""
    return culled_rows([(mask, joints, record, bone)])


def culled_rows(casts: list) -> np.ndarray:
    """The words of a frame's draws at once (``cascade_words`` of each (mask, joints, record, bone), one after the
    other; masks of one width): a call per draw and caster was about 860 a turning frame (island-117-view.md
    "Speed", part 12)."""
    if not casts:
        return np.zeros((0, 4), np.uint32)
    sizes = [len(cast[0]) for cast in casts]
    starts = np.cumsum([0, *sizes[:-1]])
    local = (np.arange(sum(sizes)) - np.repeat(starts, sizes)).astype(np.uint32)
    record = np.repeat(np.array([cast[2] for cast in casts], np.uint32), sizes) + local
    stride = np.repeat(np.array([max(1, cast[1]) for cast in casts], np.uint32), sizes)
    bone = np.repeat(np.array([cast[3] for cast in casts], np.uint32), sizes) + local * stride
    instance_index, cascade_index = (
        indices.astype(np.uint32) for indices in np.nonzero(np.concatenate([cast[0] for cast in casts]))
    )
    words = np.zeros((len(instance_index), 4), np.uint32)
    words[:, 0] = (cascade_index << 30) | ALPHA | record[instance_index]
    words[:, 2] = bone[instance_index]
    return words


class CCulled:
    """A shadow draw's culled words before the frame writes them (``CFrameInstances.upload``): how many (its
    instances, one a caster and cascade)."""

    __slots__ = ("m_instance_count",)

    def __init__(self, instance_count: int) -> None:
        self.m_instance_count = instance_count

    def __len__(self) -> int:
        return self.m_instance_count


def stamp_of(camera: gs.SCamera, fill, attribute_name: str = "key") -> tuple | None:
    """What a draw's buffers hold besides its instances: the camera (its values, worked out once per camera) and the
    pass's own fill (its ``key``, or ``common_key`` for what it writes into the buffers without instance data; a fill
    without one: None, the buffers are filled every time)."""
    key = camera.__dict__.get("_stamp")
    if key is None:
        key = camera.__dict__["_stamp"] = (np.asarray(camera.view).tobytes(), np.asarray(camera.projection).tobytes(),
                                           np.asarray(camera.eye).tobytes(), camera.near, camera.far,
                                           tuple(camera.size_px), camera.shadow_far)  # fmt: skip
    if fill is None:
        return key, None
    own = (
        getattr(fill, attribute_name, None)
        if attribute_name == "key"
        else getattr(fill, attribute_name, getattr(fill, "key", None))
    )
    return (key, own) if own is not None else None


class CCommon:
    """The buffers of every terrain node through one program that hold no node value (``terraindraw``): one set,
    each compared with what any node last sent (``sent``) and filled once for a camera and pass (``stamps``); a node
    made while the camera moves makes only its own."""

    def __init__(self, rhi, alike=()) -> None:
        self.m_rhi = rhi
        self.m_alike = frozenset(alike)  # value names every user writes alike (the island's place)
        self.m_buffers: dict[str, object] = {}
        self.m_sizes: dict[str, int] = {}
        self.m_sent: dict[str, bytes] = {}
        self.m_stamps: dict[str, tuple] = {}  # what a buffer was last filled for (``stamp_of``): the next node skips it
        self._m_retired: list = []  # buffers a name held at another size: still bound until the release
        self._m_made: dict[str, object] = {}  # what every node binds alike (``once``: a white map, an empty buffer)

    def once(self, name: str, make):
        """The resource ``name`` every node binds alike, made by ``make()`` the first time (each one a driver
        allocation: a turning camera makes nodes every frame)."""
        if name not in self._m_made:
            self._m_made[name] = make()
        return self._m_made[name]

    def buffer(self, name: str, size_bytes: int):
        got = self.m_buffers.get(name)
        if got is None or self.m_sizes[name] != size_bytes:
            if got is not None:
                self._m_retired.append(got)
            got = self.m_buffers[name] = gamedraw.uniform_buffer(self.m_rhi, size_bytes)
            self.m_sizes[name] = size_bytes
            self.m_sent.pop(name, None)
            self.m_stamps.pop(name, None)
        return got

    def release(self) -> None:
        for buffer in (*self.m_buffers.values(), *self._m_retired, *self._m_made.values()):
            buffer.destroy()
        self.m_buffers, self.m_sizes, self.m_sent, self.m_stamps, self._m_retired, self._m_made = {}, {}, {}, {}, [], {}


class CFrameInstances:
    """A frame's instance data of every draw in one buffer each (module docstring): ``begin`` a new frame, ``place``
    a draw's instances and ``place_casters`` a shadow draw's while the frame's draws are made, ``upload`` once before
    the passes (a still camera's frame keeps the last frame's); ``bindings`` are the buffers by the shaders' names,
    ``offsets`` the draws' slots. A buffer that grows is made anew and ``generation`` counts up: a binding set of an
    older one is made again (``CPartPass``)."""

    def __init__(self, rhi) -> None:
        self.m_rhi = rhi
        self.m_buffers: dict[str, QRhiBuffer] = {}
        self.m_offsets: QRhiBuffer | None = None
        self.m_generation = 0
        self.m_is_terrain_adapted = (
            False  # the frame hands the session's terrain: the models adapt to it (else none does)
        )
        self.begin()

    def begin(self) -> None:
        self._m_parts: dict[str, list] = {kind: [] for kind in KINDS}
        self._m_counts = dict.fromkeys(KINDS, 0)
        self._m_slots: list[int] = []
        # (id of the worlds, joints, deform) -> (record, bone, the worlds kept); its slot (a prepass and its draw); its
        # shadow slot and words (each mesh's)
        self._m_records: dict[tuple, tuple] = {}
        self._m_drawn: dict[tuple, int] = {}
        self._m_cast: dict[tuple, tuple] = {}
        # per kind of words ("words", "culled") the draws' (mask, joints, record, bone): made at the upload
        self._m_later: dict[str, list] = {"words": [], "culled": []}
        self._m_sent = False
        self._m_gathered: dict | None = None
        self.m_serial = getattr(self, "m_serial", -1) + 1  # which frame's instances these are (the native copy's key)

    def _append(self, kind: str, rows: np.ndarray) -> int:
        start = self._m_counts[kind]
        self._m_parts[kind].append(rows)
        self._m_counts[kind] += len(rows)
        return start

    def _defer(self, kind: str, mask: np.ndarray, joints: int, record: int, bone: int) -> tuple[int, int]:
        """Words of ``kind`` made with the upload (``culled_rows``): where they start and how many."""
        start, count = self._m_counts[kind], int(np.count_nonzero(mask))
        self._m_counts[kind] += count
        self._m_later[kind].append((mask, joints, record, bone))
        return start, count

    def _slot(self, offset: int) -> int:
        self._m_slots.append(offset)
        return (len(self._m_slots) - 1) * SLOT

    def _placed(self, worlds: np.ndarray, joints: int, deform: bool = False) -> tuple[int, int]:
        """Where the records and bones of ``worlds`` start (written once a frame: every pass's draws name them)."""
        key = (id(worlds), joints, deform)  # the worlds are kept in ``_records``, which a frame empties with the rest
        got = self._m_records.get(key)
        if got is None:
            record = self._append(
                "records", instance_records(worlds, deform=deform, is_terrain_adapted=self.m_is_terrain_adapted)
            )
            bone = self._append("bones", bone_rows(worlds, joints))
            got = self._m_records[key] = (record, bone, worlds)  # the worlds kept: their id stays theirs this frame
        return got[0], got[1]

    def place(self, worlds: np.ndarray, joints: int = 0, deform: bool = False) -> int:
        """A draw of ``worlds`` ([n, 4, 4], column vectors; ``joints``: a skin's bones, 0 rigid; ``deform``: bent to
        the terrain): their words, each naming its record and first bone; the byte offset of its slot."""
        key = (id(worlds), joints, deform)
        if key not in self._m_drawn:
            record, bone = self._placed(worlds, joints, deform)
            start, _count = self._defer("words", np.ones((len(worlds), 1), bool), joints, record, bone)  # cascade 0
            self._m_drawn[key] = self._slot(start)
        return self._m_drawn[key]

    def place_casters(
        self, worlds: np.ndarray, joints: int, mask: np.ndarray, deform: bool = False
    ) -> tuple[int, CCulled]:
        """A shadow draw of ``worlds``, each instance into the cascades ``mask`` ([n, cascades]) marks: its slot's
        byte offset and its culled words (``CCulled``: one an instance and cascade, the draw's instance count; written
        with the frame's upload, ``culled_rows``); the meshes of a part's level share them."""
        mask = np.asarray(mask, bool)
        key = (id(worlds), joints, deform, mask.tobytes())  # a cut-out's draws take one cascade each
        if key not in self._m_cast:
            record, bone = self._placed(worlds, joints, deform)
            start, count = self._defer("culled", mask, joints, record, bone)
            self._m_cast[key] = (self._slot(start), CCulled(count))
        return self._m_cast[key]

    def place_each_cascade(self, worlds: np.ndarray, joints: int, mask: np.ndarray, deform: bool = False):
        """(cascade, slot, culled words) of a shadow draw of ``worlds`` for each cascade ``mask`` marks for any of
        them, into that cascade alone (a cut-out volume: its shadow shaders place it by the draw's cascade, not by an
        instance's word as the models' do)."""
        mask = np.asarray(mask, bool)
        for k in range(mask.shape[1]):
            only = np.zeros_like(mask)
            only[:, k] = mask[:, k]
            if only.any():
                yield (k, *self.place_casters(worlds, joints, only, deform))

    def bindings(self) -> dict:
        """The frame's buffers by the names the shaders read them by (made at their first size when missing)."""
        for kind in KINDS:
            self._fit(kind, 0)
        self._fit_offsets(1)
        return {name: self.m_buffers[kind] for kind, (_size_bytes, names) in KINDS.items() for name in names}

    def _fit(self, kind: str, size_bytes: int) -> None:
        buffer = self.m_buffers.get(kind)
        if buffer is not None and buffer.size() >= size_bytes:
            return
        if buffer is not None:  # grown with room: each growth makes every part's binding set again
            buffer.destroy()
            self.m_generation += 1
            size_bytes *= GROWTH
        buffer = self.m_rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, _grown(size_bytes))
        buffer.create()
        self.m_buffers[kind] = buffer

    def _fit_offsets(self, slots: int) -> None:
        if self.m_offsets is not None and self.m_offsets.size() >= slots * SLOT:
            return
        if self.m_offsets is not None:
            self.m_offsets.destroy()
            self.m_generation += 1
            slots *= GROWTH
        self.m_offsets = gamedraw.uniform_buffer(self.m_rhi, _grown(slots * SLOT))

    def gathered(self) -> dict[str, bytes]:
        """The frame's words, records, bones and culled words by kind, and ``"slots"`` (each draw's ``cbModel``, one
        ``SLOT`` each), as bytes: made once a frame, for the QRhi buffers (``upload``) and the native backend's."""
        if self._m_gathered is None:
            for kind, later in self._m_later.items():  # the draws' and the casters' words, each kind at once
                if later:
                    self._m_parts[kind], self._m_later[kind] = [culled_rows(later)], []
            out = {kind: np.concatenate(parts).tobytes() if parts else b"" for kind, parts in self._m_parts.items()}
            slots = np.zeros((len(self._m_slots), SLOT // 4), np.uint32)
            slots[:, 0] = self._m_slots
            out["slots"] = slots.tobytes()
            self._m_gathered = out
        return self._m_gathered

    def upload(self, batch) -> None:
        """The frame's words, records, bones and slots into their buffers (once a frame; grown first where needed)."""
        if self._m_sent:
            return
        self._m_sent = True
        got = self.gathered()
        for kind in KINDS:
            data = got[kind]
            self._fit(kind, len(data))
            if data:
                batch.uploadStaticBuffer(self.m_buffers[kind], 0, len(data), data)
        self._fit_offsets(len(self._m_slots))
        if len(self._m_slots):
            batch.updateDynamicBuffer(self.m_offsets, 0, got["slots"])

    def release(self) -> None:
        for buffer in (*self.m_buffers.values(), self.m_offsets):
            if buffer is not None:
                buffer.destroy()
        self.m_buffers, self.m_offsets = {}, None
        self.begin()


def _grown(size_bytes: int) -> int:
    """A buffer's size for ``size`` bytes: the next power of two, ``SMALLEST`` at least."""
    return max(SMALLEST, 1 << max(0, int(size_bytes) - 1).bit_length())


class SPartInputs(NamedTuple):
    """What the passes of one part (a material) share, the owner's: the material, the frame's instance buffers, the
    textures the owner hands by name, the material's arrays (``maps``: made by the first pass that draws) and their
    table, and ``meshes(mesh, batch)``: the vertex and index buffers of a mesh (one copy for every pass)."""

    material: gamedraw.SMaterial
    frame: CFrameInstances
    textures: dict
    maps: list
    table: gamedraw.CTextureTable
    meshes: Callable | None = None


class CPartPass(gamedraw.CGameModel):
    """One part (``SPartInputs``) through one program of a pass: one binding set with the frame's instance buffers and
    ``cbModel`` at a draw's slot, its constant buffers written once a frame (``prepare``), per mesh its per-vertex
    inputs and a pipeline of the program's (``mesh_inputs``)."""

    def __init__(self, rhi, program, state, part: SPartInputs) -> None:
        super().__init__(rhi, program, None, part.material, None, state, part.table)
        self.m_frame, self.m_textures, self.m_maps, self.m_meshes = part.frame, part.textures, part.maps, part.meshes
        self.m_srb = None
        self.m_slot_binding: int | None = None  # ``cbModel``'s binding: the draw's slot is its dynamic offset
        self.m_inputs: dict[int, tuple] = {}  # id of a mesh -> (vertex buffers, index buffer, pipeline, its key, mesh)
        self._m_stamp: tuple | None = None  # the camera and pass inputs its constants hold (``stamp_of``)
        self._m_shared: dict = {}  # what the pass and the owner hand by name (bound again when the frame's grow)
        self._m_generation = -1
        self._m_own: dict[tuple, object] = {}  # its samplers, stand-ins and scratch buffers: made once for every set
        self._m_shared_key: tuple = ()  # the frame's resources it was bound with (``_key_of``)

    def _cbuffer(self, name: str, number: int, stages):
        if name != SLOT_BUFFER:
            return super()._cbuffer(name, number, stages)
        if self.m_program.m_cbuffers[name].size > SLOT:  # the engine's is a root constant: a draw's slot must hold it
            raise RuntimeError(f"{name} is larger than a draw's slot ({SLOT} bytes)")
        self.m_slot_binding = number
        return gamedraw.Binding.uniformBufferWithDynamicOffset(number, stages, self.m_frame.m_offsets, SLOT)

    def _once(self, kind: str, name: str, make):
        key = (kind, name)
        if key not in self._m_own:
            self._m_own[key] = make()
        return self._m_own[key]

    def _sampler(self, name: str):
        return self._once("sampler", name, lambda: super(CPartPass, self)._sampler(name))

    def _scratch(self, name: str):
        return self._once("scratch", name, lambda: super(CPartPass, self)._scratch(name))

    def _texture(self, name: str, shared: dict, white):
        if name in shared:
            return shared[name]
        return self._once("texture", name, lambda: super(CPartPass, self)._texture(name, shared, white))

    def _make(self, batch, shared: dict) -> None:
        if not self.m_maps:
            self.m_maps.extend(self.m_table.create(self.m_rhi, batch))
            self.m_table.drop_pixels()  # on the device now: every pass binds the same arrays
            self.m_material.maps.clear()
        self.m_arrays = self.m_maps
        self.m_constants, self.m_buffers, self._m_sent, self._m_own = {}, {}, {}, {}
        self.m_white = self._white(batch)
        self._bind(shared)

    def _bind(self, shared: dict) -> None:
        """The binding set, made again when the frame's buffers or the resources the pass hands were made anew
        (the pipelines stay: alike)."""
        if self.m_srb is not None:
            self.m_keep.remove(self.m_srb)
            self.m_srb.destroy()
        self._m_shared = {**self.m_textures, **shared}
        self.m_srb = self._bindings({**self._m_shared, **self.m_frame.bindings()}, self.m_white)
        self._m_generation, self._m_shared_key = self.m_frame.m_generation, _key_of(shared)

    def prepare(self, render_pass, signature, batch, camera: gs.SCamera, fill=None, shared: dict | None = None) -> None:
        """Made when new; its constants written once for a camera and the pass's inputs (the fill's ``common_key``
        or ``key``; a fill without one: every time), a buffer sent only when it changed."""
        if not self.m_keep:
            self._make(batch, shared or {})
        elif self._m_generation != self.m_frame.m_generation or self._m_shared_key != _key_of(shared or {}):
            self._bind(shared or {})
        stamp = stamp_of(camera, fill, "common_key")
        if stamp is not None and stamp == self._m_stamp:
            return
        for name, constants_block in self.m_constants.items():
            gs.fill_frame(constants_block, camera)
            if fill is not None:
                fill(constants_block)
            for path, value in self.m_values.items():  # the owner's (a cut-out's cascade)
                constants_block.set(path, value)
            self._send(batch, name, bytes(constants_block.m_data))
        self._m_stamp = stamp

    def mesh_inputs(self, mesh: gamedraw.CMesh, render_pass, signature, batch) -> tuple:
        """A mesh's vertex buffers (the owner's, and the inputs it lacks), index buffer and pipeline, made once."""
        got = self.m_inputs.get(id(mesh))  # pattern: ok (the entry holds the mesh: its id stays its own)
        if got is None:
            vertices, indices = self.m_meshes(mesh, batch)
            fill, attributes = gamedraw.vertex_input(mesh, self.m_program.m_vertex_stage.inputs)
            fill_buffer = self._buffer(batch, QRhiBuffer.UsageFlag.VertexBuffer, fill)
            pipeline, key = self._shared_pipeline(
                render_pass, signature, attributes, mesh.m_stride, len(fill) // max(1, mesh.m_count)
            )
            got = self.m_inputs[id(mesh)] = (  # pattern: ok (as above)
                [vertices, fill_buffer],
                indices,
                pipeline,
                key,
                mesh,
            )
        return got

    def release(self) -> None:
        for _vbufs, _ibuf, _pipeline, key, _mesh in self.m_inputs.values():
            self._give_back(key)
        super().release()
        self.m_inputs, self.m_srb, self._m_stamp, self._m_generation, self._m_own = {}, None, None, -1, {}


def _key_of(shared: dict) -> tuple:
    """Which resources ``shared`` hands by name (a shadow map, a light buffer, the water's copies: made anew when
    they grow or come in, the old ones destroyed)."""
    # the bound set keeps the values (``_shared``): an id compared against it is never a reused one
    return tuple((name, id(value)) for name, value in shared.items())


class CInstanceRun:
    """One draw of a ``CPartPass``: a mesh, the instances it draws (``worlds``: the transparent pass orders by their
    box), its slot (the byte offset of its words' start), and for a shadow draw its culled ``words``."""

    m_world = np.identity(4)

    def __init__(self, setup: CPartPass, mesh: gamedraw.CMesh, slot: int, worlds: np.ndarray, words=None,
                 viewport: tuple | None = None) -> None:  # fmt: skip
        self.m_setup, self.m_mesh, self.m_slot, self.m_worlds, self.m_words = setup, mesh, slot, worlds, words
        self.m_viewport = viewport  # a share of the target (``share_rect``): one cascade's part of the shadow map
        self._m_inputs: tuple | None = None

    @property
    def m_instances(self) -> int:
        return len(self.m_worlds)

    def fresh(self) -> bool:
        """Whether its first prepare is still to come: the part's resources or this mesh's inputs are not made."""
        return (
            not self.m_setup.m_keep
            or id(self.m_mesh) not in self.m_setup.m_inputs  # pattern: ok (this run holds the mesh)
        )

    def prepare(self, render_pass, signature, batch, camera: gs.SCamera, fill=None, shared: dict | None = None) -> None:
        self.m_setup.prepare(render_pass, signature, batch, camera, fill, shared)
        self._m_inputs = self.m_setup.mesh_inputs(self.m_mesh, render_pass, signature, batch)
        if self._m_inputs[2] is None:
            raise RuntimeError(self.m_setup.m_error or "no pipeline")

    def draw(self, command_buffer, size_px: tuple[int, int], instances: int | None = None) -> None:
        vertex_buffers, index_buffer, pipeline, _key, mesh = self._m_inputs
        count = self.m_instances if instances is None else instances
        dynamic_offset = (self.m_setup.m_slot_binding, self.m_slot) if self.m_setup.m_slot_binding is not None else None
        area = size_px if self.m_viewport is None else gamedraw.share_rect(size_px, self.m_viewport)
        mesh.draw(
            command_buffer,
            pipeline,
            self.m_setup.m_srb,
            area,
            (vertex_buffers, index_buffer),
            count,
            dynamic_offset,
            self.m_setup.m_state,
        )

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The box of the instances' places: the transparent pass sorts by it."""
        places = np.asarray(self.m_worlds, float)[:, :3, 3]
        return places.min(axis=0), places.max(axis=0)
