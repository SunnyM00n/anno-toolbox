"""Our plain look as every game's fallback (viewer.md "Looks"): it draws each game's reference model as a look of its
own and inside the game's-shaders look when the game's shaders cannot draw; a pass that cannot draw waits for
nothing."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from annotoolbox.paths import REPO_ROOT

REFERENCE = {  # the reference models of the renderer's runs (tool-suite.md 21)
    "anno2205": ("ANNO2205_DIR", "data/graphics/props/air_buoy/air_buoy.cfg"),
    "anno1800": ("ANNO1800_DIR", "data/graphics/buildings/residence/residence_tier01_01/residence_tier01_01.cfg"),
    "anno2070": ("ANNO2070_DIR",
                 "data/graphics/buildings/ecos/ornaments/eden_line_green_house_ecos/eden_line_green_house_ecos.cfg"),
    "anno1404": ("ANNO1404_DIR", "data/graphics/buildings/factory/north/printing_house/n_printing_house.cfg"),
    "anno1701": ("ANNO1701_DIR", "data/graphics/objects/buildings/cultures/chinese/chinese_houses/chinese_house01/"
                                 "chinese_house_01.mcf"),
    "anno117": ("ANNO117_DIR", "data/base/graphics/library/props/storage/barrels/barrel_01.cfg"),
}  # fmt: skip

PICTURES = """
import json, sys
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources import gametree
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import rhi_picture
from annotoolbox.viewer.ownpass import COwnPass
app = QApplication([])
game, root, cfg, backend = sys.argv[1:5]
tree = gametree.open_game(game, Path(root))
scene = modelscene.build(tree, cfg, game)
out = {}
for name, look in (("plain", "editor"), ("broken", "shaders")):
    if name == "broken":
        COwnPass.why_not = lambda self: "a forced failure"
    state = SViewState(look=look, particles=False, ground=False)
    img = rhi_picture(CModelFiles(tree), scene, state, (120, 90), backend, 600)
    out[name] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(120)] for y in range(90)], float).tolist()
print(json.dumps(out))
"""


@pytest.mark.parametrize("game", sorted(REFERENCE))
def test_the_plain_look_draws_every_games_reference_model_and_stands_in_for_a_failed_pass(game):
    env_name, cfg = REFERENCE[game]
    root = os.environ.get(env_name)
    if not root or sys.platform != "win32":
        pytest.skip(f"needs an installed {game} ({env_name}) on Windows")
    from tests.render.renderfakes import render_backends

    backend = "d3d12" if game == "anno117" else "d3d11"
    if backend not in render_backends():
        pytest.skip(f"no {backend} device")
    repo = REPO_ROOT
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", PICTURES, game, root, cfg, backend], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=900)  # fmt: skip
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    got = {k: np.asarray(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}
    for name in ("plain", "broken"):
        background = got[name][:, :1]  # the sky at each row's left edge: the model stands in the middle
        covered = (np.abs(got[name] - background).sum(axis=2) > 30).mean()
        assert covered > 0.05, (game, name, covered)  # the model drawn over the sky
        assert got[name].std() > 5.0, (game, name)


def test_a_pass_that_cannot_draw_waits_for_nothing():
    """A frame drawn while the install was still read left every group waiting; once the pass knows it cannot draw
    (no compiler), the view must not count them as still loading (an offscreen picture waited its whole time)."""
    pytest.importorskip("PySide6")
    from annotoolbox.render.loader import CLoader
    from annotoolbox.viewer.ownpass import COwnPass

    p = COwnPass.__new__(COwnPass)
    p.m_sources, p.m_waiting, p.m_broken, p.SOURCE = CLoader(), 11, "", str
    assert p.busy() == 11  # the install not asked for yet: the groups wait
    p.m_sources.get("source", lambda: "the install's shaders")
    p.m_sources.m_futures["source"].result()
    assert p.busy() == 11  # read: the groups compile
    p.m_sources.forget("source")
    p.m_sources.get("source", lambda: OSError("no compiler"))
    p.m_sources.m_futures["source"].result()
    assert p.busy() == 0  # the read failed: nothing will come
    p.m_broken = "the frame failed"
    assert p.busy() == 0
    p.m_sources.close()


class FakePass:
    """A game's pass as the renderer asks it: what it says it drew itself, why it cannot, what it left."""

    m_ground, m_drawn_effects, m_drawn_decals = True, {"smoke.rdp"}, {"Decals/0"}
    why, rest = "", []
    m_switches: dict = {}

    def why_not(self) -> str:
        return self.why

    def draw(self, drawn, *_frame):
        return self.rest


