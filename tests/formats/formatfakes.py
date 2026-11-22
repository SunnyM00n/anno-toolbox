"""Builders of binary and text files of the file formats: Wwise banks, CSL scripts, Granny files, RDA archives and
baked particles."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

from annotoolbox.formats import bnk, bnkgraph, bnksfx, grannyoodle, rda
from annotoolbox.formats import grannyread as gr
from annotoolbox.formats import grannywrite as gw
from annotoolbox.formats.rda import FLAG_COMPRESSED, FLAG_CONTIGUOUS, FLAG_ENCRYPTED, V2_0
from annotoolbox.formats.rdp117 import SBakedParticle, SRdpMaterial, quantise

OLD_BANK, MID_BANK, NEW_BANK = 112, 135, 172


def node_base(version: int, parent: int, fx: bool = False, override: bool = False) -> bytes:
    """A node base with no properties: an effect when ``fx``, the positioning with its override bit set (and the
    five bytes that follow) when ``override`` (older layouts only)."""
    new = version >= 140
    head = bytes([0, 1, 0]) + bytes([0, 0xAA, 0xBB, 0xCC, 0xDD, 0, 0]) if fx else bytes([0, 0])
    head += bytes(2) if new else bytes(1)  # the metadata list (172), the attachment flag
    head += struct.pack("<I", 0) + struct.pack("<I", parent) + bytes(1) + bytes(2)  # bus, parent, bit vector, props
    positioning = bytes([0xD9, 1, 0, 0, 0, 0]) if override and not new else bytes([0xC0])
    tail = bytes(1) + (struct.pack("<I", 0) if version >= 128 else b"") + bytes(6)
    states = bytes(2) if version >= 128 else bytes(4)
    return head + positioning + tail + states + bytes(2)


def sound_object(version: int, oid: int, media: int, parent: int, stream: int = 2, **kw) -> bnk.SHircObject:
    plugin = bnk.VORBIS_PLUGIN
    if version >= 140:
        source = struct.pack("<IBQIB", plugin, stream, media | 7 << 32, 0, 0)
    elif version >= 128:
        source = struct.pack("<IBIIB", plugin, stream, media, 0, 0)
    else:
        offset = struct.pack("<I", 0) if stream != 2 else b""
        source = struct.pack("<IBII", plugin, stream, media, media) + offset + struct.pack("<IB", 0, 0)
    return bnk.SHircObject(2, oid, source + node_base(version, parent, **kw))


def container(version: int, kind: int, oid: int, parent: int, children: list[int], gap: bytes = b"") -> bnk.SHircObject:
    ids = struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    return bnk.SHircObject(kind, oid, node_base(version, parent) + gap + ids)


def switch_container(version: int, oid: int, group: int, default: int, branches: dict[int, list[int]], state=False):
    kids = [c for nodes in branches.values() for c in nodes]
    gap = struct.pack("<BIIB", int(state), group, default, 0)
    table = struct.pack("<I", len(branches))
    for value, nodes in branches.items():
        table += struct.pack("<II", value, len(nodes)) + b"".join(struct.pack("<I", n) for n in nodes)
    obj = container(version, bnkgraph.SWITCH, oid, 0, kids, gap)
    return bnk.SHircObject(obj.type, oid, obj.payload + table + struct.pack("<I", 0))


def event(version: int, oid: int, actions: list[int]) -> bnk.SHircObject:
    ids = b"".join(struct.pack("<I", a) for a in actions)
    if version >= 140:
        return bnk.SHircObject(4, oid, bytes(7) + bytes([len(actions)]) + ids)
    count = struct.pack("<B" if version >= 128 else "<I", len(actions))
    return bnk.SHircObject(4, oid, count + ids)


def action(oid: int, kind: int, target: int, group: int = 0, value: int = 0) -> bnk.SHircObject:
    head = struct.pack("<HIBBB", kind, target, 0, 0, 0)
    return bnk.SHircObject(
        3, oid, head + (struct.pack("<II", group, value) if kind >> 8 in (0x12, 0x19) else b"\x04" + bytes(4))
    )


LEAF = 0xFFFFFFFF


def assert_a_playlist_tree(nodes) -> None:
    """What every playlist of every installed bank (112, 135, 172) satisfies, the evidence for the fields' meaning."""
    pending = [1]  # pre-order: the first node is the root, each node's children follow it
    for n in nodes:
        assert pending, "a node after the tree is complete"
        pending[-1] -= 1
        while pending and pending[-1] == 0:
            pending.pop()
        if n.num_children:
            pending.append(n.num_children)
        if n.rs_type == LEAF:  # a segment: its own, no children, the flag and the avoid count unused
            assert n.segment_id and not n.num_children and not n.use_weight and not n.avoid_repeat and n.loop in (0, 1)
        else:  # a group: no segment, 0 and 1 are sequences, 2 and 3 random; the avoid count fits its children
            assert not n.segment_id and n.rs_type in (0, 1, 2, 3) and n.use_weight == 1
            assert n.loop in (0, 1) and 1 <= n.avoid_repeat <= max(n.num_children, 1)
        assert n.min_loop == n.max_loop == 0 and 10000 <= n.weight <= 100000
    assert not pending and len({n.item_id for n in nodes}) == len(nodes)


