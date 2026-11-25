"""A frame's native object draws (``objectsnative``): a part's level in one pass as one group of draws made once
(``CLevelDraws``, a frame's ``CNativeGroup``: only its slots and instance count are new each frame), a single run where
a draw needs its own box or viewport (``CNativeRun``: the transparent pass, a cut-out's cascade), and the instance
buffers on the device (``CInstanceBuffers``: the store's records and bones written only where they changed).
docs/rendering/performance.md section 4, item 1.
"""

from __future__ import annotations

import numpy as np

from .gameinstances import CULL_ARGS_WORDS, CULL_WORDS, KINDS, SLOT_BUFFER, SDrawnSet

# the frame's instance buffers by the names the shaders read them (``gameinstances.KINDS``)
INSTANCE_NAMES = {name: kind for kind, (_stride, names) in KINDS.items() for name in names}
# the GPU culling's inputs (``gameinstances.cull_objects``): their element's bytes and the names model_culler.fx reads
CULL_STRIDES = {"cull": CULL_WORDS * 4, "cull_args": CULL_ARGS_WORDS * 4}
CULL_NAMES = {"cInputObjects": "cull", "cInputDrawArgs": "cull_args", "cInstanceDataBuffer": "records"}


class CNativeRun:
    """One draw of a native part this frame: a role, a mesh, its slot (``cbModel``'s byte offset) and how many
    instances (a shadow draw: its culled words); in the transparent pass ordered by its box as the current path's
    runs and recorded by the backend in its place (``native``: ``nativepass.draw_models``); ``viewport``: a share
    of the target (x, y from the top left, width, height), None the whole."""

    __slots__ = ("m_part", "m_role", "m_mesh", "m_slot", "m_drawn", "m_words", "m_viewport")
    m_native = True
    m_grouped = False
    m_world = np.identity(4)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return self.m_drawn.box()

    def __init__(self, part, role: str, mesh, slot: int, drawn: SDrawnSet, words=None, viewport=None) -> None:
        self.m_part, self.m_role, self.m_mesh, self.m_slot, self.m_drawn = part, role, mesh, slot, drawn
        self.m_words, self.m_viewport = words, viewport

    @property
    def m_instances(self) -> int:
        return len(self.m_words) if self.m_words is not None else self.m_drawn.count


class CLevelDraws:
    """A part's draws at one level in one pass, made once: its (role, mesh) pairs in order, each one's joints (the
    words' slot they share), the roles among them; per pass the module's draws (``m_items``), kept while the pass's
    sets for those roles are the same objects."""

    __slots__ = ("m_pairs", "m_joints", "m_roles", "m_is_uniform", "m_items", "m_culled")

    def __init__(self, pairs: list[tuple[str, object]], joints: list[int]) -> None:
        self.m_pairs, self.m_joints = tuple(pairs), tuple(joints)
        self.m_roles = tuple(dict.fromkeys(role for role, _mesh in pairs))
        self.m_is_uniform = len(set(joints)) <= 1  # one slot for every draw
        self.m_items: dict[str, tuple] = {}  # pass -> (the roles' sets, the module's draws)
        self.m_culled: tuple | None = None  # per draw (mesh, joints, box, index count): ``culled``, made once

    def culled(self) -> tuple | None:
        """Per draw what the GPU's caster culling takes of it (its mesh, joints, box in its own space and index
        count), made once; None while a mesh's box is not known (``CMesh.box``, kept on the mesh before its bytes go
        to the device): its casters are then culled on the CPU."""
        if self.m_culled is None and all(mesh.m_box is not None or mesh.m_data for _role, mesh in self.m_pairs):
            self.m_culled = tuple((mesh, joints, mesh.box(), len(mesh.m_indices))
                                  for (_role, mesh), joints in zip(self.m_pairs, self.m_joints, strict=True))  # fmt: skip
        return self.m_culled

    def slots(self, slot_of: dict[int, int]) -> list[int]:
        """Each draw's slot from the slots by joints (``slot_of``)."""
        if self.m_is_uniform:
            return [next(iter(slot_of.values()))] * len(self.m_pairs) if self.m_pairs else []
        return [slot_of[joints] for joints in self.m_joints]


class CNativeGroup:
    """A frame's draws of one ``CLevelDraws``: their slots and the instances each draws (a shadow group: its culled
    words' count); recorded in one go (``CNativeObjects.record``)."""

    __slots__ = ("m_part", "m_draws", "m_slots", "m_count")
    m_native = True
    m_grouped = True
    m_viewport = None

    def __init__(self, part, draws: CLevelDraws, slots: list[int], count: int) -> None:
        self.m_part, self.m_draws, self.m_slots, self.m_count = part, draws, slots, count

    def __len__(self) -> int:
        return len(self.m_draws.m_pairs)


