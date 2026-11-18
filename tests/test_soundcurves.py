"""The sound editor's curve edits: attenuations, layer containers and the RTPC entries of a node base, in the three bank
layouts (112, 135, 172); the layouts are written out byte by byte here so a change of them shows."""

from __future__ import annotations

import math
import struct

import pytest
from tests.test_bnkgraph import container, sound
from tests.test_soundedit import sound_window  # noqa: F401

from annotoolbox.formats import bnk, bnkcurves, bnkgraph, bnkmusic
from annotoolbox.soundedit import curveedits
from annotoolbox.soundedit.bankdoc import BankDoc
from annotoolbox.soundedit.registry import EditError

VERSIONS = (112, 135, 172)
OLD, MID, NEW = VERSIONS
CURVE_A = [(0.0, 0.0, 9), (10.0, -1.0, 4)]
CURVE_B = [(0.0, 100.0, 4), (5.0, 50.0, 2), (20.0, 0.0, 4)]


def raw_points(points) -> bytes:
    return b"".join(struct.pack("<ffI", *p) for p in points)


def raw_attenuation(version: int, cone: bool = False) -> bytes:
    """Cone flag (and cone), the curve-to-use bytes (7; 24 and a height byte first in 172), two curves, no RTPC."""
    slots = 24 if version == NEW else 7
    use = bytes([0, 0, 0, 1] + [255] * (slots - 4))
    out = (bytes([1]) if version == NEW else b"") + (
        bytes([1]) + struct.pack("<5f", 100, 180, -18, 27, 29) if cone else b"\0"
    )
    out += use + bytes([2])
    out += (
        struct.pack("<BH", 2, len(CURVE_A))
        + raw_points(CURVE_A)
        + struct.pack("<BH", 0, len(CURVE_B))
        + raw_points(CURVE_B)
    )
    return out + struct.pack("<H", 0)


def raw_rtpc(parameter: int, driven: int, points, scaling: int = 0, curve_id: int = 5) -> bytes:
    return struct.pack("<IBBBIBH", parameter, 0, 2, driven, curve_id, scaling, len(points)) + raw_points(points)


def raw_layers(version: int, children: list[int]) -> bytes:
    """Two layers: layer 1 (one RTPC entry, parameter 700) with a curve for every child, layer 2 with none."""
    out = struct.pack("<I", 2) + struct.pack("<I", 1) + struct.pack("<H", 1) + raw_rtpc(61, 0, CURVE_A, 2)
    out += struct.pack("<IBI", 700, 0, len(children))
    for child in children:
        out += struct.pack("<II", child, len(CURVE_B)) + raw_points(CURVE_B)
    out += struct.pack("<I", 2) + struct.pack("<H", 0) + struct.pack("<IBI", 0, 0, 0)
    return out + (b"" if version == 112 else b"\0")


def layer_container(version: int, oid: int, children: list[int]) -> bnk.HircObject:
    base = container(version, bnkgraph.LAYER, oid, 0, children)
    return bnk.HircObject(base.type, oid, base.payload + raw_layers(version, children))


def with_rtpc(obj: bnk.HircObject, entries: bytes, count: int) -> bnk.HircObject:
    """An object whose node base ends in an RTPC list of ``count`` entries instead of an empty one."""
    return bnk.HircObject(obj.type, obj.id, obj.payload[:-2] + struct.pack("<H", count) + entries)


def make(version: int) -> BankDoc:
    """Sound 11 and 12 under layer container 20; attenuation 30; sound 11 drives its volume by an RTPC."""
    objs = [
        sound(version, 11, 1100, 20), sound(version, 12, 1200, 20),
        layer_container(version, 20, [11, 12]),
        bnk.HircObject(14, 30, raw_attenuation(version)),
    ]  # fmt: skip
    objs[0] = with_rtpc(objs[0], raw_rtpc(41, 0, CURVE_A, 2), 1)
    head = bnk.Chunk(b"BKHD", struct.pack("<III", version, 9, 0))
    return BankDoc(bnk.write(bnk.SoundBank([head, bnk.Chunk(b"HIRC", b"")], objs, [])))


