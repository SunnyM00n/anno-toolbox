"""Anno 117's distance clusters drawn through the native backend by the game's shipped ``distance_cluster.fx``: the
G-buffer's ``DistanceClusterDeferred`` and, per cascade, ``DistanceClusterShadow`` into the cascade's part of the
shadow map; the atlases and material records of ``distanceclusters.CClusterAtlas`` on the device; per region one
vertex and index buffer, and every region's ``cbDistanceCluster`` in one buffer whose record a draw's root offset
picks. The draws are ``nativedraws.CNativeRun``s the native objects record in their passes.

``shaders(natives)`` finds the shaders (in the background); ``CNativeClusters(objects, found)`` is the device side:
``put`` uploads a region's mesh, ``runs`` gives a frame's draws. docs/rendering/distance-clusters.md.
"""

from __future__ import annotations

import struct

from . import gameshadow, nativelayout, renderstates
from .distanceclusters import CClusterAtlas, SClusterMesh, cluster_record
from .gameinstances import SDrawnSet
from .nativedraws import CNativeRun
from .objectsnative import PARTITION, TARGETS, CNativePart, STechnique, pass_state

STEM = "distance_cluster"
ROLES = {"main": ("vsDistanceCluster", "psDistanceClusterDeferred"),
         "shadow": ("vsDistanceClusterShadow", "psDistanceClusterShadow")}  # fmt: skip
RECORD_NAME = "cbDistanceCluster"  # a root view: each draw's record of one buffer
RECORD_BYTES = 256  # a constant buffer view's alignment: a region's record
MAX_REGIONS = 4096  # records in the buffer [ours]: a continental island has a few hundred regions
MATERIAL_BYTES = 16
ELEMENTS = ((5, 4, 1, 0), (4, 4, 1, 4))  # COLOR0 and TEXCOORD0, one uint each (``VSInDistanceCluster``)
ONE = SDrawnSet(None, None, None, 1)  # a region is one draw of one instance
FORMATS = {"diff_alpha": "BC3_UNORM", "gloss_metal": "BC1_UNORM"}  # the shader turns the diffuse into linear itself
# ``cNoiseTex`` for the trees' noise [I]: the engine's file for it is not read; 0.5 is where the shader keeps the
# trees' albedo as it is ("balanced around 1.0"), so they lose the pattern, not their brightness
NOISE_BALANCE = 128


def shaders(natives) -> dict:
    """The shipped vertex and pixel shaders per role and their reflections (``KeyError`` when the build lacks them)."""
    variant = natives.variant(STEM)
    out = {}
    for role, functions in ROLES.items():
        stages = {
            stage: natives.call(variant, function) for stage, function in zip(("vs", "ps"), functions, strict=True)
        }
        out[role] = (stages, tuple(natives.reflection(stages[stage]) for stage in ("vs", "ps")))
    return out


class SClusterMeshOnDevice:
    """A region's mesh on the device, shaped as ``objectsnative`` makes a mesh's pipeline: its vertex layout and its
    index count (``m_indices``), its vertices and indices (pieces of the objects' pool), its record's slot."""

    m_elements = ELEMENTS
    m_stride = 8

    def __init__(self, vertices, indices, index_count: int, slot: int, merged: SClusterMesh) -> None:
        self.m_vertices, self.m_index_buffer, self.m_slot, self.m_merged = vertices, indices, slot, merged
        self.m_indices = range(index_count)  # its length is what a draw takes
        self.m_runs: dict[str, object] = {}  # role -> its draw (``CNativeClusters._run``)


