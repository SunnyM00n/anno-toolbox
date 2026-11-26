"""1800's and 2205's island views by their own terrain shaders (render.terrain1800, terrain2205; the looks
desktopgame1800, desktopgame2205): the constants, the pages and the bake's encoding against the shaders' own
formulas, the look chosen by the island's game; installed (ANNO1800_DIR / ANNO2205_DIR, Windows, Direct3D 12 and 11): a
synthetic island drawn by the games' shaders. docs/rendering/island-1800-2205-view.md."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.render import terrain1800, terrain2205, terraindraw
from annotoolbox.render.terrainnodes import SNode


def test_the_bakes_albedo_round_trips_through_the_games_ycc_decode():
    rng = np.random.default_rng(1)
    linear = rng.random((64, 3)) ** 2
    stored = np.round(terrain1800.ycc_encode(linear) * 255) / 255  # bytes, as the slice holds them
    assert np.abs(terrain1800.ycc_decode(stored) - linear).max() < 0.03
    grey = terrain1800.ycc_encode(np.full((1, 3), 0.2))[0]
    assert abs(grey[0] - 0.5) < 1e-6 and abs(grey[2] - 0.5) < 1e-6  # no chroma: the middle


def test_the_encode_shader_is_the_cpu_encode():
    text = terrain1800.ENCODE_FS
    for word in ("1.0 / 2.2", "0.299", "0.587", "0.114", "0.169", "0.331", "0.419", "0.081", "sqrt"):
        assert word in text  # ycc_encode's numbers
    assert "vec4(root(cb), y, root(cr)" in text  # stored as (Cb, Y, Cr): the shader's ``ycc.yxz``


def test_1800s_normal_page_holds_x_z_and_y():
    h = np.zeros((9, 9))
    h[:, 5:] = 1.0  # a step up along +x
    page = terrain1800.normal_page_xzy(h, 0.5)
    n = page[..., :3].astype(float) / 255 * 2 - 1
    xyz = n[..., [0, 2, 1]]  # the shader's sample.xzy
    assert np.allclose(np.linalg.norm(xyz, axis=-1), 1.0, atol=0.02)
    assert xyz[4, 4, 0] < -0.3 and xyz[0, 0, 1] > 0.99  # the slope leans the normal back; the flat is up
    assert np.array_equal(page[..., :2], terraindraw.normal_page(h, 0.5))  # 117's x and z


def test_the_cloud_stand_in_leaves_the_sun_on():
    def cloud_shadow(noise: float, density: float = 0.5, intensity: float = 1.0, invert: float = 1.0) -> float:
        lerp = min(1.0, max(0.0, 1.0 + (noise - 1.0) * 2.75 * density))  # functions_shadow.fxh CalculateCloudShadow
        return min(1.0, max(0.0, abs(invert - lerp) ** ((2.0 + invert) * intensity))) + (1.0 - invert)

    noise = terrain1800.FALLBACKS["cCloudTex"][3] / 255  # the frame's stand-in, the alpha the shaders read
    assert cloud_shadow(noise) == 1.0
    assert cloud_shadow(1.0) == 0.0  # a white stand-in shadowed the whole sun: 2205's terrain was nearly black


def test_1800s_node_constants_name_its_level_where_its_shader_reads_it():
    values = terrain1800.node_values(3, 7)
    assert values["cTrnChunkLevelParams"] == 3 and values["cTrnBakedTexArrayIndex"] == 7  # bit 4 clear: new normals


def test_2205s_nodes_are_mirrored_patches_in_the_views_axes():
    size = 64.0
    values = terrain2205.node_values(SNode(3, 8.0, 16.0), size)
    real = values["cTrnBakeChunkPos"][3] - terraindraw.UNIT  # vsTerrain: the real size is w less a nugget
    flip = values["cTrnBakeChunkOffstFLip"]
    x = 10.0
    placed = (real - x if flip[2] < 0 else x) + flip[0] * real  # the shader's own formula
    assert placed == size - x and flip[3] == 1.0
    assert values["cTrnBakeChunkPos"][:3] == [size - 8.0 - 8.0, 16.0, 3.0]


def test_2205s_global_normal_page_is_in_the_views_axes_and_cell_centred():
    h = np.zeros((9, 9))
    h[:, 5:] = 1.0  # higher towards the island's +x: towards the view's -x
    page = terrain2205.view_page(terrain1800.normal_page_xzy(h, 0.5))
    n = page[..., :3].astype(float) / 255 * 2 - 1
    # a texel per cell, as the shader samples it at position / size (it was a texel per sample: half a cell off)
    assert page.shape[:2] == (8, 8)
    assert n[4, 3, 0] > 0.3 and n[0, 0, 1] > 0.99  # the slope (island cell 4, view cell 3) faces the view's +x


def test_1800s_page_makes_117s_page_once():
    from unittest import mock

    h = np.random.default_rng(2).random((17, 17))
    with mock.patch.object(terraindraw, "normal_page", wraps=terraindraw.normal_page) as made:
        terrain1800.normal_page_xzy(h, 0.5)
    assert made.call_count == 1  # it was made twice: a continental island's page took 0.6 s at every stroke


def test_2205s_session_page_turns_the_normals_x_over():
    from annotoolbox.models import bc1encode, ddsdecode

    rng = np.random.default_rng(4)
    texels = rng.integers(0, 256, (64, 4, 4, 3)).astype(np.uint8)  # random blocks: both of BC1's modes
    raw = bc1encode.encode_blocks(texels.reshape(64, 16, 3)).tobytes()
    raw = raw[:8] + bytes([0x00, 0x80, 0x00, 0xF8]) + raw[12:]  # one three-colour block (c0 < c1)
    blocks = np.frombuffer(raw, np.uint8).reshape(-1, 8)
    before = ddsdecode._bc1_blocks(blocks, False)
    after = ddsdecode._bc1_blocks(np.frombuffer(terrain2205.negated_red(raw), np.uint8).reshape(-1, 8), False)
    opaque = before[..., 3] == 255
    assert np.abs(after[..., 0] - (255 - before[..., 0]))[opaque].max() <= 1  # a third's rounding
    assert np.array_equal(after[..., 1:3], before[..., 1:3])


def test_2205s_node_reads_the_session_page_that_holds_it_in_the_views_axes():
    size, page = 1024.0, (1, 5, 7)  # 32 tiles a page: tiles 160..192 across, 224..256 down
    params = terrain2205.page_params(size, page)
    w = size + terraindraw.UNIT
    for x, z in ((161.0, 225.0), (191.0, 255.0)):
        view_x = size - x  # the view's axes
        tc = np.array([view_x, z]) / w * params[2] - np.array(params[:2])  # psTerrain's formula
        flipped = (192.0 - x) / 32  # the page flipped left to right (session_page)
        assert np.allclose(tc, [flipped, (z - 224.0) / 32]), (x, z)
    whole = terrain2205.page_params(size, None)
    assert np.allclose(np.array([1000.0, 10.0]) / w * whole[2] - whole[:2], [1000 / size, 10 / size])


def test_2205s_stored_levels_are_keyed_by_our_node_levels():
    from annotoolbox.formats.filedb import SAttrib, STag

    def node(x: int) -> STag:
        verts = np.array([[x * 32, 32768, 0, 0, 0, 0, 0, 0]] * 3, "<u2").tobytes()
        return STag("i", [SAttrib("Vertices", verts), SAttrib("Indices", np.array([0, 1, 2], "<u2").tobytes())])

    empty = STag("i", [SAttrib("Vertices", b""), SAttrib("Indices", b"")])
    level0 = STag(
        "i", [SAttrib("LevelSize", (2).to_bytes(4, "little")), STag("Nodes", [node(0), empty, empty, node(4)])]
    )
    level1 = STag("i", [SAttrib("LevelSize", (1).to_bytes(4, "little")), STag("Nodes", [empty])])
    rd = STag("root", [STag("Terrain", [STag("Levels", [level0, level1])])])
    levels = terrain2205.stored_levels(rd)
    assert set(levels) == {2} and set(levels[2]) == {(0, 0), (1, 1)}  # 4 tiles a node: ours level 2; row by row
    assert list(levels[2][(1, 1)][1]) == [0, 1, 2]


def test_2205s_stored_node_asks_for_its_page_and_draws_with_it_once_in():
    t = object.__new__(terrain2205.CTerrain2205)
    t.m_source = terraindraw.SIslandSource({}, {3: {(1, 2): (b"", np.zeros(0))}})
    t.m_files_shown, t.m_edited, t.m_pages, t.m_detailed = True, None, None, None
    t.m_normal_pages, t.m_ready, t.m_wanted, t.m_failed = {(0, 0, 1): "p0", (1, 0, 0): "p1"}, {}, {}, set()
    from collections import OrderedDict

    t.m_ready = OrderedDict()
    node = SNode(3, 8.0, 16.0)  # 8 tiles: inside level 0's page (0, 1)
    assert t._kind(node) == ("stored", None, 0) and list(t.m_wanted) == [(0, 0, 1)]
    t.m_ready[(0, 0, 1)] = tex = object()
    assert t._kind(node) == ("stored", (0, 0, 1), tex) and t._kind(node)[2] is tex  # held, not its id
    assert t._kind(SNode(3, 0.0, 0.0))[0] == "grid"  # no stored mesh there: the view's heights


def test_2205s_stored_node_stands_in_while_its_quarters_bake_by_its_session_page():
    """2205's stored nodes name a session normal page (the terrain's own store, not height pages): a larger node drawn
    before stands in for its unbaked quarters while that page is still the one on the device."""
    from collections import OrderedDict
    from types import SimpleNamespace

    from annotoolbox.render.terrainnodes import CSliceCache

    parent = SNode(4, 0.0, 0.0)
    kids = parent.children(32.0)
    t = object.__new__(terrain2205.CTerrain2205)
    t.m_size_tiles, t._m_visible, t.m_cache, t.m_pages = 32.0, None, CSliceCache(8), None
    t.m_ready = OrderedDict({(0, 0, 0): (tex := object())})
    t.m_models = {n: SimpleNamespace(m_node=n, m_kind=("grid", None, 0)) for n in (parent, *kids)}
    t.m_models[parent].m_kind = ("stored", (0, 0, 0), tex)
    for n in (parent, *kids):
        t.m_cache.get(n)
    t.m_cache.baked(parent)
    t._m_drawn = {parent}
    wanted = [t.m_models[k] for k in kids]
    assert t.shown(wanted) == [t.m_models[parent]]
    t.m_ready[(0, 0, 0)] = object()  # the page let go and read again: the parent's model is stale
    assert t.shown(wanted) == wanted


def test_1800s_look_keeps_the_view_drawing_while_its_water_and_clouds_move_or_it_settles():
    """With the view's clock the sea, rivers and clouds move: the view drew only on input and the waves stood still;
    a still clock and a settled frame let it rest."""
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop.desktopgame1800 import CGameIsland1800
    from annotoolbox.editor.desktop.desktoprender import SViewOptions

    look = object.__new__(CGameIsland1800)
    look.m_renderer = SimpleNamespace(game_sea=False, game_inland=False)
    look.m_ending, look.m_pages, look._m_unbaked = SimpleNamespace(m_again=False), None, 0
    assert look._moving(SViewOptions())  # the clouds drift by the clock
    assert not look._moving(SViewOptions(waves=False))  # the water motion switched off holds the clock
    still = SViewOptions(particle_time=1.0)
    assert not look._moving(still)
    look._m_unbaked = 3  # nodes left to bake: the next frames bake them
    assert look._moving(still)
    look._m_unbaked, look.m_pages = 0, SimpleNamespace(busy=lambda: True)
    assert look._moving(still)
    look.m_pages, look.m_ending.m_again = None, True  # the exposure still moving
    assert look._moving(still)


def test_the_game_look_is_offered_for_1800_and_2205_islands(monkeypatch):
    from types import SimpleNamespace

    from annotoolbox.render import nativelook

    monkeypatch.setattr(nativelook, "direct3d_here", lambda: True)  # the Windows behaviour (CI runs on Linux)
    from annotoolbox.editor.desktop.desktopeditor import CIslandDoc
    from annotoolbox.editor.desktop.desktoprender import SViewOptions

    for game, terrain in (("anno1800", True), ("anno2205", False), (None, True)):
        island = {"game": game} if game else {}
        view = SimpleNamespace(m_opts=SViewOptions(), m_backend="d3d11", update=lambda: None)
        doc = SimpleNamespace(m_session=SimpleNamespace(m_island=island), m_view=view, m_info={})
        doc.game_key = lambda doc=doc: CIslandDoc.game_key(doc)
        doc.look_reason = lambda key, doc=doc: CIslandDoc.look_reason(doc, key)
        state = CIslandDoc.look_state(doc)
        # 117's islands: their game look draws on Direct3D 12 alone, so on Direct3D 11 it is greyed
        assert state["native"][0] == (game is not None) and state["game_terrain"][0] == terrain, game
        assert not state["look2070"][0], game
        CIslandDoc.apply_game_look(doc)  # an island of another game opens in its look on Direct3D 11
        assert view.m_opts.native == (game is not None) and view.m_opts.game_terrain == (game == "anno1800"), game
    for backend, native in (("d3d12", True), ("opengl", False), ("gl", False)):
        view.m_backend, view.m_opts = backend, SViewOptions()
        doc.m_session.m_island = {"game": "anno1800"}
        CIslandDoc.apply_game_look(doc)
        assert view.m_opts.native == native, backend  # the default device draws them too; OpenGL does not


def test_the_island_view_makes_the_look_of_the_islands_game():
    from annotoolbox.editor.desktop import desktopgame

    assert set(desktopgame.LOOKS) == {"anno1800", "anno2205"}
    for game, (module, name) in desktopgame.LOOKS.items():
        cls = getattr(__import__(f"annotoolbox.editor.desktop.{module}", fromlist=[name]), name)
        assert issubclass(cls, desktopgame.CGameIsland) and cls.GAME == game


def test_an_islands_light_is_its_regions_setting():
    from annotoolbox.editor.desktop.desktopgame1800 import setting_of

    assert setting_of("anno1800", "data/dlc06/sessions/islands/pool/colony02_s_01/x.a7m") == "colony_02"
    assert setting_of("anno1800", "data/sessions/islands/pool/moderate/moderate_s_01/x.a7m") == "moderate_01"
    assert setting_of("anno2205", "data/sessions/maps/session_earth_02/x.a6m") == "moderate_session_01"
    assert setting_of("anno2205", "data/sessions/maps/session_arctic_01/x.a6m") == "arctic_session01"


def test_1800s_space_hints_are_dropped_for_its_5_0_compile():
    class FakeShaderFiles:
        def exists(self, path):
            return True

        def read(self, path):
            return b"shared root_descriptor12 cbuffer cbTerrainNode : register(b1, space3)\n{ float a; }"

    source = terrain1800.CTerrainSource(FakeShaderFiles(), None, "anno1800")
    assert source._read("x.fxh") == b"shared root_descriptor12 cbuffer cbTerrainNode \n{ float a; }"


INSTALL_ISLAND = """
import json, os, sys, time
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.editor.desktop.desktoprender import CRenderer, SViewOptions
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.formats.a7m.reader import CGameFiles
from annotoolbox.islands.islandview import CFiles
from annotoolbox.othergames.installs import GAMES
from annotoolbox.render.camera import COrbitCamera
from annotoolbox.render.rhiview import CRhiView
from tests.editor.editorfakes import edit_island
app = QApplication([])
GAME, ROOT, BACKEND = sys.argv[1], sys.argv[2], sys.argv[3]

