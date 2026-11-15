"""The UI editor's comparison with a layout dump of the game (uiedit/gamedump.py): a synthetic dump in the format the
game side writes, held against a synthetic file's elements and preview boxes."""

from __future__ import annotations

import json
import uuid

import pytest

from annotoolbox.uiedit import elements, gamedump, labels, layout


def _g(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


FILE, A, B, C, D = (_g(n) for n in ("file", "a", "b", "c", "d"))


def _el(guid: uuid.UUID, parent: elements.Element) -> elements.Element:
    el = elements.Element({"guid": guid.bytes_le, "schema_type": "c19f99dd", "fields": []}, "image", parent)
    parent.children.append(el)
    return el


def _world(sx: float, sy: float, tx: float, ty: float) -> list[float]:
    return [sx, 0, 0, 0, 0, sy, 0, 0, 0, 0, 1, 0, tx, ty, 0, 1]


def _w(i, owner, guid, world, size, shown=True, template=None, name=None) -> dict:
    return {"id": i, "parent": owner, "owner": owner, "name": name, "guid": str(guid) if guid else None,
            "in": str(FILE) if owner >= 0 else None, "template": str(template) if template else None,
            "class": "ImageWidget", "world": world, "layout": {"bbox_pos": [0, 0, 0], "bbox_size": size},
            "visible": shown, "shown": shown, "alpha_product": 1.0}  # fmt: skip


@pytest.fixture
def setup(tmp_path):
    top = elements.Element({"guid": FILE.bytes_le, "schema_type": "da06bd6e", "fields": []}, "template")
    a, b, c = _el(A, top), _el(B, top), _el(C, top)
    root = _world(0.5, 0.5, 10, 20)  # the window at half the reference size, the root offset by (10, 20)
    dump = {"format": 1, "probe": "ui_dump", "scene": "testscene", "stamp": "x", "ui_scale": [0.5, 0.5],
            "reference_size": [1920, 1080], "widgets": [
                _w(0, -1, None, root, [1920, 1080], template=FILE),
                _w(1, 0, A, _world(0.5, 0.5, 10 + 50, 20 + 25), [200, 100]),  # at (100, 50) in reference pixels
                _w(2, 0, B, _world(0.5, 0.5, 10 + 150, 20 + 150), [40, 40], shown=False),
                _w(3, 0, D, _world(0.5, 0.5, 10, 20), [10, 10], name="made at run time"),
            ]}  # fmt: skip
    path = tmp_path / "x-testscene.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    frame = layout.Frame((1920, 1080))
    frame.boxes[id(a.node)] = layout.Box(layout.translate(100, 50), (200, 100))
    frame.boxes[id(b.node)] = layout.Box(layout.translate(330, 300), (40, 40))  # 30 px off, and shown
    frame.boxes[id(c.node)] = layout.Box(layout.translate(0, 0), (5, 5))
    return gamedump.load(path), top, frame, (a, b, c)


def test_the_game_rects_come_into_the_files_reference_space(setup):
    dump, top, frame, (a, b, _c) = setup
    anchor = gamedump.anchor(dump, top.guid)
    assert anchor is not None and anchor.id == 0
    assert gamedump.rect_in(dump.widgets[1], anchor.world) == pytest.approx((100, 50, 300, 150))
    assert gamedump.to_matrix([1, 2, 0, 0, 3, 4, 0, 0, 0, 0, 1, 0, 5, 6, 0, 1]) == (1, 2, 3, 4, 5, 6)
    assert gamedump.to_matrix(None) is None


def test_differences_beyond_the_tolerance_are_listed(setup):
    dump, top, frame, (a, b, c) = setup
    result = gamedump.compare(dump, top, frame, 2.0)
    kinds = {(r.guid, r.kind) for r in result.differences}
    assert result.matched == 2
    assert (str(B), "visibility") in kinds  # hidden in the game, shown in the preview
    assert (str(C), "only in the preview") in kinds
    assert (str(D), "only in the game") in kinds
    assert all(r.guid != str(A) for r in result.differences)  # the same rectangle
    assert next(r for r in result.differences if r.guid == str(B)).node_id == id(b.node)
    assert ((100, 50, 300, 150), gamedump.GAME) in [(tuple(round(v) for v in r), c) for r, c in result.overlay]
    frame.boxes[id(a.node)] = layout.Box(layout.translate(104, 50), (200, 100))
    moved = gamedump.compare(dump, top, frame, 2.0)
    row = next(r for r in moved.differences if r.guid == str(A))
    assert row.kind == "rect" and row.delta == pytest.approx(4.0)
    assert not any(r.guid == str(A) for r in gamedump.compare(dump, top, frame, 5.0).differences)


def test_a_scene_is_found_by_its_name_and_other_files_are_refused(setup, tmp_path):
    dump, top, frame, _ = setup
    other = elements.Element({"guid": _g("other").bytes_le, "schema_type": "1795cb2b", "fields": []}, "template")
    assert gamedump.anchor(dump, other.guid) is None
    assert gamedump.anchor(dump, other.guid, "TestScene").id == 0
    assert "no widget" in gamedump.compare(dump, other, frame).note
    bad = tmp_path / "bad.json"
    bad.write_text('{"widgets": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        gamedump.load(bad)


def test_the_labels_of_a_dump_become_a_table_of_what_their_alignment_is_read_from(tmp_path):
    label = {
        "id": 4, "parent": 0, "owner": 0, "name": "Caption", "guid": str(A), "in": str(FILE), "template": None,
        "class": "LabelWidget", "world": _world(1, 1, 0, 0), "layout": {"bbox_pos": [1, 2, 0], "bbox_size": [90, 30]},
        "visible": True, "shown": True, "alpha_product": 1.0,
        "props": {"AlignmentY": 1.0, "TextPlane": [100.0, 40.0], "DisplayedOffset": [0.0, 7.5],
                  "DisplayedSize": [88.0, 20.0], "BaselineY": 22.5, "Text": "Hello, culture", "AlignmentX": None},
    }  # fmt: skip
    image = {**label, "id": 5, "class": "ImageWidget", "name": "Frame"}
    doc = {"format": 1, "probe": "ui_dump", "scene": "s", "widgets": [image, label]}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    dump = gamedump.load(path)
    (row,) = gamedump.label_table(dump)  # only the label
    assert row["name"] == "Caption" and row["AlignmentY"] == "1" and row["DisplayedOffset"] == "0 7.5"
    assert row["bbox"] == "1 2 90 30" and row["BaselineY"] == "22.5" and row["AlignmentX"] == ""
    lines = gamedump.label_csv(dump).decode("utf-8").splitlines()
    assert lines[0].split(",")[:3] == ["name", "in_file", "AlignmentY"] and lines[1].startswith("Caption,")


def test_a_name_the_file_gives_is_kept_even_when_it_reads_like_the_unnamed_text(setup, monkeypatch):
    import dataclasses

    from annotoolbox.formats import phoenixedit

    dump, top, frame, (_a, b, _c) = setup
    space = gamedump.anchor(dump, top.guid).world
    game = dataclasses.replace(dump.widgets[2], name="the game's name")
    assert not b.named and gamedump._row(b, game, frame, space, 2.0).name == "the game's name"
    monkeypatch.setattr(phoenixedit, "object_name", lambda node: "(unnamed)")
    assert b.named and gamedump._row(b, game, frame, space, 2.0).name == "(unnamed)"


def test_the_games_label_texts_replace_the_samples_in_the_preview(setup):
    dump, top, _frame, _els = setup
    label = {"guid": A.bytes_le, "schema_type": "41a404e5", "fields": []}
    assert gamedump.live_text(dump, top.guid) == {}  # no widget of the dump is a label with a text
    for w in dump.widgets:
        if w.guid == str(A):
            w.cls, w.raw["props"] = "LabelWidget", {"Text": "1,234 t"}
    assert gamedump.live_text(dump, top.guid) == {str(A): "1,234 t"}
    assert gamedump.live_text(dump, str(_g("other"))) == {}  # a file the dump does not place
    ctx = layout.Context(files=None)
    ctx.live_text = gamedump.live_text(dump, top.guid)
    assert labels.label_text(ctx, label, {}) == "1,234 t"
    ctx.live_text = {}
    assert labels.label_text(ctx, label, {(labels.TEXT,): "sample"}) == "sample"
