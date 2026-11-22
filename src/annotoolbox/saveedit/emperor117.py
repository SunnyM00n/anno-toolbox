"""117's emperor in a save: who reigns and each emperor's reputation book, read from ``data.a7s`` and changed in a
copy (docs/save-emperor.md). Start at ``read`` (the ``SState``); ``set_reputation`` and ``set_emperor`` return the
changed document after that page's checks, against ``SRules`` (what the game's emperor and zone assets say).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import filedb

ZONES = ("HostileZone", "UnrulyZone", "CasualZone", "EffortZone", "ChallengeZone")  # ReputationZones, ids 0 to 4
CASUAL = 2  # the zone a record leaves out: a new record's (save-emperor "The game's rules")
SPECIAL_STATES = {
    1: "Rebellion",
    2: "ProConsul",
    3: "Consul",
    4: "RebellionPending",
    5: "ConsulPending",
    6: "ProConsulPending",
    7: "EmperorChangePending",
}  # ReputationSpecialState data ids (formats/enums-117.md)
KEPT = frozenset({"LastRebellionEndTime"})  # the emperor's own state that holds a value with nothing under way
OBJECTS = "MetaGameManager/MetaGameObjectManager/MetaObjectMap"
ACTIVE = "MetaGameManager/ParticipantManager/ActiveEmperor"
CAMPAIGN = "MetaGameManager/QuestManager/CampaignHandler"
CHAPTERS = {
    0: "Act 1, chapter 1",
    8: "Act 1, chapter 2",
    9: "Act 1, chapter 3",
    1: "Act 2, chapter 4",
    10: "Act 2, chapter 5",
    11: "Act 2, chapter 6",
    2: "Act 3, chapter 7",
    12: "Act 3, chapter 8",
    13: "Act 3, chapter 9",
    7: "completed",
}  # QuestCampaignChapter data ids (formats/enums-117.md)
COMPLETED = 7  # CampaignCompleted: the game goes on as free play (save-emperor "The campaign")


class CEmperorError(ValueError):
    pass


@dataclass(frozen=True)
class SStanding:
    """One record of an emperor's book: a participant's reputation, zone and special state."""

    participant: int
    reputation: int | None  # None: left out (0)
    zone: int
    special: int | None  # None: no special state
    request: bool  # one of the emperor's requests is under way


@dataclass(frozen=True)
class SEmperor:
    guid: int
    book: tuple[SStanding, ...]
    busy: tuple[str, ...]  # the parts of his own state that hold something (a decision, a rebellion, ...)
    deactivated: bool


@dataclass(frozen=True)
class SCampaign:
    """A campaign's state: its chapter (``CHAPTERS``; None when left out), the story components under way, the
    emperors who died in it."""

    chapter: int | None
    running: int
    dead: frozenset[int]

    @property
    def completed(self) -> bool:
        return self.chapter == COMPLETED and not self.running

    def stage(self) -> str:
        return CHAPTERS.get(self.chapter, f"chapter {self.chapter}") if self.chapter is not None else CHAPTERS[0]


@dataclass(frozen=True)
class SState:
    active: int | None  # None: no emperor reigns
    emperors: dict[int, SEmperor]
    campaign: SCampaign | None  # None: not a campaign

    def standing(self, participant: int) -> SStanding | None:
        """The participant's record in the book of the emperor who reigns."""
        emperor = self.emperors.get(self.active) if self.active is not None else None
        return (
            next((standing for standing in emperor.book if standing.participant == participant), None)
            if emperor
            else None
        )


@dataclass(frozen=True)
class SZoneEffects:
    """What a zone of an emperor starts and unlocks (``SEmperor/ZoneConfiguration``), in the asset's order."""

    effects: tuple[int, ...] = ()
    unlocks: tuple[int, ...] = ()


@dataclass(frozen=True)
class SRules:
    """The game's assets: per emperor his zone configuration (comparable text, and per zone its ``SZoneEffects`` in
    ``zones``), per zone (``ZONES`` order) its lower, lower inner, upper inner and upper bound (``ReputationFeature``),
    and the ``EffectScope`` of each effect the zones start (``scopes``)."""

    emperors: dict[int, str]
    bounds: tuple[tuple[int, int, int, int], ...]
    zones: dict[int, tuple[SZoneEffects, ...]] = field(default_factory=dict)
    scopes: dict[int, str] = field(default_factory=dict)

    @property
    def lowest(self) -> int:
        return self.bounds[0][0]

    @property
    def highest(self) -> int:
        return self.bounds[-1][3]

    def zone_for(self, current: int, reputation: int) -> int:
        """The zone the game gives ``reputation`` from ``current`` (save-emperor "The game's rules")."""
        for zone, (low, low_inner, up_inner, upper) in enumerate(self.bounds):
            if zone + 1 == current:  # coming from above
                fits = low <= reputation <= up_inner
            elif zone - 1 == current:  # coming from below
                fits = low_inner <= reputation <= upper
            else:
                fits = low <= reputation <= upper
            if fits:
                return zone
        return current

    def kept(self, zone: int) -> tuple[int, int]:
        """The lowest and highest reputation that keep a record in ``zone``."""
        values = [
            reputation for reputation in range(self.lowest, self.highest + 1) if self.zone_for(zone, reputation) == zone
        ]
        return (values[0], values[-1]) if values else (0, -1)


