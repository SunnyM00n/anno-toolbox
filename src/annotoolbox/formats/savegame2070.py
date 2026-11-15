"""2070's savegames (``.sww``): the archive of ``ZZZZ`` packed binary tag trees, its money, and a write that keeps
every unchanged byte.

A save is a V2.0 RDA archive of ``game2.xml``, ``save.sww``, ``stats.xml`` and two pictures; the packing, the trees
and the 24 preamble bytes the writer copies are ``docs/save-editor.md``, "Anno 2070 in detail". ``read_bytes`` is the
entry; ``Save2070.to_bytes`` writes, ``read_money`` / ``set_money`` are the one edit.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import buildcache
from . import bintree, rda

PACKED = b"ZZZZ"
PREAMBLE_KEEP = slice(520, 544)  # a u32 20 and a 20-byte value not understood: copied from the original
CREDIT_UNIT = 1024  # ``Credits`` is fixed point with ten fraction bits
PACK_LEVEL = 2  # any of 2..5 writes the game's zlib header 78 5E
MONEY_MAX = (1 << 31) - 1


class Save2070Error(ValueError):
    pass


def is_packed(raw: bytes) -> bool:
    return raw[:4] == PACKED and len(raw) >= 8


def unpack(raw: bytes, name: str = "part") -> bytes:
    """A ``ZZZZ`` part inflated; the stream ends in a sync flush, so its end is not required."""
    (size,) = struct.unpack_from("<I", raw, 4)
    try:
        doc = zlib.decompressobj().decompress(raw[8:])
    except zlib.error as e:
        raise Save2070Error(f"{name}: {e}") from None
    if len(doc) != size:
        raise Save2070Error(f"{name}: {len(doc)} bytes inflated, the header says {size}")
    return doc


def pack(doc: bytes) -> bytes:
    """A part in the game's shape: ``ZZZZ``, the size, a zlib stream ending in a sync flush."""
    c = zlib.compressobj(PACK_LEVEL)
    return PACKED + struct.pack("<I", len(doc)) + c.compress(doc) + c.flush(zlib.Z_SYNC_FLUSH)


@dataclass(frozen=True)
class Save2070:
    """The archive's parts as stored (``raw``, in file order), the trees of the packed ones, the original file."""

    raw: dict[str, bytes]
    trees: dict[str, bintree.Tree]
    original: bytes
    changed: frozenset[str] = field(default_factory=frozenset)

    def with_trees(self, changed: dict[str, bintree.Tree]) -> Save2070:
        return Save2070(self.raw, {**self.trees, **changed}, self.original, self.changed | set(changed))

    def _part(self, name: str, raw: bytes) -> bytes:
        """The stored bytes of a part: as read, unless its tree no longer writes the document that was read."""
        if name not in self.changed:
            return raw
        doc = bintree.write(self.trees[name])
        return raw if doc == unpack(raw, name) else pack(doc)

    def to_bytes(self) -> bytes:
        """The original when no part's tree differs from what was read (also after an edit to the same value); else
        the archive anew with the changed parts packed, the preamble's kept bytes copied in."""
        parts = {n: self._part(n, raw) for n, raw in self.raw.items()}
        if parts == self.raw:
            return self.original
        out = bytearray(rda.write_v20(parts, 0, flags=0))
        out[PREAMBLE_KEEP] = self.original[PREAMBLE_KEEP]
        return bytes(out)


def read_bytes(data: bytes, name: str = "save") -> Save2070:
    try:
        archive = rda.RdaArchive.from_bytes(data, name)
        raw = archive.read_all()
    except rda.RdaError as e:
        raise Save2070Error(f"{name}: not a savegame ({e})") from None
    if "save.sww" not in raw:
        raise Save2070Error(f"{name}: no save.sww in the archive")
    if archive.version.name != "2.0":
        raise Save2070Error(f"{name}: an RDA {archive.version.name} archive, 2070 writes 2.0")
    trees = {}
    for part, body in raw.items():
        if is_packed(body):
            doc = unpack(body, part)
            if bintree.is_bintree(doc):
                trees[part] = bintree.read(doc)
    if "save.sww" not in trees:
        raise Save2070Error(f"{name}: save.sww is no packed binary tag tree")
    return Save2070(raw, trees, data)


def write(save: Save2070, path: str | Path) -> None:
    """Whole or not at all (a temporary file beside the target, read back, then renamed into place)."""
    path = Path(path)
    data = save.to_bytes()
    back = read_bytes(data, str(path))
    if any(bintree.write(back.trees[n]) != bintree.write(t) for n, t in save.trees.items()):
        raise Save2070Error(f"{path}: the written save does not read back the same")
    buildcache.write_atomic(path, data)


# The player and the money


def players(tree: bintree.Tree) -> list:
    """The player records of ``save.sww``'s ``m_GameEngine/m_Players`` that hold one (``hasValue`` 1)."""
    found = tree.path("SaveGame/m_GameEngine/m_Players")
    return [i for i in (found.children if found else []) if i.value("hasValue") == "1"]


def human_id(save: Save2070) -> int:
    """The player the save was made by (``m_CurPlayerID``)."""
    engine = save.trees["save.sww"].path("SaveGame/m_GameEngine")
    if engine is None:
        raise Save2070Error("save.sww has no m_GameEngine")
    return engine.int("m_CurPlayerID")


def _credits(save: Save2070, player: int):
    for p in players(save.trees["save.sww"]):
        if p.int("m_PlayerID", -1) == player:
            node = p.path("m_Resource/Credits")
            if node is not None and node.text is not None:
                return node
    raise Save2070Error(f"the save has no credits of player {player}")


def read_money(save: Save2070, player: int | None = None) -> int:
    """The player's whole credits (the stored value is credits times 1,024)."""
    node = _credits(save, human_id(save) if player is None else player)
    return int(node.text) // CREDIT_UNIT


def set_money(save: Save2070, amount: int, player: int | None = None) -> Save2070:
    """The player's credits; the human's also go into the load list's ``GoldAmount`` (``stats.xml``)."""
    player = human_id(save) if player is None else player
    if not 0 <= amount * CREDIT_UNIT <= MONEY_MAX:
        raise Save2070Error(f"money {amount} is outside 0..{MONEY_MAX // CREDIT_UNIT}")
    trees = {"save.sww": bintree.read(bintree.write(save.trees["save.sww"]))}  # a copy: the original stays
    edited = save.with_trees(trees)
    node = _credits(edited, player)
    node.text = str(amount * CREDIT_UNIT + int(node.text) % CREDIT_UNIT)
    if player == human_id(save) and "stats.xml" in save.trees:
        stats = bintree.read(bintree.write(save.trees["stats.xml"]))
        gold = stats.path("GameStats/GoldAmount")
        if gold is not None and gold.text is not None:
            gold.text = str(amount)
            edited = edited.with_trees({"stats.xml": stats})
    return edited
