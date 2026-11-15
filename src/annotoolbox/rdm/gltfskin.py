"""The skeletons and animations of a glTF document as RDM's: joints from a skin, keys from the channels.

``skeletons`` turns the skins a file's meshes use into ``Skin`` records (the RDM skeleton, its joint nodes, the map
from a skin's joint slot to the RDM joint); ``animation_files`` groups the animations into the files they came from.
A joint's inverse bind pose is its RDM record; a key is the joint's local transform (docs/rdm-gltf.md "Skeletons").
"""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..formats.rdmread import Animation, Joint, Keyframe, RdmFile, Skeleton, Track
from ..models.meshxform import qconj
from . import rigmath
from .gltfdoc import Doc, GltfImportError, tags
from .rdmgltf import inverse_bind

DEFAULT_RATE = 30  # frames per second of an animation that names none and whose keys do not show one
RATES = (30, 15, 25, 60, 24, 50, 10, 5)  # the rates 2070's and 117's animations use, the default first (rdm.md)
EXACT = 1e-4  # how close the stored values must come to the document's to be taken as they are


@dataclass
class Skin:
    skeleton: Skeleton
    gltf_skin: int
    nodes: list[int]  # the joint nodes, in RDM joint order
    slot: dict[int, int]  # the skin's joint slot -> the RDM joint index


def skeletons(d: Doc, file: str, mesh_nodes: list[int]) -> list[Skin]:
    """The skeletons of a file: the skins its meshes use and those tagged with the file, in the tagged order."""
    used = [d.node(n)["skin"] for n in mesh_nodes if "skin" in d.node(n)]
    tagged = [i for i, s in enumerate(d.g.items("skins")) if tags(s).get("file") == file]
    order = sorted(set(used) | set(tagged), key=lambda i: (tags(d.g.items("skins")[i]).get("skeleton", 1 << 30), i))
    return [_skin(d, i, mesh_nodes) for i in order]


def _weighted_slots(d: Doc, skin: int, mesh_nodes: list[int]) -> set[int]:
    """The joint slots of ``skin`` some vertex weighs."""
    out = set()
    for n in mesh_nodes:
        if d.node(n).get("skin") != skin:
            continue
        for prim in d.g.items("meshes")[d.node(n)["mesh"]].get("primitives", []):
            a = prim.get("attributes", {})
            if "JOINTS_0" not in a:
                continue
            weights = d.g.values(a["WEIGHTS_0"]) if "WEIGHTS_0" in a else None
            for v, joints in enumerate(d.g.values(a["JOINTS_0"])):
                out |= {j for k, j in enumerate(joints) if weights is None or weights[v][k] > 0}
    return out


def _skin(d: Doc, index: int, mesh_nodes: list[int]) -> Skin:
    skin = d.g.items("skins")[index]
    slots = list(skin.get("joints", []))
    if not slots:
        raise GltfImportError(f"skin {index}: no joints")
    t = [tags(d.node(n)) for n in slots]
    marked = [k for k, x in enumerate(t) if isinstance(x.get("joint"), int)]
    keep = list(range(len(slots)))
    if marked and len(marked) < len(slots):  # some joints came from the RDM, the others from the modelling tool
        weighted = _weighted_slots(d, index, mesh_nodes)
        added = [k for k in keep if k not in marked]
        if any(k in weighted for k in added):
            names = ", ".join(repr(d.name(slots[k])) for k in added)
            raise GltfImportError(f"skin {index}: joints {names} are not the source's and carry vertex weights")
        d.notes += [f"dropped joint {d.name(slots[k])!r}: added by the modelling tool, weighs nothing" for k in added]
        keep = marked
    if marked and len({t[k]["joint"] for k in keep}) == len(keep):
        keep.sort(key=lambda k: t[k]["joint"])
    ibms = _ibms(d, skin, len(slots))
    joints = [_joint(d, slots[k], ibms[k], t[k]) for k in keep]
    order = {slots[k]: r for r, k in enumerate(keep)}
    for r, k in enumerate(keep):
        parent = d.parents.get(slots[k])
        while parent is not None and parent not in order:
            parent = d.parents.get(parent)
        joints[r].parent = order.get(parent) if parent is not None else None
    joints, keep = _parent_first(d, joints, keep, index)
    _name_joints(d, joints, index)
    word = tags(skin).get("word", 0)
    extra = base64.b64decode(tags(skin)["extra"]) if "extra" in tags(skin) else b""
    if not marked:
        _like_values(d, joints)
    return Skin(Skeleton(joints, word, extra), index, [slots[k] for k in keep], {k: r for r, k in enumerate(keep)})


