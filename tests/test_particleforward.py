"""2205's particle effects laid out for its own ``particle.fx`` (``render.particleforward``): synthetic effects, and
with an installed game in ``ANNO2205_DIR`` the game's particle pass compiles with its own compiler."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.formats.rdp117 import RdpMaterial
from annotoolbox.render import particleforward as pf
from annotoolbox.render.particlefx import Effect, turns


def _rotate(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """The shaders' ``RotateWithQuaternion``."""
    return p + 2.0 * np.cross(q[:3], np.cross(q[:3], p) + q[3] * p)


def _effect(materials, starts, counts, which, frames=8) -> Effect:
    n = int(sum(counts))
    pos = np.stack([np.arange(n), np.zeros(n), -np.arange(n)], -1).astype("f4")
    rot = np.zeros((n, 3), "f4")
    size = np.full((n, 3), 0.5, "f4")
    color = np.ones((n, 4), "f4")
    lives = (np.array(starts), np.array(counts), np.array(which))
    return Effect._with_tracks("fx", materials, ("atlas.dds", frames, 33.3, True), lives, (pos, size, rot, color))


def test_the_quaternions_turn_a_point_as_the_files_angles_do():
    angles = np.random.default_rng(3).uniform(-np.pi, np.pi, (40, 3))
    m = turns(angles)
    q = pf.quaternions(m)
    assert np.allclose(np.linalg.norm(q, axis=1), 1.0)
    p = np.array([0.3, -0.2, 0.7])
    for k in range(len(m)):
        assert np.allclose(_rotate(p, q[k]), m[k] @ p, atol=1e-6)


def test_the_tracks_put_each_tick_at_its_column_and_blank_the_rest():
    mats = [RdpMaterial(order=1, additive=True), RdpMaterial(order=0)]
    fx = _effect(mats, starts=[6, 1], counts=[4, 3], which=[0, 1])
    tr = pf.tracks(fx)
    assert tr.rows.tolist() == [1, 0]  # the material of order 0 first
    assert tr.runs == [(False, 0, 1), (True, 1, 1)]
    position, scale, born = (tr.textures[n] for n in ("cParticlePositionTex", "cParticleScaleTex", "cParticleBornTex"))
    assert position.shape == (2, 8, 4) and born.shape == (1, 2, 4)
    # the additive particle lives at ticks 6, 7, 0, 1 (wrapped); its first track tick is fx.first[0]
    alive = [t for t in range(8) if scale[1, t, 0] > 0]
    assert alive == [0, 1, 6, 7]
    got = (position[1, 7, :3] * 2 - 1) * tr.extents + tr.center
    assert np.allclose(got, fx.position[fx.first[0] + 1], atol=1e-5)
    assert np.allclose(born[0, 1, :3], (6 / 255, 1.0, 4 / 255))
    assert np.allclose(scale[1, :, 3] * 256, 0) and np.allclose(scale[0, :, 3] * 256, 1)  # the material index


def test_a_draw_takes_the_emitter_without_its_scale_the_time_as_a_share_and_the_materials_by_four():
    mats = [RdpMaterial(view_aligned=0, color_boost=13, crop=(0.5, 0.25, 0.25, 0.25)), RdpMaterial()]
    fx = _effect(mats, starts=[0, 2], counts=[3, 3], which=[0, 1])
    fx.sort = True
    tr = pf.tracks(fx)
    world = np.diag([-2.0, 2.0, 2.0, 1.0])
    world[:3, 3] = (1, 2, 3)
    v = pf.effect_values(fx, tr, pf.Placement(world, seconds=0.0333 * 2, eye=np.zeros(3)), tr.runs[0])
    assert np.allclose(v["cParticleEmitterMtxWorld"][3, :3], (1, 2, 3))  # row vectors: the translation last
    assert np.allclose(np.diag(v["cParticleEmitterMtxWorld"])[:3], (-1, 1, 1))
    assert v["cParticleEmitterScales"][0] == [2.0] * 4 and len(v["cParticleEmitterScales"]) == pf.MAX_FRAMES // 4
    assert v["cParticleAge"] == pytest.approx(2 / 8, abs=1e-3)
    assert v["cParticleMaterialViewAligned"] == [[0.0, 1.0, 0.0, 0.0]]
    assert v["cParticleMaterialColorBoost"][0][0] == pytest.approx(1.8, abs=0.02)
    assert v["cParticleMaterialCropsOrMovieSpeed"][0] == [0.5, 0.25, 0.25, 0.25]
    assert v["cParticleFlags"] == pf.EMITTER_BOUND | pf.SORTED and v["cSortedParticleIndices"]


