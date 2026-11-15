"""The RDM toolchain's commands (``rdmtools``) over files and folders, the layout names, the conventions check."""

import pytest
from tests import rdmsamples

from annotoolbox.formats import rdmlayout
from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.models import rdmnative
from annotoolbox.rdm import commands as rdmtools
from annotoolbox.rdm import conventions


def run(*argv) -> int:
    return rdmtools.main([str(a) for a in argv])


@pytest.fixture
def models(tmp_path):
    (tmp_path / "in" / "anim").mkdir(parents=True)
    (tmp_path / "in" / "house_lod0.rdm").write_bytes(rdmsamples.static_file())
    (tmp_path / "in" / "house_lod1.rdm").write_bytes(rdmsamples.one_mesh_file())
    (tmp_path / "in" / "crane_lod0.rdm").write_bytes(rdmsamples.skinned_file())
    (tmp_path / "in" / "anim" / "crane_work.rdm").write_bytes(rdmsamples.animation_file())
    return tmp_path


def test_a_folder_goes_to_glTF_and_back_unchanged(models, capsys):
    assert run("export", models / "in", "--anims", models / "in" / "anim", "-o", models / "glb") == 0
    assert "skipped" in capsys.readouterr().out  # the animation file goes along with its mesh, not alone
    assert run("import", models / "glb", "-o", models / "back") == 0
    for name in ("house_lod0.rdm", "house_lod1.rdm", "crane_lod0.rdm"):
        assert (models / "back" / name).read_bytes() == (models / "in" / name).read_bytes()
    assert (models / "back" / "crane_work.rdm").read_bytes() == rdmsamples.animation_file()
    assert run("diff", models / "in" / "crane_lod0.rdm", models / "glb" / "crane_lod0.glb", "--values") == 0
    assert "the same in every element and value" in capsys.readouterr().out


def test_nothing_is_overwritten_without_force(models, capsys):
    src = models / "in" / "house_lod0.rdm"
    assert run("export", src, "-o", models / "a.gltf", "--form", "gltf") == 0
    assert (models / "a.bin").is_file()
    assert run("export", src, "-o", models / "a.gltf", "--form", "gltf") == 1
    assert "not overwritten" in capsys.readouterr().out
    assert run("export", src, "-o", models / "a.gltf", "--form", "gltf", "--force") == 0


def test_a_lod_chain_and_a_layout_choice(models):
    assert run("export", models / "in" / "house_lod0.rdm", "--lods", "-o", models / "chain.glb") == 0
    assert run("import", models / "chain.glb", "-o", models / "chain" / "x.rdm") == 0
    assert sorted(p.name for p in (models / "chain").iterdir()) == ["house_lod0.rdm", "house_lod1.rdm"]
    layout = "P4h_N4b_G4b_B4b_T2h"
    assert run("import", models / "chain.glb", "-o", models / "plain", "--layout", layout) == 0
    assert {m.format for m in read_rdm((models / "plain" / "house_lod1.rdm").read_bytes()).meshes} == {layout}


def test_diff_names_what_differs(models, capsys):
    other = read_rdm(rdmsamples.static_file())
    other.materials[0].name = "slate"
    from annotoolbox.formats.rdmwrite import write_file

    (models / "other.rdm").write_bytes(write_file(other))
    assert run("diff", models / "in" / "house_lod0.rdm", models / "other.rdm") == 1
    out = capsys.readouterr().out
    assert "-material 0: name 'roof'" in out and "+material 0: name 'slate'" in out


def test_inspect_lists_every_element_and_marks_the_helpers(models, capsys):
    run("export", models / "in" / "crane_lod0.rdm", "-o", models / "c.glb")
    assert run("inspect", models / "in" / "crane_lod0.rdm", models / "c.glb", "--values") == 0
    out = capsys.readouterr().out
    assert "joint 2 'hand': parent 1" in out and "element 6 W4b" in out and "values " in out
    assert "helper (scene)" in out and "foreign" not in out


def test_validate_holds_files_against_117s_conventions(models, capsys):
    good = rdmnative.convert_static(rdmsamples.one_mesh_file()).data
    (models / "good.rdm").write_bytes(good)
    assert run("validate", models / "good.rdm") == 0
    assert run("validate", models / "in" / "crane_lod0.rdm") == 1
    out = capsys.readouterr().out
    assert "2 skeletons; 117 registers the first only" in out and "P4h_N4b_T2h" in out
    assert conventions.classify(read_rdm(rdmsamples.animation_file())) == "animation"


def test_relayout_writes_117s_layouts_and_lists_what_it_filled(models, capsys):
    assert run("relayout", models / "in" / "crane_lod0.rdm", "-o", models / "117") == 0
    got = read_rdm((models / "117" / "crane_lod0.rdm").read_bytes())
    assert len(got.skeletons) == 1 and {m.format for m in got.meshes} == {"P4h_N4b_G4b_B4b_T2h_I4b_W4b"}
    assert "filled:" in capsys.readouterr().out
    assert run("relayout", models / "in" / "crane_lod0.rdm") == 1  # never over its input


def test_layout_names_parse_to_the_elements_the_games_store():
    for name, elements in rdmlayout.VANILLA_117.items():
        assert tuple((e.usage, e.type, e.subtype, e.count) for e in rdmlayout.parse(name)) == elements
    for name in rdmlayout.KNOWN:
        assert rdmlayout.name_of(rdmlayout.parse(name)) == name
    for bad in ("N4b", "P4h_X2h", "P4h_C3b", "P4h_I2h"):
        with pytest.raises(rdmlayout.LayoutError):
            rdmlayout.parse(bad)
    assert all(round((rdmlayout.direction(bytes((b, 0, 0)))[0] + 1) * 127.5) == b for b in range(256))


def test_a_texture_goes_into_the_export_for_one_material_or_all(models):
    from annotoolbox.formats import imagewrite
    from annotoolbox.formats.gltf import read_gltf

    png = models / "roof.png"
    png.write_bytes(imagewrite.png_bytes(2, 2, bytes(range(16))))
    src = models / "in" / "house_lod0.rdm"
    assert run("export", src, "-o", models / "t.glb", "--texture", f"roof={png}") == 0
    doc = read_gltf(models / "t.glb").doc
    used = [m.get("pbrMetallicRoughness", {}).get("baseColorTexture") for m in doc["materials"]]
    assert doc["images"][0]["mimeType"] == "image/png" and used == [{"index": 0}, None]
    assert run("import", models / "t.glb", "-o", models / "t.rdm") == 0  # an .rdm holds no texture: the same file
    assert (models / "t.rdm").read_bytes() == src.read_bytes()
