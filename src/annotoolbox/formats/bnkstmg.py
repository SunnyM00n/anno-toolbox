"""The state manager (``STMG`` chunk) of a Wwise bank's Init file: its state groups and the switch groups a game
parameter drives, read and written (versions 112, 132 to 135, 172).

``read(data, version)`` gives a ``StateManager``; ``render`` writes it back byte for byte. What is in the chunk, the
three layouts and what a group is: ``docs/sound/bank-objects.md`` "State and switch groups".
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import bnkgraph
from .bnkmusic import Reader

PARAMETER_SIZE = 21  # a game parameter: its id and 17 bytes (default, interpolation, attack, release)


@dataclass
class StateGroup:
    """A state group: its id, the default transition time (ms) and the custom transitions (from state, to state, ms)."""

    id: int
    transition: int = 0
    custom: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class SwitchGroup:
    """A switch group a game parameter drives: the parameter, a type byte and the points (parameter value, switch id,
    interpolation)."""

    id: int
    parameter: int
    kind: int = 0
    points: list[tuple[float, int, int]] = field(default_factory=list)


@dataclass
class StateManager:
    head: bytes
    states: list[StateGroup]
    switches: list[SwitchGroup]
    parameters: bytes  # the game parameters, with their count, and the tail: kept as read


def read(data: bytes, version: int) -> StateManager:
    """The chunk; ``ValueError`` when the game parameters and the tail do not fill what follows the switch groups."""
    layout = bnkgraph.layout(version)
    r = Reader(data)
    head = r.take(layout.stmg_head)
    states = []
    for _ in range(r.u32()):
        ident, transition = r.u32(), r.u32()
        states.append(StateGroup(ident, transition, [(r.u32(), r.u32(), r.u32()) for _ in range(r.u32())]))
    switches = []
    for _ in range(r.u32()):
        ident, parameter, kind = r.u32(), r.u32(), r.u8()
        switches.append(SwitchGroup(ident, parameter, kind, [(r.f32(), r.u32(), r.u32()) for _ in range(r.u32())]))
    rest = data[r.pos :]
    if len(rest) < 4 or len(rest) != 4 + struct.unpack_from("<I", rest)[0] * PARAMETER_SIZE + layout.stmg_tail:
        raise ValueError("the game parameters do not fill the state manager")
    return StateManager(head, states, switches, rest)


def render(sm: StateManager) -> bytes:
    out = sm.head + struct.pack("<I", len(sm.states))
    for g in sm.states:
        out += struct.pack("<III", g.id, g.transition, len(g.custom))
        out += b"".join(struct.pack("<III", *c) for c in g.custom)
    out += struct.pack("<I", len(sm.switches))
    for s in sm.switches:
        out += struct.pack("<IIBI", s.id, s.parameter, s.kind, len(s.points))
        out += b"".join(struct.pack("<fII", *p) for p in s.points)
    return out + sm.parameters
