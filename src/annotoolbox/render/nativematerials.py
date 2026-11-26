"""The model shaders' material inputs on the native backend as the engine binds them: every map of the island in one
bindless table (``cBindlessTextures[]``, space 1), the material structures as structured buffers of all materials
(``cMaterialComponentIndices``, ``cModelCommon``, ``cPhysicallyBasedSubstance`` ..., space 2) indexed by the draw's
``cGlobalMaterialIndex``, and the stand-ins of what no pass hands, by the kind the shader declares.

``CBindlessMaps(device, lst)`` holds the table (``slot(path, dds)``), ``CMaterialTables(device)`` the structures
(``add(structured, fields, slots)``), ``CStandIns(device, lst)`` the stand-ins. docs/rendering/native-backend.md,
sections 1 and 11.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

from . import gamemaps
from . import gameshader as gs
from .bakenative import CStored
from .nativecompute import records

SPACE, REGISTER = 1, 3  # cBindlessTextures: t3 of space 1, unbounded
CAPACITY = 1 << 14  # the table's descriptors: an island's distinct maps [ours]
NULL = 0  # the engine's null texture: white (gametextures.CTextureTable)
KEEP_FRAMES = 4  # frames a slot let go stays unused (QRhi's frames in flight may still read it) [ours]
# a frame's map uploads stop past this (one always goes up): a first visit's maps came in one frame, up to 80 ms on
# the medium island (performance.md section 4, "What is left") [ours]
MAPS_BUDGET_S = 0.004
INDEX_TABLE = "cMaterialComponentIndices"
NO_COMPONENT = 0xFFFFFFFF  # a component the material lacks
# D3D_SRV_DIMENSION -> the module's texture kind of a zeroed stand-in (a plain float 2D texture stands in white)
DIMENSIONS = {5: "2darray", 8: "3d", 9: "cube", 10: "cubearray"}
INTEGER = (3, 4)  # D3D_RESOURCE_RETURN_TYPE: sint, uint
# D3D_SIF_TEXTURE_COMPONENTS: a float4 texture (``Texture2D``, ``Texture2D<float4>``) stands in white, one of fewer
# components zeros, as the current path's ``CGameModel._texture`` (a wetness or mask map then reads none)
FOUR_COMPONENTS = 0x4 | 0x8
SMALLEST = 16  # a table's first capacity in elements (grown to the next power of two)
_UNNAMED = itertools.count()


class CBindlessMaps:
    """Every map the island's materials name, once per file, at a slot of one descriptor table that all model draws
    bind (``slot``); a slot let go (``give_back``) is taken again some frames later. Uploaded on ``command_list`` (the
    module's), as stored (``bakenative.CStored``), at most ``max_size`` texels a side (``gamemaps.smaller``)."""

    def __init__(self, device, command_list, max_size: int = 0) -> None:
        self.m_device, self.m_list, self.m_max_size = device, command_list, max_size
        self.m_layout = device.bindless_layout([("texture", SPACE)], first=REGISTER, capacity=1 << 16)
        self.m_table = device.descriptor_table(self.m_layout, CAPACITY)
        self.m_white = device.texture(4, 4, "RGBA8_UNORM", name="null map")
        command_list.write_texture(self.m_white, bytes([255]) * 64, 16)
        command_list.set_state(self.m_white, "shader_resource")
        self.m_held: dict[str, list] = {}  # path -> [slot, texture (None till ``fill``), users, its levels till then]
        self.m_free: list[int] = []
        self.m_cooling: list[tuple[int, int]] = []  # (frame let go, slot)
        self.m_frame = 0
        self.m_spent_s = 0.0  # this frame's time in ``fill``
        self.m_next = 1
        for slot in range(CAPACITY):  # every descriptor valid: the shaders index the whole range
            device.write_table(self.m_table, ("texture", slot, self.m_white))

    def slot(self, path: str, dds) -> int:
        """The table's slot of a map file (``dds`` its ``DdsFile``, kept until ``fill`` uploads it: white till then);
        the null slot when the table is full."""
        held = self.m_held.get(path)
        if held is not None:
            held[2] += 1
            return held[0]
        if self.m_free:
            at = self.m_free.pop()
        elif self.m_next < CAPACITY:
            at, self.m_next = self.m_next, self.m_next + 1
        else:
            return NULL
        self.m_held[path] = [at, None, 1, gamemaps.smaller(dds, self.m_max_size)]
        return at

    def fill(self, paths) -> bool:
        """Upload the files of ``paths`` not up yet and put them at their slots; True once every one is up. It runs
        when a part that names them first draws, as the current path's pool makes a file's array at its first draw (a
        part made ahead and never drawn holds no map: 300 MB on Egypt's quadrant, native-backend.md 16.9). No frame
        read those slots before. A frame uploads for ``MAPS_BUDGET_S``: the rest go up in the next frames."""
        for path in paths:
            held = self.m_held.get(path)
            if held is None or held[1] is not None:
                continue
            if self.m_spent_s >= MAPS_BUDGET_S:
                return False
            started_s = time.perf_counter()
            texture = CStored(held[3]).upload(self.m_device, self.m_list, path)
            self.m_list.set_state(texture, "shader_resource")  # a table's maps are tracked by no binding set
            self.m_device.write_table(self.m_table, ("texture", held[0], texture))
            held[1], held[3] = texture, None
            self.m_spent_s += time.perf_counter() - started_s
        return True

    def slots(self, material, read: set | frozenset | None = None) -> tuple[dict[str, int], list[str]]:
        """(a material's maps by parameter as their slots, the files taken: what ``give_back`` lets go), only those
        ``read`` names (the members of the material structures its shaders read; None: all): a map no structure
        names is never sampled, and the current path never uploads it either. Its maps let go after (the table holds
        them now)."""
        out, taken = {}, []
        for name, dds in material.maps.items():
            if read is not None and name not in read:
                continue
            path = material.paths.get(name) or f"<map {next(_UNNAMED)}>"
            out[name] = self.slot(path, dds)
            taken.append(path)
        material.maps.clear()
        return out, taken

    def give_back(self, paths) -> None:
        """One user fewer of each map; one without users lets its texture go (late) and its slot cool."""
        for path in paths:
            held = self.m_held.get(path)
            if held is None:
                continue
            held[2] -= 1
            if held[2] > 0:
                continue
            del self.m_held[path]
            if held[1] is not None:
                self.m_device.write_table(self.m_table, ("texture", held[0], self.m_white))
                held[1].release()
            self.m_cooling.append((self.m_frame, held[0]))

    def end_frame(self) -> None:
        self.m_frame, self.m_spent_s = self.m_frame + 1, 0.0
        while self.m_cooling and self.m_frame - self.m_cooling[0][0] > KEEP_FRAMES:
            self.m_free.append(self.m_cooling.pop(0)[1])

    def release(self) -> None:
        for held in self.m_held.values():
            if held[1] is not None:
                held[1].release()
        for resource in (self.m_white, self.m_table, self.m_layout):
            resource.release()
        self.m_held = {}


def element_layout(constant_buffer) -> tuple:
    """What a structured buffer's element looks like (its size and members): tables alike in it take the same
    records."""

    def members(variable) -> tuple:
        return tuple(
            (member.name, member.offset, member.size, member.type, member.elements, members(member))
            for member in variable.members
        )

    return (
        constant_buffer.name,
        constant_buffer.size,
        tuple(members(variable) for variable in constant_buffer.variables),
    )


@dataclass
class STableGroup:
    """The tables of materials whose shaders lay their structures out alike: one record per material in each
    (``records``: the bytes, ``buffers``: on the device, ``capacity`` records each); ``generation`` counts their
    growth (a binding set of older buffers is made again)."""

    layouts: dict  # name -> its reflection (the element: ``$Element``)
    records: dict = field(default_factory=dict)  # name -> bytearray of every material's record
    buffers: dict = field(default_factory=dict)
    count: int = 0
    capacity: int = 0
    dirty: set = field(default_factory=set)  # (name, index) records to send
    generation: int = 0
    free: list = field(default_factory=list)  # indices of materials let go, taken by the next ones


class CMaterialTables:
    """The material structures of every material on the device (module docstring): ``add`` gives a material its
    index in its group's tables, ``send(lst)`` writes what changed, ``group`` the buffers by name for the sets."""

    def __init__(self, device) -> None:
        self.m_device = device
        self.m_groups: dict[tuple, STableGroup] = {}

    def add(self, structured: list, fields: dict, slots: dict) -> tuple[tuple, int]:
        """A material (``fields``: structure -> field -> value, ``slots``: its maps' parameters -> bindless slots)
        into the tables its shaders read (``structured``: their space-2 reflections); (its group, its index)."""
        tables = {constant_buffer.name: constant_buffer for constant_buffer in structured}
        key = tuple(sorted(element_layout(constant_buffer) for constant_buffer in tables.values()))
        group = self.m_groups.get(key)
        if group is None:
            group = self.m_groups[key] = STableGroup(tables)
        reused = bool(group.free)
        index = group.free.pop() if reused else group.count
        group.count += not reused
        used = {"c" + field.removesuffix("Struct") for field in fields}
        for name, constant_buffer in tables.items():
            constants = gs.CConstants(records(constant_buffer, 1))
            if name == INDEX_TABLE:
                for member in constant_buffer.variables[0].members:
                    on = "c" + member.name.removesuffix("Index") in used
                    constants.set(f"{name}[0].{member.name}", index if on else NO_COMPONENT)
            elif name in used:  # its fields and the maps' slots (a structure of maps alone has no other field)
                for k, value in {**fields[name[1:] + "Struct"], **slots}.items():
                    constants.set(f"{name}[0].{k}", value)
            record = group.records.setdefault(name, bytearray())
            if reused:
                record[index * constant_buffer.size : (index + 1) * constant_buffer.size] = constants.m_data[
                    : constant_buffer.size
                ]
            else:
                record.extend(constants.m_data[: constant_buffer.size])  # the stride: no padding to 16
            group.dirty.add((name, index))
        return key, index

    def remove(self, key: tuple, index: int) -> None:
        """A material's record let go: the next ``add`` of its group takes the index (the device's copy is written on
        the module's list, which the queue runs after every frame that read the old one)."""
        group = self.m_groups.get(key)
        if group is not None:  # the tables released first: nothing to free
            group.free.append(index)

    def send(self, command_list) -> None:
        """The records added since into their buffers (a group grown: its buffers made again, all of it sent)."""
        for group in self.m_groups.values():
            if not group.dirty:
                continue
            if group.count > group.capacity:
                for buffer in group.buffers.values():
                    buffer.release()
                group.capacity = max(SMALLEST, 1 << (group.count - 1).bit_length())
                group.buffers = {name: self.m_device.buffer(constant_buffer.size * group.capacity, "structured", stride=constant_buffer.size,
                                                          name=name) for name, constant_buffer in group.layouts.items()}  # fmt: skip
                group.generation += 1
                for name, buffer in group.buffers.items():
                    command_list.write_buffer(buffer, bytes(group.records[name]))
            else:
                for name, index in sorted(group.dirty):
                    size = group.layouts[name].size
                    command_list.write_buffer(group.buffers[name], bytes(group.records[name][index * size : (index + 1) * size]),
                                     index * size)  # fmt: skip
            group.dirty.clear()

    def group(self, key: tuple) -> STableGroup:
        return self.m_groups[key]

    def release(self) -> None:
        for group in self.m_groups.values():
            for buffer in group.buffers.values():
                buffer.release()
        self.m_groups = {}


class CStandIns:
    """What a draw binds where no pass hands a resource (``-all-resources-bound``; NVRHI's validation refuses a
    set that leaves one out), each made once: white for a plain float 2D texture, zeros of the declared kind
    otherwise (``of``), a zeroed structured buffer of a stride, a raw storage buffer (the texture feedback)."""

    def __init__(self, device, command_list) -> None:
        self.m_device, self.m_list = device, command_list
        self.m_made: dict[tuple, object] = {}

    def texture(self, binding):
        """The stand-in of a texture binding (``dxc.SBinding``: its dimension, return type and components)."""
        kind = DIMENSIONS.get(binding.dimension, "2d")
        integer = binding.returns in INTEGER
        if kind == "2d" and not integer and binding.flags & FOUR_COMPONENTS == FOUR_COMPONENTS:
            return self._made(("white",), lambda: self._filled("2d", "RGBA8_UNORM", 255))
        texture_format = "R32_UINT" if integer else "RGBA8_UNORM"
        return self._made((kind, texture_format), lambda: self._filled(kind, texture_format, 0))

    def structured(self, stride: int):
        return self._made(("structured", stride), lambda: self._zeros(stride * 64, "structured", stride))

    def raw(self, writable: bool):
        usage = "raw,uav" if writable else "raw"
        return self._made(("raw", writable), lambda: self._zeros(4096, usage, 0))

    def _made(self, key, make):
        found = self.m_made.get(key)
        if found is None:
            found = self.m_made[key] = make()
        return found

    def _zeros(self, size_bytes: int, usage: str, stride: int):
        buffer = self.m_device.buffer(size_bytes, usage, stride=stride, name="stand-in")
        self.m_list.write_buffer(buffer, bytes(size_bytes))
        return buffer

    def _filled(self, kind: str, texture_format: str, value: int):
        layers = 6 if kind.startswith("cube") else 4 if kind == "3d" else 1
        texture = self.m_device.texture(4, 4, texture_format, depth=layers, kind=kind, name=f"stand-in {kind}")
        for layer in range(1 if kind == "3d" else layers):
            data = bytes([value]) * 64 * (layers if kind == "3d" else 1)
            self.m_list.write_texture(texture, data, 16, layer=layer, depth_pitch=64)
        return texture

    def release(self) -> None:
        for resource in self.m_made.values():
            resource.release()
        self.m_made = {}
