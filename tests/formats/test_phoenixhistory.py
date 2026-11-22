"""The History Editions' Phoenix files (formats.phoenixhistory): a typed value list after the counted fields, with
plain values and inline objects, read and written byte for byte; a record whose parent was another one; the layouts
built from the schemas; and, with the games installed, every one of their 732 files decoded and re-encoded exactly."""

from __future__ import annotations

import os
import struct
import uuid
from pathlib import Path

import pytest

from annotoolbox.formats import leb128, phoenix, phoenixedit, phoenixhistory, phoenixread, phoenixwrite
from annotoolbox.sources import gametree

OBJ, BASE, MID, T, COLOR = "af01aeda", "22222222", "44444444", "11111111", "33333333"
FIELD = {"elem": None, "flags": "0x2001", "offset": 8}
TYPED = {**FIELD, "hash": "45257b69", "code": "0x1f01", "name": "DynamicProperties", "trailing": True}


def _record(h: str, parent: str | None, fields: list[dict], guid: str = "") -> dict:
    return {"hash": h, "name": None, "parent": parent, "guid": guid or str(uuid.uuid5(uuid.NAMESPACE_URL, h)),
            "fields": fields}  # fmt: skip


SCHEMA = {
    OBJ: _record(OBJ, None, [{**FIELD, "hash": "00000000", "code": "0x0", "offset": 0, "name": None}]),
    BASE: _record(BASE, OBJ, []),
    T: _record(T, BASE, [{**FIELD, "hash": "a0000001", "code": "0x100", "name": "A"}, TYPED]),
    COLOR: _record(COLOR, OBJ, [{**FIELD, "hash": "c0000001", "code": "0x400", "name": "C"}]),
}


def _varint(v: int) -> bytes:
    return leb128.encode(v)


def _file(table: list[tuple[str, int]], totals: list[tuple[int, int]], untyped: int, tokens: list[tuple],
          body: bytes) -> bytes:  # fmt: skip
    """A whole file: header, type table (hash, object count), kind totals, the typed count, tokens, the body."""
    out = bytearray(phoenixwrite.header("WI")) + _varint(len(table))
    for h, count in table:
        out += struct.pack("<I", int(h, 16)) + uuid.UUID(SCHEMA[h]["guid"]).bytes_le + _varint(count)
    out += _varint(len(totals)) + b"".join(bytes([k]) + _varint(n) for k, n in totals)
    toks = b"".join(bytes([t[0]]) + b"".join(_varint(x) for x in t[1:]) for t in tokens)
    out += _varint(untyped) + _varint(len(tokens)) + _varint(len(toks)) + toks + b"\x02"
    return bytes(out) + body


def _typed_file(after: int = 0) -> bytes:
    """A root with A and a typed value list: a bool, an inline object (a COLOR with C = 5), ``after`` more bools."""
    color = bytes([2, 0]) + struct.pack("<I", int(COLOR, 16)) + bytes([2, 0x40, 5])
    values = (_varint(2 + after) + struct.pack("<IHI", 0xAABBCCDD, 0x100, 33) + bytes([1])
              + struct.pack("<IHII", 0x11223344, 0x4000, int(COLOR, 16), 33) + color
              + b"".join(struct.pack("<IHI", 7 + i, 0x100, 33) + bytes(1) for i in range(after)))  # fmt: skip
    body = bytes(range(16)) + struct.pack("<I", int(T, 16)) + bytes([2, 0x40, 1]) + values
    tokens = [(0x40, 1, 0), (0, 2 + after), (1, 1), (0x40, 1, 3), *[(1, 1)] * after]
    return _file([(T, 1), (OBJ, 0), (BASE, 0), (COLOR, 1)], [(1, 1 + after)], 2 + after, tokens, body)


def test_a_typed_value_list_after_the_counted_fields_round_trips():
    data = _typed_file()
    d = phoenixread.load(data, SCHEMA)
    typed = phoenixedit.get_entry(d.m_root, "DynamicProperties")
    assert d.m_root["n"] == 2 and typed is not None  # the count covers Object and A only
    assert [c for _, c, _ in phoenix.typed_values(typed)] == [0x100, 0x4000]
    assert [o["type"] for o in typed["objs"]] == [int(COLOR, 16)] and "gaps" not in typed  # the object ends it
    assert phoenixwrite.save(d) == data  # tokens: the list's count as kind 0, the bool, the object's type token


def test_the_values_after_an_inline_object_are_kept_with_it():
    data = _typed_file(after=2)
    d = phoenixread.load(data, SCHEMA)
    typed = phoenixedit.get_entry(d.m_root, "DynamicProperties")
    assert len(typed["gaps"]) == 1 and len(typed["gaps"][0]) == 2 * 11
    values = phoenix.typed_values(typed)
    assert [(u, c) for u, c, _ in values] == [(0xAABBCCDD, 0x100), (0x11223344, 0x4000), (7, 0x100), (8, 0x100)]
    assert values[0][2] == b"\x01" and values[1][2] is typed["objs"][0]  # a plain value's bytes, the object itself
    assert phoenixwrite.save(d) == data


def test_a_record_of_a_version_can_have_another_parent(monkeypatch):
    schema = {**SCHEMA, MID: _record(MID, OBJ, [{**FIELD, "hash": "b0000001", "code": "0x800", "name": "M"}])}
    versions = {h: r["guid"] for h, r in schema.items()}
    assert phoenix.type_chain(schema, T, versions) == [OBJ, BASE, T]
    monkeypatch.setitem(phoenixhistory.PARENTS, (T, versions[T]), MID)
    assert phoenix.type_chain(schema, T, versions) == [OBJ, MID, T]
    assert phoenix.type_chain(schema, T, {k: v for k, v in versions.items() if k != MID}) == [OBJ, T]  # not saved
    assert [f["name"] for _, f in phoenix.file_layout(schema, T, versions)] == [None, "M", "A", "DynamicProperties"]


