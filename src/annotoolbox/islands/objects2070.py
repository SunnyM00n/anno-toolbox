"""2070's island objects: their asset types and the maths of placing one.

A 2070 island places its objects by asset GUID (``isd2070.Island.objects``: group, GUID, position in tiles with
the height in world units, direction, variation, scale, orientation). The assets (2070
``data/config/game/assets.xml``) list each type's model variations as ``.cfg`` files. 2070's direction is the
turn in degrees x 4096 and its quaternions (which also tilt rocks and cliffs) turn the same way as 117's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

ASSETS_2070 = "data/config/game/assets.xml"
ASSETS_ADDON = "addondata/config/balancing/addon_01_assets.xml"  # the add-on's own: its third-party buildings
HERD_TEMPLATES = {"AnimalHerd", "HerdMember", "SimpleAIUnit"}  # 2070's deer, elk and bear
TREE_TEMPLATE = "Tree"  # 2070's and 117's template for a tree
# ``MinePathObject`` is the access a 2070 mine builds from: its mines are two parts, the shaft on the slot in the
# rocks and the works on the plateau, joined by a stone ramp or a station (the hydroelectric dam has its wall the
# same way)
DECOR_TEMPLATES = {"NatureMountainObject", "SimpleStoneObject", "Vegetation", "MinePathObject"}
# 2070 also dresses its islands with faction props (chimneys and oil tanks around an oil baron site, depots and
# lanterns at an eco one, harbour walkways): 603 of them over the 133 normal islands. The same templates hold the
# editor's invisible helpers - placement checks, grass blockers, docking points - so only the asset groups whose
# name ends in "props" are placed.
DECOR_PROP_TEMPLATES = {"SimpleBlocking", "SimpleSelectable"}
RELIEF_TEMPLATES = {"NatureMountainObject"}  # cliff bases, plateaus, mountains: 2070's relief
UNDERWATER_DECOR = "/underwater"  # 2070's model folders under the sea: placed as props
# The buildings mod's kinds (factories, farms and fields, residences, public buildings, warehouses, ornaments): their
# models are its files, with its flags and feedback actors, so where an island places one (Ebashi's algae farm) the
# islands write their own copy beside it (``building_models``; 2070-mode "One mod")
BUILDING_MODEL_TEMPLATES = {
    *("FactoryBuilding", "FarmBuilding", "FarmfieldObject", "SimpleProductionBuilding"),
    *("LinkedObjectProductionBuilding", "ResidenceBuilding", "PublicBuilding", "OrnamentBuilding"),
    *("OrnamentFeedbackBuilding", "Warehouse", "WarehouseWithGuns", "Markethouse", "WarehouseWithoutTrading"),
}
# The buildings that stand ready on the campaign, scenario and world-event islands (specialislands), drawn as models:
# the player's and the factions' settlements, the Ark, the harbour warehouses, the ruins and monuments. Quay pieces
# (``Harbour``), fields' rows, video screens, units and ships are left out. Their models get the island's own copy
# like the buildings mod's kinds, so the two parts never write one file twice.
PREBUILT_TEMPLATES = {
    *("NeutralEcoBalanceBuilding", "SimpleBuilding", "SimpleFeedbackBuilding", "SimpleLinkedObjectBlocking"),
    *("Ark", "AcademyBuilding", "PropagandaBuilding", "MobilePropagandaBuilding", "SupportBuilding"),
    *("RepairHarbourBuilding", "TaskBasedProductionBuilding", "AirportBuilding", "BridgeBuilding"),
    *("SpecialActionBuilding", "MobileMilitaryTurret", "Flotsam"),
    *("Monument", "FarmfieldLinkedObject", "ExplorableObject", "ThirdPartyBuilding"),
    *("Harbour", "SimpleObject", "SimpleVideoObject"),  # quay tiles, loose props and street decals, media screens
}
# The people, vehicles and ships standing on a pre-built island: drawn as models in their pose, not walking
# (``Shared.context`` keeps their animations out); their cfgs are the islands' own copies like the buildings'.
UNIT_TEMPLATES = {
    *("SimpleUnit", "SimpleAIUnit", "Resident", "SimpleTransporter", "TradingShip", "WarShip", "FlagShip"),
    *("FlyingWarShip", "SubmarineWarship", "Submarine", "SimpleFlyingUnit", "SimpleFlyingFeedbackUnit"),
    *("BlackMarketShip", "CargoLifter"),
}
STREET_FX = "/effects/street/"  # the cracks, manholes and crossings 2070 lays on its streets
NOT_PLACED_NAMES = ("dummy_",)  # the developers' test objects: their models are not in the install
OWN_COPY_SUFFIX = "_island"
BUILDINGS_FOLDER = "data/graphics/buildings/"
LANDSCAPE_FOLDER = "/graphics/landscape/"
# the slots convert a building's model with flags of their own (and a sea slot's lift): a copy of their own beside the
# decoration's, or the campaign island's pre-built hydro plant and a dam slot write one dam file two ways
SLOT_COPY_SUFFIX = "_islandslot"


def own_suffix(kind: str) -> str:
    """The suffix of the island's own copies that a model context of that kind writes."""
    return SLOT_COPY_SUFFIX if kind == "slots" else OWN_COPY_SUFFIX