class Editor:
    m_files = CFiles(CGameFiles(GAMES[GAME].index(Path(ROOT), None)))
    m_a7m_path = ""
    def __getattr__(self, name):  # what a view asks an editor for: not here (loaded in the background, said)
        def missing(*args, **kw):
            raise FileNotFoundError(name)
        return missing

s = CEditSession(edit_island(objects=[], props=[], forest={"tiles": [], "models": []}, game=GAME))
s.finish_loading()

class View(CRhiView):
    def ready(self, ctx):
        self.r = CRenderer(ctx, s, Editor())
    def draw(self, ctx, screen, size_px):
        self.r.render(screen, size_px, COrbitCamera((8.0, 0.5, 8.0), 14.0), self.opts)

view = View(BACKEND)
out = {}
for native in (False, True):
    view.opts = SViewOptions(native=native, game_light=True, layers=dict.fromkeys(("objects", "markers", "props",
                            "grass", "forest", "water"), False))
    start = time.monotonic()
    img = view.picture(160, 120)
    while native and (view.r.m_game is None or view.r.m_game.busy()) and time.monotonic() - start < 240:
        time.sleep(0.2)
        img = view.picture(160, 120)
    for _ in range(3):  # the bake and the uploads land over a frame or two
        img = view.picture(160, 120)
    out[native] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(160)] for y in range(120)], float)