def _ibms(d: Doc, skin: dict, n: int) -> list[rigmath.Matrix]:
    if "inverseBindMatrices" not in skin:
        return [rigmath.IDENTITY] * n
    return [rigmath.from_columns(v) for v in d.g.values(skin["inverseBindMatrices"])]


def _joint(d: Doc, node: int, ibm: rigmath.Matrix, t: dict) -> Joint:
    m = rigmath.mirrored(ibm) if d.mirror else ibm
    q, pos, scale = rigmath.decompose(m)
    if any(abs(s - 1) > 1e-3 for s in scale):
        raise GltfImportError(
            f"joint {d.name(node)!r}: its bind pose scales {scale}; an RDM joint is turn and move only"
        )
    rotation = qconj(q)
    if "position" in t and "rotation" in t:  # the stored values, when they still give this bind pose
        stored = inverse_bind(tuple(t["position"]), tuple(t["rotation"]))
        if all(abs(stored[i][j] - m[i][j]) <= EXACT for i in range(3) for j in range(4)):
            pos, rotation = tuple(t["position"]), tuple(t["rotation"])
    lo, hi = tuple(t.get("box_min", (0.0, 0.0, 0.0))), tuple(t.get("box_max", (0.0, 0.0, 0.0)))
    return Joint(d.node(node).get("name", ""), tuple(pos), tuple(rotation), None, lo, hi)


def _parent_first(d: Doc, joints: list[Joint], keep: list[int], skin: int) -> tuple[list[Joint], list[int]]:
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
            Joint(j.name, j.position, j.rotation, None if j.parent is None else new[j.parent], j.box_min, j.box_max)
        )
    d.notes.append(f"skin {skin}: joints reordered parent first (the game drops a skeleton that is not)")
    return out, [keep[old] for old in done]


def _name_joints(d: Doc, joints: list[Joint], skin: int) -> None:
    if d.opts.joint_names == "index":
        for k, j in enumerate(joints):
            j.name = str(k)
        return
    names = [j.name for j in joints]
    if not all(names) or len(set(names)) != len(names):
        raise GltfImportError(
            f"skin {skin}: joint names are missing or repeated; animation tracks find joints by name "
            "(rename them, or --joint-names index)"
        )


def _like_values(d: Doc, joints: list[Joint]) -> None:
    """Joint boxes from the ``like`` file (by name); without one they stay zero, which 2070 also ships."""
    like = {j.name: j for s in (d.opts.like.skeletons if d.opts.like else []) for j in s.joints}
    missing = 0
    for j in joints:
        if j.name in like:
            j.box_min, j.box_max = like[j.name].box_min, like[j.name].box_max
        else:
            missing += 1
    if missing:
        d.notes.append(f"{missing} joints without a stored box: written as zero (the extent is not in glTF)")


# Animations


def joint_names(d: Doc) -> dict[int, str]:
    """Every joint node of the document and its RDM joint name."""
    out = {}
    for skin in d.g.items("skins"):
        for k, n in enumerate(skin.get("joints", [])):
            out.setdefault(n, str(k) if d.opts.joint_names == "index" else d.name(n))
    return out


def animation_files(d: Doc, infos: dict[str, dict], default_name: str) -> list[tuple[str, RdmFile, set[int]]]:
    """(file name, the file, the joint nodes it moves) per animation file: the tagged files in their entry order;
    an untagged animation becomes a file of its own named after it."""
    names = joint_names(d)
    entries: dict[str, list[tuple[int, Animation]]] = {}
    moved: dict[str, set[int]] = {}
    for k, a in enumerate(d.g.items("animations")):
        t = tags(a)
        file = t.get("file") or _file_name(default_name, a.get("name") or f"animation{k}")
        anim, nodes = _animation(d, a, t, names)
        entries.setdefault(file, []).append((t.get("entry", len(entries.get(file, []))), anim))
        moved.setdefault(file, set()).update(nodes)
    for name, info in infos.items():
        for u in info.get("unkeyed", []):
            tracks = [Track(j, []) for j in u.get("tracks", [])]
            entries.setdefault(name, []).append(
                (u.get("entry", 0), Animation(u["name"], u["duration_ms"], u["frame_rate"], tracks))
            )
    out = []
    for name, items in entries.items():
        info = infos.get(name, {})
        rdm = RdmFile(False, info.get("source", ""), info.get("exporter", ""))
        rdm.animations = [a for _k, a in sorted(items, key=lambda x: x[0])]
        if info.get("kind") == "animation":
            rdm.skeletons = [s.skeleton for s in skeletons(d, name, [])]
        out.append((name, rdm, moved.get(name, set())))
    return out


