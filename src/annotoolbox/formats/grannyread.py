"""Granny 2 ``.gr2`` files (Anno 1404's and 1701's meshes) read from their own structure: the header, the
section table, the sections (stored or Oodle1-compressed, ``grannyoodle``), the pointer fixups and the
type trees the file carries for every object in it.

``GrannyFile(data).root()`` is the entry: the file's root object (Granny's file info: ``Meshes``,
``Materials``, ``Models``, ...) as a ``GrannyObject``, a read-only mapping that reads a member when it is
asked for, so only the sections a caller touches are decompressed. Arrays of plain records (vertices,
indices) come back as ``GrannyArray`` (raw bytes plus a ``struct`` format). docs/formats/granny.md.
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


class GrannyError(ValueError):
    """Not a Granny file this reader knows, or one whose structure does not hold together."""


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
class Section:
    compression: int
    offset: int
    stored_size: int
    size: int
    stop0: int  # end of the 4-byte part (Oodle1 decodes the parts with separate models)
    stop1: int  # end of the 2-byte part
    fixup_offset: int
    fixup_count: int


@dataclass(frozen=True)
class Member:
    """One member of a Granny type: its kind, name, the type it refers to (a reference) and its array width."""

    kind: int
    name: str
    target: tuple[int, int] | None
    width: int


class GrannyFile:
    """A ``.gr2`` file's sections and pointers; ``root()`` is its root object."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        if len(data) < INFO_AT + 40 or data[:16] not in MAGICS:
            raise GrannyError(f"not a Granny 2 file this reader knows (magic {data[:16].hex()})")
        self.pointer = MAGICS[data[:16]]
        self.revision, self.total_size, _crc, table, count = struct.unpack_from("<5I", data, INFO_AT)
        if self.revision != 7:
            raise GrannyError(f"Granny file format revision {self.revision}, not 7")
        self.root_type = struct.unpack_from("<II", data, INFO_AT + 20)
        self.root_object = struct.unpack_from("<II", data, INFO_AT + 28)
        self.tag = struct.unpack_from("<I", data, INFO_AT + 36)[0]
        self.sections = []
        for k in range(count):
            comp, off, stored, size, _align, s0, s1, fix, nfix, _m, _nm = struct.unpack_from(
                "<11I", data, INFO_AT + table + SECTION_ENTRY * k
            )
            self.sections.append(Section(comp, off, stored, size, s0, s1, fix, nfix))
        self._bytes: dict[int, bytes] = {}
        self._fixups: dict[int, dict[int, tuple[int, int]]] = {}
        self._types: dict[tuple[int, int], list[Member]] = {}

    def check(self) -> None:
        """Raise ``GrannyError`` for a section this reader cannot decompress (without decompressing any)."""
        for k, s in enumerate(self.sections):
            if s.stored_size and s.compression not in (NONE, OODLE1):
                raise GrannyError(f"section {k} uses compression {s.compression} (only none and Oodle1 are read)")
            if s.offset + s.stored_size > len(self.data) or s.fixup_offset + 12 * s.fixup_count > len(self.data):
                raise GrannyError(f"section {k} reaches past the end of the file")

    def section(self, k: int) -> bytes:
        """Section ``k`` decompressed (once)."""
        if k not in self._bytes:
            if not 0 <= k < len(self.sections):
                raise GrannyError(f"a pointer into section {k} of {len(self.sections)}")
            s = self.sections[k]
            raw = self.data[s.offset : s.offset + s.stored_size]
            if s.compression == NONE or not s.stored_size:
                out = raw[: s.size] + bytes(max(0, s.size - len(raw)))
            elif s.compression == OODLE1:
                try:
                    out = grannyoodle.decompress(raw, s.size, s.stop0, s.stop1)
                except (grannyoodle.GrannyCompressionError, IndexError) as exc:
                    raise GrannyError(f"section {k} does not decompress: {exc}") from exc
            else:
                raise GrannyError(f"section {k} uses compression {s.compression} (only none and Oodle1 are read)")
            self._bytes[k] = out
        return self._bytes[k]

    def _fixups_of(self, k: int) -> dict[int, tuple[int, int]]:
        if k not in self._fixups:
            s = self.sections[k]
            table = struct.iter_unpack("<III", self.data[s.fixup_offset : s.fixup_offset + 12 * s.fixup_count])
            self._fixups[k] = {src: (sec, off) for src, sec, off in table}
        return self._fixups[k]

    def pointer_at(self, at: tuple[int, int]) -> tuple[int, int] | None:
        """Where the pointer stored at ``at`` (section, offset) points; None for a null pointer."""
        return self._fixups_of(at[0]).get(at[1])

    def string(self, at: tuple[int, int] | None) -> str:
        if at is None:
            return ""
        data = self.section(at[0])
        end = data.find(b"\0", at[1])
        return data[at[1] : end if end >= 0 else len(data)].decode("latin-1")

    def type_at(self, at: tuple[int, int]) -> list[Member]:
        """The members of the type definition at ``at``, up to its end marker."""
        if at not in self._types:
            p = self.pointer
            stride = member_stride(p)
            members, (sec, off) = [], at
            data = self.section(sec)
            while True:
                if off + stride > len(data):
                    raise GrannyError(f"a type definition runs past section {sec}")
                kind = struct.unpack_from("<I", data, off)[0]
                if kind == END:
                    break
                if kind > EMPTY_REFERENCE:
                    raise GrannyError(f"member kind {kind} at {sec}:{off}")
                name = self.string(self.pointer_at((sec, off + 4)))
                width = struct.unpack_from("<i", data, off + 4 + 2 * p)[0]
                members.append(Member(kind, name, self.pointer_at((sec, off + 4 + p)), width))
                off += stride
            self._types[at] = members
        return self._types[at]

    def size_of(self, members: list[Member]) -> int:
        """The bytes one record of a type takes (Granny packs its records without padding)."""
        return sum(self.member_size(m) * max(m.width, 1) for m in members)

    def member_size(self, m: Member) -> int:
        """The bytes one element of a member takes (times its width for the whole member)."""
        inline = self.size_of(self.type_at(m.target)) if m.kind == INLINE and m.target else 0
        return field_size(m.kind, self.pointer, inline)

    def root(self) -> GrannyObject:
        return GrannyObject(self, self.type_at(tuple(self.root_type)), tuple(self.root_object))


