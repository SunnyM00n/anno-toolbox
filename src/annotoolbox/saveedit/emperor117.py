"""117's emperor in a save: who reigns and each emperor's reputation book, read from ``data.a7s`` and changed in a
copy (docs/save-emperor.md). Start at ``read`` (the ``State``); ``set_reputation`` and ``set_emperor`` return the
changed document after that page's checks, against ``Rules`` (what the game's emperor and zone assets say).
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


class EmperorError(ValueError):
    pass


@dataclass(frozen=True)
class Standing:
    """One record of an emperor's book: a participant's reputation, zone and special state."""

    participant: int
    reputation: int | None  # None: left out (0)
    zone: int
    special: int | None  # None: no special state
    request: bool  # one of the emperor's requests is under way


@dataclass(frozen=True)
class Emperor:
    guid: int
    book: tuple[Standing, ...]
    busy: tuple[str, ...]  # the parts of his own state that hold something (a decision, a rebellion, ...)
    deactivated: bool


@dataclass(frozen=True)
class Campaign:
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
class State:
    active: int | None  # None: no emperor reigns
    emperors: dict[int, Emperor]
    campaign: Campaign | None  # None: not a campaign

    def standing(self, participant: int) -> Standing | None:
        """The participant's record in the book of the emperor who reigns."""
        emperor = self.emperors.get(self.active) if self.active is not None else None
        return next((s for s in emperor.book if s.participant == participant), None) if emperor else None


@dataclass(frozen=True)
class ZoneEffects:
    """What a zone of an emperor starts and unlocks (``Emperor/ZoneConfiguration``), in the asset's order."""

    effects: tuple[int, ...] = ()
    unlocks: tuple[int, ...] = ()


@dataclass(frozen=True)
class Rules:
    """The game's assets: per emperor his zone configuration (comparable text, and per zone its ``ZoneEffects`` in
    ``zones``), per zone (``ZONES`` order) its lower, lower inner, upper inner and upper bound (``ReputationFeature``),
    and the ``EffectScope`` of each effect the zones start (``scopes``)."""

    emperors: dict[int, str]
    bounds: tuple[tuple[int, int, int, int], ...]
    zones: dict[int, tuple[ZoneEffects, ...]] = field(default_factory=dict)
    scopes: dict[int, str] = field(default_factory=dict)

    @property
    def lowest(self) -> int:
        return self.bounds[0][0]

    @property
    def highest(self) -> int:
        return self.bounds[-1][3]

    def zone_for(self, current: int, reputation: int) -> int:
        """The zone the game gives ``reputation`` from ``current`` (save-emperor "The game's rules")."""
        for zone, (low, low_inner, up_inner, up) in enumerate(self.bounds):
            if zone + 1 == current:  # coming from above
                fits = low <= reputation <= up_inner
            elif zone - 1 == current:  # coming from below
                fits = low_inner <= reputation <= up
            else:
                fits = low <= reputation <= up
            if fits:
                return zone
        return current

    def kept(self, zone: int) -> tuple[int, int]:
        """The lowest and highest reputation that keep a record in ``zone``."""
        values = [r for r in range(self.lowest, self.highest + 1) if self.zone_for(zone, r) == zone]
        return (values[0], values[-1]) if values else (0, -1)


def padded(n: int) -> int:
    """A value's size in the document: attributes are padded to 8 bytes."""
    return (n + 7) // 8 * 8


def close_of(tag: filedb.Span) -> int:
    """Where the tag's closing node starts."""
    if not tag.children:
        return tag.header + 8
    last = tag.children[-1]
    return close_of(last) + 8 if last.is_tag else last.start + padded(last.size)


def _section(node: filedb.Span) -> str:
    """The name of the part of ``MetaGameManager`` a node lies in, "" outside it."""
    while node.parent is not None and node.parent.parent is not None and node.parent.parent.parent is not None:
        node = node.parent
    return node.name if node.parent is not None and node.parent.name == "MetaGameManager" else ""


