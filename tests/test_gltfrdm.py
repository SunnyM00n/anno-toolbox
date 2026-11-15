"""glTF to RDM (gltfrdm) when a modelling tool has been at the file: its additions dropped or refused, never written."""

import json
import struct

import pytest
from tests import rdmsamples
from tests.test_rdmgltf import export, strip

from annotoolbox.formats import gltf
from annotoolbox.formats.gltf import Builder, Gltf
from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.formats.rdmwrite import write_file
from annotoolbox.rdm import gltfrdm
from annotoolbox.rdm.gltfdoc import GltfImportError, Options


def exported(name="crane_lod0.rdm", data=None, anims=()):
    builder, _ = export(name, data or rdmsamples.skinned_file(), anims)
    return builder


def run(builder: Builder, name="crane_lod0.rdm", **kw):
    g = Gltf(json.loads(json.dumps(builder.doc)), [bytes(builder.blob)])
    got = gltfrdm.import_gltf(g, name, Options(**kw))
    return {n: write_file(f) for n, f in got.files}, got.notes


def test_a_camera_a_light_and_the_armature_object_are_dropped_and_said():
    b = exported()
    roots = b.doc["scenes"][0]["nodes"]
    joint_roots = [r for r in roots if "mesh" not in b.doc["nodes"][r]]
    armature = b.add("nodes", {"name": "Armature", "children": joint_roots})  # what Blender puts above the bones
    camera = b.add(
        "nodes",
        {
            "name": "Camera",
            "camera": b.add("cameras", {"type": "perspective", "perspective": {"yfov": 1, "znear": 0.1}}),
        },
    )
    light = b.add("nodes", {"name": "Sun", "extensions": {"KHR_lights_punctual": {"light": 0}}})
    b.doc["scenes"][0]["nodes"] = [r for r in roots if r not in joint_roots] + [armature, camera, light]
    out, notes = run(b)
    assert out == {"crane_lod0.rdm": rdmsamples.skinned_file()}
    assert {n.split(":")[0] for n in notes} == {
        "dropped node 'Armature'",
        "dropped node 'Camera'",
        "dropped node 'Sun'",
    }


def test_a_bone_the_modelling_tool_added_is_dropped_unless_vertices_weigh_it():
    b = exported()
    skin = b.doc["skins"][0]
    root = skin["joints"][0]
    extra = b.add("nodes", {"name": "Armature_root", "children": [root]})
    b.doc["scenes"][0]["nodes"] = [n for n in b.doc["scenes"][0]["nodes"] if n != root] + [extra]
    skin["joints"].append(extra)  # a joint slot nothing weighs; its bind matrix is the identity
    ibms = Gltf(b.doc, [bytes(b.blob)]).values(skin["inverseBindMatrices"])
    skin["inverseBindMatrices"] = b.accessor(
        [*ibms, (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)], "MAT4", gltf.FLOAT
    )
    out, notes = run(b)
    assert out["crane_lod0.rdm"] == rdmsamples.skinned_file()
    assert any("dropped joint 'Armature_root'" in n for n in notes)
    attrs = b.doc["meshes"][0]["primitives"][0]["attributes"]
    joints = Gltf(b.doc, [bytes(b.blob)]).values(attrs["JOINTS_0"])
    attrs["JOINTS_0"] = b.accessor([(3, *j[1:]) for j in joints], "VEC4", gltf.UBYTE)  # now vertices weigh it
    with pytest.raises(GltfImportError, match="not the source's and carry vertex weights"):
        run(b)


def test_an_extra_uv_map_or_colour_layer_is_not_written_into_the_layout():
    b = exported("house_lod0.rdm", rdmsamples.static_file())
    prims = b.doc["meshes"][0]["primitives"]
    n = Gltf(b.doc, [bytes(b.blob)]).items("accessors")[prims[0]["attributes"]["POSITION"]]["count"]
    uv = b.accessor([(0.0, 0.0)] * n, "VEC2", gltf.FLOAT)
    colour = b.accessor([(1.0, 1.0, 1.0, 1.0)] * n, "VEC4", gltf.FLOAT)
    for p in prims:
        p["attributes"] = {**p["attributes"], "TEXCOORD_1": uv, "COLOR_2": colour}
    out, notes = run(b, "house_lod0.rdm")
    assert out["house_lod0.rdm"] == rdmsamples.static_file()
    assert sorted(n.split("dropped attribute ")[1].split(":")[0] for n in notes) == ["COLOR_2", "TEXCOORD_1"]


def test_a_primitive_without_a_material_is_refused_not_given_a_default():
    b = exported("house_lod0.rdm", rdmsamples.static_file())
    del b.doc["meshes"][0]["primitives"][0]["material"]
    with pytest.raises(GltfImportError, match="no material"):
        run(b, "house_lod0.rdm")


