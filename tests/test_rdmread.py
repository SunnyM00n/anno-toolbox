import struct

import pytest
from tests import bigendian as rdmbe
from tests.rdmfiles import Builder as _Builder
from tests.rdmfiles import build_rdm, build_skinned

from annotoolbox.formats import rdmread
from annotoolbox.formats.rdmwrite import FORMAT_P4H_N4B_G4B_B4B_T2H, RdmMeshData, RdmSubmesh, write_rdm


def _vertices(n: int, stride: int = 24) -> bytes:
    out = bytearray()
    for i in range(n):
        row = struct.pack("<4e", float(i), 2.0 * i, -1.0, 1.0) + bytes((i, 1, 2, 3)) + bytes((4, 5, 6, 7) * 2)
        out += row + struct.pack("<2e", 0.25 * i, 0.5)
        out += bytes(stride - 24)
    return bytes(out)


def _mesh(name: str = "part", material: int = 0) -> RdmMeshData:
    return RdmMeshData(
        name,
        _vertices(4),
        24,
        FORMAT_P4H_N4B_G4B_B4B_T2H,
        [0, 1, 2, 2, 1, 3],
        ["wood", "stone"],
        [RdmSubmesh(0, 6, material)],
        (0.0, 0.0, -1.0, 3.0, 6.0, -1.0),
        "scene.max",
        "Tangent.rmp",
    )


def test_reads_a_written_mesh():
    f = rdmread.read_rdm(write_rdm(_mesh()))
    assert not f.big_endian and (f.source, f.exporter) == ("scene.max", "Tangent.rmp")
    assert [(m.name, m.format, m.vertex_count, m.stride) for m in f.meshes] == [("part", "P4h_N4b_G4b_B4b_T2h", 4, 24)]
    m = f.meshes[0]
    assert m.indices == [0, 1, 2, 2, 1, 3] and m.index_type == 3 and m.skeleton is None
    assert [(s.first_index, s.index_count, s.material) for s in m.submeshes] == [(0, 6, 0)]
    assert m.bbox == (0.0, 0.0, -1.0, 3.0, 6.0, -1.0) == m.computed_bounds()
    assert m.positions()[3] == (3.0, 6.0, -1.0) and m.element(4)[1] == (0.25, 0.5)
    assert m.element(1)[2] == (2, 1, 2, 3)  # a packed vector: its four bytes
    assert [mat.name for mat in f.materials] == ["wood", "stone"]
    assert m.offsets() == {"P4h": [0], "N4b": [8], "G4b": [12], "B4b": [16], "T2h": [20]}


def test_several_meshes_in_one_file():
    data = write_rdm(_mesh("a"), _mesh("b", material=1))
    f = rdmread.read_rdm(data)
    assert [m.name for m in f.meshes] == ["a", "b"] and f.meshes[1].submeshes[0].material == 1
    assert write_rdm(_mesh()) == write_rdm(_mesh(), *())  # one mesh: the layout as before


def test_big_endian_reads_as_the_little_endian_file():
    data = write_rdm(_mesh())
    le, be = rdmread.read_rdm(data), rdmread.read_rdm(rdmbe.to_big_endian(data))
    assert be.big_endian and not le.big_endian
    assert be.meshes == le.meshes and be.materials == le.materials


def test_early_mesh_record_reads_without_box():
    data = bytearray(build_rdm(68, [(-1.0, 2.0, 0.5), (3.0, -4.0, 1.5)]))
    struct.pack_into("<II", data, 0xC, 4, 0x1C)  # the header words the game checks
    m = rdmread.read_rdm(bytes(data)).meshes[0]
    assert m.record_size == 68 and m.bbox == (0.0,) * 6  # the engine's zero-filled read
    assert m.vertex_count == 2 and m.vertices[:6] == struct.pack("<3e", -1.0, 2.0, 0.5)


def test_skeleton_and_bone_elements():
    data = build_skinned([("root", 0xFFFFFFFF), ("arm", 0)], [(bytes((1, 0, 0, 0)), bytes((255, 0, 0, 0)))] * 3)
    f = rdmread.read_rdm(data)
    m = f.meshes[0]
    assert m.format == "P4h_N4b_G4b_B4b_T2h_I4b_W4b" and m.bones_per_vertex == 1 and m.skeleton == 0
    joints = f.skeletons[0].joints
    assert [(j.name, j.parent, j.position[0]) for j in joints] == [("root", None, 0.0), ("arm", 0, 1.0)]
    assert joints[1].rotation == (0.0, 0.0, 0.0, 1.0)


def _animation_file(rate: int = 30) -> bytes:
    b = _Builder()
    b.block("header1", ["export", bytes(12), "anims", bytes(28)])
    b.block("export", [b"\0" * 72])
    b.block("anims", ["anim_name", "tracks", struct.pack("<II", 3333, rate), bytes(32)])
    b.block("anim_name", [b"house_lod0"], count=10)
    b.block("tracks", ["joint_name", "keys", bytes(16)])
    b.block("joint_name", [b"Circle001"], count=9)
    keys = [struct.pack("<8f", 0, 0, 0, -1, 0.5, 0, 0, t) for t in (0.0, 3.333)]
    b.block("keys", keys, count=2)
    return b.finish()


def test_animation_tracks_and_keys():
    f = rdmread.read_rdm(_animation_file())
    a = f.animations[0]
    assert (a.name, a.duration_ms, a.frame_rate) == ("house_lod0", 3333, 30)
    assert [t.joint for t in a.tracks] == ["Circle001"]
    k = a.tracks[0].keys
    assert k[0].rotation == (0.0, 0.0, 0.0, -1.0) and k[0].position == (0.5, 0.0, 0.0)
    assert k[1].time == pytest.approx(3.333) and not f.meshes


def test_bad_files_are_refused():
    with pytest.raises(rdmread.RdmReadError):
        rdmread.read_rdm(b"RDX" + bytes(40))
    data = bytearray(write_rdm(_mesh()))
    struct.pack_into("<I", data, 8, 7)
    with pytest.raises(rdmread.RdmReadError):
        rdmread.read_rdm(bytes(data))
    with pytest.raises(rdmread.RdmReadError):
        rdmread.read_rdm(write_rdm(_mesh())[:-40])
