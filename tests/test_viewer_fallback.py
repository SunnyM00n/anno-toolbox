"""Our plain look as every game's fallback (viewer.md "Looks"): it draws each game's reference model as a look of its
own and inside the game's-shaders look when the game's shaders cannot draw; a pass that cannot draw waits for
nothing."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

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
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
from annotoolbox.viewer.ownpass import OwnPass
app = QApplication([])
game, root, cfg, backend = sys.argv[1:5]
tree = gametree.open_game(game, Path(root))
scene = modelscene.build(tree, cfg, game)
out = {}
for name, look in (("plain", "editor"), ("broken", "shaders")):
    if name == "broken":
        OwnPass.why_not = lambda self: "a forced failure"
    state = ViewState(look=look, particles=False, ground=False)
    img = rhi_picture(ModelFiles(tree), scene, state, (120, 90), backend, 600)
    out[name] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(120)] for y in range(90)], float).tolist()
print(json.dumps(out))
"""


@pytest.mark.parametrize("game", sorted(REFERENCE))
def test_the_plain_look_draws_every_games_reference_model_and_stands_in_for_a_failed_pass(game):
    env_name, cfg = REFERENCE[game]
    root = os.environ.get(env_name)
    if not root or sys.platform != "win32":
        pytest.skip(f"needs an installed {game} ({env_name}) on Windows")
    from tests.test_rhi import _backends

    backend = "d3d12" if game == "anno117" else "d3d11"
    if backend not in _backends():
        pytest.skip(f"no {backend} device")
    repo = Path(__file__).resolve().parents[1]
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
    from annotoolbox.render.loader import Loader
    from annotoolbox.viewer.ownpass import OwnPass

    p = OwnPass.__new__(OwnPass)
    p.sources, p.waiting, p.broken, p.SOURCE = Loader(), 11, "", str
    assert p.busy() == 11  # the install not asked for yet: the groups wait
    p.sources.get("source", lambda: "the install's shaders")
    p.sources.futures["source"].result()
    assert p.busy() == 11  # read: the groups compile
    p.sources.forget("source")
    p.sources.get("source", lambda: OSError("no compiler"))
    p.sources.futures["source"].result()
    assert p.busy() == 0  # the read failed: nothing will come
    p.broken = "the frame failed"
    assert p.busy() == 0
    p.sources.close()


def test_no_imitation_of_a_games_materials_is_left_in_the_plain_look():
    """The owner's decision of 2026-11-13: no prefiltered sky reflection, dye mix, metal, gloss or glow channels, or a
    game's own light falloff in our look; one look of ours besides the game's."""
    from annotoolbox.render import nativelook
    from annotoolbox.viewer import plainshading

    for name in ("envTex", "brdfTex", "dyeTex", "metalTex", "glow", "dyeTimes", "glossFactor"):
        assert name not in plainshading.LIT_FS
    looks = nativelook.options("anno2070", nativelook.Found(why="none"), {"gl"}).looks
    assert [o.key for o in looks] == [nativelook.NATIVE, nativelook.PLAIN]
