"""The Phoenix constructor and binding builder (phoenix): objects as the shipped files store them, round trips."""

import struct
import uuid

import pytest

from annotoolbox.formats import leb128, phoenix, phoenixedit, phoenixread, phoenixwrite
from fakes import installed

SCHEMA = phoenix.load_schema()
VERSIONS = {h: str(uuid.uuid5(uuid.NAMESPACE_URL, h)) for h in phoenix.full_schema(SCHEMA)}  # no older layout anywhere


def test_constructed_objects_match_the_shipped_encodings():
    # the most common encodings of these objects in the shipped files (format facts, phoenix-scene-format)
    assert (
        phoenixwrite.encode_object(phoenixedit.new_object(SCHEMA, "c346a1bd")).hex()
        == "0200bda146c3057800000000000100000001000000"
    )
    assert (
        phoenixwrite.encode_object(phoenixedit.new_object(SCHEMA, "9df79719")).hex()
        == "02001997f79d0470000000000100000001000000"
    )
    assert phoenixwrite.encode_object(phoenixedit.new_object(SCHEMA, "33d396c1")).hex() == (
        "0200c196d333047000000000000200ed6797a70470ffffff"
    )
    item = phoenixedit.new_object(SCHEMA, "7e4fae85")
    phoenixedit.set_field(item, "DataMode", 1)
    assert phoenixwrite.encode_object(item).hex() == "020085ae4f7e03600100000000"


def test_a_layout_version_drops_its_missing_fields():
    full = phoenixedit.new_object(SCHEMA, "41a404e5")
    current = phoenixedit.new_object(SCHEMA, "41a404e5", versions={"41a404e5": "960a9a6c-30e7-1df3-afce-ab4cf76e5ce7"})
    old = phoenixedit.new_object(SCHEMA, "41a404e5", versions={"41a404e5": "bfb3caf6-84c9-00d8-9cb3-a4835c6cb92e"})
    assert current["n"] == full["n"] and old["n"] == full["n"] - 1  # 74 shipped labels lack BaselineY
    assert not phoenixedit.has_field(old, "BaselineY") and phoenixedit.has_field(old, "ActiveAreaType")


LABEL_GUID = uuid.UUID("12345678-0000-4000-8000-00000000abcd")
# A shipped label's fields from AllowResize on (the main menu's "Text": AlignmentX and AlignmentY Center, a plane of
# 260 x 32, Visible, DisplayedContent, Visible, DisplayedContent), its style GUID replaced
LABEL_TAIL = (
    "01" "00000000" "016cc997c6" + LABEL_GUID.bytes_le.hex() + "01000000" "00" "01000000" "0000803f" "00000000"
    "00000000" "00000000" "00008243" "00000042" "05000000" "01000000" "04000000" "01000000" + "00" * 36
)  # fmt: skip


def test_a_label_stores_its_fields_in_the_order_the_game_reads_them():
    # TextSizingFactor (flags 0x2080) is set at run time and not stored, so each name meets its own value (layout.md
    # "What the game's dumps show": 557 labels of the game's dumps against their files)
    assert all(f["name"] != "TextSizingFactor" for _, f in phoenix.stored_fields(SCHEMA, "41a404e5"))
    label = phoenixedit.new_object(SCHEMA, "41a404e5", guid=uuid.uuid4().bytes_le)
    for name, value in (("AllowResize", True), ("AlignmentX", 1), ("AlignmentY", 1), ("TextPlane", (260.0, 32.0)),
                        ("HorizontalOverflow", 5), ("HorizontalSizing", 1), ("VerticalOverflow", 4),
                        ("VerticalSizing", 1)):  # fmt: skip
        phoenixedit.set_field(label, name, value)
    phoenixedit.set_text_style(label, str(LABEL_GUID))
    assert phoenixwrite.encode_object(label).hex().endswith(LABEL_TAIL)
    root, wf = _file_with(label)
    wf.finish("test")
    again = phoenixread.load(phoenixwrite.write_file(SCHEMA, "WI", root, VERSIONS), SCHEMA)
    (read,) = [n for n in phoenixedit.walk(again.root) if n.get("schema_type") == "41a404e5"]
    assert phoenixedit.get_ref(read, "TextStyle") == str(LABEL_GUID)
    raw = {e["field"]["name"]: e["raw"] for e in read["fields"] if e["record"] == "41a404e5"}
    assert raw["LineSpacing"] == struct.pack("<f", 1.0) and raw["ResizeAnchor"] == bytes(4)  # the defaults
    assert raw["TextPlane"] == struct.pack("<2f", 260.0, 32.0) and raw["VerticalOverflow"] == bytes([4, 0, 0, 0])