PLAIN_TAIL = (
    bytes(6) + b"\0\1" + bytes(8)
)  # positioning, aux, reflections, advanced settings (virtual queue 1), state, rtpc


MIXER_TAIL = b"\3\x0a" + bytes(5) + b"\0\1" + bytes(8)  # 3D positioning with an emitter


def sfx_node(
    bus: int, parent: int, props: dict[int, bytes], tail: bytes, ranged: dict[int, bytes] | None = None
) -> bytes:
    return bnksfx.CNodeBase(bytes(4), bus, parent, 0, props, ranged or {}, tail).render()


def attenuation_payload(points=((0.0, 0.0), (4.0, -0.5), (20.0, -1.0))) -> bytes:
    """An Attenuation shaped as the shipped ones: a 26-byte head, one dB curve over distance, no game parameters."""
    curve = struct.pack("<BH", 2, len(points)) + b"".join(struct.pack("<ffI", x, y, 4) for x, y in points)
    return bytes([1]) + bytes(4) + bytes(21) + b"\1" + curve + b"\0\0"


ATTENUATION = attenuation_payload()


def donor_bank() -> bnk.SSoundBank:
    """A bank holding one donor of each kind, with ids that are not the shipped ones (found by shape)."""
    att = bnk.SHircObject(bnksfx.ATTENUATION, 100, ATTENUATION)
    mixer = bnk.SHircObject(
        bnksfx.MIXER,
        200,
        sfx_node(1213198520, 55, {bnksfx.PROP_ATTENUATION: struct.pack("<I", 100)}, MIXER_TAIL)
        + struct.pack("<II", 1, 300),
    )
    source = struct.pack("<IBQIB", 0x40001, 2, 1234, 2087, 0)
    sound = bnk.SHircObject(bnksfx.SOUND, 300, source + sfx_node(0, 200, {74: bytes(4)}, PLAIN_TAIL))
    ranseq_params = struct.pack("<HHHfffHBBBB", 1, 0, 0, 1000.0, 0.0, 0.0, 1, 0, 0, 0, 18)
    ranseq = bnk.SHircObject(
        bnksfx.RANSEQ,
        400,
        sfx_node(0, 200, {}, PLAIN_TAIL, {1: bytes(8)})
        + ranseq_params
        + struct.pack("<II", 1, 300)
        + struct.pack("<HII", 1, 300, 50000),
    )
    header = struct.pack("<III", 172, 7, 0x12345678) + struct.pack("<II", 16, 9136) + bytes(4) + bytes(16)
    return bnk.SSoundBank(
        chunks=[bnk.SChunk(b"BKHD", header), bnk.SChunk(b"HIRC", b"")], hirc=[att, sound, ranseq, mixer]
    )


WORK_API = (
    "<globalvars>"
    + "".join(
        f"<globalvar><name>{n}</name><id>1</id><value>{v}</value></globalvar>"
        for n, v in [
            ("_SEQ_IDLE01", 5000),
            ("_SEQ_WALK01", 1000),
            ("_SEQ_WALK02", 1001),
            ("_SEQ_WORK01", 6000),
            ("_SEQ_WORK02", 6001),
            ("_SEQ_WORK03", 6002),
            ("_feedback_transportation_droneGUID", 20132),
        ]
    )
    + "</globalvars>"
)


FISHERY = """module Fishery()
{
	function init()
	{
		if (_getDummyPosition(b, "ship", -1, p) && _getDummyPosition(b, "harbour_0", 0, w)) { return 1 }
		return 0
	}
	interruptable function goFishing()
	{
		if (_getWaterPos(b, t, 30))
		{
			_setIdleSequence(_SEQ_WORK01)
			_sleep(_getSequenceDuration(_SEQ_WORK01) * 2)
		}
		else { _sleep(30000) }
	}
	interruptable function main() { while (_isAlive(b)) { goFishing() _sleep(10000) } }
}
"""


