"""The state manager (``STMG`` chunk) of a Wwise bank's Init file: its state groups and the switch groups a game
parameter drives, read and written (versions 112, 132 to 135, 172).

``read(data, version)`` gives a ``SStateManager``; ``render`` writes it back byte for byte. What is in the chunk, the
three layouts and what a group is: ``docs/sound/bank-objects.md`` "State and switch groups".
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import bnkgraph
from .bnkmusic import CReader

PARAMETER_SIZE = 21  # a game parameter: its id and 17 bytes (default, interpolation, attack, release)


@dataclass
class SStateGroup:
    """A state group: its id, the default transition time (ms) and the custom transitions (from state, to state, ms)."""

    id: int
    transition: int = 0
    custom: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class SSwitchGroup:
    """A switch group a game parameter drives: the parameter, a type byte and the points (parameter value, switch id,
    interpolation)."""

    id: int
    parameter: int
    kind: int = 0
    points: list[tuple[float, int, int]] = field(default_factory=list)


@dataclass
class SStateManager:
    head: bytes
    states: list[SStateGroup]
    switches: list[SSwitchGroup]
    parameters: bytes  # the game parameters, with their count, and the tail: kept as read


def read(data: bytes, version: int) -> SStateManager:
    """The chunk; ``ValueError`` when the game parameters and the tail do not fill what follows the switch groups."""
    layout = bnkgraph.layout(version)
    reader = CReader(data)
    head = reader.take(layout.stmg_head)
    states = []
    for _ in range(reader.u32()):
        ident, transition = reader.u32(), reader.u32()
        states.append(
            SStateGroup(ident, transition, [(reader.u32(), reader.u32(), reader.u32()) for _ in range(reader.u32())])
        )
    switches = []
    for _ in range(reader.u32()):
        ident, parameter, kind = reader.u32(), reader.u32(), reader.u8()
        switches.append(
            SSwitchGroup(
                ident, parameter, kind, [(reader.f32(), reader.u32(), reader.u32()) for _ in range(reader.u32())]
            )
        )
    rest = data[reader.m_pos :]
    if len(rest) < 4 or len(rest) != 4 + struct.unpack_from("<I", rest)[0] * PARAMETER_SIZE + layout.stmg_tail:
        raise ValueError("the game parameters do not fill the state manager")
    return SStateManager(head, states, switches, rest)


def render(state_manager: SStateManager) -> bytes:
    out = state_manager.head + struct.pack("<I", len(state_manager.states))
    for group in state_manager.states:
        out += struct.pack("<III", group.id, group.transition, len(group.custom))
        out += b"".join(struct.pack("<III", *custom) for custom in group.custom)
    out += struct.pack("<I", len(state_manager.switches))
    for switch in state_manager.switches:
        out += struct.pack("<IIBI", switch.id, switch.parameter, switch.kind, len(switch.points))
        out += b"".join(struct.pack("<fII", *point) for point in switch.points)
    return out + state_manager.parameters
