"""Granny 2 ``.gr2`` files (Anno 1404's and 1701's meshes) read from their own structure: the header, the
section table, the sections (stored or Oodle1-compressed, ``grannyoodle``), the pointer fixups and the
type trees the file carries for every object in it.

``CGrannyFile(data).root()`` is the entry: the file's root object (Granny's file info: ``Meshes``,
``Materials``, ``Models``, ...) as a ``CGrannyObject``, a read-only mapping that reads a member when it is
asked for, so only the sections a caller touches are decompressed. Arrays of plain records (vertices,
indices) come back as ``SGrannyArray`` (raw bytes plus a ``struct`` format). docs/formats/granny.md.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property

from . import grannyoodle

# fmt: off
MAGICS = {  # the first 16 bytes -> pointer size (revision 7, little-endian; the only kinds Anno ships)
    bytes.fromhex("29de6cc0baa4532b25f5b7a5f666e2ee"): 4,
    bytes.fromhex("e59b495e6f631f141e13eba990beedc4"): 8,
}
(END, INLINE, REFERENCE, REFERENCE_TO_ARRAY, ARRAY_OF_REFERENCES, VARIANT_REFERENCE, _REMOVED,
 REFERENCE_TO_VARIANT_ARRAY, STRING, TRANSFORM, REAL32, INT8, UINT8, BINORMAL_INT8, NORMAL_UINT8, INT16,
 UINT16, BINORMAL_INT16, NORMAL_UINT16, INT32, UINT32, REAL16, EMPTY_REFERENCE) = range(23)
SCALARS = {  # member kind -> struct code
    REAL32: "f", INT8: "b", UINT8: "B", BINORMAL_INT8: "b", NORMAL_UINT8: "B", INT16: "h", UINT16: "H",
    BINORMAL_INT16: "h", NORMAL_UINT16: "H", INT32: "i", UINT32: "I", REAL16: "e",
}
# fmt: on
NONE, OODLE0, OODLE1 = 0, 1, 2  # section compression (3 and 4 are Bitknit, newer than Anno's files)
TRANSFORM_SIZE = 68  # flags, position (3 floats), orientation (4), scale and shear (3 x 3)
SECTION_ENTRY = 44  # eleven u32 per section
INFO_AT = 32  # the file info follows the 32-byte header


class CGrannyError(ValueError):
    """Not a Granny file this reader knows, or one whose structure does not hold together."""


class CGrannyMemberError(CGrannyError, KeyError):
    """An object without the member asked for: a key error too, as callers of a file without skeletons expect."""

    def __str__(self) -> str:
        return ValueError.__str__(self)


def member_stride(pointer: int) -> int:
    """The bytes of one member definition: kind, name, target, width, three reserved ints, one reserved pointer."""
    return 4 + 2 * pointer + 16 + pointer


def field_size(kind: int, pointer: int, inline_size: int = 0) -> int:
    """The bytes one element of a member of ``kind`` takes in a record (``inline_size``: an inline record's)."""
    if kind in SCALARS:
        return struct.calcsize("<" + SCALARS[kind])
    if kind == INLINE:
        return inline_size
    if kind in (REFERENCE, STRING, EMPTY_REFERENCE):
        return pointer
    if kind in (REFERENCE_TO_ARRAY, ARRAY_OF_REFERENCES):
        return 4 + pointer
    if kind == VARIANT_REFERENCE:
        return 2 * pointer
    if kind == REFERENCE_TO_VARIANT_ARRAY:
        return 2 * pointer + 4
    return TRANSFORM_SIZE if kind == TRANSFORM else 0


@dataclass(frozen=True)
class SSection:
    compression: int
    offset: int
    stored_size: int
    size: int
    stop0: int  # end of the 4-byte part (Oodle1 decodes the parts with separate models)
    stop1: int  # end of the 2-byte part
    fixup_offset: int
    fixup_count: int


@dataclass(frozen=True)
class SMember:
    """One member of a Granny type: its kind, name, the type it refers to (a reference) and its array width."""

    kind: int
    name: str
    target: tuple[int, int] | None
    width: int


class CGrannyFile:
    """A ``.gr2`` file's sections and pointers; ``root()`` is its root object."""

    def __init__(self, data: bytes) -> None:
        self.m_data = data
        if len(data) < INFO_AT + 40 or data[:16] not in MAGICS:
            raise CGrannyError(f"not a Granny 2 file this reader knows (magic {data[:16].hex()})")
        self.m_pointer = MAGICS[data[:16]]
        self.m_revision, self.m_total_size, _crc, table, count = struct.unpack_from("<5I", data, INFO_AT)
        if self.m_revision != 7:
            raise CGrannyError(f"Granny file format revision {self.m_revision}, not 7")
        self.m_root_type = struct.unpack_from("<II", data, INFO_AT + 20)
        self.m_root_object = struct.unpack_from("<II", data, INFO_AT + 28)
        self.m_tag = struct.unpack_from("<I", data, INFO_AT + 36)[0]
        self.m_sections = []
        if INFO_AT + table + SECTION_ENTRY * count > len(data):
            raise CGrannyError(f"a section table of {count} entries runs past the end of the file")
        for k in range(count):
            compression, offset, stored_size, size_bytes, _align, stop0, stop1, fixup_offset, fixup_count, _m, _nm = (
                struct.unpack_from("<11I", data, INFO_AT + table + SECTION_ENTRY * k)
            )
            self.m_sections.append(
                SSection(compression, offset, stored_size, size_bytes, stop0, stop1, fixup_offset, fixup_count)
            )
        self._m_bytes: dict[int, bytes] = {}
        self._m_fixups: dict[int, dict[int, tuple[int, int]]] = {}
        self._m_types: dict[tuple[int, int], list[SMember]] = {}

    def check(self) -> None:
        """Raise ``CGrannyError`` for a section this reader cannot decompress (without decompressing any)."""
        for k, section_entry in enumerate(self.m_sections):
            if section_entry.stored_size and section_entry.compression not in (NONE, OODLE1):
                raise CGrannyError(
                    f"section {k} uses compression {section_entry.compression} (only none and Oodle1 are read)"
                )
            if section_entry.offset + section_entry.stored_size > len(
                self.m_data
            ) or section_entry.fixup_offset + 12 * section_entry.fixup_count > len(self.m_data):
                raise CGrannyError(f"section {k} reaches past the end of the file")

    def section(self, k: int) -> bytes:
        """Section ``k`` decompressed (once)."""
        if k not in self._m_bytes:
            if not 0 <= k < len(self.m_sections):
                raise CGrannyError(f"a pointer into section {k} of {len(self.m_sections)}")
            entry = self.m_sections[k]
            raw = self.m_data[entry.offset : entry.offset + entry.stored_size]
            if entry.compression == NONE or not entry.stored_size:
                out = raw[: entry.size] + bytes(max(0, entry.size - len(raw)))
            elif entry.compression == OODLE1:
                try:
                    out = grannyoodle.decompress(raw, entry.size, entry.stop0, entry.stop1)
                except (grannyoodle.CGrannyCompressionError, IndexError, ZeroDivisionError) as exc:
                    raise CGrannyError(f"section {k} does not decompress: {exc}") from exc
            else:
                raise CGrannyError(f"section {k} uses compression {entry.compression} (only none and Oodle1 are read)")
            self._m_bytes[k] = out
        return self._m_bytes[k]

    def _fixups_of(self, k: int) -> dict[int, tuple[int, int]]:
        if k not in self._m_fixups:
            entry = self.m_sections[k]
            raw = self.m_data[entry.fixup_offset : entry.fixup_offset + 12 * entry.fixup_count]
            if len(raw) != 12 * entry.fixup_count:
                raise CGrannyError(f"section {k}: the fixup table reaches past the end of the file")
            table = struct.iter_unpack("<III", raw)
            self._m_fixups[k] = {source: (section_index, offset) for source, section_index, offset in table}
        return self._m_fixups[k]

    def pointer_at(self, address: tuple[int, int]) -> tuple[int, int] | None:
        """Where the pointer stored at ``address`` (section, offset) points; None for a null pointer."""
        return self._fixups_of(address[0]).get(address[1])

    def string(self, address: tuple[int, int] | None) -> str:
        if address is None:
            return ""
        data = self.section(address[0])
        end = data.find(b"\0", address[1])
        return data[address[1] : end if end >= 0 else len(data)].decode("latin-1")

    def type_at(self, address: tuple[int, int]) -> list[SMember]:
        """The members of the type definition at ``address``, up to its end marker."""
        if address not in self._m_types:
            pointer_size = self.m_pointer
            stride = member_stride(pointer_size)
            members, (section_index, offset) = [], address
            data = self.section(section_index)
            while True:
                if offset + stride > len(data):
                    raise CGrannyError(f"a type definition runs past section {section_index}")
                kind = struct.unpack_from("<I", data, offset)[0]
                if kind == END:
                    break
                if kind > EMPTY_REFERENCE:
                    raise CGrannyError(f"member kind {kind} at {section_index}:{offset}")
                name = self.string(self.pointer_at((section_index, offset + 4)))
                width = struct.unpack_from("<i", data, offset + 4 + 2 * pointer_size)[0]
                members.append(SMember(kind, name, self.pointer_at((section_index, offset + 4 + pointer_size)), width))
                offset += stride
            self._m_types[address] = members
        return self._m_types[address]

    def size_of(self, members: list[SMember]) -> int:
        """The bytes one record of a type takes (Granny packs its records without padding)."""
        return sum(self.member_size(member) * max(member.width, 1) for member in members)

    def member_size(self, member: SMember) -> int:
        """The bytes one element of a member takes (times its width for the whole member)."""
        inline = self.size_of(self.type_at(member.target)) if member.kind == INLINE and member.target else 0
        return field_size(member.kind, self.m_pointer, inline)

    def root(self) -> CGrannyObject:
        return CGrannyObject(self, self.type_at(tuple(self.m_root_type)), tuple(self.m_root_object))


class CGrannyObject(Mapping):
    """One record in a Granny file, read member by member when asked (``obj["Meshes"]``)."""

    def __init__(self, file: CGrannyFile, members: list[SMember], address: tuple[int, int]) -> None:
        self.m_file, self.m_members, self.m_at = file, members, address

    @cached_property
    def _offsets(self) -> dict[str, tuple[SMember, int]]:
        out, offset = {}, self.m_at[1]
        for member in self.m_members:
            out.setdefault(member.name, (member, offset))
            offset += self.m_file.member_size(member) * max(member.width, 1)
        return out

    def __getitem__(self, name: str):
        if name not in self._offsets:
            raise CGrannyMemberError(f"the object has no member {name!r}")
        member, offset = self._offsets[name]
        try:
            if member.width > 1 and member.kind in SCALARS:
                return struct.unpack_from(
                    f"<{member.width}{SCALARS[member.kind]}", self.m_file.section(self.m_at[0]), offset
                )
            return _value(self.m_file, member, (self.m_at[0], offset))
        except struct.error as error:  # a value past the end of its section
            raise CGrannyError(f"member {name!r} reaches past its section: {error}") from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._offsets)

    def __len__(self) -> int:
        return len(self._offsets)

    def __repr__(self) -> str:
        return f"GrannyObject({', '.join(self._offsets)})"