def test_morph_weights_and_scaling_keys_are_refused_and_unit_scale_keys_ignored():
    b = exported(anims=[("w.rdm", rdmsamples.animation_file())])
    anim = b.doc["animations"][0]
    times = anim["samplers"][0]["input"]
    node = anim["channels"][0]["target"]["node"]
    for scale, ok in (((1.0, 1.0, 1.0), True), ((2.0, 1.0, 1.0), False)):
        out = b.accessor([scale] * 3, "VEC3", gltf.FLOAT)
        anim["samplers"].append({"input": times, "output": out})
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1, "target": {"node": node, "path": "scale"}})
        if ok:
            assert run(b)[0]["w.rdm"] == rdmsamples.animation_file()
        else:
            with pytest.raises(GltfImportError, match="scales"):
                run(b)
        anim["channels"].pop()
    anim["channels"].append({"sampler": 0, "target": {"node": node, "path": "weights"}})
    with pytest.raises(GltfImportError, match="morph target"):
        run(b)


def test_step_and_cubic_keys_become_linear_rdm_keys():
    b = exported(anims=[("w.rdm", rdmsamples.animation_file())])
    anim = b.doc["animations"][1]  # the door: two keys at 0 and 1 s, 60 fps
    g = Gltf(b.doc, [bytes(b.blob)])
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
    b = Builder()
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
    b.doc["scenes"], b.doc["scene"] = [{"nodes": [0]}], 0
    out, notes = run(b, "rock.rdm")
    rdm = read_rdm(out["rock.rdm"])
    m = rdm.meshes[0]
    assert (m.name, m.format, [x.name for x in rdm.materials]) == ("rock", "P4h_N4b_G4b_B4b_T2h", ["stone"])
    assert m.positions()[0] == (-0.0, 2.0, 0.0) and m.indices == [0, 1, 2]  # node moved, mirrored, winding turned
    assert any("filled the tangent frame" in n for n in notes)
    assert rdm.source == rdm.exporter == ""  # no placeholder strings


def test_the_like_file_gives_the_material_order_and_the_layout():
    b = exported("house_lod0.rdm", rdmsamples.static_file())
    plain = Builder(strip(json.loads(json.dumps(b.doc))), b.blob)
    plain.doc["materials"] = plain.doc["materials"][::-1]
    for prim in plain.doc["meshes"][0]["primitives"] + plain.doc["meshes"][1]["primitives"]:
        prim["material"] = 1 - prim["material"]
    like = read_rdm(rdmsamples.static_file())
    out, _ = run(plain, "house_lod0.rdm", like=like)
    got = read_rdm(out["house_lod0.rdm"])
    assert [m.name for m in got.materials] == ["roof", "wall"] and got.materials[0].texture == "data/roof_diff.psd"
    assert [m.format for m in got.meshes] == [m.format for m in like.meshes]
    assert [s.material for s in got.meshes[0].submeshes] == [1, 0]


def test_one_mesh_of_several_is_taken_by_name_or_index():
    b = exported("house_lod0.rdm", rdmsamples.static_file())
    out, notes = run(b, "house_lod0.rdm", mesh="1")
    assert [m.name for m in read_rdm(out["house_lod0.rdm"]).meshes] == [""]
    assert any("dropped node 'house_lod0': a mesh left out" in n for n in notes)
    with pytest.raises(GltfImportError, match="no mesh named"):
        run(b, "house_lod0.rdm", mesh="nothing")


def test_joint_names_that_repeat_are_refused_or_numbered_on_request():
    b = exported()
    for j in b.doc["skins"][0]["joints"]:
        b.doc["nodes"][j]["name"] = "bone"
    with pytest.raises(GltfImportError, match="repeated"):
        run(b)
    out, _ = run(b, joint_names="index")
    assert [j.name for j in read_rdm(out["crane_lod0.rdm"]).skeletons[0].joints] == ["0", "1", "2"]


def test_a_joint_that_scales_is_refused():
    b = exported()
    skin = b.doc["skins"][0]
    g = Gltf(b.doc, [bytes(b.blob)])
    ibms = [list(m) for m in g.values(skin["inverseBindMatrices"])]
    ibms[0][0] *= 2.0
    skin["inverseBindMatrices"] = b.accessor([tuple(m) for m in ibms], "MAT4", gltf.FLOAT)
    with pytest.raises(GltfImportError, match="scales"):
        run(b)


def test_the_glTF_reader_takes_strides_and_sparse_accessors():
    b = Builder()
    payload = b"".join(struct.pack("<3f4x", k, 2 * k, 3 * k) for k in range(3))  # 16-byte stride, 12-byte values
    view = b.view(payload)
    b.doc["bufferViews"][view]["byteStride"] = 16
    b.add("accessors", {"bufferView": view, "componentType": gltf.FLOAT, "count": 3, "type": "VEC3"})
    where, what = b.view(struct.pack("<H", 1)), b.view(struct.pack("<3f", 9, 9, 9))
    sparse = {
        "count": 1,
        "indices": {"bufferView": where, "componentType": gltf.USHORT},
        "values": {"bufferView": what},
    }
    b.add("accessors", {"bufferView": view, "componentType": gltf.FLOAT, "count": 3, "type": "VEC3", "sparse": sparse})
    g = Gltf(b.doc, [bytes(b.blob)])
    assert g.values(0) == [(0, 0, 0), (1, 2, 3), (2, 4, 6)] and g.values(1)[1] == (9, 9, 9)
