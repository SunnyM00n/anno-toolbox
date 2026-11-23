"""117's near terrain as the engine builds it (``render.terrainpages``, the tessellated and stored nodes of
``render.terraindraw``): the page a node reads, the coarse heights as the upload reads them, the quad patches and
their skirts, how each node draws; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 a page of a vanilla
island rebuilt by the game's compute shaders against the CPU rebuild, and the island view drawing its near nodes
tessellated (in a child process)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.render import terraindraw, terrainpages
from annotoolbox.render.terrainnodes import SNode


def test_a_node_reads_the_finest_page_that_holds_it_whole():
    keys = {(0, 1, 0), (1, 0, 0), (2, 0, 0)}
    assert terrainpages.finest_page(keys, 2, 16.0, 4.0) == (0, 1, 0)  # 4 tiles at x 16: the level-0 page 1
    assert terrainpages.finest_page(keys, 3, 8.0, 0.0) == (1, 0, 0)  # 8 tiles: in page 0 of level 1 (no 0-0 page)
    assert terrainpages.finest_page(keys, 6, 0.0, 0.0) == (2, 0, 0)  # 64 tiles: only the 64-tile page holds it
    assert terrainpages.finest_page(keys, 5, 64.0, 0.0) is None
    assert terrainpages.page_params((1, 3, 2), 320.0) == [3.0, 2.0, 10.0]  # position / 320 * 10 - (3, 2)


def test_the_coarse_heights_reach_the_upload_turned_over_in_x():
    size, step = 8, 0.5
    j, i = np.mgrid[0:17, 0:17]
    heights = (i * step * 0.25 + j * 0.01).astype(np.float32)  # world units, rising along x
    data, width = terrainpages.upload_heights(heights, step)
    assert width == 2 * size
    got = np.frombuffer(data, "<i2").reshape(width, width) / 256
    # texel i is the island's x = size - i / 2: the view's mirrored axes (the island matrix turns x over)
    assert np.allclose(got[3], [(size - k / 2) * 0.25 + 0.03 for k in range(width)], atol=1 / 256)
    coarse, width1 = terrainpages.upload_heights(heights[::2, ::2], 1.0)  # a large island's grid: onto half tiles
    assert width1 == width and np.allclose(np.frombuffer(coarse, "<i2"), np.frombuffer(data, "<i2"), atol=3)


def test_patches_and_skirts_follow_the_domain_shaders_corners():
    q = terraindraw.patch_indices(3, 2).reshape(-1, 4)
    assert q.tolist() == [[0, 3, 4, 1], [1, 4, 5, 2]]  # (x0, z0), (x0, z1), (x1, z1), (x1, z0)
    node, w = SNode(2, 0.0, 0.0), 9
    heights = np.full((w, w), 2.0, np.float32)
    raw = terraindraw.encode_vertices(heights, 0.5, node, w, terraindraw.vertex_normals(heights, 0.5))
    data, indices = terraindraw.with_skirts(raw, w, w, drop_tiles=1.0)
    v = np.frombuffer(data, np.uint8).reshape(-1, 16)
    ring = len(v) - w * w
    assert ring == 4 * (w - 1) + 1 and len(indices) == ((w - 1) ** 2 + ring - 1) * 4
    skirt = v[w * w :]
    assert (skirt[:, 9] < 128).all() and (v[: w * w, 9] > 128).all()  # the skirt flag: the normal's y below zero
    y = skirt[:, 2:4].copy().view("<u2").astype(float) / 256 - 128
    assert np.allclose(y, 1.0)  # one world unit below the edge (2.0)
    assert len(terraindraw.quad_triangles(indices)) == len(indices) // 4 * 6


class FakePages:
    def __init__(self, ready) -> None:
        self.m_ready, self.asked, self.coarse = ready, [], "coarse"

    def want(self, key, parts) -> None:
        self.asked.append(key)


def test_the_pages_a_frame_reads_are_not_let_go_however_many_it_reads():
    """A view whose nodes read more pages than are kept (a continental island) let the first go to make the last,
    and made them again the next frame, the nodes over them baked again: the frame never settled. The pages a frame
    asks for stay; the longest unused others go down to ``KEEP``."""
    from collections import OrderedDict
    from types import SimpleNamespace

    pages = terrainpages.CPages.__new__(terrainpages.CPages)
    pages.m_ready, pages.m_queue, pages._m_made, pages.m_version = OrderedDict(), OrderedDict(), {}, 0
    pages.m_wanted, pages._m_coarse_data, pages.m_slots, pages.m_coarse = set(), None, [], None
    gone = []
    ctx = SimpleNamespace(later=gone.append, native_pass=lambda step: None)
    many = terrainpages.KEEP + 40
    for k in range(many):
        pages.m_ready[(4, k, 0)] = (f"h{k}", f"n{k}")
        pages._m_made[(4, k, 0)] = 0
    for k in range(many):
        pages.want((4, k, 0), None)
    pages.run(ctx)
    assert len(pages.m_ready) == many and not gone and pages.m_wanted == set()
    pages.want((4, 0, 0), None)  # the next frame reads one: the others go, the longest unused first
    pages.run(ctx)
    assert len(pages.m_ready) == terrainpages.KEEP and (4, 0, 0) in pages.m_ready and gone[:2] == ["h1", "n1"]


@pytest.mark.xdist_group("island_view")
def test_each_node_draws_tessellated_stored_or_from_the_views_heights():
    terrain = terraindraw.CTerrain(None, None, 64.0, 0.5, 129, detailed=object())
    pages = {(0, 0, 0): "parts", (2, 0, 0): "parts"}
    terrain.m_source = terraindraw.SIslandSource(pages, {4: {(0, 0): (b"", np.zeros(0))}, 2: {(5, 5): (b"", None)}})
    terrain.m_pages = FakePages({(0, 0, 0): ("height", "normal")})
    terrain.m_files_shown = True
    assert terrain._kind(SNode(2, 0.0, 0.0))[:2] == ("detailed", (0, 0, 0))
    assert terrain._kind(SNode(3, 16.0, 16.0))[:2] == ("grid", None)  # its page (2, 0, 0) is not built yet: asked
    assert terrain.m_pages.asked[-1] == (2, 0, 0)
    assert terrain._kind(SNode(4, 0.0, 0.0))[:2] == ("stored", (0, 0, 0))  # far: its stored mesh, the page's normals
    assert terrain._kind(SNode(2, 20.0, 20.0))[:2] == ("stored", None)
    terrain.m_edited = np.zeros((16, 16), bool)
    terrain.m_edited[0, 0] = True  # an edit in the first block: those nodes draw from the view's heights
    assert terrain._kind(SNode(2, 0.0, 0.0))[0] == "grid" and terrain._kind(SNode(4, 0.0, 0.0))[0] == "grid"
    terrain.m_files_shown = False
    assert terrain._kind(SNode(2, 20.0, 20.0))[0] == "grid"
    values = terraindraw.node_values(3, 5, terrainpages.page_params((1, 1, 0), 64.0), 1)
    assert values["cTrnPackedLevels"] == 3 | 1 << 8 and values["cTrnNormalPageParams"] == [1.0, 0.0, 2.0]
    assert terraindraw.island_values(64.0)["cTrnHeightPageUVOffset"] == pytest.approx(8.5 / 528)


INSTALL_PAGE = """
import json, sys
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.infra import dxc
from annotoolbox.islands import gameterrain
from annotoolbox.islands.islandview import CIslandView
from annotoolbox.models import shaderview
from annotoolbox.render import terraindraw, terrainpages
from annotoolbox.render.rhiview import CRhiView
from annotoolbox.sources.anno117 import CAnno117Files
root = Path(sys.argv[1])
files = CAnno117Files(root)
island = "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m"
view = CIslandView(files, island)
key = (0, 14, 7)
data = files.read(f"{view.gamedata_dir}normalmaps/0/14x7.ctt")
cpu = gameterrain.rebuild_page(data)
stages = shaderview.CShaderSource(files, dxc.CDxc.find(anno117=root)).stage_set(terrainpages.STAGES)
app = QApplication([])

