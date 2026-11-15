"""What the property editor offers for a config's fields (``models.cfgfields``): choices, bits, colour groups with
their scale and the edits a picked colour makes, and the files a field may name."""

from __future__ import annotations

import pytest

from annotoolbox.models import cfgdoc, cfgfields
from annotoolbox.models.cfgdoc import Doc

LIGHT = b"""<Config><ConfigType>LIGHT</ConfigType>
<Diffuse.r>1</Diffuse.r>
<Diffuse.g>0.8</Diffuse.g>
<Diffuse.b>0.6</Diffuse.b>
<Diffuse.a>1</Diffuse.a>
<Range>2</Range>
</Config>
"""


def _fields(text: bytes = LIGHT) -> list[cfgdoc.Field]:
    doc = Doc.read(text, "a.cfg")
    return cfgdoc.fields(doc.parts().node)


def test_117_names_its_shaders_and_a_track_element_its_types():
    shaders = dict(cfgfields.choices("ShaderID", "MATERIAL", "anno117"))
    assert shaders["8"] == "Physically-Based" and shaders["22"] == "Plant" and "3" not in shaders
    assert [v for v, _l in cfgfields.choices("Type", "TrackElement", "anno2070")] == ["0", "1", "2"]
    assert [v for v, _l in cfgfields.choices("Type", "TrackElement", "anno1800")] == ["0", "1", "2", "3"]
    assert not cfgfields.choices("Type", "LIGHT", "anno117")  # the same word in another part is free


def test_1800_and_2205_name_the_shaders_their_install_gives_evidence_for():
    s1800 = dict(cfgfields.choices("ShaderID", "MATERIAL", "anno1800"))
    s2205 = dict(cfgfields.choices("ShaderID", "MATERIAL", "anno2205"))
    assert s1800["7"] == "Water" and s1800["18"] == "Low-Poly" and "8" not in s1800
    assert s2205["10"] == "Interior Mapping" and s2205["12"] == "Hair" and "18" not in s2205
    assert not cfgfields.choices("ShaderID", "MATERIAL", "anno2070")  # 2070 and 1404 index their shaders: no names
    assert dict(cfgfields.choices("Type", "TrackElement", "anno117"))["3"] == "Sound"


def test_the_condition_bits_have_the_numbers_the_notes_give():
    names = cfgfields.flag_names("Conditions", "anno117")
    assert len(names) == 32 and names[3] == "Selected" and names[5] == "Common01" and names[31] == "EnableShipWave"
    assert 1 << names.index("Common01") == 32  # "Conditions 32 (Common01)" in the notes
    assert not cfgfields.flag_names("Conditions", "anno2070") and not cfgfields.flag_names("Range", "anno117")
    assert cfgfields.flags_value(" 32 ") == 32 and cfgfields.flags_value("x") is None


def test_a_colour_group_needs_red_green_and_blue():
    groups = cfgfields.colour_groups(_fields())
    assert list(groups) == ["Diffuse"] and sorted(groups["Diffuse"]) == ["a", "b", "g", "r"]
    assert not cfgfields.colour_groups(_fields(b"<Config><A.r>1</A.r><A.g>1</A.g></Config>"))


@pytest.mark.parametrize(
    ("values", "scale"),
    [((1, 0.8, 0.6), 1), ((255, 128, 0), 255), ((2.5, 1, 1), None), ((300, 1, 1), None), ((-1, 0, 0), None),
     ((0, 0, 0), 1)],
)  # fmt: skip
def test_the_scale_of_a_colour_is_floats_or_bytes_or_none(values, scale):
    text = "".join(f"<C.{c}>{v}</C.{c}>" for c, v in zip("rgb", values, strict=True))
    group = cfgfields.colour_groups(_fields(f"<Config>{text}</Config>".encode()))["C"]
    assert cfgfields.colour_scale(group) == scale


def test_a_picked_colour_changes_only_the_channels_it_moves():
    group = cfgfields.colour_groups(_fields())["Diffuse"]
    assert cfgfields.colour_channels(group, 1) == [255, 204, 153, 255]
    assert cfgfields.colour_edits(group, [255, 204, 153, 255], 1) == []  # the same colour: no edit, no rounding
    changed = {f.name: text for f, text in cfgfields.colour_edits(group, [255, 0, 153, 128], 1)}
    assert changed == {"Diffuse.g": "0", "Diffuse.a": "0.502"}
    whole = cfgfields.colour_groups(_fields(b"<Config><C.r>255</C.r><C.g>0</C.g><C.b>7</C.b></Config>"))["C"]
    assert {f.name: t for f, t in cfgfields.colour_edits(whole, [250, 0, 7], 255)} == {"C.r": "250"}


def test_the_files_a_field_may_name_follow_its_value_then_its_name_then_its_part():
    assert cfgfields.file_suffixes("cModelDiffTex", "x.dds", "MATERIAL") == cfgfields.TEXTURES
    assert cfgfields.file_suffixes("FileName", "data/x.fbx", "MODEL") == cfgfields.MESHES
    assert cfgfields.file_suffixes("cModelNormalTex", "", "MATERIAL") == cfgfields.TEXTURES
    assert cfgfields.file_suffixes("FileName", "", "FILE") == cfgfields.CONFIGS
    assert cfgfields.file_suffixes("FileName", "x.cfg", "MODEL") == cfgfields.CONFIGS  # the value wins
    assert cfgfields.file_suffixes("Name", "", "LIGHT") == ()


def test_a_newer_game_lists_a_texture_by_its_source_name_and_no_lower_levels():
    paths = ["data/a/wall_diff_0.dds", "data/a/wall_diff_1.dds", "data/a/wall_diff_4.dds", "data/a/other.dds",
             "data/a/mesh.rdm", "data/a/wall_diff.png"]  # fmt: skip
    found = cfgfields.file_candidates(paths, cfgfields.TEXTURES, True)
    assert found == ["data/a/wall_diff.psd", "data/a/other.dds", "data/a/wall_diff.png"]
    assert cfgfields.file_candidates(paths, cfgfields.TEXTURES, True, "other") == ["data/a/other.dds"]
    assert "data/a/wall_diff_1.dds" in cfgfields.file_candidates(paths, cfgfields.TEXTURES, False)  # an older game
    meshes = cfgfields.file_candidates(["a/m_lod0.rdm", "a/m_lod1.rdm", "a/m_lod2.rdm"], cfgfields.MESHES, True)
    assert meshes == ["a/m_lod0.rdm"]
    assert len(cfgfields.file_candidates((f"a/{k}.rdm" for k in range(1000)), cfgfields.MESHES, True)) == 300


def test_a_pick_is_written_as_the_current_value_names_files():
    assert cfgfields.file_value("data/a/wall.psd", "data/a/old.png") == "data/a/wall.png"
    assert cfgfields.file_value("data/a/wall.psd", "") == "data/a/wall.psd"
    assert cfgfields.file_value("data/a/wall.psd", "data/a/old.dds") == "data/a/wall.psd"
    assert cfgfields.file_value("data/a/m_lod0.rdm", "data/a/old.fbx") == "data/a/m.fbx"
    assert cfgfields.file_value("data/a/m.rdm", "data/a/old.rdm") == "data/a/m.rdm"