def layers_of(doc: BankDoc):
    node = doc.graph[20]
    at = bnkgraph.child_block(doc.obj(20), doc.family, set(node.children))
    return bnkcurves.read_layers(doc.obj(20).payload, at + 4 + 4 * len(node.children))


def test_the_attenuation_layouts_read_as_pinned_and_render_back():
    for version in VERSIONS:
        for cone in (False, True):
            obj = bnk.HircObject(14, 1, raw_attenuation(version, cone))
            att = bnkcurves.read_attenuation(obj, version)
            assert (att.height, len(att.use), [len(c.points) for c in att.curves]) == (
                1 if version == NEW else None, 24 if version == NEW else 7, [2, 3],
            ), version  # fmt: skip
            assert (att.use[:5], att.curves[0].scaling, att.cone[0]) == ([0, 0, 0, 1, 255], 2, int(cone))
            assert bnkcurves.render_attenuation(att, version) == obj.payload
    with pytest.raises(ValueError):
        bnkcurves.read_attenuation(bnk.HircObject(14, 1, raw_attenuation(135) + b"x"), 135)


@pytest.mark.parametrize("version", VERSIONS)
def test_an_attenuations_curves_are_set_added_removed_and_assigned(version):
    doc = make(version)
    before = bnk.write(doc.bank)
    doc.do("set_attenuation_curve", attenuation=30, curve=1, points=[(0, 80), (30, 0, 7)], scaling=2)
    att = bnkcurves.read_attenuation(doc.obj(30), version)
    assert [(p.x, p.y, p.interp) for p in att.curves[1].points] == [(0.0, 80.0, 4), (30.0, 0.0, 7)]
    assert att.curves[1].scaling == 2
    doc.do("add_attenuation_curve", attenuation=30, points=CURVE_A, slot=4)
    att = bnkcurves.read_attenuation(doc.obj(30), version)
    assert len(att.curves) == 3 and att.use[4] == 2
    doc.do("set_attenuation_slot", attenuation=30, slot=3, curve=None)
    assert bnkcurves.read_attenuation(doc.obj(30), version).use[3] == 255
    doc.do("remove_attenuation_curve", attenuation=30, curve=0)  # slots 0 to 2 lose their curve, the later ones move up
    att = bnkcurves.read_attenuation(doc.obj(30), version)
    assert len(att.curves) == 2 and att.use[:5] == [255, 255, 255, 255, 1]
    while doc.undo():
        pass
    assert bnk.write(doc.bank) == before and not doc.changed


def test_curves_that_the_engine_would_not_take_are_refused_and_leave_the_bank():
    doc = make(135)
    before = bnk.write(doc.bank)
    for points in (
        [(0, 0)],
        [],
        [(5, 0), (1, 1)],
        [(0, math.nan), (1, 1)],
        [(0, 0, 10), (1, 1)],
        [(0, 0, -1), (1, 1)],
        "x",
        [(0,), (1,)],
    ):
        with pytest.raises(EditError):
            doc.do("set_attenuation_curve", attenuation=30, curve=0, points=points)
    for name, args in (
        ("set_attenuation_curve", {"attenuation": 30, "curve": 5, "points": CURVE_A}),
        ("set_attenuation_curve", {"attenuation": 30, "curve": 0, "points": CURVE_A, "scaling": 300}),
        ("set_attenuation_slot", {"attenuation": 30, "slot": 9, "curve": 0}),
        ("set_attenuation_slot", {"attenuation": 30, "slot": 0, "curve": 7}),
        ("add_attenuation_curve", {"attenuation": 30, "points": CURVE_A, "slot": 99}),
        ("remove_attenuation_curve", {"attenuation": 30, "curve": 2}),
        ("set_attenuation_rtpc", {"attenuation": 30, "index": 0}),
        ("set_attenuation_curve", {"attenuation": 20, "curve": 0, "points": CURVE_A}),
    ):
        with pytest.raises(EditError):
            doc.do(name, **args)
    assert bnk.write(doc.bank) == before


