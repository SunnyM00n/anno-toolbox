"""``uiedit/labelfit``: a dump's labels counted against the readings of a vertical alignment and of the sizing modes,
over synthetic dumps built to follow one reading each (what the game does is not known: that is what a dump is for)."""

from __future__ import annotations

import json

import pytest

from annotoolbox.uiedit import gamedump, labelfit


def _label(name: str, box: tuple[float, float], props: dict) -> dict:
    return {
        "id": 1, "parent": 0, "owner": 0, "name": name, "guid": None, "in": "f", "template": None,
        "class": "LabelWidget", "world": None, "visible": True, "shown": True, "alpha_product": 1.0,
        "layout": {"bbox_pos": [0, 0, 0], "bbox_size": list(box)}, "props": props,
    }  # fmt: skip


def _dump(tmp_path, labels: list[dict]):
    path = tmp_path / "dump.json"
    path.write_text(json.dumps({"format": 1, "probe": "ui_dump", "scene": "s", "widgets": labels}), encoding="utf-8")
    return gamedump.load(path)


def test_each_stored_alignment_is_counted_against_the_reading_its_labels_follow(tmp_path):
    def row(name, align, offset):
        props = {"AlignmentY": align, "TextPlane": [100, 1], "DisplayedSize": [50, 20], "DisplayedOffset": [0, offset],
                 "BaselineY": 15}  # fmt: skip
        return _label(name, (100, 60), props)

    dump = _dump(
        tmp_path, [row("a", 1.0, 40), row("b", 1.0, 40), row("c", 1.4, 0), row("d", 1.5, 7), row("e", 1.5, 20)]
    )
    result = labelfit.fit(dump)
    assert result.labels == 5
    assert result.vertical[1.0].agree["bottom"] == 2 and result.vertical[1.0].agree["top"] == 0
    assert result.vertical[1.4].agree["top"] == 1
    assert result.vertical[1.5].none == ["d"] and result.vertical[1.5].agree["centre"] == 1  # one agrees, one none
    assert labelfit.vertical_readings(60, 20, 45, 15)["baseline"] is True


def test_a_box_one_line_high_tells_nothing_about_the_alignment(tmp_path):
    props = {"AlignmentY": 1.0, "TextPlane": [100, 1], "DisplayedSize": [50, 20], "DisplayedOffset": [0, 0]}
    result = labelfit.fit(_dump(tmp_path, [_label("one line", (100, 20), props)]))
    assert result.vertical[1.0].labels == 1 and result.vertical[1.0].informative == 0


@pytest.mark.parametrize("mode, box, readings", [
    (0, 200, {"plane", "larger"}),  # the plane is 100 times a scale of 2: larger than the text
    (1, 80, {"shown text", "smaller"}),
    (2, 200, {"plane", "larger"}),
    (3, 80, {"shown text", "smaller"}),
    (4, 123, set()),  # a mode whose box is none of the four
])  # fmt: skip
def test_a_sizing_modes_box_is_counted_against_the_four_readings(tmp_path, mode, box, readings):
    props = {"HorizontalSizing": mode, "TextPlane": [100, 1], "Scale": [2.0, 2.0, 1.0], "DisplayedSize": [80, 20]}
    group = labelfit.fit(_dump(tmp_path, [_label("t", (box, 20), props)])).horizontal[mode]
    assert {r for r in labelfit.SIZING if group.agree[r]} == readings
    assert group.none == (["t"] if not readings else [])


def test_the_report_names_the_readings_and_the_labels_none_of_them_fits(tmp_path):
    props = {"HorizontalSizing": 4, "TextPlane": [100, 1], "DisplayedSize": [80, 20], "AlignmentY": 1.0,
             "DisplayedOffset": [0, 3]}  # fmt: skip
    text = labelfit.report(labelfit.fit(_dump(tmp_path, [_label("odd", (150, 60), props)])))
    assert "HorizontalSizing as stored" in text and "4: 1 labels" in text and "no reading fits: odd" in text


def test_the_label_table_carries_the_columns_the_fit_reads():
    for column in ("AlignmentY", "TextPlane", "DisplayedOffset", "DisplayedSize", "BaselineY", "HorizontalSizing",
                   "VerticalSizing", "Scale"):  # fmt: skip
        assert column in gamedump.LABEL_COLUMNS
