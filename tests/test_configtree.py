"""``models.configtree``: model configs of the newer games and of 2070 read as a tree, on synthetic files."""

from __future__ import annotations

import numpy as np

from annotoolbox.models import configtree as ct

# a 117-style config: tag suffixes, a material heading, Anno's </> shorthand, levels of detail, a sequence
CFG117 = """<Config><ConfigType>MAIN</ConfigType>
<Files><Config><ConfigType>FILE</ConfigType>
<Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.x>1</><Position.y>0</><Position.z>2</>
<Rotation.x>0</><Rotation.y>0</><Rotation.z>0</><Rotation.w>1</><Scale>2</></Config></Transformer>
<Name>tree</><FileName>data\\base\\graphics\\props\\tree.cfg</FileName></Config></Files>
<Models><Config><ConfigType>MODEL</ConfigType>
<Materials><Config><ConfigType>MATERIAL</ConfigType><Name>wall</Name><ShaderID>8</ShaderID>
<Common [ModelCommon]>Common [ModelCommon]</Common [ModelCommon]>
<cModelDiffTex>data/base/graphics/walls/wall_diff.psd</cModelDiffTex><cDiffuseColor.r>0.5</cDiffuseColor.r>
<NORMAL_ENABLED>0</NORMAL_ENABLED><DistanceScale;>1</DistanceScale;></Config>
<Config><ConfigType>MATERIAL</ConfigType><Name>roof</Name></Config></Materials>
<MaterialLODInfos><i><Indices><i>0</i><i>1</i></Indices></i><i><Indices><i>1</i></Indices></i></MaterialLODInfos>
<Animations><Config><ConfigType>ANIMATION</ConfigType><FileName>data/anim/walk.fbx</FileName></Config></Animations>
<FileName>shared/base/graphics/house/rdm/house_lod0.rdm</FileName></Config></Models>
<Particles><Config><ConfigType>PARTICLE</ConfigType><FileName>data/fx/smoke.rdp</FileName>
<Transformer><Config><ConfigType>VISIBILITY_TRANSFORM</ConfigType><Conditions>4</Conditions></Config></Transformer>
</Config></Particles>
<Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>100691457</SequenceID><Looped>1</Looped>
<Track><TrackID>1</TrackID><TrackElement><Type>0</Type><StartTime>0</StartTime><EndTime>900</EndTime>
<ModelID>0</ModelID><AnimationID>0</AnimationID></TrackElement></Track>
<Track><TrackElement><Type>2</Type><StartTime>0</StartTime><EndTime>600</EndTime></TrackElement></Track>
</Config></Sequences>
</Config>"""

CFG2070 = """<m_Config><m_ConfigType>MAIN</m_ConfigType>
<m_Models><m_Config><m_ConfigType>MODEL</m_ConfigType><m_FileName>data\\graphics\\a.rdm</m_FileName>
<m_Transformer><m_Config><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType><m_Position.y>3</m_Position.y>
</m_Config></m_Transformer></m_Config></m_Models>
<m_Sequences><m_Config><m_ConfigType>SEQUENCE</m_ConfigType><m_SequenceID>5000</m_SequenceID>
<Track><TrackElement><Type>0</Type><m_StartTime>0</m_StartTime><m_EndTime>200</m_EndTime><m_ModelID>0</m_ModelID>
</TrackElement></Track></m_Config></m_Sequences>
</m_Config>"""


def test_a_newer_games_config_reads_as_a_tree_of_typed_parts():
    tree = ct.read_tree(CFG117)
    assert tree.kind == "MAIN" and [c.section for c in tree.children] == ["Files", "Models", "Particles", "Sequences"]
    model = tree.section_of("Models")[0]
    assert model.path == "Models/0" and [m.name for m in model.section_of("Materials")] == ["wall", "roof"]
    wall = tree.find("Models/0/Materials/0")
    assert wall.get("cDiffuseColor.r") == "0.5" and wall.number("ShaderID") == 8
    assert (ct.GROUP, "Common [ModelCommon]") in wall.fields and wall.get("DistanceScale") == "1"
    assert ct.material_lods(model) == [[0, 1], [1]]
    assert ct.mesh_file(model.section_of("Animations")[0]) == "data/anim/walk.rdm"


def test_placement_nests_the_orientation_transforms_and_states_leave_a_part_out():
    tree = ct.read_tree(CFG117)
    place = ct.placement(tree.section_of("Files")[0])
    assert place.position == (1.0, 0.0, 2.0) and place.scale == (2.0, 2.0, 2.0)
    assert ct.shown_always(tree.section_of("Files")[0])
    assert not ct.shown_always(tree.section_of("Particles")[0])  # shown only in a state of the object


def test_sequences_list_their_tracks_elements():
    (seq,) = ct.sequences(ct.read_tree(CFG117))
    assert seq.looped and seq.duration_ms == 900 and seq.label == "100691457 (6 / 110 / 1)"
    assert [[e.kind for e in t] for t in seq.tracks] == [[0], [2]]
    assert seq.tracks[0][0].end_ms == 900 and seq.tracks[0][0].animation == 0


def test_2070s_m_prefixed_config_reads_the_same_way():
    tree = ct.read_tree(CFG2070)
    model = tree.section_of("Models")[0]
    assert model.kind == "MODEL" and model.get("FileName") == "data\\graphics\\a.rdm"
    assert np.allclose(ct.placement(model).position, (0, 3, 0))
    (seq,) = ct.sequences(tree)
    assert seq.sequence_id == 5000 and seq.tracks[0][0].end_ms == 200


def test_file_names_as_the_game_loads_them():
    assert ct.shared_config("data/base/graphics/a.cfg") == "shared/base/graphics/a.cfg"
    assert ct.shared_config("shared/base/a.cfg") == ""
    have = {"m/a_lod1.rdm", "m/a_lod2.rdm"}
    assert ct.lod_files("m/a_lod0.rdm", have.__contains__) == ["m/a_lod0.rdm", "m/a_lod1.rdm", "m/a_lod2.rdm"]
    assert ct.lod_files("m/a.rdm", have.__contains__) == ["m/a.rdm"]
    assert ct.lod_files("m/a_lod2.rdm", have.__contains__) == ["m/a_lod2.rdm"]  # a chain starts at _lod0 as it is
    assert ct.lod_files("m/a_lod2.rdm", have.__contains__, any_level=True) == ["m/a_lod1.rdm", "m/a_lod2.rdm"]
    assert ct.lod_files("m/a_lod0.rdm", have.__contains__, any_level=True, count=2) == ["m/a_lod1.rdm"]


def test_the_model_configs_of_a_game_by_its_suffix():
    paths = ["a/x.cfg", "a/y.mcf", "a/z.rdm"]
    assert ct.model_configs(paths, "anno117") == ["a/x.cfg"]
    assert ct.model_configs(paths, "anno1701") == ["a/y.mcf"]
