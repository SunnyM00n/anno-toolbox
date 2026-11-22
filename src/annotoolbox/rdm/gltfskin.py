"""The skeletons and animations of a glTF document as RDM's: joints from a skin, keys from the channels.

``skeletons`` turns the skins a file's meshes use into ``SSkin`` records (the RDM skeleton, its joint nodes, the map
from a skin's joint slot to the RDM joint); ``animation_files`` groups the animations into the files they came from.
A joint's inverse bind pose is its RDM record; a key is the joint's local transform (docs/rdm-gltf.md "Skeletons").
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..formats.rdmread import SAnimation, SJoint, SKeyframe, SRdmFile, SSkeleton, STrack
from ..models.meshxform import qconj
from . import rigmath
from .gltfdoc import CGltfImportError, SDoc, tags
from .rdmgltf import inverse_bind

DEFAULT_RATE = 30  # frames per second of an animation that names none and whose keys do not show one
RATES = (30, 15, 25, 60, 24, 50, 10, 5)  # the rates 2070's and 117's animations use, the default first (rdm.md)
EXACT = 1e-4  # how close the stored values must come to the document's to be taken as they are


@dataclass
class SSkin:
    skeleton: SSkeleton
    gltf_skin: int
    nodes: list[int]  # the joint nodes, in RDM joint order
    slot: dict[int, int]  # the skin's joint slot -> the RDM joint index


def skeletons(document: SDoc, file: str, mesh_nodes: list[int]) -> list[SSkin]:
    """The skeletons of a file: the skins its meshes use and those tagged with the file, in the tagged order."""
    used = [document.node(node)["skin"] for node in mesh_nodes if "skin" in document.node(node)]
    tagged = [i for i, gltf_skin in enumerate(document.gltf.items("skins")) if tags(gltf_skin).get("file") == file]
    order = sorted(
        set(used) | set(tagged), key=lambda i: (tags(document.gltf.items("skins")[i]).get("skeleton", 1 << 30), i)
    )
    return [_skin(document, i, mesh_nodes) for i in order]


def _weighted_slots(document: SDoc, skin: int, mesh_nodes: list[int]) -> set[int]:
    """The joint slots of ``skin`` some vertex weighs."""
    out = set()
    for node in mesh_nodes:
        if document.node(node).get("skin") != skin:
            continue
        for prim in document.gltf.items("meshes")[document.node(node)["mesh"]].get("primitives", []):
            attributes = prim.get("attributes", {})
            if "JOINTS_0" not in attributes:
                continue
            weights = document.gltf.values(attributes["WEIGHTS_0"]) if "WEIGHTS_0" in attributes else None
            for vertex, joints in enumerate(document.gltf.values(attributes["JOINTS_0"])):
                out |= {j for k, j in enumerate(joints) if weights is None or weights[vertex][k] > 0}
    return out


def _skin(document: SDoc, index: int, mesh_nodes: list[int]) -> SSkin:
    skin = document.gltf.items("skins")[index]
    slots = list(skin.get("joints", []))
    if not slots:
        raise CGltfImportError(f"skin {index}: no joints")
    joint_tags = [tags(document.node(node)) for node in slots]
    marked = [k for k, x in enumerate(joint_tags) if isinstance(x.get("joint"), int)]
    keep = list(range(len(slots)))
    if marked and len(marked) < len(slots):  # some joints came from the RDM, the others from the modelling tool
        weighted = _weighted_slots(document, index, mesh_nodes)
        added = [k for k in keep if k not in marked]
        if any(k in weighted for k in added):
            names = ", ".join(repr(document.name(slots[k])) for k in added)
            raise CGltfImportError(f"skin {index}: joints {names} are not the source's and carry vertex weights")
        document.notes += [
            f"dropped joint {document.name(slots[k])!r}: added by the modelling tool, weighs nothing" for k in added
        ]
        keep = marked
    if marked and len({joint_tags[k]["joint"] for k in keep}) == len(keep):
        keep.sort(key=lambda k: joint_tags[k]["joint"])
    inverse_bind_matrices = _ibms(document, skin, len(slots))
    joints = [_joint(document, slots[k], inverse_bind_matrices[k], joint_tags[k]) for k in keep]
    order = {slots[k]: rank for rank, k in enumerate(keep)}
    for rank, k in enumerate(keep):
        parent = document.parents.get(slots[k])
        while parent is not None and parent not in order:
            parent = document.parents.get(parent)
        joints[rank].parent = order.get(parent) if parent is not None else None
    joints, keep = _parent_first(document, joints, keep, index)
    _name_joints(document, joints, index)
    word = tags(skin).get("word", 0)
    extra = base64.b64decode(tags(skin)["extra"]) if "extra" in tags(skin) else b""
    if not marked:
        _like_values(document, joints)
    return SSkin(
        SSkeleton(joints, word, extra), index, [slots[k] for k in keep], {k: rank for rank, k in enumerate(keep)}
    )


def _ibms(document: SDoc, skin: dict, count: int) -> list[rigmath.Matrix]:
    if "inverseBindMatrices" not in skin:
        return [rigmath.IDENTITY] * count
    return [rigmath.from_columns(column_major) for column_major in document.gltf.values(skin["inverseBindMatrices"])]


def _joint(document: SDoc, node: int, inverse_bind_matrix: rigmath.Matrix, joint_tags: dict) -> SJoint:
    matrix = rigmath.mirrored(inverse_bind_matrix) if document.mirror else inverse_bind_matrix
    bind_rotation, position, scale = rigmath.decompose(matrix)
    if any(abs(factor - 1) > 1e-3 for factor in scale):
        raise CGltfImportError(
            f"joint {document.name(node)!r}: its bind pose scales {scale}; an RDM joint is turn and move only"
        )
    rotation = qconj(bind_rotation)
    if "position" in joint_tags and "rotation" in joint_tags:  # the stored values, when they still give this bind pose
        stored = inverse_bind(tuple(joint_tags["position"]), tuple(joint_tags["rotation"]))
        if all(abs(stored[i][j] - matrix[i][j]) <= EXACT for i in range(3) for j in range(4)):
            position, rotation = tuple(joint_tags["position"]), tuple(joint_tags["rotation"])
    low, high = tuple(joint_tags.get("box_min", (0.0, 0.0, 0.0))), tuple(joint_tags.get("box_max", (0.0, 0.0, 0.0)))
    return SJoint(document.node(node).get("name", ""), tuple(position), tuple(rotation), None, low, high)


def _parent_first(document: SDoc, joints: list[SJoint], keep: list[int], skin: int) -> tuple[list[SJoint], list[int]]:
    """The joints with every parent before its children (117 drops another order), the source's order where it is."""
    if all(j.parent is None or j.parent < i for i, j in enumerate(joints)):
        return joints, keep
    done: list[int] = []
    while len(done) < len(joints):
        for i, j in enumerate(joints):
            if i not in done and (j.parent is None or j.parent in done):
                done.append(i)
                break
    new = {old: k for k, old in enumerate(done)}
    out = []
    for old in done:
        j = joints[old]
        out.append(
            SJoint(j.name, j.position, j.rotation, None if j.parent is None else new[j.parent], j.box_min, j.box_max)
        )
    document.notes.append(f"skin {skin}: joints reordered parent first (the game drops a skeleton that is not)")
    return out, [keep[old] for old in done]


