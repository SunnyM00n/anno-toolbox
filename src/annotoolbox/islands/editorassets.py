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
        match.group(1)
        for match in re.finditer(r"<Name>([^<]+)</Name>(?:(?!<Name>).)*?<OnlyVisibleInEditor>1<", templates_text, re.S)
    }
    out = []
    for found in ASSET_BLOCK.finditer(assets_text):
        block = found.group(1)
        guid = re.search(r"<GUID>(\d+)</GUID>", block)
        template = re.search(r"<Template>([^<]*)</Template>", block)
        if not guid or not template or template.group(1) not in POINT_TEMPLATES:
            continue
        if template.group(1) in editor_templates or EDITOR_FLAGS.search(block):
            name = (re.search(r"<Name>([^<]*)</Name>", block) or [None, guid.group(1)])[1]
            out.append({"guid": int(guid.group(1)), "name": name, "template": template.group(1)})
    return sorted(out, key=lambda entry: (entry["template"], entry["name"].lower()))


def load(files) -> tuple[list[dict], dict[int, str]]:
    """The catalogue and the GUID -> template map, read from the game (``files.read``)."""
    from ..sources.anno117 import ASSETS_XML_PATH, TEMPLATES_XML_PATH

    assets = files.read(ASSETS_XML_PATH).decode("utf-8", "ignore")
    templates = files.read(TEMPLATES_XML_PATH).decode("utf-8", "ignore")
    return catalogue(assets, templates), template_map(assets)


def _objects_of(gamedata: bytes):
    """(group, object) for every object with a guid in an island's areas."""
    session = filedb.read(gamedata).path("GameSessionManager")
    for group, object_tag in a7mread.object_tags(session, GROUPS) if session is not None else ():
        if object_tag.find("guid") is not None and object_tag.find("Position") is not None:
            yield group, object_tag


def samples(files, template_of: dict[int, str]) -> dict[str, tuple[str, filedb.STag]]:
    """One vanilla instance per point kind: template -> (object group, object). ``files`` reads the
    game (``iter_paths``/``read``); ``template_of``: asset GUID -> template."""
    found: dict[str, tuple[str, filedb.STag]] = {}
    for path in sorted(candidate for candidate in files.iter_paths("data/") if candidate.endswith(".a7m")):
        data = files.read(path)
        try:
            (gamedata,) = a7mread.members(data, "gamedata.data")
        except Exception:
            continue
        for group, object_tag in _objects_of(gamedata):
            template = template_of.get(struct.unpack_from("<I", object_tag.find("guid").data)[0])
            if template in POINT_TEMPLATES and template not in found:
                found[template] = (group, object_tag)
        if len(found) == len(POINT_TEMPLATES):
            break
    return found


def template_map(assets_text: str) -> dict[int, str]:
    """Asset GUID -> template (117 writes the template ahead of the GUID)."""
    out = {}
    for found in ASSET_BLOCK.finditer(assets_text):
        block = found.group(1)
        guid = re.search(r"<GUID>(\d+)</GUID>", block)
        template = re.search(r"<Template>([^<]*)</Template>", block)
        if guid and template:
            out.setdefault(int(guid.group(1)), template.group(1))
    return out


def clone(sample: filedb.STag, guid: int, serial: int, position, rotation) -> filedb.STag:
    """A copy of a vanilla instance as asset ``guid`` at ``position`` (Anno's coordinates), turned
    by ``rotation`` (x, y, z, w). Quest links and the source island's folder are dropped."""
    object_tag = copy.deepcopy(sample)
    object_tag.children = [
        child for child in object_tag.children if child.name not in ("QuestObject", "ObjectFolderID")
    ]
    object_tag.find("guid").data = struct.pack("<I", guid)
    object_tag.find("ID").data = struct.pack("<I", serial) + object_tag.find("ID").data[4:]
    object_tag.find("Position").data = struct.pack("<3f", *map(float, position))
    orient = object_tag.path("Mesh/Orientation")
    if orient is not None:
        orient.data = struct.pack("<4f", *map(float, rotation))
    # vanilla point objects carry the orientation's yaw as Direction (0..2pi, right after Position;
    # absent when unturned), so the sample's own must not survive the turn
    yaw = islandedits._yaw(rotation) % (2 * math.pi)
    direction = object_tag.find("Direction")
    if direction is not None:
        direction.data = struct.pack("<f", yaw)
    elif yaw > 1e-6 and 2 * math.pi - yaw > 1e-6:
        position_index = [child.name for child in object_tag.children].index("Position") + 1
        object_tag.children.insert(position_index, filedb.SAttrib("Direction", struct.pack("<f", yaw)))
    return object_tag


def place(builder, found: dict[str, tuple[str, filedb.STag]], entry: dict) -> bool:
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
