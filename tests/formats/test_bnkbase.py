"""``formats/bnkbase``: a node base of the older banks (112, 132 to 135) read from its start to its RTPC list, over
synthetic nodes in every shape the shipped banks have (positioning with an attenuation or a path, aux sends, state
groups, the reflections bus of 135). The installed banks are read in ``test_soundinstalled``."""

from __future__ import annotations

import struct

import pytest

from annotoolbox.formats import bnkbase
from annotoolbox.formats.bnkmusic import CReader

OLD, MID_134, MID_135 = 112, 134, 135
ADVANCED = bytes([0, 1, 0, 0, 0, 0])
PATH = bytes([1]) + struct.pack("<iI", 1000, 2) + bytes(32) + struct.pack("<I", 1) + bytes(20)  # 2 vertices, 1 item
STATE_GROUP = struct.pack("<I", 7) + bytes([0])


def head(parent: int = 5) -> bytes:
    """The node base up to its positioning in the older layouts: no effect, the attachment byte, the bus, the parent,
    the bit vector and two empty property lists."""
    return bytes([0, 0, 0]) + struct.pack("<II", 0, parent) + bytes([0, 0, 0])


def old_tail(positioning: bytes, aux: bytes = bytes(1), groups: int = 0) -> bytes:
    states = struct.pack("<I", groups) + b"".join(STATE_GROUP + struct.pack("<H", 2) + bytes(16) for _ in range(groups))
    return positioning + aux + ADVANCED + states


def mid_tail(version: int, positioning: bytes, aux: bytes = bytes(1), props: int = 0, groups: int = 0) -> bytes:
    reflections = bytes(4) if version >= 135 else b""
    states = bytes([props]) + bytes(3 * props) + bytes([groups])
    states += b"".join(STATE_GROUP + bytes([1]) + bytes(8) for _ in range(groups))
    return positioning + aux + reflections + ADVANCED + states


RTPC = struct.pack("<H", 1) + struct.pack("<IBBBIBH", 9, 0, 2, 0, 3, 0, 1) + bytes(12)


def end_of_base(version: int, node: bytes) -> int:
    """Where ``skip_before_rtpc`` stops in ``node`` followed by an RTPC list."""
    r = CReader(node + RTPC)
    bnkbase.skip_before_rtpc(r, version)
    return r.m_pos


@pytest.mark.parametrize(
    "positioning",
    (
        bytes([0xC0]),  # does not override its parent
        bytes([0xC3]),  # overrides, no extra bytes
        bytes([0xD9, 1]) + struct.pack("<I", 123),  # an attenuation
        bytes([0x79, 0]) + struct.pack("<I", 0) + PATH,  # an attenuation slot and a path
    ),
)
def test_an_older_node_base_of_112_reads_to_its_rtpc_list(positioning):
    node = head() + old_tail(positioning)
    assert end_of_base(OLD, node) == len(node)


def test_112_reads_its_aux_list_and_state_groups():
    node = head() + old_tail(bytes([0xC0]), bytes([0x08]) + bytes(16), groups=2)
    assert end_of_base(OLD, node) == len(node)


@pytest.mark.parametrize("version", (MID_134, MID_135))
@pytest.mark.parametrize(
    "positioning",
    (
        bytes([0]),
        bytes([5]),  # overrides without a 3D byte
        bytes([3, 0x0A]),  # a 3D byte that follows no path
        bytes([0x23, 0x4A]) + PATH,  # a 3D position that follows a path
    ),
)
def test_a_node_base_of_132_to_135_reads_to_its_rtpc_list(version, positioning):
    node = head() + mid_tail(version, positioning)
    assert end_of_base(version, node) == len(node)


@pytest.mark.parametrize("version", (MID_134, MID_135))
def test_135_has_the_reflections_bus_and_134_does_not_and_both_read_aux_lists_and_state_props(version):
    node = head() + mid_tail(version, bytes([0]), bytes([0x08]) + bytes(16), props=5, groups=1)
    assert end_of_base(version, node) == len(node)
    other = MID_134 if version == MID_135 else MID_135
    assert end_of_base(other, node) != len(node)  # the other version's layout would read it differently


def test_a_node_base_that_ends_early_is_refused():
    node = head() + old_tail(bytes([0xC0]))
    with pytest.raises(ValueError):
        bnkbase.skip_before_rtpc(CReader(node[:-5]), OLD)