def padded(value: int) -> int:
    """A value's size in the document: attributes are padded to 8 bytes."""
    return (value + 7) // 8 * 8


def close_of(tag: filedb.CSpan) -> int:
    """Where the tag's closing node starts."""
    if not tag.m_children:
        return tag.m_header + 8
    last = tag.m_children[-1]
    return close_of(last) + 8 if last.m_is_tag else last.m_start + padded(last.m_size_bytes)


def _section(node: filedb.CSpan) -> str:
    """The name of the part of ``MetaGameManager`` a node lies in, "" outside it."""
    while (
        node.m_parent is not None and node.m_parent.m_parent is not None and node.m_parent.m_parent.m_parent is not None
    ):
        node = node.m_parent
    return node.m_name if node.m_parent is not None and node.m_parent.m_name == "MetaGameManager" else ""


def scan(document: bytes) -> filedb.CSpan:
    """The tree up to the end of the quest manager: the participants, the emperor who reigns, the campaign."""
    quests = []

    def done(span: filedb.CSpan) -> bool:
        if _section(span) == "QuestManager":
            quests.append(True)
            return False
        return bool(quests)

    try:
        return filedb.scan(document, stop=done)
    except ValueError as error:
        raise CEmperorError(f"data.a7s: {error}") from None


def number(span: filedb.CSpan | None, document: bytes, struct_format: str) -> int | None:
    """An attribute's value by its ``struct`` format, None when it is missing or of another size."""
    if span is None or span.m_is_tag or span.m_size_bytes != struct.calcsize(struct_format):
        return None
    return struct.unpack(struct_format, span.value(document))[0]


def enum(tag: filedb.CSpan | None, document: bytes) -> int | None:
    """An enum tag's ``id`` (u16), None when it holds none."""
    return None if tag is None else number(tag.find("id"), document, "<H")


def _holds(node: filedb.CSpan) -> bool:
    return bool(node.m_children) if node.m_is_tag else node.m_size_bytes > 0


def _standing(key: filedb.CSpan, record: filedb.CSpan, document: bytes) -> SStanding:
    participant = number(key, document, "<I")
    if participant is None or not record.m_is_tag:
        raise CEmperorError("a reputation book of this save is not laid out as known")
    quest = record.find("ActiveEmperorQuest")
    zone = enum(record.find("ActiveZone"), document)
    return SStanding(
        participant,
        number(record.find("CurrentReputation"), document, "<i"),
        CASUAL if zone is None else zone,
        enum(record.find("SpecialState"), document),
        quest is not None and _holds(quest),
    )


def _emperor(entry: filedb.CSpan, document: bytes) -> SEmperor:
    meta = entry.find("MetaEmperor")
    book = meta.find("ReputationMap")
    if book is None or len(book.m_children) % 2:
        raise CEmperorError("a reputation book of this save is not laid out as known")
    pairs = zip(book.m_children[::2], book.m_children[1::2], strict=True)
    busy = tuple(
        child.m_name
        for child in meta.m_children
        if child.m_name != "ReputationMap" and child.m_name not in KEPT and _holds(child)
    )
    deactivated = entry.find("Deactivated")
    return SEmperor(
        number(entry.find("guid"), document, "<I"),
        tuple(_standing(k, record, document) for k, record in pairs),
        busy,
        deactivated is not None and _holds(deactivated) and any(deactivated.value(document)),
    )


def _entries(root: filedb.CSpan) -> list[filedb.CSpan]:
    objects = root.path(OBJECTS)
    if objects is None:
        raise CEmperorError("the save has no MetaObjectMap")
    return [element for element in objects.m_children if element.m_is_tag and element.find("MetaEmperor") is not None]


def state_of(root: filedb.CSpan, document: bytes) -> SState:
    """``read`` on a tree ``scan`` made."""
    emperors = {}
    for entry in _entries(root):
        found = _emperor(entry, document)
        emperors[found.guid] = found
    return SState(number(root.path(ACTIVE), document, "<I"), emperors, _campaign(root.path(CAMPAIGN), document))


def _campaign(handler: filedb.CSpan | None, document: bytes) -> SCampaign | None:
    """The campaign of the save; None when its handler holds nothing (not a campaign)."""
    if handler is None or not any(_holds(child) for child in handler.m_children):
        return None
    running = handler.find("ActiveComponentGUIDs")
    dead = handler.find("DeadEmperors")
    keys = [number(child, document, "<I") for child in dead.m_children] if dead is not None else []
    return SCampaign(
        enum(handler.find("ActiveChapter"), document),
        len(running.m_children) if running is not None else 0,
        frozenset(k for k in keys if k is not None),
    )


def read(document: bytes) -> SState:
    """The emperor who reigns, every emperor of the save with his book, and the campaign's state."""
    return state_of(scan(document), document)


