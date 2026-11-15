"""Anno 117's compiled UI files (``PHX??``): what the reader, writer and editor share.

The header constants, the reflection schema and its layout versions (which fields an object of a type stores, and
which an older save of the type lacks), and the encoding of single values. ``phoenixread`` decodes a file into an
object tree, ``phoenixwrite`` encodes one, ``phoenixedit`` changes and builds trees. The format is in
``docs/formats/phoenix-scene-format.md``.
"""

from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path

from ..paths import format_data
from . import leb128, phoenixhistory

SCHEMA_PATH = format_data("phoenix-schema.json")  # the package's copy, or the checkout's docs/formats/
# other executables' schemas: their type records are older layout versions of the same types (Anno 1800's)
VERSION_SCHEMA_PATHS = (format_data("phoenix-schema-1800.json"),)
GENERATED = "data/ui/studio/generated/"
SERIALIZER = 0x109
MAGIC_CONST = 0xBAB1F4CE
FLAGS = 0x1CF0
TOOL_VERSION = (28, 9, 1)  # the editor build most shipped files carry
TYPED_VALUES = 0x1F01  # a list of typed values (the History Editions' DynamicProperties), not a code of the schema
TYPED_OBJECTS = (0x4000, 0x4200)  # typed values that are inline objects
OBJECT = "af01aeda"  # the root record of every type

# fixed-size type codes -> bytes (the low bit of a code makes it a list of the type)
SIZES = {
    0x100: 1,  # bool
    0x400: 1,  # u8
    0x600: 2,  # u16
    0x700: 4,
    0x800: 4,  # u32
    0x900: 8,  # i64 (Int64Key)
    0xA00: 8,  # u64 (UInt64Key)
    0xB00: 4,  # enum
    0x1100: 8,  # vec2
    0x1200: 12,  # vec3
    0x1300: 16,
    0x1900: 16,  # id
    0x1A00: 8,
}

# Layout versions