class CCulledGroup:
    """A frame's shadow draws of one ``CLevelDraws`` whose casters the GPU culls (``shadowcullnative``): each draw's
    slot (where the words the GPU writes start) and its arguments' index; recorded by indirect draws."""

    __slots__ = ("m_part", "m_draws", "m_slots", "m_args")
    m_native = True
    m_grouped = True
    m_viewport = None
    m_count = 0  # the instances are the GPU's

    def __init__(self, part, draws: CLevelDraws, slots: list[int], args: list[int]) -> None:
        self.m_part, self.m_draws, self.m_slots, self.m_args = part, draws, slots, args

    def __len__(self) -> int:
        return len(self.m_draws.m_pairs)

    @staticmethod
    def of(part, draws: CLevelDraws, drawn: SDrawnSet, frame) -> CCulledGroup | None:
        """The draws of ``drawn`` placed for the GPU's culling (``CFrameInstances.place_culled``), None where a mesh's
        box is not known."""
        culled = draws.culled()
        if culled is None:
            return None
        places, local = drawn.places, drawn.local
        placed = [
            frame.place_culled(places, local, joints, mesh, box, indices) for mesh, joints, box, indices in culled
        ]
        return CCulledGroup(part, draws, [slot for slot, _arg in placed], [arg for _slot, arg in placed])


class CInstanceBuffers:
    """The frame's instance buffers as the module's structured buffers: its words (``gameinstances.CFrameInstances
    .gathered``) written when the frame's instances are new, the store's records and bones only where they changed
    (``kept_rows``), and the draws' slots as one constant buffer whose slot a draw's ``cbModel`` root view points at;
    grown to the next power of two (made again, every row written: the sets follow by their ids)."""

    def __init__(self, device) -> None:
        self.m_device = device
        self.m_buffers: dict[str, object] = {}
        self.m_serial = None
        self.m_cull_counts = (0, 0, 0)  # the frame's cull objects, culled draws and the words they may write
        self.m_let_go: list = []  # the frame grew out of these since take_let_go; their sets go too

    def _fit(self, kind: str, size_bytes: int, usage: str, stride: int) -> tuple[object, bool]:
        """(the buffer of ``kind`` for ``size_bytes``, whether it was made anew)."""
        buffer = self.m_buffers.get(kind)
        need = max(1 << 16, 1 << max(0, size_bytes - 1).bit_length())
        if buffer is not None and buffer.size >= size_bytes:
            return buffer, False
        if buffer is not None:
            buffer.release()
            self.m_let_go.append(buffer)
        buffer = self.m_buffers[kind] = self.m_device.buffer(need, usage, stride=stride, name=f"frame {kind}")
        return buffer, True

    def send(self, command_list, frame) -> None:
        if frame.m_serial == self.m_serial and self.m_buffers:
            return
        self.m_serial = frame.m_serial
        got = frame.gathered()
        for kind, stride in (*((kind, KINDS[kind][0]) for kind in ("words", "culled")), *CULL_STRIDES.items()):
            buffer, _made = self._fit(kind, len(got[kind]), "structured", stride)
            if got[kind]:
                command_list.write_buffer(buffer, got[kind])
        self.m_cull_counts = (len(got["cull"]) // CULL_STRIDES["cull"], len(got["cull_args"]) // CULL_STRIDES["cull_args"],
                              frame.m_cull_outputs)  # fmt: skip
        for kind, rows, top, ranges in frame.kept_rows("native"):
            stride = KINDS[kind][0]
            buffer, made = self._fit(kind, top * stride, "structured", stride)
            for start, stop in [(0, top)] if made and top else ranges:
                command_list.write_buffer(buffer, rows[start:stop].tobytes(), start * stride)
        buffer, _made = self._fit("slots", len(got["slots"]), "constant", 0)
        if got["slots"]:
            command_list.write_buffer(buffer, got["slots"])

    def named(self) -> dict:
        """The buffers by the shaders' names, and ``cbModel``'s slots under its own."""
        if not self.m_buffers:
            raise RuntimeError("the frame's instances were not sent")
        return {
            **{name: self.m_buffers[kind] for name, kind in INSTANCE_NAMES.items()},
            SLOT_BUFFER: self.m_buffers["slots"],
        }

    def cull_named(self) -> dict:
        """What the GPU culling reads by model_culler.fx's names: the cull objects, the draws' arguments, the records."""
        if not self.m_buffers:
            raise RuntimeError("the frame's instances were not sent")
        return {name: self.m_buffers[kind] for name, kind in CULL_NAMES.items()}

    def take_let_go(self) -> list:
        """Return the buffers let go since the last call: a set kept with one is never asked for again."""
        gone, self.m_let_go = self.m_let_go, []
        return gone

    def release(self) -> None:
        for buffer in self.m_buffers.values():
            buffer.release()
        self.m_buffers, self.m_serial, self.m_cull_counts, self.m_let_go = {}, None, (0, 0, 0), []
