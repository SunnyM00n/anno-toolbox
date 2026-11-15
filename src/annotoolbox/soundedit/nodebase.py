"""The properties of a Sound or a container (volume, pitch, their random ranges ...), read and written in place.

``read_props(obj, version)`` finds the property lists of an object's node base (the layout of the node base's head per
bank version is ``formats/bnkgraph.skip_head``; ``docs/sound/bank-objects.md``) and ``PropBlock`` changes them;
``with_props`` returns the object with the block put back. Nothing else of the object is touched, so an object whose
block is not changed keeps its bytes. The property ids differ between the versions: ``prop_id`` names them per layout
(``bnkgraph.layout``; read from the shipped banks of the three games, ``docs/sound/bank-objects.md`` "Properties").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import bnkgraph
from ..formats.bnk import HircObject
from ..formats.bnkmusic import Reader
from ..infra.i18n import tr


class PropError(ValueError):
    """An object has no node base this module reads."""


def name_of(version: int, prop: int) -> str:
    """The display name of a property id (a number for the ones not named)."""
    return bnkgraph.layout(version).prop_names.get(prop, f"property {prop}")


def prop_id(version: int, name: str) -> int:
    """The id of a named property (``volume``, ``pitch``, ``low_pass``) in a bank of ``version``."""
    return bnkgraph.layout(version).prop_ids[name]


@dataclass
class PropBlock:
    """The two property lists of a node base: single values (4 bytes) and random ranges (two floats), each by id, and
    where they sit in the payload they were read from."""

    start: int
    end: int
    props: dict[int, bytes] = field(default_factory=dict)
    ranged: dict[int, bytes] = field(default_factory=dict)

    def float(self, prop: int, default: float = 0.0) -> float:
        return struct.unpack("<f", self.props[prop])[0] if prop in self.props else default

    def range(self, prop: int) -> tuple[float, float] | None:
        return struct.unpack("<ff", self.ranged[prop]) if prop in self.ranged else None

    def set_float(self, prop: int, value: float | None) -> None:
        """Set a property (``None`` removes it, which is the default of the engine)."""
        if value is None:
            self.props.pop(prop, None)
        else:
            self.props[prop] = struct.pack("<f", value)

    def set_range(self, prop: int, span: tuple[float, float] | None) -> None:
        if span is None:
            self.ranged.pop(prop, None)
        else:
            self.ranged[prop] = struct.pack("<ff", *span)

    def render(self) -> bytes:
        out = bytes([len(self.props)]) + bytes(self.props) + b"".join(self.props.values())
        return out + bytes([len(self.ranged)]) + bytes(self.ranged) + b"".join(self.ranged.values())


def read_props(obj: HircObject, version: int) -> PropBlock:
    """The property lists of an object."""
    try:
        r = Reader(obj.payload)
        r.pos = bnkgraph.base_start(obj, version)
        bnkgraph.skip_head(r, bnkgraph.family(version))
        r.u8()  # the bit vector of the properties that follow
        start = r.pos
        props, ranged = bnkgraph.read_prop_lists(r)
    except (ValueError, struct.error) as exc:
        raise PropError(
            tr("{type_name} {id}: the node base does not read ({exc})", type_name=obj.type_name, id=obj.id, exc=exc)
        ) from exc
    return PropBlock(start, r.pos, props, ranged)


def with_props(obj: HircObject, block: PropBlock) -> HircObject:
    """``obj`` with the property lists of ``block`` in place of the ones it was read with."""
    payload = obj.payload[: block.start] + block.render() + obj.payload[block.end :]
    return HircObject(obj.type, obj.id, payload)
