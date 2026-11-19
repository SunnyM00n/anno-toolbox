"""Many instances of a model drawn by Anno 117's own shaders as the engine feeds them: the frame's words, instance
records, bone matrices and culled shadow words in one buffer each for all draws, a draw naming only where its words
start (``cbModel``); the island view's objects (docs/rendering/island-117-view.md "Phase 5", "Speed" part 5).

``FrameInstances(rhi)`` gathers a frame's instances (``place``, ``place_casters``) and uploads them once; a
``PartPass`` is one part through one program (shaders of ``gameshader.adapt(frame=True)``), an ``InstanceRun`` one
of its draws. ``Common`` holds the terrain nodes' buffers without node values.
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


def instance_records(worlds: np.ndarray, colour: int = 0xFFF, deform: bool = False, adapt: bool = True) -> np.ndarray:
    """Each instance's ``ModelInstanceData`` ([n, 12] words, 48 bytes): its place, its object colour (12 bits; 0 is
    black), ``deform`` (bent to the terrain), its height as the terrain adaptation reads it (7.8 fixed point from -4,
    clamped as the engine packs it; ``adapt`` False: no adaptation, no terrain to read), no last frame's
    orientation."""
    rec = np.zeros((len(worlds), 12), np.uint32)
    places = np.asarray(worlds, np.float32)[:, :3, 3]
    rec[:, 0:3] = np.ascontiguousarray(places).view(np.uint32)
    height = np.clip(np.rint((places[:, 1].astype(np.float64) + 4.0) * 256), 0, 0x7FFF).astype(np.uint32)
    rec[:, 4], rec[:, 7] = (colour << 4) | (DEFORM if deform else 0), LAST_NONE
    rec[:, 5] = (height << 17) | (0 if adapt else NO_TERRAIN_ADAPT)
    return rec


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
    sizes = [len(c[0]) for c in casts]
    starts = np.cumsum([0, *sizes[:-1]])
    local = (np.arange(sum(sizes)) - np.repeat(starts, sizes)).astype(np.uint32)
    record = np.repeat(np.array([c[2] for c in casts], np.uint32), sizes) + local
    stride = np.repeat(np.array([max(1, c[1]) for c in casts], np.uint32), sizes)
    bone = np.repeat(np.array([c[3] for c in casts], np.uint32), sizes) + local * stride
    i, k = (a.astype(np.uint32) for a in np.nonzero(np.concatenate([c[0] for c in casts])))
    words = np.zeros((len(i), 4), np.uint32)
    words[:, 0] = (k << 30) | ALPHA | record[i]
    words[:, 2] = bone[i]
    return words


class Culled:
    """A shadow draw's culled words before the frame writes them (``FrameInstances.upload``): how many (its
    instances, one a caster and cascade)."""

    __slots__ = ("count",)

    def __init__(self, count: int) -> None:
        self.count = count

    def __len__(self) -> int:
        return self.count


def stamp_of(camera: gs.Camera, fill, attr: str = "key") -> tuple | None:
    """What a draw's buffers hold besides its instances: the camera (its values, worked out once per camera) and the
    pass's own fill (its ``key``, or ``common_key`` for what it writes into the buffers without instance data; a fill
    without one: None, the buffers are filled every time)."""
    key = camera.__dict__.get("_stamp")
    if key is None:
        key = camera.__dict__["_stamp"] = (np.asarray(camera.view).tobytes(), np.asarray(camera.proj).tobytes(),
                                           np.asarray(camera.eye).tobytes(), camera.near, camera.far,
                                           tuple(camera.size), camera.shadow_far)  # fmt: skip
    if fill is None:
        return key, None
    own = getattr(fill, attr, None) if attr == "key" else getattr(fill, attr, getattr(fill, "key", None))
    return (key, own) if own is not None else None


class Common:
    """The buffers of every terrain node through one program that hold no node value (``terraindraw``): one set,
    each compared with what any node last sent (``sent``) and filled once for a camera and pass (``stamps``); a node
    made while the camera moves makes only its own."""

    def __init__(self, rhi, alike=()) -> None:
        self.rhi = rhi
        self.alike = frozenset(alike)  # value names every user writes alike (the island's place)
        self.buffers: dict[str, object] = {}
        self.sizes: dict[str, int] = {}
        self.sent: dict[str, bytes] = {}
        self.stamps: dict[str, tuple] = {}  # what a buffer was last filled for (``stamp_of``): the next node skips it
        self._retired: list = []  # buffers a name held at another size: still bound until the release
        self._made: dict[str, object] = {}  # what every node binds alike (``once``: a white map, an empty buffer)

    def once(self, name: str, make):
        """The resource ``name`` every node binds alike, made by ``make()`` the first time (each one a driver
        allocation: a turning camera makes nodes every frame)."""
        if name not in self._made:
            self._made[name] = make()
        return self._made[name]

    def buffer(self, name: str, size: int):
        got = self.buffers.get(name)
        if got is None or self.sizes[name] != size:
            if got is not None:
                self._retired.append(got)
            got = self.buffers[name] = gamedraw.uniform_buffer(self.rhi, size)
            self.sizes[name] = size
            self.sent.pop(name, None)
            self.stamps.pop(name, None)
        return got

    def release(self) -> None:
        for buf in (*self.buffers.values(), *self._retired, *self._made.values()):
            buf.destroy()
        self.buffers, self.sizes, self.sent, self.stamps, self._retired, self._made = {}, {}, {}, {}, [], {}


class FrameInstances:
    """A frame's instance data of every draw in one buffer each (module docstring): ``begin`` a new frame, ``place``
    a draw's instances and ``place_casters`` a shadow draw's while the frame's draws are made, ``upload`` once before
    the passes (a still camera's frame keeps the last frame's); ``bindings`` are the buffers by the shaders' names,
    ``offsets`` the draws' slots. A buffer that grows is made anew and ``generation`` counts up: a binding set of an
    older one is made again (``PartPass``)."""

    def __init__(self, rhi) -> None:
        self.rhi = rhi
        self.buffers: dict[str, QRhiBuffer] = {}
        self.offsets: QRhiBuffer | None = None
        self.generation = 0
        self.adapt = False  # the frame hands the session's terrain: the models adapt to it (else none does)
        self.begin()

    def begin(self) -> None:
        self._parts: dict[str, list] = {kind: [] for kind in KINDS}
        self._counts = dict.fromkeys(KINDS, 0)
        self._slots: list[int] = []
        # (id of the worlds, joints, deform) -> (record, bone, the worlds kept); its slot (a prepass and its draw); its
        # shadow slot and words (each mesh's)
        self._records: dict[tuple, tuple] = {}
        self._drawn: dict[tuple, int] = {}
        self._cast: dict[tuple, tuple] = {}
        # per kind of words ("words", "culled") the draws' (mask, joints, record, bone): made at the upload
        self._later: dict[str, list] = {"words": [], "culled": []}
        self._sent = False

    def _append(self, kind: str, rows: np.ndarray) -> int:
        at = self._counts[kind]
        self._parts[kind].append(rows)
        self._counts[kind] += len(rows)
        return at

    def _defer(self, kind: str, mask: np.ndarray, joints: int, record: int, bone: int) -> tuple[int, int]:
        """Words of ``kind`` made with the upload (``culled_rows``): where they start and how many."""
        at, n = self._counts[kind], int(np.count_nonzero(mask))
        self._counts[kind] += n
        self._later[kind].append((mask, joints, record, bone))
        return at, n

    def _slot(self, offset: int) -> int:
        self._slots.append(offset)
        return (len(self._slots) - 1) * SLOT

    def _placed(self, worlds: np.ndarray, joints: int, deform: bool = False) -> tuple[int, int]:
        """Where the records and bones of ``worlds`` start (written once a frame: every pass's draws name them)."""
        key = (id(worlds), joints, deform)
        got = self._records.get(key)
        if got is None:
            record = self._append("records", instance_records(worlds, deform=deform, adapt=self.adapt))
            bone = self._append("bones", bone_rows(worlds, joints))
            got = self._records[key] = (record, bone, worlds)  # the worlds kept: their id stays theirs this frame
        return got[0], got[1]

    def place(self, worlds: np.ndarray, joints: int = 0, deform: bool = False) -> int:
        """A draw of ``worlds`` ([n, 4, 4], column vectors; ``joints``: a skin's bones, 0 rigid; ``deform``: bent to
        the terrain): their words, each naming its record and first bone; the byte offset of its slot."""
        key = (id(worlds), joints, deform)
        if key not in self._drawn:
            record, bone = self._placed(worlds, joints, deform)
            at, _n = self._defer("words", np.ones((len(worlds), 1), bool), joints, record, bone)  # cascade 0
            self._drawn[key] = self._slot(at)
        return self._drawn[key]

    def place_casters(
        self, worlds: np.ndarray, joints: int, mask: np.ndarray, deform: bool = False
    ) -> tuple[int, Culled]:
        """A shadow draw of ``worlds``, each instance into the cascades ``mask`` ([n, cascades]) marks: its slot's
        byte offset and its culled words (``Culled``: one an instance and cascade, the draw's instance count; written
        with the frame's upload, ``culled_rows``); the meshes of a part's level share them."""
        mask = np.asarray(mask, bool)
        key = (id(worlds), joints, deform, mask.tobytes())  # a cut-out's draws take one cascade each
        if key not in self._cast:
            record, bone = self._placed(worlds, joints, deform)
            at, n = self._defer("culled", mask, joints, record, bone)
            self._cast[key] = (self._slot(at), Culled(n))
        return self._cast[key]

    def bindings(self) -> dict:
        """The frame's buffers by the names the shaders read them by (made at their first size when missing)."""
        for kind in KINDS:
            self._fit(kind, 0)
        self._fit_offsets(1)
        return {name: self.buffers[kind] for kind, (_size, names) in KINDS.items() for name in names}

    def _fit(self, kind: str, size: int) -> None:
        buf = self.buffers.get(kind)
        if buf is not None and buf.size() >= size:
            return
        if buf is not None:  # grown with room: each growth makes every part's binding set again
            buf.destroy()
            self.generation += 1
            size *= GROWTH
        buf = self.rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, _grown(size))
        buf.create()
        self.buffers[kind] = buf

    def _fit_offsets(self, slots: int) -> None:
        if self.offsets is not None and self.offsets.size() >= slots * SLOT:
            return
        if self.offsets is not None:
            self.offsets.destroy()
            self.generation += 1
            slots *= GROWTH
        self.offsets = gamedraw.uniform_buffer(self.rhi, _grown(slots * SLOT))

    def upload(self, batch) -> None:
        """The frame's words, records, bones and slots into their buffers (once a frame; grown first where needed)."""
        if self._sent:
            return
        self._sent = True
        for kind, later in self._later.items():  # the draws' and the casters' words, each kind at once
            if later:
                self._parts[kind], self._later[kind] = [culled_rows(later)], []
        for kind in KINDS:
            parts = self._parts[kind]
            data = np.concatenate(parts).tobytes() if parts else b""
            self._fit(kind, len(data))
            if data:
                batch.uploadStaticBuffer(self.buffers[kind], 0, len(data), data)
        slots = np.zeros((len(self._slots), SLOT // 4), np.uint32)
        slots[:, 0] = self._slots
        self._fit_offsets(len(self._slots))
        if len(self._slots):
            batch.updateDynamicBuffer(self.offsets, 0, slots.tobytes())

    def release(self) -> None:
        for buf in (*self.buffers.values(), self.offsets):
            if buf is not None:
                buf.destroy()
        self.buffers, self.offsets = {}, None
        self.begin()


def _grown(size: int) -> int:
    """A buffer's size for ``size`` bytes: the next power of two, ``SMALLEST`` at least."""
    return max(SMALLEST, 1 << max(0, int(size) - 1).bit_length())


class PartInputs(NamedTuple):
    """What the passes of one part (a material) share, the owner's: the material, the frame's instance buffers, the
    textures the owner hands by name, the material's arrays (``maps``: made by the first pass that draws) and their
    table, and ``meshes(mesh, batch)``: the vertex and index buffers of a mesh (one copy for every pass)."""

    material: gamedraw.Material
    frame: FrameInstances
    textures: dict
    maps: list
    table: gamedraw.TextureTable
    meshes: Callable | None = None


class PartPass(gamedraw.GameModel):
    """One part (``PartInputs``) through one program of a pass: one binding set with the frame's instance buffers and
    ``cbModel`` at a draw's slot, its constant buffers written once a frame (``prepare``), per mesh its per-vertex
    inputs and a pipeline of the program's (``mesh_inputs``)."""

    def __init__(self, rhi, program, state, part: PartInputs) -> None:
        super().__init__(rhi, program, None, part.material, None, state, part.table)
        self.frame, self.textures, self.maps, self.meshes = part.frame, part.textures, part.maps, part.meshes
        self.srb = None
        self.slot_binding: int | None = None  # ``cbModel``'s binding: the draw's slot is its dynamic offset
        self.inputs: dict[int, tuple] = {}  # id of a mesh -> (vertex buffers, index buffer, pipeline, its key, mesh)
        self._stamp: tuple | None = None  # the camera and pass inputs its constants hold (``stamp_of``)
        self._shared: dict = {}  # what the pass and the owner hand by name (bound again when the frame's grow)
        self._generation = -1
        self._own: dict[tuple, object] = {}  # its samplers, stand-ins and scratch buffers: made once for every set
        self._shared_key: tuple = ()  # the frame's resources it was bound with (``_key_of``)

    def _cbuffer(self, name: str, number: int, stages):
        if name != SLOT_BUFFER:
            return super()._cbuffer(name, number, stages)
        if self.program.cbuffers[name].size > SLOT:  # the engine's is a root constant: a draw's slot must hold it
            raise RuntimeError(f"{name} is larger than a draw's slot ({SLOT} bytes)")
        self.slot_binding = number
        return gamedraw.B.uniformBufferWithDynamicOffset(number, stages, self.frame.offsets, SLOT)

    def _once(self, kind: str, name: str, make):
        key = (kind, name)
        if key not in self._own:
            self._own[key] = make()
        return self._own[key]

    def _sampler(self, name: str):
        return self._once("sampler", name, lambda: super(PartPass, self)._sampler(name))

    def _scratch(self, name: str):
        return self._once("scratch", name, lambda: super(PartPass, self)._scratch(name))

    def _texture(self, name: str, shared: dict, white):
        if name in shared:
            return shared[name]
        return self._once("texture", name, lambda: super(PartPass, self)._texture(name, shared, white))

    def _make(self, batch, shared: dict) -> None:
        if not self.maps:
            self.maps.extend(self.table.create(self.rhi, batch))
            self.table.drop_pixels()  # on the device now: every pass binds the same arrays
            self.material.maps.clear()
        self.arrays = self.maps
        self.constants, self.buffers, self._sent, self._own = {}, {}, {}, {}
        self.white = self._white(batch)
        self._bind(shared)

    def _bind(self, shared: dict) -> None:
        """The binding set, made again when the frame's buffers or the resources the pass hands were made anew
        (the pipelines stay: alike)."""
        if self.srb is not None:
            self.keep.remove(self.srb)
            self.srb.destroy()
        self._shared = {**self.textures, **shared}
        self.srb = self._bindings({**self._shared, **self.frame.bindings()}, self.white)
        self._generation, self._shared_key = self.frame.generation, _key_of(shared)

    def prepare(self, rp, signature, batch, camera: gs.Camera, fill=None, shared: dict | None = None) -> None:
        """Made when new; its constants written once for a camera and the pass's inputs (the fill's ``common_key``
        or ``key``; a fill without one: every time), a buffer sent only when it changed."""
        if not self.keep:
            self._make(batch, shared or {})
        elif self._generation != self.frame.generation or self._shared_key != _key_of(shared or {}):
            self._bind(shared or {})
        stamp = stamp_of(camera, fill, "common_key")
        if stamp is not None and stamp == self._stamp:
            return
        for name, c in self.constants.items():
            gs.fill_frame(c, camera)
            if fill is not None:
                fill(c)
            for path, value in self.values.items():  # the owner's (a cut-out's cascade)
                c.set(path, value)
            self._send(batch, name, bytes(c.data))
        self._stamp = stamp

    def mesh_inputs(self, mesh: gamedraw.Mesh, rp, signature, batch) -> tuple:
        """A mesh's vertex buffers (the owner's, and the inputs it lacks), index buffer and pipeline, made once."""
        got = self.inputs.get(id(mesh))
        if got is None:
            vertices, indices = self.meshes(mesh, batch)
            fill, attrs = gamedraw.vertex_input(mesh, self.program.vs.inputs)
            fill_buf = self._buffer(batch, QRhiBuffer.UsageFlag.VertexBuffer, fill)
            pipeline, key = self._shared_pipeline(rp, signature, attrs, mesh.stride, len(fill) // max(1, mesh.count))
            got = self.inputs[id(mesh)] = ([vertices, fill_buf], indices, pipeline, key, mesh)
        return got

    def release(self) -> None:
        for _vbufs, _ibuf, _pipeline, key, _mesh in self.inputs.values():
            self._give_back(key)
        super().release()
        self.inputs, self.srb, self._stamp, self._generation, self._own = {}, None, None, -1, {}


def _key_of(shared: dict) -> tuple:
    """Which resources ``shared`` hands by name (a shadow map, a light buffer, the water's copies: made anew when
    they grow or come in, the old ones destroyed)."""
    return tuple((name, id(value)) for name, value in shared.items())


class InstanceRun:
    """One draw of a ``PartPass``: a mesh, the instances it draws (``worlds``: the transparent pass orders by their
    box), its slot (the byte offset of its words' start), and for a shadow draw its culled ``words``."""

    world = np.identity(4)

    def __init__(self, setup: PartPass, mesh: gamedraw.Mesh, slot: int, worlds: np.ndarray, words=None,
                 viewport: tuple | None = None) -> None:  # fmt: skip
        self.setup, self.mesh, self.slot, self.worlds, self.words = setup, mesh, slot, worlds, words
        self.viewport = viewport  # a share of the target (``share_rect``): one cascade's part of the shadow map
        self._inputs: tuple | None = None

    @property
    def instances(self) -> int:
        return len(self.worlds)

    def fresh(self) -> bool:
        """Whether its first prepare is still to come: the part's resources or this mesh's inputs are not made."""
        return not self.setup.keep or id(self.mesh) not in self.setup.inputs

    def prepare(self, rp, signature, batch, camera: gs.Camera, fill=None, shared: dict | None = None) -> None:
        self.setup.prepare(rp, signature, batch, camera, fill, shared)
        self._inputs = self.setup.mesh_inputs(self.mesh, rp, signature, batch)
        if self._inputs[2] is None:
            raise RuntimeError(self.setup.error or "no pipeline")

    def draw(self, cb, size: tuple[int, int], instances: int | None = None) -> None:
        vbufs, ibuf, pipeline, _key, mesh = self._inputs
        count = self.instances if instances is None else instances
        offset = (self.setup.slot_binding, self.slot) if self.setup.slot_binding is not None else None
        area = size if self.viewport is None else gamedraw.share_rect(size, self.viewport)
        mesh.draw(cb, pipeline, self.setup.srb, area, (vbufs, ibuf), count, offset, self.setup.state)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The box of the instances' places: the transparent pass sorts by it."""
        places = np.asarray(self.worlds, float)[:, :3, 3]
        return places.min(axis=0), places.max(axis=0)
