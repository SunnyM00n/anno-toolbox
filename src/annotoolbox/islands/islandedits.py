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
    parse_pair,
    read_bits,
    read_sparse,
    warn_unmatched,
    write_bits,
    write_sparse,
)
from .islandground import stroke_material
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


def _scale(value):
    return tuple(map(float, value)) if isinstance(value, list) else float(value)


def _set_mesh_attribute(object_tag: filedb.STag, name: str, data: bytes, default: bytes) -> None:
    """Set ``Mesh/<name>``; an object that stores none (1800's, at the default) gets one only for another value."""
    attribute = object_tag.path(f"Mesh/{name}")
    if attribute is not None:
        attribute.data = data
    elif data != default:
        mesh = object_tag.find("Mesh")
        if mesh is None:
            mesh = filedb.STag("Mesh", [])
            object_tag.children.append(mesh)
        mesh.children.append(filedb.SAttrib(name, data))


def _heading(object_tag: filedb.STag) -> float:
    """The turn an object stands at: its ``Direction``, else its orientation's turn."""
    if object_tag.find("Direction") is not None:
        return struct.unpack("<f", object_tag.find("Direction").data)[0]
    orient = object_tag.path("Mesh/Orientation")
    return _yaw(struct.unpack("<4f", orient.data)) if orient is not None else 0.0


def set_object(object_tag: filedb.STag, change: dict) -> None:
    """Apply an object edit (``position`` / ``rotation`` / ``scale``) to a placed object; a path it has (a work area
    slot's outline) moves and turns with it."""
    place = object_tag.find("Position")
    had = (struct.unpack("<3f", place.data), _heading(object_tag)) if place is not None else None
    _write_pose(object_tag, change)
    if had is not None:
        now = (struct.unpack("<3f", place.data), _heading(object_tag))
        if had != now:
            objectpath.follow(object_tag, had[0], now[0], now[1] - had[1])


def _write_pose(object_tag: filedb.STag, change: dict) -> None:
    if "position" in change:
        object_tag.find("Position").data = struct.pack("<3f", *map(float, change["position"]))
    if object_tag.path("Prop/Rotation") is not None:  # an object of the Prop group keeps its transform there as well
        _set_prop_object(object_tag, change)
        return
    if "rotation" in change:
        _set_mesh_attribute(
            object_tag,
            "Orientation",
            struct.pack("<4f", *map(float, change["rotation"])),
            struct.pack("<4f", 0, 0, 0, 1),
        )
        if object_tag.find("Direction") is not None:
            object_tag.find("Direction").data = struct.pack("<f", _yaw(change["rotation"]) % (2 * math.pi))
    if "scale" in change:
        _set_mesh_attribute(object_tag, "Scale", struct.pack("<f", float(change["scale"])), struct.pack("<f", 1.0))


def _set_prop_object(object_tag: filedb.STag, change: dict) -> None:
    """An object of the Prop group (1800): its ``Prop`` block holds the place, the turn and a scale per axis."""
    prop = object_tag.find("Prop")
    if "position" in change and prop.find("Position") is not None:
        prop.find("Position").data = object_tag.find("Position").data
    if "rotation" in change:
        prop.find("Rotation").data = struct.pack("<4f", *map(float, change["rotation"]))
        if object_tag.find("Direction") is not None:
            object_tag.find("Direction").data = struct.pack("<f", _yaw(change["rotation"]) % (2 * math.pi))
    if "scale" in change and prop.find("Scale") is not None:
        scale = _scale(change["scale"])
        prop.find("Scale").data = struct.pack("<3f", *(scale if isinstance(scale, tuple) else (scale,) * 3))


def object_name(object_tag: filedb.STag) -> str | None:
    """What names a placed object in the edit file: its model config, or ``guid:<asset>`` for one
    that draws its asset's model or none (slots, markers, herds)."""
    cfg = object_tag.path("DynamicVariation/ConfigFilename")
    if cfg is not None:
        return cfg.data.decode("utf-16-le")
    guid_tag = object_tag.find("guid")
    return f"guid:{struct.unpack_from('<I', guid_tag.data)[0]}" if guid_tag is not None else None


def _objects(builder):
    """Every placed object: (group, area objects tag, object, name), editor objects included; an island that edits
    more groups names them (``object_groups``: 1800's natural presets and props)."""
    for _, doc, _ in builder._areas_data():
        for group in getattr(builder, "object_groups", ("GameObject", "EditorObject")):
            object_list = area_objects(doc, group)
            for object_tag in list(object_list.children) if object_list is not None else []:
                name = (
                    object_name(object_tag)
                    if isinstance(object_tag, filedb.STag) and object_tag.find("Position") is not None
                    else None
                )
                if name:
                    yield group, object_list, object_tag, name


def height_grid(heights: dict, rows: int, cols: int):
    """The height edits (``"z,x"`` -> a number) as a ``rows`` x ``cols`` float grid (numpy); a corner off it is dropped."""
    import numpy as np

    grid = np.zeros((rows, cols), np.float64)
    for corner_key, height in heights.items():
        corner = parse_pair(corner_key)
        if corner is not None and 0 <= corner[0] < rows and 0 <= corner[1] < cols:
            grid[corner] = float(height)
    return grid