def _name_joints(document: SDoc, joints: list[SJoint], skin: int) -> None:
    if document.options.joint_names == "index":
        for k, j in enumerate(joints):
            j.name = str(k)
        return
    names = [j.name for j in joints]
    if not all(names) or len(set(names)) != len(names):
        raise CGltfImportError(
            f"skin {skin}: joint names are missing or repeated; animation tracks find joints by name "
            "(rename them, or --joint-names index)"
        )


def _like_values(document: SDoc, joints: list[SJoint]) -> None:
    """Joint boxes from the ``like`` file (by name); without one they stay zero, which 2070 also ships."""
    like = {
        j.name: j
        for skeleton in (document.options.like.skeletons if document.options.like else [])
        for j in skeleton.joints
    }
    missing = 0
    for j in joints:
        if j.name in like:
            j.box_min, j.box_max = like[j.name].box_min, like[j.name].box_max
        else:
            missing += 1
    if missing:
        document.notes.append(f"{missing} joints without a stored box: written as zero (the extent is not in glTF)")


# Animations


def joint_names(document: SDoc) -> dict[int, str]:
    """Every joint node of the document and its RDM joint name."""
    out = {}
    for skin in document.gltf.items("skins"):
        for k, node in enumerate(skin.get("joints", [])):
            out.setdefault(node, str(k) if document.options.joint_names == "index" else document.name(node))
    return out