MINE = """module Mine()
{
	var drone
	function fly()
	{
		_setWalkSequence(_SEQ_WALK01)
		heightDuration = _changeFlyHeight(5)
		_sleep(heightDuration)
		_sleep(4000)
		_setWalkSequence(_SEQ_WALK02)
		_sleep(3000)
	}
	function init() { _getDummyPosition(m, "drone", 0, t) drone = _createObjectAtPos3D(_feedback_transportation_droneGUID, 1, s) }
	function main() { while (1) { fly() _sleep(10000) } }
}
"""


LUMBER = """module Lumberjack(unitHID)
{
	var fallingTreeHID
	var treeFallingTime = 1200
	function lumber()
	{
		var workSequence
		var cutTime
		if (treeHID == 0) { _sleep(30000) return }
		targetPos.x += (b.x - treePos.x) * 2048 / dist;
		workSequence = _SEQ_WORK01
		cutTime = 2270
		if (_random(100) > 50) { workSequence = _SEQ_WORK02 cutTime = 3150 }
		_shakeTree(fallingTreeHID, unitHID, 1820, 700, difftime - 2000, 0, 0)
		_sleep(difftime - 2000)
		_startTreeDying(fallingTreeHID, unitHID, treeFallingTime + 5000)
		_sleep(2000)
		_sleep(treeFallingTime + 3000)
	}
}
"""


PLANT = """module TreeFarmer(unitHID)
{
	function plant()
	{
		if (!_getPlantPos(b, treePos)) { _sleep(30000) return }
		targetPos.x += (b.x - treePos.x) * 2048 / dist;
		_sleep(_getSequenceDuration(_SEQ_WORK02))
		_sleep(_getSequenceDuration(_SEQ_WORK03))
	}
}
"""


def cdata(raw: bytes) -> bytes:
    return b"CDATA[" + struct.pack("<I", len(raw)) + raw + b"]"


MAGIC = {4: bytes.fromhex("29de6cc0baa4532b25f5b7a5f666e2ee"), 8: bytes.fromhex("e59b495e6f631f141e13eba990beedc4")}


# An Oodle1 encoder mirroring the decoder (ranges, adaptive windows, runs)
class _Coder:
    def __init__(self) -> None:
        self.low, self.range, self.shifts = 0, 0x80, 0

    def put(self, top: int, value: int, width: int = 1) -> None:
        while self.range <= 0x800000:
            self.low, self.range, self.shifts = self.low << 8, self.range << 8, self.shifts + 1
        step = self.range // top
        self.low += step * value
        self.range = step * width if value + width < top else self.range - step * value

    def finish(self) -> bytes:
        return (self.low << 1).to_bytes(self.shifts + 1, "big")  # the decoder reads one bit late


def _symbol(win: grannyoodle._CWindow, coder: _Coder, top: int, value: int) -> None:
    if win.total >= win.next_ranges:
        if win.next_ranges >= win.next_weights:
            win._rebuild_weights()
        win._rebuild_ranges()
    ranges, weights, values = win.ranges, win.weights, win.values
    known = next((k for k in range(1, len(ranges) - 1) if values[k] == value), None)
    k = known or 0
    coder.put(0x4000, ranges[k], ranges[k + 1] - ranges[k])
    weights[k] += 1
    win.total += 1
    if known:
        return
    if len(weights) >= len(ranges):
        fresh = next((k for k in range(len(ranges) - 1, len(weights)) if values[k] == value), None)
        coder.put(2, int(fresh is not None))
        if fresh is not None:
            coder.put(len(weights) - len(ranges) + 1, fresh - len(ranges) + 1)
            weights[fresh] += 2
            win.total += 2
            return
    weights.append(2)
    win.total += 2
    if len(weights) == win.cap:
        win.total -= weights[0]
        weights[0] = 0
    coder.put(top, value)
    values.append(value)


PARAMS = struct.pack("<II4B", 256 | 4096 << 9, 256 | 8 << 19, 64, 64, 64, 64)