class View(CRhiView):
    def ready(self, ctx):
        self.pages = terrainpages.CPages(ctx.m_rhi, stages, float(view.size), terraindraw.island_values(view.size))
        self.pages.set_coarse(view.height_map / 4, 0.5)
        self.pages.want(key, terrainpages.SPageParts(*gameterrain.read_page(data)))
        self.pages.m_read_back = key
    def draw(self, ctx, screen, size_px):
        self.pages.run(ctx)

v = View("d3d12")
for _ in range(4):
    v.picture(16, 16)
rb = v.pages.m_readbacks
h = np.frombuffer(bytes(rb["heights"].data), "<f4").reshape(528, 528) * 256 - 128
comp = np.frombuffer(bytes(rb["compact"].data), "<u2").reshape(528, 528) / 65535 * 2 - 1
n = np.frombuffer(bytes(rb["normals"].data), np.uint8).reshape(512, 512, 4)
t = terrainpages.page_tiles(key[0])
at = (np.arange(528) - 8) / 512 * t  # the compaction's positions: texel index, not centre
zz, xx = np.meshgrid(key[2] * t + at, key[1] * t + at, indexing="ij")
want = np.clip(h - gameterrain.coarse(view.height_map / 4, xx, zz), -1, 1)
f = (cpu + 128) / 256
dx = (f[8:520, 9:521] - f[8:520, 8:520]) * 256 * 512 / t  # csComputeNormalMap's forward differences
dz = (f[9:521, 8:520] - f[8:520, 8:520]) * 256 * 512 / t
normal_x = -dx / np.sqrt(1 + dx * dx + dz * dz)
from annotoolbox.models import ddsdecode
bc4 = ddsdecode._compressed(b"ATI1", bytes(rb["blocks"].data), 528, 528, 0)[..., 0] / 255 * 2 - 1
print(json.dumps({"heights": float(np.abs(h - cpu).max()), "compact": float(np.abs(comp - want)[8:521, 8:521].mean()),
                  "border": float(comp[:8].mean()), "bc4": float(np.abs(bc4 - comp).mean()), "normal": float(np.abs(n[..., 0] / 255 * 2 - 1 - normal_x).max()),
                  "ready": len(v.pages.m_ready), "error": v.pages.m_error, "failed": v.m_failed}))