def apply_heights(builder, edits: dict) -> int:
    """Raise or lower the island's tile corners by ``edits["heights"]`` (``"z,x"`` -> world units),
    before the builder turns its corner heights into heightmaps and render data. Returns the corners
    changed; an edit off the island, and a river or lake edit that matches nothing, is a warning."""
    changed_count = 0
    heights = edits.get("heights", {})
    for corner_key, delta in heights.items():
        z, x = parse_pair(corner_key) or (-1, -1)
        if 0 <= z < len(builder.heights) and 0 <= x < len(builder.heights[z]):
            builder.heights[z][x] += float(delta) * HEIGHT_UNITS
            changed_count += 1
    if changed_count < len(heights):
        message = f"{len(heights) - changed_count} of {len(heights)} height edits lie off the island: dropped"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    warn_unmatched_water(builder, edits)
    return changed_count


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
    if not edits or not any(edits.get(edit_key) for edit_key in (*WATER_EDITS, SHAPE_EDITS)):
        return None
    rest = (*WATER_EDITS[2:], SHAPE_EDITS)
    return {edit_key: edits.get(edit_key, {}) for edit_key in ("rivers", "lakes")} | {
        edit_key: edits[edit_key] for edit_key in rest if edits.get(edit_key)
    }


def area_id(value) -> int:
    """The area id an area edit's value stands for: 0 none, 1 the construction area, any other number that id."""
    kind = int(value)
    return AREA_NONE if kind == 0 else AREA_ISLAND if kind == 1 else kind


def apply_area(game_manager: filedb.STag, cells: dict) -> dict:
    """Set area tiles (``"x,z"`` -> 1 the construction area, 0 none, or the id of another area the island has, such as
    a continental island's 0x4001) in ``AreaIDs``. A tile of another area is left alone by 0 and 1; an id the island
    does not have is not made up. Returns the tiles changed and what matched nothing (``unmatched``)."""
    tag = game_manager.path("AreaIDs")
    report: dict = {"cells": 0, "unmatched": []}
    if not cells:
        return report
    if tag is None:
        report["unmatched"].append(tr("islands.no_area_grid_island"))
        return report
    grid = read_sparse(tag)
    kinds = {tile_id for row in grid for tile_id in row} - {0, AREA_NONE, AREA_ISLAND}  # the island's other areas
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
            report["unmatched"].append(tr("islands.area_tile_key", key=name))
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

    regrow_manager = builder.game_session_manager.path("RegrowManager")
    changes = edits.get("forest", {})
    if regrow_manager is None or not changes:
        return {"planted": 0, "cleared": 0}
    trees = np.asarray(read_bits(regrow_manager.find("TreeMap")), np.int64)
    forest_chunk_map = regrow_manager.find("ForestChunkMap")
    chunks = np.asarray(read_sparse(forest_chunk_map, "B"), np.int64) if forest_chunk_map is not None else None
    labelled = np.argwhere(chunks > 0) if chunks is not None else np.zeros((0, 2), int)
    land = np.asarray(builder.land_mask(), bool)
    report = {"planted": 0, "cleared": 0}
    for tile_key, planted in changes.items():
        x, z = parse_pair(tile_key) or (-1, -1)
        if not (0 <= z < trees.shape[0] and 0 <= x < trees.shape[1]) or (planted and not land[z, x]):
            continue
        trees[z, x] = 1 if planted else 0
        report["planted" if planted else "cleared"] += 1
        if chunks is not None:
            if not planted:
                chunks[z, x] = 0
            elif len(labelled):
                near = labelled[np.argmin(((labelled - (z, x)) ** 2).sum(axis=1))]
                chunks[z, x] = chunks[near[0], near[1]]
            else:
                chunks[z, x] = 1
    write_bits(regrow_manager.find("TreeMap"), trees.tolist())
    if chunks is not None:
        write_sparse(forest_chunk_map, chunks.tolist(), "B")
    return report


def _paint_material_ok(stroke: dict) -> bool:
    return 0 <= stroke_material(stroke) < 32


def apply_paint(builder, edits: dict) -> int:
    """Hand the painted strokes (``edits["paint"]``) to the builder; its material weights lay them
    over the 2070 layers (``islandground.paint_strokes``). Call before ``apply_materials``."""
    builder.paint = list(edits.get("paint", []))
    lost = sum(1 for stroke in builder.paint if not _paint_material_ok(stroke))
    if lost:
        message = f"{lost} of {len(builder.paint)} painted strokes name a material outside 0-31: they paint nothing"
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    return len(builder.paint)


