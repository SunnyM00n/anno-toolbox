"""Data files exported as JSON and CSV: FileDB documents, XML (assets, templates, properties), language texts,
Phoenix UI files, baked particles, and island files (the object list, the 2070 height map).

JSON keeps the structure and the small values; a big binary attribute is its size and its first bytes, so a JSON of
a FileDB document is for reading and searching, not a byte-exact dump (the stored file is exported as well).
``export_data`` and the others take a ``SJob`` (``run``). docs/exporting.md.
"""

from __future__ import annotations

import csv
import io
import json
import struct
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

from ..formats import filedb
from .run import SJob, SMade

HEX_BYTES = 48  # a binary value is shown by its first bytes and its size
ROW_LIMIT = 200_000  # CSV rows at most
FILEDB_SUFFIXES = (".ifo", ".fc", ".ctt", ".a7t", ".a7te", ".a7minfo", ".tmc", ".bfg")


def _dump(value, indent: int | None = 1) -> bytes:
    return (json.dumps(value, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def _csv(header: list[str], rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


# FileDB


def _attribute(attribute: filedb.SAttrib) -> dict:
    data = attribute.data
    out: dict = {"name": attribute.name, "size": len(data)}
    if data and all(32 <= byte_value < 127 for byte_value in data):
        out["text"] = data.decode("ascii")
    elif len(data) == 4:
        out["u32"] = struct.unpack("<I", data)[0]
    else:
        out["hex"] = data[:HEX_BYTES].hex()
        if len(data) > HEX_BYTES:
            out["truncated"] = True
    return out


def filedb_tree(tag: filedb.STag) -> dict:
    """A FileDB tag as plain data: its name and children (tags recursively, attributes by ``_attribute``)."""
    return {
        "name": tag.name,
        "children": [
            filedb_tree(child) if isinstance(child, filedb.STag) else _attribute(child) for child in tag.children
        ],
    }


def filedb_json(data: bytes) -> bytes:
    """The JSON of a FileDB document."""
    return _dump(filedb_tree(filedb.read(data)))


def is_filedb(data: bytes) -> bool:
    return filedb.version(data) in (2, 3)  # version 1 has no magic: only a suffix says so


# XML


def _xml_node(element: ET.Element) -> dict:
    out: dict = {"tag": element.tag}
    if element.attrib:
        out["attributes"] = dict(element.attrib)
    if (element.text or "").strip():
        out["text"] = element.text.strip()
    if len(element):
        out["children"] = [_xml_node(child) for child in element]
    return out


def xml_root(data: bytes) -> ET.Element:
    """The root of an XML file; a byte order mark and the encoding line are handled."""
    return ET.fromstring(data.lstrip(b"\xef\xbb\xbf"))


def _is_xml_text(data: bytes) -> bool:
    """Whether a file whose suffix says FileDB is XML text (a FileDB document starts with a magic number, not a tag)."""
    return data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<"


def _flatten(element: ET.Element, prefix: str, row: dict[str, str], depth: int) -> None:
    for k, value in element.attrib.items():
        row.setdefault(f"{prefix}@{k}" if prefix else f"@{k}", value)
    if not len(element):
        if (element.text or "").strip():
            key, count = prefix, 1
            while key in row:
                count += 1
                key = f"{prefix}[{count}]"
            row[key] = element.text.strip()
        return
    if depth >= 6:
        return
    for child in element:
        _flatten(child, f"{prefix}.{child.tag}" if prefix else child.tag, row, depth + 1)


def _table_group(root: ET.Element) -> tuple[list[ET.Element], str]:
    """The largest run of same-named sibling elements (two or more), shallowest first, and where it is."""
    best: tuple[int, int, list[ET.Element], str] | None = None
    queue = deque([(root, root.tag, 0)])
    while queue:
        element, where, depth = queue.popleft()
        counts: dict[str, list[ET.Element]] = {}
        for child in element:
            counts.setdefault(child.tag, []).append(child)
            queue.append((child, f"{where}/{child.tag}", depth + 1))
        for tag, group in counts.items():
            if len(group) >= 2 and (best is None or (len(group), -depth) > (best[0], -best[1])):
                best = (len(group), depth, group, f"{where}/{tag}")
    return (best[2], best[3]) if best else ([], "")


def xml_rows(root: ET.Element) -> tuple[list[str], list[list[str]], str]:
    """(columns, rows, where) of the XML's biggest table of repeated elements. An ``Asset`` row starts with its GUID,
    template and name (``Values/Standard``)."""
    group, where = _table_group(root)
    rows: list[dict[str, str]] = []
    for element in group[:ROW_LIMIT]:
        row: dict[str, str] = {}
        if element.tag == "Asset":
            std = element.find("Values/Standard")
            for key, value in (
                ("GUID", std.findtext("GUID") if std is not None else None),
                ("Template", element.findtext("Template")),
                ("Name", std.findtext("Name") if std is not None else None),
            ):
                if value is not None:
                    row[key] = value.strip()
        _flatten(element, "", row, 0)
        rows.append(row)
    columns = list(dict.fromkeys(k for record in rows for k in record))
    return columns, [[record.get(column, "") for column in columns] for record in rows], where


def export_data(job: SJob) -> SMade:
    """JSON (and CSV for XML tables) of a data file; plain text files have no other form than as stored."""
    made, suffix = SMade(), Path(job.path).suffix.lower()
    if suffix == ".xml" or (suffix in FILEDB_SUFFIXES and _is_xml_text(job.data)):  # 117's .ifo and .fc are text
        try:
            root = xml_root(job.data)
        except ET.ParseError as exc:
            return SMade(notes=[f"not read as XML: {exc}"], status="failed")
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


def export_text(job: SJob) -> SMade:
    """A language text file (``GUID=text`` lines) as CSV; with ``options.language`` only that language's files."""
    from ..sources import loca2070

    language = _language_of(job.path)
    if job.options.language and language and language != job.options.language.lower():
        return SMade(notes=[f"skipped: language {language}, asked for {job.options.language}"])
    texts = loca2070.parse_guid_text(loca2070.decode_text_file(job.data))
    if not texts:
        return SMade(notes=["no GUID=text lines found"], status="failed")
    rows = [[guid, text] for guid, text in sorted(texts.items())]
    return SMade(
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
        return {str(k): _plain(item, depth + 1) for k, item in value.items() if k not in ("layout", "mask")}
    if isinstance(value, list | tuple):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def export_ui(job: SJob) -> SMade:
    """The decoded object tree of a Phoenix UI file as JSON (types by name, GUIDs, fields)."""
    from ..formats import phoenix, phoenixread

    if "json" not in job.formats:
        return SMade()
    errors = []
    for path in (None, *phoenix.VERSION_SCHEMA_PATHS):
        try:
            schema = phoenix.load_schema(path)
            decoder = phoenixread.load(job.data, schema)
        except (OSError, ValueError, EOFError, KeyError, IndexError, struct.error) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        names = {type_id: type_record.get("name", "") for type_id, type_record in decoder.m_schema.items()}
        doc = {"file": job.path, "types": names, "root": _plain(decoder.m_root)}
        return SMade({job.name(".json"): _dump(doc, None)}, ["json: the decoded tree; values as the codec reads them"])
    return SMade(notes=[f"not decoded: {errors[0] if errors else 'no schema'}"], status="failed")


# Baked particles


def export_effect(job: SJob) -> SMade:
    """A 117 ``.rdp`` as JSON (header, materials, per particle its frames and first and last keys)."""
    from ..formats import rdp117

    if "json" not in job.formats:
        return SMade()
    if Path(job.path).suffix.lower() != ".rdp":
        return SMade(notes=["only 117's .rdp has a JSON form; exported as stored"])
    try:
        effect = rdp117.parse_rdp(job.data)
    except (KeyError, ValueError, struct.error) as exc:
        return SMade(notes=[f"not read as a baked particle file: {exc}"], status="failed")
    particles = []
    first_key = 0
    for i in range(len(effect.starts)):
        ticks = effect.ticks_of(i)
        keys = slice(first_key, first_key + ticks)
        particles.append(
            {
                "start": effect.starts[i],
                "end": effect.ends[i],
                "material": effect.particle_materials[i],
                "ticks": ticks,
                "position": [effect.positions[keys][0], effect.positions[keys][-1]] if ticks else [],
                "color": [effect.colors[keys][0], effect.colors[keys][-1]] if ticks else [],
            }
        )
        first_key += ticks
    doc = {
        "frame_count": effect.frame_count,
        "frame_time": effect.frame_time,
        "looped": effect.looped,
        "max_scale": effect.max_scale,
        "center": effect.center,
        "extents": effect.extents,
        "texture": effect.texture,
        "materials": [vars(material) for material in effect.materials],
        "flags": {"self_occlusion": effect.self_occlusion, "lighting": effect.lighting, "do_sort": effect.do_sort},
        "extra": effect.extra,
        "particles": particles,
    }
    return SMade(
        {job.name(".json"): _dump(doc, None)},
        ["json: quantised values as stored (positions 16-bit, colours bytes: blue, green, red, alpha)"],
    )


# Islands


def export_island(job: SJob) -> SMade:
    """2070's ``.isd`` and 117's (or 1800's) ``.a7m``: the object list (json, csv) and the height map (png16, exr); an
    ``.a7m`` also its members and each FileDB member as JSON."""
    suffix = Path(job.path).suffix.lower()
    if suffix == ".isd":
        return _isd(job)
    if suffix == ".a7m":
        return _a7m(job)
    if suffix in (".a7t", ".a7te") and "json" in job.formats and is_filedb(job.data):
        return export_data(job)
    return SMade(notes=["no converted form for this island file; exported as stored"])


def _isd(job: SJob) -> SMade:
    from ..formats import isd2070

    made = SMade()
    try:
        island = isd2070.CIsland(job.data)
        objects = island.objects()
    except (AttributeError, ValueError, KeyError, struct.error) as exc:
        return SMade(notes=[f"not read as a 2070 island: {exc}"], status="failed")
    header = ["group", "guid", "x", "height", "z", "direction", "variation", "scale", "id"]
    rows = [
        [
            placed_object.kind,
            placed_object.guid,
            placed_object.x,
            placed_object.y,
            placed_object.z,
            placed_object.direction,
            placed_object.variation,
            placed_object.scale,
            placed_object.id,
        ]
        for placed_object in objects
    ]
    if "csv" in job.formats:
        made.files[job.name("_objects.csv")] = _csv(header, rows)
    if "json" in job.formats:
        doc = {
            "width": island.m_width,
            "height": island.m_height,
            "sea_level": island.m_sea_level,
            "clime": island.m_clime,
            "difficulty": island.m_difficulty,
            "objects": [dict(zip(header, record, strict=True)) for record in rows],
        }
        made.files[job.name(".json")] = _dump(doc, None)
    if job.formats & {"png16", "exr"}:
        try:
            grid = island.heightmap()
        except (AttributeError, ValueError, struct.error, ZeroDivisionError) as exc:
            made.notes.append(f"no height map: {exc}")
            return made
        _height_files(job, made, len(grid[0]), len(grid), [value for row in grid for value in row])
    return made


def _height_files(job: SJob, made: SMade, width_cells: int, height_cells: int, flat: list[float]) -> None:
    """The height map of an island as the files of the forms asked for (``flat``: rows by z, world units)."""
    from ..formats import imagewrite

    low, high = min(flat), max(flat)
    if "exr" in job.formats:
        made.files[job.name("_height.exr")] = imagewrite.exr_bytes(
            width_cells, height_cells, {"Y": struct.pack(f"<{len(flat)}f", *flat)}
        )
    if "png16" in job.formats:
        span = (high - low) or 1.0
        wide = struct.pack(f"<{len(flat)}H", *(int((value - low) / span * 65535 + 0.5) for value in flat))
        made.files[job.name("_height.png")] = imagewrite.png_bytes(width_cells, height_cells, wide, 1, 16)
        made.notes.append(f"height png: 0 is {low:g}, 65535 is {high:g} (world units); the exr holds the values")
    made.notes.append(f"height map {width_cells} x {height_cells} corners, rows by z")


def _a7m(job: SJob) -> SMade:
    from ..formats import rda

    made = SMade()
    try:
        archive = rda.CRdaArchive.from_bytes(job.data)
        members = sorted(archive.entries)
    except (rda.CRdaError, ValueError, struct.error, OSError) as exc:
        return SMade(notes=[f"not read as an island archive: {exc}"], status="failed")
    if "json" in job.formats:
        listing = []
        for name in members:
            listing.append({"name": name, "size": archive.entries[name].size})
            try:
                body = archive.read(name)
            except (rda.CRdaError, ValueError, OSError):
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


def _a7m_maps(job: SJob, made: SMade, archive) -> None:
    """The height map (png16, exr) and the object list (csv, json) of a 117 or 1800 island, from its members."""
    from ..formats import a7mread, rda

    if job.formats & {"png16", "exr"}:
        try:
            width, flat = a7mread.heights(archive)
            _height_files(job, made, width, width, flat)
        except (a7mread.CIslandError, rda.CRdaError, ValueError, struct.error, OSError) as exc:
            made.notes.append(f"no height map: {exc}")
    if job.formats & {"csv", "json"}:
        try:
            placed = a7mread.objects(archive)
        except (a7mread.CIslandError, rda.CRdaError, ValueError, EOFError, struct.error, IndexError, OSError) as exc:
            made.notes.append(f"no object list: {exc}")
            return
        rows = [
            [
                placed_object.group,
                placed_object.guid,
                placed_object.variation,
                *placed_object.position,
                list(placed_object.rotation),
                placed_object.scale,
                placed_object.config,
            ]
            for placed_object in placed
        ]
        if "csv" in job.formats:
            made.files[job.name("_objects.csv")] = _csv(
                OBJECT_HEADER, [[*row[:6], " ".join(f"{value:g}" for value in row[6]), row[7], row[8]] for row in rows]
            )
        if "json" in job.formats:
            made.files[job.name("_objects.json")] = _dump(
                {"objects": [dict(zip(OBJECT_HEADER, row, strict=True)) for row in rows]}, None
            )
        made.notes.append(f"{len(rows)} objects: positions in world units, rotation as a quaternion x y z w")
