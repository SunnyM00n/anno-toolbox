"""Many instances of a model drawn by Anno 117's own shaders as the engine feeds them: the frame's words and culled
shadow words in one buffer each for all draws, the instance records and bone matrices kept between frames
(``instancestore``), a draw naming only where its words start (``cbModel``); the island view's objects
(docs/rendering/island-117-view.md "Phase 5", "Speed" part 5; performance.md section 4, item 1).

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

from . import gamedraw, gametextures
from . import gameshader as gs
from .instancestore import ALPHA, CInstanceStore, SPlaces

SLOT = 256  # a draw's slot in the offsets' uniform buffer: the alignment of a dynamic offset
SLOT_BUFFER = "cbModel"  # the root constant of a draw: where its words start
# the frame's buffers: their element's bytes and the names the shaders read them by (the bones stand still: last
# frame's are this frame's, one buffer binds both)
KINDS = {"words": (16, ("cModelPerFrameDataBuffer",)), "records": (48, ("cModelInstanceDataBuffer",)),
         "bones": (48, ("cCurrBoneMatrixBuffer", "cLastBoneMatrixBuffer")),
         "culled": (16, ("cModelGPUCulledInstanceData",))}  # fmt: skip
ROW_BYTES = 48  # a record's and a bone matrix's bytes (``KINDS``)
SMALLEST = 1 << 16  # a frame buffer's first size in bytes (grown to the next power of two)
GROWTH = 4  # a frame buffer that grows takes this many times what it needs (part 12: fewer binding sets made again)


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
    return culled_rows([(mask, joints, record, bone, np.arange(len(mask)))])


def culled_rows(casts: list) -> np.ndarray:
    """The words of a frame's draws at once, one after the other: each (mask, joints, record, bone, local) names the
    instances ``local`` of the slab whose records start at ``record`` and bones at ``bone``, each into the cascades
    its row of ``mask`` marks (masks of one width; a draw's words: one column); a call per draw was about 860 a
    turning frame (island-117-view.md "Speed", part 12). A mask of None (every cast's): each instance once, cascade 0."""
    if not casts:
        return np.zeros((0, 4), np.uint32)
    sizes = [len(cast[4]) for cast in casts]
    local = np.concatenate([cast[4] for cast in casts]).astype(np.uint32)
    record = np.repeat(np.array([cast[2] for cast in casts], np.uint32), sizes) + local
    stride = np.repeat(np.array([max(1, cast[1]) for cast in casts], np.uint32), sizes)
    bone = np.repeat(np.array([cast[3] for cast in casts], np.uint32), sizes) + local * stride
    if casts[0][0] is None:  # a draw's words: every instance, no cascade
        instance_index, cascade_index = np.arange(len(local)), np.zeros(len(local), np.uint32)
    else:
        instance_index, cascade_index = (
            indices.astype(np.uint32) for indices in np.nonzero(np.concatenate([cast[0] for cast in casts]))
        )
    words = np.zeros((len(instance_index), 4), np.uint32)
    words[:, 0] = (cascade_index << 30) | ALPHA | record[instance_index]
    words[:, 2] = bone[instance_index]
    return words


CULL_CASCADES = 4  # the culled words a cull object may write: one per cascade (SHADOW_MAP_CASCADE_COUNT)
CULL_WORDS = 16  # ``ModelCullObject``'s words (model_culler.fx): 64 bytes
CULL_ARGS_WORDS = 4  # ``InputDrawArgs``: index count, index and vertex offset, padding


def cull_objects(culled: list) -> tuple[np.ndarray, np.ndarray]:
    """(``ModelCullObject`` rows [n, 16], ``InputDrawArgs`` rows [draws, 4]) of a frame's GPU-culled draws
    (``CFrameInstances.place_culled``' entries) at once: each instance's per-frame word (its record and first bone),
    its mesh's box, its draw and where its draw's words start; one entity, no volumetric cascade."""
    if not culled:
        return np.zeros((0, CULL_WORDS), np.uint32), np.zeros((0, CULL_ARGS_WORDS), np.uint32)
    sizes = [len(entry[3]) for entry in culled]
    local = np.concatenate([entry[3] for entry in culled]).astype(np.uint32)
    record = np.repeat(np.array([entry[1] for entry in culled], np.uint32), sizes) + local
    stride = np.repeat(np.array([max(1, entry[0]) for entry in culled], np.uint32), sizes)
    rows = np.zeros((len(local), CULL_WORDS), np.uint32)
    rows[:, 0] = ALPHA | record
    rows[:, 2] = np.repeat(np.array([entry[2] for entry in culled], np.uint32), sizes) + local * stride
    boxes = np.array([np.concatenate(entry[4]) for entry in culled], np.float32).view(np.uint32)
    each = np.repeat(np.arange(len(culled)), sizes)
    rows[:, 4:7], rows[:, 8:11] = boxes[each, :3], boxes[each, 3:]
    rows[:, 7] = each
    rows[:, 11] = np.repeat(np.array([entry[6] for entry in culled], np.uint32), sizes)
    rows[:, 12] = 1
    arguments = np.zeros((len(culled), CULL_ARGS_WORDS), np.uint32)
    arguments[:, 0] = [entry[5] for entry in culled]
    return rows, arguments


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
    key = gs.camera_key(camera)
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
    """A frame's instance data of every draw (module docstring): ``begin`` a new frame, ``place`` a draw's instances
    and ``place_casters`` a shadow draw's while the frame's draws are made, ``upload`` once before the passes (a still
    camera's frame keeps the last frame's). The records and bones are the store's (``m_store``), kept between frames:
    a frame writes only its words and the rows that changed. ``bindings`` are the buffers by the shaders' names,
    ``offsets`` the draws' slots. A buffer that grows is made anew and ``generation`` counts up: a binding set of an
    older one is made again (``CPartPass``)."""

    def __init__(self, rhi) -> None:
        self.m_rhi = rhi
        self.m_buffers: dict[str, QRhiBuffer] = {}
        self.m_offsets: QRhiBuffer | None = None
        self.m_generation = 0
        self.m_store = CInstanceStore()
        self.m_is_terrain_adapted = False  # the frame hands the session's terrain: the models adapt to it
        self.begin()

    def begin(self) -> None:
        self.m_store.end_frame()
        self._m_counts = {"words": 0, "culled": 0}
        self._m_slots: list[int] = []
        # (id of the places, id of the drawn ones, joints[, mask]) -> (slot[, culled words], the places and drawn ones
        # kept: their ids stay theirs this frame)
        self._m_drawn: dict[tuple, tuple] = {}
        self._m_cast: dict[tuple, tuple] = {}
        # per kind of words ("words", "culled") the draws' (mask, joints, record, bone, drawn ones): made at the upload
        self._m_later: dict[str, list] = {"words": [], "culled": []}
        # the draws whose casters the GPU culls (``place_culled``): (joints, record, bone, drawn ones, box, indices)
        self._m_culled: list[tuple] = []
        self._m_cull_keys: dict[tuple, tuple] = {}
        self.m_cull_outputs = 0  # the culled words those draws may write, ``cascades`` an instance
        self._m_sent = False
        self._m_gathered: dict | None = None
        self.m_serial = getattr(self, "m_serial", -1) + 1  # which frame's instances these are (the native copy's key)

    def _defer(self, kind: str, mask, joints: int, slab: tuple, local: np.ndarray) -> tuple[int, int]:
        """Words of ``kind`` made with the upload (``culled_rows``): where they start and how many."""
        start, count = self._m_counts[kind], len(local) if mask is None else int(np.count_nonzero(mask))
        self._m_counts[kind] += count
        self._m_later[kind].append((mask, joints, slab[0], slab[1], local))
        return start, count

    def _slot(self, offset: int) -> int:
        self._m_slots.append(offset)
        return (len(self._m_slots) - 1) * SLOT

    def _local(self, places: SPlaces, joints: int, local) -> tuple[tuple, np.ndarray]:
        self.m_store.adapt(self.m_is_terrain_adapted)
        slab = self.m_store.slab(places, joints)
        return slab, np.arange(slab[2]) if local is None else local

    def place(self, places: SPlaces, local: np.ndarray | None = None, joints: int = 0) -> int:
        """A draw of the instances ``local`` (indices into ``places``' instances; None: all of them; ``joints``: a
        skin's bones, 0 rigid): their words, each naming its record and first bone in the store; the byte offset of
        its slot. The meshes and passes of a part's level share it."""
        key = (id(places), id(local), joints)
        got = self._m_drawn.get(key)
        if got is None:
            slab, drawn = self._local(places, joints, local)
            start, _count = self._defer("words", None, joints, slab, drawn)
            got = self._m_drawn[key] = (self._slot(start), places, local)
        return got[0]

    def place_casters(self, places: SPlaces, local, joints: int, mask: np.ndarray) -> tuple[int, CCulled]:
        """A shadow draw of the instances ``local`` of ``places`` (None: all), each into the cascades ``mask`` ([n,
        cascades]) marks: its slot's byte offset and its culled words (``CCulled``: one an instance and cascade, the
        draw's instance count; written with the frame's upload, ``culled_rows``); the meshes of a part's level share
        them."""
        key = (id(places), id(local), joints, id(mask))
        got = self._m_cast.get(key)
        if got is None:
            slab, drawn = self._local(places, joints, local)
            start, count = self._defer("culled", np.asarray(mask, bool), joints, slab, drawn)
            got = self._m_cast[key] = (self._slot(start), CCulled(count), places, local, mask)
        return got[0], got[1]

    def place_each_cascade(self, places: SPlaces, local, joints: int, mask: np.ndarray):
        """(cascade, slot, culled words) of a shadow draw of ``local`` for each cascade ``mask`` marks for any of
        them, into that cascade alone (a cut-out volume: its shadow shaders place it by the draw's cascade, not by an
        instance's word as the models' do)."""
        key = (id(places), id(local), joints, id(mask), "each")  # each mask is a draw of its own, as in place_casters
        got = self._m_cast.get(key)
        if got is None:
            reach, out = np.asarray(mask, bool), []
            for k in range(reach.shape[1]):
                only = np.zeros_like(reach)
                only[:, k] = reach[:, k]
                if only.any():
                    out.append((k, *self.place_casters(places, local, joints, only)))
            got = self._m_cast[key] = (out, places, local, mask)
        yield from got[0]

    def place_culled(self, places: SPlaces, local, joints: int, mesh, box: tuple, index_count: int) -> tuple[int, int]:
        """A shadow draw of ``mesh`` (its ``box`` in its own space: min, max) whose casters the GPU culls into the
        cascades (``shadowcullnative``): one cull object per instance of ``local`` (None: all), its words written by the
        GPU from where the draw's slot names on; (its slot's byte offset, its draw arguments' index)."""
        key = (id(places), id(local), joints, id(mesh))
        got = self._m_cull_keys.get(key)
        if got is None:
            slab, drawn = self._local(places, joints, local)
            start = self.m_cull_outputs
            self.m_cull_outputs += len(drawn) * CULL_CASCADES
            self._m_culled.append((joints, slab[0], slab[1], drawn, box, index_count, start))
            got = self._m_cull_keys[key] = (self._slot(start), len(self._m_culled) - 1, places, local, mesh)
        return got[0], got[1]

    def drawn_set(self, places: SPlaces, local: np.ndarray | None = None) -> SDrawnSet:
        """The instances ``local`` of ``places`` (None: all) as a draw names them."""
        return SDrawnSet(self, places, local, len(places.worlds()) if local is None else len(local))

    def positions(self, places: SPlaces, local) -> np.ndarray:
        """The places ([n, 3]) of the instances ``local`` of ``places`` (None: all), as their records hold them."""
        slab, drawn = self._local(places, 0, local)
        return self.m_store.positions(slab[0], drawn)

    def bindings(self) -> dict:
        """The frame's buffers by the names the shaders read them by (made at their first size when missing)."""
        for kind in KINDS:
            self._fit(kind, 0)
        self._fit_offsets(1)
        return {name: self.m_buffers[kind] for kind, (_size_bytes, names) in KINDS.items() for name in names}

    def _fit(self, kind: str, size_bytes: int) -> bool:
        """Whether the buffer of ``kind`` was made anew to hold ``size_bytes``."""
        buffer = self.m_buffers.get(kind)
        if buffer is not None and buffer.size() >= size_bytes:
            return False
        if buffer is not None:  # grown with room: each growth makes every part's binding set again
            buffer.destroy()
            self.m_generation += 1
            size_bytes *= GROWTH
        buffer = self.m_rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, _grown(size_bytes))
        buffer.create()
        self.m_buffers[kind] = buffer
        return True

    def _fit_offsets(self, slots: int) -> None:
        if self.m_offsets is not None and self.m_offsets.size() >= slots * SLOT:
            return
        if self.m_offsets is not None:
            self.m_offsets.destroy()
            self.m_generation += 1
            slots *= GROWTH
        self.m_offsets = gamedraw.uniform_buffer(self.m_rhi, _grown(slots * SLOT))

    def gathered(self) -> dict[str, bytes]:
        """The frame's words and culled words by kind, and ``"slots"`` (each draw's ``cbModel``, one ``SLOT`` each),
        as bytes: made once a frame, for the QRhi buffers (``upload``) and the native backend's."""
        if self._m_gathered is None:
            out = {kind: culled_rows(later).tobytes() for kind, later in self._m_later.items()}
            slots = np.zeros((len(self._m_slots), SLOT // 4), np.uint32)
            slots[:, 0] = self._m_slots
            out["slots"] = slots.tobytes()
            out["cull"], out["cull_args"] = (rows.tobytes() for rows in cull_objects(self._m_culled))
            self._m_gathered = out
        return self._m_gathered

    def kept_rows(self, consumer: str):
        """(kind, the store's rows of it, how many are in use, its changes for ``consumer``) of the records and bones:
        each consumer (a buffer set) takes the ranges written since its last call (``CRows.changes``)."""
        for kind, rows in (("records", self.m_store.m_records), ("bones", self.m_store.m_bones)):
            yield kind, rows.m_rows, rows.m_top, rows.changes(consumer)

    def upload(self, batch) -> None:
        """The frame's words and slots, and the records and bones that changed, into their buffers (once a frame;
        grown first where needed: a buffer made anew takes every row)."""
        if self._m_sent:
            return
        self._m_sent = True
        got = self.gathered()
        for kind in ("words", "culled"):
            data = got[kind]
            self._fit(kind, len(data))
            if data:
                batch.uploadStaticBuffer(self.m_buffers[kind], 0, len(data), data)
        for kind, rows, top, ranges in self.kept_rows("qrhi"):
            if self._fit(kind, top * ROW_BYTES):
                ranges = [(0, top)] if top else []
            for start, stop in ranges:
                data = rows[start:stop].tobytes()
                batch.uploadStaticBuffer(self.m_buffers[kind], start * ROW_BYTES, len(data), data)
        self._fit_offsets(len(self._m_slots))
        if len(self._m_slots):
            batch.updateDynamicBuffer(self.m_offsets, 0, got["slots"])

    def release(self) -> None:
        for buffer in (*self.m_buffers.values(), self.m_offsets):
            if buffer is not None:
                buffer.destroy()
        self.m_buffers, self.m_offsets = {}, None
        self.m_store.clear()
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
    table: gametextures.CTextureTable
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


class SDrawnSet(NamedTuple):
    """The instances a draw names: ``local`` of ``places`` (None: all) in ``frame``'s store, ``count`` of them."""

    frame: CFrameInstances | None
    places: SPlaces | None
    local: np.ndarray | None
    count: int

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The box of their places: the transparent pass sorts by it."""
        if self.frame is None or not self.count:
            return np.zeros(3), np.zeros(3)
        places = self.frame.positions(self.places, self.local).astype(float)
        return places.min(axis=0), places.max(axis=0)


NO_INSTANCES = SDrawnSet(None, None, None, 0)


class CInstanceRun:
    """One draw of a ``CPartPass``: a mesh, the instances it draws (``drawn``: the transparent pass orders by their
    box), its slot (the byte offset of its words' start), and for a shadow draw its culled ``words``."""

    m_world = np.identity(4)

    def __init__(self, setup: CPartPass, mesh: gamedraw.CMesh, slot: int, drawn: SDrawnSet, words=None,
                 viewport: tuple | None = None) -> None:  # fmt: skip
        self.m_setup, self.m_mesh, self.m_slot, self.m_drawn, self.m_words = setup, mesh, slot, drawn, words
        self.m_viewport = viewport  # a share of the target (``share_rect``): one cascade's part of the shadow map
        self._m_inputs: tuple | None = None

    @property
    def m_instances(self) -> int:
        return self.m_drawn.count

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
        return self.m_drawn.box()