def animation_files(
    document: SDoc, file_infos: dict[str, dict], default_name: str
) -> list[tuple[str, SRdmFile, set[int]]]:
    """(file name, the file, the joint nodes it moves) per animation file: the tagged files in their entry order;
    an untagged animation becomes a file of its own named after it."""
    names = joint_names(document)
    entries: dict[str, list[tuple[int, SAnimation]]] = {}
    moved: dict[str, set[int]] = {}
    for k, gltf_animation in enumerate(document.gltf.items("animations")):
        animation_tags = tags(gltf_animation)
        file = animation_tags.get("file") or _file_name(default_name, gltf_animation.get("name") or f"animation{k}")
        animation, nodes = _animation(document, gltf_animation, animation_tags, names)
        entries.setdefault(file, []).append((animation_tags.get("entry", len(entries.get(file, []))), animation))
        moved.setdefault(file, set()).update(nodes)
    for name, file_info in file_infos.items():
        for joint in file_info.get("unkeyed", []):
            tracks = [STrack(j, []) for j in joint.get("tracks", [])]
            entries.setdefault(name, []).append(
                (joint.get("entry", 0), SAnimation(joint["name"], joint["duration_ms"], joint["frame_rate"], tracks))
            )
    out = []
    for name, items in entries.items():
        file_info = file_infos.get(name, {})
        rdm = SRdmFile(False, file_info.get("source", ""), file_info.get("exporter", ""))
        rdm.animations = [gltf_animation for _k, gltf_animation in sorted(items, key=lambda x: x[0])]
        if file_info.get("kind") == "animation":
            rdm.skeletons = [skin.skeleton for skin in skeletons(document, name, [])]
        out.append((name, rdm, moved.get(name, set())))
    return out