def scan(doc: bytes) -> filedb.Span:
    """The tree up to the end of the quest manager: the participants, the emperor who reigns, the campaign."""
    quests = []

    def done(attr: filedb.Span) -> bool:
        if _section(attr) == "QuestManager":
            quests.append(True)
            return False
        return bool(quests)

    try:
        return filedb.scan(doc, stop=done)
    except ValueError as e:
        raise EmperorError(f"data.a7s: {e}") from None


def number(span: filedb.Span | None, doc: bytes, fmt: str) -> int | None:
    """An attribute's value by its ``struct`` format, None when it is missing or of another size."""
    if span is None or span.is_tag or span.size != struct.calcsize(fmt):
        return None
    return struct.unpack(fmt, span.value(doc))[0]


def enum(tag: filedb.Span | None, doc: bytes) -> int | None:
    """An enum tag's ``id`` (u16), None when it holds none."""
    return None if tag is None else number(tag.find("id"), doc, "<H")


def _holds(node: filedb.Span) -> bool:
    return bool(node.children) if node.is_tag else node.size > 0


def _standing(key: filedb.Span, record: filedb.Span, doc: bytes) -> Standing:
    participant = number(key, doc, "<I")
    if participant is None or not record.is_tag:
        raise EmperorError("a reputation book of this save is not laid out as known")
    quest = record.find("ActiveEmperorQuest")
    zone = enum(record.find("ActiveZone"), doc)
    return Standing(
        participant,
        number(record.find("CurrentReputation"), doc, "<i"),
        CASUAL if zone is None else zone,
        enum(record.find("SpecialState"), doc),
        quest is not None and _holds(quest),
    )


def _emperor(entry: filedb.Span, doc: bytes) -> Emperor:
    meta = entry.find("MetaEmperor")
    book = meta.find("ReputationMap")
    if book is None or len(book.children) % 2:
        raise EmperorError("a reputation book of this save is not laid out as known")
    pairs = zip(book.children[::2], book.children[1::2], strict=True)
    busy = tuple(c.name for c in meta.children if c.name != "ReputationMap" and c.name not in KEPT and _holds(c))
    deactivated = entry.find("Deactivated")
    return Emperor(
        number(entry.find("guid"), doc, "<I"),
        tuple(_standing(k, v, doc) for k, v in pairs),
        busy,
        deactivated is not None and _holds(deactivated) and any(deactivated.value(doc)),
    )


def _entries(root: filedb.Span) -> list[filedb.Span]:
    objects = root.path(OBJECTS)
    if objects is None:
        raise EmperorError("the save has no MetaObjectMap")
    return [e for e in objects.children if e.is_tag and e.find("MetaEmperor") is not None]


def state_of(root: filedb.Span, doc: bytes) -> State:
    """``read`` on a tree ``scan`` made."""
    emperors = {}
    for entry in _entries(root):
        found = _emperor(entry, doc)
        emperors[found.guid] = found
    return State(number(root.path(ACTIVE), doc, "<I"), emperors, _campaign(root.path(CAMPAIGN), doc))


def _campaign(handler: filedb.Span | None, doc: bytes) -> Campaign | None:
    """The campaign of the save; None when its handler holds nothing (not a campaign)."""
    if handler is None or not any(_holds(c) for c in handler.children):
        return None
    running = handler.find("ActiveComponentGUIDs")
    dead = handler.find("DeadEmperors")
    keys = [number(c, doc, "<I") for c in dead.children] if dead is not None else []
    return Campaign(
        enum(handler.find("ActiveChapter"), doc),
        len(running.children) if running is not None else 0,
        frozenset(k for k in keys if k is not None),
    )


def read(doc: bytes) -> State:
    """The emperor who reigns, every emperor of the save with his book, and the campaign's state."""
    return state_of(scan(doc), doc)