def test_movies_lines_and_waves_are_left_to_our_drawer_and_additive_runs_are_never_lit():
    fx = _effect([RdpMaterial(line_type=1)], [0], [2], [0])
    assert pf.not_drawn(fx)
    fx = _effect([RdpMaterial()], [0], [2], [0])
    fx.lighting = fx.self_occlusion = True
    assert not pf.not_drawn(fx)
    assert ("PARTICLE_ENABLE_LIGHTING", "") in pf.defines(fx, False)
    assert pf.defines(fx, True)[-1] == ("PARTICLE_ADDITIVE", "") and ("PARTICLE_ENABLE_LIGHTING", "") not in pf.defines(
        fx, True
    )
    mesh = pf.quad_mesh()
    assert mesh.count == 4 and mesh.indices.tolist() == [0, 1, 2, 0, 2, 3]


def test_a_node_that_receives_shadows_takes_the_shadow_variant_on_its_blended_runs_only_and_soft_sets_bit_4():
    fx = _effect([RdpMaterial()], [0], [2], [0])
    fx.lighting = True
    assert pf.SHADOWED in pf.defines(fx, False, shadows=True) and pf.SHADOWED not in pf.defines(fx, False)
    assert pf.SHADOWED not in pf.defines(fx, True, shadows=True)  # the additive path has no diffuse term to darken
    tr = pf.tracks(fx)
    soft = pf.Placement(np.identity(4), soft=True, depth_bias=True)
    flags = pf.effect_values(fx, tr, soft, tr.runs[0])["cParticleFlags"]
    assert flags == pf.EMITTER_BOUND | pf.SOFT | pf.DEPTH_BIAS and pf.SOFT == 16 and pf.DEPTH_BIAS == 8
    assert pf.effect_values(fx, tr, pf.Placement(np.identity(4)), tr.runs[0])["cParticleFlags"] == pf.EMITTER_BOUND


@pytest.mark.skipif(not os.environ.get("ANNO2205_DIR"), reason="needs an installed Anno 2205 (ANNO2205_DIR)")
def test_the_installed_games_particle_pass_compiles_with_its_own_compiler():
    from annotoolbox.infra import fxc
    from annotoolbox.models import shaderforward
    from annotoolbox.render.particlefx import load_rdp
    from annotoolbox.sources import gametree

    folder = Path(os.environ["ANNO2205_DIR"])
    tree = gametree.open_game("anno2205", folder)
    source = shaderforward.ForwardSource(tree, fxc.Fxc.find(folder), "anno2205")
    fx = load_rdp(tree.read("data/graphics/particles/particle_lights/flashing_light_new_01.rdp"))
    tr = pf.tracks(fx)
    assert [r[0] for r in tr.runs] == [False, True]  # the ring blended, the flare and glows additive
    vs, ps = source.effect_pass(pf.FX, pf.defines(fx, True), pf.TECHNIQUE)
    assert set(pf.TEXTURES) <= {b.name for b in vs.reflection.bindings}
    assert "cParticleMaterialTex" in {b.name for b in ps.reflection.bindings}
    additive = source.technique_state(pf.FX, pf.defines(fx, True), pf.TECHNIQUE, pf.ADDITIVE_SLOT)
    assert additive.blend and additive.dest == "one" and not additive.depth_write
    vs, ps = source.effect_pass(pf.FX, pf.defines(fx, False, shadows=True), pf.TECHNIQUE)
    read = {b.name for b in ps.reflection.bindings}
    assert {"cShadowMap", "cReadOnlyDepthBuffer"} <= read  # the shadow variant and the soft fade's depth copy