def _file_name(default_name: str, animation: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", animation).strip("_") or "animation"
    return f"{Path(default_name).stem}_{safe}.rdm"


def _animation(
    document: SDoc, gltf_animation: dict, animation_tags: dict, names: dict[int, str]
) -> tuple[SAnimation, set[int]]:
    who = f"animation {gltf_animation.get('name', '')!r}"
    groups: dict[tuple, dict] = {}  # a track: by its tagged place (two tracks may name one joint), else its joint
    on_node: dict[int | None, tuple] = {}
    nodes = set()
    for channel in gltf_animation.get("channels", []):
        target, sampler = channel.get("target", {}), gltf_animation["samplers"][channel["sampler"]]
        node, path = target.get("node"), target.get("path")
        if path == "weights":
            raise CGltfImportError(f"{who}: morph target weights; an RDM key holds a turn and a move only")
        if node is not None and node not in names:
            document.notes.append(f"{who}: dropped the channel on {document.name(node)!r}, which is not a joint")
            continue
        joint = names[node] if node is not None else tags(channel).get("joint")
        if joint is None:
            document.notes.append(f"{who}: dropped a channel without a target")
            continue
        place = tags(channel).get("track")
        key = ("track", place) if isinstance(place, int) else on_node.get(node, ("joint", joint))
        groups.setdefault(key, {"node": node, "joint": joint})[path] = sampler
        on_node.setdefault(node, key)  # an untagged channel (a tool's own) joins the track already on its joint
        if node is not None:
            nodes.add(node)
    rate = (
        animation_tags.get("frame_rate")
        or document.options.frame_rate
        or _rate(document, gltf_animation)
        or DEFAULT_RATE
    )
    tracks = _ordered(
        {key: _track(document, group["joint"], group, rate, who) for key, group in groups.items()}, animation_tags
    )
    last = max((k.time for track in tracks for k in track.keys), default=0.0)
    duration_ms = (
        animation_tags["duration_ms"]
        if abs(animation_tags.get("duration_ms", -9) - last * 1000) <= 1
        else round(last * 1000)
    )
    return SAnimation(animation_tags.get("name", gltf_animation.get("name", "")), duration_ms, rate, tracks), nodes


def _ordered(tracks: dict[tuple, STrack], animation_tags: dict) -> list[STrack]:
    """The tracks in the source's order (by their tagged places, else by the joint names the extras list), the ones
    without keys back in their places."""
    order, empty = animation_tags.get("tracks") or [], set(animation_tags.get("empty", []))
    if not order:
        return list(tracks.values())
    placed = {key[1]: track for key, track in tracks.items() if key[0] == "track"}
    by_name = {track.joint: track for key, track in tracks.items() if key[0] == "joint"}
    out = []
    for k, name in enumerate(order):
        if k in placed:
            out.append(placed.pop(k))
        elif name in by_name:
            out.append(by_name.pop(name))
        elif k in empty:
            out.append(STrack(name, []))
    return out + list(placed.values()) + list(by_name.values())


def _rate(document: SDoc, gltf_animation: dict) -> int | None:
    """The first of the games' frame rates every key time falls on (None: none fits, or no keys)."""
    times = {time for sampler in gltf_animation.get("samplers", []) for time in document.gltf.values(sampler["input"])}
    if not times:
        return None
    for rate in RATES:
        if all(abs(time * rate - round(time * rate)) < 1e-3 for time in times):
            return rate
    document.notes.append(
        f"animation {gltf_animation.get('name', '')!r}: its keys fall on no usual frame rate; {DEFAULT_RATE} written"
    )
    return None


def _track(document: SDoc, joint: str, group: dict, rate: int, who: str) -> STrack:
    node = group["node"]
    rest = document.node(node) if node is not None else {}
    if "scale" in group:
        _, values = _keys(document, group["scale"])
        if any(abs(component - 1) > 1e-4 for value in values for component in value):
            raise CGltfImportError(f"{who}: joint {joint!r} scales; an RDM key holds a turn and a move only")
    times = sorted(
        {
            x
            for channel_path in ("rotation", "translation")
            if channel_path in group
            for x in _times(document, group[channel_path], rate)
        }
    )
    rotations = _at(document, group.get("rotation"), times, tuple(rest.get("rotation", (0.0, 0.0, 0.0, 1.0))), True)
    translations = _at(
        document, group.get("translation"), times, tuple(rest.get("translation", (0.0, 0.0, 0.0))), False
    )
    above = document.parents.get(node) if node is not None else None
    frame = document.world(above) if above is not None and not _is_joint(document, above) else rigmath.IDENTITY
    keys = []
    for time, rotation, translation in zip(times, rotations, translations, strict=True):
        if not rigmath.is_identity(frame, 0.0):  # a root under an armature object that moves or turns
            rotation, translation, _s = rigmath.decompose(
                rigmath.multiply(frame, rigmath.from_rotation_translation(rotation, translation))
            )
        if document.mirror:
            rotation, translation = rigmath.mirrored_rotation(rotation), rigmath.mirrored_point(translation)
        keys.append(SKeyframe(qconj(rotation), tuple(translation), time))
    return STrack(joint, keys)


def _is_joint(document: SDoc, node: int) -> bool:
    return any(node in skin.get("joints", []) for skin in document.gltf.items("skins"))


def _keys(document: SDoc, sampler: dict) -> tuple[list[float], list[tuple]]:
    return document.gltf.values(sampler["input"]), [tuple(value) for value in document.gltf.values(sampler["output"])]


def _times(document: SDoc, sampler: dict, rate: int) -> list[float]:
    times = document.gltf.values(sampler["input"])
    if sampler.get("interpolation") == "CUBICSPLINE" and times:  # sampled at the frame rate
        steps = int(math.floor((times[-1] - times[0]) * rate + 1e-6))
        return sorted(set(times) | {times[0] + k / rate for k in range(steps + 1)})
    return list(times)


def _at(document: SDoc, sampler: dict | None, times: list[float], rest: tuple, rotation: bool) -> list[tuple]:
    """The sampler's values at ``times`` by its interpolation (``rest`` where the path is not animated)."""
    if sampler is None:
        return [rest] * len(times)
    inputs, out = _keys(document, sampler)
    mode = sampler.get("interpolation", "LINEAR")
    if mode == "CUBICSPLINE":
        return [_cubic(document, sampler, inputs, time, rotation) for time in times]
    exact = dict(zip(inputs, out, strict=True))
    values = []
    for time in times:
        if time in exact:
            values.append(exact[time])
            continue
        k = max((i for i, x in enumerate(inputs) if x <= time), default=0)
        if time < inputs[0] or mode == "STEP" or k + 1 >= len(inputs):  # before the first key the first value holds
            values.append(out[k])
            continue
        fraction = (time - inputs[k]) / (inputs[k + 1] - inputs[k])
        values.append(_lerp(out[k], out[k + 1], fraction, rotation))
    return values


def _lerp(start: tuple, end: tuple, fraction: float, rotation: bool) -> tuple:
    if rotation and sum(x * y for x, y in zip(start, end, strict=True)) < 0:
        end = tuple(-x for x in end)
    blended = tuple(x + (y - x) * fraction for x, y in zip(start, end, strict=True))
    if rotation:
        length = math.sqrt(sum(component * component for component in blended)) or 1.0
        blended = tuple(component / length for component in blended)
    return blended


def _cubic(document: SDoc, sampler: dict, inputs: list[float], time: float, rotation: bool) -> tuple:
    """A cubic spline sampler's value at ``t`` (glTF's Hermite form: in-tangent, value, out-tangent per key)."""
    raw = [tuple(value) for value in document.gltf.values(sampler["output"])]
    k = max((i for i, x in enumerate(inputs) if x <= time), default=0)
    if time < inputs[0] or k + 1 >= len(inputs):  # before the first key the first value holds
        return raw[3 * k + 1]
    interval_s = inputs[k + 1] - inputs[k]
    fraction = (time - inputs[k]) / interval_s
    start_value, start_tangent, end_value, end_tangent = (
        raw[3 * k + 1],
        raw[3 * k + 2],
        raw[3 * (k + 1) + 1],
        raw[3 * (k + 1)],
    )
    basis = (
        2 * fraction**3 - 3 * fraction**2 + 1,
        fraction**3 - 2 * fraction**2 + fraction,
        -2 * fraction**3 + 3 * fraction**2,
        fraction**3 - fraction**2,
    )
    value = tuple(
        basis[0] * start_component
        + basis[1] * interval_s * start_tangent_component
        + basis[2] * end_component
        + basis[3] * interval_s * end_tangent_component
        for start_component, start_tangent_component, end_component, end_tangent_component in zip(
            start_value, start_tangent, end_value, end_tangent, strict=True
        )
    )
    if rotation:
        length = math.sqrt(sum(component * component for component in value)) or 1.0
        value = tuple(component / length for component in value)
    return value