@dataclass(frozen=True)
class SGrannyArray:
    """An array of plain records (no pointers): ``raw`` holds ``count`` records of ``fields``, each a
    ``(name, kind, width)``; ``format`` is the record's ``struct`` format."""

    fields: tuple[tuple[str, int, int], ...]
    count: int
    raw: bytes

    @property
    def format(self) -> str:
        return "<" + "".join(f"{max(width, 1)}{SCALARS[kind]}" for _, kind, width in self.fields)

    def records(self) -> list[tuple]:
        return list(struct.iter_unpack(self.format, self.raw)) if self.count else []

    def column(self, name: str) -> list[tuple]:
        """One field of every record, as a tuple per record (a scalar field: one-tuples)."""
        start = 0
        for field_name, _k, width in self.fields:
            if field_name == name:
                return [record[start : start + max(width, 1)] for record in self.records()]
            start += max(width, 1)
        raise KeyError(name)

    def names(self) -> list[str]:
        return [field_name for field_name, _k, _w in self.fields]


def _value(file: CGrannyFile, member: SMember, address: tuple[int, int]):
    data, pointer_size = file.section(address[0]), file.m_pointer
    if member.kind in SCALARS:
        return struct.unpack_from("<" + SCALARS[member.kind], data, address[1])[0]
    if member.kind == INLINE:
        return CGrannyObject(file, file.type_at(member.target), address) if member.target else None
    if member.kind == STRING:
        return file.string(file.pointer_at(address))
    if member.kind == REFERENCE:
        pointed_to = file.pointer_at(address)
        return CGrannyObject(file, file.type_at(member.target), pointed_to) if pointed_to and member.target else None
    if member.kind == REFERENCE_TO_ARRAY:
        count = struct.unpack_from("<i", data, address[1])[0]
        return _array(
            file,
            file.type_at(member.target) if member.target else [],
            count,
            file.pointer_at((address[0], address[1] + 4)),
        )
    if member.kind == ARRAY_OF_REFERENCES:
        count = struct.unpack_from("<i", data, address[1])[0]
        table = file.pointer_at((address[0], address[1] + 4))
        members = file.type_at(member.target) if member.target else []
        out = []
        if table and count * pointer_size > len(file.section(table[0])):
            raise CGrannyError(f"an array of {count} references runs past section {table[0]}")
        for k in range(count if table else 0):
            pointed_to = file.pointer_at((table[0], table[1] + k * pointer_size))
            out.append(CGrannyObject(file, members, pointed_to) if pointed_to else None)
        return out
    if member.kind == VARIANT_REFERENCE:
        kind, pointed_to = file.pointer_at(address), file.pointer_at((address[0], address[1] + pointer_size))
        return CGrannyObject(file, file.type_at(kind), pointed_to) if kind and pointed_to else None
    if member.kind == REFERENCE_TO_VARIANT_ARRAY:
        kind = file.pointer_at(address)
        count = struct.unpack_from("<i", data, address[1] + pointer_size)[0]
        return _array(
            file,
            file.type_at(kind) if kind else [],
            count,
            file.pointer_at((address[0], address[1] + pointer_size + 4)),
        )
    if member.kind == TRANSFORM:
        values = struct.unpack_from("<I16f", data, address[1])
        return {"flags": values[0], "position": values[1:4], "orientation": values[4:8], "scale_shear": values[8:17]}
    return None


def _array(file: CGrannyFile, members: list[SMember], count: int, target_address: tuple[int, int] | None):
    """``count`` records at ``to``: a ``SGrannyArray`` when they are plain numbers, else a list of objects."""
    if not target_address or count <= 0 or not members:
        return []
    size_bytes = file.size_of(members)
    if size_bytes * count > len(file.section(target_address[0])):  # a damaged count must not build billions of objects
        raise CGrannyError(f"an array of {count} records runs past section {target_address[0]}")
    if all(member.kind in SCALARS for member in members):
        data = file.section(target_address[0])
        if target_address[1] + size_bytes * count > len(data):
            raise CGrannyError(f"an array of {count} records runs past section {target_address[0]}")
        fields = tuple((member.name, member.kind, member.width) for member in members)
        return SGrannyArray(fields, count, data[target_address[1] : target_address[1] + size_bytes * count])
    return [CGrannyObject(file, members, (target_address[0], target_address[1] + k * size_bytes)) for k in range(count)]