def _file_name(default_name: str, animation: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", animation).strip("_") or "animation"
    return f"{Path(default_name).stem}_{safe}.rdm"


def _animation(d: Doc, a: dict, t: dict, names: dict[int, str]) -> tuple[Animation, set[int]]:
    who = f"animation {a.get('name', '')!r}"
    groups: dict[tuple, dict] = {}  # a track: by its tagged place (two tracks may name one joint), else its joint
    on_node: dict[int | None, tuple] = {}
    nodes = set()
    for ch in a.get("channels", []):
        target, sampler = ch.get("target", {}), a["samplers"][ch["sampler"]]
        node, path = target.get("node"), target.get("path")
        if path == "weights":
            raise GltfImportError(f"{who}: morph target weights; an RDM key holds a turn and a move only")
        if node is not None and node not in names:
            d.notes.append(f"{who}: dropped the channel on {d.name(node)!r}, which is not a joint")
            continue
        joint = names[node] if node is not None else tags(ch).get("joint")
        if joint is None:
            d.notes.append(f"{who}: dropped a channel without a target")
            continue
        place = tags(ch).get("track")
        key = ("track", place) if isinstance(place, int) else on_node.get(node, ("joint", joint))
        groups.setdefault(key, {"node": node, "joint": joint})[path] = sampler
        on_node.setdefault(node, key)  # an untagged channel (a tool's own) joins the track already on its joint
        if node is not None:
            nodes.add(node)
    rate = t.get("frame_rate") or d.opts.frame_rate or _rate(d, a) or DEFAULT_RATE
    tracks = _ordered({key: _track(d, g["joint"], g, rate, who) for key, g in groups.items()}, t)
    last = max((k.time for tr in tracks for k in tr.keys), default=0.0)
    duration = t["duration_ms"] if abs(t.get("duration_ms", -9) - last * 1000) <= 1 else round(last * 1000)
    return Animation(t.get("name", a.get("name", "")), duration, rate, tracks), nodes


def _ordered(tracks: dict[tuple, Track], t: dict) -> list[Track]:
    """The tracks in the source's order (by their tagged places, else by the joint names the extras list), the ones
    without keys back in their places."""
    order, empty = t.get("tracks") or [], set(t.get("empty", []))
    if not order:
        return list(tracks.values())
    placed = {key[1]: tr for key, tr in tracks.items() if key[0] == "track"}
    by_name = {tr.joint: tr for key, tr in tracks.items() if key[0] == "joint"}
    out = []
    for k, name in enumerate(order):
        if k in placed:
            out.append(placed.pop(k))
        elif name in by_name:
            out.append(by_name.pop(name))
        elif k in empty:
            out.append(Track(name, []))
    return out + list(placed.values()) + list(by_name.values())


def _rate(d: Doc, a: dict) -> int | None:
    """The first of the games' frame rates every key time falls on (None: none fits, or no keys)."""
    times = {t for s in a.get("samplers", []) for t in d.g.values(s["input"])}
    if not times:
        return None
    for rate in RATES:
        if all(abs(t * rate - round(t * rate)) < 1e-3 for t in times):
            return rate
    d.notes.append(f"animation {a.get('name', '')!r}: its keys fall on no usual frame rate; {DEFAULT_RATE} written")
    return None


def _track(d: Doc, joint: str, g: dict, rate: int, who: str) -> Track:
    node = g["node"]
    rest = d.node(node) if node is not None else {}
    if "scale" in g:
        _, values = _keys(d, g["scale"])
        if any(abs(c - 1) > 1e-4 for v in values for c in v):
            raise GltfImportError(f"{who}: joint {joint!r} scales; an RDM key holds a turn and a move only")
    times = sorted({x for p in ("rotation", "translation") if p in g for x in _times(d, g[p], rate)})
    rot = _at(d, g.get("rotation"), times, tuple(rest.get("rotation", (0.0, 0.0, 0.0, 1.0))), True)
    pos = _at(d, g.get("translation"), times, tuple(rest.get("translation", (0.0, 0.0, 0.0))), False)
    above = d.parents.get(node) if node is not None else None
    frame = d.world(above) if above is not None and not _is_joint(d, above) else rigmath.IDENTITY
    keys = []
    for time, q, p in zip(times, rot, pos, strict=True):
        if not rigmath.is_identity(frame, 0.0):  # a root under an armature object that moves or turns
            q, p, _s = rigmath.decompose(rigmath.multiply(frame, rigmath.from_rotation_translation(q, p)))
        if d.mirror:
            q, p = rigmath.mirrored_rotation(q), rigmath.mirrored_point(p)
        keys.append(Keyframe(qconj(q), tuple(p), time))
    return Track(joint, keys)


def _is_joint(d: Doc, node: int) -> bool:
    return any(node in s.get("joints", []) for s in d.g.items("skins"))


def _keys(d: Doc, sampler: dict) -> tuple[list[float], list[tuple]]:
    return d.g.values(sampler["input"]), [tuple(v) for v in d.g.values(sampler["output"])]


def _times(d: Doc, sampler: dict, rate: int) -> list[float]:
    times = d.g.values(sampler["input"])
    if sampler.get("interpolation") == "CUBICSPLINE" and times:  # sampled at the frame rate
        n = int(math.floor((times[-1] - times[0]) * rate + 1e-6))
        return sorted(set(times) | {times[0] + k / rate for k in range(n + 1)})
    return list(times)


def _at(d: Doc, sampler: dict | None, times: list[float], rest: tuple, rotation: bool) -> list[tuple]:
    """The sampler's values at ``times`` by its interpolation (``rest`` where the path is not animated)."""
    if sampler is None:
        return [rest] * len(times)
    inputs, out = _keys(d, sampler)
    mode = sampler.get("interpolation", "LINEAR")
    if mode == "CUBICSPLINE":
        return [_cubic(d, sampler, inputs, t, rotation) for t in times]
    exact = dict(zip(inputs, out, strict=True))
    values = []
    for t in times:
        if t in exact:
            values.append(exact[t])
            continue
        k = max((i for i, x in enumerate(inputs) if x <= t), default=0)
        if mode == "STEP" or k + 1 >= len(inputs):
            values.append(out[k])
            continue
        u = (t - inputs[k]) / (inputs[k + 1] - inputs[k])
        values.append(_lerp(out[k], out[k + 1], u, rotation))
    return values


def _lerp(a: tuple, b: tuple, u: float, rotation: bool) -> tuple:
    if rotation and sum(x * y for x, y in zip(a, b, strict=True)) < 0:
        b = tuple(-x for x in b)
    v = tuple(x + (y - x) * u for x, y in zip(a, b, strict=True))
    if rotation:
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        v = tuple(c / n for c in v)
    return v


def _cubic(d: Doc, sampler: dict, inputs: list[float], t: float, rotation: bool) -> tuple:
    """A cubic spline sampler's value at ``t`` (glTF's Hermite form: in-tangent, value, out-tangent per key)."""
    raw = [tuple(v) for v in d.g.values(sampler["output"])]
    k = max((i for i, x in enumerate(inputs) if x <= t), default=0)
    if k + 1 >= len(inputs):
        return raw[3 * k + 1]
    dt = inputs[k + 1] - inputs[k]
    s = (t - inputs[k]) / dt
    p0, m0, p1, m1 = raw[3 * k + 1], raw[3 * k + 2], raw[3 * (k + 1) + 1], raw[3 * (k + 1)]
    h = (2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2)
    v = tuple(h[0] * a + h[1] * dt * b + h[2] * c + h[3] * dt * e for a, b, c, e in zip(p0, m0, p1, m1, strict=True))
    if rotation:
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        v = tuple(c / n for c in v)
    return v
