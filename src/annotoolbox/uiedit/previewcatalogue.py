"""The choices each preview default can take, read from Anno 117's assets: the characters by role, the player's
customization (portraits, emblems, colours, banner parts, title words), the regions, tiers, DLCs, the assets locked at
the start, the research nodes and the session setup's options with their values.

``read(game)`` takes an opened ``assetedit.assetgame.Game`` (slow: read it off the window's thread) and gives a
``Catalogue``; ``Catalogue.defaults()`` the campaign's ``previewdefaults.Defaults`` knowing every picture and colour.
docs/ui-editor.md, "What the preview assumes about the player".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .previewdefaults import Character, Defaults

ROLE_OF_TEMPLATE = (("Emperor", "emperor"), ("Pirate", "pirate"), ("2ndParty", "rival"), ("Human", "player"),
                    ("3rdParty", "trader"))  # fmt: skip
REGION_OF_GROUP = {"Roman": "Roman", "Celtic": "Celtic", "Egyptian": "Egyptian"}
CUSTOMIZATION = 500769  # ProfileCustomizationScene: what the profile customization offers
DLC_CONTROLLER = 410027
SETUP_TEMPLATE = "DifficultySetup"
DLC_TECH = re.compile(r"Tech (DLC\d+)")
CUT = re.compile("UNUSED| CUT")  # asset names that mark research cut from the game
UNUSED = "unused"  # the group of the nodes in no tree
TECH_ROW = re.compile(r"(-?\d+)\s+(-?\d+)")  # "Tech Economy -1 1 ...": the node's column and row


@dataclass(frozen=True)
class Option:
    """One choice: an asset (or a data set item) with its name, icon and group."""

    guid: int
    name: str
    icon: str = ""
    group: str = ""
    rgb: tuple[int, int, int] | None = None  # a colour
    tier: int = 0  # a research node's row, a population level's place in its region
    repeatable: bool = False
    dlc: int = 0  # a research node's tree belongs to this DLC product (0: a base tree)


@dataclass
class Catalogue:
    characters: dict[str, list[Character]] = field(default_factory=dict)  # role -> participants
    portraits: list[Option] = field(default_factory=list)  # the profile customization's
    emblems: list[Option] = field(default_factory=list)
    colours: list[Option] = field(default_factory=list)
    banner: dict[str, list[Option]] = field(default_factory=dict)  # BannerShape ... -> the parts
    titles: dict[str, list[Option]] = field(default_factory=dict)  # prefix, suffix -> the words
    regions: list[Option] = field(default_factory=list)  # the provinces (sessions) with their region
    tiers: list[Option] = field(default_factory=list)  # the population levels, group = their region
    dlcs: list[Option] = field(default_factory=list)
    unlocks: list[Option] = field(default_factory=list)  # locked at the start, group = their template
    research: list[Option] = field(default_factory=list)  # group = the research category
    setup: dict[str, list[Option]] = field(default_factory=dict)  # Setup... option -> its values (guid: data id)
    presets: list[Option] = field(default_factory=list)  # the difficulty presets

    def defaults(self, base: Defaults | None = None) -> Defaults:
        """``base`` (the campaign's) knowing the catalogue's characters, pictures and colours."""
        d = base or Defaults()
        d.known = {c.participant: c for cs in self.characters.values() for c in cs}
        d.pictures = {o.guid: o.icon for o in (*self.portraits, *self.emblems) if o.icon}
        d.colours = {o.guid: o.rgb for o in self.colours if o.rgb}
        return d


def _text(el, path: str) -> str:
    found = el.find(path) if el is not None else None
    return found.text.strip() if found is not None and found.text else ""


def _int(el, path: str) -> int:
    value = _text(el, path)
    return int(value) if value.lstrip("-").isdigit() else 0


def argb(value: int) -> tuple[int, int, int]:
    """A colour stored as a signed ARGB number as (r, g, b)."""
    u = value & 0xFFFFFFFF
    return (u >> 16) & 255, (u >> 8) & 255, u & 255


class _Reader:
    def __init__(self, game, language: str) -> None:
        self.game, self.index, self.texts = game, game.index, game.texts(language)
        self.english = game.texts("english") if language != "english" else self.texts

    def name(self, guid: int) -> str:
        el = self.index.element(guid) if guid in self.index.by_guid else None
        for path in ("Values/Text/OasisId", "Values/Portrait/PortraitName"):
            key = _text(el, path)
            if key and (found := self.texts.get(key) or self.english.get(key)):
                return found
        entry = self.index.by_guid.get(guid)
        return entry.name if entry is not None else str(guid)

    def option(self, guid: int, group: str = "", **extra) -> Option:
        el = self.index.element(guid) if guid in self.index.by_guid else None
        icon = _text(el, "Values/Standard/IconFilename") or _text(el, "Values/Banner/Filename")  # a banner part's
        return Option(guid, self.name(guid), icon, group, **extra)

    def colour(self, guid: int) -> Option:
        el = self.index.element(guid) if guid in self.index.by_guid else None
        raw = _text(el, "Values/ParticipantColor/ParticipantUIColor/None")
        return Option(guid, self.name(guid), "", "", argb(int(raw)) if raw.lstrip("-").isdigit() else None)

    def of_template(self, template: str) -> list[int]:
        return [g for g, e in self.index.by_guid.items() if getattr(e, "template", "") == template]

    def character(self, guid: int, role: str, region: str) -> Character:
        el = self.index.element(guid)
        portrait, emblem = _int(el, "Values/Participant/Portrait"), _int(el, "Values/Participant/CompanyIcon")
        colour = self.colour(_int(el, "Values/Participant/DefaultColor"))
        return Character(role, guid, self.name(guid), portrait, self.option(portrait).icon if portrait else "",
                         emblem, self.option(emblem).icon if emblem else "", colour.guid, colour.rgb or (255, 255, 255),
                         region)  # fmt: skip


def _characters(r: _Reader) -> dict[str, list[Character]]:
    out: dict[str, list[Character]] = {}
    for guid, entry in sorted(r.index.by_guid.items()):
        template = getattr(entry, "template", "") or ""
        if not template.startswith("Participant") or any(w in template for w in ("Group", "Color", "NoTrader")):
            continue
        role = next((role for word, role in ROLE_OF_TEMPLATE if word in template), "")
        el = r.index.element(guid)
        if not role or not _int(el, "Values/Participant/Portrait"):
            continue  # a template of participants, or one with no face
        region = "Celtic" if any(w in entry.name for w in ("Celtic", "Wetlands", "Albion")) else "Roman"
        region = "Egyptian" if any(w in entry.name for w in ("Aegyptus", "Egypt")) else region
        out.setdefault(role, []).append(r.character(guid, role, region))
    return out


def _customization(r: _Reader, cat: Catalogue) -> None:
    el = r.index.element(CUSTOMIZATION) if CUSTOMIZATION in r.index.by_guid else None
    scene = el.find("Values/ProfileCustomizationScene") if el is not None else None
    if scene is None:
        return
    cat.portraits = [r.option(int(x.text)) for x in scene.iter("Portrait") if x.text and x.text.isdigit()]
    cat.emblems = [r.option(int(x.text)) for x in scene.iter("Logo") if x.text and x.text.isdigit()]
    cat.colours = [r.colour(int(x.text)) for x in scene.iter("Colors") if x.text and x.text.isdigit()]
    for part, tag in (("BannerShape", "Shape"), ("BannerPattern", "Pattern"),
                      ("BannerBackgroundPattern", "BackgroundPattern"), ("BannerStripes", "Stripes")):  # fmt: skip
        cat.banner[part] = [r.option(int(x.text)) for x in scene.iter(tag) if x.text and x.text.isdigit()]
    cat.titles = {"prefix": [r.option(int(x.text)) for x in scene.iter("Prefix") if x.text and x.text.isdigit()],
                  "suffix": [r.option(int(x.text)) for x in scene.iter("Suffix") if x.text and x.text.isdigit()]}  # fmt: skip


def _progression(r: _Reader, cat: Catalogue) -> None:
    for guid in sorted(r.of_template("PopulationGroup7")):
        el = r.index.element(guid)
        region = _text(el, "Values/PopulationGroup7/Regional")
        levels = [int(x.text) for x in el.iter("Level") if x.text and x.text.isdigit()]
        cat.tiers += [r.option(g, region, tier=i + 1) for i, g in enumerate(levels)]
    for guid in sorted(r.of_template("SessionModerate")):
        el = r.index.element(guid)
        if _text(el, "Values/Session/IsVisibleInGameSetup") == "1":
            region = _int(el, "Values/Session/Region")
            cat.regions.append(r.option(guid, r.name(region) if region else ""))
    dlcs = r.index.element(DLC_CONTROLLER) if DLC_CONTROLLER in r.index.by_guid else None
    cat.dlcs = [r.option(int(x.text)) for x in (dlcs.iter("DLC") if dlcs is not None else ()) if x.text.isdigit()]
    for guid, entry in sorted(r.index.by_guid.items()):
        el = r.index.element(guid)
        if _text(el, "Values/Locked/DefaultLockedState") == "1":
            cat.unlocks.append(r.option(guid, getattr(entry, "template", "") or ""))


def _trees(r: _Reader, cat: Catalogue) -> tuple[dict[int, tuple[str, int]], dict[str, tuple[str, int]]]:
    """The research trees: node -> (tree name, DLC of the tree), and the trees by their ``CategoryType`` (the DLC
    ones, ``DLC01``..., are named as the DLC is, and ``Text`` ties a tree to its DLC product)."""
    product = {_text(r.index.element(o.guid), "Values/Text/OasisId"): o.guid for o in cat.dlcs}
    of_node, by_type = {}, {}
    for guid in r.of_template("TechCategory"):
        el = r.index.element(guid)
        tree = (r.name(guid), product.get(_text(el, "Values/Text/OasisId"), 0))
        by_type[_text(el, "Values/TechCategory/CategoryType")] = tree
        for x in el.iter("Tech"):
            if x.text and x.text.isdigit():
                of_node[int(x.text)] = tree
    return of_node, by_type


def _research(r: _Reader, cat: Catalogue) -> None:
    of_node, by_type = _trees(r, cat)
    for guid in sorted(r.of_template("Tech")):
        el = r.index.element(guid)
        if _text(el, "Values/Tech/IsGate") == "1":
            continue  # a gate between the tree's stages, not a node a player researches
        asset = r.index.by_guid[guid].name or ""
        row = TECH_ROW.search(asset)
        repeat = any("repeat" in c.tag.lower() and (c.text or "").strip() not in ("", "0") for c in el.iter())
        name = r.texts.get(_text(el, "Values/Tech/TechName"), "") or r.name(guid)
        kind = DLC_TECH.search(asset)  # a node in no tree list is still named for the DLC it belongs to
        tree = of_node.get(guid) or (by_type.get(kind.group(1)) if kind else None) or ("", 0)
        if guid not in of_node and CUT.search(asset):
            tree = (UNUSED, 0)  # cut from the game: in no tree
        cat.research.append(Option(guid, name, _text(el, "Values/Standard/IconFilename"), tree[0],
                                   tier=abs(int(row.group(2))) if row else 0, repeatable=repeat, dlc=tree[1]))  # fmt: skip


def _setup(r: _Reader, cat: Catalogue) -> None:
    prop = r.game.schema.properties.get(SETUP_TEMPLATE)
    for spec in prop.values if prop is not None else []:
        data_set = r.game.schema.data_sets.get(spec.data_set) if spec.data_set else None
        if data_set is not None and spec.name.startswith("Setup"):
            cat.setup[spec.name] = [Option(int(i) if i.lstrip("-").isdigit() else n, n) for n, i in data_set.items]
    cat.presets = [r.option(g) for g in sorted(r.of_template(SETUP_TEMPLATE))]


def read(game, language: str = "english") -> Catalogue:
    """Every choice the preview's defaults can take, from the game's assets (``assetgame.Game``)."""
    r = _Reader(game, language)
    cat = Catalogue(characters=_characters(r))
    _customization(r, cat)
    _progression(r, cat)
    _research(r, cat)
    _setup(r, cat)
    return cat
