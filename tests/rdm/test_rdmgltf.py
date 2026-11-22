"""RDM to glTF and back (rdmgltf, gltfrdm) on synthetic files: byte-identical, nothing added, nothing lost."""

import json
import math

import pytest
from tests import rdmfiles
from tests.rdm.rdmfakes import export, strip

from annotoolbox.formats.gltf import SGltf, read_gltf
from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.formats.rdmwrite import write_file
from annotoolbox.rdm import gltfrdm, listing, rdmgltf
from annotoolbox.rdm.gltfdoc import SOptions


def files():
    return {
        "house_lod0.rdm": rdmfiles.static_file(),
        "crane_lod0.rdm": rdmfiles.skinned_file(),
    }


def back(builder, name="x.rdm", **kw):
    g = SGltf(json.loads(json.dumps(builder.m_doc)), [bytes(builder.m_blob)])  # as a file holds it: JSON, no tuples
    got = gltfrdm.import_gltf(g, name, SOptions(**kw))
    return {n: write_file(f) for n, f in got.files}, got.notes


def test_the_writer_writes_back_what_it_read():
    for data in (*files().values(), rdmfiles.animation_file()):
        assert write_file(read_rdm(data)) == data
    one = rdmfiles.one_mesh_file()  # the byte-order helper swaps a file of one mesh
    big = rdmfiles.to_big_endian(one)
    assert read_rdm(big).big_endian and write_file(read_rdm(big)) == one


@pytest.mark.parametrize("axes", ["gltf", "anno"])
@pytest.mark.parametrize("form", ["glb", "gltf", "gltfmin", "embedded"])
def test_a_round_trip_through_every_form_is_byte_identical(tmp_path, axes, form):
    anim = ("crane_work.rdm", rdmfiles.animation_file())
    for name, data in files().items():
        builder, _ = export(name, data, [anim] if name.startswith("crane") else [], axes=axes)
        path = tmp_path / f"{name}.{'glb' if form == 'glb' else 'gltf'}"
        for p, blob in builder.files(path, form).items():
            p.write_bytes(blob)
        got = gltfrdm.import_gltf(read_gltf(path), "other.rdm")
        out = {n: write_file(f) for n, f in got.files}
        assert out[name] == data and not got.notes
        if name.startswith("crane"):
            assert out["crane_work.rdm"] == anim[1]


def test_the_export_adds_nothing_but_the_marked_scene():
    for name, data in files().items():
        rdm = read_rdm(data)
        doc = export(name, data)[0].m_doc
        joints = sum(len(s.joints) for s in rdm.skeletons)
        assert len(doc["nodes"]) == len(rdm.meshes) + joints  # no armature, root or dummy node
        assert len(doc.get("skins", [])) == len(rdm.skeletons) and len(doc["materials"]) == len(rdm.materials)
        assert [len(m["primitives"]) for m in doc["meshes"]] == [max(1, len(m.submeshes)) for m in rdm.meshes]
        assert "cameras" not in doc and "animations" not in doc
        assert all(item.get("extras", {}).get("rdm") for kind in ("nodes", "meshes", "materials") for item in doc[kind])
        lines = listing.describe_gltf(SGltf(doc, []))
        assert not [line for line in lines if "foreign" in line] and "helper (scene)" in lines[1]
        skins = doc.get("skins", [])
        assert [len(s["joints"]) for s in skins] == [len(s.joints) for s in rdm.skeletons]
        assert all("skeleton" not in s for s in skins)  # no skeleton root named that the source lacks


def test_joint_order_names_and_keys_survive_and_tracks_keep_their_places():
    builder, notes = export("crane_lod0.rdm", rdmfiles.skinned_file(), [("w.rdm", rdmfiles.animation_file())])
    doc = builder.m_doc
    joint_names = [doc["nodes"][j]["name"] for s in doc["skins"] for j in s["joints"]]
    assert joint_names == ["root", "arm", "hand", "door"]
    assert [a["name"] for a in doc["animations"]] == ["w:0", "w:1"] and len(doc["animations"][0]["channels"]) == 8
    assert any("Point001" in n for n in notes)  # a track on no joint of the mesh: kept, unbound, said
    out, _ = back(builder)
    anim = read_rdm(out["w.rdm"]).animations[0]
    assert [t.joint for t in anim.tracks] == ["root", "arm", "Point001", "hand", "arm"] and anim.tracks[3].keys == []


def test_a_lod_chain_goes_into_one_document_under_marked_file_nodes():
    lod0, lod1 = rdmfiles.static_file(), rdmfiles.skinned_file()
    sources = [rdmgltf.SSource("a_lod0.rdm", read_rdm(lod0)), rdmgltf.SSource("a_lod1.rdm", read_rdm(lod1))]
    builder, _ = rdmgltf.export(sources)
    roots = builder.m_doc["scenes"][0]["nodes"]
    assert [builder.m_doc["nodes"][r]["extras"]["rdm"]["helper"] for r in roots] == ["file", "file"]
    out, notes = back(builder)
    assert out == {"a_lod0.rdm": lod0, "a_lod1.rdm": lod1} and not notes


