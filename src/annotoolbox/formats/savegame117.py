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


class SaveError(ValueError):
    pass


def pack_part(doc: bytes) -> bytes:
    """A part as the game writes it: the zlib stream, an empty zlib stream, the inflated size (u32 LE)."""
    return zlib.compress(doc, 9) + EMPTY_ZLIB + struct.pack("<I", len(doc))


def unpack_part(raw: bytes, name: str = "part") -> bytes:
    """Inflate a part the way the game does; raises ``SaveError`` for anything it would reject."""
    if len(raw) < 6 or raw[0] != 0x78 or raw[1] not in (0x01, 0x9C, 0xDA):
        raise SaveError(f"{name}: not a zlib stream")
    (size,) = struct.unpack_from("<I", raw, len(raw) - 4)
    inflater = zlib.decompressobj()
    try:
        doc = inflater.decompress(raw[:-4])
    except zlib.error as e:
        raise SaveError(f"{name}: {e}") from None
    if not inflater.eof:
        raise SaveError(f"{name}: no 12-byte tail (the zlib stream does not end before the size field)")
    if inflater.unused_data not in (b"", EMPTY_ZLIB):
        raise SaveError(f"{name}: unexpected bytes after the zlib stream")
    if len(doc) != size:
        raise SaveError(f"{name}: size field {size} differs from the inflated size {len(doc)}")
    return doc


@dataclass(frozen=True)
class Save:
    """The four inflated parts; ``stored`` holds the packed bytes of those still equal to what was read, ``original``
    the file they were read from (its archive may hold more than the four parts' bytes: stale blocks the game left)."""

    docs: dict[str, bytes]
    stored: dict[str, bytes] = field(default_factory=dict)
    original: bytes = b""

    def with_docs(self, changed: dict[str, bytes]) -> Save:
        docs = {**self.docs, **changed}
        return Save(docs, {n: raw for n, raw in self.stored.items() if docs[n] == self.docs[n]}, self.original)

    def to_bytes(self) -> bytes:
        """The original file when no part changed (a rewrite would drop what the archive holds beyond the parts)."""
        if self.original and all(n in self.stored for n in PARTS):
            return self.original
        parts = {n: self.stored.get(n) or pack_part(self.docs[n]) for n in PARTS}
        return rda.write_v22_stored(parts)


def read(path: str | Path) -> Save:
    """Read a save and check every part like the game does."""
    if not Path(path).is_file():
        raise SaveError(f"{path}: no such file")
    return read_bytes(Path(path).read_bytes(), str(path))


def read_bytes(data: bytes, path: str = "save") -> Save:
    """``read`` on a save's bytes (a copy in memory: nothing holds the file open)."""
    try:
        raw = rda.RdaArchive.from_bytes(data, path).read_all(PARTS)
    except rda.RdaError as e:
        raise SaveError(f"{path}: not a savegame ({e})") from None
    docs = {}
    for n in PARTS:
        try:
            docs[n] = unpack_part(raw[n], n)
        except SaveError as e:
            raise SaveError(f"{path}: {e}") from None
    return Save(docs, raw, data)


def write(save: Save, path: str | Path) -> None:
    """Write whole or not at all (read back first, then a temp file beside the target moved over it)."""
    path = Path(path)
    data = save.to_bytes()
    if read_bytes(data, str(path)).docs != save.docs:
        raise SaveError(f"{path}: the written save does not read back the same")
    buildcache.write_atomic(path, data)


def _utf16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _u32(v: int) -> bytes:
    if not 0 <= v < 1 << 32:
        raise SaveError(f"{v} is not a GUID")
    return struct.pack("<I", v)


def _check_name(name: str) -> str:
    if not name or "\0" in name:
        raise SaveError("a name must not be empty or contain a NUL")
    return name


# The save's own name


def _name_span(doc: bytes) -> filedb.Span:
    span = filedb.scan(doc).find("CorporationSaveGameName")
    if span is None:
        raise SaveError("meta.a7s has no CorporationSaveGameName")
    return span


