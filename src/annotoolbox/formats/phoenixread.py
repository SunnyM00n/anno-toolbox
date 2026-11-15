"""Decode a Phoenix UI file (``PHX??``) into an object tree against the reflection schema.

``load`` is the entry point: a ``Decoder`` whose ``root`` is the tree (per object its type, frame flags, GUID, field
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


class Decoder:
    def __init__(self, schema: dict, data: bytes) -> None:
        self.s = full_schema(schema)
        self.b = data
        self.i = 0
        self.log: list[str] = []
        self.depth = 0
        self.unknown: set[str] = set()
        self.layouts: dict[tuple, int] = {}  # (type, fields left out) -> objects read in that older layout
        self.entry: dict | None = None  # the field being read (inline objects attach to it)
        self.root: dict | None = None
        self.guid_of: dict[str, str] = {}  # type hash -> schema guid (layout version) from the type table
        self.kind_order: list[int] = []  # the kinds of the shape totals in the file's order

    # Bytes

    def varint(self) -> int:
        v = 0
        sh = 0
        while True:
            c = self.b[self.i]
            self.i += 1
            v |= (c & 0x7F) << sh
            sh += 7
            if c < 0x80:
                return v

    def take(self, n: int) -> bytes:
        out = self.b[self.i : self.i + n]
        if len(out) < n:
            raise EOFError(f"need {n} bytes at {self.i:#x}, file has {len(self.b)}")
        self.i += n
        return out

    def note(self, text: str) -> None:
        self.log.append("  " * self.depth + text)

    # Header and type table

    def header(self) -> dict:
        """The fixed header, the type table and the shape prefix; leaves ``i`` at the root object."""
        b = self.b
        kind = b[:5].decode("latin1")
        version, const, flags = struct.unpack_from("<HIH", b, 8)
        self.i = 16
        n = self.varint()
        table = []  # (type hash, schema guid, number of objects of the type in the file)
        for _ in range(n):
            h = struct.unpack("<I", self.take(4))[0]
            g = uuid.UUID(bytes_le=self.take(16))
            table.append((f"{h:08x}", str(g), self.varint()))
        # shape prefix: kind count, (kind byte, sum of the token counts of that kind) pairs, a varint (0),
        # token count, token byte length, the tokens, 02
        totals = []
        for _ in range(self.varint()):
            k = self.take(1)[0]
            totals.append((k, self.varint()))
        extra = self.varint()
        ntok, nbytes = self.varint(), self.varint()
        tokens = []
        for _ in range(ntok):
            k = self.take(1)[0]
            c = self.varint()
            tokens.append((k, c, self.varint()) if k == 0x40 else (k, c))
        if self.take(1) != b"\x02":
            raise ValueError(f"shape prefix does not end with 02 at {self.i - 1:#x}")
        return {
            "kind": kind,
            "version": version,
            "const": const,
            "flags": flags,
            "table": table,
            "totals": totals,
            "extra": extra,
            "ntok": ntok,
            "nbytes": nbytes,
            "tokens": tokens,
        }

    # Layouts

    def layout(self, fields: list[tuple[dict, dict]], hs: str) -> list[tuple[dict, dict]]:
        """The field list for the layout versions named in this file's type table (OLD_LAYOUTS, ALT_LAYOUTS, the
        other executables' schemas, the History Editions')."""
        for rec in {id(r): r for r, _ in fields}.values():
            key = (rec["hash"], self.guid_of.get(rec["hash"]))
            if key in ALT_LAYOUTS:
                return alt_layout(fields, ALT_LAYOUTS[key])
        return file_layout(self.s, hs, self.guid_of)

    @staticmethod
    def guessed_layouts(fields: list[tuple[dict, dict]], n: int):
        """Candidates when the type table names a layout version not in OLD_LAYOUTS: the tail of the whole
        list is missing (the usual case), else any single field. The first that reads cleanly wins."""
        yield fields[:n]
        if len(fields) - n == 1:
            for i in range(len(fields) - 2, 0, -1):
                yield [x for j, x in enumerate(fields) if j != i]

    # Objects

    def object_body(self, h: int, table: list, declared: str | None = None, node: dict | None = None) -> None:
        """After the type hash: varint field count, presence bitmask (MSB first), then the present fields.

        A type missing from the schema dump is read with the field's declared type (its base)."""
        hs = f"{h:08x}"
        if hs not in self.s and declared in self.s:
            self.note(f"! type {hs} not in the schema, read as {declared} {self.s[declared].get('name')}")
            hs = declared
            self.unknown.add(f"{h:08x}")
        full = stored_fields(self.s, hs)
        n = self.varint()
        mask = self.take((n + 7) // 8)
        present = [(mask[i // 8] >> (7 - i % 8)) & 1 for i in range(n)]
        node = node if node is not None else {}
        node.update(schema_type=hs, n=n, mask=mask)
        fields = self.layout(full, hs)
        trailing = [x for x in fields if x[1].get("trailing")]  # stored after the counted fields, always present
        fields = [x for x in fields if not x[1].get("trailing")]
        if n != len(fields):
            fields = counted(fields, self.guid_of, n) or fields
        self.read_counted(hs, full, fields, n, present, table, node)
        if trailing:
            node["layout"] = [*node["layout"], *trailing]
            node["fields"] += self.read_fields(trailing, [1] * len(trailing), table)

    def read_counted(
        self, hs: str, full: list, fields: list, n: int, present: list[int], table: list, node: dict
    ) -> None:
        """The ``n`` fields the field count and the presence mask cover, in the layout ``fields``."""
        if [f["hash"] for _, f in fields] != [f["hash"] for _, f in full]:
            kept = {(r["hash"], f["hash"]) for r, f in fields}
            gone = [f["name"] or f["hash"] for r, f in full if (r["hash"], f["hash"]) not in kept]
            self.note(f"! older layout of {hs} (type table), without: {', '.join(gone)}")
            self.layouts[(hs, tuple(gone))] = self.layouts.get((hs, tuple(gone)), 0) + 1
        if n >= len(fields):
            if n > len(fields):
                self.note(
                    f"! field count {n} in file, {len(fields)} in schema for {hs} {self.s.get(hs, {}).get('name')}"
                )
            node["layout"], node["fields"] = fields[:n], self.read_fields(fields[:n], present, table)
            return
        self.note(
            f"! field count {n} in file, {len(fields)} for the layout of {hs} {self.s.get(hs, {}).get('name')}; guessing"
        )
        self.guess_layout(hs, fields, n, present, table, node)

    def guess_layout(self, hs: str, fields: list, n: int, present: list[int], table: list, node: dict) -> None:
        """A layout version the tables do not know: try the candidates, keep the first that reads cleanly."""
        start, logged, depth, first = self.i, len(self.log), self.depth, None
        for cand in self.guessed_layouts(fields, n):
            layouts = dict(self.layouts)
            try:
                kept = {id(f) for _, f in cand}
                gone = [f["name"] or f["hash"] for _, f in fields if id(f) not in kept]
                self.note(f"! guessed layout of {hs}, without: {', '.join(gone) or '-'}")
                key = (hs, ("guessed",) + tuple(gone))
                self.layouts[key] = self.layouts.get(key, 0) + 1
                node["layout"], node["fields"] = cand, self.read_fields(cand, present, table)
                return
            except (ValueError, EOFError, IndexError, struct.error) as e:
                self.layouts = layouts
                first = first or e
                self.i, self.depth = start, depth
                del self.log[logged:]
        raise first

    def read_fields(self, fields: list[tuple[dict, dict]], present: list[int], table: list) -> list[dict]:
        """The present fields as tree entries: ``raw`` bytes for plain values; for values holding
        inline objects the bytes before them (``head``: list count, boxed code and hash) and ``objs``."""
        entries = []
        outer = self.entry
        try:
            for idx, (rec, f) in enumerate(fields):
                if not present[idx]:
                    self.note(f"[{rec['name'] or rec['hash']}] {f['name'] or f['hash']}: absent")
                    continue
                start = self.i
                self.entry = e = {"field": f, "record": rec["hash"], "objs": []}
                text = self.value(f, table)
                if text:
                    self.note(f"[{rec['name'] or rec['hash']}] {text}")
                if e["objs"]:
                    e["head"] = self.b[start : e["objs"][0]["start"]]
                    self._gaps(e)
                else:
                    e["raw"] = self.b[start : self.i]
                entries.append(e)
        finally:
            self.entry = outer
        return entries

    def _gaps(self, e: dict) -> None:
        """The bytes after each inline object of an entry, kept as ``gaps`` where a value list interleaves them."""
        ends, objs = e.pop("ends"), e["objs"]
        gaps = [self.b[end : (objs[k + 1]["start"] if k + 1 < len(objs) else self.i)] for k, end in enumerate(ends)]
        if any(gaps):
            e["gaps"] = gaps

    def inline_object(self, table: list, label: str, declared: str | None = None) -> None:
        """An inline frame: ``02 flags [guid] type hash``, then the object body."""
        self.note(f"{label} raw: {self.b[self.i : self.i + 12].hex(' ')}")
        start = self.i
        marker, flags = self.take(1)[0], self.take(1)[0]
        if marker != 2:
            raise ValueError(f"inline object marker {marker:#x} at {self.i - 2:#x}")
        graw = self.take(16) if flags & 2 else None
        g = uuid.UUID(bytes_le=graw) if graw else None
        h = struct.unpack("<I", self.take(4))[0]
        node = {"start": start, "flags": flags, "guid": graw, "type": h}
        self.entry["objs"].append(node)
        self.note(
            f"{label}: object {h:08x} {self.s.get(f'{h:08x}', {}).get('name')} marker {marker:#x} flags {flags:#x}"
            + (f" guid {g}" if g else "")
        )
        self.depth += 1
        self.object_body(h, table, declared, node)
        self.depth -= 1
        self.entry.setdefault("ends", []).append(self.i)

    # Values

    def boxed(self, code: int, name: str) -> str:
        """A value of the given type code without a schema field (0x1f00 variants)."""
        if code in (0x4000, 0x4200):  # boxed object: u32 type hash, then the inline object frame
            h = struct.unpack("<I", self.take(4))[0]
            self.inline_object([], f"{name} ({h:08x})")
            return ""
        return self.value(
            {"code": f"{code:#x}", "name": name, "hash": "", "offset": 1, "flags": "0x2001", "elem": None}, []
        )

    def value(self, f: dict, table: list) -> str:
        """Read one field value; returns the log line (empty when the value was logged as objects)."""
        code = int(f["code"], 16)
        name = f["name"] or f["hash"]
        if code == 0x0:
            return f"{name}: (object base)"
        if code == 0x801:  # list of u32 (PropertyIdPath)
            n = self.varint()
            return f"{name} = [{n}] " + " ".join(f"{struct.unpack('<I', self.take(4))[0]:08x}" for _ in range(n))
        if code & 1 and code - 1 in SIZES:  # list of fixed-size values: LEB128 count, then the elements (0x401 = bytes)
            n, size = self.varint(), SIZES[code - 1]
            raw = self.take(n * size)
            return f"{name} = [{n} x {size} bytes] {raw[:32].hex(' ')}{' ...' if len(raw) > 32 else ''}"
        if code == 0x1700:  # string: LEB128 length; when non-empty a kind byte (01) and the utf-8 bytes
            n = self.varint()
            if n == 0:
                return f"{name} = ''"
            kind = self.take(1)[0]
            return f"{name} = string kind {kind} {self.take(n)!r}"
        if code == TYPED_VALUES:  # DynamicProperties (phoenix.typed_values has the encoding)
            return self.typed(name, table)
        if code == 0x1F00:  # boxed value: u16 type code, then the value
            inner = struct.unpack("<H", self.take(2))[0]
            return f"{name} = boxed {inner:#x}: " + self.boxed(inner, "value")
        if code in (0x4400, 0x4300):  # single asset reference: flags byte, then hash + guid when set
            flags = self.take(1)[0]
            if flags == 0:
                return f"{name} = none"
            h = struct.unpack("<I", self.take(4))[0]
            g = uuid.UUID(bytes_le=self.take(16))
            return f"{name} = ref flags {flags:#x} {h:08x} {g}"
        if code in SIZES:
            return self.fixed(code, name)
        if code == 0x1000:  # float (RotationAngle, AlphaTint, UVRotation)
            return f"{name} = f32 {struct.unpack('<f', self.take(4))[0]:g}"
        if code in (0x4401, 0x4100):  # list of asset references: hash + guid per element
            n = self.varint()
            items = []
            for _ in range(n):
                h = struct.unpack("<I", self.take(4))[0]
                g = uuid.UUID(bytes_le=self.take(16))
                items.append(f"{h:08x}:{g}")
            return f"{name} = [{n}] " + ", ".join(items)
        if code == 0x4200:  # single inline object (same frame as 0x4000); a zero byte would mean none
            if self.b[self.i] == 0:
                self.i += 1
                return f"{name} = none"
            self.inline_object(table, name, f.get("elem"))
            return ""
        if code == 0x4201:  # list of inline objects
            n = self.varint()
            self.note(f"{name} = list of {n}")
            for k in range(n):
                self.inline_object(table, f"{name}[{k}]", f.get("elem"))
            return ""
        if code == 0x4000:  # embedded object
            self.inline_object(table, name, f.get("elem"))
            return ""
        raise ValueError(f"no rule for code {code:#x} ({name}) at {self.i:#x}")

    def typed(self, name: str, table: list) -> str:
        """A typed value list (``phoenix.typed_values``): a varint count, then per value its id, code and value."""
        n = self.varint()
        for k in range(n):
            uid, inner = struct.unpack("<IH", self.take(6))
            if inner in TYPED_OBJECTS:
                h, tag = struct.unpack("<II", self.take(8))
                self.note(f"{name}[{k}] = {uid:08x} {inner:#x} {h:08x} {tag}")
                self.inline_object(table, f"{name}[{k}]")
            else:
                tag = struct.unpack("<I", self.take(4))[0]
                self.note(f"{name}[{k}] = {uid:08x} {inner:#x} {tag}: " + self.boxed(inner, "value"))
        return f"{name} = [{n}] typed values"

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
        hdr = self.header()
        hdr["shape_end"] = self.i
        self.guid_of = {h: g for h, g, _ in hdr["table"]}
        self.kind_order = [k for k, _ in hdr["totals"]]
        graw = self.take(16)
        g = uuid.UUID(bytes_le=graw)
        h = struct.unpack("<I", self.take(4))[0]
        self.root = {"start": self.i - 20, "flags": None, "guid": graw, "type": h}
        self.note(f"root {h:08x} {self.s.get(f'{h:08x}', {}).get('name')} guid {g}")
        self.depth = 1
        self.object_body(h, hdr["table"], None, self.root)
        hdr["consumed"] = self.i
        hdr["size"] = len(self.b)
        return hdr


def load(data: bytes, schema: dict) -> Decoder:
    """Decode a whole file; a file with bytes left over is refused."""
    d = Decoder(schema, data)
    r = d.decode()
    if r["consumed"] != r["size"]:
        raise ValueError(f"decoded {r['consumed']} of {r['size']} bytes")
    return d


def type_table(data: bytes) -> list[tuple[str, str, int]]:
    """The type table of a file (type hash, schema GUID, object count), without decoding its body."""
    return Decoder({}, data).header()["table"]