def test_the_mirror_into_glTF_space_is_exact_and_turns_the_winding():
    mirrored = export("house_lod0.rdm", rdmfiles.static_file())[0]
    raw = export("house_lod0.rdm", rdmfiles.static_file(), axes="anno")[0]

    def first(builder, attribute):
        g = SGltf(builder.m_doc, [bytes(builder.m_blob)])
        prim = builder.m_doc["meshes"][0]["primitives"][0]
        return g.values(prim["attributes"][attribute] if attribute != "indices" else prim["indices"])

    assert [(-p[0], p[1], p[2]) for p in first(raw, "POSITION")] == first(mirrored, "POSITION")
    a, b = first(raw, "indices"), first(mirrored, "indices")
    assert (a[0], a[2], a[1]) == tuple(b[:3])
    t_raw, t_mir = first(raw, "TANGENT")[0], first(mirrored, "TANGENT")[0]
    assert t_mir == (-t_raw[0], t_raw[1], t_raw[2], -t_raw[3])  # a mirror turns the frame's handedness


def test_strict_glTF_rounds_back_within_one_step():
    builder, _ = export("crane_lod0.rdm", rdmfiles.skinned_file(), strict=True)
    g = SGltf(builder.m_doc, [bytes(builder.m_blob)])
    prim = builder.m_doc["meshes"][0]["primitives"][0]["attributes"]
    assert all(abs(math.dist(n, (0, 0, 0)) - 1) < 1e-6 for n in g.values(prim["NORMAL"]))
    assert all(sum(w) == 255 for w in g.values(prim["WEIGHTS_0"], normalize=False))
    out, _ = back(builder)
    src, got = read_rdm(rdmfiles.skinned_file()).meshes[0], read_rdm(out["crane_lod0.rdm"]).meshes[0]
    for k in (1, 2, 3):  # normals, tangents, bitangents
        pairs = zip(src.element(k), got.element(k), strict=True)
        assert all(abs(a - b) <= 1 for va, vb in pairs for a, b in zip(va[:3], vb[:3], strict=True))


def test_without_the_extras_every_field_the_game_reads_comes_back():
    """A modelling tool that drops the extras: layouts from the attributes, joints from the bind matrices."""
    builder, _ = export("crane_lod0.rdm", rdmfiles.skinned_file(), [("w.rdm", rdmfiles.animation_file())])
    g = SGltf(strip(json.loads(json.dumps(builder.m_doc))), [bytes(builder.m_blob)])
    got = gltfrdm.import_gltf(g, "crane_lod0.rdm")
    src, out = read_rdm(rdmfiles.skinned_file()), got.files[0][1]
    assert [m.name for m in out.meshes] == [m.name for m in src.meshes]
    a, b = src.meshes[0], out.meshes[0]
    assert (a.format, a.vertex_count, a.indices, a.submeshes) == (b.format, b.vertex_count, b.indices, b.submeshes)
    for k in (1, 2, 3, 4, 5, 6):  # every element but the position's 4th value and the normal's 4th byte
        width = 3 if k == 1 else 4
        assert [x[:width] for x in a.element(k)] == [y[:width] for y in b.element(k)]
    for sa, sb in zip(src.skeletons, out.skeletons, strict=True):
        for ja, jb in zip(sa.joints, sb.joints, strict=True):
            assert (ja.name, ja.parent) == (jb.name, jb.parent)
            assert (
                max(abs(x - y) for x, y in zip(ja.position + ja.rotation, jb.position + jb.rotation, strict=True))
                < 1e-6
            )
    assert any("without a stored box" in n for n in got.notes)  # said, not invented
    work = next(f for n, f in got.files if f.animations and f.animations[0].frame_rate == 30)
    src_keys = read_rdm(rdmfiles.animation_file()).animations[0].tracks[0].keys
    assert work.animations[0].tracks[0].keys == src_keys


def test_a_big_endian_file_exports_like_its_little_endian_copy():
    one = rdmfiles.one_mesh_file()
    big = rdmfiles.to_big_endian(one)
    assert back(export("house_lod0.rdm", big)[0])[0]["house_lod0.rdm"] == one


def test_what_glTF_cannot_hold_is_refused_with_its_reason():
    rdm = read_rdm(rdmfiles.static_file())
    rdm.meshes[0].submeshes = rdm.meshes[0].submeshes[::-1]
    with pytest.raises(rdmgltf.CExportError, match="submeshes do not run in order"):
        rdmgltf.export([rdmgltf.SSource("x.rdm", rdm)])
    with pytest.raises(rdmgltf.CExportError, match="same file name"):
        one = rdmgltf.SSource("x.rdm", read_rdm(rdmfiles.static_file()))
        rdmgltf.export([one], [one])


def test_an_empty_submesh_is_refused_not_crashed_on():
    rdm = read_rdm(rdmfiles.static_file())
    rdm.meshes[0].submeshes[0].index_count = 0
    with pytest.raises(rdmgltf.CExportError, match="submeshes do not run in order"):
        rdmgltf.export([rdmgltf.SSource("x.rdm", rdm)])


def test_tracks_on_no_joint_are_each_said_to_name_none_not_called_a_second_track():
    _, notes = export("house_lod0.rdm", rdmfiles.static_file(), [("anim.rdm", rdmfiles.animation_file())])
    assert not any("second track" in n for n in notes)
    assert sum("names no joint" in n and "'arm'" in n for n in notes) == 2
