"""Where the RTPC list of a Wwise object's node base starts, in the banks of every version the games ship.

``skip_before_rtpc(r, version)`` reads a node base from its start to its RTPC list (the last block of it): the head,
the properties, the positioning, the aux sends, the advanced settings and the state chunk. 117's layout (172) is
``bnkmusic.skip_before_rtpc``; the layouts of 112 (Anno 2205) and 132 to 135 (Anno 1800) are read here, each checked
against every Sound and container of the installed banks (``tests/test_soundinstalled.py``). The tables of what each
byte is are in ``docs/sound/bank-objects.md`` "The node base's tail".
"""

from __future__ import annotations

from . import bnkgraph, bnkmusic
from .bnkmusic import Reader

OLD_ATTENUATION_BITS = 0x18  # 112: a byte and an attenuation id follow the positioning bits
OLD_PATH_BIT = 0x20  # 112: a path of vertices follows the attenuation
OVERRIDE, HAS_3D = 0x01, 0x02  # 132 to 135: the positioning's own bits, and a 3D byte follows when both are set
AUX_LIST = 0x08  # the aux sends: four bus ids follow
REFLECTIONS_FROM = 135  # the version that added the reflections bus after the aux sends
ADVANCED = 6  # bytes of advanced settings in every version
STATE_PROP = 3  # bytes a state property takes (132 to 135)
STATE = 8  # bytes a state of a group takes: its id and the instance


def skip_before_rtpc(r: Reader, version: int) -> None:
    """Read a node base from its start to its RTPC list. ``ValueError`` where the bytes end early."""
    fam = bnkgraph.family(version)
    if fam == bnkgraph.NEW:
        bnkmusic.skip_before_rtpc(r)
        return
    bnkgraph.skip_head(r, fam)
    r.u8()  # the bit vector of the properties
    bnkgraph.read_prop_lists(r)
    if fam == bnkgraph.OLD:
        _positioning_old(r)
    else:
        _positioning_mid(r)
    if r.u8() & AUX_LIST:
        r.take(16)
    if version >= REFLECTIONS_FROM:
        r.u32()
    r.take(ADVANCED)
    if fam == bnkgraph.OLD:
        _states_old(r)
    else:
        _states_mid(r)


def _positioning_old(r: Reader) -> None:
    bits = r.u8()
    if bits & OLD_ATTENUATION_BITS:
        r.take(5)
    if bits & OLD_PATH_BIT:
        _path(r)


def _positioning_mid(r: Reader) -> None:
    bits = r.u8()
    if bits & OVERRIDE and bits & HAS_3D and (r.u8() >> 5) & 3:  # the 3D byte: a position that follows a path
        _path(r)


def _path(r: Reader) -> None:
    """The path of a moving position: mode, transition time, the vertices and the playlist of their offsets."""
    r.u8()
    r.s32()
    r.take(16 * r.u32())
    r.take(20 * r.u32())


def _states_old(r: Reader) -> None:
    for _ in range(r.u32()):
        r.take(5)  # the group id and the sync type
        r.take(STATE * r.u16())


def _states_mid(r: Reader) -> None:
    r.take(STATE_PROP * r.u8())
    for _ in range(r.u8()):
        r.take(5)
        r.take(STATE * r.u8())
