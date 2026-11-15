"""Anno 1701's effect files (``formats.efc1701``) and the viewer's particles of them (``models.particles1701``,
``viewer.modelfiles.effects1701``) on texts written here; with ``ANNO1701_DIR`` every shipped file too."""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.formats import efc1701
from annotoolbox.models import particles1701
from annotoolbox.models.particlebake import BakeError
from annotoolbox.render.particlefx import turns
from annotoolbox.viewer.modelfiles import effects1701


def graph(name: str, value: float = 0.0, points: str = "", single: int = 1, top: float = 100.0) -> str:
    return (f"\t\t{name}\n\t\t{{\n\t\t\tMinimum 0.000000\n\t\t\tMaximum {top}\n\t\t\tSingle {single}\n"
            f"\t\t\tValue {value}\n\t\t\tLinear 1\n{points}\t\t}}\n")  # fmt: skip


def point(x: float, y: float) -> str:
    return f"\t\t\tPoint\n\t\t\t{{\n\t\t\t\tp {x} {y}\n\t\t\t\ti -0.1 0.0\n\t\t\t\to 0.1 0.0\n\t\t\t\tudt 0\n\t\t\t}}\n"


HEADER = """\t\tName "{name}"\n\t\tStartDelay 0\n\t\tDuration 1000\n\t\tLoop 1\n\t\tStateBlock "ParticleEffect"
\t\tSrcBlend ""\n\t\tDestBlend "ONE"\n\t\tPosition 0.000000 10.000000 0.000000\n\t\tRotation 0 0 0 1
\t\tScaleFactors 2.000000 2.000000 2.000000\n\t\tPositionSpline\n\t\t{{\n\t\t\tLinear 1\n\t\t}}
\t\tTexture "data\\graphics\\textures\\effects\\effects_big.tga"\n\t\tRect 0.500000 1.000000 0.625000 0.875000\n"""
FOUNTAIN = (
    "Effect\n{\n\tParticleEffect\n\t{\n" + HEADER.format(name="jet")
    + graph("Emit", 30.0) + graph("LifeTime", 1000.0, top=10000.0) + graph("Pitch", math.pi / 2, top=6.283185)
    + graph("Speed", 20.0) + graph("GravityY", -10.0)
    + graph("Alpha", 0.0, point(0.0, 1.0) + point(1.0, 0.0), single=0, top=1.0)
    + graph("SizeX", 0.0, point(0.0, 0.1) + point(1.0, 0.3), single=0) + graph("SizeY", 4.0)
    + "\t\tColor\n\t\t{\n\t\t\tColor\n\t\t\t{\n\t\t\t\tRGBA 1 0 0 1\n\t\t\t\tTime 0\n\t\t\t}\n"
    + "\t\t\tColor\n\t\t\t{\n\t\t\t\tRGBA 0 0 1 1\n\t\t\t\tTime 1\n\t\t\t}\n\t\t}\n\t}\n"
    + "\tGrannyEffect\n\t{\n\t\tName \"model\"\n\t\tFileName \"data\\graphics\\effects\\gr2\\x.gr2\"\n\t}\n}\n"
)  # fmt: skip


def test_the_text_reads_into_blocks_fields_graphs_and_colours():
    jet, model = efc1701.read_efc(FOUNTAIN.replace("\n", "\r\n"))
    assert (jet.kind, jet.name, model.kind) == ("ParticleEffect", "jet", "GrannyEffect")
    assert jet.position == (0.0, 10.0, 0.0) and jet.scale == (2.0, 2.0, 2.0) and jet.loop and jet.additive
    assert jet.texture == "data/graphics/textures/effects/effects_big.tga" and jet.rect == (0.5, 1.0, 0.625, 0.875)
    assert jet.graph("Emit").at(0.3) == 30.0 and jet.graph("Missing", 7.0).at(0.5) == 7.0
    assert jet.graph("SizeX").at(0.5) == pytest.approx(20.0) and jet.graph("Alpha").at(1.0) == 0.0
    assert jet.colors["Color"].at(0.5) == pytest.approx((0.5, 0.0, 0.5, 1.0))
    assert efc1701.is_efc1701(FOUNTAIN) and not efc1701.is_efc1701("<Effect0></Effect0>")
    with pytest.raises(efc1701.EfcError):
        efc1701.read_efc("Effect\n{\n\tParticleEffect\n\t{\n")


