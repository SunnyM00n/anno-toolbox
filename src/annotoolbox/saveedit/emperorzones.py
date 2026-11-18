"""A reputation change that crosses a zone border, made in a copy of ``data.a7s`` as the game makes it
(docs/save-emperor.md, "A zone change"): the record's zone by the game's rule, the old zone's effects ended and the new
zone's started in the effect manager, the zone's unlocks switched in the participant's unlock state. Start at
``set_reputation``; ``emperor117`` reads the book and makes a change within a zone.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..formats import filedb
from . import emperor117
from .emperor117 import EmperorError, Rules, Standing

EFFECTS = "MetaGameManager/MetaEffectManager"
UNLOCKS = "MetaGameManager/ParticipantUnlockManager/UnlockData/Data"
SCOPES = {"Meta": 12}  # an effect's EffectScope as its Scope data id (formats/enums-117.md); zones start Meta ones
META_BIT = 1 << 63  # a meta effect's id is the effect manager's next index with the top bit set
KEY, ENTRY = 0x8000, 1  # the name ids of a map's key attribute and of its entry tag
LOCKED = 0  # a LockState value; an asset that is unlocked has no entry (save-emperor "A zone change")
ATTRS = ("Guid", "Id", "TargetParticipant", "id", "value")
TAGS = ("EndTimestamp", "Scope", "value")


@dataclass(frozen=True)
class _Effect:
    span: filedb.Span
    guid: int | None
    target: int | None


@dataclass(frozen=True)
class _Lock:
    key: filedb.Span
    entry: filedb.Span
    value: int | None


def _attr(nid: int, data: bytes) -> bytes:
    return struct.pack("<iI", len(data), nid) + data + bytes(emperor117.padded(len(data)) - len(data))


def _tag(nid: int, *children: bytes) -> bytes:
    return struct.pack("<iI", 0, nid) + b"".join(children) + bytes(8)


def _end(node: filedb.Span) -> int:
    """Where the node's bytes end (after an attribute's padding, after a tag's closing node)."""
    return emperor117.close_of(node) + 8 if node.is_tag else node.start + emperor117.padded(node.size)


def _replace(node: filedb.Span, doc: bytes, data: bytes) -> tuple[int, int, bytes]:
    """An attribute with new content (the size may change), as a splice."""
    nid = struct.unpack_from("<I", doc, node.header + 4)[0]
    return node.header, _end(node), _attr(nid, data)


