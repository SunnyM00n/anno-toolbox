"""The properties of a Sound or a container (volume, pitch, their random ranges ...), read and written in place.

``read_props(obj, version)`` finds the property lists of an object's node base (the layout of the node base's head per
bank version is ``formats/bnkgraph.skip_head``; ``docs/sound/bank-objects.md``) and ``CPropBlock`` changes them;
``with_props`` returns the object with the block put back. Nothing else of the object is touched, so an object whose
block is not changed keeps its bytes. The property ids differ between the versions: ``prop_id`` names them per layout
(``bnkgraph.layout``; read from the shipped banks of the three games, ``docs/sound/bank-objects.md`` "Properties").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import bnkgraph
from ..formats.bnk import SHircObject
from ..formats.bnkmusic import CReader
from ..infra.i18n import tr


class CPropError(ValueError):
    """An object has no node base this module reads."""


def name_of(version: int, prop: int) -> str:
    """The display name of a property id (a number for the ones not named)."""
    return bnkgraph.layout(version).prop_names.get(prop, f"property {prop}")


def prop_id(version: int, name: str) -> int:
    """The id of a named property (``volume``, ``pitch``, ``low_pass``) in a bank of ``version``."""
    return bnkgraph.layout(version).prop_ids[name]


@dataclass
class CPropBlock:
    """The two property lists of a node base: single values (4 bytes) and random ranges (two floats), each by id, and
    where they sit in the payload they were read from."""

    m_start: int
    m_end: int
    m_props: dict[int, bytes] = field(default_factory=dict)
    m_ranged: dict[int, bytes] = field(default_factory=dict)

    def float(self, prop: int, default: float = 0.0) -> float:
        return struct.unpack("<f", self.m_props[prop])[0] if prop in self.m_props else default

    def range(self, prop: int) -> tuple[float, float] | None:
        return struct.unpack("<ff", self.m_ranged[prop]) if prop in self.m_ranged else None

    def set_float(self, prop: int, value: float | None) -> None:
        """Set a property (``None`` removes it, which is the default of the engine)."""
        if value is None:
            self.m_props.pop(prop, None)
        else:
            self.m_props[prop] = struct.pack("<f", value)

    def set_range(self, prop: int, span: tuple[float, float] | None) -> None:
        if span is None:
            self.m_ranged.pop(prop, None)
        else:
            self.m_ranged[prop] = struct.pack("<ff", *span)

    def render(self) -> bytes:
        out = bytes([len(self.m_props)]) + bytes(self.m_props) + b"".join(self.m_props.values())
        return out + bytes([len(self.m_ranged)]) + bytes(self.m_ranged) + b"".join(self.m_ranged.values())


def read_props(hirc_object: SHircObject, version: int) -> CPropBlock:
    """The property lists of an object."""
    try:
        reader = CReader(hirc_object.payload)
        reader.m_pos = bnkgraph.base_start(hirc_object, version)
        bnkgraph.skip_head(reader, bnkgraph.family(version))
        reader.u8()  # the bit vector of the properties that follow
        start = reader.m_pos
        props, ranged = bnkgraph.read_prop_lists(reader)
    except (ValueError, struct.error) as exc:
        raise CPropError(
            tr("sound.node_base_not_read", type_name=hirc_object.type_name, id=hirc_object.id, exc=exc)
        ) from exc
    return CPropBlock(start, reader.m_pos, props, ranged)


def with_props(hirc_object: SHircObject, block: CPropBlock) -> SHircObject:
    """``obj`` with the property lists of ``block`` in place of the ones it was read with."""
    payload = hirc_object.payload[: block.m_start] + block.render() + hirc_object.payload[block.m_end :]
    return SHircObject(hirc_object.type, hirc_object.id, payload)
