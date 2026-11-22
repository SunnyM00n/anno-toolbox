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
from .emperor117 import CEmperorError, SRules, SStanding

EFFECTS = "MetaGameManager/MetaEffectManager"
UNLOCKS = "MetaGameManager/ParticipantUnlockManager/UnlockData/Data"
SCOPES = {"Meta": 12}  # an effect's EffectScope as its Scope data id (formats/enums-117.md); zones start Meta ones
META_BIT = 1 << 63  # a meta effect's id is the effect manager's next index with the top bit set
KEY, ENTRY = 0x8000, 1  # the name ids of a map's key attribute and of its entry tag
LOCKED = 0  # a LockState value; an asset that is unlocked has no entry (save-emperor "A zone change")
ATTRS = ("Guid", "Id", "TargetParticipant", "id", "value")
TAGS = ("EndTimestamp", "Scope", "value")


@dataclass(frozen=True)
class _SEffect:
    span: filedb.CSpan
    guid: int | None
    target: int | None


@dataclass(frozen=True)
class _SLock:
    key: filedb.CSpan
    entry: filedb.CSpan
    value: int | None


def _attr(nid: int, data: bytes) -> bytes:
    return struct.pack("<iI", len(data), nid) + data + bytes(emperor117.padded(len(data)) - len(data))


def _tag(nid: int, *children: bytes) -> bytes:
    return struct.pack("<iI", 0, nid) + b"".join(children) + bytes(8)


def _end(node: filedb.CSpan) -> int:
    """Where the node's bytes end (after an attribute's padding, after a tag's closing node)."""
    return emperor117.close_of(node) + 8 if node.m_is_tag else node.m_start + emperor117.padded(node.m_size_bytes)


def _replace(node: filedb.CSpan, document: bytes, data: bytes) -> tuple[int, int, bytes]:
    """An attribute with new content (the size may change), as a splice."""
    nid = struct.unpack_from("<I", document, node.m_header + 4)[0]
    return node.m_header, _end(node), _attr(nid, data)