class CNativeClusters:
    """The clusters on the native backend (module docstring); the part a frame's ``CNativeRun``s name."""

    m_native = True
    m_is_transparent = False
    m_cutout = False
    item = CNativePart.item  # a draw made once per role and region, made again when its sets change
    m_push = b""

    def __init__(self, objects, found: dict) -> None:
        self.m_objects = objects
        device, command_list = objects.m_device, objects.m_list
        self.m_records = device.buffer(MAX_REGIONS * RECORD_BYTES, "constant", name="cluster records")
        self.m_materials = device.buffer(MATERIAL_BYTES * (1 << 14), "structured", stride=MATERIAL_BYTES,
                                         name="cluster materials")  # fmt: skip
        side = 2048
        self.m_atlas = {kind: device.texture(side, side, texture_format, mips=4, name=f"cluster {kind}")
                        for kind, texture_format in FORMATS.items()}  # fmt: skip
        self.m_noise = device.texture(4, 4, "RGBA8_UNORM", name="cluster noise")
        command_list.write_texture(self.m_noise, bytes([NOISE_BALANCE]) * 64, 16)
        command_list.set_state(self.m_noise, "shader_resource")
        own = {RECORD_NAME: self.m_records, "cClusterMaterialBuffer": self.m_materials, "cNoiseTex": self.m_noise,
               "cClusterDiffAlphaTex": self.m_atlas["diff_alpha"], "cClusterGlossMetalTex": self.m_atlas["gloss_metal"]}  # fmt: skip
        self.m_group, self.m_index = objects.m_tables.add([], {}, {})  # a material group of no structures
        self.m_techniques = {"main": self._technique(*found["main"], renderstates.GBUFFER_STATE, "gbuffer", own)}
        for cascade in range(gameshadow.COUNT):
            self.m_techniques[f"shadow{cascade}"] = self._technique(*found["shadow"], renderstates.GBUFFER_STATE,
                                                                    "shadow", own, cascade)  # fmt: skip
        self.m_items: dict = {}
        self._m_meshes: dict[int, SClusterMeshOnDevice] = {}  # record slot -> the region's mesh holding it
        self._m_free = list(range(MAX_REGIONS - 1, -1, -1))
        self._m_written: dict[int, bytes] = {}  # slot -> the record last written there
        self.m_atlas_serial = -1
        self.m_material_bytes = 0
        command_list.set_state(self.m_atlas["diff_alpha"], "shader_resource")
        command_list.set_state(self.m_atlas["gloss_metal"], "shader_resource")

    def _technique(self, stages: dict, reflections: tuple, state, target: str, own: dict, cascade=None) -> STechnique:
        """A role's technique as ``CNativeObjects.technique`` makes one, ``cbDistanceCluster`` a root view."""
        objects = self.m_objects
        plan = nativelayout.SBindingPlan.of(list(reflections), root_views=(RECORD_NAME,))
        shaders_made = (objects.shader("vs", stages["vs"]), objects.shader("ps", stages["ps"]))
        bindings = {binding.name: binding for reflection in reflections for binding in reflection.bindings}
        cbuffers = {cbuffer.name: cbuffer for reflection in reflections for cbuffer in reflection.cbuffers}
        technique = STechnique(plan, [objects.layout(spec) for spec in plan.layouts], shaders_made, stages["vs"].bytecode,
                               bindings, cbuffers, pass_state(state, TARGETS[target], objects.m_depth))  # fmt: skip
        technique.named.update(own)
        if cascade is not None and PARTITION in cbuffers:  # ``cCurrentCascade``, the cascade's own buffer
            data = struct.pack("<I", cascade).ljust(RECORD_BYTES, b"\0")
            technique.named[PARTITION] = objects.static(data, "constant", f"cluster cascade {cascade}")
        return technique

    def sync(self, atlas: CClusterAtlas) -> None:
        """The atlases and the material records the device lacks, on the module's list (sent with the passes)."""
        command_list = self.m_objects.m_list
        if atlas.m_serial != self.m_atlas_serial:
            for kind, texture in self.m_atlas.items():
                for mip, (data, pitch) in enumerate(atlas.levels(kind)):
                    command_list.write_texture(texture, data, pitch, mip=mip)
                command_list.set_state(texture, "shader_resource")
            self.m_atlas_serial = atlas.m_serial
        if len(atlas.m_records) != self.m_material_bytes:
            command_list.write_buffer(self.m_materials, bytes(atlas.m_records))
            self.m_material_bytes = len(atlas.m_records)

    def put(self, merged: SClusterMesh) -> SClusterMeshOnDevice | None:
        """A region's mesh on the device (None when every record is taken)."""
        if not self._m_free:
            return None
        pool, command_list = self.m_objects.m_pool, self.m_objects.m_list  # CNativePart.item binds pool pieces
        vertices, indices = pool.place(command_list, merged.vertices), pool.place(command_list, merged.indices)
        mesh = SClusterMeshOnDevice(vertices, indices, merged.triangle_count * 3, self._m_free.pop(), merged)
        self._m_meshes[mesh.m_slot] = mesh
        return mesh

    def mesh(self, mesh) -> tuple:
        """(vertices, indices, mesh) as ``CNativePart.item`` takes them."""
        return mesh.m_vertices, mesh.m_index_buffer, mesh

    def drop(self, mesh: SClusterMeshOnDevice) -> None:
        """A region's mesh let go (its region was built again or left the island)."""
        if self._m_meshes.get(mesh.m_slot) is not mesh:
            return
        del self._m_meshes[mesh.m_slot]
        for role in self.m_techniques:
            self.m_items.pop((role, id(mesh)), None)  # pattern: ok (CNativePart.item's key, the mesh still held)
        self._free(mesh)
        self._m_written.pop(mesh.m_slot, None)
        self._m_free.append(mesh.m_slot)

    def _free(self, mesh: SClusterMeshOnDevice) -> None:
        for piece in (mesh.m_vertices, mesh.m_index_buffer):
            self.m_objects.m_pool.free(piece)

    def runs(self, drawn: list[tuple[SClusterMeshOnDevice, float]], reach) -> tuple[list, list]:
        """A frame's draws of the regions ``drawn`` ((mesh, its fade)): into the G-buffer and into each cascade
        ``reach`` marks for it ([n, cascades] bool; None: no shadow), their records written where they changed."""
        command_list = self.m_objects.m_list
        gbuffer = []
        for mesh, alpha in drawn:
            record = cluster_record(mesh.m_merged, alpha)
            if self._m_written.get(mesh.m_slot) != record:
                command_list.write_buffer(self.m_records, record, mesh.m_slot * RECORD_BYTES)
                self._m_written[mesh.m_slot] = record
            gbuffer.append(self._run(mesh, "main"))
        casters = []  # cascade by cascade: the backend records a cascade's draws in one go (its viewport)
        for cascade in range(gameshadow.COUNT) if reach is not None else ():
            casters += [self._run(mesh, f"shadow{cascade}", gameshadow.DIVISIONS[cascade])
                        for k, (mesh, _alpha) in enumerate(drawn) if reach[k, cascade]]  # fmt: skip
        return gbuffer, casters

    def _run(self, mesh: SClusterMeshOnDevice, role: str, viewport=None) -> CNativeRun:
        """A region's draw in ``role``, made once (a frame hands the same ones to the backend)."""
        found = mesh.m_runs.get(role)
        if found is None:
            found = mesh.m_runs[role] = CNativeRun(self, role, mesh, mesh.m_slot * RECORD_BYTES, ONE, None, viewport)
        return found

    def release(self) -> None:
        for mesh in self._m_meshes.values():
            self._free(mesh)
        for technique in self.m_techniques.values():
            partition = technique.named.get(PARTITION)
            if partition is not None:
                partition.release()
        for resource in (self.m_records, self.m_materials, self.m_noise, *self.m_atlas.values()):
            resource.release()
        self.m_objects.m_tables.remove(self.m_group, self.m_index)
        self._m_meshes, self.m_items, self.m_techniques = {}, {}, {}