# The schema GUID of a type-table entry is the layout version of that record. Older versions still found in the
# shipped files, as (record hash, schema guid) -> fields of that record the version does not have. Derived from
# the files (each pair always lacks the same fields; the current GUIDs lack none).
OLD_LAYOUTS = {
    ("0c454dec", "73346670-5873-babb-3af2-576b8629e543"): ("e4c4a02a",),  # WidgetInstance without ConsiderSize
    ("1795cb2b", "51bac1e4-9ca2-d4a9-bdab-677196f7816d"): ("b1b11020",),  # SceneTemplate
    ("1795cb2b", "e4e1548e-b3b6-d200-3819-bea0d003e757"): ("b1b11020",),  # SceneTemplate
    ("17afa8e7", "e1281f0c-7421-fe6f-15db-e59a5dc51430"): (
        "a115f62d",
        "d7645aed",
    ),  # WidgetTypeTemplate without Components, d7645aed
    ("1bb72d66", "50143ba4-1ab8-14f2-6344-9ed8fe337462"): ("7f67eaef",),  # PropertyContainer without UseCount
    ("41a404e5", "960a9a6c-30e7-1df3-afce-ab4cf76e5ce7"): ("30411ebc",),  # LabelWidgetInstance without ActiveAreaType
    ("41a404e5", "bfb3caf6-84c9-00d8-9cb3-a4835c6cb92e"): (
        "dd0d7908",
        "30411ebc",
    ),  # ... without BaselineY, ActiveAreaType
    # font language entry: FallbackFontGroup kept (a set one read it), df84b302 gone, as in 1800's schema
    ("4674a895", "12ebe5dd-b69e-7105-e3c8-f70e76c80e91"): ("df84b302", "5962aa9a"),
    ("4674a895", "5a46c6af-3a4b-f283-dae1-07eec564c03b"): ("5962aa9a",),
    ("4674a895", "d181fc46-22ee-3e5c-fdca-71b262d2ab71"): ("bd64a1b1", "5962aa9a"),
    ("6dcd0e65", "055cf849-1d60-506c-34a9-c574a159b2ca"): ("8c00a6d0", "df84b302", "5962aa9a"),  # PHXLF font type
    ("6dcd0e65", "130a697b-011c-6a0b-a027-2e2814972f96"): ("bd64a1b1", "5962aa9a"),
    ("6dcd0e65", "dfcd410e-3af8-a315-1060-3faffc46e655"): ("bd64a1b1",),
    ("6fd1ffc4", "d08796c2-f2c4-b9f5-7a26-0e1cbb221707"): (
        "af9ddbd7",
        "570c3823",
        "3f22df21",
    ),  # ConverterInstance: BindingMode only
    ("84f12f7b", "8d1f8d9d-4326-cd75-6fcf-b534c597f314"): ("1d36b876",),  # ComponentTemplate
    ("8eeb57e6", "e4d6d177-f675-4424-07e3-cfcab1c0cf60"): ("586cbbb4",),  # VideoWidgetInstance without IsView
    ("9f98c15a", "53c73067-058c-921c-b190-37ba7f6db000"): (
        "216a0a13",
        "3dd0d65f",
        "c8b2c2f7",
        "a6d87800",
        "71af3881",
    ),  # NumbersStyle .. StylisticAlternate
    ("d90a3eef", "04316982-54b0-f45e-a24c-9e9853a1a44d"): (
        "002d405f",
        "00fa28c8",
    ),  # FontAsset base of GraffitiFontAsset
    ("da06bd6e", "5088ea16-cf44-6c4e-9841-c1d00ec10dc5"): (
        "8e4aa971",
        "7ff8905c",
        "7fefac90",
    ),  # ContainerWidgetTemplate
    ("edcf0433", "864ca57a-4288-31ab-68f1-38d2ff89d9d1"): ("af9ddbd7",),  # without IsDynamic
    # two versions only Anno 1800's files carry (derived from them the same way)
    ("da06bd6e", "ce132fa8-5b7f-64fc-4254-13ca8385416b"): ("18b0dff0", "a115f62d", "7fefac90"),
    ("232cec0d", "0b884292-e629-8e95-55fc-a11cbf38d87f"): ("4ebb2751",),  # ImageAsset without TextureFiltering
}
# types the files use that 117's schema dump lacks: an upgrader of old files, still in 1800's schema
EXTRA_TYPES = {
    "d251ac32": {"hash": "d251ac32", "name": "SubstractConverterInstance", "parent": "0f7f54d0", "fields": []},
}
# older versions that are not a subset of the current fields: (type hash, schema guid) -> the fields after the Object
# bit (schema field hashes, or (name, code) for a field the current schema no longer has)
_FALLBACK_FONT_OLD = (
    "70a6a7ec",
    ("old-byte", "0x400"),
    "9e9fb14b",
    "1c2c57ba",
    "619e4928",
)  # a byte after Font, no ScalingType
ALT_LAYOUTS = {
    ("ca32a806", "2795e325-23ed-7e61-c603-2561554e6baa"): _FALLBACK_FONT_OLD,
    ("ca32a806", "b88baba8-0fd6-520f-c907-0760b5dc754b"): _FALLBACK_FONT_OLD,
}


# The schema


def load_schema(path=None) -> dict:
    """A schema file (``path``: a path or a package resource; by default 117's, ``SCHEMA_PATH``)."""
    source = Path(path) if isinstance(path, str) else path or SCHEMA_PATH
    return json.loads(source.read_text(encoding="utf-8"))


def full_schema(schema: dict) -> dict:
    """The schema with the types the dump lacks (``EXTRA_TYPES``, the History Editions' new types) added."""
    extra = {**EXTRA_TYPES, **_history()[1]}
    return {**schema, **{h: r for h, r in extra.items() if h not in schema}}


def chain_records(schema: dict, h: str, versions: dict[str, str] | None = None) -> list[dict]:
    """The records of a type's parent chain, Object first. With layout versions (``versions``): the chain of those
    versions (``phoenixhistory.PARENTS``), and when they cover the type as a type table does (Object and the type
    itself) without the records they lack, which did not exist when the file was saved."""
    out = []
    cur = schema.get(h)
    while cur:
        out.append(cur)
        parent = phoenixhistory.PARENTS.get((cur["hash"], versions.get(cur["hash"]))) if versions else None
        cur = schema.get(parent or cur.get("parent") or "")
    out.reverse()
    whole = versions and OBJECT in versions and h in versions
    return [r for r in out if r["hash"] in versions] if whole else out


def type_chain(schema: dict, h: str, versions: dict[str, str] | None = None) -> list[str]:
    """Record hashes of a type's parent chain, Object first (``versions``: as ``chain_records``)."""
    return [r["hash"] for r in chain_records(schema, h, versions)]


def is_stored(f: dict) -> bool:
    """False for computed (offset 0) and editor-only (flags 0x2000) fields; the Object base bit is kept."""
    return int(f["code"], 16) == 0 or (f["offset"] != 0 and int(f["flags"], 16) != 0x2000)