def apply(builder, edits: dict, template: filedb.STag | None = None, editor_samples: dict | None = None) -> dict:
    """Apply ``edits`` to an island being built and say what matched.

    ``template``: the decoration object new objects are copied from (``donor117.load_template``);
    ``editor_samples``: vanilla instances of the editor-only kinds (``editorassets.samples``), for
    added entries that name an asset (``guid``) instead of a model."""
    report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "props changed": 0}
    report |= {"props deleted": 0, "props added": 0, "editor objects added": 0, "unmatched": []}
    last = None

    def keyed():
        nonlocal last
        for group, object_list, placed, name in _objects(builder):
            if group == "GameObject":
                last = object_list  # where a new object goes: the last game objects list
            yield key(name, struct.unpack("<3f", placed.find("Position").data)), (object_list, placed)

    def remove(gone: list) -> None:
        for object_list, placed in gone:
            object_list.children.remove(placed)

    ops = objectedits.SObjectOps(lambda handle, change: set_object(handle[1], change), remove)
    objectedits.apply_changes(keyed(), edits.get("objects", {}), ops, report)
    if edits.get("added_objects") and template is not None and last is not None:
        high = template.find("ID").data[4:]
        serial = last_serial(last, high)
        for add in edits["added_objects"]:
            if "guid" in add:
                continue
            serial += 1
            new_object = clone_object(template, serial)
            new_object.path("DynamicVariation/ConfigFilename").data = add["cfg"].replace("/", "\\").encode("utf-16-le")
            set_object(
                new_object, {"position": add["position"], "rotation": add["rotation"], "scale": add.get("scale", 1)}
            )
            last.children.append(new_object)
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
        area = apply_area(builder.game_session_manager, edits["area"])
        report["area tiles"] = area["cells"]
        report["unmatched"] += area["unmatched"]
    if edits.get("session"):
        session = sessionsettings.apply(builder.game_session_manager, edits["session"], builder.size)
        report["session settings"] = len(session["set"])
        report["unmatched"] += session["unmatched"]
    if edits.get("herd"):
        herd = herdareas.apply(builder.game_session_manager, edits["herd"], getattr(builder, "herd_template", None))
        report["herd tiles"] = herd["tiles"]
        report["unmatched"] += herd["unmatched"]
        if herd.get("dropped"):  # painted, but a patch under the minimum is no herd area (island-editor "Herd areas")
            report["unmatched"].append(
                tr(
                    "islands.herd_tiles_patches_under_not",
                    n=herd["dropped"],
                    m=herdareas.HERD_AREA_MIN,
                )
            )
    if slots := refresh_slot_list(builder):
        report["slot list"] = slots
    return report


def refresh_slot_list(builder) -> dict[str, int]:
    """List the slot objects where the edits left them (``slotlist.refresh``): what moved, was dropped or added."""
    island_info = getattr(builder, "a7minfo", None)
    if island_info is None:
        return {}
    placed = (
        (
            object_tag.find("ID").data,
            struct.unpack_from("<I", object_tag.find("guid").data)[0],
            struct.unpack("<3f", object_tag.find("Position").data),
        )
        for _group, _object_list, object_tag, _name in _objects(builder)
        if object_tag.find("guid") is not None and object_tag.find("ID") is not None
    )
    return slotlist.refresh(island_info, placed)


def _apply_props(builder, edits: dict, report: dict) -> None:
    prop_grid = builder.rd3d.path("PropGrid")
    names = prop_grid.find("FileNames") if prop_grid is not None else None
    instances = prop_grid.find("Instances") if prop_grid is not None else None
    if names is None or instances is None:
        return
    files = [child.data.decode("utf-16-le") for child in names.children]
    wanted = dict(edits.get("props", {}))
    for prop in list(instances.children):
        index = struct.unpack("<I", prop.find("Index").data)[0]
        prop_key = key(files[index] if index < len(files) else "", struct.unpack("<3f", prop.find("Position").data))
        change = wanted.pop(prop_key, None)
        if change is None:
            continue
        if change.get("deleted"):
            instances.children.remove(prop)
            report["props deleted"] += 1
            continue
        if "position" in change:
            prop.find("Position").data = struct.pack("<3f", *map(float, change["position"]))
        if "rotation" in change:
            prop.find("Rotation").data = struct.pack("<4f", *map(float, change["rotation"]))
        if "scale" in change:
            scale = change["scale"]
            prop.find("Scale").data = struct.pack(
                "<3f", *(map(float, scale) if isinstance(scale, list) else (float(scale),) * 3)
            )
        if change.get("color") and prop.find("Color") is not None:
            prop.find("Color").data = struct.pack("<4f", *map(float, change["color"]))
        report["props changed"] += 1
    report["unmatched"] += sorted(wanted)
    if edits.get("added_props"):
        items = [  # the whole quaternion: the editor can tilt a prop, not only turn it
            (
                added["cfg"],
                tuple(added["position"]),
                tuple(map(float, added.get("rotation", (0, 0, 0, 1)))),
                _scale(added.get("scale", 1.0)),
                tuple(map(float, added["color"])) if added.get("color") else None,
            )
            for added in edits["added_props"]
        ]
        report["props added"] += append_props(builder, items)