def save_name(save: Save) -> str:
    doc = save.docs["meta.a7s"]
    return _name_span(doc).value(doc).decode("utf-16-le")


def set_save_name(save: Save, name: str) -> Save:
    """The save's own name (``meta.a7s``); the load list shows the file's name instead."""
    doc = save.docs["meta.a7s"]
    return save.with_docs({"meta.a7s": filedb.patch(doc, [(_name_span(doc), _utf16(_check_name(name)))])})


# The player


@dataclass(frozen=True)
class PlayerFields:
    """The human player as one place of the save holds it (``profile_name`` only in header and game setup)."""

    name: str
    color: int
    logo: int
    portrait: int
    profile_name: str | None = None
    flags: tuple[int, ...] = ()  # data.a7s: the "custom value" byte of name, colour, logo, portrait


def _need(span: filedb.Span | None, what: str) -> filedb.Span:
    if span is None:
        raise SaveError(f"the save has no {what}")
    return span


def _setup_spans(doc: bytes, root_tag: str) -> dict[str, filedb.Span]:
    top = _need(filedb.scan(doc).find(root_tag), root_tag)
    slot = _need(top.path("SetupSlots/Slot"), f"{root_tag}/SetupSlots/Slot")
    spans = {"profile_name": top.find("SavegameProfileDisplayName"), "name": slot.find("CompanyName")}
    for key, tag in (("color", "ColorGuid"), ("logo", "LogoGuid"), ("portrait", "PortraitGuid")):
        spans[key] = slot.find(tag)
    return {k: _need(v, f"{root_tag}/{k}") for k, v in spans.items()}


def _data_spans(doc: bytes) -> dict[str, tuple[filedb.Span, filedb.Span]]:
    """Per field, the (flag, value) attributes of the human participant's custom tags."""

    def is_human(entry: filedb.Span) -> bool:
        guid = entry.find("guid")
        return guid is not None and guid.size == 4 and struct.unpack("<I", guid.value(doc))[0] == HUMAN_PARTICIPANT

    def done(attr: filedb.Span) -> bool:  # the human's four custom tags are complete: the rest is not needed
        custom = attr.parent
        participant = custom.parent
        if custom.name not in CUSTOM_TAGS or participant is None or participant.name != "MetaParticipant":
            return False
        tags = [participant.find(t) for t in CUSTOM_TAGS]
        return all(t is not None and len(t.children) == 2 for t in tags) and is_human(participant.parent)

    root = filedb.scan(doc, stop=done)
    objects = _need(root.path("MetaGameManager/MetaGameObjectManager/MetaObjectMap"), "MetaObjectMap")
    for entry in objects.children:
        participant = entry.find("MetaParticipant")
        if participant is None or not is_human(entry):
            continue
        spans = {}
        for key, tag in zip(("name", "color", "logo", "portrait"), CUSTOM_TAGS, strict=True):
            custom = participant.find(tag)
            if custom is None or len(custom.children) != 2:
                raise SaveError(f"participant {HUMAN_PARTICIPANT} has no complete {tag}")
            spans[key] = (custom.children[0], custom.children[1])
        return spans
    raise SaveError(f"the save has no participant {HUMAN_PARTICIPANT}")


def read_player(save: Save) -> dict[str, PlayerFields]:
    """The player in header, game setup and data (in that order), as each stores it."""
    out = {}
    for part, root_tag in SETUP_PARTS.items():
        doc = save.docs[part]
        s = _setup_spans(doc, root_tag)
        out[part] = PlayerFields(
            s["name"].value(doc).decode("utf-8"),
            struct.unpack("<I", s["color"].value(doc))[0],
            struct.unpack("<I", s["logo"].value(doc))[0],
            struct.unpack("<I", s["portrait"].value(doc))[0],
            s["profile_name"].value(doc).decode("utf-16-le"),
        )
    doc = save.docs["data.a7s"]
    d = _data_spans(doc)

    def guid(key: str) -> int:
        return struct.unpack("<I", d[key][1].value(doc))[0]

    out["data.a7s"] = PlayerFields(
        d["name"][1].value(doc).decode("utf-16-le"),
        guid("color"),
        guid("logo"),
        guid("portrait"),
        flags=tuple(d[k][0].value(doc)[0] for k in ("name", "color", "logo", "portrait")),
    )
    return out