def _splice(document: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Byte ranges replaced (an insertion is an empty range); the tail's two name-table offsets move by the change.
    FileDB tags carry no size, so nothing else does."""
    out = bytearray()
    position = delta = 0
    for start, stop, new in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        if start < position:
            raise CEmperorError("two changes of the zone overlap in the document")
        out += document[position:start] + new
        position = stop
        delta += len(new) - (stop - start)
    out += document[position:]
    tags_off, attrs_off = struct.unpack_from("<II", out, len(out) - 16)
    struct.pack_into("<II", out, len(out) - 16, tags_off + delta, attrs_off + delta)
    return bytes(out)


def _u32s(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 4}I", data)) if len(data) % 4 == 0 else []


def _ids(data: bytes) -> list[int] | None:
    return list(struct.unpack(f"<{len(data) // 8}Q", data)) if len(data) % 8 == 0 else None


def _effects(tag: filedb.CSpan, document: bytes) -> dict[int, _SEffect]:
    """The effect manager's entries by id."""
    out = {}
    for entry in tag.m_children:
        ident = emperor117.number(entry.find("Id"), document, "<Q") if entry.m_is_tag else None
        if ident is not None:
            guid = emperor117.number(entry.find("Guid"), document, "<I")
            out[ident] = _SEffect(entry, guid, emperor117.number(entry.find("TargetParticipant"), document, "<I"))
    return out


def _lock_value(entry: filedb.CSpan, document: bytes) -> int | None:
    """The value of a LockState entry (``#1/value/value/value``), None for another shape."""
    node = entry
    for name in ("value", "value", "value"):
        if not node.m_is_tag or len(node.m_children) != 1 or node.m_children[0].m_name != name:
            return None
        node = node.m_children[0]
    return emperor117.number(node, document, "<I")


def _lock_state(root: filedb.CSpan, document: bytes, participant: int) -> filedb.CSpan:
    data = root.path(UNLOCKS)
    if data is None or len(data.m_children) % 2:
        raise CEmperorError("the save's participant unlock state is not laid out as known")
    for key, entry in zip(data.m_children[::2], data.m_children[1::2], strict=True):
        if emperor117.number(key, document, "<I") == participant and entry.find("LockState") is not None:
            return entry.find("LockState")
    raise CEmperorError(f"participant {participant} has no unlock state in the save")


def _locks(tag: filedb.CSpan, document: bytes) -> dict[int, _SLock]:
    if len(tag.m_children) % 2:
        raise CEmperorError("a participant's unlock state is not laid out as known")
    out = {}
    for key, entry in zip(tag.m_children[::2], tag.m_children[1::2], strict=True):
        guid = emperor117.number(key, document, "<I")
        if guid is not None:
            out[guid] = _SLock(key, entry, _lock_value(entry, document))
    return out


def _names(document: bytes) -> tuple[dict[str, int], dict[str, int]]:
    tags, attrs = filedb.name_ids(document)
    missing = [name for name in ATTRS if name not in attrs] + [name for name in TAGS if name not in tags]
    if missing:
        raise CEmperorError(f"the save names no {', '.join(missing)}: a zone's effect cannot be written")
    return tags, attrs


def _zone_rules(rules: SRules, emperor: int, old: int, new: int) -> tuple[emperor117.SZoneEffects, ...]:
    zones = rules.zones.get(emperor, ())
    if len(zones) != len(emperor117.ZONES):
        raise CEmperorError(f"the game's assets give emperor {emperor} no zone configuration")
    for effect in zones[old].effects + zones[new].effects:
        scope = rules.scopes.get(effect)
        if scope not in SCOPES:
            raise CEmperorError(
                f"effect {effect} has the scope {scope}: its state would lie outside the effect manager"
            )
    return zones[old], zones[new]


def _check_as_left(started, unlocked, effects, locks, participant, zone, kept) -> str:
    """Why the save does not hold what ``zone`` left (effects and unlocks), "" when it does."""
    if started is None or sorted(started) != sorted(set(started)):
        return "its started effects are not a list of effect ids"
    found = [effects.get(i) for i in started]
    if any(effect is None or effect.guid is None or effect.target != participant for effect in found) or sorted(
        effect.guid for effect in found
    ) != sorted(zone.effects):
        return "its started effects are not the zone's in the effect manager"
    if sorted(unlocked) != sorted(zone.unlocks):
        return "its unlocked assets are not the zone's"
    if any(guid in locks for guid in zone.unlocks):
        return "an asset its zone unlocked is locked in its unlock state"
    if any(locks[guid].value != LOCKED for guid in kept if guid in locks):
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


def _zone_edit(record: filedb.CSpan, document: bytes, attrs: dict, zone: int) -> list[tuple[int, int, bytes]]:
    """The record's ``ActiveZone``: left out for Casual, as the game writes it."""
    tag = record.find("ActiveZone")
    if tag is None or not tag.m_is_tag:
        raise CEmperorError("the record has no ActiveZone")
    ident = tag.find("id")
    if zone == emperor117.CASUAL:
        return [(ident.m_header, _end(ident), b"")] if ident is not None else []
    value = struct.pack("<H", zone)
    return (
        [_replace(ident, document, value)]
        if ident is not None
        else [(tag.m_header + 8, tag.m_header + 8, _attr(attrs["id"], value))]
    )


def _change_zone(document: bytes, root: filedb.CSpan, emperor: int, standing: SStanding, value: int, zone: int, rules: SRules) -> bytes:  # fmt: skip
    participant = standing.participant
    old, new = _zone_rules(rules, emperor, standing.zone, zone)
    record = emperor117.record_of(root, document, emperor, participant)
    spans = {name: record.find(name) for name in ("CurrentReputation", "StartedEffects", "UnlockedAssets")}
    manager = root.path(EFFECTS)
    index = manager.find("NextEffectIndex") if manager is not None else None
    effects_tag = manager.find("Effects") if manager is not None else None
    if any(span is None or span.m_is_tag for span in spans.values()) or index is None or effects_tag is None:
        raise CEmperorError("the record or the effect manager lacks a part a zone change writes")
    effects = _effects(effects_tag, document)
    lock_tag = _lock_state(root, document, participant)
    locks = _locks(lock_tag, document)
    started = _ids(spans["StartedEffects"].value(document))
    unlocked = _u32s(spans["UnlockedAssets"].value(document))
    gained = [guid for guid in new.unlocks if guid not in old.unlocks]
    reason = _check_as_left(started, unlocked, effects, locks, participant, old, gained)
    if reason:
        raise CEmperorError(f"the zone of participant {participant} is not changed: {reason}")
    tags, attrs = _names(document)
    first = emperor117.number(index, document, "<Q")
    if first is None or first & META_BIT or any(i >= first | META_BIT for i in effects):
        raise CEmperorError("the effect manager's next index is not above its effects' ids")
    new_ids = [(first + k) | META_BIT for k in range(len(new.effects))]
    edits = [
        _replace(spans["CurrentReputation"], document, struct.pack("<i", value)),
        _replace(spans["StartedEffects"], document, struct.pack(f"<{len(new_ids)}Q", *new_ids)),
        _replace(spans["UnlockedAssets"], document, struct.pack(f"<{len(new.unlocks)}I", *new.unlocks)),
        _replace(index, document, struct.pack("<Q", first + len(new_ids))),
        *_zone_edit(record, document, attrs, zone),
    ]
    edits += [(effects[i].span.m_header, _end(effects[i].span), b"") for i in started]
    entries = [_effect_entry(tags, attrs, guid, i, participant, SCOPES[rules.scopes[guid]]) for guid, i in zip(new.effects, new_ids, strict=True)]  # fmt: skip
    edits.append((emperor117.close_of(effects_tag), emperor117.close_of(effects_tag), b"".join(entries)))
    edits += [(locks[guid].key.m_header, _end(locks[guid].entry), b"") for guid in gained if guid in locks]
    relocked = b"".join(_lock_entry(tags, attrs, guid) for guid in old.unlocks if guid not in new.unlocks)
    edits.append((emperor117.close_of(lock_tag), emperor117.close_of(lock_tag), relocked))
    out = _splice(document, edits)
    _verify(out, emperor, participant, value, zone, new, new_ids)
    return out


def _verify(document: bytes, emperor: int, participant: int, value: int, zone: int, new, new_ids: list[int]) -> None:
    """The changed document reads back as the new zone leaves a record (else nothing is written)."""
    root = emperor117.scan(document)
    standing = emperor117.state_of(root, document).standing(participant)
    record = emperor117.record_of(root, document, emperor, participant)
    effects = _effects(root.path(EFFECTS).find("Effects"), document)
    locks = _locks(_lock_state(root, document, participant), document)
    started = _ids(record.find("StartedEffects").value(document))
    unlocked = _u32s(record.find("UnlockedAssets").value(document))
    if (
        standing is None
        or (standing.reputation, standing.zone) != (value, zone)
        or started != new_ids
        or _check_as_left(started, unlocked, effects, locks, participant, new, [])
    ):
        raise CEmperorError("the changed save does not read back as written")


def set_reputation(document: bytes, participant: int, value: int, rules: SRules) -> bytes:
    """The participant's reputation with the emperor who reigns; across a zone border the zone changes with it."""
    root = emperor117.scan(document)
    state = emperor117.state_of(root, document)
    standing = emperor117.check_change(state.standing(participant), participant, value, rules)
    zone = rules.zone_for(standing.zone, value)
    if zone == standing.zone:
        return emperor117.set_reputation(document, participant, value, rules)
    return _change_zone(document, root, state.active, standing, value, zone, rules)