def test_layout_versions_prefer_the_newest_used():
    new, old = "00000000-0000-0000-0000-000000000001", "d08796c2-f2c4-b9f5-7a26-0e1cbb221707"
    labels = ("960a9a6c-30e7-1df3-afce-ab4cf76e5ce7", "bfb3caf6-84c9-00d8-9cb3-a4835c6cb92e")
    tables = [[("6fd1ffc4", old, 50)], [("6fd1ffc4", new, 1), ("41a404e5", labels[1], 9)], [("41a404e5", labels[0], 2)]]
    got = phoenix.layout_versions(tables)
    assert got["6fd1ffc4"] == new  # the current layout, however rare
    assert got["41a404e5"] == labels[0]  # among older ones the fewest missing fields


def _file_with(instance: dict) -> tuple[dict, phoenixedit.WidgetFile]:
    root = phoenixedit.new_object(SCHEMA, phoenixedit.WIDGET_TEMPLATE, guid=bytes(range(16)), root=True)
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(instance)
    return root, wf


def test_widget_file_bindings_round_trip():
    label = phoenixedit.new_object(SCHEMA, "41a404e5", guid=uuid.uuid4().bytes_le)
    phoenixedit.set_name(label, "Value")
    root, wf = _file_with(label)
    shown = wf.add_property("Shown", "bool")
    wf.add_property("Amount", "float", 2.0)
    vis = wf.own("Shown")
    wf.bind(vis, wf.end(label, [phoenixedit.BUILTIN["Visible"]]))
    wf.bind(wf.own("Amount"), wf.end(label, [0x70000001, 0x7AB17C97]))
    wf.finish("test")
    data = phoenixwrite.write_file(SCHEMA, "WI", root, VERSIONS)
    d = phoenixread.load(data, SCHEMA)
    assert phoenixwrite.save(d) == data
    props = phoenixedit.objects(d.root, "18b0dff0")
    assert [phoenixedit.object_name(p) for p in props] == ["Shown", "Amount"]
    assert phoenixedit.u32_of(props[0], "UniqueId") == shown and 0x10000000 <= shown < 0x20000000
    assert phoenixedit.get_entry(props[1], "DefaultValue")["raw"] == struct.pack("<Hf", 0x1000, 2.0)
    pcs = phoenixedit.objects(d.root, "PropertyContainers")
    cpcs = phoenixedit.objects(d.root, "7ff8905c")
    (b1, b2) = phoenixedit.objects(d.root, "a4336119")
    src = phoenixedit.get_entry(b1, "28c38074")["raw"]
    dst = phoenixedit.get_entry(b1, "cec4084b")["raw"]
    assert src[1] == len(pcs) + 0  # own properties come after the property containers (no constants)
    assert phoenixedit.u32_of(cpcs[src[1] - len(pcs)], "CustomPropertyIndex") == 0
    target = pcs[dst[1]]
    (pid,) = phoenixedit.objects(target, "PropertyPath")
    assert phoenixedit.u32_of(pid, "UniqueId") == 0x80000007 and phoenixedit.u32_of(pid, "PropertyField") == 1
    (dobj,) = phoenixedit.objects(
        phoenixedit.objects(d.root, "8fda299c")[phoenixedit.u32_of(target, "a86f2815")], "15a30137"
    )
    assert (
        phoenixedit.u32_of(dobj, "DataMode") == 0
        and phoenixedit.get_entry(dobj, "b25f63de")["raw"][5:] == label["guid"]
    )
    assert phoenixedit.u32_of(cpcs[0], "UseCount") == 1 and phoenixedit.u32_of(target, "UseCount") == 1
    assert phoenixedit.get_entry(b2, "28c38074")["raw"][1] == len(pcs) + 1


def test_widget_file_refuses_a_property_twice():
    _, wf = _file_with(phoenixedit.new_object(SCHEMA, "c19f99dd", guid=uuid.uuid4().bytes_le))
    wf.add_property("A", "bool")
    with pytest.raises(ValueError):
        wf.add_property("A", "float")


def test_image_asset_and_color():
    asset = phoenixedit.image_asset(SCHEMA, str(uuid.uuid4()), "data\\ui\\fhd\\x.png", (10.0, 20.0), versions=VERSIONS)
    data = phoenixwrite.write_file(SCHEMA, "IM", asset, VERSIONS)
    assert data[:5] == b"PHXIM" and phoenix.decode_string(
        phoenixedit.get_entry(phoenixread.load(data, SCHEMA).root, "ImageId")["raw"]
    ) == ("data\\ui\\fhd\\x.png")
    image = phoenixedit.new_object(SCHEMA, "c19f99dd", guid=uuid.uuid4().bytes_le)
    phoenixedit.set_color(image, "ColorTint", (1, 2, 3))
    (ctype,) = phoenixedit.objects(image, "ColorTint")
    (color,) = phoenixedit.objects(ctype, "Color")
    assert b"".join(e["raw"] for e in color["fields"]) == bytes([1, 2, 3])


