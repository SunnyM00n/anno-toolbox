"""What the UI preview assumes where a scene shows the player's game: the characters (Anno 117's campaign: Marcia,
Lucius, Tarragon, Athr, Concordia, Caeso, Voada, Kandis), the progression, input prompts, DLCs, single or multiplayer, the
region and the values no one decided (docs/ui-editor.md, "What the preview assumes about the player").

``current(ctx)`` is the one place every reader takes them from (the preview's bindings, the game data resolver, the
settings panel): a ``Defaults``. ``Store`` keeps a user's changes per game in the Toolbox settings and per project
(the project's win). Live values and recordings win over all of it (``layout.Placer.own``).
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from ..infra.buildcache import write_atomic

ROLES = ("player", "emperor", "rival", "pirate")


@dataclass(frozen=True)
class Character:
    """A participant as the preview shows it: name, portrait, emblem (its logo), colour and its standing."""

    role: str
    participant: int  # the participant asset's GUID (0: the player's own profile)
    name: str
    portrait: int  # the Portrait asset
    portrait_icon: str  # its IconFilename
    emblem: int  # a PlayerLogo asset (the participant's CompanyIcon, the player's logo)
    emblem_icon: str
    colour: int  # a ParticipantColor asset
    rgb: tuple[int, int, int]  # its ParticipantUIColor without a colour-blind mode
    region: str = "Roman"  # the population group's region (Roman, Celtic, Egyptian)
    relation: str = ""  # the diplomacy state shown with the player
    reputation: int | None = None  # the emperor's reputation of the player
    title: str = ""  # no campaign character carries one; the player may pick a prefix and suffix


# Read from the 117 assets of the 2026-11 build (participants, their Portrait, PlayerLogo and ParticipantColor) and
# the campaign's setup (Difficulty Preset Campaign 41206); the player's values confirmed by a campaign save's slot 0.
PLAYER = Character("player", 41, "Marcia", 38962, "data/ui/fhd/base/icon_content/portraits/portrait_marcia.png",
                   125990, "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_campaign_marcia_128.png",
                   91304, (38, 97, 156))  # fmt: skip
EMPEROR = Character("emperor", 31721, "Emperor Lucius Augustus", 33070,
                    "data/ui/fhd/base/icon_content/portraits/portrait_emperor_lucius.png", 77590,
                    "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_emperor_lucius_128.png", 91314,
                    (114, 47, 55), relation="Peace", reputation=0)  # fmt: skip
RIVALS = (
    Character("rival", 31100, "Tarragon", 31123, "data/ui/fhd/base/icon_content/portraits/portrait_rival_tarragon.png",
              77796, "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_rival_tarragon_128.png", 91304,
              (38, 97, 156), relation="Peace"),
    Character("rival", 31102, "Athr Iorgwyn", 31125, "data/ui/fhd/base/icon_content/portraits/portrait_rival_athr.png",
              77798, "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_rival_athr_128.png", 91310,
              (210, 105, 30), "Celtic", relation="Peace"),
    Character("rival", 31104, "Concordia", 31127, "data/ui/fhd/base/icon_content/portraits/portrait_rival_concordia.png",
              77800, "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_rival_concordia_128.png", 91309,
              (248, 131, 121), relation="Peace"),
)  # fmt: skip
PIRATES = (
    Character("pirate", 37495, "Caeso Regulus Syracus", 38935,
              "data/ui/fhd/base/icon_content/portraits/portrait_pirate_caeso.png", 77591,
              "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_pirate_caeso_128.png", 129650,
              (150, 25, 25), relation="War"),
    Character("pirate", 37494, "Voada Of The Scathach", 38934,
              "data/ui/fhd/base/icon_content/portraits/portrait_pirate_voada.png", 77802,
              "data/ui/fhd/base/icon_content/logos/characters/icon_2d_logo_pirate_voada_128.png", 129808,
              (150, 25, 25), "Celtic", relation="War"),
    Character("pirate", 153069, "Kandis Of Kashta", 153091,
              "data/ui/fhd/base/icon_content/portraits/portrait_dlc03_pirate_kandis.png", 153101,
              "data/ui/fhd/dlc03/icon_content/logo/icon_2d_logo_pirate_kandis_512.png", 158239,
              (150, 25, 25), "Egyptian", relation="War"),
)  # fmt: skip
CAMPAIGN = {"player": (PLAYER,), "emperor": (EMPEROR,), "rival": RIVALS, "pirate": PIRATES}

LATIUM, ALBION, AEGYPTUS = 3245, 6627, 149679  # the provinces (SessionModerate): the campaign starts in Latium
PROVINCE_NAMES = {LATIUM: "Latium", ALBION: "Albion", AEGYPTUS: "Aegyptus"}  # as the game names the provinces
TYPED_NAMES = ("textinput", "nameinput")  # the property names of a name field the player types into
REGION_OF_SESSION = {LATIUM: "Roman", ALBION: "Celtic", AEGYPTUS: "Egyptian"}
PROVINCE = {"Roman": 1589870007, "Celtic": 1563518157, "Egyptian": 1450740567}  # halo.Province (enums-117)
PLATFORM = {"mouse": 1562123227, "xbox": 1477197053, "playstation": 1506049635}  # halo.Platform: PC, Xbox, Playstation
INPUTS = ("mouse", "xbox", "playstation")
# the tiers by region (PopulationGroup7) with resident counts of a late game [I]
TIERS = {"Roman": ((1499, 1500), (1496, 1200), (1497, 800), (1498, 400)),
         "Celtic": ((1500, 1000), (1501, 600), (1502, 300), (1503, 400), (1504, 200)),
         "Egyptian": ((149681, 800), (150510, 500), (150511, 250), (150534, 300), (150542, 150))}  # fmt: skip
RESIDENCE, PRODUCTION = 3145, 3192  # Patrician Residence, Jeweller: an object menu's object [I]
# the campaign's session setup (Difficulty Preset Campaign; the option values as a campaign save holds them, each an
# index into the option's data set) and the player's banner (the save's slot 0)
SETUP = (("SetupConstructionCostRefund", 0), ("SetupRelocateBuildings", 0), ("SetupInactiveCosts", 0),
         ("SetupTaxes", 2), ("SetupMaintenance", 0), ("SetupNegativeCityStatusAttributes", 0),
         ("SetupSettlementCost", 0), ("SetupIncidents", 1), ("SetupIncidentPrevention", 2),
         ("SetupTraderRefillRate", 0), ("SetupContractCooldown", 0), ("SetupMoneyBailout", 1),
         ("SetupMetaInventoryAccess", 0), ("SetupIncludeShipsForAnnihilation", 1), ("SetupStartShips", 0),
         ("SetupStartWithKontor", 1), ("SetupStartCredits", 0), ("SetupStartGoods", 1),
         ("SetupDistributionWorkforce", 0), ("SetupRevealedMap", 1), ("SetupStartSessionIslandSize", 0),
         ("SetupStartSessionIslandDifficulty", 0), ("SetupFertility", 2), ("SetupStartForestSize", 2),
         ("SetupRawMaterial", 2), ("SetupNeedConsumption", 1), ("SetupLossCondition", 0),
         ("SetupVictoryConditionAllOrOne", 2), ("SetupVictoryConditionDiplomacy", 1), ("SetupResearchSpeed", 7),
         ("SetupTraderPassiveTradeStock", 1), ("SetupTraderPassiveTradeBudget", 1), ("SetupTraderTraderPrices", 1))  # fmt: skip
CAMPAIGN_OPTIONS: dict[str, object] = {
    "setup.DifficultyPreset": 41206, "setup.SetupMapTemplateType": 43182, "setup.SetupStartEmperor": 31721,
    "setup.DefaultStartProvince": LATIUM, "setup.GameSeed": 211, **{f"setup.{k}": v for k, v in SETUP},
    "banner.LogoColor": 91307, "banner.BannerShape": 92736, "banner.BannerPattern": 92738,
    "banner.BannerBackgroundPattern": 92740, "banner.BannerStripes": 92742,
}  # fmt: skip
PLAY = "play."  # the options Play mode keeps: ``play.<field's path of names>`` (``Store.set(..., play=True)``)

# components the game fills from the player's state: (component GUID prefix, property id) -> what it reads
REGION_INFO = ("30427a23", 0x78FDE9F4)  # PlayerRegionInfo.CurrentRegion
GAMEPAD = ("a7cc0860", 0x7BAF5E7F)  # Platform.IsGamepadModeActive
PLATFORM_ID = ("a7cc0860", 0x72B3F4CC)  # Platform.Platform
PORTRAIT_GUID, PORTRAIT_IMAGE = ("cd9c2b59", 0x7AF1EF2F), ("cd9c2b59", 0x7552C60E)  # PortraitResolver


@dataclass
class Defaults:
    """Every value the preview assumes; ``CAMPAIGN_DEFAULTS`` is the campaign's, a user's changes go on top."""

    progression: str = "all"  # all, start (the campaign's start), or tier:<PopulationLevel GUID>
    tiers: dict[int, int] = field(default_factory=dict)  # reached tier -> residents (empty: every tier, ``TIERS``)
    locked: list[int] = field(default_factory=list)  # assets still locked (empty under "all")
    input: str = "mouse"
    dlcs: list[int] | None = None  # the owned DLCs; None: every DLC the install has
    session: str = "single"  # single or multi
    region: int = LATIUM
    season: str = ""  # the game's default
    island: str = "large"  # a large island of the player's [I]
    residence: int = RESIDENCE
    production: int = PRODUCTION
    money: int = 250_000  # [I]
    goods: int = 500  # the stock of each good [I]
    player_name: str = ""  # the player's own look; empty: the player character's
    player_portrait: int = 0
    player_emblem: int = 0
    player_colour: int = 0
    characters: dict[str, list[int]] = field(default_factory=dict)  # role -> participants; empty: the campaign's
    language: str = ""  # the game texts' language; empty: the editor's
    research: str = "all"  # all (repeatables once), none, or tier:<n>; ``options`` "research.<GUID>" per node
    options: dict[str, object] = field(default_factory=dict)  # setup.*, banner.*, title.*, research.*: the user's
    # what the game's data adds (``previewcatalogue``): characters by participant, pictures and colours by asset
    known: dict[int, Character] = field(default_factory=dict, repr=False, compare=False)
    pictures: dict[int, str] = field(default_factory=dict, repr=False, compare=False)  # Portrait, PlayerLogo -> icon
    colours: dict[int, tuple[int, int, int]] = field(default_factory=dict, repr=False, compare=False)

    def key(self) -> str:
        """What the kept results depend on (``layout.Context.conditions``); not Play mode's choices, whose readers are
        dropped one by one (``playstate.chosen``)."""
        found = changes(self)
        rest = {k: v for k, v in found.get("options", {}).items() if not k.startswith(PLAY)}
        found.pop("options", None)
        return json.dumps({**found, **({"options": rest} if rest else {})}, sort_keys=True)

    def option(self, name: str, default=None):
        """A named option: the user's, else the campaign's (``CAMPAIGN_OPTIONS``), else ``default``."""
        return self.options.get(name, CAMPAIGN_OPTIONS.get(name, default))

    def researched(self, node: int, tier: int = 0) -> float:
        """A research node's state: 0 not researched, a fraction in progress, 1 or more researched (a repeatable
        node's count); the node's own value wins over the preset."""
        own = self.options.get(f"research.{node}")
        if isinstance(own, (int, float)) and not isinstance(own, bool):
            return float(own)
        if self.research == "none":
            return 0.0
        if self.research.startswith("tier:"):
            return 1.0 if tier <= int(self.research[5:]) else 0.0
        return 1.0  # all researched, a repeatable once

    # what the bindings read

    def cast(self, role: str) -> tuple[Character, ...]:
        """The characters of a role: the chosen ones (looked up in ``known``), else the campaign's."""
        chosen = [self.known.get(g) for g in self.characters.get(role, [])]
        found = tuple(c for c in chosen if c is not None)
        if role == "player":
            return (self.player(),)
        if found:
            return found
        if role != "pirate":
            return CAMPAIGN[role]
        mine = [c for c in CAMPAIGN[role] if c.region == self.region_name()]  # the region's pirate first
        return (*mine, *(c for c in CAMPAIGN[role] if c not in mine))

    def player(self) -> Character:
        """The player with the look the user gave him (name, portrait, emblem, colour) over the chosen character."""
        base = next((self.known[g] for g in self.characters.get("player", []) if g in self.known), None) or PLAYER
        look = {}
        if self.player_portrait and self.player_portrait in self.pictures:
            look.update(portrait=self.player_portrait, portrait_icon=self.pictures[self.player_portrait])
        if self.player_emblem and self.player_emblem in self.pictures:
            look.update(emblem=self.player_emblem, emblem_icon=self.pictures[self.player_emblem])
        if self.player_colour and self.player_colour in self.colours:
            look.update(colour=self.player_colour, rgb=self.colours[self.player_colour])
        return replace(base, role="player", name=self.player_name or base.name, **look)

    def region_name(self) -> str:
        return REGION_OF_SESSION.get(self.region, "Roman")

    def reached(self) -> dict[int, int]:
        """The tiers reached, with their residents: the user's, else what the progression gives."""
        if self.tiers:
            return dict(self.tiers)
        order = [t for region in TIERS.values() for t in region]
        if self.progression == "start":
            return {order[0][0]: 0}
        if self.progression.startswith("tier:"):
            last = int(self.progression[5:])
            region = next((r for r in TIERS.values() if any(g == last for g, _ in r)), ())
            upto = list(region[: [g for g, _ in region].index(last) + 1]) if region else []
            return dict(upto)
        return dict(order)

    def is_locked(self, guid: int | None = None) -> bool:
        """Whether an asset shows as locked (it is in ``locked``); without one, whether a lock of an asset the
        preview does not know shows: only below the late game (all unlocked: never)."""
        if guid is not None:
            return int(guid) in set(self.locked)
        return self.progression != "all"

    def portrait_of(self, guid: int) -> str:
        """The picture of a Portrait asset the cast or ``known`` holds, "" for one the preview does not know."""
        if guid in self.pictures:
            return self.pictures[guid]
        for c in (*self.known.values(), self.player(), *(c for r in ROLES for c in CAMPAIGN[r])):
            if c.portrait == guid:
                return c.portrait_icon
        return ""