def check_change(standing: SStanding | None, participant: int, value: int, rules: SRules) -> SStanding:
    """The record whose reputation may become ``value`` (any zone), else ``CEmperorError`` with the reason."""
    if standing is None:
        raise CEmperorError(f"participant {participant} has no record in the book of the emperor who reigns")
    if standing.reputation is None:
        raise CEmperorError(f"the record of participant {participant} leaves its reputation out: not changed")
    if standing.special is not None:
        state_name = SPECIAL_STATES.get(standing.special, standing.special)
        raise CEmperorError(f"participant {participant} is in the special state {state_name}: not changed")
    if not 0 <= standing.zone < len(rules.bounds):
        raise CEmperorError(f"participant {participant} is in zone {standing.zone}, which the game's assets lack")
    if not rules.lowest <= value <= rules.highest:
        raise CEmperorError(f"reputation {value} is outside {rules.lowest}..{rules.highest}")
    return standing


def set_reputation(document: bytes, participant: int, value: int, rules: SRules) -> bytes:
    """The participant's reputation with the emperor who reigns, a value that keeps the record in its zone (a zone
    change is ``emperorzones.set_reputation``)."""
    root = scan(document)
    state = state_of(root, document)
    standing = check_change(state.standing(participant), participant, value, rules)
    if rules.zone_for(standing.zone, value) != standing.zone:
        low, high = rules.kept(standing.zone)
        raise CEmperorError(
            f"reputation {value} would move participant {participant} out of the {ZONES[standing.zone]} "
            f"(its effects and unlocks are the game's to change); this zone keeps {low}..{high}"
        )
    record = record_of(root, document, state.active, participant)
    span = record.find("CurrentReputation")
    return filedb.patch(document, [(span, struct.pack("<i", value))])


def record_of(root: filedb.CSpan, document: bytes, emperor: int, participant: int) -> filedb.CSpan:
    """The participant's record in the emperor's book, as nodes of ``scan``'s tree."""
    book = _book(root, document, emperor)
    for key, record in zip(book.m_children[::2], book.m_children[1::2], strict=True):
        if number(key, document, "<I") == participant:
            return record
    raise CEmperorError(f"participant {participant} has no record")


def _book(root: filedb.CSpan, document: bytes, emperor: int) -> filedb.CSpan:
    for entry in _entries(root):
        if number(entry.find("guid"), document, "<I") == emperor:
            return entry.find("MetaEmperor").find("ReputationMap")
    raise CEmperorError(f"emperor {emperor} is not in the save")


def _refusal(state: SState, new: int, rules: SRules) -> str:
    """Why the emperor may not change to ``new`` here ("" when he may), save-emperor "What the Saves app changes"."""
    old = state.active
    if old is None:
        return "no emperor reigns in this save (the value would have to be added)"
    campaign = state.campaign
    if campaign is not None and not campaign.completed:
        under_way = f", {campaign.running} story parts under way" if campaign.running else ""
        return (
            f"the campaign is still running ({campaign.stage()}{under_way}): its story picks the emperor; "
            "the change is open once the campaign is completed"
        )
    if campaign is not None and new in campaign.dead:
        return f"emperor {new} died in the campaign"
    if new not in rules.emperors:
        return f"{new} is not an emperor of the game"
    if old not in rules.emperors or rules.emperors[old] != rules.emperors[new]:
        return f"emperors {old} and {new} differ in their zones' effects and unlocks"
    target = state.emperors.get(new)
    if old not in state.emperors:
        return f"emperor {old} has no record in the save"
    if target is None or target.deactivated:
        return f"emperor {new} is not in the save, or deactivated"
    if target.book:
        return f"emperor {new} already keeps a reputation book"
    for emperor in (state.emperors[old], target):
        if emperor.busy:
            return f"emperor {emperor.guid} has {', '.join(emperor.busy)} under way"
    for standing in state.emperors[old].book:
        if standing.special is not None or standing.request:
            return f"participant {standing.participant} is in a special state or has a request under way"
    return ""


def _move_book(document: bytes, old: filedb.CSpan, new: filedb.CSpan) -> bytes:
    """The records of ``old`` moved into the empty ``new``: the document keeps its size (tags carry none)."""
    start, stop, new_body = old.m_header + 8, close_of(old), new.m_header + 8
    book = document[start:stop]
    if new_body > stop:
        return document[:start] + document[stop:new_body] + book + document[new_body:]
    return document[:new_body] + book + document[new_body:start] + document[stop:]


def set_emperor(document: bytes, new: int, rules: SRules) -> bytes:
    """``new`` reigns, and the book of the emperor who reigned moves to him; refused unless the case is plain."""
    root = scan(document)
    state = state_of(root, document)
    if new == state.active:
        return document
    reason = _refusal(state, new, rules)
    if reason:
        raise CEmperorError(f"the emperor is not changed: {reason}")
    old = state.active
    patched = filedb.patch(document, [(root.path(ACTIVE), struct.pack("<I", new))])  # same size: the spans hold
    out = _move_book(patched, _book(root, document, old), _book(root, document, new))
    after = read(out)
    if after.active != new or after.emperors[new].book != state.emperors[old].book or after.emperors[old].book:
        raise CEmperorError("the changed save does not read back as written")
    return out
