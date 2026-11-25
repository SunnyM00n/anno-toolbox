"""The distance clusters without a GPU (``render.distanceclusters``): a synthetic baked data file's far-view model
read (and refused where it does not read), the atlas and material records, a region's merged mesh as the cluster
shader unpacks it."""

from __future__ import annotations

import struct

import numpy as np
import pytest
from tests.render.renderfakes import FAR_GLOSS, FAR_TILE, far_view_bfg

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.render import distanceclusters as dc


def test_a_far_view_model_is_read_with_its_triangles_materials_and_tiles():
    far = dc.read_far_view(far_view_bfg())
    assert far.positions.tolist() == [[0, 0, 0], [1, 0, 0], [0, 2, 1]]
    assert far.uvs.tolist() == [[0, 0], [1, 0], [0, 1]]
    assert far.triangles.tolist() == [[0, 1, 2]]
    assert far.surfaces == (dc.SFarMaterial(1, (1.0, 0.5, 2.0), dc.DEFAULT_GLOSS, 0),)
    assert far.tiles == ((FAR_TILE, FAR_GLOSS),)


def test_what_the_game_does_not_merge_reads_as_none():
    for flags in (0, dc.HAS_MODELS | dc.FULL_MODELS, dc.HAS_MODELS | dc.EXCLUDED):
        assert dc.read_far_view(far_view_bfg(flags)) is None
    no_low = filedb.write(STag("", [STag("Config", [SAttrib("ConfigType", struct.pack("<I", 9))])]))
    assert dc.read_far_view(no_low) is None


def test_a_vertex_without_its_material_takes_the_stand_in():
    far = dc.read_far_view(far_view_bfg(materials=[], vertex_material=0xFFFF))
    assert far.surfaces == (dc.STAND_IN,) and far.materials.tolist() == [0, 0, 0]


def test_a_far_view_model_that_does_not_read_raises_its_own_error():
    for data in (b"", far_view_bfg()[:40]):  # empty, cut short
        with pytest.raises(dc.CFarViewError):
            dc.read_far_view(data)
    with pytest.raises(dc.CFarViewError, match="index past"):
        dc.read_far_view(far_view_bfg(extra_index=5))
    with pytest.raises(dc.CFarViewError, match="tile"):
        dc.read_far_view(far_view_bfg(tile=FAR_TILE[:100]))


def test_a_material_record_packs_its_type_partition_colour_and_gloss_as_the_shader_unpacks_them():
    packed0, colour, params, padding = struct.unpack("<4I", dc.material_record(1, 300, (2.0, 1.0, 0.0), 1.0))
    assert packed0 & 0xF == 1 and (packed0 >> 4) & 0xFFFF == 300
    assert (colour & 2047, (colour >> 11) & 2047, colour >> 22) == (2047, 1024, 0)  # over 0..2
    assert params == 128 and padding == 0


def test_the_atlas_places_each_distinct_tile_once_and_shares_its_materials():
    atlas = dc.CClusterAtlas()
    far = dc.read_far_view(far_view_bfg())
    first, again = atlas.add(far), atlas.add(far)
    assert first.tolist() == again.tolist() == [0] and len(atlas.m_partitions) == 1
    assert len(atlas.m_records) == 16 and atlas.m_serial == 2
    levels = atlas.levels("diff_alpha")
    assert [len(data) for data, _pitch in levels] == [(512 >> mip) ** 2 * 16 for mip in range(4)]
    mip0 = np.frombuffer(levels[0][0], np.uint8).reshape(512, 512, 16)
    assert mip0[:8, :8].tobytes() == FAR_TILE[: 64 * 16]  # partition 0, the finest mip's 8 x 8 blocks
    assert np.frombuffer(levels[3][0], np.uint8)[:16].tobytes() == FAR_TILE[84 * 16 :]


def test_a_full_atlas_leaves_the_model_out():
    atlas = dc.CClusterAtlas()
    atlas.m_partitions = {(bytes([k % 256, k // 256]), b""): k for k in range(dc.MAX_PARTITIONS)}
    assert atlas.add(dc.read_far_view(far_view_bfg())) is None


def _unpack(mesh: dc.SClusterMesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Positions, UVs and materials as ``distance_cluster.fx`` unpacks a vertex."""
    words = np.frombuffer(mesh.vertices, "<u4").reshape(-1, 2)
    quantised = np.stack([words[:, 0] & 255, (words[:, 0] >> 8) & 63, (words[:, 0] >> 14) & 255], axis=1)
    positions = (quantised / np.array([255, 63, 255]) * 2 - 1) * mesh.scale + mesh.centre
    uvs = np.stack([(words[:, 0] >> 22) & 31, (words[:, 0] >> 27) & 31], axis=1) / 31
    return positions, uvs, (words[:, 1] >> 18) & 0x3FFF


def test_a_regions_instances_merge_into_one_mesh_the_shader_unpacks_back():
    far = dc.read_far_view(far_view_bfg())
    places = np.repeat(np.identity(4, np.float32)[None], 3, axis=0)
    places[1, :3, 3] = (4, 0, 0)
    places[2, :3, :3] = 0  # hidden by a scale of 0: left out
    mesh = dc.merge([dc.SPiece(far, places, np.array([7]))])
    positions, uvs, materials = _unpack(mesh)
    expected = np.concatenate([far.positions, far.positions + (4, 0, 0)])
    steps = np.array(mesh.scale) * 2 / np.array([255, 63, 255])
    assert np.all(np.abs(positions - expected) <= steps / 2 + 1e-5)
    assert np.frombuffer(mesh.indices, "<u4").tolist() == [0, 1, 2, 3, 4, 5]
    assert uvs.tolist() == np.tile(far.uvs, (2, 1)).tolist() and materials.tolist() == [7] * 6
    assert mesh.triangle_count == 2 and mesh.centre == (2.5, 1.0, 0.5)


def test_a_region_of_hidden_instances_has_no_mesh():
    far = dc.read_far_view(far_view_bfg())
    assert dc.merge([dc.SPiece(far, np.zeros((2, 4, 4), np.float32), np.array([0]))]) is None
    assert dc.merge([]) is None


def test_regions_and_the_cluster_record():
    assert dc.region_of(np.array([[0.5, 9, 15.9], [-0.1, 0, 16.0]]), 16.0).tolist() == [[0, 0], [-1, 1]]
    mesh = dc.SClusterMesh(b"", b"", (1.0, 2.0, 3.0), (4.0, 5.0, 6.0), 0)
    assert struct.unpack("<8f", dc.cluster_record(mesh, 0.5)) == (1, 2, 3, 0.5, 4, 5, 6, 0)