def test_a_version_stored_with_fewer_fields_is_read_by_the_count_the_object_stores(monkeypatch):
    """Some files of one version store fewer fields (``COUNTED``): the object's field count picks their list."""
    u = "55555555"
    a, b = {**FIELD, "hash": "a0000001", "code": "0x100", "name": "A"}, {**FIELD, "hash": "b0000002", "code": "0x800"}
    schema = {**SCHEMA, u: _record(u, BASE, [a, {**a, "hash": "a0000002", "name": "A2"}])}
    body = bytes(range(16)) + struct.pack("<I", int(u, 16)) + bytes([2, 0x40]) + struct.pack("<I", 0x01020304)
    out = bytearray(phoenixwrite.header("WI")) + _varint(3)
    for h in (u, OBJ, BASE):
        out += struct.pack("<I", int(h, 16)) + uuid.UUID(schema[h]["guid"]).bytes_le + _varint(int(h == u))
    data = bytes(out) + _varint(0) + _varint(0) + _varint(1) + _varint(3) + bytes([0x40, 1, 0, 2]) + body
    with pytest.raises(ValueError):  # without the list the u32 is read as a bool of the current layout (4: none)
        phoenixread.load(data, schema)
    monkeypatch.setattr(phoenix, "_g_version_layouts", {(u, schema[u]["guid"], 1): [{**b, "name": "B"}]})
    d = phoenixread.load(data, schema)
    assert [e["field"]["name"] for e in d.m_root["fields"]] == ["B"] and d.m_root["fields"][0]["raw"] == bytes(
        [4, 3, 2, 1]
    )
    assert phoenixwrite.save(d) == data


def test_the_history_layouts_are_built_from_the_schemas():
    layouts, types = phoenix._history()
    widget = [f["name"] for f in layouts[("0c454dec", "9f985b52-7e48-9a2d-a33f-4d5c81841095")]]
    assert widget[4:11] == ["HorizontalAnchorConstraint", "HorizontalAnchorMetrics", "VerticalAnchorConstraint",
                            "VerticalAnchorMetrics", "HorizontalResizeMetrics", "VerticalResizeMetrics",
                            "KeepAspectRatio"]  # fmt: skip
    container = layouts[("0f561333", "a9994114-44c2-fedd-ffec-e020b06a6ab7")]
    assert container[-1]["trailing"] and container[3]["name"] == "NoAutoGameObjectCreationCustomPropertyIds"
    assert types["cd6d9d63"]["name"] == "TextStyleAsset" and len(types["cd6d9d63"]["fields"]) == 15
    assert all(phoenixhistory.field_hash(f["name"]) == f["hash"] for f in types["0c3c261e"]["fields"] if f["name"])
    assert "cd6d9d63" in phoenix.full_schema(phoenix.load_schema())


def test_the_layout_table_is_shared_only_once_it_is_whole(monkeypatch):
    """Two threads decode at once (the UI editor's window and its layout): a half-built table would read files in the
    wrong layout, so it is published whole or not at all."""
    monkeypatch.setattr(phoenix, "_g_version_layouts", None)

    def broken():
        raise RuntimeError("the History Editions' layouts did not build")

    monkeypatch.setattr(phoenix, "_history", broken)
    with pytest.raises(RuntimeError):
        phoenix.version_layouts()
    assert phoenix._g_version_layouts is None


def _history_files(variable: str):
    folder = os.environ.get(variable)
    if not folder:
        pytest.skip(f"{variable} is not set")
    game = "anno1404" if variable == "ANNO1404_DIR" else "anno1701"
    tree = gametree.open_game(game, Path(folder))
    return tree, [p for p in tree.under("data/ui/studio/gamedata/") if tree.read(p)[:3] == b"PHX"]


@pytest.mark.parametrize("variable", ["ANNO1404_DIR", "ANNO1701_DIR"])
def test_every_file_of_the_installed_game_decodes_and_re_encodes_byte_for_byte(variable):
    tree, paths = _history_files(variable)
    schema = phoenix.load_schema()
    assert len(paths) == 732
    for p in paths:
        data = tree.read(p)
        d = phoenixread.load(data, schema)
        assert not [k for k in d.m_layouts if "guessed" in k[1]], p  # every layout is a known one
        assert phoenixwrite.save(d) == data, p


def test_every_file_of_the_installed_1800_decodes_and_re_encodes_byte_for_byte():
    """1800's 6,870 files, the earliest editors' among them (``EARLY_1800``, ``COUNTED``): each one decoded in known
    layouts and re-encoded exactly, and every listed version used by one of them."""
    folder = os.environ.get("ANNO1800_DIR")
    if not folder:
        pytest.skip("ANNO1800_DIR is not set")
    tree = gametree.open_game("anno1800", Path(folder))
    schema, versions = phoenix.load_schema(), set(phoenixhistory.EARLY_1800) | {k[:2] for k in phoenixhistory.COUNTED}
    users: dict = {}
    files = 0
    for p in tree.under("data/ui/"):
        data = tree.read(p)
        if data[:3] != b"PHX":
            continue
        d = phoenixread.load(data, schema)  # a version none of the lists has would fail here or leave bytes over
        assert phoenixwrite.save(d) == data, p
        files += 1
        for h, g, _ in phoenixread.type_table(data):
            users[(h, g)] = users.get((h, g), 0) + 1
    assert files >= 6870 and all(users.get(v) for v in versions)  # each version proven by a file that re-encodes
