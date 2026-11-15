"""Decals laid on the terrain and sent to their pass (``render.decalmesh``), read from a config by the island editor
(``editor.modelparts``) and the viewer (``viewer.modelscene``)."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.render import decalmesh

CFG = """<Config><ConfigType>MAIN</ConfigType>
<Decals><Config><ConfigType>DECAL</ConfigType><Type>Vehicle</Type><OnStreets>0</OnStreets>
<Extents.x>2</Extents.x><Extents.y>0.25</Extents.y><Extents.z>1</Extents.z>
<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>1</ShaderID><cModelDiffTex>a/track_diff.psd</cModelDiffTex>
<cOpacity>0.5</cOpacity></Config></Materials></Config>
<Config><ConfigType>DECAL</ConfigType><OnStreets>1</OnStreets>
<Extents.x>1</Extents.x><Extents.z>1</Extents.z>
<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>1</ShaderID><cModelDiffTex>a/ground_diff.psd</cModelDiffTex>
</Config></Materials></Config>
<Config><ConfigType>DECAL</ConfigType>
<Extents.x>1</Extents.x><Extents.z>1</Extents.z>
<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>1</ShaderID></Config></Materials></Config></Decals>
</Config>"""


def test_the_pass_follows_type_and_on_streets_as_117_draws_them():
    assert decalmesh.pass_of("Vehicle", True) == decalmesh.VEHICLE  # whatever OnStreets says
    assert decalmesh.pass_of("Terrain", True) == decalmesh.AFTER_STREETS
    assert decalmesh.pass_of("Terrain", False) == decalmesh.BEFORE_STREETS
    assert decalmesh.pass_of("Terrain-Baked", False) == decalmesh.pass_of("anything", False) == decalmesh.BEFORE_STREETS
    assert decalmesh.BEFORE_STREETS < decalmesh.AFTER_STREETS < decalmesh.VEHICLE  # the order they are drawn in


def test_a_decal_follows_the_ground_under_it():
    place = np.identity(4)
    place[:3, 3] = (10.0, 0.0, 5.0)
    verts, idx = decalmesh.conform((2.0, 1.0), place, lambda x, z: 0.5 * x + 0.0 * z, step=0.5)
    assert verts.shape[1] == 8 and len(idx) % 3 == 0 and idx.max() < len(verts)
    assert np.allclose(verts[:, 1], 0.5 * verts[:, 0] + decalmesh.LIFT, atol=1e-5)  # on the slope, lifted
    assert (verts[:, 0].min(), verts[:, 0].max(), verts[:, 2].min(), verts[:, 2].max()) == pytest.approx((8, 12, 4, 6))
    assert len(np.unique(verts[:, 0])) == 9  # 4 m at 0.5 m: eight cells along x
    corner = verts[np.argmin(verts[:, 0] + verts[:, 2])]
    assert tuple(corner[6:]) == (0.0, 0.0)  # u along x, v along z as on the flat rectangle


def test_the_island_views_height_sampler_reads_x_mirrored():
    heights = np.array([[0.0, 1.0], [0.0, 1.0]])  # rows z; Anno x = 1 is high
    at = decalmesh.heights_sampler(heights, 1.0, 1.0)
    assert at(np.array([0.0]), np.array([0.5]))[0] == pytest.approx(1.0)  # view x 0 is Anno x 1
    assert at(np.array([0.75]), np.array([0.5]))[0] == pytest.approx(0.25)


def test_the_island_editor_reads_a_configs_decals_with_their_pass():
    from annotoolbox.editor.modelparts import model_parts

    decals = model_parts(CFG)["decals"]
    assert [(d["kind"], d["on_streets"], d["extents"]) for d in decals] == [
        ("Vehicle", False, (2.0, 1.0)), ("Terrain", True, (1.0, 1.0)), ("Terrain", False, (1.0, 1.0))
    ]  # fmt: skip
    assert decals[0]["diffuse"] == "a/track_diff.psd" and decals[0]["opacity"] == 0.5
