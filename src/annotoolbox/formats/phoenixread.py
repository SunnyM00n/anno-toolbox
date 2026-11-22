"""Decode a Phoenix UI file (``PHX??``) into an object tree against the reflection schema.

``load`` is the entry point: a ``CDecoder`` whose ``root`` is the tree (per object its type, frame flags, GUID, field
count, presence mask and the present fields; a plain value keeps its raw bytes, a value holding inline objects keeps
the bytes before them plus the child nodes) and whose ``log`` is the field-by-field reading. ``phoenixwrite`` turns
the tree back into bytes, ``phoenixedit`` changes it. The encoding rules are in ``docs/formats/phoenix-scene-format.md``.
"""

from __future__ import annotations

import struct
import uuid

from .phoenix import (
    ALT_LAYOUTS,
    SIZES,
    TYPED_OBJECTS,
    TYPED_VALUES,
    alt_layout,
    counted,
    file_layout,
    full_schema,
    stored_fields,
)


class CDecoder:
    def __init__(self, schema: dict, data: bytes) -> None:
        self.m_schema = full_schema(schema)
        self.m_data = data
        self.m_position = 0
        self.m_log: list[str] = []
        self.m_depth = 0
        self.m_unknown: set[str] = set()
        self.m_layouts: dict[tuple, int] = {}  # (type, fields left out) -> objects read in that older layout
        self.m_entry: dict | None = None  # the field being read (inline objects attach to it)
        self.m_root: dict | None = None
        self.m_guid_of: dict[str, str] = {}  # type hash -> schema guid (layout version) from the type table
        self.m_kind_order: list[int] = []  # the kinds of the shape totals in the file's order

    # Bytes

    def varint(self) -> int:
        value = 0
        shift = 0
        while True:
            if self.m_position >= len(self.m_data):
                raise EOFError(
                    f"a varint at {self.m_position:#x} runs past the end of the file ({len(self.m_data)} bytes)"
                )
            byte = self.m_data[self.m_position]
            self.m_position += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value

    def take(self, count: int) -> bytes:
        out = self.m_data[self.m_position : self.m_position + count]
        if len(out) < count:
            raise EOFError(f"need {count} bytes at {self.m_position:#x}, file has {len(self.m_data)}")
        self.m_position += count
        return out

    def note(self, text: str) -> None:
        self.m_log.append("  " * self.m_depth + text)

    # Header and type table

    def header(self) -> dict:
        """The fixed header, the type table and the shape prefix; leaves ``i`` at the root object."""
        raw = self.m_data
        if len(raw) < 16:
            raise EOFError(f"a Phoenix file of {len(raw)} bytes has no header")
        kind = raw[:5].decode("latin1")
        version, const, flags = struct.unpack_from("<HIH", raw, 8)
        self.m_position = 16
        type_count = self.varint()
        table = []  # (type hash, schema guid, number of objects of the type in the file)
        for _ in range(type_count):
            type_hash = struct.unpack("<I", self.take(4))[0]
            guid = uuid.UUID(bytes_le=self.take(16))
            table.append((f"{type_hash:08x}", str(guid), self.varint()))
        # shape prefix: kind count, (kind byte, sum of the token counts of that kind) pairs, a varint (0),
        # token count, token byte length, the tokens, 02
        totals = []
        for _ in range(self.varint()):
            k = self.take(1)[0]
            totals.append((k, self.varint()))
        extra = self.varint()
        token_count, byte_count = self.varint(), self.varint()
        tokens = []
        for _ in range(token_count):
            k = self.take(1)[0]
            token_value = self.varint()
            tokens.append((k, token_value, self.varint()) if k == 0x40 else (k, token_value))
        if self.take(1) != b"\x02":
            raise ValueError(f"shape prefix does not end with 02 at {self.m_position - 1:#x}")
        return {
            "kind": kind,
            "version": version,
            "const": const,
            "flags": flags,
            "table": table,
            "totals": totals,
            "extra": extra,
            "ntok": token_count,
            "nbytes": byte_count,
            "tokens": tokens,
        }

    # Layouts

    def layout(self, fields: list[tuple[dict, dict]], hash_hex: str) -> list[tuple[dict, dict]]:
        """The field list for the layout versions named in this file's type table (OLD_LAYOUTS, ALT_LAYOUTS, the
        other executables' schemas, the History Editions')."""
        for record in {id(owner): owner for owner, _ in fields}.values():
            key = (record["hash"], self.m_guid_of.get(record["hash"]))
            if key in ALT_LAYOUTS:
                return alt_layout(fields, ALT_LAYOUTS[key])
        return file_layout(self.m_schema, hash_hex, self.m_guid_of)

    @staticmethod
    def guessed_layouts(fields: list[tuple[dict, dict]], field_count: int):
        """Candidates when the type table names a layout version not in OLD_LAYOUTS: the tail of the whole
        list is missing (the usual case), else any single field. The first that reads cleanly wins."""
        yield fields[:field_count]
        if len(fields) - field_count == 1:
            for i in range(len(fields) - 2, 0, -1):
                yield [x for j, x in enumerate(fields) if j != i]

    # Objects

    def object_body(self, type_hash: int, table: list, declared: str | None = None, node: dict | None = None) -> None:
        """After the type hash: varint field count, presence bitmask (MSB first), then the present fields.

        A type missing from the schema dump is read with the field's declared type (its base)."""
        hash_hex = f"{type_hash:08x}"
        if hash_hex not in self.m_schema and declared in self.m_schema:
            self.note(f"! type {hash_hex} not in the schema, read as {declared} {self.m_schema[declared].get('name')}")
            hash_hex = declared
            self.m_unknown.add(f"{type_hash:08x}")
        full = stored_fields(self.m_schema, hash_hex)
        count = self.varint()
        mask = self.take((count + 7) // 8)
        present = [(mask[i // 8] >> (7 - i % 8)) & 1 for i in range(count)]
        node = node if node is not None else {}
        node.update(schema_type=hash_hex, n=count, mask=mask)
        fields = self.layout(full, hash_hex)
        trailing = [x for x in fields if x[1].get("trailing")]  # stored after the counted fields, always present
        fields = [x for x in fields if not x[1].get("trailing")]
        if count != len(fields):
            fields = counted(fields, self.m_guid_of, count) or fields
        self.read_counted(hash_hex, full, fields, count, present, table, node)
        if trailing:
            node["layout"] = [*node["layout"], *trailing]
            node["fields"] += self.read_fields(trailing, [1] * len(trailing), table)

    def read_counted(
        self, hash_hex: str, full: list, fields: list, count: int, present: list[int], table: list, node: dict
    ) -> None:
        """The ``n`` fields the field count and the presence mask cover, in the layout ``fields``."""
        if [field["hash"] for _, field in fields] != [field["hash"] for _, field in full]:
            kept = {(owner["hash"], field["hash"]) for owner, field in fields}
            gone = [
                field["name"] or field["hash"] for owner, field in full if (owner["hash"], field["hash"]) not in kept
            ]
            self.note(f"! older layout of {hash_hex} (type table), without: {', '.join(gone)}")
            self.m_layouts[(hash_hex, tuple(gone))] = self.m_layouts.get((hash_hex, tuple(gone)), 0) + 1
        if count >= len(fields):
            if count > len(fields):
                self.note(
                    f"! field count {count} in file, {len(fields)} in schema for {hash_hex} {self.m_schema.get(hash_hex, {}).get('name')}"
                )
            node["layout"], node["fields"] = fields[:count], self.read_fields(fields[:count], present, table)
            return
        self.note(
            f"! field count {count} in file, {len(fields)} for the layout of {hash_hex} {self.m_schema.get(hash_hex, {}).get('name')}; guessing"
        )
        self.guess_layout(hash_hex, fields, count, present, table, node)

    def guess_layout(
        self, hash_hex: str, fields: list, field_count: int, present: list[int], table: list, node: dict
    ) -> None:
        """A layout version the tables do not know: try the candidates, keep the first that reads cleanly."""
        start, logged, depth, first = self.m_position, len(self.m_log), self.m_depth, None
        for candidate in self.guessed_layouts(fields, field_count):
            layouts = dict(self.m_layouts)
            try:
                kept = {id(field) for _, field in candidate}
                gone = [field["name"] or field["hash"] for _, field in fields if id(field) not in kept]
                self.note(f"! guessed layout of {hash_hex}, without: {', '.join(gone) or '-'}")
                key = (hash_hex, ("guessed",) + tuple(gone))
                self.m_layouts[key] = self.m_layouts.get(key, 0) + 1
                node["layout"], node["fields"] = candidate, self.read_fields(candidate, present, table)
                return
            except (ValueError, EOFError, IndexError, struct.error) as error:
                self.m_layouts = layouts
                first = first or error
                self.m_position, self.m_depth = start, depth
                del self.m_log[logged:]
        raise first

    def read_fields(self, fields: list[tuple[dict, dict]], present: list[int], table: list) -> list[dict]:
        """The present fields as tree entries: ``raw`` bytes for plain values; for values holding
        inline objects the bytes before them (``head``: list count, boxed code and hash) and ``objs``."""
        entries = []
        outer = self.m_entry
        try:
            for index, (record, field) in enumerate(fields):
                if not present[index]:
                    self.note(f"[{record['name'] or record['hash']}] {field['name'] or field['hash']}: absent")
                    continue
                start = self.m_position
                self.m_entry = entry = {"field": field, "record": record["hash"], "objs": []}
                text = self.value(field, table)
                if text:
                    self.note(f"[{record['name'] or record['hash']}] {text}")
                if entry["objs"]:
                    entry["head"] = self.m_data[start : entry["objs"][0]["start"]]
                    self._gaps(entry)
                else:
                    entry["raw"] = self.m_data[start : self.m_position]
                entries.append(entry)
        finally:
            self.m_entry = outer
        return entries

    def _gaps(self, entry: dict) -> None:
        """The bytes after each inline object of an entry, kept as ``gaps`` where a value list interleaves them."""
        ends, objs = entry.pop("ends"), entry["objs"]
        gaps = [
            self.m_data[end : (objs[k + 1]["start"] if k + 1 < len(objs) else self.m_position)]
            for k, end in enumerate(ends)
        ]
        if any(gaps):
            entry["gaps"] = gaps

    def inline_object(self, table: list, label: str, declared: str | None = None) -> None:
        """An inline frame: ``02 flags [guid] type hash``, then the object body."""
        self.note(f"{label} raw: {self.m_data[self.m_position : self.m_position + 12].hex(' ')}")
        start = self.m_position
        marker, flags = self.take(1)[0], self.take(1)[0]
        if marker != 2:
            raise ValueError(f"inline object marker {marker:#x} at {self.m_position - 2:#x}")
        guid_bytes = self.take(16) if flags & 2 else None
        guid = uuid.UUID(bytes_le=guid_bytes) if guid_bytes else None
        type_hash = struct.unpack("<I", self.take(4))[0]
        node = {"start": start, "flags": flags, "guid": guid_bytes, "type": type_hash}
        self.m_entry["objs"].append(node)
        self.note(
            f"{label}: object {type_hash:08x} {self.m_schema.get(f'{type_hash:08x}', {}).get('name')} marker {marker:#x} flags {flags:#x}"
            + (f" guid {guid}" if guid else "")
        )
        self.m_depth += 1
        self.object_body(type_hash, table, declared, node)
        self.m_depth -= 1
        self.m_entry.setdefault("ends", []).append(self.m_position)

    # Values

    def boxed(self, code: int, name: str) -> str:
        """A value of the given type code without a schema field (0x1f00 variants)."""
        if code in (0x4000, 0x4200):  # boxed object: u32 type hash, then the inline object frame
            type_hash = struct.unpack("<I", self.take(4))[0]
            self.inline_object([], f"{name} ({type_hash:08x})")
            return ""
        return self.value(
            {"code": f"{code:#x}", "name": name, "hash": "", "offset": 1, "flags": "0x2001", "elem": None}, []
        )

    def value(self, field: dict, table: list) -> str:
        """Read one field value; returns the log line (empty when the value was logged as objects)."""
        code = int(field["code"], 16)
        name = field["name"] or field["hash"]
        if code == 0x0:
            return f"{name}: (object base)"
        if code == 0x801:  # list of u32 (PropertyIdPath)
            count = self.varint()
            return f"{name} = [{count}] " + " ".join(
                f"{struct.unpack('<I', self.take(4))[0]:08x}" for _ in range(count)
            )
        if code & 1 and code - 1 in SIZES:  # list of fixed-size values: LEB128 count, then the elements (0x401 = bytes)
            count, size_bytes = self.varint(), SIZES[code - 1]
            raw = self.take(count * size_bytes)
            return f"{name} = [{count} x {size_bytes} bytes] {raw[:32].hex(' ')}{' ...' if len(raw) > 32 else ''}"
        if code == 0x1700:  # string: LEB128 length; when non-empty a kind byte (01) and the utf-8 bytes
            count = self.varint()
            if count == 0:
                return f"{name} = ''"
            kind = self.take(1)[0]
            return f"{name} = string kind {kind} {self.take(count)!r}"
        if code == TYPED_VALUES:  # DynamicProperties (phoenix.typed_values has the encoding)
            return self.typed(name, table)
        if code == 0x1F00:  # boxed value: u16 type code, then the value
            inner = struct.unpack("<H", self.take(2))[0]
            return f"{name} = boxed {inner:#x}: " + self.boxed(inner, "value")
        if code in (0x4400, 0x4300):  # single asset reference: flags byte, then hash + guid when set
            flags = self.take(1)[0]
            if flags == 0:
                return f"{name} = none"
            type_hash = struct.unpack("<I", self.take(4))[0]
            guid = uuid.UUID(bytes_le=self.take(16))
            return f"{name} = ref flags {flags:#x} {type_hash:08x} {guid}"
        if code in SIZES:
            return self.fixed(code, name)
        if code == 0x1000:  # float (RotationAngle, AlphaTint, UVRotation)
            return f"{name} = f32 {struct.unpack('<f', self.take(4))[0]:g}"
        if code in (0x4401, 0x4100):  # list of asset references: hash + guid per element
            count = self.varint()
            items = []
            for _ in range(count):
                type_hash = struct.unpack("<I", self.take(4))[0]
                guid = uuid.UUID(bytes_le=self.take(16))
                items.append(f"{type_hash:08x}:{guid}")
            return f"{name} = [{count}] " + ", ".join(items)
        if code == 0x4200:  # single inline object (same frame as 0x4000); a zero byte would mean none
            if self.m_data[self.m_position] == 0:
                self.m_position += 1
                return f"{name} = none"
            self.inline_object(table, name, field.get("elem"))
            return ""
        if code == 0x4201:  # list of inline objects
            count = self.varint()
            self.note(f"{name} = list of {count}")
            for k in range(count):
                self.inline_object(table, f"{name}[{k}]", field.get("elem"))
            return ""
        if code == 0x4000:  # embedded object
            self.inline_object(table, name, field.get("elem"))
            return ""
        raise ValueError(f"no rule for code {code:#x} ({name}) at {self.m_position:#x}")

    def typed(self, name: str, table: list) -> str:
        """A typed value list (``phoenix.typed_values``): a varint count, then per value its id, code and value."""
        count = self.varint()
        for k in range(count):
            uid, inner = struct.unpack("<IH", self.take(6))
            if inner in TYPED_OBJECTS:
                type_hash, tag = struct.unpack("<II", self.take(8))
                self.note(f"{name}[{k}] = {uid:08x} {inner:#x} {type_hash:08x} {tag}")
                self.inline_object(table, f"{name}[{k}]")
            else:
                tag = struct.unpack("<I", self.take(4))[0]
                self.note(f"{name}[{k}] = {uid:08x} {inner:#x} {tag}: " + self.boxed(inner, "value"))
        return f"{name} = [{count}] typed values"

    def fixed(self, code: int, name: str) -> str:
        """A fixed-size value (``SIZES``), logged by kind."""
        raw = self.take(SIZES[code])
        if code == 0x800:
            return f"{name} = u32 {struct.unpack('<I', raw)[0]} / f32 {struct.unpack('<f', raw)[0]:g}"
        if code == 0x1100:
            return f"{name} = vec2 {struct.unpack('<ff', raw)}"
        if code == 0xB00:
            return f"{name} = enum {raw.hex(' ')}"
        if code == 0x100 and raw[0] > 1:
            raise ValueError(f"implausible bool {raw[0]} for {name}")
        if code == 0x1900:
            return f"{name} = id {uuid.UUID(bytes_le=raw)}"
        return f"{name} = {raw.hex(' ')}"

    def decode(self) -> dict:
        """Decode the whole file: the header dict with ``consumed`` and ``size`` added; the tree is ``root``."""
        header = self.header()
        header["shape_end"] = self.m_position
        self.m_guid_of = {type_hash: guid for type_hash, guid, _ in header["table"]}
        self.m_kind_order = [k for k, _ in header["totals"]]
        guid_bytes = self.take(16)
        guid = uuid.UUID(bytes_le=guid_bytes)
        type_hash = struct.unpack("<I", self.take(4))[0]
        self.m_root = {"start": self.m_position - 20, "flags": None, "guid": guid_bytes, "type": type_hash}
        self.note(f"root {type_hash:08x} {self.m_schema.get(f'{type_hash:08x}', {}).get('name')} guid {guid}")
        self.m_depth = 1
        self.object_body(type_hash, header["table"], None, self.m_root)
        header["consumed"] = self.m_position
        header["size"] = len(self.m_data)
        return header


def load(data: bytes, schema: dict) -> CDecoder:
    """Decode a whole file; a file with bytes left over is refused."""
    decoder = CDecoder(schema, data)
    root = decoder.decode()
    if root["consumed"] != root["size"]:
        raise ValueError(f"decoded {root['consumed']} of {root['size']} bytes")
    return decoder


def type_table(data: bytes) -> list[tuple[str, str, int]]:
    """The type table of a file (type hash, schema GUID, object count), without decoding its body."""
    return CDecoder({}, data).header()["table"]
