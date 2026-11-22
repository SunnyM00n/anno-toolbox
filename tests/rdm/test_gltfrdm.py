"""glTF to RDM (gltfrdm) when a modelling tool has been at the file: its additions dropped or refused, never written."""

import json
import struct

import pytest
from tests import rdmfiles
from tests.rdm.rdmfakes import export, strip

from annotoolbox.formats import gltf
from annotoolbox.formats.gltf import CBuilder, SGltf
from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.formats.rdmwrite import write_file
from annotoolbox.rdm import gltfrdm
from annotoolbox.rdm.gltfdoc import CGltfImportError, SDoc, SOptions


def exported(name="crane_lod0.rdm", data=None, anims=()):
    builder, _ = export(name, data or rdmfiles.skinned_file(), anims)
    return builder


def run(builder: CBuilder, name="crane_lod0.rdm", **kw):
    g = SGltf(json.loads(json.dumps(builder.m_doc)), [bytes(builder.m_blob)])
    got = gltfrdm.import_gltf(g, name, SOptions(**kw))
    return {n: write_file(f) for n, f in got.files}, got.notes


def test_a_camera_a_light_and_the_armature_object_are_dropped_and_said():
    b = exported()
    roots = b.m_doc["scenes"][0]["nodes"]
    joint_roots = [r for r in roots if "mesh" not in b.m_doc["nodes"][r]]
    armature = b.add("nodes", {"name": "Armature", "children": joint_roots})  # what Blender puts above the bones
    camera = b.add(
        "nodes",
        {
            "name": "Camera",
            "camera": b.add("cameras", {"type": "perspective", "perspective": {"yfov": 1, "znear": 0.1}}),
        },
    )
    light = b.add("nodes", {"name": "Sun", "extensions": {"KHR_lights_punctual": {"light": 0}}})
    b.m_doc["scenes"][0]["nodes"] = [r for r in roots if r not in joint_roots] + [armature, camera, light]
    out, notes = run(b)
    assert out == {"crane_lod0.rdm": rdmfiles.skinned_file()}
    assert {n.split(":")[0] for n in notes} == {
        "dropped node 'Armature'",
        "dropped node 'Camera'",
        "dropped node 'Sun'",
    }


def test_a_bone_the_modelling_tool_added_is_dropped_unless_vertices_weigh_it():
    b = exported()
    skin = b.m_doc["skins"][0]
    root = skin["joints"][0]
    extra = b.add("nodes", {"name": "Armature_root", "children": [root]})
    b.m_doc["scenes"][0]["nodes"] = [n for n in b.m_doc["scenes"][0]["nodes"] if n != root] + [extra]
    skin["joints"].append(extra)  # a joint slot nothing weighs; its bind matrix is the identity
    ibms = SGltf(b.m_doc, [bytes(b.m_blob)]).values(skin["inverseBindMatrices"])
    skin["inverseBindMatrices"] = b.accessor(
        [*ibms, (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)], "MAT4", gltf.FLOAT
    )
    out, notes = run(b)
    assert out["crane_lod0.rdm"] == rdmfiles.skinned_file()
    assert any("dropped joint 'Armature_root'" in n for n in notes)
    attrs = b.m_doc["meshes"][0]["primitives"][0]["attributes"]
    joints = SGltf(b.m_doc, [bytes(b.m_blob)]).values(attrs["JOINTS_0"])
    attrs["JOINTS_0"] = b.accessor([(3, *j[1:]) for j in joints], "VEC4", gltf.UBYTE)  # now vertices weigh it
    with pytest.raises(CGltfImportError, match="not the source's and carry vertex weights"):
        run(b)


def test_an_extra_uv_map_or_colour_layer_is_not_written_into_the_layout():
    b = exported("house_lod0.rdm", rdmfiles.static_file())
    prims = b.m_doc["meshes"][0]["primitives"]
    n = SGltf(b.m_doc, [bytes(b.m_blob)]).items("accessors")[prims[0]["attributes"]["POSITION"]]["count"]
    uv = b.accessor([(0.0, 0.0)] * n, "VEC2", gltf.FLOAT)
    colour = b.accessor([(1.0, 1.0, 1.0, 1.0)] * n, "VEC4", gltf.FLOAT)
    for p in prims:
        p["attributes"] = {**p["attributes"], "TEXCOORD_1": uv, "COLOR_2": colour}
    out, notes = run(b, "house_lod0.rdm")
    assert out["house_lod0.rdm"] == rdmfiles.static_file()
    assert sorted(n.split("dropped attribute ")[1].split(":")[0] for n in notes) == ["COLOR_2", "TEXCOORD_1"]


