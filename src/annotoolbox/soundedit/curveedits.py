"""The edits of curves: the RTPC entries of an object, a layer container's layers and an attenuation's
curves; ``curve_refs`` lists what an object has for the window's curve form.

Each ``@edit`` takes plain arguments (ids, numbers, points as ``(x, y, interpolation)`` triples) and raises
``CEditError`` where the layout does not read or a curve would not be one the engine takes
(``bnkcurves.check_points``). Layouts: docs/sound/bank-objects.md "RTPC curves, layer containers and attenuations"; the
user's side: docs/sound-editor.md "Curves".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..formats import bnkcurves, bnkgraph, bnkmusic
from ..formats.bnk import SHircObject, fnv1_32
from ..formats.bnkcurves import NO_CURVE, SPoint
from ..infra.i18n import N_, tr
from . import containers, nodebase
from .registry import CEditError, check_byte, check_index, check_u32, edit, reading

if TYPE_CHECKING:
    from .bankdoc import CBankDoc

SLOT_NAMES = (
    N_("common.volume_dry"), N_("common.volume_auxiliary_game_defined"), N_("common.volume_auxiliary_user_defined"),
    N_("common.low_pass_filter"), N_("common.high_pass_filter"), N_("common.spread"), N_("common.focus"),
)  # fmt: skip


def points_of(raw) -> list[SPoint]:
    """Points from ``(x, y[, interpolation])`` entries (linear, 4, when it is left out), checked."""
    try:
        points = [SPoint(float(point[0]), float(point[1]), int(point[2]) if len(point) > 2 else 4) for point in raw]
        bnkcurves.check_points(points)
    except (TypeError, ValueError, IndexError) as exc:
        raise CEditError(tr("common.curve_points_x_y_interpolation", exc=exc)) from exc
    return points


def _entry(entry: bnkcurves.SRtpc, points, scaling, parameter, driven) -> None:
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


def _node_rtpcs(doc: CBankDoc, obj_id: int) -> tuple[SHircObject, int, int, list[bnkcurves.SRtpc]]:
    hirc_object = doc.hirc_object(obj_id)
    node = doc.graph.get(obj_id)
    with reading(tr("sound.type_name_id_node_base", type_name=hirc_object.type_name, id=obj_id)):
        start, end = bnkcurves.rtpc_span(
            hirc_object, doc.m_version, node.children if node is not None and node.exact else None
        )
        return hirc_object, start, end, bnkcurves.read_rtpcs(bnkmusic.CReader(hirc_object.payload[start:end]))


def _put_node_rtpcs(
    doc: CBankDoc, hirc_object: SHircObject, span: tuple[int, int], rtpcs: list[bnkcurves.SRtpc]
) -> None:
    doc.splice(hirc_object, span[0], span[1], bnkcurves.render_rtpcs(rtpcs))


@edit(N_("sound.change_rtpc"))
def set_rtpc(
    doc: CBankDoc, id: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of an object's node base: its curve's points and scaling, the game parameter, the property."""
    hirc_object, start, end, rtpcs = _node_rtpcs(doc, id)
    _entry(rtpcs[check_index(rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_node_rtpcs(doc, hirc_object, (start, end), rtpcs)


@edit(N_("sound.add_rtpc"))
def add_rtpc(doc: CBankDoc, id: int, parameter: int, driven: int, points, scaling: int = 0) -> None:
    """A game parameter drives a property (``driven``: ``nodebase.prop_id``) of an object along a new curve."""
    hirc_object, start, end, rtpcs = _node_rtpcs(doc, id)
    entry = bnkcurves.SRtpc(0, 0, 2, 0, fnv1_32(f"{id}.rtpc.{len(rtpcs)}.{parameter}.{driven}"), 0)
    _entry(entry, points, scaling, parameter, driven)
    _put_node_rtpcs(doc, hirc_object, (start, end), [*rtpcs, entry])


@edit(N_("sound.remove_rtpc"))
def remove_rtpc(doc: CBankDoc, id: int, index: int) -> None:
    """An RTPC entry out of an object's node base."""
    hirc_object, start, end, rtpcs = _node_rtpcs(doc, id)
    check_index(rtpcs, index, tr("common.rtpc_entries"))
    _put_node_rtpcs(doc, hirc_object, (start, end), [rtpc for i, rtpc in enumerate(rtpcs) if i != index])


# layer containers


def _layers(doc: CBankDoc, container: int) -> tuple[SHircObject, int, list[int], list[bnkcurves.SLayer], bytes]:
    hirc_object, node = doc.hirc_object(container), doc.graph.get(container)
    if hirc_object.type != bnkgraph.LAYER or node is None:
        raise CEditError(tr("common.not_layer_container", type_name=hirc_object.type_name, container=container))
    try:
        after = containers.child_span(hirc_object, node, doc.m_version)[1]
    except containers.CLayoutError as exc:
        raise CEditError(str(exc)) from exc
    with reading(tr("sound.layer_list_container", container=container)):
        layers, rest = bnkcurves.read_layers(hirc_object.payload, after)
    return hirc_object, after, list(node.children), layers, rest


def _layer(layers: list[bnkcurves.SLayer], layer: int) -> bnkcurves.SLayer:
    found = next((x for x in layers if x.id == layer), None)
    if found is None:
        raise CEditError(tr("common.container_no_layer", layer=layer))
    return found


def _put_layers(
    doc: CBankDoc, hirc_object: SHircObject, after: int, layers: list[bnkcurves.SLayer], rest: bytes
) -> None:
    doc.splice(hirc_object, after, len(hirc_object.payload), bnkcurves.render_layers(layers, rest))


@edit(N_("sound.change_layer"))
def set_layer(doc: CBankDoc, container: int, layer: int, parameter: int) -> None:
    """The game parameter that moves a layer of a Layer container."""
    hirc_object, after, _kids, layers, rest = _layers(doc, container)
    _layer(layers, layer).parameter = check_u32(parameter, tr("sound.game_parameter"))
    _put_layers(doc, hirc_object, after, layers, rest)


@edit(N_("sound.change_layers_rtpc"))
def set_layer_rtpc(
    doc: CBankDoc, container: int, layer: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of a layer (its volume, say): points, scaling, game parameter, property."""
    hirc_object, after, _kids, layers, rest = _layers(doc, container)
    found = _layer(layers, layer)
    _entry(found.rtpcs[check_index(found.rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_layers(doc, hirc_object, after, layers, rest)


@edit(N_("sound.change_layer_curve"))
def set_layer_curve(doc: CBankDoc, container: int, layer: int, child: int, points) -> None:
    """The curve a layer gives a child of the container (made when the child has none yet; a curve the layer already has
    for an id that is not a child is changed too: the shipped layers have some)."""
    hirc_object, after, kids, layers, rest = _layers(doc, container)
    found, new = _layer(layers, layer), points_of(points)
    if child not in kids and child not in [
        child_id for child_id, _p in found.curves
    ]:  # the shipped layers name some ids that are not
        raise CEditError(tr("sound.child_not_container", child=child, container=container))  # children
    found.curves = [(child_id, new if child_id == child else curve_index) for child_id, curve_index in found.curves]
    if child not in [child_id for child_id, _p in found.curves]:
        found.curves.append((child, new))
    _put_layers(doc, hirc_object, after, layers, rest)


@edit(N_("sound.remove_layer_curve"))
def remove_layer_curve(doc: CBankDoc, container: int, layer: int, child: int) -> None:
    """A child out of a layer (it stays a child of the container; the layer gives it no curve)."""
    hirc_object, after, _kids, layers, rest = _layers(doc, container)
    found = _layer(layers, layer)
    if child not in [child_id for child_id, _p in found.curves]:
        raise CEditError(tr("common.layer_no_curve", layer=layer, child=child))
    found.curves = [(child_id, curve_index) for child_id, curve_index in found.curves if child_id != child]
    _put_layers(doc, hirc_object, after, layers, rest)


# attenuations


def _attenuation(doc: CBankDoc, obj_id: int) -> tuple[SHircObject, bnkcurves.SAttenuation]:
    hirc_object = doc.hirc_object(obj_id)
    if hirc_object.type != bnkgraph.ATTENUATION:
        raise CEditError(tr("common.type_name_id_not_attenuation", type_name=hirc_object.type_name, id=obj_id))
    with reading(tr("sound.the_attenuation_id", id=obj_id)):
        return hirc_object, bnkcurves.read_attenuation(hirc_object, doc.m_version)


def _put_attenuation(doc: CBankDoc, hirc_object: SHircObject, att: bnkcurves.SAttenuation) -> None:
    doc.put(SHircObject(hirc_object.type, hirc_object.id, bnkcurves.render_attenuation(att, doc.m_version)))


@edit(N_("sound.change_attenuation_curve"))
def set_attenuation_curve(doc: CBankDoc, attenuation: int, curve: int, points, scaling: int | None = None) -> None:
    """The points (and the scaling) of one curve of an Attenuation."""
    hirc_object, att = _attenuation(doc, attenuation)
    target = att.curves[check_index(att.curves, curve, tr("common.curves"))]
    target.points = points_of(points)
    if scaling is not None:
        target.scaling = check_byte(scaling, tr("common.the_scaling"))
    _put_attenuation(doc, hirc_object, att)


@edit(N_("sound.add_attenuation_curve"))
def add_attenuation_curve(
    doc: CBankDoc, attenuation: int, points, scaling: int = 0, slot: int | None = None
) -> None:  # fmt: skip
    """A new curve at the end of an Attenuation's list; ``slot``: the use (0 volume dry, 3 low-pass ...) that takes it."""
    hirc_object, att = _attenuation(doc, attenuation)
    if len(att.curves) >= NO_CURVE - 1:
        raise CEditError(tr("common.attenuation_holds_fewer_254_curves"))
    att.curves.append(bnkcurves.SCurve(check_byte(scaling, tr("common.the_scaling")), points_of(points)))
    if slot is not None:
        att.use[check_index(att.use, slot, tr("islands.slots"))] = len(att.curves) - 1
    _put_attenuation(doc, hirc_object, att)


@edit(N_("sound.remove_attenuation_curve"))
def remove_attenuation_curve(doc: CBankDoc, attenuation: int, curve: int) -> None:
    """A curve out of an Attenuation: the slots that used it use none, the later curves' slots move up."""
    hirc_object, att = _attenuation(doc, attenuation)
    check_index(att.curves, curve, tr("common.curves"))
    del att.curves[curve]
    att.use = [NO_CURVE if use == curve else use - 1 if use < NO_CURVE - 1 and use > curve else use for use in att.use]
    _put_attenuation(doc, hirc_object, att)


@edit(N_("sound.change_attenuation_slot"))
def set_attenuation_slot(doc: CBankDoc, attenuation: int, slot: int, curve: int | None) -> None:
    """The curve a use of an Attenuation takes (None: none)."""
    hirc_object, att = _attenuation(doc, attenuation)
    check_index(att.use, slot, tr("islands.slots"))
    if curve is not None:
        check_index(att.curves, curve, tr("common.curves"))
    att.use[slot] = NO_CURVE if curve is None else curve
    _put_attenuation(doc, hirc_object, att)


@edit(N_("sound.change_attenuations_rtpc"))
def set_attenuation_rtpc(
    doc: CBankDoc, attenuation: int, index: int, points=None, scaling=None, parameter=None, driven=None
) -> None:  # fmt: skip
    """One RTPC entry of an Attenuation."""
    hirc_object, att = _attenuation(doc, attenuation)
    _entry(att.rtpcs[check_index(att.rtpcs, index, tr("common.rtpc_entries"))], points, scaling, parameter, driven)
    _put_attenuation(doc, hirc_object, att)


# what the window offers


@dataclass
class SCurveRef:
    """One curve of an object the window can edit: its label, the edit that sets it, that edit's fixed arguments and
    the curve now."""

    label: str
    edit: str
    args: dict
    points: list[SPoint]
    scaling: int | None = None
    note: list[str] = field(default_factory=list)


def _slot_label(att: bnkcurves.SAttenuation, curve: int) -> str:
    slots = [tr(SLOT_NAMES[i]) if i < len(SLOT_NAMES) else str(i) for i, use in enumerate(att.use) if use == curve]
    return ", ".join(slots)


def curve_refs(doc: CBankDoc, obj_id: int) -> list[SCurveRef]:
    """The curves the picked object has that an edit here changes: an Attenuation's, a Layer container's layer curves
    and RTPC entries, and a node base's RTPC entries."""
    if not doc.kind_of(obj_id):
        return []
    hirc_object = doc.hirc_object(obj_id)
    out: list[SCurveRef] = []
    if hirc_object.type == bnkgraph.ATTENUATION:
        try:
            att = _attenuation(doc, obj_id)[1]
        except CEditError:
            return out
        for i, curve in enumerate(att.curves):
            label = tr("common.curve_index_slots", index=i, slots=_slot_label(att, i) or tr("common.not_used"))
            out.append(
                SCurveRef(
                    label, "set_attenuation_curve", {"attenuation": obj_id, "curve": i}, curve.points, curve.scaling
                )
            )
        out += _rtpc_refs(att.rtpcs, "set_attenuation_rtpc", {"attenuation": obj_id}, doc.m_version)
        return out
    if hirc_object.type == bnkgraph.LAYER:
        try:
            _o, _after, _kids, layers, _rest = _layers(doc, obj_id)
        except CEditError:
            return out
        for layer in layers:
            for child, points in layer.curves:
                label = tr("common.layer_curve_child", layer=layer.id, child=child)
                out.append(
                    SCurveRef(
                        label, "set_layer_curve", {"container": obj_id, "layer": layer.id, "child": child}, points
                    )
                )
            out += _rtpc_refs(layer.rtpcs, "set_layer_rtpc", {"container": obj_id, "layer": layer.id}, doc.m_version)
        return out
    try:
        rtpcs = _node_rtpcs(doc, obj_id)[3]
    except CEditError:
        return out
    return _rtpc_refs(rtpcs, "set_rtpc", {"id": obj_id}, doc.m_version)


def _rtpc_refs(rtpcs: list[bnkcurves.SRtpc], name: str, base: dict, version: int) -> list[SCurveRef]:
    return [
        SCurveRef(
            tr(
                "common.rtpc_parameter_drives",
                index=i,
                parameter=rtpc.parameter,
                driven=nodebase.name_of(version, rtpc.driven),
            ),
            name,
            {**base, "index": i},
            rtpc.points,
            rtpc.scaling,
        )
        for i, rtpc in enumerate(rtpcs)
    ]
