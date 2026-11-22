"""117's savegames (``.a8s``): read, check as the game does, edit the player, the save's name and money, write.

A save is an RDA V2.2 archive of four zlib FileDB parts, each with a 12-byte tail; the player lives in three
places (``docs/formats/save-format.md``). Unchanged parts keep their stored bytes.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import buildcache
from . import filedb, rda

PARTS = ("meta.a7s", "header.a7s", "gamesetup.a7s", "data.a7s")
SETUP_PARTS = {"header.a7s": "CorporationProfile", "gamesetup.a7s": "GameSetupManager"}
HUMAN_PARTICIPANT = 41  # Profile_Human_00, the human of slot 0 in a single-player save
EMPTY_ZLIB = zlib.compress(b"", 9)  # 78 da 03 00 00 00 00 01
CUSTOM_TAGS = ("CustomCompanyName", "CustomCompanyColorGuids", "CustomCompanyLogo", "CustomAvatarPortrait")


class CSaveError(ValueError):
    pass


def pack_part(doc: bytes) -> bytes:
    """A part as the game writes it: the zlib stream, an empty zlib stream, the inflated size (u32 LE)."""
    return zlib.compress(doc, 9) + EMPTY_ZLIB + struct.pack("<I", len(doc))


def unpack_part(raw: bytes, name: str = "part") -> bytes:
    """Inflate a part the way the game does; raises ``CSaveError`` for anything it would reject."""
    if len(raw) < 6 or raw[0] != 0x78 or raw[1] not in (0x01, 0x9C, 0xDA):
        raise CSaveError(f"{name}: not a zlib stream")
    (size,) = struct.unpack_from("<I", raw, len(raw) - 4)
    inflater = zlib.decompressobj()
    try:
        doc = inflater.decompress(raw[:-4])
    except zlib.error as error:
        raise CSaveError(f"{name}: {error}") from None
    if not inflater.eof:
        raise CSaveError(f"{name}: no 12-byte tail (the zlib stream does not end before the size field)")
    if inflater.unused_data not in (b"", EMPTY_ZLIB):
        raise CSaveError(f"{name}: unexpected bytes after the zlib stream")
    if len(doc) != size:
        raise CSaveError(f"{name}: size field {size} differs from the inflated size {len(doc)}")
    return doc


@dataclass(frozen=True)
class SSave:
    """The four inflated parts; ``stored`` holds the packed bytes of those still equal to what was read, ``original``
    the file they were read from (its archive may hold more than the four parts' bytes: stale blocks the game left)."""

    docs: dict[str, bytes]
    stored: dict[str, bytes] = field(default_factory=dict)
    original: bytes = b""

    def with_docs(self, changed: dict[str, bytes]) -> SSave:
        docs = {**self.docs, **changed}
        return SSave(
            docs, {part: raw for part, raw in self.stored.items() if docs[part] == self.docs[part]}, self.original
        )

    def to_bytes(self) -> bytes:
        """The original file when no part changed (a rewrite would drop what the archive holds beyond the parts)."""
        if self.original and all(part in self.stored for part in PARTS):
            return self.original
        parts = {part: self.stored.get(part) or pack_part(self.docs[part]) for part in PARTS}
        return rda.write_v22_stored(parts)


def read(path: str | Path) -> SSave:
    """Read a save and check every part like the game does."""
    if not Path(path).is_file():
        raise CSaveError(f"{path}: no such file")
    return read_bytes(Path(path).read_bytes(), str(path))


def read_bytes(data: bytes, path: str = "save") -> SSave:
    """``read`` on a save's bytes (a copy in memory: nothing holds the file open)."""
    try:
        raw = rda.CRdaArchive.from_bytes(data, path).read_all(PARTS)
    except rda.CRdaError as error:
        raise CSaveError(f"{path}: not a savegame ({error})") from None
    docs = {}
    for part in PARTS:
        try:
            docs[part] = unpack_part(raw[part], part)
        except CSaveError as error:
            raise CSaveError(f"{path}: {error}") from None
    return SSave(docs, raw, data)


def write(save: SSave, path: str | Path) -> None:
    """Write whole or not at all (read back first, then a temp file beside the target moved over it)."""
    path = Path(path)
    data = save.to_bytes()
    if read_bytes(data, str(path)).docs != save.docs:
        raise CSaveError(f"{path}: the written save does not read back the same")
    buildcache.write_atomic(path, data)


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _u32(value: int) -> bytes:
    if not 0 <= value < 1 << 32:
        raise CSaveError(f"{value} is not a GUID")
    return struct.pack("<I", value)


def _check_name(name: str) -> str:
    if not name or "\0" in name:
        raise CSaveError("a name must not be empty or contain a NUL")
    return name


# The save's own name


def _name_span(doc: bytes) -> filedb.CSpan:
    span = filedb.scan(doc).find("CorporationSaveGameName")
    if span is None:
        raise CSaveError("meta.a7s has no CorporationSaveGameName")
    return span


def save_name(save: SSave) -> str:
    doc = save.docs["meta.a7s"]
    return _name_span(doc).value(doc).decode("utf-16-le")


def set_save_name(save: SSave, name: str) -> SSave:
    """The save's own name (``meta.a7s``); the load list shows the file's name instead."""
    doc = save.docs["meta.a7s"]
    return save.with_docs({"meta.a7s": filedb.patch(doc, [(_name_span(doc), _utf16(_check_name(name)))])})


# The player


@dataclass(frozen=True)
class SParticipantFields:
    """The human player as one place of the save holds it (``profile_name`` only in header and game setup)."""

    name: str
    color: int
    logo: int
    portrait: int
    profile_name: str | None = None
    flags: tuple[int, ...] = ()  # data.a7s: the "custom value" byte of name, colour, logo, portrait


def _need(span: filedb.CSpan | None, what: str) -> filedb.CSpan:
    if span is None:
        raise CSaveError(f"the save has no {what}")
    return span


def _setup_spans(doc: bytes, root_tag: str) -> dict[str, filedb.CSpan]:
    top = _need(filedb.scan(doc).find(root_tag), root_tag)
    slot = _need(top.path("SetupSlots/Slot"), f"{root_tag}/SetupSlots/Slot")
    spans = {"profile_name": top.find("SavegameProfileDisplayName"), "name": slot.find("CompanyName")}
    for key, tag in (("color", "ColorGuid"), ("logo", "LogoGuid"), ("portrait", "PortraitGuid")):
        spans[key] = slot.find(tag)
    return {field_name: _need(span, f"{root_tag}/{field_name}") for field_name, span in spans.items()}


def _data_spans(doc: bytes) -> dict[str, tuple[filedb.CSpan, filedb.CSpan]]:
    """Per field, the (flag, value) attributes of the human participant's custom tags."""

    def is_human(entry: filedb.CSpan | None) -> bool:
        guid = entry.find("guid") if entry is not None else None
        return (
            guid is not None and guid.m_size_bytes == 4 and struct.unpack("<I", guid.value(doc))[0] == HUMAN_PARTICIPANT
        )

    def done(attr: filedb.CSpan) -> bool:  # the human's four custom tags are complete: the rest is not needed
        custom = attr.m_parent
        participant = custom.m_parent
        if custom.m_name not in CUSTOM_TAGS or participant is None or participant.m_name != "MetaParticipant":
            return False
        tags = [participant.find(tag) for tag in CUSTOM_TAGS]
        return all(tag is not None and len(tag.m_children) == 2 for tag in tags) and is_human(participant.m_parent)

    root = filedb.scan(doc, stop=done)
    objects = _need(root.path("MetaGameManager/MetaGameObjectManager/MetaObjectMap"), "MetaObjectMap")
    for entry in objects.m_children:
        participant = entry.find("MetaParticipant")
        if participant is None or not is_human(entry):
            continue
        spans = {}
        for key, tag in zip(("name", "color", "logo", "portrait"), CUSTOM_TAGS, strict=True):
            custom = participant.find(tag)
            if custom is None or len(custom.m_children) != 2:
                raise CSaveError(f"participant {HUMAN_PARTICIPANT} has no complete {tag}")
            spans[key] = (custom.m_children[0], custom.m_children[1])
        return spans
    raise CSaveError(f"the save has no participant {HUMAN_PARTICIPANT}")


def read_participant(save: SSave) -> dict[str, SParticipantFields]:
    """The player in header, game setup and data (in that order), as each stores it."""
    out = {}
    for part, root_tag in SETUP_PARTS.items():
        doc = save.docs[part]
        spans = _setup_spans(doc, root_tag)
        out[part] = SParticipantFields(
            spans["name"].value(doc).decode("utf-8"),
            struct.unpack("<I", spans["color"].value(doc))[0],
            struct.unpack("<I", spans["logo"].value(doc))[0],
            struct.unpack("<I", spans["portrait"].value(doc))[0],
            spans["profile_name"].value(doc).decode("utf-16-le"),
        )
    doc = save.docs["data.a7s"]
    data_spans = _data_spans(doc)

    def guid(key: str) -> int:
        return struct.unpack("<I", data_spans[key][1].value(doc))[0]

    out["data.a7s"] = SParticipantFields(
        data_spans["name"][1].value(doc).decode("utf-16-le"),
        guid("color"),
        guid("logo"),
        guid("portrait"),
        flags=tuple(data_spans[field_name][0].value(doc)[0] for field_name in ("name", "color", "logo", "portrait")),
    )
    return out


def set_participant(
    save: SSave,
    name: str | None = None,
    color: int | None = None,
    logo: int | None = None,
    portrait: int | None = None,
    profile_name: str | None = None,
) -> SSave:
    """Change the human player in all three places. ``profile_name`` (header and game setup only) follows
    ``name`` unless given. A changed value in ``data.a7s`` also sets its "custom value" flag."""
    if name is not None:
        _check_name(name)
    if profile_name is None:
        profile_name = name
    else:
        _check_name(profile_name)
    values = {"name": name, "color": color, "logo": logo, "portrait": portrait}
    guids = {
        field_name: None if guid_value is None else _u32(guid_value)
        for field_name, guid_value in values.items()
        if field_name != "name"
    }
    changed = {}
    for part, root_tag in SETUP_PARTS.items():
        doc = save.docs[part]
        spans = _setup_spans(doc, root_tag)
        edits = [(spans[field_name], packed) for field_name, packed in guids.items() if packed is not None]
        if name is not None:
            edits.append((spans["name"], name.encode("utf-8")))
        if profile_name is not None:
            edits.append((spans["profile_name"], _utf16(profile_name)))
        if edits:
            changed[part] = filedb.patch(doc, edits)
    doc = save.docs["data.a7s"]
    data_spans = _data_spans(doc)
    edits = []
    for key, value in {**guids, "name": None if name is None else _utf16(name)}.items():
        if value is not None:
            edits += [(data_spans[key][0], b"\x01"), (data_spans[key][1], value)]
    if edits:
        changed["data.a7s"] = filedb.patch(doc, edits)
    return save.with_docs(changed)


# Money (save-format "Money": the last i32 of a participant's meta storage)

MONEY_MAX = (1 << 31) - 1


def _money_span(doc: bytes, participant: int) -> filedb.CSpan:
    """The ``StrgLrg`` of the participant's economy pair; the scan ends once the economy manager is behind it."""
    seen = []

    def in_economy(node: filedb.CSpan | None) -> bool:
        while node is not None:
            if (
                node.m_name == "EconomyManager"
                and node.m_parent is not None
                and node.m_parent.m_name == "MetaGameManager"
            ):
                return True
            node = node.m_parent
        return False

    def done(attr: filedb.CSpan) -> bool:
        if in_economy(attr):
            seen.append(True)
            return False
        return bool(seen)

    economy = _need(filedb.scan(doc, stop=done).path("MetaGameManager/EconomyManager"), "EconomyManager")
    for pair in economy.findall("Pair"):
        participant_node = pair.find("ParticipantGUID")
        if (
            participant_node is not None
            and participant_node.m_size_bytes == 4
            and struct.unpack("<I", participant_node.value(doc))[0] == participant
        ):
            storage = _need(pair.path("MetaEconomy/StrgLrg"), f"meta storage of participant {participant}")
            if storage.m_size_bytes < 4 or storage.m_size_bytes % 4:
                raise CSaveError(f"participant {participant}'s meta storage has {storage.m_size_bytes} bytes")
            return storage
    raise CSaveError(f"the save has no economy of participant {participant}")


def read_money(save: SSave, participant: int = HUMAN_PARTICIPANT) -> int:
    doc = save.docs["data.a7s"]
    span = _money_span(doc, participant)
    return struct.unpack("<i", span.value(doc)[-4:])[0]


def set_money(save: SSave, amount: int, participant: int = HUMAN_PARTICIPANT) -> SSave:
    """The participant's balance; the storage keeps its size, so only those four bytes change."""
    if not 0 <= amount <= MONEY_MAX:
        raise CSaveError(f"money {amount} is outside 0..{MONEY_MAX}")
    doc = save.docs["data.a7s"]
    span = _money_span(doc, participant)
    value = span.value(doc)[:-4] + struct.pack("<i", amount)
    return save.with_docs({"data.a7s": filedb.patch(doc, [(span, value)])})