def test_a_pass_that_does_not_draw_leaves_the_ground_the_effects_and_the_decals_to_the_view():
    """A pass that failed (or still compiles) kept saying it drew the ground, the effects and the decals of its last
    frame, so the view drew none of them: the model stood without its ground and its smoke."""
    pytest.importorskip("PySide6")
    from annotoolbox.viewer.modellights import SHADERS
    from annotoolbox.viewer.modelrender import CModelRenderer, SViewState

    r = CModelRenderer.__new__(CModelRenderer)
    r.m_game_pass, r.m_state, r.m_box, r.m_messages, r._m_game_drew = (
        FakePass(),
        SViewState(look=SHADERS),
        None,
        [],
        False,
    )
    light = {"sunDir": (0.0, 1.0, 0.0), "sunColor": (1.0, 1.0, 1.0)}
    drawn = [("part", {"key": 0}, None)]
    assert r._game_shaders(drawn, None, (4, 4), None, light) == []
    assert r._by_game("m_ground", None) and r._by_game("m_drawn_effects", set()) == {"smoke.rdp"}
    r.m_game_pass.rest = None  # a shader compiles: our look draws the frame, all of it
    assert r._game_shaders(drawn, None, (4, 4), None, light) == drawn
    assert not r._by_game("m_ground", None) and r._by_game("m_drawn_decals", set()) == set()
    r.m_game_pass.rest, r.m_game_pass.why = [], "the frame failed: our look draws it"
    assert r._game_shaders(drawn, None, (4, 4), None, light) == drawn
    assert not r._by_game("m_ground", None) and r._by_game("m_drawn_effects", set()) == set()


@pytest.mark.parametrize("name", ["fxcpass:CFxcPass", "fxcpass1404:CFxcPass1404", "forwardpass:CForwardPass"])
def test_a_frame_without_a_sea_hands_the_ground_back_to_the_view(name):
    """The sea of the frame before stood for the ground; a frame that draws nothing by the game's shaders (every part
    hidden or left to our look) draws no sea, and said it still did."""
    pytest.importorskip("PySide6")
    from importlib import import_module
    from types import SimpleNamespace as N

    from annotoolbox.render.loader import CLoader

    module, _, cls = name.partition(":")
    kind = getattr(import_module(f"annotoolbox.viewer.{module}"), cls)
    p = kind.__new__(kind)
    p.m_renderer = N(m_ctx=N(), m_scene=N(m_parts=[], m_decals=[], m_effects=[]), m_state=N(particles=False))
    p.m_models, p.m_switches, p.m_sources, p.m_ground, p.m_frame = {}, {}, CLoader(), True, None
    camera = N(eye=lambda: (0.0, 0.0, 0.0))
    assert p.draw([], None, (4, 4), camera, None, None, np.identity(4)) == [] and p.m_ground is False
    p.m_sources.close()


class FakeHeld:
    """A device object that counts its releases."""

    def __init__(self, log: list, name: str) -> None:
        self.log, self.name = log, name

    def release(self) -> None:
        self.log.append(self.name)

    close = release_scene = release


def test_a_released_renderer_gives_back_what_it_made_for_itself():
    """A view makes a new renderer per game opened and releases the one before: its shadow map (2048 x 2048), ground,
    stand-in textures, instance buffer and programs stayed on the device, every time."""
    pytest.importorskip("PySide6")
    from annotoolbox.viewer.modelrender import CModelRenderer

    log: list[str] = []
    r = CModelRenderer.__new__(CModelRenderer)
    r.m_items, r._m_decals, r._m_targets, r.m_textures = (
        {},
        {},
        None,
        {"missing": FakeHeld(log, "checker"), "s:gone.dds": None},
    )
    r.m_game_pass, r.m_loader, r.m_game_loader, r.m_particles = (
        FakeHeld(log, "pass"),
        FakeHeld(log, "loader"),
        FakeHeld(log, "game loader"),
        FakeHeld(log, "particles"),
    )
    r._m_ground = {"vaos": {"main": FakeHeld(log, "ground vao")}, "vbo": FakeHeld(log, "ground vbo"),
                 "ibo": FakeHeld(log, "ground ibo"), "joints": None}  # fmt: skip
    r._m_shadow = (FakeHeld(log, "shadow map"), FakeHeld(log, "shadow target"))
    r._m_dummy, r._m_dummy_3d, r._m_dummy_shadow = (FakeHeld(log, n) for n in ("grey", "no table", "no shadow"))
    r._m_rest_joints, r.m_inst = FakeHeld(log, "rest joints"), FakeHeld(log, "instance")
    r.m_fullscreen, r.m_prog = {"sky": FakeHeld(log, "sky vao")}, {"sky": FakeHeld(log, "sky program")}
    r.release()
    made = {"ground vao", "ground vbo", "ground ibo", "shadow map", "shadow target", "grey", "no table", "no shadow",
            "rest joints", "instance", "sky vao", "sky program", "checker", "particles"}  # fmt: skip
    assert made <= set(log) and len([n for n in log if n in made]) == len(made)  # each once
    assert r._m_ground is None and r._m_shadow is None and r.m_prog == {} and r.m_textures == {}


def test_no_imitation_of_a_games_materials_is_left_in_the_plain_look():
    """The decision of 2026-11-13: no prefiltered sky reflection, dye mix, metal, gloss or glow channels, or a
    game's own light falloff in our look; one look of ours besides the game's."""
    from annotoolbox.render import nativelook
    from annotoolbox.viewer import plainshading

    for name in ("envTex", "brdfTex", "dyeTex", "metalTex", "glow", "dyeTimes", "glossFactor"):
        assert name not in plainshading.LIT_FS
    looks = nativelook.options("anno2070", nativelook.SFound(why="none"), {"gl"}).looks
    assert [o.key for o in looks] == [nativelook.NATIVE, nativelook.PLAIN]