CAMPAIGN_DEFAULTS = Defaults()
# a picture slot's name -> (role, what of the character), first match; the game (seen 2026-11-16) drew other
# participants in the bare "Portrait" slots (the governors' bar, diplomacy's "Portrait_Encounter") and the
# player's emblem in the title's "logo"
WORDS_PICTURE = (
    (("emperor", "imperator"), "emperor", "portrait"), (("pirate",), "pirate", "portrait"),
    (("player", "profile", "governor", "avatar", "own"), "player", "portrait"),
    (("logo", "emblem", "crest", "coatofarms", "banner"), "player", "emblem"),
    (("rival", "opponent", "secondparty", "2ndparty", "portrait", "encounter"), "rival", "portrait"),
)  # fmt: skip


def current(ctx) -> Defaults:
    """The defaults the preview of ``ctx`` (a ``layout.Context``, or None) assumes: the one place to read them."""
    found = getattr(ctx, "defaults", None)
    return found if isinstance(found, Defaults) else CAMPAIGN_DEFAULTS


def _words(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def picture_for(slot: str, d: Defaults) -> str | None:
    """The picture a run-time picture slot shows when its name names a character: a portrait or an emblem."""
    low = _words(slot)
    for words, role, what in WORDS_PICTURE:
        if any(w in low for w in words) and "2d" not in low[:2] and "3d" not in low[:2]:  # 2dPortrait: an icon
            who = d.cast(role)[0]
            return who.emblem_icon if what == "emblem" or "logo" in low or "emblem" in low else who.portrait_icon
    return None


def text_for(name: str, d: Defaults) -> str | None:
    """A text whose property's name names a character's name (the player's, the emperor's, a rival's or a
    pirate's); None for any other."""
    low = _words(name)
    if low in TYPED_NAMES:  # what the player types into a name field (the company's, the profile's)
        return d.player().name
    if "name" not in low:
        return None
    for words, role, _ in WORDS_PICTURE:
        if role != "player" and any(w in low for w in words if w not in ("portrait", "encounter")):
            return d.cast(role)[0].name
    if any(w in low for w in ("player", "profile", "company", "governor", "user", "character", "nickname")):
        return d.player().name
    if any(w in low for w in ("province", "session", "region")):
        return PROVINCE_NAMES.get(d.region)
    return None


def bool_for(name: str, d: Defaults) -> bool | None:
    """A flag whose property's name says what it decides: a gamepad, multiplayer, a DLC's store lock (a lock is
    ``placeholders.state_for``'s, through ``is_locked``)."""
    low = _words(name)
    if any(w in low for w in ("gamepad", "controller", "console")):
        return d.input != "mouse"
    if any(w in low for w in ("multiplayer", "coop", "online", "ismp", "mponly")):
        return d.session == "multi"
    if "dlc" in low and any(w in low for w in ("owned", "active", "available")):
        return True
    return None


def number_for(name: str, d: Defaults) -> int | None:
    low = _words(name)
    if low == "playercount":  # the session's players: the player alone, or the player and one more [I]
        return 1 if d.session == "single" else 2
    if any(w in low for w in ("money", "coins", "denarii", "gold", "balance")):
        return d.money
    if any(w in low for w in ("stock", "storage", "goodsamount")):
        return d.goods
    if "reputation" in low:
        return d.cast("emperor")[0].reputation or 0
    return None


def component_values(comps: dict[str, int], d: Defaults) -> dict[tuple[int, int], object]:
    """The values the game gives the components it fills from the player's state, by (instance uid, property id):
    the player's region, the input device, the portrait a resolver turns a GUID into."""
    out: dict[tuple[int, int], object] = {}
    if REGION_INFO[0] in comps:
        out[(comps[REGION_INFO[0]], REGION_INFO[1])] = PROVINCE[d.region_name()]
    if GAMEPAD[0] in comps:
        out[(comps[GAMEPAD[0]], GAMEPAD[1])] = d.input != "mouse"
        out[(comps[PLATFORM_ID[0]], PLATFORM_ID[1])] = PLATFORM.get(d.input, PLATFORM["mouse"])
    return out


def resolve_portrait(values: dict, comps: dict[str, int], d: Defaults) -> None:
    """A PortraitResolver's picture: the portrait of the GUID it holds, else the player's (``values`` in place)."""
    uid = comps.get(PORTRAIT_GUID[0])
    if uid is None:
        return
    guid = values.get((uid, PORTRAIT_GUID[1]))
    icon = d.portrait_of(int(guid)) if isinstance(guid, int) and guid else ""
    values[(uid, PORTRAIT_IMAGE[1])] = icon or d.player().portrait_icon


# what is kept: only what differs from the campaign's defaults


SAVED = tuple(f for f in Defaults.__dataclass_fields__ if f not in ("known", "pictures", "colours"))


def changes(d: Defaults) -> dict:
    """The values of ``d`` that differ from the campaign's, as JSON."""
    base, mine = asdict(CAMPAIGN_DEFAULTS), asdict(replace(d, known={}, pictures={}, colours={}))
    return {k: mine[k] for k in SAVED if mine[k] != base[k]}


def play_choices(d: Defaults) -> dict[str, object]:
    """Play mode's choices: the field's path of names -> the value chosen (the options ``PLAY`` names)."""
    return {k[len(PLAY) :]: v for k, v in d.options.items() if k.startswith(PLAY)}


def with_changes(data: dict, base: Defaults | None = None) -> Defaults:
    """The campaign's defaults with ``data`` (``changes``) on top, keeping what ``base`` knows of the game's data;
    what does not read is left at the default."""
    d = Defaults()
    if base is not None:
        d.known, d.pictures, d.colours = base.known, base.pictures, base.colours
    for k, v in (data or {}).items():
        if k not in SAVED:
            continue
        try:
            if k == "tiers" and isinstance(v, dict):
                v = {int(t): int(n) for t, n in v.items()}
            elif k == "characters" and isinstance(v, dict):
                v = {str(r): [int(g) for g in gs] for r, gs in v.items() if r in ROLES}
            elif k in ("locked", "dlcs") and v is not None:
                v = [int(g) for g in v]
        except (TypeError, ValueError):  # a file edited by hand, a project's from someone else
            continue
        default = getattr(CAMPAIGN_DEFAULTS, k)
        if default is None or isinstance(v, type(default)):
            setattr(d, k, v)
    return d


class Store:
    """The user's changes for one game: in the Toolbox settings (``apps["ui"]["preview"][game]``) and, when the
    project keeps its own, in ``ui-edits/<game>-preview.json`` of the project, which wins. ``set`` and ``reset`` are
    the write API (the panel, a scene's play mode); every change calls the ``listeners`` with the new ``Defaults``."""

    def __init__(self, apps: dict, game: str, project_file: Path | None = None, base: Defaults | None = None) -> None:
        self.apps, self.game, self.project_file = apps, game, project_file
        self.per_project = bool(project_file and project_file.is_file())
        self.listeners: list = []
        self.played: set[str] = set()  # the names a scene's play mode set (``reset(played=True)`` undoes those)
        self.last: frozenset[str] = frozenset()  # the names the last change set (empty: any may have changed)
        self.defaults = self.load(base)

    def play_names(self) -> list[str]:
        """What ``reset(played=True)`` puts back: the names set in play mode, and Play mode's kept choices."""
        return sorted(self.played | {f"options.{k}" for k in self.defaults.options if k.startswith(PLAY)})

    def only_play(self) -> bool:
        """Whether the last change set Play mode's choices only (their readers follow without a new layout)."""
        return bool(self.last) and all(n.startswith(f"options.{PLAY}") for n in self.last)

    def _settings(self) -> dict:
        return self.apps.setdefault("ui", {}).setdefault("preview", {}).setdefault(self.game, {})

    def load(self, base: Defaults | None = None) -> Defaults:
        data = dict(self.apps.get("ui", {}).get("preview", {}).get(self.game, {}))
        if self.per_project:
            try:
                data = json.loads(self.project_file.read_text(encoding="utf-8")).get("values", {})
            except (OSError, ValueError, AttributeError):
                data = {}
        return with_changes(data, base)

    def learn(self, base: Defaults) -> None:
        """What the game's data adds (characters, pictures, colours) to the values kept."""
        self.defaults.known, self.defaults.pictures, self.defaults.colours = base.known, base.pictures, base.colours
        self.last = frozenset()
        self._changed()

    def _apply(self, name: str, value) -> None:
        d = self.defaults
        if name.startswith("options."):
            key = name[len("options.") :]
            if value is None:
                d.options.pop(key, None)
            else:
                d.options[key] = value
        elif name in SAVED:
            setattr(d, name, copy.deepcopy(getattr(CAMPAIGN_DEFAULTS, name)) if value is None else value)
        else:
            raise KeyError(name)

    def set(self, name: str, value, play: bool = False) -> None:
        """One value: a field of ``Defaults`` or ``options.<name>`` (a setup, banner, title or research option);
        None puts it back to the campaign's; ``play``: a choice made inside a scene in play mode."""
        self._apply(name, value)
        (self.played.add if play and value is not None else self.played.discard)(name)
        self.last = frozenset({name})
        self.save()

    def set_many(self, values: dict[str, object]) -> None:
        """Several values as ``set`` takes them, kept and told once (a preset that sets a node each)."""
        for name, value in values.items():
            self._apply(name, value)
            self.played.discard(name)
        self.last = frozenset(values)
        self.save()

    def reset(self, name: str | None = None, played: bool = False) -> None:
        """Back to the campaign's defaults: one value, the play mode's changes (``played``), or every value."""
        if name is None and not played:
            self.defaults = with_changes({}, self.defaults)
            self.played.clear()
            self.last = frozenset()
            self.save()
            return
        if name:
            self.set(name, None)
        elif self.play_names():
            self.set_many(dict.fromkeys(self.play_names()))

    def save(self) -> None:
        """Keep the changes where they are kept now (the project's file, else the settings), then tell."""
        d = self.defaults
        if self.per_project and self.project_file is not None:
            text = json.dumps({"format": 1, "values": changes(d)}, indent=1, sort_keys=True) + "\n"
            write_atomic(self.project_file, text.encode("utf-8"))
        else:
            self._settings().clear()
            self._settings().update(changes(d))
        self._changed()

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener(self.defaults)

    def keep_in_project(self, on: bool) -> None:
        """Start (or stop) keeping the values in the project; stopping removes the project's file."""
        if self.project_file is None:
            return
        self.per_project = on
        if on:
            self.save()
        elif self.project_file.is_file():
            self.project_file.unlink()


def drop_made(samples) -> None:
    """Forget the placeholders made under the old defaults (``viewmodels.Samples``): they are made again."""
    for key in list(samples.generated):
        samples.set(key, None)


def attach(window, game: str) -> Store:
    """Give a UI window (``uiwindow.UiWindow``, a game just opened) its store: the preview reads its defaults, a
    change lays the preview out again with fresh placeholders; 117's assets add the choices off the window's thread
    (``window.preview_catalogue``, ``previewcatalogue``)."""
    from . import previewpanel, projectpart

    project = window.project()
    store = Store(window.settings.apps, game, projectpart.sidecar(project, game, "preview") if project else None)
    window.preview_store, window.preview_catalogue = store, None

    def changed(d: Defaults) -> None:
        if window.ctx is None or getattr(window, "preview_store", None) is not store or store.only_play():
            return  # Play mode's choices: its controller drops what read them (``playmode``)
        window.ctx.defaults = d
        if window.pictures is not None:
            window.pictures.defaults = d
        drop_made(window.samples)
        window._redraw()

    store.listeners.append(changed)
    if getattr(window, "play", None) is not None:
        window.play.store_opened(store)  # Play mode reads and writes its choices through it
    window.ctx.defaults = store.defaults
    if window.pictures is not None:
        window.pictures.defaults = store.defaults
    if game == "anno117":
        window.background.run(lambda: _catalogue(window.settings), lambda cat: _learned(window, store, cat))
    panel = getattr(window, "preview_panel", None)
    if panel is not None:
        previewpanel.open_game(window, store, loading=game == "anno117")  # its choices come with the catalogue
    return store


def _catalogue(settings):
    from ..assetedit import assetgame
    from . import previewcatalogue

    return previewcatalogue.read(assetgame.open_installed(settings, "anno117"), assetgame.interface_language())


def _learned(window, store: Store, cat) -> None:
    """The catalogue read: kept on the window, and what it knows of pictures and colours given to the store."""
    if getattr(window, "preview_store", None) is not store:
        return
    panel = getattr(window, "preview_panel", None)
    if isinstance(cat, Exception):  # no 117 assets (no install folder or workspace set): the campaign's own table stays
        if panel is not None:
            panel.set_catalogue(None)
        return
    window.preview_catalogue = cat
    store.learn(cat.defaults(Defaults()))
    if panel is not None:
        panel.set_catalogue(cat)