def set_player(
    save: Save,
    name: str | None = None,
    color: int | None = None,
    logo: int | None = None,
    portrait: int | None = None,
    profile_name: str | None = None,
) -> Save:
    """Change the human player in all three places. ``profile_name`` (header and game setup only) follows
    ``name`` unless given. A changed value in ``data.a7s`` also sets its "custom value" flag."""
    if name is not None:
        _check_name(name)
    if profile_name is None:
        profile_name = name
    else:
        _check_name(profile_name)
    values = {"name": name, "color": color, "logo": logo, "portrait": portrait}
    guids = {k: None if v is None else _u32(v) for k, v in values.items() if k != "name"}
    changed = {}
    for part, root_tag in SETUP_PARTS.items():
        doc = save.docs[part]
        s = _setup_spans(doc, root_tag)
        edits = [(s[k], g) for k, g in guids.items() if g is not None]
        if name is not None:
            edits.append((s["name"], name.encode("utf-8")))
        if profile_name is not None:
            edits.append((s["profile_name"], _utf16(profile_name)))
        if edits:
            changed[part] = filedb.patch(doc, edits)
    doc = save.docs["data.a7s"]
    d = _data_spans(doc)
    edits = []
    for key, value in {**guids, "name": None if name is None else _utf16(name)}.items():
        if value is not None:
            edits += [(d[key][0], b"\x01"), (d[key][1], value)]
    if edits:
        changed["data.a7s"] = filedb.patch(doc, edits)
    return save.with_docs(changed)


# Money (save-format "Money": the last i32 of a participant's meta storage)

MONEY_MAX = (1 << 31) - 1


def _money_span(doc: bytes, participant: int) -> filedb.Span:
    """The ``StrgLrg`` of the participant's economy pair; the scan ends once the economy manager is behind it."""
    seen = []

    def in_economy(node: filedb.Span) -> bool:
        while node is not None:
            if node.name == "EconomyManager" and node.parent is not None and node.parent.name == "MetaGameManager":
                return True
            node = node.parent
        return False

    def done(attr: filedb.Span) -> bool:
        if in_economy(attr):
            seen.append(True)
            return False
        return bool(seen)

    economy = _need(filedb.scan(doc, stop=done).path("MetaGameManager/EconomyManager"), "EconomyManager")
    for pair in economy.findall("Pair"):
        pid = pair.find("ParticipantGUID")
        if pid is not None and pid.size == 4 and struct.unpack("<I", pid.value(doc))[0] == participant:
            storage = _need(pair.path("MetaEconomy/StrgLrg"), f"meta storage of participant {participant}")
            if storage.size < 4 or storage.size % 4:
                raise SaveError(f"participant {participant}'s meta storage has {storage.size} bytes")
            return storage
    raise SaveError(f"the save has no economy of participant {participant}")


def read_money(save: Save, participant: int = HUMAN_PARTICIPANT) -> int:
    doc = save.docs["data.a7s"]
    span = _money_span(doc, participant)
    return struct.unpack("<i", span.value(doc)[-4:])[0]


def set_money(save: Save, amount: int, participant: int = HUMAN_PARTICIPANT) -> Save:
    """The participant's balance; the storage keeps its size, so only those four bytes change."""
    if not 0 <= amount <= MONEY_MAX:
        raise SaveError(f"money {amount} is outside 0..{MONEY_MAX}")
    doc = save.docs["data.a7s"]
    span = _money_span(doc, participant)
    value = span.value(doc)[:-4] + struct.pack("<i", amount)
    return save.with_docs({"data.a7s": filedb.patch(doc, [(span, value)])})
