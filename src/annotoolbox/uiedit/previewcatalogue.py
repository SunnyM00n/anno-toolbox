"""The choices each preview default can take, read from Anno 117's assets: the characters by role, the player's
customization (portraits, emblems, colours, banner parts, title words), the regions, tiers, DLCs, the assets locked at
the start, the research nodes and the session setup's options with their values.

``read(game)`` takes an opened ``assetedit.assetgame.CGame`` (slow: read it off the window's thread) and gives a
``SCatalogue``; ``SCatalogue.defaults()`` the campaign's ``previewdefaults.SDefaults`` knowing every picture and colour.
docs/ui-editor.md, "What the preview assumes about the player".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .previewdefaults import SCharacter, SDefaults

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
class SOption:
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
class SCatalogue:
    characters: dict[str, list[SCharacter]] = field(default_factory=dict)  # role -> participants
    portraits: list[SOption] = field(default_factory=list)  # the profile customization's
    emblems: list[SOption] = field(default_factory=list)
    colours: list[SOption] = field(default_factory=list)
    banner: dict[str, list[SOption]] = field(default_factory=dict)  # BannerShape ... -> the parts
    titles: dict[str, list[SOption]] = field(default_factory=dict)  # prefix, suffix -> the words
    regions: list[SOption] = field(default_factory=list)  # the provinces (sessions) with their region
    tiers: list[SOption] = field(default_factory=list)  # the population levels, group = their region
    dlcs: list[SOption] = field(default_factory=list)
    unlocks: list[SOption] = field(default_factory=list)  # locked at the start, group = their template
    research: list[SOption] = field(default_factory=list)  # group = the research category
    setup: dict[str, list[SOption]] = field(default_factory=dict)  # Setup... option -> its values (guid: data id)
    presets: list[SOption] = field(default_factory=list)  # the difficulty presets

    def defaults(self, base: SDefaults | None = None) -> SDefaults:
        """``base`` (the campaign's) knowing the catalogue's characters, pictures and colours."""
        defaults = base or SDefaults()
        defaults.known = {
            character.participant: character
            for character_list in self.characters.values()
            for character in character_list
        }
        defaults.pictures = {option.guid: option.icon for option in (*self.portraits, *self.emblems) if option.icon}
        defaults.colours = {option.guid: option.rgb for option in self.colours if option.rgb}
        return defaults


def _text(element, path: str) -> str:
    found = element.find(path) if element is not None else None
    return found.text.strip() if found is not None and found.text else ""


def _int(element, path: str) -> int:
    value = _text(element, path)
    return int(value) if value.lstrip("-").isdigit() else 0


def argb(value: int) -> tuple[int, int, int]:
    """A colour stored as a signed ARGB number as (r, g, b)."""
    unsigned = value & 0xFFFFFFFF
    return (unsigned >> 16) & 255, (unsigned >> 8) & 255, unsigned & 255


class _CReader:
    def __init__(self, game, language: str) -> None:
        self.m_game, self.m_index, self.m_texts = game, game.m_index, game.texts(language)
        self.m_english = game.texts("english") if language != "english" else self.m_texts

    def name(self, guid: int) -> str:
        element = self.m_index.element(guid) if guid in self.m_index.m_by_guid else None
        for path in ("Values/Text/OasisId", "Values/Portrait/PortraitName"):
            key = _text(element, path)
            if key and (found := self.m_texts.get(key) or self.m_english.get(key)):
                return found
        entry = self.m_index.m_by_guid.get(guid)
        return entry.name if entry is not None else str(guid)

    def option(self, guid: int, group: str = "", **extra) -> SOption:
        element = self.m_index.element(guid) if guid in self.m_index.m_by_guid else None
        icon = _text(element, "Values/Standard/IconFilename") or _text(
            element, "Values/Banner/Filename"
        )  # a banner part's
        return SOption(guid, self.name(guid), icon, group, **extra)

    def colour(self, guid: int) -> SOption:
        element = self.m_index.element(guid) if guid in self.m_index.m_by_guid else None
        raw = _text(element, "Values/ParticipantColor/ParticipantUIColor/None")
        return SOption(guid, self.name(guid), "", "", argb(int(raw)) if raw.lstrip("-").isdigit() else None)

    def of_template(self, template: str) -> list[int]:
        return [
            guid for guid, element in self.m_index.m_by_guid.items() if getattr(element, "template", "") == template
        ]

    def character(self, guid: int, role: str, region: str) -> SCharacter:
        element = self.m_index.element(guid)
        portrait, emblem = _int(element, "Values/Participant/Portrait"), _int(element, "Values/Participant/CompanyIcon")
        colour = self.colour(_int(element, "Values/Participant/DefaultColor"))
        return SCharacter(role, guid, self.name(guid), portrait, self.option(portrait).icon if portrait else "",
                         emblem, self.option(emblem).icon if emblem else "", colour.guid, colour.rgb or (255, 255, 255),
                         region)  # fmt: skip


def _characters(reader: _CReader) -> dict[str, list[SCharacter]]:
    out: dict[str, list[SCharacter]] = {}
    for guid, entry in sorted(reader.m_index.m_by_guid.items()):
        template = getattr(entry, "template", "") or ""
        if not template.startswith("Participant") or any(
            excluded_word in template for excluded_word in ("Group", "Color", "NoTrader")
        ):
            continue
        role = next((role for word, role in ROLE_OF_TEMPLATE if word in template), "")
        element = reader.m_index.element(guid)
        if not role or not _int(element, "Values/Participant/Portrait"):
            continue  # a template of participants, or one with no face
        region = (
            "Celtic"
            if any(excluded_word in entry.name for excluded_word in ("Celtic", "Wetlands", "Albion"))
            else "Roman"
        )
        region = "Egyptian" if any(excluded_word in entry.name for excluded_word in ("Aegyptus", "Egypt")) else region
        out.setdefault(role, []).append(reader.character(guid, role, region))
    return out


def _customization(reader: _CReader, cat: SCatalogue) -> None:
    element = reader.m_index.element(CUSTOMIZATION) if CUSTOMIZATION in reader.m_index.m_by_guid else None
    scene = element.find("Values/ProfileCustomizationScene") if element is not None else None
    if scene is None:
        return
    cat.portraits = [reader.option(int(x.text)) for x in scene.iter("Portrait") if x.text and x.text.isdigit()]
    cat.emblems = [reader.option(int(x.text)) for x in scene.iter("Logo") if x.text and x.text.isdigit()]
    cat.colours = [reader.colour(int(x.text)) for x in scene.iter("Colors") if x.text and x.text.isdigit()]
    for part, tag in (("BannerShape", "Shape"), ("BannerPattern", "Pattern"),
                      ("BannerBackgroundPattern", "BackgroundPattern"), ("BannerStripes", "Stripes")):  # fmt: skip
        cat.banner[part] = [reader.option(int(x.text)) for x in scene.iter(tag) if x.text and x.text.isdigit()]
    cat.titles = {"prefix": [reader.option(int(x.text)) for x in scene.iter("Prefix") if x.text and x.text.isdigit()],
                  "suffix": [reader.option(int(x.text)) for x in scene.iter("Suffix") if x.text and x.text.isdigit()]}  # fmt: skip


def _progression(reader: _CReader, cat: SCatalogue) -> None:
    for guid in sorted(reader.of_template("PopulationGroup7")):
        element = reader.m_index.element(guid)
        region = _text(element, "Values/PopulationGroup7/Regional")
        levels = [int(x.text) for x in element.iter("Level") if x.text and x.text.isdigit()]
        cat.tiers += [reader.option(population_level, region, tier=i + 1) for i, population_level in enumerate(levels)]
    for guid in sorted(reader.of_template("SessionModerate")):
        element = reader.m_index.element(guid)
        if _text(element, "Values/Session/IsVisibleInGameSetup") == "1":
            region = _int(element, "Values/Session/Region")
            cat.regions.append(reader.option(guid, reader.name(region) if region else ""))
    dlcs = reader.m_index.element(DLC_CONTROLLER) if DLC_CONTROLLER in reader.m_index.m_by_guid else None
    cat.dlcs = [
        reader.option(int(x.text))
        for x in (dlcs.iter("DLC") if dlcs is not None else ())
        if x.text and x.text.isdigit()
    ]
    for guid, entry in sorted(reader.m_index.m_by_guid.items()):
        element = reader.m_index.element(guid)
        if _text(element, "Values/Locked/DefaultLockedState") == "1":
            cat.unlocks.append(reader.option(guid, getattr(entry, "template", "") or ""))


def _trees(reader: _CReader, cat: SCatalogue) -> tuple[dict[int, tuple[str, int]], dict[str, tuple[str, int]]]:
    """The research trees: node -> (tree name, DLC of the tree), and the trees by their ``CategoryType`` (the DLC
    ones, ``DLC01``..., are named as the DLC is, and ``Text`` ties a tree to its DLC product)."""
    product = {_text(reader.m_index.element(option.guid), "Values/Text/OasisId"): option.guid for option in cat.dlcs}
    of_node, by_type = {}, {}
    for guid in reader.of_template("TechCategory"):
        element = reader.m_index.element(guid)
        tree = (reader.name(guid), product.get(_text(element, "Values/Text/OasisId"), 0))
        by_type[_text(element, "Values/TechCategory/CategoryType")] = tree
        for x in element.iter("Tech"):
            if x.text and x.text.isdigit():
                of_node[int(x.text)] = tree
    return of_node, by_type


def _research(reader: _CReader, cat: SCatalogue) -> None:
    of_node, by_type = _trees(reader, cat)
    for guid in sorted(reader.of_template("Tech")):
        element = reader.m_index.element(guid)
        if _text(element, "Values/Tech/IsGate") == "1":
            continue  # a gate between the tree's stages, not a node a player researches
        asset = reader.m_index.m_by_guid[guid].name or ""
        row = TECH_ROW.search(asset)
        repeat = any(
            "repeat" in child.tag.lower() and (child.text or "").strip() not in ("", "0") for child in element.iter()
        )
        name = reader.m_texts.get(_text(element, "Values/Tech/TechName"), "") or reader.name(guid)
        kind = DLC_TECH.search(asset)  # a node in no tree list is still named for the DLC it belongs to
        tree = of_node.get(guid) or (by_type.get(kind.group(1)) if kind else None) or ("", 0)
        if guid not in of_node and CUT.search(asset):
            tree = (UNUSED, 0)  # cut from the game: in no tree
        cat.research.append(SOption(guid, name, _text(element, "Values/Standard/IconFilename"), tree[0],
                                   tier=abs(int(row.group(2))) if row else 0, repeatable=repeat, dlc=tree[1]))  # fmt: skip


def _setup(reader: _CReader, cat: SCatalogue) -> None:
    prop = reader.m_game.m_schema.properties.get(SETUP_TEMPLATE)
    for spec in prop.values if prop is not None else []:
        data_set = reader.m_game.m_schema.data_sets.get(spec.data_set) if spec.data_set else None
        if data_set is not None and spec.name.startswith("Setup"):
            cat.setup[spec.name] = [
                SOption(int(i) if i.lstrip("-").isdigit() else name, name) for name, i in data_set.items
            ]
    cat.presets = [reader.option(guid) for guid in sorted(reader.of_template(SETUP_TEMPLATE))]


def read(game, language: str = "english") -> SCatalogue:
    """Every choice the preview's defaults can take, from the game's assets (``assetgame.CGame``)."""
    reader = _CReader(game, language)
    cat = SCatalogue(characters=_characters(reader))
    _customization(reader, cat)
    _progression(reader, cat)
    _research(reader, cat)
    _setup(reader, cat)
    return cat