@pytest.mark.parametrize("version", VERSIONS)
def test_a_layer_containers_layers_are_read_and_their_curves_edited(version):
    doc = make(version)
    before = bnk.write(doc.bank)
    layers, rest = layers_of(doc)
    assert [(x.id, x.parameter, len(x.rtpcs), [c for c, _p in x.curves]) for x in layers] == [
        (1, 700, 1, [11, 12]), (2, 0, 0, []),
    ]  # fmt: skip
    assert rest == (b"" if version == 112 else b"\0")
    doc.do("set_layer", container=20, layer=2, parameter=555)
    doc.do("set_layer_curve", container=20, layer=1, child=12, points=[(0, 1), (9, 0)])
    doc.do("set_layer_curve", container=20, layer=2, child=11, points=CURVE_A)  # a layer gets a child's curve
    doc.do("set_layer_rtpc", container=20, layer=1, index=0, points=CURVE_B, scaling=0, parameter=99, driven=1)
    layers, _ = layers_of(doc)
    assert layers[1].parameter == 555 and [c for c, _p in layers[1].curves] == [11]
    assert [(p.x, p.y) for _c, pts in layers[0].curves for p in pts][-2:] == [(0.0, 1.0), (9.0, 0.0)]
    assert (layers[0].rtpcs[0].parameter, layers[0].rtpcs[0].driven, len(layers[0].rtpcs[0].points)) == (99, 1, 3)
    doc.do("remove_layer_curve", container=20, layer=1, child=11)
    assert [c for c, _p in layers_of(doc)[0][0].curves] == [12]
    assert doc.graph[20].children == [11, 12]  # the children themselves are untouched
    while doc.undo():
        pass
    assert bnk.write(doc.bank) == before


def test_layer_edits_name_what_is_missing():
    doc = make(172)
    for name, args in (
        ("set_layer", {"container": 20, "layer": 9, "parameter": 1}),
        ("set_layer_curve", {"container": 20, "layer": 1, "child": 99, "points": CURVE_A}),
        ("remove_layer_curve", {"container": 20, "layer": 2, "child": 11}),
        ("set_layer_rtpc", {"container": 20, "layer": 2, "index": 0}),
        ("set_layer", {"container": 11, "layer": 1, "parameter": 1}),
        ("set_layer", {"container": 20, "layer": 1, "parameter": -1}),
    ):
        with pytest.raises(EditError):
            doc.do(name, **args)
    assert not doc.changed


@pytest.mark.parametrize("version", VERSIONS)
def test_a_node_bases_rtpc_entries_are_edited_in_every_banks_layout(version):
    doc = make(version)
    doc.do("set_rtpc", id=11, index=0, points=CURVE_B, scaling=0, driven=1)
    doc.do("add_rtpc", id=11, parameter=77, driven=2, points=CURVE_A, scaling=2)
    start, end = bnkcurves.rtpc_span(doc.obj(11), version)
    entries = bnkcurves.read_rtpcs(bnkmusic.Reader(doc.obj(11).payload[start:end]))
    assert [(e.parameter, e.driven, e.scaling, len(e.points)) for e in entries] == [(41, 1, 0, 3), (77, 2, 2, 2)]
    assert entries[1].curve_id not in (0, entries[0].curve_id) and end == len(doc.obj(11).payload)
    assert bnkgraph.parent_of(doc.obj(11), version) == 20  # the rest of the node base is as it was
    doc.do("add_rtpc", id=20, parameter=5, driven=0, points=CURVE_A)  # a container's list is followed by its child list
    assert doc.graph[20].children == [11, 12] and layers_of(doc)[1] == (b"" if version == OLD else b"\0")
    doc.do("remove_rtpc", id=11, index=0)
    start, end = bnkcurves.rtpc_span(doc.obj(11), version)
    assert [e.parameter for e in bnkcurves.read_rtpcs(bnkmusic.Reader(doc.obj(11).payload[start:end]))] == [77]
    with pytest.raises(EditError):
        doc.do("remove_rtpc", id=11, index=3)