def check_change(standing: Standing | None, participant: int, value: int, rules: Rules) -> Standing:
    """The record whose reputation may become ``value`` (any zone), else ``EmperorError`` with the reason."""
    if standing is None:
        raise EmperorError(f"participant {participant} has no record in the book of the emperor who reigns")
    if standing.reputation is None:
        raise EmperorError(f"the record of participant {participant} leaves its reputation out: not changed")
    if standing.special is not None:
        state_name = SPECIAL_STATES.get(standing.special, standing.special)
        raise EmperorError(f"participant {participant} is in the special state {state_name}: not changed")
    if not 0 <= standing.zone < len(rules.bounds):
        raise EmperorError(f"participant {participant} is in zone {standing.zone}, which the game's assets lack")
    if not rules.lowest <= value <= rules.highest:
        raise EmperorError(f"reputation {value} is outside {rules.lowest}..{rules.highest}")
    return standing


def set_reputation(doc: bytes, participant: int, value: int, rules: Rules) -> bytes:
    """The participant's reputation with the emperor who reigns, a value that keeps the record in its zone (a zone
    change is ``emperorzones.set_reputation``)."""
    root = scan(doc)
    state = state_of(root, doc)
    standing = check_change(state.standing(participant), participant, value, rules)
    if rules.zone_for(standing.zone, value) != standing.zone:
        low, high = rules.kept(standing.zone)
        raise EmperorError(
            f"reputation {value} would move participant {participant} out of the {ZONES[standing.zone]} "
            f"(its effects and unlocks are the game's to change); this zone keeps {low}..{high}"
        )
    record = record_of(root, doc, state.active, participant)
    span = record.find("CurrentReputation")
    return filedb.patch(doc, [(span, struct.pack("<i", value))])


def record_of(root: filedb.Span, doc: bytes, emperor: int, participant: int) -> filedb.Span:
    """The participant's record in the emperor's book, as nodes of ``scan``'s tree."""
    book = _book(root, doc, emperor)
    for key, record in zip(book.children[::2], book.children[1::2], strict=True):
        if number(key, doc, "<I") == participant:
            return record
    raise EmperorError(f"participant {participant} has no record")


def _book(root: filedb.Span, doc: bytes, emperor: int) -> filedb.Span:
    for entry in _entries(root):
        if number(entry.find("guid"), doc, "<I") == emperor:
            return entry.find("MetaEmperor").find("ReputationMap")
    raise EmperorError(f"emperor {emperor} is not in the save")


def _refusal(state: State, new: int, rules: Rules) -> str:
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
    if target is None or target.deactivated:
        return f"emperor {new} is not in the save, or deactivated"
    if target.book:
        return f"emperor {new} already keeps a reputation book"
    for emperor in (state.emperors[old], target):
        if emperor.busy:
            return f"emperor {emperor.guid} has {', '.join(emperor.busy)} under way"
    for s in state.emperors[old].book:
        if s.special is not None or s.request:
            return f"participant {s.participant} is in a special state or has a request under way"
    return ""


def _move_book(doc: bytes, old: filedb.Span, new: filedb.Span) -> bytes:
    """The records of ``old`` moved into the empty ``new``: the document keeps its size (tags carry none)."""
    start, stop, at = old.header + 8, close_of(old), new.header + 8
    book = doc[start:stop]
    if at > stop:
        return doc[:start] + doc[stop:at] + book + doc[at:]
    return doc[:at] + book + doc[at:start] + doc[stop:]


def set_emperor(doc: bytes, new: int, rules: Rules) -> bytes:
    """``new`` reigns, and the book of the emperor who reigned moves to him; refused unless the case is plain."""
    root = scan(doc)
    state = state_of(root, doc)
    if new == state.active:
        return doc
    reason = _refusal(state, new, rules)
    if reason:
        raise EmperorError(f"the emperor is not changed: {reason}")
    old = state.active
    patched = filedb.patch(doc, [(root.path(ACTIVE), struct.pack("<I", new))])  # same size: the spans hold
    out = _move_book(patched, _book(root, doc, old), _book(root, doc, new))
    after = read(out)
    if after.active != new or after.emperors[new].book != state.emperors[old].book or after.emperors[old].book:
        raise EmperorError("the changed save does not read back as written")
    return out