def _splice(doc: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Byte ranges replaced (an insertion is an empty range); the tail's two name-table offsets move by the change.
    FileDB tags carry no size, so nothing else does."""
    out = bytearray()
    pos = delta = 0
    for start, stop, new in sorted(edits, key=lambda e: (e[0], e[1])):
        if start < pos:
            raise EmperorError("two changes of the zone overlap in the document")
        out += doc[pos:start] + new
        pos = stop
        delta += len(new) - (stop - start)
    out += doc[pos:]
    tags_off, attrs_off = struct.unpack_from("<II", out, len(out) - 16)
    struct.pack_into("<II", out, len(out) - 16, tags_off + delta, attrs_off + delta)
    return bytes(out)


def _u32s(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 4}I", data)) if len(data) % 4 == 0 else []


def _ids(data: bytes) -> list[int] | None:
    return list(struct.unpack(f"<{len(data) // 8}Q", data)) if len(data) % 8 == 0 else None


def _effects(tag: filedb.Span, doc: bytes) -> dict[int, _Effect]:
    """The effect manager's entries by id."""
    out = {}
    for entry in tag.children:
        ident = emperor117.number(entry.find("Id"), doc, "<Q") if entry.is_tag else None
        if ident is not None:
            guid = emperor117.number(entry.find("Guid"), doc, "<I")
            out[ident] = _Effect(entry, guid, emperor117.number(entry.find("TargetParticipant"), doc, "<I"))
    return out


def _lock_value(entry: filedb.Span, doc: bytes) -> int | None:
    """The value of a LockState entry (``#1/value/value/value``), None for another shape."""
    node = entry
    for name in ("value", "value", "value"):
        if not node.is_tag or len(node.children) != 1 or node.children[0].name != name:
            return None
        node = node.children[0]
    return emperor117.number(node, doc, "<I")


def _lock_state(root: filedb.Span, doc: bytes, participant: int) -> filedb.Span:
    data = root.path(UNLOCKS)
    if data is None or len(data.children) % 2:
        raise EmperorError("the save's participant unlock state is not laid out as known")
    for key, entry in zip(data.children[::2], data.children[1::2], strict=True):
        if emperor117.number(key, doc, "<I") == participant and entry.find("LockState") is not None:
            return entry.find("LockState")
    raise EmperorError(f"participant {participant} has no unlock state in the save")


def _locks(tag: filedb.Span, doc: bytes) -> dict[int, _Lock]:
    if len(tag.children) % 2:
        raise EmperorError("a participant's unlock state is not laid out as known")
    out = {}
    for key, entry in zip(tag.children[::2], tag.children[1::2], strict=True):
        guid = emperor117.number(key, doc, "<I")
        if guid is not None:
            out[guid] = _Lock(key, entry, _lock_value(entry, doc))
    return out


def _names(doc: bytes) -> tuple[dict[str, int], dict[str, int]]:
    tags, attrs = filedb.name_ids(doc)
    missing = [n for n in ATTRS if n not in attrs] + [n for n in TAGS if n not in tags]
    if missing:
        raise EmperorError(f"the save names no {', '.join(missing)}: a zone's effect cannot be written")
    return tags, attrs


def _zone_rules(rules: Rules, emperor: int, old: int, new: int) -> tuple[emperor117.ZoneEffects, ...]:
    zones = rules.zones.get(emperor, ())
    if len(zones) != len(emperor117.ZONES):
        raise EmperorError(f"the game's assets give emperor {emperor} no zone configuration")
    for effect in zones[old].effects + zones[new].effects:
        scope = rules.scopes.get(effect)
        if scope not in SCOPES:
            raise EmperorError(f"effect {effect} has the scope {scope}: its state would lie outside the effect manager")
    return zones[old], zones[new]


def _check_as_left(started, unlocked, effects, locks, participant, zone, kept) -> str:
    """Why the save does not hold what ``zone`` left (effects and unlocks), "" when it does."""
    if started is None or sorted(started) != sorted(set(started)):
        return "its started effects are not a list of effect ids"
    found = [effects.get(i) for i in started]
    if any(e is None or e.target != participant for e in found) or sorted(e.guid for e in found) != sorted(
        zone.effects
    ):
        return "its started effects are not the zone's in the effect manager"
    if sorted(unlocked) != sorted(zone.unlocks):
        return "its unlocked assets are not the zone's"
    if any(g in locks for g in zone.unlocks):
        return "an asset its zone unlocked is locked in its unlock state"
    if any(locks[g].value != LOCKED for g in kept if g in locks):
        return "an asset the new zone unlocks has a lock state other than locked"
    return ""


def _effect_entry(tags: dict, attrs: dict, guid: int, ident: int, participant: int, scope: int) -> bytes:
    """An effect manager entry as the game writes one: GUID, id, target, no end, the scope."""
    return _tag(
        ENTRY,
        _attr(attrs["Guid"], struct.pack("<I", guid)),
        _attr(attrs["Id"], struct.pack("<Q", ident)),
        _attr(attrs["TargetParticipant"], struct.pack("<I", participant)),
        _tag(tags["EndTimestamp"]),
        _tag(tags["Scope"], _tag(tags["value"], _attr(attrs["id"], struct.pack("<H", scope)))),
    )


def _lock_entry(tags: dict, attrs: dict, guid: int) -> bytes:
    inner = _tag(tags["value"], _tag(tags["value"], _attr(attrs["value"], struct.pack("<I", LOCKED))))
    return _attr(KEY, struct.pack("<I", guid)) + _tag(ENTRY, inner)


def _zone_edit(record: filedb.Span, doc: bytes, attrs: dict, zone: int) -> list[tuple[int, int, bytes]]:
    """The record's ``ActiveZone``: left out for Casual, as the game writes it."""
    tag = record.find("ActiveZone")
    if tag is None or not tag.is_tag:
        raise EmperorError("the record has no ActiveZone")
    ident = tag.find("id")
    if zone == emperor117.CASUAL:
        return [(ident.header, _end(ident), b"")] if ident is not None else []
    value = struct.pack("<H", zone)
    return (
        [_replace(ident, doc, value)]
        if ident is not None
        else [(tag.header + 8, tag.header + 8, _attr(attrs["id"], value))]
    )


def _change_zone(doc: bytes, root: filedb.Span, emperor: int, standing: Standing, value: int, zone: int, rules: Rules) -> bytes:  # fmt: skip
    participant = standing.participant
    old, new = _zone_rules(rules, emperor, standing.zone, zone)
    record = emperor117.record_of(root, doc, emperor, participant)
    spans = {n: record.find(n) for n in ("CurrentReputation", "StartedEffects", "UnlockedAssets")}
    manager = root.path(EFFECTS)
    index = manager.find("NextEffectIndex") if manager is not None else None
    effects_tag = manager.find("Effects") if manager is not None else None
    if any(s is None or s.is_tag for s in spans.values()) or index is None or effects_tag is None:
        raise EmperorError("the record or the effect manager lacks a part a zone change writes")
    effects = _effects(effects_tag, doc)
    lock_tag = _lock_state(root, doc, participant)
    locks = _locks(lock_tag, doc)
    started = _ids(spans["StartedEffects"].value(doc))
    unlocked = _u32s(spans["UnlockedAssets"].value(doc))
    gained = [g for g in new.unlocks if g not in old.unlocks]
    reason = _check_as_left(started, unlocked, effects, locks, participant, old, gained)
    if reason:
        raise EmperorError(f"the zone of participant {participant} is not changed: {reason}")
    tags, attrs = _names(doc)
    first = emperor117.number(index, doc, "<Q")
    if first is None or first & META_BIT or any(i >= first | META_BIT for i in effects):
        raise EmperorError("the effect manager's next index is not above its effects' ids")
    new_ids = [(first + k) | META_BIT for k in range(len(new.effects))]
    edits = [
        _replace(spans["CurrentReputation"], doc, struct.pack("<i", value)),
        _replace(spans["StartedEffects"], doc, struct.pack(f"<{len(new_ids)}Q", *new_ids)),
        _replace(spans["UnlockedAssets"], doc, struct.pack(f"<{len(new.unlocks)}I", *new.unlocks)),
        _replace(index, doc, struct.pack("<Q", first + len(new_ids))),
        *_zone_edit(record, doc, attrs, zone),
    ]
    edits += [(effects[i].span.header, _end(effects[i].span), b"") for i in started]
    entries = [_effect_entry(tags, attrs, g, i, participant, SCOPES[rules.scopes[g]]) for g, i in zip(new.effects, new_ids, strict=True)]  # fmt: skip
    edits.append((emperor117.close_of(effects_tag), emperor117.close_of(effects_tag), b"".join(entries)))
    edits += [(locks[g].key.header, _end(locks[g].entry), b"") for g in gained if g in locks]
    relocked = b"".join(_lock_entry(tags, attrs, g) for g in old.unlocks if g not in new.unlocks)
    edits.append((emperor117.close_of(lock_tag), emperor117.close_of(lock_tag), relocked))
    out = _splice(doc, edits)
    _verify(out, emperor, participant, value, zone, new, new_ids)
    return out


def _verify(doc: bytes, emperor: int, participant: int, value: int, zone: int, new, new_ids: list[int]) -> None:
    """The changed document reads back as the new zone leaves a record (else nothing is written)."""
    root = emperor117.scan(doc)
    standing = emperor117.state_of(root, doc).standing(participant)
    record = emperor117.record_of(root, doc, emperor, participant)
    effects = _effects(root.path(EFFECTS).find("Effects"), doc)
    locks = _locks(_lock_state(root, doc, participant), doc)
    started = _ids(record.find("StartedEffects").value(doc))
    unlocked = _u32s(record.find("UnlockedAssets").value(doc))
    if (
        standing is None
        or (standing.reputation, standing.zone) != (value, zone)
        or started != new_ids
        or _check_as_left(started, unlocked, effects, locks, participant, new, [])
    ):
        raise EmperorError("the changed save does not read back as written")


def set_reputation(doc: bytes, participant: int, value: int, rules: Rules) -> bytes:
    """The participant's reputation with the emperor who reigns; across a zone border the zone changes with it."""
    root = emperor117.scan(doc)
    state = emperor117.state_of(root, doc)
    standing = emperor117.check_change(state.standing(participant), participant, value, rules)
    zone = rules.zone_for(standing.zone, value)
    if zone == standing.zone:
        return emperor117.set_reputation(doc, participant, value, rules)
    return _change_zone(doc, root, state.active, standing, value, zone, rules)