def test_builtin_ids_are_the_game_tables():
    b = phoenixedit.BUILTIN
    assert (b["Position"], b["Size"], b["Scale"], b["Visible"]) == (0x80000001, 0x80000004, 0x80000035, 0x80000007)
    assert (b["Text"], b["LocalizationId"], b["ImageId"]) == (0x80000008, 0x8000000A, 0x8000002A)
    assert phoenixedit.BUILTIN_ANIMATION["Start"] == 0x8000001F and phoenixedit.BUILTIN_VIDEO["Start"] == 0x80000013


OBJ, BASE, T = "af01aeda", "22222222", "11111111"
FIELD = {"elem": None, "flags": "0x2001", "offset": 8}
TINY = {
    OBJ: {
        "hash": OBJ,
        "name": "Object",
        "parent": None,
        "guid": None,
        "fields": [{**FIELD, "hash": "00000000", "code": "0x0", "offset": 0, "name": None}],
    },
    BASE: {"hash": BASE, "name": None, "parent": OBJ, "guid": "00000000-0000-0000-0000-0000000000b0", "fields": []},
    T: {
        "hash": T,
        "name": None,
        "parent": BASE,
        "guid": "00000000-0000-0000-0000-000000000001",
        "fields": [
            {**FIELD, "hash": "a0000001", "code": "0x100", "name": "A"},
            {**FIELD, "hash": "a0000002", "code": "0x800", "name": "B"},
        ],
    },
}
OLD_T = "00000000-0000-0000-0000-000000000002"


def _tiny_file(table: list[str], fields: bytes, n: int, mask: int, root_guid: bytes = bytes(range(16))) -> bytes:
    out = bytearray(phoenixwrite.header("WI")) + leb128.encode(len(table))
    for h in table:
        g = OLD_T if h == T else TINY[h]["guid"] or "00000000-0000-0000-0000-0000000000a0"
        out += struct.pack("<I", int(h, 16)) + uuid.UUID(g).bytes_le + bytes([1 if h == T else 0])
    out += bytes([0, 0, 1, 3, 0x40, 1, 0, 2])  # no kind totals; one token: the root
    return bytes(out) + root_guid + struct.pack("<I", int(T, 16)) + bytes([n, mask]) + fields


def test_a_version_schema_supplies_an_older_record(monkeypatch):
    # the older version has B and a byte the current schema no longer knows, and lacks A
    old = [TINY[T]["fields"][1], {**FIELD, "hash": "a0000009", "code": "0x400", "name": None}]
    monkeypatch.setattr(phoenix, "_version_layouts", {(T, OLD_T): old})
    data = _tiny_file([T, OBJ, BASE], struct.pack("<I", 7) + bytes([5]), 3, 0x60)
    d = phoenixread.load(data, TINY)
    assert [e["field"]["hash"] for e in d.root["fields"]] == ["a0000002", "a0000009"]
    assert phoenixwrite.save(d) == data


def test_a_chain_record_the_type_table_lacks_is_left_out(monkeypatch):
    monkeypatch.setattr(phoenix, "_version_layouts", {(T, OLD_T): TINY[T]["fields"]})
    data = _tiny_file([T, OBJ], bytes([1]) + struct.pack("<I", 9), 3, 0x60)  # saved before BASE existed
    d = phoenixread.load(data, TINY)
    assert phoenixwrite.save(d) == data


def test_the_kind_order_of_a_decoded_file_is_kept():
    label = phoenixedit.new_object(SCHEMA, "41a404e5", guid=uuid.uuid4().bytes_le)
    root, wf = _file_with(label)
    wf.add_property("Shown", "bool")
    wf.bind(wf.own("Shown"), wf.end(label, [phoenixedit.BUILTIN["Visible"]]))
    wf.finish("kinds")
    data = phoenixwrite.write_file(SCHEMA, "WI", root, VERSIONS)
    d = phoenixread.load(data, SCHEMA)
    assert d.kind_order == sorted(d.kind_order, reverse=True) and len(d.kind_order) > 1  # how the editor writes now
    old = list(reversed(d.kind_order))  # editors up to 17.19 wrote another order (1800's files, omtroopscene)
    again = phoenixread.load(phoenixwrite.encode_file(SCHEMA, data[:16], d.root, d.guid_of, old), SCHEMA)
    assert again.kind_order == old and phoenixwrite.save(again) == phoenixwrite.encode_file(
        SCHEMA, data[:16], d.root, d.guid_of, old
    )


@pytest.mark.parametrize("game", ["anno117", "anno1800"])
def test_shipped_files_round_trip(game):
    files = installed(game)
    if files is None:
        pytest.skip(f"needs the installed game ({game})")
    read = files.read if game == "anno117" else files.read_bytes
    paths = list(files.iter_paths(phoenix.GENERATED))[:400]
    done = 0
    for p in paths:
        b = read(p)
        if b[:3] != b"PHX":
            continue
        try:
            d = phoenixread.load(b, SCHEMA)
        except (ValueError, EOFError, IndexError, struct.error):
            assert game == "anno1800", p  # 117: every file; 1800: those of editors older than both schemas fail
            continue
        assert phoenixwrite.save(d) == b, p
        done += 1
    assert done > 100
