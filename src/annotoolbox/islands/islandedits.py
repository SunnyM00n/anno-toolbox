"""Hand edits to an island, kept in a file of their own and applied on top of every build.

The island editor writes them (``islandeditor.py``); ``scripts/island_test.py --edits`` applies
them while it builds the island, so a better build and a hand fix do not overwrite each other.
An object or prop is named by its model and the position the build gave it (``key``), not by
its place in a list: a build change that moves nothing keeps every edit attached, and an edit
that no longer finds its object is reported, not lost. Format: ``docs/island-editor-tools.md`` "The edit file".
"""

from __future__ import annotations

import json
import math
import struct
import warnings
from pathlib import Path

from ..formats import filedb
from ..formats.a7mread import area_objects
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from . import herdareas, objectedits, objectpath, sessionsettings, slotlist
from .islandgrid import (
    AREA_ISLAND,
    AREA_NONE,
    HEIGHT_UNITS,
    cell_key,
    read_bits,
    read_sparse,
    warn_unmatched,
    write_bits,
    write_sparse,
)
from .islandwater import clone_object, last_serial
from .propgrid import append_props

VERSION = 1


def key(cfg: str, position) -> str:
    """An object's name across builds: its model and its built position (to 1 cm)."""
    x, y, z = position
    return f"{cfg.replace(chr(92), '/').lower()}|{x:.2f}|{y:.2f}|{z:.2f}"


WATER_EDITS = ("rivers", "lakes", "coast", "lake_outlines")  # the edit file's water keys the build takes
SHAPE_EDITS = "blocker_shapes"  # {"build": {"k": {"points": ...}}, "camera": {...}} (island-editor "Blocker shapes")


def empty() -> dict:
    return {
        "version": VERSION,
        "objects": {},
        "added_objects": [],
        "props": {},
        "added_props": [],
        "heights": {},
        "paint": [],
        "tint": [],
        "rivers": {},
        "lakes": {},
        "ford": {},
        "environment": {},
        "lake_outlines": {},
        "forest": {},
        "materials": {},
    }


