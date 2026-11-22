"""Where the RTPC list of a Wwise object's node base starts, in the banks of every version the games ship.

``skip_before_rtpc(r, version)`` reads a node base from its start to its RTPC list (the last block of it): the head,
the properties, the positioning, the aux sends, the advanced settings and the state chunk. 117's layout (172) is
``bnkmusic.skip_before_rtpc``; the layouts of 112 (Anno 2205) and 132 to 135 (Anno 1800) are read here, each checked
against every Sound and container of the installed banks (``tests/soundedit/test_soundinstalled.py``). The tables of what each
byte is are in ``docs/sound/bank-objects.md`` "The node base's tail".
"""

from __future__ import annotations

from . import bnkgraph, bnkmusic
from .bnkmusic import CReader

OLD_ATTENUATION_BITS = 0x18  # 112: a byte and an attenuation id follow the positioning bits
OLD_PATH_BIT = 0x20  # 112: a path of vertices follows the attenuation
OVERRIDE, HAS_3D = 0x01, 0x02  # 132 to 135: the positioning's own bits, and a 3D byte follows when both are set
AUX_LIST = 0x08  # the aux sends: four bus ids follow
REFLECTIONS_FROM = 135  # the version that added the reflections bus after the aux sends
ADVANCED = 6  # bytes of advanced settings in every version
STATE_PROP = 3  # bytes a state property takes (132 to 135)
STATE = 8  # bytes a state of a group takes: its id and the instance


def skip_before_rtpc(reader: CReader, version: int) -> None:
    """Read a node base from its start to its RTPC list. ``ValueError`` where the bytes end early."""
    family = bnkgraph.family(version)
    if family == bnkgraph.NEW:
        bnkmusic.skip_before_rtpc(reader)
        return
    bnkgraph.skip_head(reader, family)
    reader.u8()  # the bit vector of the properties
    bnkgraph.read_prop_lists(reader)
    if family == bnkgraph.OLD:
        _positioning_old(reader)
    else:
        _positioning_mid(reader)
    if reader.u8() & AUX_LIST:
        reader.take(16)
    if version >= REFLECTIONS_FROM:
        reader.u32()
    reader.take(ADVANCED)
    if family == bnkgraph.OLD:
        _states_old(reader)
    else:
        _states_mid(reader)


def _positioning_old(reader: CReader) -> None:
    bits = reader.u8()
    if bits & OLD_ATTENUATION_BITS:
        reader.take(5)
    if bits & OLD_PATH_BIT:
        _path(reader)


def _positioning_mid(reader: CReader) -> None:
    bits = reader.u8()
    if bits & OVERRIDE and bits & HAS_3D and (reader.u8() >> 5) & 3:  # the 3D byte: a position that follows a path
        _path(reader)


def _path(reader: CReader) -> None:
    """The path of a moving position: mode, transition time, the vertices and the playlist of their offsets."""
    reader.u8()
    reader.s32()
    reader.take(16 * reader.u32())
    reader.take(20 * reader.u32())


def _states_old(reader: CReader) -> None:
    for _ in range(reader.u32()):
        reader.take(5)  # the group id and the sync type
        reader.take(STATE * reader.u16())


def _states_mid(reader: CReader) -> None:
    reader.take(STATE_PROP * reader.u8())
    for _ in range(reader.u8()):
        reader.take(5)
        reader.take(STATE * reader.u8())