def stored_fields(schema: dict, h: str) -> list[tuple[dict, dict]]:
    """(record, field) pairs in serialization order: parent chain first, raw table order, transient fields skipped."""
    return [(rec, f) for rec in chain_records(schema, h) for f in rec["fields"] if is_stored(f)]


_version_layouts: dict[tuple[str, str], list[dict]] | None = None
_history_built: tuple[dict, dict] | None = None


def _history() -> tuple[dict[tuple[str, str], list[dict]], dict[str, dict]]:
    """The History Editions' layouts and new types (``phoenixhistory.build``) over this schema and 1800's."""
    global _history_built
    if _history_built is None:
        schemas = [load_schema(p) for p in (SCHEMA_PATH, *VERSION_SCHEMA_PATHS) if p.is_file()]
        _history_built = phoenixhistory.build([{**s, **EXTRA_TYPES} for s in schemas], is_stored)
    return _history_built


def version_layouts() -> dict[tuple[str, str], list[dict]]:
    """(record hash, schema guid) -> its stored fields, from the other executables' schemas (VERSION_SCHEMA_PATHS)
    and the History Editions' layouts (``phoenixhistory``): the exact field list of a layout version the current
    schema has replaced."""
    global _version_layouts
    if _version_layouts is None:  # built whole before it is shared: another thread may decode meanwhile
        built = {}
        for path in VERSION_SCHEMA_PATHS:
            if path.is_file():
                for h, rec in load_schema(path).items():
                    if rec.get("guid"):
                        built[(h, rec["guid"])] = [f for f in rec["fields"] if is_stored(f)]
        built.update(_history()[0])
        _version_layouts = built
    return _version_layouts


def with_versions(
    fields: list[tuple[dict, dict]], guid_of: dict[str, str], chain: list[dict]
) -> list[tuple[dict, dict]]:
    """``fields`` (of a type whose records are ``chain``, Object first) with each record whose layout version in
    ``guid_of`` is another executable's replaced by that version's fields (version_layouts), also a record that
    has no fields today; records in the current or an unknown version stay."""
    known = version_layouts()
    out = []
    for rec in chain:
        g = guid_of.get(rec["hash"])
        key = (rec["hash"], g)
        alt = known.get(key) if g and g != rec.get("guid") and key not in OLD_LAYOUTS else None
        out += [(rec, f) for f in alt] if alt is not None else [(r, f) for r, f in fields if r is rec]
    return out


def file_layout(schema: dict, h: str, versions: dict[str, str]) -> list[tuple[dict, dict]]:
    """The stored fields of type ``h`` in the layout versions ``versions`` names (a file's type table): the chain of
    that version, each record in its version's fields (``with_versions``) less what the version lacks
    (``versioned``). Without ``versions`` the current layout."""
    chain = chain_records(schema, h, versions)
    fields = [(rec, f) for rec in chain for f in rec["fields"] if is_stored(f)]
    return versioned(with_versions(fields, versions, chain), versions) if versions else fields


def counted(fields: list[tuple[dict, dict]], guid_of: dict[str, str], n: int) -> list[tuple[dict, dict]] | None:
    """``fields`` with the record whose version some files store with fewer fields (``phoenixhistory.COUNTED``) in
    its list for the count ``n`` the object stores, None when no record has one."""
    known = version_layouts()
    for rec in {id(r): r for r, _ in fields}.values():
        own = [x for x in fields if x[0] is rec]
        alt = known.get((rec["hash"], guid_of.get(rec["hash"]), n - len(fields) + len(own)))
        if alt is not None and own:
            at = fields.index(own[0])
            rest = [x for x in fields if x[0] is not rec]
            return rest[:at] + [(rec, f) for f in alt] + rest[at:]
    return None


def versioned(fields: list[tuple[dict, dict]], guid_of: dict[str, str]) -> list[tuple[dict, dict]]:
    """``fields`` without those the layout versions in ``guid_of`` lack (OLD_LAYOUTS)."""
    gone = set()
    for rec in {id(r): r for r, _ in fields}.values():
        gone |= {(rec["hash"], h) for h in OLD_LAYOUTS.get((rec["hash"], guid_of.get(rec["hash"])), ())}
    return [(r, f) for r, f in fields if (r["hash"], f["hash"]) not in gone]