def load(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return empty()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {**empty(), **data}


def save(path: Path, edits: dict) -> None:
    text = json.dumps({**empty(), **edits}, indent=1, sort_keys=True) + "\n"
    write_atomic(path, text.encode("utf-8"))  # hours of hand work: never half written


def _yaw(rotation) -> float:
    """The turn about +Y a placed quaternion makes (the way ``objects2070.placed_quat`` writes it)."""
    x, y, z, w = rotation
    return -2.0 * math.atan2(y, w)


def _scale(s):
    return tuple(map(float, s)) if isinstance(s, list) else float(s)


def _set_mesh_attr(obj: filedb.Tag, name: str, data: bytes, default: bytes) -> None:
    """Set ``Mesh/<name>``; an object that stores none (1800's, at the default) gets one only for another value."""
    attr = obj.path(f"Mesh/{name}")
    if attr is not None:
        attr.data = data
    elif data != default:
        mesh = obj.find("Mesh")
        if mesh is None:
            mesh = filedb.Tag("Mesh", [])
            obj.children.append(mesh)
        mesh.children.append(filedb.Attrib(name, data))


def _heading(obj: filedb.Tag) -> float:
    """The turn an object stands at: its ``Direction``, else its orientation's turn."""
    if obj.find("Direction") is not None:
        return struct.unpack("<f", obj.find("Direction").data)[0]
    orient = obj.path("Mesh/Orientation")
    return _yaw(struct.unpack("<4f", orient.data)) if orient is not None else 0.0


def set_object(obj: filedb.Tag, change: dict) -> None:
    """Apply an object edit (``position`` / ``rotation`` / ``scale``) to a placed object; a path it has (a work area
    slot's outline) moves and turns with it."""
    place = obj.find("Position")
    had = (struct.unpack("<3f", place.data), _heading(obj)) if place is not None else None
    _write_pose(obj, change)
    if had is not None:
        now = (struct.unpack("<3f", place.data), _heading(obj))
        if had != now:
            objectpath.follow(obj, had[0], now[0], now[1] - had[1])


def _write_pose(obj: filedb.Tag, change: dict) -> None:
    if "position" in change:
        obj.find("Position").data = struct.pack("<3f", *map(float, change["position"]))
    if obj.path("Prop/Rotation") is not None:  # an object of the Prop group keeps its transform there as well
        _set_prop_object(obj, change)
        return
    if "rotation" in change:
        _set_mesh_attr(
            obj, "Orientation", struct.pack("<4f", *map(float, change["rotation"])), struct.pack("<4f", 0, 0, 0, 1)
        )
        if obj.find("Direction") is not None:
            obj.find("Direction").data = struct.pack("<f", _yaw(change["rotation"]) % (2 * math.pi))
    if "scale" in change:
        _set_mesh_attr(obj, "Scale", struct.pack("<f", float(change["scale"])), struct.pack("<f", 1.0))


def _set_prop_object(obj: filedb.Tag, change: dict) -> None:
    """An object of the Prop group (1800): its ``Prop`` block holds the place, the turn and a scale per axis."""
    prop = obj.find("Prop")
    if "position" in change and prop.find("Position") is not None:
        prop.find("Position").data = obj.find("Position").data
    if "rotation" in change:
        prop.find("Rotation").data = struct.pack("<4f", *map(float, change["rotation"]))
        if obj.find("Direction") is not None:
            obj.find("Direction").data = struct.pack("<f", _yaw(change["rotation"]) % (2 * math.pi))
    if "scale" in change and prop.find("Scale") is not None:
        s = _scale(change["scale"])
        prop.find("Scale").data = struct.pack("<3f", *(s if isinstance(s, tuple) else (s,) * 3))


def object_name(o: filedb.Tag) -> str | None:
    """What names a placed object in the edit file: its model config, or ``guid:<asset>`` for one
    that draws its asset's model or none (slots, markers, herds)."""
    cfg = o.path("DynamicVariation/ConfigFilename")
    if cfg is not None:
        return cfg.data.decode("utf-16-le")
    g = o.find("guid")
    return f"guid:{struct.unpack_from('<I', g.data)[0]}" if g is not None else None


def _objects(builder):
    """Every placed object: (group, area objects tag, object, name), editor objects included; an island that edits
    more groups names them (``object_groups``: 1800's natural presets and props)."""
    for _, doc, _ in builder._areas_data():
        for group in getattr(builder, "object_groups", ("GameObject", "EditorObject")):
            objs = area_objects(doc, group)
            for o in list(objs.children) if objs is not None else []:
                name = object_name(o) if isinstance(o, filedb.Tag) and o.find("Position") is not None else None
                if name:
                    yield group, objs, o, name


def height_grid(heights: dict, rows: int, cols: int):
    """The height edits (``"z,x"`` -> a number) as a ``rows`` x ``cols`` float grid (numpy); a corner off it is dropped."""
    import numpy as np

    grid = np.zeros((rows, cols), np.float64)
    for k, v in heights.items():
        z, x = map(int, k.split(","))
        if 0 <= z < rows and 0 <= x < cols:
            grid[z, x] = float(v)
    return grid


def apply_heights(builder, edits: dict) -> int:
    """Raise or lower the island's tile corners by ``edits["heights"]`` (``"z,x"`` -> world units),
    before the builder turns its corner heights into heightmaps and render data. Returns the corners
    changed; an edit off the island, and a river or lake edit that matches nothing, is a warning."""
    n = 0
    heights = edits.get("heights", {})
    for k, delta in heights.items():
        z, x = map(int, k.split(","))
        if 0 <= z < len(builder.heights) and 0 <= x < len(builder.heights[z]):
            builder.heights[z][x] += float(delta) * HEIGHT_UNITS
            n += 1
    if n < len(heights):
        message = f"{len(heights) - n} of {len(heights)} height edits lie off the island: dropped"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    warn_unmatched_water(builder, edits)
    return n


def warn_unmatched_water(builder, edits: dict) -> None:
    """Warn about river and lake edits whose index the built island does not have: the island builder
    skips them, for example when the build now finds fewer rivers than the editor showed."""
    for what, key, count in (
        ("river", "rivers", len(getattr(builder, "rivers", None) or [])),
        ("lake", "lakes", len(getattr(builder, "lake_levels", None) or [])),
    ):
        warn_unmatched(edits.get(key, {}), count, what, f"{what}s", stacklevel=4)


def water(edits: dict | None) -> dict | None:
    """The water and blocker shapes of an edit file (rivers and lake levels; outlines, coast lines and shapes when it
    has some), as the island builder takes them."""
    if not edits or not any(edits.get(k) for k in (*WATER_EDITS, SHAPE_EDITS)):
        return None
    rest = (*WATER_EDITS[2:], SHAPE_EDITS)
    return {k: edits.get(k, {}) for k in ("rivers", "lakes")} | {k: edits[k] for k in rest if edits.get(k)}


def area_id(value) -> int:
    """The area id an area edit's value stands for: 0 none, 1 the construction area, any other number that id."""
    kind = int(value)
    return AREA_NONE if kind == 0 else AREA_ISLAND if kind == 1 else kind


def apply_area(gm: filedb.Tag, cells: dict) -> dict:
    """Set area tiles (``"x,z"`` -> 1 the construction area, 0 none, or the id of another area the island has, such as
    a continental island's 0x4001) in ``AreaIDs``. A tile of another area is left alone by 0 and 1; an id the island
    does not have is not made up. Returns the tiles changed and what matched nothing (``unmatched``)."""
    tag = gm.path("AreaIDs")
    report: dict = {"cells": 0, "unmatched": []}
    if not cells:
        return report
    if tag is None:
        report["unmatched"].append(tr("no area grid on this island"))
        return report
    grid = read_sparse(tag)
    kinds = {v for row in grid for v in row} - {0, AREA_NONE, AREA_ISLAND}  # the island's other areas
    for name, value in sorted(cells.items()):
        cell = cell_key(name, len(grid))
        try:
            want = area_id(value) if cell else None
        except (ValueError, TypeError):
            want = None
        old = grid[cell[1]][cell[0]] if cell else None
        plain = want in (AREA_NONE, AREA_ISLAND)
        if (
            want is None
            or old not in (AREA_NONE, AREA_ISLAND, *(() if plain else kinds))
            or not (plain or want in kinds)
        ):
            report["unmatched"].append(tr("area tile {key}", key=name))
            continue
        report["cells"] += int(old != want)
        grid[cell[1]][cell[0]] = want
    if report["cells"]:
        write_sparse(tag, grid)
    return report


def apply_forest(builder, edits: dict) -> dict:
    """Plant or clear a 117 island's forest tiles (``edits["forest"]``: ``"x,z"`` -> 1 or 0); a planted
    tile joins the nearest chunk. A built island has no forest tiles: its trees are props, and the
    same edits plant and clear those."""
    import numpy as np

    rg = builder.gm.path("RegrowManager")
    changes = edits.get("forest", {})
    if rg is None or not changes:
        return {"planted": 0, "cleared": 0}
    trees = np.asarray(read_bits(rg.find("TreeMap")), np.int64)
    fc = rg.find("ForestChunkMap")
    chunks = np.asarray(read_sparse(fc, "B"), np.int64) if fc is not None else None
    labelled = np.argwhere(chunks > 0) if chunks is not None else np.zeros((0, 2), int)
    land = np.asarray(builder.land_mask(), bool)
    report = {"planted": 0, "cleared": 0}
    for k, v in changes.items():
        x, z = map(int, k.split(","))
        if not (0 <= z < trees.shape[0] and 0 <= x < trees.shape[1]) or (v and not land[z, x]):
            continue
        trees[z, x] = 1 if v else 0
        report["planted" if v else "cleared"] += 1
        if chunks is not None:
            if not v:
                chunks[z, x] = 0
            elif len(labelled):
                near = labelled[np.argmin(((labelled - (z, x)) ** 2).sum(axis=1))]
                chunks[z, x] = chunks[near[0], near[1]]
            else:
                chunks[z, x] = 1
    write_bits(rg.find("TreeMap"), trees.tolist())
    if chunks is not None:
        write_sparse(fc, chunks.tolist(), "B")
    return report


def _paint_material_ok(stroke: dict) -> bool:
    try:
        return 0 <= int(stroke.get("material", -1)) < 32
    except (TypeError, ValueError):
        return False


def apply_paint(builder, edits: dict) -> int:
    """Hand the painted strokes (``edits["paint"]``) to the builder; its material weights lay them
    over the 2070 layers (``islandground.paint_strokes``). Call before ``apply_materials``."""
    builder.paint = list(edits.get("paint", []))
    lost = sum(1 for st in builder.paint if not _paint_material_ok(st))
    if lost:
        message = f"{lost} of {len(builder.paint)} painted strokes name a material outside 0-31: they paint nothing"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    return len(builder.paint)


def apply(builder, edits: dict, template: filedb.Tag | None = None, editor_samples: dict | None = None) -> dict:
    """Apply ``edits`` to an island being built and say what matched.

    ``template``: the decoration object new objects are copied from (``donor117.load_template``);
    ``editor_samples``: vanilla instances of the editor-only kinds (``editorassets.samples``), for
    added entries that name an asset (``guid``) instead of a model."""
    report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "props changed": 0}
    report |= {"props deleted": 0, "props added": 0, "editor objects added": 0, "unmatched": []}
    last = None

    def keyed():
        nonlocal last
        for group, objs, o, name in _objects(builder):
            if group == "GameObject":
                last = objs  # where a new object goes: the last game objects list
            yield key(name, struct.unpack("<3f", o.find("Position").data)), (objs, o)

    def remove(gone: list) -> None:
        for objs, o in gone:
            objs.children.remove(o)

    ops = objectedits.ObjectOps(lambda handle, change: set_object(handle[1], change), remove)
    objectedits.apply_changes(keyed(), edits.get("objects", {}), ops, report)
    if edits.get("added_objects") and template is not None and last is not None:
        high = template.find("ID").data[4:]
        serial = last_serial(last, high)
        for add in edits["added_objects"]:
            if "guid" in add:
                continue
            serial += 1
            obj = clone_object(template, serial)
            obj.path("DynamicVariation/ConfigFilename").data = add["cfg"].replace("/", "\\").encode("utf-16-le")
            set_object(obj, {"position": add["position"], "rotation": add["rotation"], "scale": add.get("scale", 1)})
            last.children.append(obj)
            report["objects added"] += 1
    for add in edits.get("added_objects", []):
        if "guid" in add:
            from .editorassets import place

            if place(builder, editor_samples or {}, add):
                report["editor objects added"] += 1
            else:
                report["unmatched"].append(f"guid:{add['guid']} ({add.get('template')})")
    _apply_props(builder, edits, report)
    if edits.get("area"):
        area = apply_area(builder.gm, edits["area"])
        report["area tiles"] = area["cells"]
        report["unmatched"] += area["unmatched"]
    if edits.get("session"):
        session = sessionsettings.apply(builder.gm, edits["session"], builder.size)
        report["session settings"] = len(session["set"])
        report["unmatched"] += session["unmatched"]
    if edits.get("herd"):
        herd = herdareas.apply(builder.gm, edits["herd"], getattr(builder, "herd_template", None))
        report["herd tiles"] = herd["tiles"]
        report["unmatched"] += herd["unmatched"]
        if herd.get("dropped"):  # painted, but a patch under the minimum is no herd area (island-editor "Herd areas")
            report["unmatched"].append(
                tr(
                    "{n} herd tiles in patches under {m} tiles were not kept",
                    n=herd["dropped"],
                    m=herdareas.HERD_AREA_MIN,
                )
            )
    if slots := refresh_slot_list(builder):
        report["slot list"] = slots
    return report