game = view.r.m_game
np.save(sys.argv[4], out[True])
print(json.dumps({"diff": float(np.abs(out[True] - out[False]).mean()), "std": float(out[True].std()),
                  "nodes": view.r.m_stats.get("terrain_nodes", 0), "why": game.why_not() if game else "none",
                  "look": type(game).__name__, "errors": view.r.m_ctx.m_errors[-3:], "messages": view.r.m_messages[-3:]}))
"""


@pytest.mark.parametrize(("game", "env", "look"), [("anno1800", "ANNO1800_DIR", "CGameIsland1800"),
                                                   ("anno2205", "ANNO2205_DIR", "CGameIsland2205")])  # fmt: skip
def test_the_island_views_terrain_is_drawn_by_the_games_own_shaders_alike_on_direct3d_12_and_11(
    game, env, look, tmp_path
):
    import os
    import sys

    root = os.environ.get(env)
    if not root or sys.platform != "win32":
        pytest.skip(f"needs an installed game ({env}) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    backends = [b for b in ("d3d12", "d3d11") if b in render_backends()]
    if not backends:
        pytest.skip("no Direct3D device")
    pictures = []
    for backend in backends:
        picture = tmp_path / f"{backend}.npy"
        args = f"{game!r}, {root!r}, {backend!r}"
        code = INSTALL_ISLAND.replace("sys.argv[1], sys.argv[2], sys.argv[3]", args).replace(
            "sys.argv[4]", repr(str(picture))
        )
        got = run_child(code, timeout=int(os.environ.get("ANNO_TEST_TIMEOUT", "600")))
        assert got["look"] == look and got["why"] == "" and not got["errors"], (backend, got)
        assert got["nodes"] > 0 and got["std"] > 3 and got["diff"] > 0.5, (backend, got)
        pictures.append(np.load(picture))
    # every node's bake into targets of its own: on Direct3D 12 a shared target left most slices black
    assert len(pictures) < 2 or np.abs(pictures[0] - pictures[1]).mean() < 1.0


def test_2205s_vertex_is_read_by_its_own_semantics():
    from annotoolbox.render import gamedraw
    from annotoolbox.render.gameshader import SInput

    inputs = [SInput("PositionOS", "uint4", "POSITION0", 0), SInput("NormalWS", "float4", "COLOR0", 1),
              SInput("TangentWS", "float4", "COLOR1", 2)]  # fmt: skip
    mesh = gamedraw.CMesh(bytes(32), terraindraw.STRIDE, terrain2205.ELEMENTS, np.arange(2, dtype=np.uint32), 2)
    fill, attrs = gamedraw.vertex_input(mesh, inputs)
    assert not fill and [a.offset() for a in attrs] == [0, 8, 12]  # every input from the vertex, none a stand-in


def test_2205s_page_reads_follow_the_shown_nodes_and_finish_what_they_began():
    from types import SimpleNamespace

    from annotoolbox.editor.desktop.desktopgame2205 import PAGES_IN_FLIGHT, CGameIsland2205

    class FakeLoader:
        def __init__(self):
            self.started, self.done = [], set()

        def get(self, job, work):
            if job not in self.started:
                self.started.append(job)
                return None
            return (4, b"") if job in self.done else None

        def forget(self, job):
            self.started.remove(job)

    added = []
    keys = [(0, x, 0) for x in range(PAGES_IN_FLIGHT + 4)]
    terrain = SimpleNamespace(m_wanted=dict.fromkeys(keys), m_normal_pages={k: f"p{k[1]}" for k in keys},
                              add_page=lambda key, page: added.append(key))  # fmt: skip
    look = object.__new__(CGameIsland2205)
    look.m_terrain, look.m_reading = terrain, {}
    look.m_renderer = SimpleNamespace(m_loader=FakeLoader(), m_editor=SimpleNamespace(m_files=None), _message=print)
    look._after_nodes()
    assert list(look.m_reading) == keys[:PAGES_IN_FLIGHT]  # a few at a time, nearest first
    terrain.m_wanted = dict.fromkeys(keys[-1:])  # the camera moved: one other page asked for
    look.m_renderer.m_loader.done = {("game-island-normal-page", k) for k in keys[:2]}
    look._after_nodes()
    assert added == keys[:2]  # the reads begun are taken in, asked for or not
    assert keys[-1] in look.m_reading and not set(keys[PAGES_IN_FLIGHT:-1]) & set(look.m_reading)  # nothing stale begun


def test_1800s_terrain_casts_once_per_cascade_into_that_cascades_part_of_the_map():
    from annotoolbox.render import gameshadow

    class FakeDraw:
        def prepare(self, render_pass, signature, batch, textures, fill):
            fill({})

        def draw(self, command_buffer, size_px):
            command_buffer.setViewport(None)  # as gamedraw.CMesh.draw: the whole target

    seen = []

    class FakeCommands:
        def setViewport(self, vp):
            seen.append(tuple(vp.viewport()))

    filled = []
    caster = terrain1800.CCaster(FakeDraw, cascaded=True, partition=lambda: 0.5)
    from unittest import mock

    with mock.patch.object(terrain1800.gameshader, "set_values", lambda _c, v: filled.append(v["cCurrentCascade"])):
        caster.m_instances = 3
        caster.prepare(None, None, None, {}, lambda c: None)
    assert filled == [0, 1, 2] and len(caster.m_draws) == 3  # a draw per cascade read
    caster.draw(FakeCommands(), (100, 100))
    first, second = (tuple(d * 100 for d in div) for div in gameshadow.DIVISIONS[:2])
    assert seen[0] == (first[0], 100 - first[1] - first[3], first[2], first[3])  # QRhi counts from the bottom
    assert seen[1] == (50.0, 50.0, 50.0, 50.0) and len(seen) == 3


def test_1800s_terrain_casts_its_front_faces_and_reads_its_partitions_reciprocal():
    t = object.__new__(terrain1800.CTerrain1800)
    made = []

    class FakeDraw:
        def __init__(self, ctx, program, mesh, state, dummies):
            made.append(self)

    from unittest import mock

    t2 = object.__new__(terrain2205.CTerrain2205)
    t.m_ctx = t.m_dummies = t2.m_ctx = t2.m_dummies = t.m_shared = t2.m_shared = None
    with mock.patch.object(terrain1800, "CFxcDraw", FakeDraw):
        t._caster_draw(None, (None, None))
        t2._caster_draw(None, (None, None))
    # 1800's cascades mirror once more (the view's mirror cancels: else its back faces, the skirts, cast); 2205's not
    assert made[0].m_is_mirrored is False and made[1].m_is_mirrored is True
    assert terrain1800.PARTITION_REC == 2.0  # a quarter of the map: "reciprocal width and height of the partition"


def test_1800s_session_heights_are_unorm_about_minus_128():
    from annotoolbox.render import pages1800

    for h in (-2.0, 3.25):
        raw, width = pages1800.coarse_texels(np.full((9, 9), h), 0.5)
        texels = np.frombuffer(raw, "<u2").astype(float) / 65535 * 256 - 128  # UnpackTerrainHeightSample
        assert width == 8 and len(texels) == 64 and np.allclose(texels, h, atol=0.01)


def test_1800s_tessellated_terrain_and_height_pages_compile_with_the_install():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from annotoolbox.editor.formats.a7m.reader import CGameFiles
    from annotoolbox.infra.fxc import CFxc
    from annotoolbox.islands.islandview import CFiles
    from annotoolbox.othergames.installs import GAMES
    from annotoolbox.render import pages1800

    files = CFiles(CGameFiles(GAMES["anno1800"].index(Path(root), None)))
    compiler = CFxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    stages, _state = terrain1800.compile_terrain(files, compiler, "anno1800", terrain1800.DETAILED, True)
    assert set(stages) == {"vs", "ps", "hs", "ds"}
    shadow, _state = terrain1800.compile_terrain(files, compiler, "anno1800", terrain1800.SHADOW_DETAILED, True)
    assert shadow["hs"].entry == "hsShadow"
    compute = pages1800.compile_stages(terrain1800.CTerrainSource(files, compiler, "anno1800"))
    assert set(compute) == {*pages1800.STAGES, "pack"} and "granularity" in compute


def test_a_nodes_shadow_box_is_in_the_views_axes():
    t = object.__new__(terrain1800.CTerrain1800)
    t.m_size_tiles, t.m_step_tiles, t.m_width_samples = 16.0, 0.5, 33
    t.m_heights = np.zeros((33, 33))
    t.m_heights[0:9, 0:9] = 3.0  # island tiles 0..4 on both axes
    box = t.node_box(SNode(2, 0.0, 0.0))  # 4 tiles at the island's corner
    assert box[:, 0].min() == 12.0 and box[:, 0].max() == 16.0  # x turned over
    assert box[:, 2].min() == 0.0 and box[:, 2].max() == 4.0
    assert box[:, 1].max() == 3.0 and box[:, 1].min() == 3.0 - terraindraw.GRID_SKIRT


def test_1800s_patches_run_u_along_z_so_the_tessellators_triangles_face_up():
    """1800's hull emits clockwise triangles and its technique culls back faces: in 117's corner order they faced down
    (measured, island-1800-2205-view.md 10); the skirts' quads turn with them."""
    t = object.__new__(terrain1800.CTerrain1800)
    t.m_size_tiles, t.m_step_tiles, t.m_width_samples = 16.0, 1.0, 17
    t.m_heights = np.zeros((17, 17))
    t._m_vertex_normals = terraindraw.CRegional(terraindraw.vertex_normals, cells=False)
    mesh = t._mesh(SNode(2, 4.0, 8.0), ("detailed", (0, 0, 0), 0))
    xyz = np.frombuffer(mesh.m_data, np.uint8).reshape(-1, terraindraw.STRIDE)[:, 0:8].copy().view("<u2")
    xz = xyz[:, [0, 2]].astype(float) * terraindraw.UNIT
    first = mesh.m_indices.reshape(-1, 4)[0]
    assert np.allclose(xz[first], [[4, 8], [5, 8], [5, 9], [4, 9]])  # (x0, z0), (x1, z0), (x1, z1), (x0, z1)
    _data, plain = terraindraw.with_skirts(b"\0" * 16 * 25, 5, 5)
    assert np.array_equal(mesh.m_indices.reshape(-1, 4), plain.reshape(-1, 4)[:, terrain1800.PATCH_CORNERS])