"""

INSTALL_VIEW = """
import json, sys, time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop.desktoprender import CRenderer, SViewOptions
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.render.camera import COrbitCamera
from annotoolbox.render.rhiview import CRhiView
app = QApplication([])
eapp = editorapp.CApp()
eapp.open("anno117", "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m")
s = CEditSession(json.loads(eapp.m_editor.island_json()), eapp.m_editor.edits())
s.finish_loading()

class View(CRhiView):
    def ready(self, ctx):
        self.r = CRenderer(ctx, s, eapp.m_editor)
    def draw(self, ctx, screen, size_px):
        self.r.render(screen, size_px, COrbitCamera((92.0, 4.0, 110.0), 30.0), self.opts)

view = View("d3d12")
view.opts = SViewOptions(native=True, game_terrain=True, game_light=True, layers=dict.fromkeys(("objects", "markers",
                        "props", "grass", "forest", "water"), False))
start = time.monotonic()
view.picture(160, 100)
while (view.r.m_game is None or view.r.busy()) and time.monotonic() - start < 300:
    time.sleep(0.2)
    view.picture(160, 100)
for _ in range(3):
    view.picture(160, 100)
st = view.r.m_stats
print(json.dumps({"detailed": st.get("terrain_detailed", 0), "stored": st.get("terrain_stored", 0),
                  "nodes": st.get("terrain_nodes", 0), "why": view.r.m_game.why_not(), "messages": view.r.m_messages,
                  "failed": view.m_failed}))
"""


def _installed():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    return root


def test_a_vanilla_page_rebuilt_by_the_games_compute_shaders_matches_the_cpu_rebuild():
    root = _installed()
    from tests.render.renderfakes import run_child

    got = run_child(INSTALL_PAGE.replace("sys.argv[1]", repr(root)))
    assert not got["error"] and not got["failed"] and got["ready"] == 1, got
    assert got["heights"] < 0.005  # the engine reads the 16-bit level as unorm: 65536/65535 of ours
    assert got["compact"] < 0.03  # the coarse heights' BC5 (two channels of 8-bit blocks) is all that differs
    assert got["normal"] < 0.02 and got["bc4"] < 0.005  # the page's BC4 blocks, as the copy laid them
    assert got["border"] > 0.9  # the border texels: the compaction's unsigned "x - border" wraps, the page is clamped


def test_the_island_view_draws_its_near_nodes_tessellated_from_the_pages():
    _installed()  # the child opens the island as the app does (its saved or detected game folders)
    from tests.render.renderfakes import run_child

    got = run_child(INSTALL_VIEW)
    assert got["why"] == "" and not got["failed"] and not got["messages"], got
    assert got["detailed"] > 0 and got["stored"] > 0, got


def test_the_height_pages_are_kept_as_stored_and_decoded_when_a_node_asks():
    """A continental island's decoded pages took 1.3 GB: they stay compressed, a page is decoded when asked and only
    the latest few are kept decoded."""
    from annotoolbox.editor.desktop.desktopgame import CPageFiles

    decoded = []
    pages = CPageFiles({(0, k, 0): bytes([k]) for k in range(4)}, lambda raw: decoded.append(raw) or raw[0], keep=2)
    assert (0, 3, 0) in pages and (0, 9, 0) not in pages and len(pages) == 4 and decoded == []
    assert pages[(0, 1, 0)] == 1 and pages[(0, 1, 0)] == 1 and decoded == [b"\x01"]  # decoded once
    pages[(0, 2, 0)], pages[(0, 3, 0)]
    assert list(pages.m_parts) == [(0, 2, 0), (0, 3, 0)]  # the oldest let go
    assert terrainpages.finest_page(pages, 2, 16.0, 4.0) == (0, 1, 0)
    assert terrainpages.finest_page(pages, 2, 80.0, 0.0) is None