def test_a_primitive_without_a_material_is_refused_not_given_a_default():
    b = exported("house_lod0.rdm", rdmfiles.static_file())
    del b.m_doc["meshes"][0]["primitives"][0]["material"]
    with pytest.raises(CGltfImportError, match="no material"):
        run(b, "house_lod0.rdm")


def test_morph_weights_and_scaling_keys_are_refused_and_unit_scale_keys_ignored():
    b = exported(anims=[("w.rdm", rdmfiles.animation_file())])
    anim = b.m_doc["animations"][0]
    times = anim["samplers"][0]["input"]
    node = anim["channels"][0]["target"]["node"]
    for scale, ok in (((1.0, 1.0, 1.0), True), ((2.0, 1.0, 1.0), False)):
        out = b.accessor([scale] * 3, "VEC3", gltf.FLOAT)
        anim["samplers"].append({"input": times, "output": out})
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1, "target": {"node": node, "path": "scale"}})
        if ok:
            assert run(b)[0]["w.rdm"] == rdmfiles.animation_file()
        else:
            with pytest.raises(CGltfImportError, match="scales"):
                run(b)
        anim["channels"].pop()
    anim["channels"].append({"sampler": 0, "target": {"node": node, "path": "weights"}})
    with pytest.raises(CGltfImportError, match="morph target"):
        run(b)


def test_step_and_cubic_keys_become_linear_rdm_keys():
    b = exported(anims=[("w.rdm", rdmfiles.animation_file())])
    anim = b.m_doc["animations"][1]  # the door: two keys at 0 and 1 s, 60 fps
    g = SGltf(b.m_doc, [bytes(b.m_blob)])
    rot = anim["samplers"][0]
    rot["interpolation"] = "STEP"
    trans = anim["samplers"][1]
    values = g.values(trans["output"])
    cubic = [v for value in values for v in ((0.0, 0.0, 0.0), value, (0.0, 0.0, 0.0))]
    trans["output"], trans["interpolation"] = b.accessor(cubic, "VEC3", gltf.FLOAT), "CUBICSPLINE"
    out, _ = run(b)
    door = read_rdm(out["w.rdm"]).animations[1].tracks[0]
    assert len(door.keys) == 61 and door.keys[0].time == 0.0 and abs(door.keys[-1].time - 1.0) < 1e-6
    assert door.keys[30].rotation == door.keys[0].rotation  # held until the next key


def test_a_model_made_elsewhere_gets_what_the_layout_needs_and_says_what_it_filled():
    b = CBuilder()
    pos = b.accessor([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], "VEC3", gltf.FLOAT)
    uv = b.accessor([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], "VEC2", gltf.FLOAT)
    idx = b.accessor([0, 2, 1], "SCALAR", gltf.USHORT)
    b.add("materials", {"name": "stone"})
    b.add(
        "meshes",
        {
            "name": "rock",
            "primitives": [{"attributes": {"POSITION": pos, "TEXCOORD_0": uv}, "indices": idx, "material": 0}],
        },
    )
    b.add("nodes", {"name": "rock", "mesh": 0, "translation": [0.0, 2.0, 0.0]})
    b.m_doc["scenes"], b.m_doc["scene"] = [{"nodes": [0]}], 0
    out, notes = run(b, "rock.rdm")
    rdm = read_rdm(out["rock.rdm"])
    m = rdm.meshes[0]
    assert (m.name, m.format, [x.name for x in rdm.materials]) == ("rock", "P4h_N4b_G4b_B4b_T2h", ["stone"])
    assert m.positions()[0] == (-0.0, 2.0, 0.0) and m.indices == [0, 1, 2]  # node moved, mirrored, winding turned
    assert any("filled the tangent frame" in n for n in notes)
    assert rdm.source == rdm.exporter == ""  # no placeholder strings


def test_the_like_file_gives_the_material_order_and_the_layout():
    b = exported("house_lod0.rdm", rdmfiles.static_file())
    plain = CBuilder(strip(json.loads(json.dumps(b.m_doc))), b.m_blob)
    plain.m_doc["materials"] = plain.m_doc["materials"][::-1]
    for prim in plain.m_doc["meshes"][0]["primitives"] + plain.m_doc["meshes"][1]["primitives"]:
        prim["material"] = 1 - prim["material"]
    like = read_rdm(rdmfiles.static_file())
    out, _ = run(plain, "house_lod0.rdm", like=like)
    got = read_rdm(out["house_lod0.rdm"])
    assert [m.name for m in got.materials] == ["roof", "wall"] and got.materials[0].texture == "data/roof_diff.psd"
    assert [m.format for m in got.meshes] == [m.format for m in like.meshes]
    assert [s.material for s in got.meshes[0].submeshes] == [1, 0]