def _encode_run(coder: _Coder, data: bytes, start: int, stop: int) -> None:
    literal_max, offset_max, literal_count, high_count, counts = grannyoodle._parameters(PARAMS)
    low_max, mid_max, high_max = min(offset_max + 1, 4), min(offset_max // 4 + 1, 256), offset_max // 1024 + 1
    low, high = grannyoodle._CWindow(low_max - 1, low_max), grannyoodle._CWindow(high_max - 1, high_count + 1)
    mids = [grannyoodle._CWindow(mid_max - 1, mid_max) for _ in range(high_max)]
    literals = [grannyoodle._CWindow(literal_max - 1, literal_count) for _ in range(4)]
    sizes = [grannyoodle._CWindow(64, counts[3 - i]) for i in range(4) for _ in range(16)]
    sizes.append(grannyoodle._CWindow(64, counts[0]))
    pos, last = start, 0
    while pos < stop:
        best = (0, 0)
        for offset in range(1, pos - start + 1):  # the longest repeat inside this run
            n = 0
            while n < 60 and pos + n < stop and data[pos + n] == data[pos + n - offset]:
                n += 1
            best = max(best, (n, offset))
        length, offset = best
        if length >= 3:
            _symbol(sizes[last], coder, 65, length - 1)
            last, reach, d = length - 1, min(offset_max, pos - start), offset - 1
            _symbol(low, coder, low_max, d & 3)
            _symbol(high, coder, reach // 1024 + 1, d >> 10)
            _symbol(mids[d >> 10], coder, min(reach // 4 + 1, 256), (d >> 2) & 255)
            pos += length
        else:
            _symbol(sizes[last], coder, 65, 0)
            last = 0
            _symbol(literals[pos & 3], coder, literal_max, data[pos])
            pos += 1


def oodle1(data: bytes, stop0: int, stop1: int) -> bytes:
    coder = _Coder()
    for start, stop in ((0, stop0), (stop0, stop1), (stop1, len(data))):
        _encode_run(coder, data, start, stop)
    return PARAMS * 3 + coder.finish()


# A Granny file built member by member
class _Section:
    def __init__(self, pointer: int) -> None:
        self.p, self.data, self.fixups = pointer, bytearray(), []

    def add(self, raw: bytes) -> int:
        self.data += bytes(-len(self.data) % 4)
        self.data += raw
        return len(self.data) - len(raw)

    def string(self, text: str) -> int:
        return self.add(text.encode("latin-1") + b"\0")

    def point(self, at: int, target: int | None) -> None:
        if target is not None:
            self.fixups.append((at, 0, target))

    def kind(self, members: list[tuple]) -> int:
        """A type definition: (kind, name, target type or None, width)."""
        names = [self.string(m[1]) for m in members]
        fmt = "<I" + ("Q" if self.p == 8 else "I") * 2 + "i3i" + ("Q" if self.p == 8 else "I")
        start = None
        for (kind, _name, target, width), name in zip(members, names, strict=True):
            at = self.add(struct.pack(fmt, kind, 0, 0, width, 0, 0, 0, 0))
            start = at if start is None else start
            self.point(at + 4, name)
            self.point(at + 4 + self.p, target)
        self.add(struct.pack(fmt, gr.END, 0, 0, 0, 0, 0, 0, 0))
        return start

    def record(self, fields: list[tuple]) -> int:
        """An object: ("ptr", target) | ("i32", n) | ("f32", x) | ("raw", bytes) fields in order."""
        raw, ptrs = bytearray(), []
        for kind, value in fields:
            if kind == "ptr":
                ptrs.append((len(raw), value))
                raw += bytes(self.p)
            else:
                raw += value if kind == "raw" else struct.pack("<i" if kind == "i32" else "<f", value)
        at = self.add(bytes(raw))
        for off, target in ptrs:
            self.point(at + off, target)
        return at


def granny_file(pointer: int = 4, compress: bool = False) -> bytes:
    """One mesh (a quad, a triangle each of materials "wood" and "roof") with a transform and an inline record."""
    s = _Section(pointer)
    vertex_t = s.kind(
        [(gr.REAL32, "Position", None, 3), (gr.REAL32, "Normal", None, 3), (gr.REAL16, "TextureCoordinates0", None, 2)]
    )
    vdata_t = s.kind([(gr.REFERENCE_TO_VARIANT_ARRAY, "Vertices", None, 0)])
    group_t = s.kind(
        [(gr.INT32, "MaterialIndex", None, 0), (gr.INT32, "TriFirst", None, 0), (gr.INT32, "TriCount", None, 0)]
    )
    int32_t, int16_t = s.kind([(gr.INT32, "Int32", None, 0)]), s.kind([(gr.INT16, "Int16", None, 0)])
    topo_t = s.kind(
        [
            (gr.REFERENCE_TO_ARRAY, "Groups", group_t, 0),
            (gr.REFERENCE_TO_ARRAY, "Indices", int32_t, 0),
            (gr.REFERENCE_TO_ARRAY, "Indices16", int16_t, 0),
        ]
    )
    material_t = s.kind([(gr.STRING, "Name", None, 0)])
    binding_t = s.kind([(gr.REFERENCE, "Material", material_t, 0)])
    mesh_t = s.kind(
        [
            (gr.STRING, "Name", None, 0),
            (gr.REFERENCE, "PrimaryVertexData", vdata_t, 0),
            (gr.REFERENCE, "PrimaryTopology", topo_t, 0),
            (gr.REFERENCE_TO_ARRAY, "MaterialBindings", binding_t, 0),
        ]
    )
    art_t = s.kind([(gr.REAL32, "UnitsPerMeter", None, 0)])
    root_t = s.kind(
        [
            (gr.INLINE, "ArtToolInfo", art_t, 0),
            (gr.TRANSFORM, "Placement", None, 0),
            (gr.ARRAY_OF_REFERENCES, "Meshes", mesh_t, 0),
            (gr.STRING, "FromFileName", None, 0),
        ]
    )
    corners = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
    vertices = s.add(b"".join(struct.pack("<6f2e", *c, 0, 2, 0, c[0], c[2]) for c in corners))
    vdata = s.record([("ptr", vertex_t), ("i32", 4), ("ptr", vertices)])
    groups = s.add(struct.pack("<6i", 0, 0, 1, 1, 1, 1))
    indices = s.add(struct.pack("<6h", 0, 1, 2, 0, 2, 3))
    topo = s.record([("i32", 2), ("ptr", groups), ("i32", 0), ("ptr", None), ("i32", 6), ("ptr", indices)])
    wood, roof = (s.record([("ptr", s.string(n))]) for n in ("Wood", "roof"))
    bindings = s.record([("ptr", wood), ("ptr", roof)])
    mesh = s.record([("ptr", s.string("house")), ("ptr", vdata), ("ptr", topo), ("i32", 2), ("ptr", bindings)])
    table = s.record([("ptr", mesh)])
    placement = struct.pack("<I16f", 7, 1, 2, 3, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1)
    root = s.record([("f32", 1.0), ("raw", placement), ("i32", 1), ("ptr", table), ("ptr", s.string("x.max"))])
    raw = bytes(s.data)
    stop0, stop1 = len(raw) // 3, len(raw) // 2
    stored = oodle1(raw, stop0, stop1) if compress else raw
    data_at = gr.INFO_AT + 72 + gr.SECTION_ENTRY
    fixups_at = data_at + len(stored)
    total = fixups_at + 12 * len(s.fixups)
    out = bytearray(MAGIC[pointer] + struct.pack("<II8x", gr.INFO_AT + 72 + gr.SECTION_ENTRY, 0))
    out += struct.pack("<5I", 7, total, 0, 72, 1) + struct.pack("<4I", 0, root_t, 0, root) + struct.pack("<I", 0)
    out += bytes(gr.INFO_AT + 72 - len(out))
    section = (gr.OODLE1 if compress else gr.NONE, data_at, len(stored), len(raw), 4, stop0, stop1, fixups_at)
    out += struct.pack("<11I", *section, len(s.fixups), total, 0)
    out += stored + b"".join(struct.pack("<3I", *f) for f in s.fixups)
    return bytes(out)


I16 = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


def rig_file(colours: bool = False, animated: bool = True) -> bytes:
    """A quad on a two-bone skeleton (root, tip one along x): its far edge on the tip; the tip rises two in a second."""
    wood = gw.SRec(gw.MATERIAL, {"Name": "Wood"})
    corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 0.0, 1.0)]
    spec = gw.SMeshSpec(
        "quad", corners, [(0.0, 1.0, 0.0)] * 4, [(c[0], c[2]) for c in corners], [(wood, [0, 1, 2, 0, 2, 3])]
    )
    if colours:  # 1404's flags: the pole's vertices blue 128
        spec.colours = [
            (0.0, 0.0, 128 / 255, 1.0),
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0, 1.0),
            (0.0, 0.0, 128 / 255, 1.0),
        ]
    else:
        spec.bones = ["root", "tip"]
        spec.bone_indices = [(0, 0, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0), (0, 0, 0, 0)]
        spec.bone_weights = [(255, 0, 0, 0)] * 4
    mesh = gw.mesh_record(spec)
    bones = [
        {"Name": "root", "ParentIndex": -1, "InverseWorldTransform": I16},
        {"Name": "tip", "ParentIndex": 0, "Transform": {**gw.IDENTITY_TRANSFORM, "position": (1.0, 0.0, 0.0)},
         "InverseWorldTransform": (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, -1.0, 0, 0, 1.0)},
    ]  # fmt: skip
    skeleton = gw.skeleton_record("rig", bones)
    model = gw.model_record("quad", skeleton, [mesh])
    lists = {"Materials": [wood], "Skeletons": [skeleton], "Meshes": [mesh], "Models": [model]}
    if animated:
        rise = gw.curve([0.0, 1.0], [(1.0, 0.0, 0.0), (1.0, 2.0, 0.0)])
        lists["Animations"] = [
            gw.animation_record("rise", 1.0, 0.25, [("quad", [{"Name": "tip", "PositionCurve": rise}])])
        ]
    return gw.write_file(gw.root_record("quad.max", **lists))


def _encode(data: bytes, flags: int, v: rda.SRdaVersion) -> bytes:
    if flags & FLAG_COMPRESSED:
        data = zlib.compress(data)
    if flags & FLAG_ENCRYPTED:
        data = rda.decrypt(data, v.seed)  # XOR stream: encrypt == decrypt
    return data


def _file_header(name: str, offset: int, compressed: int, size: int, v: rda.SRdaVersion) -> bytes:
    raw = name.encode("utf-16-le")
    assert len(raw) < rda.FILE_PATH_BYTES
    return raw.ljust(rda.FILE_PATH_BYTES, b"\0") + v.file_header_tail.pack(offset, compressed, size, 1234567890, 0)


def write_rda(path: Path, blocks: list[tuple[int, dict[str, bytes]]], v: rda.SRdaVersion = V2_0) -> Path:
    """Write an archive with one block per (flags, {path: content}) tuple."""
    out = bytearray(v.magic + b"\0" * v.padding)
    first_block_pos = len(out)
    out += b"\0" * v.uint_size  # first block offset, patched below
    block_offsets: list[int] = []
    for flags, files in blocks:
        headers = bytearray()
        if flags & FLAG_CONTIGUOUS:
            blob = bytearray()
            for name, content in files.items():
                headers += _file_header(name, len(blob), len(content), len(content), v)
                blob += content
            blob_enc = _encode(bytes(blob), flags, v)
            out += blob_enc
            hdr_enc = _encode(bytes(headers), flags, v)
            out += hdr_enc
            out += v.blob_info.pack(len(blob_enc), len(blob))
        else:
            for name, content in files.items():
                enc = _encode(content, flags, v)
                headers += _file_header(name, len(out), len(enc), len(content), v)
                out += enc
            hdr_enc = _encode(bytes(headers), flags, v)
            out += hdr_enc
        block_offsets.append(len(out))
        out += v.block_header.pack(flags, len(files), len(hdr_enc), len(headers), 0)  # next patched below
    # Chain the blocks; the last one points past the end of the file.
    for i, off in enumerate(block_offsets):
        nxt = block_offsets[i + 1] if i + 1 < len(block_offsets) else len(out)
        struct.pack_into("<" + v.uint, out, off + v.block_header.size - v.uint_size, nxt)
    struct.pack_into("<" + v.uint, out, first_block_pos, block_offsets[0] if block_offsets else len(out))
    path.write_bytes(bytes(out))
    return path


def baked_particle(start=0, ticks=4, material=0, x0=-1.0, x1=1.0, color=(1.0, 0.5, 0.25, 1.0)):
    xs = [x0 + (x1 - x0) * k / (ticks - 1) for k in range(ticks)]
    return SBakedParticle(
        start=start,
        end=start + ticks - 1,
        material=material,
        position=[(x, 2 * x, -x) for x in xs],
        size=[(0.5, 0.5, 0.5)] * ticks,
        rotation=[(0.0, math.pi / 2, 0.0)] * ticks,
        color=[color] * ticks,
    )


def rdp_file(particles=None, materials=1, frames=8, looped=False):
    return quantise(
        particles or [baked_particle()], [SRdpMaterial(order=i) for i in range(materials)], "tex\\a.psd", frames, looped
    )