@pytest.mark.parametrize("version", (OLD, MID))
def test_an_older_nodes_end_is_checked_so_a_misread_layout_is_refused(version):
    doc = make(version)
    padded = bnk.HircObject(2, 11, doc.obj(11).payload + b"\0")  # a Sound that goes on after its RTPC list
    with pytest.raises(ValueError, match="does not end where the object goes on"):
        bnkcurves.rtpc_span(padded, version)
    cont = doc.obj(20)
    with pytest.raises(ValueError, match="does not end"):
        bnkcurves.rtpc_span(cont, version, [11, 99])  # the child list that follows is another
    assert bnkcurves.rtpc_span(cont, version, [11, 12])[1] > 0


def test_the_window_lists_the_curves_of_an_object():
    doc = make(NEW)
    refs = curveedits.curve_refs(doc, 30)
    assert [r.edit for r in refs] == ["set_attenuation_curve"] * 2
    assert "volume (dry)" in refs[0].label and refs[1].label.endswith("low-pass filter")
    layer_refs = curveedits.curve_refs(doc, 20)
    assert [r.edit for r in layer_refs] == ["set_layer_curve", "set_layer_curve", "set_layer_rtpc"]
    assert curveedits.curve_refs(doc, 11)[0].args == {"id": 11, "index": 0}
    doc.do(refs[0].edit, **refs[0].args, points=[(0, 5), (1, 6)])  # a ref's arguments are the edit's
    assert curveedits.curve_refs(doc, 30)[0].points[0].y == 5.0


def test_the_panel_parses_points_and_the_window_edits_a_curve_through_it(sound_window):  # noqa: F811
    from annotoolbox.soundedit import curvepanel

    assert curvepanel.parse_points("0:1, 2:3:9") == [(0.0, 1.0, 4), (2.0, 3.0, 9)]
    assert curvepanel.points_text([bnkcurves.Point(0.0, 1.5, 4), bnkcurves.Point(2.0, 3.0, 9)]) == "0:1.5:4, 2:3:9"
    for bad in ("1", "1:2:3:4", "a:b"):
        with pytest.raises(ValueError):
            curvepanel.parse_points(bad)
    window = sound_window
    window.doc = make(NEW)
    window._fill_outline()
    window._select(30)
    assert window.curves.which.count() == 2 and "volume (dry)" in window.curves.which.itemText(0)
    assert "curve 0: volume (dry)" in window.facts.toPlainText()
    window.curves.which.setCurrentIndex(1)
    window.curves.points.setText("0:70, 40:0:7")
    window.curves.scaling.setValue(2)
    window.curves._apply()
    assert bnkcurves.read_attenuation(window.doc.obj(30), NEW).curves[1].points[1].interp == 7
    window.curves.points.setText("0:70")  # one point: said, the bank untouched
    before = bnk.write(window.doc.bank)
    window.curves._apply()
    assert "cannot" in window.statusBar().currentMessage() and bnk.write(window.doc.bank) == before
    window._select(11)  # a Sound of 117: its RTPC entry, and a new one
    assert window.curves.which.count() == 1
    window.curves.parameter.setText("123")
    window.curves.points.setText("0:0, 1:1")
    window.curves._add_rtpc()
    assert len(curveedits.curve_refs(window.doc, 11)) == 2
    window.curves.which.setCurrentIndex(1)
    window.curves._remove_rtpc()
    assert len(curveedits.curve_refs(window.doc, 11)) == 1
    window._select(12)  # a Sound with no entry still takes one in 117's banks
    assert window.curves.which.count() == 0


def test_a_curves_points_are_checked_by_one_rule_with_a_minimum():
    from annotoolbox.formats import bnkcurves

    bnkcurves.check_curve([1.0], [0], minimum=1)
    bnkcurves.check_curve([0.0, 0.0, 1.0], [0, 9, 3])
    for xs, interps, minimum in (([1.0], [0], 2), ([], [], 1), ([1.0, 0.0], [0, 0], 2), ([0.0, float("nan")], [0, 0], 2),
                                  ([0.0, 1.0], [0, 10], 2)):  # fmt: skip
        with pytest.raises(ValueError):
            bnkcurves.check_curve(xs, interps, minimum)
    with pytest.raises(ValueError, match="finite"):
        bnkcurves.check_points([bnkcurves.Point(0.0, 0.0, 0), bnkcurves.Point(1.0, float("inf"), 0)])