def test_1800s_session_heights_are_handed_on_though_no_pass_takes_their_batch():
    from unittest import mock

    from annotoolbox.render import pages1800

    pages = object.__new__(pages1800.CPages1800)
    pages.m_rhi, pages.m_coarse, pages.m_ready, pages._m_made = mock.MagicMock(), None, {}, {}
    cb, batch = mock.MagicMock(), mock.MagicMock()
    pages._run_upload(cb, batch, b"\0" * 32, 4)
    batch.uploadTexture.assert_called_once()
    cb.resourceUpdate.assert_called_once_with(batch)  # a batch no pass takes is dropped: the heights stayed zero


def test_1800s_session_height_texture_of_another_width_is_let_go_after_the_frame():
    from unittest import mock

    from annotoolbox.render import pages1800

    pages = object.__new__(pages1800.CPages1800)
    old = mock.MagicMock()
    old.pixelSize.return_value.width.return_value = 4
    pages.m_rhi, pages.m_coarse, pages.m_ready, pages._m_made, pages.m_ctx = (
        mock.MagicMock(),
        old,
        {},
        {},
        mock.MagicMock(),
    )
    pages._run_upload(mock.MagicMock(), mock.MagicMock(), b"\0" * 128, 8)
    old.destroy.assert_not_called()  # the frame's nodes were made with it
    pages.m_ctx.later.assert_called_once_with(old)


