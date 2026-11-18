"""The edits of curves: the RTPC entries of an object, a layer container's layers and an attenuation's
curves; ``curve_refs`` lists what an object has for the window's curve form.

Each ``@edit`` takes plain arguments (ids, numbers, points as ``(x, y, interpolation)`` triples) and raises
``EditError`` where the layout does not read or a curve would not be one the engine takes
(``bnkcurves.check_points``). Layouts: docs/sound/bank-objects.md "RTPC curves, layer containers and attenuations"; the
user's side: docs/sound-editor.md "Curves".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..formats import bnkcurves, bnkgraph, bnkmusic
from ..formats.bnk import HircObject, fnv1_32
from ..formats.bnkcurves import NO_CURVE, Point
from ..infra.i18n import N_, tr
from . import containers, nodebase
from .registry import EditError, check_byte, check_index, check_u32, edit, reading

if TYPE_CHECKING:
    from .bankdoc import BankDoc

SLOT_NAMES = (
    N_("common.volume_dry"), N_("common.volume_auxiliary_game_defined"), N_("common.volume_auxiliary_user_defined"),
    N_("common.low_pass_filter"), N_("common.high_pass_filter"), N_("common.spread"), N_("common.focus"),
)  # fmt: skip


def points_of(raw) -> list[Point]:
    """Points from ``(x, y[, interpolation])`` entries (linear, 4, when it is left out), checked."""
    try:
        points = [Point(float(p[0]), float(p[1]), int(p[2]) if len(p) > 2 else 4) for p in raw]
        bnkcurves.check_points(points)
    except (TypeError, ValueError, IndexError) as exc:
        raise EditError(tr("common.curve_points_x_y_interpolation", exc=exc)) from exc
    return points


def _entry(entry: bnkcurves.Rtpc, points, scaling, parameter, driven) -> None:
    """Change what is given of an RTPC entry."""
    if points is not None:
        entry.points = points_of(points)
    if scaling is not None:
        entry.scaling = check_byte(scaling, tr("common.the_scaling"))
    if parameter is not None:
        entry.parameter = check_u32(parameter, tr("sound.game_parameter"))
    if driven is not None:
        entry.driven = check_byte(driven, tr("common.the_property"))


# the RTPC entries of a node base


def _node_rtpcs(doc: BankDoc, obj_id: int) -> tuple[HircObject, int, int, list[bnkcurves.Rtpc]]:
    obj = doc.obj(obj_id)
    node = doc.graph.get(obj_id)
    with reading(tr("sound.type_name_id_node_base", type_name=obj.type_name, id=obj_id)):
        start, end = bnkcurves.rtpc_span(obj, doc.version, node.children if node is not None and node.exact else None)
        return obj, start, end, bnkcurves.read_rtpcs(bnkmusic.Reader(obj.payload[start:end]))


def _put_node_rtpcs(doc: BankDoc, obj: HircObject, span: tuple[int, int], rtpcs: list[bnkcurves.Rtpc]) -> None:
    doc.splice(obj, span[0], span[1], bnkcurves.render_rtpcs(rtpcs))


@edit(N_("sound.change_rtpc"))
def set_rtpc(
    doc: BankDoc, id: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of an object's node base: its curve's points and scaling, the game parameter, the property."""
    obj, start, end, rtpcs = _node_rtpcs(doc, id)
    _entry(rtpcs[check_index(rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_node_rtpcs(doc, obj, (start, end), rtpcs)


@edit(N_("sound.add_rtpc"))
def add_rtpc(doc: BankDoc, id: int, parameter: int, driven: int, points, scaling: int = 0) -> None:
    """A game parameter drives a property (``driven``: ``nodebase.prop_id``) of an object along a new curve."""
    obj, start, end, rtpcs = _node_rtpcs(doc, id)
    entry = bnkcurves.Rtpc(0, 0, 2, 0, fnv1_32(f"{id}.rtpc.{len(rtpcs)}.{parameter}.{driven}"), 0)
    _entry(entry, points, scaling, parameter, driven)
    _put_node_rtpcs(doc, obj, (start, end), [*rtpcs, entry])


@edit(N_("sound.remove_rtpc"))
def remove_rtpc(doc: BankDoc, id: int, index: int) -> None:
    """An RTPC entry out of an object's node base."""
    obj, start, end, rtpcs = _node_rtpcs(doc, id)
    check_index(rtpcs, index, tr("common.rtpc_entries"))
    _put_node_rtpcs(doc, obj, (start, end), [e for i, e in enumerate(rtpcs) if i != index])


# layer containers


def _layers(doc: BankDoc, container: int) -> tuple[HircObject, int, list[int], list[bnkcurves.Layer], bytes]:
    obj, node = doc.obj(container), doc.graph.get(container)
    if obj.type != bnkgraph.LAYER or node is None:
        raise EditError(tr("common.not_layer_container", type_name=obj.type_name, container=container))
    try:
        after = containers.child_span(obj, node, doc.version)[1]
    except containers.LayoutError as exc:
        raise EditError(str(exc)) from exc
    with reading(tr("sound.layer_list_container", container=container)):
        layers, rest = bnkcurves.read_layers(obj.payload, after)
    return obj, after, list(node.children), layers, rest


def _layer(layers: list[bnkcurves.Layer], layer: int) -> bnkcurves.Layer:
    found = next((x for x in layers if x.id == layer), None)
    if found is None:
        raise EditError(tr("common.container_no_layer", layer=layer))
    return found


def _put_layers(doc: BankDoc, obj: HircObject, after: int, layers: list[bnkcurves.Layer], rest: bytes) -> None:
    doc.splice(obj, after, len(obj.payload), bnkcurves.render_layers(layers, rest))


@edit(N_("sound.change_layer"))
def set_layer(doc: BankDoc, container: int, layer: int, parameter: int) -> None:
    """The game parameter that moves a layer of a Layer container."""
    obj, after, _kids, layers, rest = _layers(doc, container)
    _layer(layers, layer).parameter = check_u32(parameter, tr("sound.game_parameter"))
    _put_layers(doc, obj, after, layers, rest)


@edit(N_("sound.change_layers_rtpc"))
def set_layer_rtpc(
    doc: BankDoc, container: int, layer: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of a layer (its volume, say): points, scaling, game parameter, property."""
    obj, after, _kids, layers, rest = _layers(doc, container)
    found = _layer(layers, layer)
    _entry(found.rtpcs[check_index(found.rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_layers(doc, obj, after, layers, rest)


@edit(N_("sound.change_layer_curve"))
def set_layer_curve(doc: BankDoc, container: int, layer: int, child: int, points) -> None:
    """The curve a layer gives a child of the container (made when the child has none yet; a curve the layer already has
    for an id that is not a child is changed too: the shipped layers have some)."""
    obj, after, kids, layers, rest = _layers(doc, container)
    found, new = _layer(layers, layer), points_of(points)
    if child not in kids and child not in [
        c for c, _p in found.curves
    ]:  # the shipped layers name some ids that are not
        raise EditError(tr("sound.child_not_container", child=child, container=container))  # children
    found.curves = [(c, new if c == child else p) for c, p in found.curves]
    if child not in [c for c, _p in found.curves]:
        found.curves.append((child, new))
    _put_layers(doc, obj, after, layers, rest)


@edit(N_("sound.remove_layer_curve"))
def remove_layer_curve(doc: BankDoc, container: int, layer: int, child: int) -> None:
    """A child out of a layer (it stays a child of the container; the layer gives it no curve)."""
    obj, after, _kids, layers, rest = _layers(doc, container)
    found = _layer(layers, layer)
    if child not in [c for c, _p in found.curves]:
        raise EditError(tr("common.layer_no_curve", layer=layer, child=child))
    found.curves = [(c, p) for c, p in found.curves if c != child]
    _put_layers(doc, obj, after, layers, rest)


# attenuations


def _attenuation(doc: BankDoc, obj_id: int) -> tuple[HircObject, bnkcurves.Attenuation]:
    obj = doc.obj(obj_id)
    if obj.type != bnkgraph.ATTENUATION:
        raise EditError(tr("common.type_name_id_not_attenuation", type_name=obj.type_name, id=obj_id))
    with reading(tr("sound.the_attenuation_id", id=obj_id)):
        return obj, bnkcurves.read_attenuation(obj, doc.version)


def _put_attenuation(doc: BankDoc, obj: HircObject, att: bnkcurves.Attenuation) -> None:
    doc.put(HircObject(obj.type, obj.id, bnkcurves.render_attenuation(att, doc.version)))


@edit(N_("sound.change_attenuation_curve"))
def set_attenuation_curve(doc: BankDoc, attenuation: int, curve: int, points, scaling: int | None = None) -> None:
    """The points (and the scaling) of one curve of an Attenuation."""
    obj, att = _attenuation(doc, attenuation)
    target = att.curves[check_index(att.curves, curve, tr("common.curves"))]
    target.points = points_of(points)
    if scaling is not None:
        target.scaling = check_byte(scaling, tr("common.the_scaling"))
    _put_attenuation(doc, obj, att)


@edit(N_("sound.add_attenuation_curve"))
def add_attenuation_curve(
    doc: BankDoc, attenuation: int, points, scaling: int = 0, slot: int | None = None
) -> None:  # fmt: skip
    """A new curve at the end of an Attenuation's list; ``slot``: the use (0 volume dry, 3 low-pass ...) that takes it."""
    obj, att = _attenuation(doc, attenuation)
    if len(att.curves) >= NO_CURVE - 1:
        raise EditError(tr("common.attenuation_holds_fewer_254_curves"))
    att.curves.append(bnkcurves.Curve(check_byte(scaling, tr("common.the_scaling")), points_of(points)))
    if slot is not None:
        att.use[check_index(att.use, slot, tr("islands.slots"))] = len(att.curves) - 1
    _put_attenuation(doc, obj, att)


@edit(N_("sound.remove_attenuation_curve"))
def remove_attenuation_curve(doc: BankDoc, attenuation: int, curve: int) -> None:
    """A curve out of an Attenuation: the slots that used it use none, the later curves' slots move up."""
    obj, att = _attenuation(doc, attenuation)
    check_index(att.curves, curve, tr("common.curves"))
    del att.curves[curve]
    att.use = [NO_CURVE if u == curve else u - 1 if u < NO_CURVE - 1 and u > curve else u for u in att.use]
    _put_attenuation(doc, obj, att)


@edit(N_("sound.change_attenuation_slot"))
def set_attenuation_slot(doc: BankDoc, attenuation: int, slot: int, curve: int | None) -> None:
    """The curve a use of an Attenuation takes (None: none)."""
    obj, att = _attenuation(doc, attenuation)
    check_index(att.use, slot, tr("islands.slots"))
    if curve is not None:
        check_index(att.curves, curve, tr("common.curves"))
    att.use[slot] = NO_CURVE if curve is None else curve
    _put_attenuation(doc, obj, att)


@edit(N_("sound.change_attenuations_rtpc"))
def set_attenuation_rtpc(
    doc: BankDoc, attenuation: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of an Attenuation."""
    obj, att = _attenuation(doc, attenuation)
    _entry(att.rtpcs[check_index(att.rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_attenuation(doc, obj, att)


# what the window offers


@dataclass
class CurveRef:
    """One curve of an object the window can edit: its label, the edit that sets it, that edit's fixed arguments and
    the curve now."""

    label: str
    edit: str
    args: dict
    points: list[Point]
    scaling: int | None = None
    note: list[str] = field(default_factory=list)


def _slot_label(att: bnkcurves.Attenuation, curve: int) -> str:
    slots = [tr(SLOT_NAMES[i]) if i < len(SLOT_NAMES) else str(i) for i, u in enumerate(att.use) if u == curve]
    return ", ".join(slots)


def curve_refs(doc: BankDoc, obj_id: int) -> list[CurveRef]:
    """The curves the picked object has that an edit here changes: an Attenuation's, a Layer container's layer curves
    and RTPC entries, and a node base's RTPC entries."""
    if not doc.kind_of(obj_id):
        return []
    obj = doc.obj(obj_id)
    out: list[CurveRef] = []
    if obj.type == bnkgraph.ATTENUATION:
        try:
            att = _attenuation(doc, obj_id)[1]
        except EditError:
            return out
        for i, c in enumerate(att.curves):
            label = tr("common.curve_index_slots", index=i, slots=_slot_label(att, i) or tr("common.not_used"))
            out.append(
                CurveRef(label, "set_attenuation_curve", {"attenuation": obj_id, "curve": i}, c.points, c.scaling)
            )
        out += _rtpc_refs(att.rtpcs, "set_attenuation_rtpc", {"attenuation": obj_id}, doc.version)
        return out
    if obj.type == bnkgraph.LAYER:
        try:
            _o, _after, _kids, layers, _rest = _layers(doc, obj_id)
        except EditError:
            return out
        for layer in layers:
            for child, points in layer.curves:
                label = tr("common.layer_curve_child", layer=layer.id, child=child)
                out.append(
                    CurveRef(label, "set_layer_curve", {"container": obj_id, "layer": layer.id, "child": child}, points)
                )
            out += _rtpc_refs(layer.rtpcs, "set_layer_rtpc", {"container": obj_id, "layer": layer.id}, doc.version)
        return out
    try:
        rtpcs = _node_rtpcs(doc, obj_id)[3]
    except EditError:
        return out
    return _rtpc_refs(rtpcs, "set_rtpc", {"id": obj_id}, doc.version)


def _rtpc_refs(rtpcs: list[bnkcurves.Rtpc], name: str, base: dict, version: int) -> list[CurveRef]:
    return [
        CurveRef(
            tr(
                "common.rtpc_parameter_drives",
                index=i,
                parameter=e.parameter,
                driven=nodebase.name_of(version, e.driven),
            ),
            name,
            {**base, "index": i},
            e.points,
            e.scaling,
        )
        for i, e in enumerate(rtpcs)
    ]
