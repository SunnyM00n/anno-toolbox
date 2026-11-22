"""2070's foam sheets (the falls) in the island editor's and the viewer's views: the map is data, not a colour
(docs/known-issues.md "Pink falls")."""

import pytest

from annotoolbox.editor import modelparts, modelviewer
from annotoolbox.formats import rdmread
from annotoolbox.models import modelread
from annotoolbox.render import shaders
from annotoolbox.render.modellibrary import FOAM_SHADER


def _sheet_rdm_mesh() -> rdmread.SMesh:
    """A foam sheet's vertices: position and a colour of stored bytes (blue 117, green 108, red 0, alpha 255)."""
    elements = (rdmread.SVertexElement(0, 6, 0, 4), rdmread.SVertexElement(5, 5, 4, 1))
    rows = [bytes(8) + bytes((117, 108, 0, 255))] * 4
    return rdmread.SMesh("w", elements, 0, 1, len(rows), 12, b"".join(rows), [], [], None, (0,) * 6, 0x5C)


def test_a_2070_foam_surface_is_a_part_of_the_foam_shader_with_the_speeds_its_mesh_gives():
    surface = modelread.SSurface("oldmetal", "maps/n_waterfall_diff_0.dds", "", "foam")
    piece = modelread.SPiece("rdm/fall.rdm", modelread.SPlace(), surfaces=(surface,))
    (part,) = modelviewer.rdm_parts(piece)
    assert part["shader"] == FOAM_SHADER == modelviewer.FOAM_SHADER
    speed0, speed1, opacity = modelread.foam_values(_sheet_rdm_mesh())
    assert speed0 == pytest.approx(0.5 * 108 / 255) and speed1 == pytest.approx(117 / 255)
    assert opacity == pytest.approx(0.25)


def test_the_speeds_of_a_file_that_does_not_read_are_the_full_ones():
    def missing(_path: str) -> bytes:
        raise OSError("not there")

    assert modelread.mesh_foam_values(missing, "x.rdm") == (0.5, 1.0, 0.25)
    assert modelread.mesh_foam_values(lambda _p: b"not an rdm", "x.rdm") == (0.5, 1.0, 0.25)


CFG = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/x/rdm/fall.rdm</FileName><Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>31</ShaderID>
<cModelDiffTex>data/x/maps/n_waterfall_diff.psd</cModelDiffTex><DIFFUSE_ENABLED>1</DIFFUSE_ENABLED>
<cAlphaFactor>1</cAlphaFactor><cFloat3Value.x>0.2</cFloat3Value.x><cFloat3Value.y>0.45</cFloat3Value.y>
</Config></Materials></Config></Models></Config>"""


def test_a_117_fall_material_on_the_mods_fall_slot_carries_its_speeds_and_the_shaders_quarter_opacity():
    (part,) = modelparts.model_parts(CFG)["parts"]
    assert part["shader"] == FOAM_SHADER
    assert part["foam"] == pytest.approx([0.2, 0.45, 0.25])


def test_the_model_shader_has_a_foam_branch_that_reads_the_map_as_data():
    source = shaders.MODEL_FS
    assert "kind == 4" in source and "uniform vec3 foam" in source and "pow(a.r * b.r, 2.2)" in source