def test_a_smooth_graph_follows_its_tangents_between_its_points():
    smooth = efc1701.Graph(
        0.0, 1.0, False, 0.0, False, ((0.0, 0.0, 0.0, 0.0, 0.3, 0.3), (1.0, 1.0, -0.3, -0.3, 0.0, 0.0))
    )
    assert smooth.at(0.5) == pytest.approx(0.5, abs=1e-3) and smooth.at(0.0) == 0.0 and smooth.at(2.0) == 1.0
    eased = efc1701.Graph(
        0.0, 1.0, False, 0.0, False, ((0.0, 0.0, 0.0, 0.0, 0.3, 0.0), (1.0, 1.0, -0.3, 0.0, 0.0, 0.0))
    )
    assert eased.at(0.1) < 0.1 and eased.at(0.9) > 0.9  # flat tangents: slow at both ends


def test_a_fountain_bakes_into_a_seamless_loop_of_rising_and_falling_particles():
    (jet, model) = efc1701.read_efc(FOUNTAIN)
    baked = particles1701.bake(jet, "seed")
    assert baked.looped and baked.frame_count == 30 and 25 <= len(baked.particles) <= 35
    p = baked.particles[0]
    assert p.pos[0] == pytest.approx((0.0, 10.0, 0.0))  # the emitter's move; its scale doubles what it throws
    top = max(q[1] for q in p.pos)
    assert top == pytest.approx(10.0 + 2 * (20.0 - 10.0 / 2))  # a second up at 20, pulled down at 10
    assert p.color[0][:3] == (1.0, 0.0, 0.0) and p.color[-1][3] == pytest.approx(0.0)
    assert p.size[0] == pytest.approx((20.0, 20.0, 8.0)) and p.size[-1][0] == pytest.approx(60.0)
    assert baked.material.additive and baked.material.crop == (0.5, 0.875, 0.125, 0.125)
    assert particles1701.bake(jet, "seed").particles[3].pos == baked.particles[3].pos  # the same each time
    with pytest.raises(BakeError, match="GrannyEffect is not drawn"):
        particles1701.bake(model, "seed")


def test_puffs_sprays_and_cards():
    puffs = efc1701.read_efc("Effect\n{\n\tInstancingEffect\n\t{\n" + HEADER.format(name="smoke") +
        "\t\tLifeTime 2000\n\t\tMinSize 10\n\t\tMaxSize 30\n\t\tVelocity 5\n\t\tColorRed 255\n\t\tColorGreen 0\n"
        "\t\tColorBlue 0\n\t\tTargetRed 0\n\t\tTargetGreen 0\n\t\tTargetBlue 255\n\t\tLoop 0\n\t\tPPS 10\n\t}\n}\n")[0]  # fmt: skip
    baked = particles1701.bake(puffs, "s")
    assert not baked.looped and len(baked.particles) == 10  # the second Loop wins: one pass
    p = baked.particles[0]
    assert p.size[0][0] == pytest.approx(20.0) and p.size[-1][0] == pytest.approx(60.0)  # scaled by 2
    assert p.pos[-1][1] - p.pos[0][1] == pytest.approx(2 * 5 * 2.0, rel=0.05) and p.color[-1][2] == 1.0
    card = efc1701.read_efc("Effect\n{\n\tBillboardEffect\n\t{\n" + HEADER.format(name="flash").replace(
        "Rotation 0 0 0 1", "Rotation 0.707107 0 0 0.707107") + "\t\tAlignment 2\n" + graph("SizeX", 5.0) +
        graph("SizeY", 5.0) + "\t}\n}\n")[0]  # fmt: skip
    flash = particles1701.bake(card, "s")
    assert len(flash.particles) == 1 and flash.material.view_aligned == particles1701.FIXED
    turn = turns(np.array([flash.particles[0].rot[0]]))[0]
    assert turn[:, 1] == pytest.approx((0.0, 1.0, 0.0), abs=1e-5)  # turned a quarter about x: it lies flat


def test_the_viewer_gets_the_drawn_emitters_and_names_the_rest():
    found, left = effects1701(FOUNTAIN, "fx/jet.efc", lambda name: "found/" + name)
    assert len(found) == 1 and found[0].texture == "found/data/graphics/textures/effects/effects_big.tga"
    assert (
        len(left) == 1
        and left[0].startswith("fx/jet.efc #1: GrannyEffect is not drawn")
        and found[0].at(0.5).position.shape[1] == 3
    )


def test_every_shipped_effect_file_reads():
    folder = os.environ.get("ANNO1701_DIR")
    if not folder or not Path(folder).is_dir():
        pytest.skip("needs an installed Anno 1701 (ANNO1701_DIR)")
    from annotoolbox.sources import gametree

    tree = gametree.open_game("anno1701", Path(folder))
    paths = [p for p in tree.paths if p.endswith(".efc")]
    kinds = [e.kind for p in paths for e in efc1701.read_efc(tree.read(p).decode("latin-1"))]
    assert len(paths) >= 200 and kinds.count("ParticleEffect") >= 300
    found, left = effects1701(tree.read(paths[0]).decode("latin-1"), paths[0], str)
    assert found or left