def refresh_slot_list(builder) -> dict[str, int]:
    """List the slot objects where the edits left them (``slotlist.refresh``): what moved, was dropped or added."""
    info = getattr(builder, "info", None)
    if info is None:
        return {}
    placed = (
        (
            o.find("ID").data,
            struct.unpack_from("<I", o.find("guid").data)[0],
            struct.unpack("<3f", o.find("Position").data),
        )
        for _group, _objs, o, _name in _objects(builder)
        if o.find("guid") is not None and o.find("ID") is not None
    )
    return slotlist.refresh(info, placed)


def _apply_props(builder, edits: dict, report: dict) -> None:
    pg = builder.rd.path("PropGrid")
    names = pg.find("FileNames") if pg is not None else None
    inst = pg.find("Instances") if pg is not None else None
    if names is None or inst is None:
        return
    files = [c.data.decode("utf-16-le") for c in names.children]
    wanted = dict(edits.get("props", {}))
    for p in list(inst.children):
        index = struct.unpack("<I", p.find("Index").data)[0]
        k = key(files[index] if index < len(files) else "", struct.unpack("<3f", p.find("Position").data))
        change = wanted.pop(k, None)
        if change is None:
            continue
        if change.get("deleted"):
            inst.children.remove(p)
            report["props deleted"] += 1
            continue
        if "position" in change:
            p.find("Position").data = struct.pack("<3f", *map(float, change["position"]))
        if "rotation" in change:
            p.find("Rotation").data = struct.pack("<4f", *map(float, change["rotation"]))
        if "scale" in change:
            s = change["scale"]
            p.find("Scale").data = struct.pack("<3f", *(map(float, s) if isinstance(s, list) else (float(s),) * 3))
        if change.get("color") and p.find("Color") is not None:
            p.find("Color").data = struct.pack("<4f", *map(float, change["color"]))
        report["props changed"] += 1
    report["unmatched"] += sorted(wanted)
    if edits.get("added_props"):
        items = [  # the whole quaternion: the editor can tilt a prop, not only turn it
            (
                a["cfg"],
                tuple(a["position"]),
                tuple(map(float, a.get("rotation", (0, 0, 0, 1)))),
                _scale(a.get("scale", 1.0)),
                tuple(map(float, a["color"])) if a.get("color") else None,
            )
            for a in edits["added_props"]
        ]
        report["props added"] += append_props(builder, items)
