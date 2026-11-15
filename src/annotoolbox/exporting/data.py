"""Data files exported as JSON and CSV: FileDB documents, XML (assets, templates, properties), language texts,
Phoenix UI files, baked particles, and island files (the object list, the 2070 height map).

JSON keeps the structure and the small values; a big binary attribute is its size and its first bytes, so a JSON of
a FileDB document is for reading and searching, not a byte-exact dump (the stored file is exported as well).
``export_data`` and the others take a ``Job`` (``run``). docs/exporting.md.
"""

from __future__ import annotations

import csv
import io
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from ..formats import filedb
from .run import Job, Made

HEX_BYTES = 48  # a binary value is shown by its first bytes and its size
ROW_LIMIT = 200_000  # CSV rows at most
FILEDB_SUFFIXES = (".ifo", ".fc", ".ctt", ".a7t", ".a7te", ".a7minfo", ".tmc", ".bfg")


def _dump(value, indent: int | None = 1) -> bytes:
    return (json.dumps(value, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def _csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# FileDB


def _attribute(a: filedb.Attrib) -> dict:
    data = a.data
    out: dict = {"name": a.name, "size": len(data)}
    if data and all(32 <= b < 127 for b in data):
        out["text"] = data.decode("ascii")
    elif len(data) == 4:
        out["u32"] = struct.unpack("<I", data)[0]
    else:
        out["hex"] = data[:HEX_BYTES].hex()
        if len(data) > HEX_BYTES:
            out["truncated"] = True
    return out


def filedb_tree(tag: filedb.Tag) -> dict:
    """A FileDB tag as plain data: its name and children (tags recursively, attributes by ``_attribute``)."""
    return {
        "name": tag.name,
        "children": [filedb_tree(c) if isinstance(c, filedb.Tag) else _attribute(c) for c in tag.children],
    }


def filedb_json(data: bytes) -> bytes:
    """The JSON of a FileDB document."""
    return _dump(filedb_tree(filedb.read(data)))


def is_filedb(data: bytes) -> bool:
    return filedb.version(data) in (2, 3)  # version 1 has no magic: only a suffix says so


# XML


def _xml_node(el: ET.Element) -> dict:
    out: dict = {"tag": el.tag}
    if el.attrib:
        out["attributes"] = dict(el.attrib)
    if (el.text or "").strip():
        out["text"] = el.text.strip()
    if len(el):
        out["children"] = [_xml_node(c) for c in el]
    return out


def xml_root(data: bytes) -> ET.Element:
    """The root of an XML file; a byte order mark and the encoding line are handled."""
    return ET.fromstring(data.lstrip(b"\xef\xbb\xbf"))


def _flatten(el: ET.Element, prefix: str, row: dict[str, str], depth: int) -> None:
    for k, v in el.attrib.items():
        row.setdefault(f"{prefix}@{k}" if prefix else f"@{k}", v)
    if not len(el):
        if (el.text or "").strip():
            key, n = prefix, 1
            while key in row:
                n += 1
                key = f"{prefix}[{n}]"
            row[key] = el.text.strip()
        return
    if depth >= 6:
        return
    for child in el:
        _flatten(child, f"{prefix}.{child.tag}" if prefix else child.tag, row, depth + 1)


def _table_group(root: ET.Element) -> tuple[list[ET.Element], str]:
    """The largest run of same-named sibling elements (two or more), shallowest first, and where it is."""
    best: tuple[int, int, list[ET.Element], str] | None = None
    stack = [(root, root.tag, 0)]
    while stack:
        el, where, depth = stack.pop(0)
        counts: dict[str, list[ET.Element]] = {}
        for c in el:
            counts.setdefault(c.tag, []).append(c)
            stack.append((c, f"{where}/{c.tag}", depth + 1))
        for tag, group in counts.items():
            if len(group) >= 2 and (best is None or (len(group), -depth) > (best[0], -best[1])):
                best = (len(group), depth, group, f"{where}/{tag}")
    return (best[2], best[3]) if best else ([], "")


def xml_rows(root: ET.Element) -> tuple[list[str], list[list[str]], str]:
    """(columns, rows, where) of the XML's biggest table of repeated elements. An ``Asset`` row starts with its GUID,
    template and name (``Values/Standard``)."""
    group, where = _table_group(root)
    rows: list[dict[str, str]] = []
    for el in group[:ROW_LIMIT]:
        row: dict[str, str] = {}
        if el.tag == "Asset":
            std = el.find("Values/Standard")
            for key, value in (
                ("GUID", std.findtext("GUID") if std is not None else None),
                ("Template", el.findtext("Template")),
                ("Name", std.findtext("Name") if std is not None else None),
            ):
                if value is not None:
                    row[key] = value.strip()
        _flatten(el, "", row, 0)
        rows.append(row)
    columns = list(dict.fromkeys(k for r in rows for k in r))
    return columns, [[r.get(c, "") for c in columns] for r in rows], where


def export_data(job: Job) -> Made:
    """JSON (and CSV for XML tables) of a data file; plain text files have no other form than as stored."""
    made, suffix = Made(), Path(job.path).suffix.lower()
    if suffix == ".xml":
        try:
            root = xml_root(job.data)
        except ET.ParseError as exc:
            return Made(notes=[f"not read as XML: {exc}"], status="failed")
        if "json" in job.formats:
            made.files[job.name(".json")] = _dump(_xml_node(root))
        if "csv" in job.formats:
            columns, rows, where = xml_rows(root)
            if rows:
                made.files[job.name(".csv")] = _csv(columns, rows)
                made.notes.append(f"csv: {len(rows)} rows of {where}")
            else:
                made.notes.append("csv: no table of repeated elements in this file")
        return made
    if suffix in FILEDB_SUFFIXES or is_filedb(job.data):
        if "json" in job.formats:
            try:
                made.files[job.name(".json")] = filedb_json(job.data)
                made.notes.append("json: structure and small values; binary attributes by size and first bytes")
            except (ValueError, EOFError, struct.error, IndexError) as exc:
                made.notes.append(f"FileDB not read: {exc}")
                made.status = "failed"
        if "csv" in job.formats:
            made.notes.append("csv: a FileDB document has no table form; use json")
        return made
    made.notes.append("a text file or other data: no converted form, exported as stored")
    return made


# Language texts


def _language_of(path: str) -> str:
    parts = path.lower().split("/")
    return parts[parts.index("loca") + 1] if "loca" in parts and parts.index("loca") + 1 < len(parts) - 1 else ""


def export_text(job: Job) -> Made:
    """A language text file (``GUID=text`` lines) as CSV; with ``options.language`` only that language's files."""
    from ..sources import loca2070

    language = _language_of(job.path)
    if job.options.language and language and language != job.options.language.lower():
        return Made(notes=[f"skipped: language {language}, asked for {job.options.language}"])
    texts = loca2070.parse_guid_text(loca2070.decode_text_file(job.data))
    if not texts:
        return Made(notes=["no GUID=text lines found"], status="failed")
    rows = [[guid, text] for guid, text in sorted(texts.items())]
    return Made(
        {job.name(".csv"): _csv(["guid", "text"], rows)},
        [f"{len(rows)} texts" + (f" ({language})" if language else "")],
    )


# Phoenix UI files


def _plain(value, depth: int = 0):
    """A decoded Phoenix tree as JSON data: bytes by their size and first bytes, schema records left out."""
    if depth > 80:
        return "..."
    if isinstance(value, bytes | bytearray):
        return {"size": len(value), "hex": bytes(value[:HEX_BYTES]).hex()}
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items() if k not in ("layout", "mask")}
    if isinstance(value, list | tuple):
        return [_plain(v, depth + 1) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def export_ui(job: Job) -> Made:
    """The decoded object tree of a Phoenix UI file as JSON (types by name, GUIDs, fields)."""
    from ..formats import phoenix, phoenixread

    if "json" not in job.formats:
        return Made()
    errors = []
    for path in (None, *phoenix.VERSION_SCHEMA_PATHS):
        try:
            schema = phoenix.load_schema(path)
            decoder = phoenixread.load(job.data, schema)
        except (OSError, ValueError, EOFError, KeyError, IndexError, struct.error) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        names = {h: r.get("name", "") for h, r in decoder.s.items()}
        doc = {"file": job.path, "types": names, "root": _plain(decoder.root)}
        return Made({job.name(".json"): _dump(doc, None)}, ["json: the decoded tree; values as the codec reads them"])
    return Made(notes=[f"not decoded: {errors[0] if errors else 'no schema'}"], status="failed")


# Baked particles


def export_effect(job: Job) -> Made:
    """A 117 ``.rdp`` as JSON (header, materials, per particle its frames and first and last keys)."""
    from ..formats import rdp117

    if "json" not in job.formats:
        return Made()
    if Path(job.path).suffix.lower() != ".rdp":
        return Made(notes=["only 117's .rdp has a JSON form; exported as stored"])
    try:
        r = rdp117.parse_rdp(job.data)
    except (KeyError, ValueError, struct.error) as exc:
        return Made(notes=[f"not read as a baked particle file: {exc}"], status="failed")
    particles = []
    at = 0
    for i in range(len(r.starts)):
        ticks = r.ticks_of(i)
        keys = slice(at, at + ticks)
        particles.append(
            {
                "start": r.starts[i],
                "end": r.ends[i],
                "material": r.particle_materials[i],
                "ticks": ticks,
                "position": [r.positions[keys][0], r.positions[keys][-1]] if ticks else [],
                "color": [r.colors[keys][0], r.colors[keys][-1]] if ticks else [],
            }
        )
        at += ticks
    doc = {
        "frame_count": r.frame_count,
        "frame_time": r.frame_time,
        "looped": r.looped,
        "max_scale": r.max_scale,
        "center": r.center,
        "extents": r.extents,
        "texture": r.texture,
        "materials": [vars(m) for m in r.materials],
        "flags": {"self_occlusion": r.self_occlusion, "lighting": r.lighting, "do_sort": r.do_sort},
        "extra": r.extra,
        "particles": particles,
    }
    return Made(
        {job.name(".json"): _dump(doc, None)},
        ["json: quantised values as stored (positions 16-bit, colours bytes: blue, green, red, alpha)"],
    )


# Islands


def export_island(job: Job) -> Made:
    """2070's ``.isd`` and 117's (or 1800's) ``.a7m``: the object list (json, csv) and the height map (png16, exr); an
    ``.a7m`` also its members and each FileDB member as JSON."""
    suffix = Path(job.path).suffix.lower()
    if suffix == ".isd":
        return _isd(job)
    if suffix == ".a7m":
        return _a7m(job)
    if suffix in (".a7t", ".a7te") and "json" in job.formats and is_filedb(job.data):
        return export_data(job)
    return Made(notes=["no converted form for this island file; exported as stored"])


def _isd(job: Job) -> Made:
    from ..formats import isd2070

    made = Made()
    try:
        island = isd2070.Island(job.data)
        objects = island.objects()
    except (AttributeError, ValueError, KeyError, struct.error) as exc:
        return Made(notes=[f"not read as a 2070 island: {exc}"], status="failed")
    header = ["group", "guid", "x", "height", "z", "direction", "variation", "scale", "id"]
    rows = [[o.kind, o.guid, o.x, o.y, o.z, o.direction, o.variation, o.scale, o.id] for o in objects]
    if "csv" in job.formats:
        made.files[job.name("_objects.csv")] = _csv(header, rows)
    if "json" in job.formats:
        doc = {
            "width": island.width,
            "height": island.height,
            "sea_level": island.sea_level,
            "clime": island.clime,
            "difficulty": island.difficulty,
            "objects": [dict(zip(header, r, strict=True)) for r in rows],
        }
        made.files[job.name(".json")] = _dump(doc, None)
    if job.formats & {"png16", "exr"}:
        try:
            grid = island.heightmap()
        except (AttributeError, ValueError, struct.error, ZeroDivisionError) as exc:
            made.notes.append(f"no height map: {exc}")
            return made
        _height_files(job, made, len(grid[0]), len(grid), [v for row in grid for v in row])
    return made


def _height_files(job: Job, made: Made, w: int, h: int, flat: list[float]) -> None:
    """The height map of an island as the files of the forms asked for (``flat``: rows by z, world units)."""
    from ..formats import imagewrite

    low, high = min(flat), max(flat)
    if "exr" in job.formats:
        made.files[job.name("_height.exr")] = imagewrite.exr_bytes(w, h, {"Y": struct.pack(f"<{len(flat)}f", *flat)})
    if "png16" in job.formats:
        span = (high - low) or 1.0
        wide = struct.pack(f"<{len(flat)}H", *(int((v - low) / span * 65535 + 0.5) for v in flat))
        made.files[job.name("_height.png")] = imagewrite.png_bytes(w, h, wide, 1, 16)
        made.notes.append(f"height png: 0 is {low:g}, 65535 is {high:g} (world units); the exr holds the values")
    made.notes.append(f"height map {w} x {h} corners, rows by z")


def _a7m(job: Job) -> Made:
    from ..formats import rda

    made = Made()
    try:
        archive = rda.RdaArchive.from_bytes(job.data)
        members = sorted(archive.entries)
    except (rda.RdaError, ValueError, struct.error, OSError) as exc:
        return Made(notes=[f"not read as an island archive: {exc}"], status="failed")
    if "json" in job.formats:
        listing = []
        for name in members:
            listing.append({"name": name, "size": archive.entries[name].size})
            try:
                body = archive.read(name)
            except (rda.RdaError, ValueError, OSError):
                continue
            if is_filedb(body):
                try:
                    made.files[f"{job.folder()}/{name.replace('\\', '/')}.json"] = filedb_json(body)
                except (ValueError, EOFError, struct.error, IndexError) as exc:
                    made.notes.append(f"{name}: FileDB not read: {exc}")
        made.files[f"{job.folder()}/members.json"] = _dump(listing)
        made.notes.append("json: the members, and each FileDB member as its own JSON")
    _a7m_maps(job, made, archive)
    return made


OBJECT_HEADER = ["group", "guid", "variation", "x", "y", "z", "rotation", "scale", "config"]


def _a7m_maps(job: Job, made: Made, archive) -> None:
    """The height map (png16, exr) and the object list (csv, json) of a 117 or 1800 island, from its members."""
    from ..formats import a7mread, rda

    if job.formats & {"png16", "exr"}:
        try:
            width, flat = a7mread.heights(archive)
            _height_files(job, made, width, width, flat)
        except (a7mread.IslandError, rda.RdaError, ValueError, struct.error, OSError) as exc:
            made.notes.append(f"no height map: {exc}")
    if job.formats & {"csv", "json"}:
        try:
            placed = a7mread.objects(archive)
        except (a7mread.IslandError, rda.RdaError, ValueError, EOFError, struct.error, IndexError, OSError) as exc:
            made.notes.append(f"no object list: {exc}")
            return
        rows = [[o.group, o.guid, o.variation, *o.position, list(o.rotation), o.scale, o.config] for o in placed]
        if "csv" in job.formats:
            made.files[job.name("_objects.csv")] = _csv(
                OBJECT_HEADER, [[*r[:6], " ".join(f"{v:g}" for v in r[6]), r[7], r[8]] for r in rows]
            )
        if "json" in job.formats:
            made.files[job.name("_objects.json")] = _dump(
                {"objects": [dict(zip(OBJECT_HEADER, r, strict=True)) for r in rows]}, None
            )
        made.notes.append(f"{len(rows)} objects: positions in world units, rotation as a quaternion x y z w")