def test_one_mesh_of_several_is_taken_by_name_or_index():
    b = exported("house_lod0.rdm", rdmfiles.static_file())
    out, notes = run(b, "house_lod0.rdm", mesh="1")
    assert [m.name for m in read_rdm(out["house_lod0.rdm"]).meshes] == [""]
    assert any("dropped node 'house_lod0': a mesh left out" in n for n in notes)
    with pytest.raises(CGltfImportError, match="no mesh named"):
        run(b, "house_lod0.rdm", mesh="nothing")


def test_joint_names_that_repeat_are_refused_or_numbered_on_request():
    b = exported()
    for j in b.m_doc["skins"][0]["joints"]:
        b.m_doc["nodes"][j]["name"] = "bone"
    with pytest.raises(CGltfImportError, match="repeated"):
        run(b)
    out, _ = run(b, joint_names="index")
    assert [j.name for j in read_rdm(out["crane_lod0.rdm"]).skeletons[0].joints] == ["0", "1", "2"]


def test_a_joint_that_scales_is_refused():
    b = exported()
    skin = b.m_doc["skins"][0]
    g = SGltf(b.m_doc, [bytes(b.m_blob)])
    ibms = [list(m) for m in g.values(skin["inverseBindMatrices"])]
    ibms[0][0] *= 2.0
    skin["inverseBindMatrices"] = b.accessor([tuple(m) for m in ibms], "MAT4", gltf.FLOAT)
    with pytest.raises(CGltfImportError, match="scales"):
        run(b)


def test_the_glTF_reader_takes_strides_and_sparse_accessors():
    b = CBuilder()
    payload = b"".join(struct.pack("<3f4x", k, 2 * k, 3 * k) for k in range(3))  # 16-byte stride, 12-byte values
    view = b.view(payload)
    b.m_doc["bufferViews"][view]["byteStride"] = 16
    b.add("accessors", {"bufferView": view, "componentType": gltf.FLOAT, "count": 3, "type": "VEC3"})
    where, what = b.view(struct.pack("<H", 1)), b.view(struct.pack("<3f", 9, 9, 9))
    sparse = {
        "count": 1,
        "indices": {"bufferView": where, "componentType": gltf.USHORT},
        "values": {"bufferView": what},
    }
    b.add("accessors", {"bufferView": view, "componentType": gltf.FLOAT, "count": 3, "type": "VEC3", "sparse": sparse})
    g = SGltf(b.m_doc, [bytes(b.m_blob)])
    assert g.values(0) == [(0, 0, 0), (1, 2, 3), (2, 4, 6)] and g.values(1)[1] == (9, 9, 9)


@pytest.mark.parametrize("children", [[[0]], [[1], [0]], [[1], [2], [0]]])
def test_a_node_tree_with_a_cycle_is_refused_not_looped(children):
    nodes = [{"name": f"n{i}", "children": c} for i, c in enumerate(children)]
    with pytest.raises(CGltfImportError, match="cycle"):
        SDoc(SGltf({"nodes": nodes}, []), SOptions(), False, {})


def test_a_shared_node_without_a_cycle_loads():
    nodes = [{"children": [1, 2]}, {"children": [3]}, {"children": [3]}, {}]
    doc = SDoc(SGltf({"nodes": nodes}, []), SOptions(), False, {})
    assert doc.walk([0]) == [0, 1, 3, 2, 3]


def test_a_key_time_before_a_channels_first_key_takes_the_first_value_not_an_extrapolation():
    from types import SimpleNamespace

    from annotoolbox.rdm import gltfskin

    d = SimpleNamespace(gltf=SimpleNamespace(values=lambda accessor: accessor))  # an accessor here is its values
    linear = {"input": [1.0, 2.0], "output": [(10.0,), (20.0,)]}
    assert gltfskin._at(d, linear, [0.0, 1.5], (0.0,), False) == [(10.0,), (15.0,)]
    step = {"input": [1.0, 2.0], "output": [(10.0,), (20.0,)], "interpolation": "STEP"}
    assert gltfskin._at(d, step, [0.0], (0.0,), False) == [(10.0,)]
    # cubic: per key in-tangent, value, out-tangent
    cubic = {"input": [1.0, 2.0], "output": [(0.0,), (10.0,), (0.0,), (0.0,), (20.0,), (0.0,)]}
    assert gltfskin._cubic(d, cubic, cubic["input"], 0.0, False) == (10.0,)