@dataclass
class ObjectType:
    guid: int
    template: str
    name: str
    variations: list[str] = field(default_factory=list)
    credits: int = 0  # BuildCost/ResourceCost/Credits; 2070's Tree template and tree assets name none


def read_types(tree) -> dict[int, ObjectType]:
    """2070 asset GUID -> template, name, model variations (2070 cfg paths) and build credits, the add-on's assets
    included."""
    text = tree.read_bytes(ASSETS_2070).decode("utf-8", "ignore")
    if tree.exists(ASSETS_ADDON):
        text += tree.read_bytes(ASSETS_ADDON).decode("utf-8", "ignore")
    out: dict[int, ObjectType] = {}
    for block in re.findall(r"<Asset>((?:(?!<Asset>).)*?)</Asset>", text, re.S):
        g = re.search(r"<GUID>(\d+)</GUID>", block)
        t = re.search(r"<Template>([^<]*)</Template>", block)
        if not g or not t:
            continue
        name = (re.search(r"<Name>([^<]*)</Name>", block) or [None, ""])[1]
        files = [f.replace("\\", "/").lower() for f in re.findall(r"<Filename>([^<]*)</Filename>", block)]
        cost = re.search(r"<BuildCost>(?:(?!</BuildCost>).)*?<Credits>(\d+)</Credits>", block, re.S)
        out[int(g.group(1))] = ObjectType(int(g.group(1)), t.group(1), name, files, int(cost.group(1)) if cost else 0)
    return out


def type_named(types: dict[int, ObjectType], name: str) -> ObjectType | None:
    """The one 2070 asset of that name (``lightsource``, ``waterfall_small``), None without it."""
    return next((t for t in types.values() if t.name == name), None)


def building_models(types: dict[int, ObjectType]) -> frozenset[str]:
    """The 2070 cfgs of the buildings mod's kinds and of the pre-built ones (``BUILDING_MODEL_TEMPLATES``,
    ``PREBUILT_TEMPLATES``), and every model from the buildings' folders an island places (a lantern among the
    buildings' props, which a building also nests with its own flags), as lower-case keys."""
    kinds = BUILDING_MODEL_TEMPLATES | PREBUILT_TEMPLATES | UNIT_TEMPLATES
    return frozenset(
        v
        for t in types.values()
        for v in t.variations
        if (t.template in kinds and LANDSCAPE_FOLDER not in v) or BUILDINGS_FOLDER in v  # bushes stay the shared file
    )


def is_decor(t: ObjectType) -> bool:
    return bool(t.variations) and (
        t.template in DECOR_TEMPLATES or (t.template in DECOR_PROP_TEMPLATES and t.name.lower().endswith("props"))
    )


def pick_variation(count: int, v: int | None, clamp: bool = False) -> int:
    """The index of 2070 variation ``v`` among ``count`` models: a variation out of range is the first one, or
    with ``clamp`` the nearest."""
    if v is None:
        return 0
    if clamp:
        return max(0, min(v, count - 1))
    return v if 0 <= v < count else 0


EFFECT_ONLY = re.compile(r"<ConfigType>(?:PARTICLE|LIGHT|DECAL)</ConfigType>")


# Placement maths
def placed_quat(o) -> tuple[float, float, float, float]:
    """The orientation (x, y, z, w) an island object is placed with: 2070's own quaternion, which may tilt it,
    else a turn by -direction about +Y."""
    yaw = math.radians(o.direction / 4096)
    return o.orientation or (0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2))


def rotate(points, quat):
    """``points`` (n x 3) turned by the unit quaternion ``quat`` (x, y, z, w)."""
    import numpy as np

    q, w = np.asarray(quat[:3], np.float64), float(quat[3])
    t = 2.0 * np.cross(q, points)
    return points + w * t + np.cross(q, t)


def prop_turn(direction: int, offset: float = 0.0) -> float:
    """The prop quaternion angle (``propgrid.append_props``) of an object 2070 turned by ``direction``
    (degrees x 4096): 2070's own quaternion turns by minus its direction."""
    return math.radians(offset - direction / 4096)


def object_turn(o, offset: float = 0.0) -> float:
    """The prop quaternion angle of a placed 2070 object: the heading 2070 draws it with (its quaternion, which on
    some waterfall pieces differs from ``m_Direction`` by over 20 degrees), else its direction."""
    if o.orientation is None:
        return prop_turn(o.direction, offset)
    _, qy, _, qw = o.orientation
    return 2 * math.atan2(qy, qw) + math.radians(offset)