def test_1800s_granularity_kernel_reads_the_deltas_finest_first():
    from annotoolbox.render import pages1800
    from annotoolbox.render.terrainpages import PAGE_TEXELS, SPageParts

    deltas = [(np.full((n, n), k, np.uint8), 100.0 * (k + 1), 3 + k) for k, n in
              enumerate((PAGE_TEXELS // 4, PAGE_TEXELS // 2, PAGE_TEXELS))]  # fmt: skip
    parts = SPageParts(np.zeros((66, 66), np.uint16), deltas)
    desc = pages1800.levels_upload(parts)
    entries = [desc.entryAt(i) for i in range(desc.entryCount())]
    assert [e.level() for e in entries] == [0, 1, 2]
    assert [len(e.description().data()) for e in entries] == [PAGE_TEXELS**2, (PAGE_TEXELS // 2) ** 2,
                                                              (PAGE_TEXELS // 4) ** 2]  # fmt: skip

    class FakeRows:
        def __init__(self):
            self.rows, self.values = {}, {}

        def set_rows(self, name, rows):
            self.rows[name] = rows

        def set(self, name, value):
            self.values[name] = value

    pages = object.__new__(pages1800.CPages1800)
    pages.m_size_tiles, pages.m_island = 64.0, terraindraw.island_values(64.0)
    c = FakeRows()
    pages._granularity_values(c, (0, 0, 0), parts)
    assert [r[0] for r in c.rows["cScaleFactor"]] == [300.0, 200.0, 100.0]  # level 0, the finest, first
    assert [r[0] for r in c.rows["cQuantBits"]] == [5, 4, 3]
    assert c.values["cSrc1Size"] == [PAGE_TEXELS, PAGE_TEXELS]


def test_1800s_tessellated_node_reads_its_pages_granularity_map():
    t = object.__new__(terrain1800.CTerrain1800)
    key = (0, 1, 2)

    class FakePages:
        m_ready = {key: ("height", "normal")}
        m_granularity = {key: "granularity"}
        m_coarse = "coarse"

    t.m_pages = FakePages()
    own = t._page_textures(("detailed", key, 0))
    assert own["cNodeGranularityMap"] == "granularity" and own["cTerrainHeightTex"] == "coarse"
    FakePages.m_granularity = {}  # not made yet: the frame's stand-in (a third)
    assert "cNodeGranularityMap" not in t._page_textures(("detailed", key, 0))


INSTALL_NEAR = """
import json, math, sys, time
import numpy as np
from annotoolbox.suite.settings import CSettings
from PySide6.QtWidgets import QApplication
app = QApplication([])
from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop.desktoprender import CRenderer, SViewOptions
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.render.camera import COrbitCamera
from annotoolbox.render.rhiview import CRhiView
ROOT, BACKEND, TMP = sys.argv[1], sys.argv[2], sys.argv[3]
from pathlib import Path
CSettings(anno1800=ROOT, workspace=TMP + "/ws", mods=TMP + "/mods").save(Path(TMP) / "settings.json")
ed = editorapp.CApp(Path(TMP) / "settings.json")
ed.open("anno1800", "data/dlc01/sessions/islands/pool/moderate_c_01/moderate_c_01.a7m")
s = CEditSession(json.loads(ed.editor.island_json()), ed.editor.edits())
s.finish_loading()
cam = COrbitCamera((s.m_size_tiles - 400.0, s.height_at(400.0, 330.0), 330.0), 12.0)
cam.pitch = math.radians(35)
opts = SViewOptions(game_terrain=True, native=True, layers=dict.fromkeys(("objects", "markers", "props", "grass",
                   "forest", "water"), False))

class View(CRhiView):
    def ready(self, ctx):
        self.r = CRenderer(ctx, s, ed.editor)
    def draw(self, ctx, screen, size_px):
        self.r.render(screen, size_px, cam, opts)

view = View(BACKEND)
start = time.monotonic()
img = view.picture(1024, 640)
while (view.r.m_game is None or view.r.busy() or view.r.m_game.busy()) and time.monotonic() - start < 400:
    time.sleep(0.3)
    img = view.picture(1024, 640)
for _ in range(3):
    img = view.picture(1024, 640)
px = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(0, 1024, 16)] for y in range(320, 640, 16)], float)
print(json.dumps({"detailed": view.r.m_stats.get("terrain_detailed", 0), "sky": float((px[..., 2] > px[..., 1]).mean()),
                  "why": view.r.m_game.why_not(), "messages": view.r.m_messages[-3:],
                  "baked": type(view.r.m_game.m_bake).__module__, "unbaked": view.r.m_game._m_unbaked}))
"""


def test_1800s_tessellated_near_nodes_draw_where_the_camera_looks(tmp_path):
    """Installed: 12 units over 1800's continental island the near nodes are tessellated and the lower half of the
    picture is ground, not the sky that showed through them (island-1800-2205-view.md 10)."""
    import os
    import sys

    root = os.environ.get("ANNO1800_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    backend = next((b for b in ("d3d12", "d3d11") if b in render_backends()), None)
    if backend is None:
        pytest.skip("no Direct3D device")
    code = INSTALL_NEAR.replace("sys.argv[1], sys.argv[2], sys.argv[3]", f"{root!r}, {backend!r}, {str(tmp_path)!r}")
    got = run_child(code, timeout=int(os.environ.get("ANNO_TEST_TIMEOUT", "600")))
    assert got["why"] == "" and got["detailed"] > 0, got
    assert got["sky"] < 0.05, got  # bluer than green: the sky through the nodes
    assert got["baked"].endswith("bake1800") and got["unbaked"] == 0, got  # the ground by 1800's own bake (13)


def test_1800s_terrain_casts_only_into_the_cascades_its_nodes_box_reaches():
    from unittest import mock

    class FakeDraw:
        def prepare(self, render_pass, signature, batch, textures, fill):
            fill({})

        def draw(self, command_buffer, size_px):
            pass

    filled = []
    caster = terrain1800.CCaster(FakeDraw, cascaded=True, partition=lambda: 0.5)
    caster.m_box = np.array([[x, y, z] for x in (0.0, 2.0) for y in (0.0, 2.0) for z in (0.0, 2.0)])
    near = np.diag([0.1, 0.1, 0.1, 1.0])
    away = near.copy()
    away[3, 0] = -5.0
    caster.cull([near, away, near])
    with mock.patch.object(terrain1800.gameshader, "set_values", lambda _c, v: filled.append(v["cCurrentCascade"])):
        caster.m_instances = 3
        caster.prepare(None, None, None, {}, lambda c: None)
    assert filled == [0, 2] and caster._m_shown == [0, 2]  # the second cascade's part is not drawn


def test_1800s_terrain_nodes_draw_into_the_frames_pre_pass_by_its_own_technique():
    from types import SimpleNamespace
    from unittest import mock

    t = object.__new__(terrain1800.CTerrain1800)
    t.m_prez, t.m_prez_detailed, t.m_casting, t.m_casting_detailed = ("prez", "state"), None, None, None
    t.m_program = t.m_detailed = "colour"
    t.m_state, t.m_textures, t.m_size_tiles = "s", {}, 64.0
    t.m_island = {}
    made = []

    def draw(program, mesh, state, which):
        d = SimpleNamespace(m_program=program, m_mesh=mesh, m_state=state, which=which)
        made.append(d)
        return d

    node = SimpleNamespace(level=2, x=0.0, z=0.0, tiles=4.0)
    with mock.patch.multiple(t, _draw=draw, _tint=lambda n: None, _mesh=lambda n, k: "mesh", _values=lambda *a: {},
                             _page_textures=lambda k: {}, _own=lambda *a: {}, node_box=lambda n: np.zeros((8, 3)),
                             create=True):  # fmt: skip
        model = t._make(node, 0, ("grid", None, 0))
    assert model.m_prepass is made[1] and made[1].m_program == "prez" and made[1].m_mesh == "mesh"
    assert model.m_prepass.m_is_mirrored and terrain1800.PREZ_NORMALS == ("PREZ_NORMAL_RT_ENABLED", "1")