def alt_layout(fields: list[tuple[dict, dict]], spec: tuple) -> list[tuple[dict, dict]]:
    """An ALT_LAYOUTS entry as (record, field) pairs: schema field hashes, or (name, code) for gone fields."""
    by_hash = {f["hash"]: (r, f) for r, f in fields}
    leaf = fields[-1][0]
    out = [fields[0]]  # the Object base bit
    for x in spec:
        if isinstance(x, tuple):
            out.append((leaf, {"name": x[0], "hash": x[0], "code": x[1], "flags": "0x2001", "offset": 1, "elem": None}))
        else:
            out.append(by_hash[x])
    return out


def layout_versions(tables: list[list[tuple[str, str, int]]]) -> dict[str, str]:
    """Per type hash the layout version new objects are written in: the newest the shipped files use (fewest
    fields missing, OLD_LAYOUTS), the one with the most objects among equals. Labels have no shipped file in
    the schema's full layout, so theirs is an older one, as the game's own. ``tables``: ``phoenixread.type_table``
    of the files to follow."""
    seen: dict[str, dict[str, int]] = {}
    for table in tables:
        for h, g, count in table:
            if (h, g) in ALT_LAYOUTS:
                continue
            seen.setdefault(h, {})
            seen[h][g] = seen[h].get(g, 0) + max(count, 1)
    return {h: min(c, key=lambda g, h=h, c=c: (len(OLD_LAYOUTS.get((h, g), ())), -c[g])) for h, c in seen.items()}


# Single values


def decode_string(raw: bytes) -> str:
    """A stored string (varint length, kind byte, UTF-8)."""
    n, used = leb128.decode(raw)
    return raw[used + 1 :].decode("utf-8") if n else ""


def encode_value(code: int, value) -> bytes:
    """A plain value in the file encoding of its type code."""
    if code == 0x100:
        return bytes([1 if value else 0])
    if code == 0x400:
        return bytes([value & 0xFF])
    if code == 0x600:
        return struct.pack("<H", value)
    if code in (0x700, 0x800, 0xB00):
        return struct.pack("<I", value & 0xFFFFFFFF)
    if code == 0x1000:
        return struct.pack("<f", value)
    if code == 0x1100:
        return struct.pack("<2f", *value)
    if code == 0x1200:
        return struct.pack("<3f", *value)
    if code == 0x1300:
        return struct.pack("<4f", *value)
    if code == 0x1700:
        raw = value.encode("utf-8")
        return leb128.encode(len(raw)) + (b"\x01" + raw if raw else b"")
    if code == 0x1900:
        return uuid.UUID(str(value)).bytes_le
    if code == 0x801:
        return leb128.encode(len(value)) + b"".join(struct.pack("<I", v) for v in value)
    raise ValueError(f"no encoder for type code {code:#x}")


def value_size(code: int, raw: bytes, at: int) -> int:
    """The length of the plain value of type ``code`` stored at ``raw[at:]``."""
    if code in SIZES:
        return SIZES[code]
    if code == 0x1000:
        return 4
    if code == 0x1700:
        n, used = leb128.decode(raw[at:])
        return used + (n + 1 if n else 0)
    if code in (0x4300, 0x4400):
        return 21 if raw[at] else 1
    raise ValueError(f"no size rule for type code {code:#x}")


def typed_values(entry: dict) -> list[tuple[int, int, bytes | dict]]:
    """The values of a ``TYPED_VALUES`` entry, in order: (id, type code, the plain value's bytes or the inline
    object). Per value: a u32 id, the u16 type code, then for an object (``TYPED_OBJECTS``) its type hash, a u32 (33)
    and the inline object, else the u32 and the plain value; the inline objects are the entry's ``objs``, so its
    bytes without them (head and gaps) are read here."""
    raw = entry["raw"] if "raw" in entry else entry["head"] + b"".join(entry.get("gaps", []))
    n, at = leb128.decode(raw)
    out, objs = [], iter(entry.get("objs", ()))
    for _ in range(n):
        uid, code = struct.unpack_from("<IH", raw, at)
        if code in TYPED_OBJECTS:
            out.append((uid, code, next(objs)))
            at += 14
        else:
            size = value_size(code, raw, at + 10)
            out.append((uid, code, raw[at + 10 : at + 10 + size]))
            at += 10 + size
    return out


def boxed(code: int, value) -> bytes:
    """A 0x1f00 boxed value (a property value, a custom property's default)."""
    return struct.pack("<H", code) + encode_value(code, value)


def ref_bytes(type_hash: str, guid: str | bytes) -> bytes:
    """A set asset reference (0x4300/0x4400): flag 1, the type hash, the GUID."""
    g = guid if isinstance(guid, bytes) else uuid.UUID(guid).bytes_le
    return b"\x01" + struct.pack("<I", int(type_hash, 16)) + g
