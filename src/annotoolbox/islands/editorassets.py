"""117's editor-only assets: objects Ubisoft's own island editor places and the game never offers -
position markers, audio spots, herds, fish flocks, sequence dummies, build and map-exit blockers,
feedback-grid modifiers (templates saying ``OnlyVisibleInEditor``, assets carrying ``PlacableInEditor``
or ``AddToEditorObjectGrid``).

Each kind is a point object (guid, ID, position, orientation, a block of its own such as ``Herd``), so
one is placed by cloning an instance from a vanilla island and giving it the chosen asset and place;
the water splines and the kinds on a Bezier path are left out (island-editor-view "The model library").
"""

from __future__ import annotations

import copy
import math
import re
import struct

from ..formats import a7mread, filedb
from . import islandedits
from .islandwater import next_serial

# point kinds found in the vanilla islands, and the object group each lives in there
POINT_TEMPLATES = (
    "PositionMarker",
    "AudioSpots",
    "Herd",
    "Flock",
    "DummySequenceAsset",
    "StaticBlockingObject",
    "FeedbackGridModifier",
    "MapExitBlocker",
)
GROUPS = ("GameObject", "EditorObject")
# one match at a time (``finditer``): a ``findall`` over the game's 60 MB asset file held the interpreter lock for
# seconds, and the island window's thread waited for it
ASSET_BLOCK = re.compile(r"<Asset>((?:(?!<Asset>).)*?)</Asset>", re.S)
EDITOR_FLAGS = re.compile(r"<(OnlyVisibleInEditor|PlacableInEditor|AddToEditorObjectGrid)>1<")


def catalogue(assets_text: str, templates_text: str) -> list[dict]:
    """Every editor-only asset of a point kind: ``{"guid", "name", "template"}``."""
    editor_templates = {
        m.group(1)
        for m in re.finditer(r"<Name>([^<]+)</Name>(?:(?!<Name>).)*?<OnlyVisibleInEditor>1<", templates_text, re.S)
    }
    out = []
    for found in ASSET_BLOCK.finditer(assets_text):
        block = found.group(1)
        g = re.search(r"<GUID>(\d+)</GUID>", block)
        t = re.search(r"<Template>([^<]*)</Template>", block)
        if not g or not t or t.group(1) not in POINT_TEMPLATES:
            continue
        if t.group(1) in editor_templates or EDITOR_FLAGS.search(block):
            name = (re.search(r"<Name>([^<]*)</Name>", block) or [None, g.group(1)])[1]
            out.append({"guid": int(g.group(1)), "name": name, "template": t.group(1)})
    return sorted(out, key=lambda e: (e["template"], e["name"].lower()))


def load(files) -> tuple[list[dict], dict[int, str]]:
    """The catalogue and the GUID -> template map, read from the game (``files.read``)."""
    from ..sources.anno117 import ASSETS_XML_PATH, TEMPLATES_XML_PATH

    assets = files.read(ASSETS_XML_PATH).decode("utf-8", "ignore")
    templates = files.read(TEMPLATES_XML_PATH).decode("utf-8", "ignore")
    return catalogue(assets, templates), template_map(assets)


def _objects_of(gamedata: bytes):
    """(group, object) for every object with a guid in an island's areas."""
    session = filedb.read(gamedata).path("GameSessionManager")
    for group, o in a7mread.object_tags(session, GROUPS) if session is not None else ():
        if o.find("guid") is not None and o.find("Position") is not None:
            yield group, o


def samples(files, template_of: dict[int, str]) -> dict[str, tuple[str, filedb.Tag]]:
    """One vanilla instance per point kind: template -> (object group, object). ``files`` reads the
    game (``iter_paths``/``read``); ``template_of``: asset GUID -> template."""
    found: dict[str, tuple[str, filedb.Tag]] = {}
    for path in sorted(p for p in files.iter_paths("data/") if p.endswith(".a7m")):
        data = files.read(path)
        try:
            (gamedata,) = a7mread.members(data, "gamedata.data")
        except Exception:
            continue
        for group, o in _objects_of(gamedata):
            t = template_of.get(struct.unpack_from("<I", o.find("guid").data)[0])
            if t in POINT_TEMPLATES and t not in found:
                found[t] = (group, o)
        if len(found) == len(POINT_TEMPLATES):
            break
    return found


def template_map(assets_text: str) -> dict[int, str]:
    """Asset GUID -> template (117 writes the template ahead of the GUID)."""
    out = {}
    for found in ASSET_BLOCK.finditer(assets_text):
        block = found.group(1)
        g = re.search(r"<GUID>(\d+)</GUID>", block)
        t = re.search(r"<Template>([^<]*)</Template>", block)
        if g and t:
            out.setdefault(int(g.group(1)), t.group(1))
    return out


def clone(sample: filedb.Tag, guid: int, serial: int, position, rotation) -> filedb.Tag:
    """A copy of a vanilla instance as asset ``guid`` at ``position`` (Anno's coordinates), turned
    by ``rotation`` (x, y, z, w). Quest links and the source island's folder are dropped."""
    o = copy.deepcopy(sample)
    o.children = [c for c in o.children if c.name not in ("QuestObject", "ObjectFolderID")]
    o.find("guid").data = struct.pack("<I", guid)
    o.find("ID").data = struct.pack("<I", serial) + o.find("ID").data[4:]
    o.find("Position").data = struct.pack("<3f", *map(float, position))
    orient = o.path("Mesh/Orientation")
    if orient is not None:
        orient.data = struct.pack("<4f", *map(float, rotation))
    # vanilla point objects carry the orientation's yaw as Direction (0..2pi, right after Position;
    # absent when unturned), so the sample's own must not survive the turn
    yaw = islandedits._yaw(rotation) % (2 * math.pi)
    direction = o.find("Direction")
    if direction is not None:
        direction.data = struct.pack("<f", yaw)
    elif yaw > 1e-6 and 2 * math.pi - yaw > 1e-6:
        at = [c.name for c in o.children].index("Position") + 1
        o.children.insert(at, filedb.Attrib("Direction", struct.pack("<f", yaw)))
    return o


def place(builder, found: dict[str, tuple[str, filedb.Tag]], entry: dict) -> bool:
    """Add an editor-only asset (an ``added_objects`` entry with ``guid`` and ``template``) to the
    builder's main area. False when no vanilla instance of its kind was found or the area lacks the group."""
    if entry.get("template") not in found:
        return False
    group, sample = found[entry["template"]]
    objs = builder.main_objects(group)
    if objs is None:
        return False
    serial = next_serial(objs, sample)
    rotation = entry.get("rotation", [0, 0, 0, 1])
    objs.children.append(clone(sample, int(entry["guid"]), serial, entry["position"], rotation))
    return True