class GrannyObject(Mapping):
    """One record in a Granny file, read member by member when asked (``obj["Meshes"]``)."""

    def __init__(self, file: GrannyFile, members: list[Member], at: tuple[int, int]) -> None:
        self.file, self.members, self.at = file, members, at

    @cached_property
    def _offsets(self) -> dict[str, tuple[Member, int]]:
        out, off = {}, self.at[1]
        for m in self.members:
            out.setdefault(m.name, (m, off))
            off += self.file.member_size(m) * max(m.width, 1)
        return out

    def __getitem__(self, name: str):
        m, off = self._offsets[name]
        if m.width > 1 and m.kind in SCALARS:
            return struct.unpack_from(f"<{m.width}{SCALARS[m.kind]}", self.file.section(self.at[0]), off)
        return _value(self.file, m, (self.at[0], off))

    def __iter__(self) -> Iterator[str]:
        return iter(self._offsets)

    def __len__(self) -> int:
        return len(self._offsets)

    def __repr__(self) -> str:
        return f"GrannyObject({', '.join(self._offsets)})"


@dataclass(frozen=True)
class GrannyArray:
    """An array of plain records (no pointers): ``raw`` holds ``count`` records of ``fields``, each a
    ``(name, kind, width)``; ``format`` is the record's ``struct`` format."""

    fields: tuple[tuple[str, int, int], ...]
    count: int
    raw: bytes

    @property
    def format(self) -> str:
        return "<" + "".join(f"{max(w, 1)}{SCALARS[k]}" for _, k, w in self.fields)

    def records(self) -> list[tuple]:
        return list(struct.iter_unpack(self.format, self.raw)) if self.count else []

    def column(self, name: str) -> list[tuple]:
        """One field of every record, as a tuple per record (a scalar field: one-tuples)."""
        start = 0
        for n, _k, w in self.fields:
            if n == name:
                return [r[start : start + max(w, 1)] for r in self.records()]
            start += max(w, 1)
        raise KeyError(name)

    def names(self) -> list[str]:
        return [n for n, _k, _w in self.fields]


def _value(file: GrannyFile, m: Member, at: tuple[int, int]):
    data, p = file.section(at[0]), file.pointer
    if m.kind in SCALARS:
        return struct.unpack_from("<" + SCALARS[m.kind], data, at[1])[0]
    if m.kind == INLINE:
        return GrannyObject(file, file.type_at(m.target), at) if m.target else None
    if m.kind == STRING:
        return file.string(file.pointer_at(at))
    if m.kind == REFERENCE:
        to = file.pointer_at(at)
        return GrannyObject(file, file.type_at(m.target), to) if to and m.target else None
    if m.kind == REFERENCE_TO_ARRAY:
        count = struct.unpack_from("<i", data, at[1])[0]
        return _array(file, file.type_at(m.target) if m.target else [], count, file.pointer_at((at[0], at[1] + 4)))
    if m.kind == ARRAY_OF_REFERENCES:
        count = struct.unpack_from("<i", data, at[1])[0]
        table = file.pointer_at((at[0], at[1] + 4))
        members = file.type_at(m.target) if m.target else []
        out = []
        for k in range(count if table else 0):
            to = file.pointer_at((table[0], table[1] + k * p))
            out.append(GrannyObject(file, members, to) if to else None)
        return out
    if m.kind == VARIANT_REFERENCE:
        kind, to = file.pointer_at(at), file.pointer_at((at[0], at[1] + p))
        return GrannyObject(file, file.type_at(kind), to) if kind and to else None
    if m.kind == REFERENCE_TO_VARIANT_ARRAY:
        kind = file.pointer_at(at)
        count = struct.unpack_from("<i", data, at[1] + p)[0]
        return _array(file, file.type_at(kind) if kind else [], count, file.pointer_at((at[0], at[1] + p + 4)))
    if m.kind == TRANSFORM:
        v = struct.unpack_from("<I16f", data, at[1])
        return {"flags": v[0], "position": v[1:4], "orientation": v[4:8], "scale_shear": v[8:17]}
    return None


def _array(file: GrannyFile, members: list[Member], count: int, to: tuple[int, int] | None):
    """``count`` records at ``to``: a ``GrannyArray`` when they are plain numbers, else a list of objects."""
    if not to or count <= 0 or not members:
        return []
    size = file.size_of(members)
    if all(m.kind in SCALARS for m in members):
        data = file.section(to[0])
        if to[1] + size * count > len(data):
            raise GrannyError(f"an array of {count} records runs past section {to[0]}")
        fields = tuple((m.name, m.kind, m.width) for m in members)
        return GrannyArray(fields, count, data[to[1] : to[1] + size * count])
    return [GrannyObject(file, members, (to[0], to[1] + k * size)) for k in range(count)]
