import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop import desktopislands, workbench
from annotoolbox.infra.i18n import tr
from annotoolbox.suite.settings import Settings

RIBBON_TABS = ["islands.home", "common.object", "islands.terrain", "islands.grid", "islands.path"]  # the tabs' ids


def test_the_window_lists_every_game_and_setup_only_without_a_workspace(tmp_path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"), workspace="ws")
    path = tmp_path / "settings.json"
    settings.save(path)
    window = workbench.MainWindow(editorapp.App(path))
    assert window.tabs.count() == 1 and window.tabs.tabText(0) == "Setup"  # a relative workspace: nothing works
    window.app.set_settings({"workspace": str(tmp_path / "ws")})
    window.reload()
    names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Setup" not in names and len(names) > 1  # no games in tmp_path: each tab says what it lacks
    dialog = desktopislands.SettingsDialog(window.app.settings, window)
    assert set(dialog.values()) == {key for key, _ in desktopislands.FOLDERS} | {"theme", "language"}
    assert dialog.values()["anno117"] == str(tmp_path) and dialog.values()["theme"] == "system"
    window.close()


def test_reload_islands_reads_the_games_lists_off_the_window_thread(tmp_path, monkeypatch):
    import threading

    from PySide6.QtWidgets import QApplication

    from annotoolbox.infra.i18n import tr

    QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(tmp_path), mods=str(tmp_path / "mods"), workspace=str(tmp_path / "ws"))
    path = tmp_path / "settings.json"
    settings.save(path)
    window = workbench.MainWindow(editorapp.App(path))
    names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    release, readers, real = threading.Event(), [], window.app.catalogue

    def slow():
        readers.append(threading.current_thread())
        assert release.wait(30)
        return real()

    monkeypatch.setattr(window.app, "catalogue", slow)
    reload_action = next(
        a for m in window.menuBar().actions() for a in m.menu().actions() if a.text() == tr("islands.reload_islands")
    )
    window.tabs.setCurrentIndex(1)
    reload_action.trigger()
    reload_action.trigger()  # asked for again while it reads: one refill, of the lists read last
    assert window.statusBar().currentMessage() == tr("islands.reading_island_lists")
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == names  # the panel stays until it is read
    filled = []
    monkeypatch.setattr(window, "_fill_game_tabs", lambda *a, real=window._fill_game_tabs: (filled.append(1), real(*a)))
    release.set()
    window.background.join()
    QApplication.processEvents()
    assert len(readers) == 2 and threading.main_thread() not in readers and filled == [1]
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == names and window.tabs.currentIndex() == 1
    monkeypatch.setattr(window.app, "catalogue", lambda: (_ for _ in ()).throw(OSError("the folder is gone")))
    reload_action.trigger()
    window.background.join()
    QApplication.processEvents()
    assert "the folder is gone" in window.statusBar().currentMessage() and window.tabs.count() == len(names)
    window.close()


def test_an_island_list_filters_and_acts_on_the_full_path():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    picked = []
    lst = desktopislands.IslandList(["data/a/n_l01.isd", "data/b/s_m02.isd"], [("Open", picked.append)])
    lst.search.setText("s_m")
    assert lst.list.count() == 1 and lst.list.item(0).text() == "s_m02.isd"
    lst.list.setCurrentRow(0)
    lst._act(picked.append)
    assert picked == ["data/b/s_m02.isd"]


def test_the_terrain_is_mirrored_in_x_and_lit_from_above():
    import numpy as np

    from annotoolbox.editor.desktop import desktopgeometry

    h = np.zeros((3, 3), "f4")
    h[:, 2] = 1.0  # rising towards the grid's last column, which the view puts at x = 0
    verts = desktopgeometry.terrain_vertices(h, 0.5)
    assert verts.shape == (9, 6)
    assert verts[0, 0] == 1.0 and verts[2, 0] == 0.0  # view x = size - x
    assert verts[0, 4] > 0.9 and verts[2, 3] > 0  # normals up, tilted away from the rise
    rows = desktopgeometry.terrain_vertices(h, 0.5, slice(1, 2))  # one row, for a brush stroke's update
    assert np.allclose(rows, verts[3:6])


def test_the_orbit_camera_turns_pans_zooms_and_casts_rays():
    import math

    import numpy as np

    from annotoolbox.render import camera

    cam = camera.OrbitCamera((10, 0, 10), 100)
    assert np.linalg.norm(cam.eye() - cam.target) == pytest.approx(100, rel=1e-4)
    cam.turn(0, 10_000)
    assert cam.pitch <= math.radians(89) + 1e-6
    cam.zoom(1000)
    assert cam.distance >= cam.MIN_DISTANCE
    slow, fast = (camera.OrbitCamera((0, 0, 0), 100) for _ in range(2))
    fast.speed = 2.0
    slow.pan(10, 0), fast.pan(10, 0)
    assert np.linalg.norm(fast.target) == pytest.approx(2 * np.linalg.norm(slow.target))
    m = cam.matrix(1.5)
    assert m.shape == (4, 4) and np.isfinite(m).all()
    cam = camera.OrbitCamera((10, 0, 10), 50)
    o, d = cam.ray(400, 300, 800, 600)  # the middle of the screen looks at the pivot
    t = np.dot(cam.target - o, d)
    assert np.linalg.norm(o + t * d - cam.target) < 1e-3
    heights = np.zeros((41, 41), "f4")  # a flat 20-tile island at height 0
    hit = camera.ground_hit(heights, 0.5, o, d)
    assert hit is not None and abs(hit[1]) < 1e-3 and np.linalg.norm(hit[[0, 2]] - [10, 10]) < 0.05


def test_a_view_whose_frame_raised_says_so_after_a_single_frame():
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QLabel

    from annotoolbox.editor.desktop.desktopview import IslandControls

    QApplication.instance() or QApplication([])
    view = SimpleNamespace(fps_label=QLabel(), failed="RuntimeError: no baker", _painted=True, _frame_times=[])
    IslandControls._hud(view)  # one frame, no frame time yet: a static view that failed paints no second frame
    assert "no baker" in view.fps_label.text()


def test_lakes_are_triangulated_and_rivers_made_ribbons():
    from annotoolbox.editor.desktop import desktopgeometry

    l_shape = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    tris = desktopgeometry.triangulate(l_shape)
    assert len(tris) == 4 and {i for t in tris for i in t} == set(range(6))
    verts, idx = desktopgeometry.ribbon([[0, 1, 0], [4, 1, 0]], [2, 2])
    assert verts.shape == (4, 3) and len(idx) == 6 and abs(verts[0, 2] - verts[1, 2]) == pytest.approx(2)


def test_the_game_terrain_look_carries_the_height_edits():
    """The Game terrain look draws the game's rebuilt surface of the saved island; a brush stroke must show on it as
    the build will put it there (the deltas are added before the height pages are generated)."""
    import numpy as np
    from tests.test_editsession import island

    from annotoolbox.editor.desktop.desktopframe import heights_with_edits
    from annotoolbox.editor.editsession import EditSession

    s = EditSession(island())
    game = s.base_heights + 0.05  # the game's near terrain differs from the heightmap by the pages' detail
    assert np.allclose(heights_with_edits(game, s), game)
    s.begin_height("raise", 2, 1.0, 8.0, 8.0)
    s.end_height()
    out = heights_with_edits(game, s)
    assert out[16, 16] == pytest.approx(game[16, 16] + s.edits["heights"]["8,8"], abs=1e-3)
    assert out[2, 2] == game[2, 2]  # untouched ground stays the game's


def test_the_renderer_draws_and_picks_offscreen():
    """A synthetic island drawn by the real renderer in a standalone OpenGL context (skipped without one)."""
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    import numpy as np
    from tests.test_editsession import island

    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.editor.editsession import EditSession
    from annotoolbox.render import camera

    s = EditSession(island())
    s.finish_loading()
    r = desktoprender.Renderer(ctx, s, None)
    w, h = 320, 200
    fbo = ctx.framebuffer([ctx.renderbuffer((w, h), 4)], ctx.depth_renderbuffer((w, h)))
    marker = next(e for e in s.entries if e.marker == "Herd")
    cam = camera.OrbitCamera((s.size - marker.position[0], 1.5, marker.position[2]), 12)
    looks = (
        {},
        {"look2070": True},
        {"shadows": False, "game_light": True, "game_terrain": True},
        {"underwater": True, "caustics": True},
    )
    for look in looks:
        opts = desktoprender.ViewOptions(**look)
        r.render(fbo, (w, h), cam, opts, overlays=[
            ("lines", np.array([[0, 0, 0, 1, 0, 0, 1], [1, 1, 1, 1, 0, 0, 1]], "f4"))])  # fmt: skip
        img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(h, w, 3)
        assert img.std() > 2  # ground, sea and sky, not one flat colour
    assert r.pick(cam, (w, h), w / 2, h / 2 - 20, desktoprender.ViewOptions()) is marker
    assert r.pick(cam, (w, h), 2, 2, desktoprender.ViewOptions()) is None  # the sky
    r.release()


def test_a_117_island_opens_in_the_games_look_on_direct3d_12():
    """The owner's decision (2026-11-13): a 117 island opens in the game's own shaders where the view draws on
    Direct3D 12 (another device: the editor's look, without the look's message); other games' islands keep theirs."""
    from PySide6.QtWidgets import QApplication
    from tests.test_editsession import island

    from annotoolbox.editor.desktop.desktopview import IslandView3D
    from annotoolbox.editor.editsession import EditSession

    QApplication.instance() or QApplication([])
    view = IslandView3D()
    sessions = {}
    for name, extra in (("117", {}), ("2070", {"mode": "2070"}), ("1800", {"game": "anno1800"})):
        sessions[name] = EditSession(island(**extra))
        sessions[name].finish_loading()
    view.load(sessions["117"])
    assert not view.opts.native  # OpenGL
    view.backend = "d3d12"
    view.load(sessions["117"])
    assert view.opts.native and view.opts.game_terrain
    for name in ("2070", "1800"):
        view.load(sessions[name])
        assert not view.opts.native and not view.opts.game_terrain, name
    view.unload()
    view.deleteLater()


def test_the_editor_ground_of_a_117_island_is_each_materials_flat_colour():
    """The owner's decision (2026-11-13): the editor's look is only the fallback of 117's, 1800's and 2205's islands,
    so their ground is each material's mean colour by its weight, no detail maps; 2070's islands keep their maps."""
    import io
    from types import SimpleNamespace

    from PIL import Image

    from annotoolbox.editor.desktop import desktopground

    def png(normal=False, **kw):
        img = Image.new("RGB", (8, 8), (200, 100, 0))
        img.putpixel((0, 0), (0, 100, 0))
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()

    ground = desktopground.GroundDraw()
    ground.editor = SimpleNamespace(texture_png=lambda path, **kw: png(**kw))
    mats = {1: {"id": 1, "diffuse": "a.psd", "normal": "a_norm.psd"}, 2: {"id": 2, "diffuse": None}}
    ground.session = SimpleNamespace(paint_materials=mats, island={})
    size, layers = ground._layers((1, 2))
    assert size == desktopground.PLAIN_SIZE and len(layers) == 2
    (rgba, normal), (grey, _flat) = layers
    assert rgba[:4] == bytes([197, 100, 0, 255]) and len(rgba) == 4 * size * size  # the mean, opaque
    assert normal[:3] == bytes([128, 128, 255]) and grey[:3] == bytes([128, 128, 128])
    ground.session.island = {"mode": "2070"}
    size, layers = ground._layers((1,))
    assert size == 256 and len(layers[0][0]) == 4 * 256 * 256  # 2070's look: its maps


def test_turns_and_tilts_convert_both_ways():
    from annotoolbox.editor.desktop import desktopgizmo

    q = desktopgizmo.quat_of(30, 10, -5)
    assert desktopgizmo.euler_of(q) == pytest.approx((30, 10, -5), abs=1e-6)
    v = desktopgizmo.to_view_quat(q)
    assert list(desktopgizmo.to_view_quat(v)) == pytest.approx(q)


def _glb(positions, indices, translation=(0, 0, 0)):
    import json
    import struct

    import numpy as np

    pos = np.asarray(positions, "<f4").tobytes()
    idx = np.asarray(indices, "<u2").tobytes()
    binary = pos + idx + b"\0" * (-len(pos + idx) % 4)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0, "translation": list(translation)}],
        "scenes": [{"nodes": [0]}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def test_a_glb_mesh_reads_with_its_node_transform():
    from annotoolbox.render import modellibrary

    prims = modellibrary.read_glb(_glb([[0, 0, 0], [1, 0, 0], [0, 0, 1]], [0, 1, 2], translation=(5, 2, 0)))
    ((verts, idx),) = prims
    assert verts.shape == (3, 8) and list(idx) == [0, 1, 2]
    assert tuple(verts[1, :3]) == (6, 2, 0) and tuple(verts[0, 3:6]) == (0, 1, 0)  # no normals given: up


def test_the_library_loads_coarser_levels_after_the_finest_and_maps_when_asked():
    """A part's ``_lodN`` meshes cut to its material by ``MaterialLODInfos`` load after every finest mesh (a level
    whose material is gone ends the chain); the maps load after them, and a map's pixels can be dropped once the GPU
    has them (the size stays)."""
    import io
    import time

    from PIL import Image

    from annotoolbox.render.modellibrary import ModelLibrary

    tri = _glb([[0, 0, 0], [1, 0, 0], [0, 0, 1]], [0, 1, 2])
    asked = []

    class Files:
        def exists(self, path):
            return path.endswith(("_lod1.rdm", "_lod2.rdm"))

    class Editor:
        files = Files()

        def model(self, _cfg):
            part = {"mesh": "data/x/house_lod0.rdm", "position": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": 1,
                    "materials": {0: {"diffuse": "data/x/wall.psd"}, 1: {"diffuse": "data/x/roof.psd"}},
                    "lod_materials": [[0, 1], [1, 0], [0]]}  # fmt: skip
            return {"parts": [part], "files": []}

        def mesh_glb(self, path, material=None):
            asked.append((path, material))
            return tri

        def texture_png(self, path, normal=False):
            out = io.BytesIO()
            Image.new("RGBA", (4, 2), (255, 0, 0, 255)).save(out, "PNG")
            return out.getvalue()

    library = ModelLibrary(Editor())
    library.request(["house.cfg"])
    start = time.monotonic()
    while time.monotonic() - start < 5 and (not library.ready("house.cfg") or any(
            library.lods(p) is None for p in library.parts("house.cfg"))):  # fmt: skip
        time.sleep(0.01)
    wall, roof = library.parts("house.cfg")
    assert len(library.lods(wall)) == 2 and len(library.lods(roof)) == 1  # the roof's material is gone at level 2
    assert ("data/x/house_lod1.rdm", 1) in asked and ("data/x/house_lod2.rdm", 0) in asked  # the swapped numbers
    while time.monotonic() - start < 5 and not library.texture("d:data/x/wall.psd"):
        time.sleep(0.01)
    assert library.texture("d:data/x/wall.psd")[:3] == (4, 2, 4)
    library.drop_pixels("d:data/x/wall.psd")
    assert library.texture("d:data/x/wall.psd") == (4, 2, 4, None)
    library.close()


def test_a_model_the_games_look_draws_keeps_its_boxes_alone_and_loads_in_full_when_asked():
    """While the game's look draws a 117 island the view's own scene is not loaded: a model keeps its parts' boxes
    (picking, its bounds), its meshes, levels and maps load only when the view's look must draw it, and go again
    when the game's look takes it over (island-117-view.md "Speed", part 6)."""
    import time

    import numpy as np

    from annotoolbox.render.modellibrary import ModelLibrary, box_mesh

    tri = _glb([[0, 0, 0], [2, 1, 0], [0, 0, 3]], [0, 1, 2])

    class Editor:
        files = None

        def model(self, _cfg):
            part = {"mesh": "data/x/rock.rdm", "position": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": 1,
                    "diffuse": "data/x/rock.psd"}  # fmt: skip
            return {"parts": [part], "files": [], "decals": [{"diffuse": "data/x/dirt.psd", "position": [0, 0, 0],
                    "rotation": [0, 0, 0, 1], "scale": 1}]}  # fmt: skip

        def mesh_glb(self, path, material=None):
            return tri

    def wait(test) -> None:
        start = time.monotonic()
        while time.monotonic() - start < 5 and not test():
            time.sleep(0.01)

    library = ModelLibrary(Editor())
    library.set_full(set())  # the game's look draws everything
    library.request(["rock.cfg"])
    wait(lambda: library.ready("rock.cfg"))
    (part,) = library.parts("rock.cfg")
    ((box, idx),) = library.box(part["mesh_key"])
    assert library.mesh(part["mesh_key"]) is None and library.lods(part) is None and library.textures_pending() == 0
    assert box.shape == (8, 8) and len(idx) == 36 and box[:, :3].min(axis=0).tolist() == [0, 0, 0]
    assert box[:, :3].max(axis=0).tolist() == [2, 1, 3]
    assert library.set_full({"rock.cfg"}) and not library.set_full({"rock.cfg"})  # its part does not draw: in full
    wait(lambda: library.ready("rock.cfg") and library.texture("d:data/x/rock.psd") is not None)
    assert len(library.mesh(part["mesh_key"])[0][0]) == 3 and library.wanted()[1] >= {"d:data/x/rock.psd"}
    library.set_full(set())  # the game's look draws it again: the full data goes, the decal's map stays
    assert library.mesh(part["mesh_key"]) is None and library.ready("rock.cfg")
    assert library.wanted() == (set(), {"d:data/x/dirt.psd"})
    assert box_mesh([]) == [] and np.array_equal(box_mesh([(box, idx)])[0][0], box)
    library.close()


def _wait(test, seconds: float = 5.0) -> None:
    import time

    start = time.monotonic()
    while time.monotonic() - start < seconds and not test():
        time.sleep(0.01)


def _two_part_editor(gates: dict):
    """A model of two parts (``a.rdm``, ``b.rdm``) whose mesh and map reads wait on ``gates`` (path -> an Event)
    and say they began (``gates["began"]``)."""
    import io

    from PIL import Image

    tri = _glb([[0, 0, 0], [2, 1, 0], [0, 0, 3]], [0, 1, 2])

    def held(path):
        gates["began"].append(path)
        if path in gates:
            gates[path].wait(5)

    class Editor:
        files = None

        def model(self, _cfg):
            parts = [{"mesh": f"data/x/{k}.rdm", "position": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": 1,
                      "diffuse": f"data/x/{k}.psd"} for k in "ab"]  # fmt: skip
            return {"parts": parts, "files": []}

        def mesh_glb(self, path, material=None):
            held(path)
            return tri

        def texture_png(self, path, normal=False):
            held(path)
            out = io.BytesIO()
            Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(out, "PNG")  # see-through: a leaf's corner
            return out.getvalue()

    return Editor()


def test_a_model_let_go_while_it_loads_keeps_a_box_for_every_part_and_drops_what_finishes_late():
    """A model the game's look took over while its meshes loaded stopped before its other parts and gave them no
    box (never ready: it vanished and could not be picked), and the mesh and maps that finished loading after it was
    let go were kept for good (gigabytes on a first start of a continental island)."""
    import threading

    from annotoolbox.render.modellibrary import ModelLibrary

    gates = {"began": [], "data/x/a.rdm": threading.Event()}
    library = ModelLibrary(_two_part_editor(gates), workers=1)
    library.request(["house.cfg"])
    _wait(lambda: "data/x/a.rdm" in gates["began"])
    library.set_full(set())  # the game's look draws it now, while its first mesh still loads
    gates["data/x/a.rdm"].set()
    _wait(lambda: library.ready("house.cfg"))
    a, b = library.parts("house.cfg")
    assert library.ready("house.cfg") and library.box(a["mesh_key"]) and library.box(b["mesh_key"])
    assert library.mesh(a["mesh_key"]) is None and library.mesh(b["mesh_key"]) is None
    library.close()

    gates = {"began": [], "data/x/a.psd": threading.Event()}
    library = ModelLibrary(_two_part_editor(gates), workers=1)
    library.request(["house.cfg"])
    _wait(lambda: "data/x/a.psd" in gates["began"])
    library.set_full(set())  # let go while its map decodes
    gates["data/x/a.psd"].set()
    _wait(lambda: library.textures_pending() == 0)
    assert "d:data/x/a.psd" not in library._textures and library.texture("d:data/x/a.psd") is None
    assert library.textures_pending() == 0  # a map no full model uses is not read again
    library.close()


def test_a_model_the_games_look_draws_is_picked_by_its_boxes_without_its_map():
    """Picking a box read the part's map at the box's corner (its texture coordinates are 0): where that texel is
    see-through (a tree's leaves) the box was not picked, and the map the game's look does not need was loaded."""
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from tests.test_editsession import island

    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.editor.editsession import EditSession
    from annotoolbox.render import camera
    from annotoolbox.render.modellibrary import ModelLibrary

    s = EditSession(island())
    s.finish_loading()
    r = desktoprender.Renderer(ctx, s, None)
    r.library = ModelLibrary(_two_part_editor({"began": []}))
    r._full_models = lambda opts: set()  # the game's look draws every model
    w, h = 160, 100
    fbo = ctx.framebuffer([ctx.renderbuffer((w, h), 4)], ctx.depth_renderbuffer((w, h)))
    rock = next(e for e in s.entries if e.base.get("cfg") == "rock.cfg")
    cam = camera.OrbitCamera((s.size - rock.position[0] - 1.0, rock.position[1] + 0.5, rock.position[2] + 1.5), 8)
    opts = desktoprender.ViewOptions()
    _wait(lambda: (r.render(fbo, (w, h), cam, opts), r.batches[rock.batch].parts)[1])
    assert r.batches[rock.batch].lean
    assert r.pick(cam, (w, h), w / 2, h / 2, opts) is not None
    assert not any(k.startswith("d:") for k in r.library._textures)  # its map was never read
    r.release()


def test_objects_are_placed_and_mirrored():
    import numpy as np

    from annotoolbox.render import modellibrary

    m = modellibrary.instance_matrices(
        np.array([[10, 1, 20], [30, 0, 5]], "f4"),
        np.array([[0, 0, 0, 1]] * 2, "f4"),
        np.array([[2] * 3, [1] * 3], "f4"),
        100,
    )
    assert m.shape == (2, 4, 4)
    p = m[0] @ np.array([1, 0, 0, 1], "f4")  # a point one unit along the model's x
    assert np.allclose(p[:3], [100 - 12, 1, 20])  # scaled, moved, then mirrored
    assert np.allclose(m[0], modellibrary.mirror(100) @ modellibrary.trs([10, 1, 20], [0, 0, 0, 1], 2))


class _Editor:
    """An opened island as the app hands it to the window: the synthetic one of test_editsession."""

    edits_path = None
    notes = ["a note from the reader"]

    def island_json(self):
        import json

        return json.dumps(self.island_data())

    def island_data(self):
        from tests.test_editsession import island

        return island()

    def edits(self):
        return {}

    def materials(self):
        return [{"id": 0, "name": "a2070_n_gras_01", "diffuse": "data/anno2070/t/gras.psd", "order": 0}]

    def vanilla_materials(self):
        return [{"file": "data/base/m/grass.cfg", "set": "data/base/s.xml", "name": "grass", "diffuse": "", "rank": 3}]

    def library(self):
        return {"117": [], "2070": [], "editor": []}

    def texture_png(self, *a, **k):
        raise FileNotFoundError("no textures here")


def _window(tmp_path):
    from PySide6.QtWidgets import QApplication

    qt = QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    path = tmp_path / "settings.json"
    settings.save(path)
    return qt, workbench.MainWindow(editorapp.App(path))


def test_ribbon_items_are_enabled_by_the_islands_state_and_tool():
    from annotoolbox.editor.desktop import desktopribbon as r

    state = {"island": True, "editable": True, "tools": {"select", "height"}, "undo": False, "redo": True}
    assert r.available("", {}) and not r.available("island", {}) and not r.available("height", {})
    assert r.available("island", state) and r.available("height", state) and r.available("redo", state)
    assert not r.available("water", state) and not r.available("undo", state) and not r.available("selection", state)
    assert r.tool_action("height", {"mode": "smooth", "plant": True}) == "tool_smooth"
    assert r.tool_action("forest", {"mode": "raise", "plant": False}) == "tool_clear"
    assert r.tool_action("select", {"mode": "raise", "plant": True}) == "tool_select"
    assert [r.mode_of(t) for t in (None, "select", "place", "height", "paint", "water")] == [
        "none", "object", "object", "terrain", "terrain", "path",
    ]  # fmt: skip
    assert r.mode_of("height", readonly=True) == "view" and set(r.MODES) == {
        "none",
        "view",
        "object",
        "terrain",
        "path",
    }


def test_the_theme_has_a_dark_and_a_light_scheme_with_an_orange_accent_and_a_status_colour_per_mode():
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QWidget

    from annotoolbox.editor.desktop import desktopribbon
    from annotoolbox.suite import theme

    QApplication.instance() or QApplication([])
    window = QWidget()
    lightness = {}
    for setting in ("dark", "light"):
        assert theme.apply(window, setting) == setting == theme.scheme()
        sheet = theme.stylesheet()
        assert theme.colors()["accent"] in sheet and "$" not in sheet
        assert all(f'QStatusBar[mode="{m}"]' in sheet for m in desktopribbon.MODES)
        lightness[setting] = theme.palette().color(QPalette.ColorRole.Window).lightness()
    assert lightness["dark"] < 80 < 180 < lightness["light"]
    assert theme.resolve("dark") == "dark" and theme.resolve("system") in ("dark", "light")
    theme.apply(window, "dark")


def test_opening_an_island_loads_it_into_the_one_window(tmp_path, monkeypatch):
    """The user saw the start window come up again when an island opened: no process may start, no second
    window appear, and the window and its OpenGL view stay the same objects from island to island."""
    import multiprocessing
    import subprocess
    import time

    qt, main = _window(tmp_path)
    main.show()  # first: on Linux the OpenGL view's library lookup runs ldconfig, which is not the opening's process
    qt.processEvents()

    def refuse(*a, **k):
        raise AssertionError("opening an island started a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(multiprocessing.Process, "start", refuse)

    def open_island(kind, name):
        main.app.editor = _Editor()
        return {"kind": "built", "name": name, "editable": True, "buildable": True}

    monkeypatch.setattr(main.app, "open", open_island)
    before, view = int(main.winId()), main.view
    main.open("built", "isle")
    doc = main.doc
    main.open("built", "isle")  # the open island is only shown again
    qt.processEvents()
    assert main.doc is doc and main.view is view and int(main.winId()) == before
    assert "a note from the reader" in main.log.toPlainText()  # what the reader left out is in the Log
    assert [w for w in qt.topLevelWidgets() if w.isVisible() and isinstance(w, workbench.MainWindow)] == [main]
    assert view.session is doc.session and main.windowTitle().startswith("isle")
    t = time.monotonic()
    while not doc.gallery.material_lists and time.monotonic() - t < 10:  # the gallery is filled in the background
        qt.processEvents()
        time.sleep(0.01)
    assert [lst.count() for lst in doc.gallery.material_lists] == [1, 1]  # grouped: Anno 117, then Anno 2070
    assert main.dock_holders["Material Gallery"].panel is doc.gallery and main.holders["History"].panel is doc.history

    # tools: the ribbon button, the view's tool and the status bar's mode agree
    assert main.actions["tool_select"].isChecked() and main.statusBar().property("mode") == "object"
    main.actions["tool_smooth"].trigger()
    assert view.tool == "height" and view.brush["mode"] == "smooth" and main.actions["tool_smooth"].isChecked()
    assert not main.actions["tool_select"].isChecked() and main.statusBar().property("mode") == "terrain"
    main.actions["tool_water"].trigger()
    assert view.tool == "water" and main.mode_label.text() == "Path Mode"
    main.actions["gizmo_scale"].trigger()
    assert view.gizmo.mode == "scale"
    main.ribbon.fields["radius"][0].setValue(9)  # the brush on the Object tab follows the Terrain, Grid and Path tabs
    assert view.brush["radius"] == 9 and [w.value() for w in main.ribbon.fields["radius"]] == [9, 9, 9, 9]
    main.fov.setValue(60)
    assert view.camera.fov == pytest.approx(1.0472, abs=1e-3)

    # the minimap dock follows the island's heights (after a short pause)
    assert main.dock_holders["Minimap"].panel is doc.minimap and doc.minimap.picture is not None
    before = doc.minimap.picture.copy()
    from annotoolbox.editor.editchange import Change

    s = doc.session
    s.D[:, :] += 50.0
    s._redraw(0, 0, s.size, s.size)
    s._notify(Change(heights=(0, 0, s.size, s.size)))
    t = time.monotonic()
    while (doc.minimap.picture == before).all() and time.monotonic() - t < 5:
        qt.processEvents()
        time.sleep(0.02)
    assert not (doc.minimap.picture == before).all()
    s.D[:, :] -= 50.0
    s._redraw(0, 0, s.size, s.size)

    # the underwater look is the 2070 look seen from below; caustics need it; the game's light excludes it
    s.island["mode"] = "2070"
    assert doc.look_state()["underwater"][0] and not doc.look_state()["caustics"][0]
    doc.set_look("underwater", True)
    o = view.opts
    assert o.underwater and o.look2070 and doc.look_state()["caustics"][0] and "underwater look" in doc.badge()
    doc.set_look("caustics", True)
    doc.set_look("underwater", False)
    assert not o.underwater and not o.caustics and o.look2070
    doc.set_look("underwater", True)
    doc.set_look("look2070", False)
    assert not o.underwater and not o.look2070
    doc.set_look("underwater", True)
    doc.set_look("native", True)  # the game's look (117's islands; the state greys it on 2070's)
    assert not o.underwater and not o.look2070 and o.native
    doc.set_look("native", False)
    s.island["mode"] = "117"

    # the particle effects are off until asked for; freezing holds them at one moment
    particles, frozen = main.look_actions["particles"], main.look_actions["particles_frozen"]
    assert particles.isEnabled() and not particles.isChecked() and not frozen.isEnabled() and not view.opts.particles
    particles.trigger()
    assert view.opts.particles and frozen.isEnabled() and view.opts.particle_time is None
    frozen.trigger()
    assert view.opts.particle_time is not None and frozen.isChecked()
    frozen.trigger()
    assert view.opts.particle_time is None
    particles.trigger()
    assert not view.opts.particles and not frozen.isEnabled()

    # an edit: dirty title, undo on, then undo off again
    assert not main.actions["undo"].isEnabled()
    herd = next(e for e in doc.session.entries if e.marker == "Herd")
    doc.session.transform(herd, "move", position=[8.0, 0.7, 7.0])
    assert "*" in main.windowTitle() and main.actions["undo"].isEnabled() and main.actions["save"].isEnabled()
    main.actions["undo"].trigger()
    assert "*" not in main.windowTitle() and not main.actions["undo"].isEnabled()

    # another island replaces it, in the same window and view
    main.open("built", "other")
    assert main.doc is not doc and not doc.alive and main.view is view and main.doc.title == "other"
    assert view.session is main.doc.session and view.tool == "select" and view.fov == 60  # the camera's FOV stays
    main.close_island()
    assert main.doc is None and view.session is None and main.holders["History"].panel is None
    assert not main.actions["save"].isEnabled()
    main.close()


def test_the_window_follows_the_systems_scheme_live_unless_the_setting_pins_it(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    from annotoolbox.suite import icons, theme

    qt, window = _window(tmp_path)
    system = {"scheme": "dark"}  # the offscreen platform has no system scheme to switch: stand-in for Qt's answer
    monkeypatch.setattr(theme, "resolve", lambda s: system["scheme"] if s == "system" else s)
    window.system_scheme_changed(Qt.ColorScheme.Dark)
    assert window.app.settings.theme == "system" and theme.scheme() == "dark"
    system["scheme"] = "light"  # Windows switches: the window switches with it, icons included
    window.system_scheme_changed(Qt.ColorScheme.Light)
    assert theme.scheme() == "light" and icons.FG == theme.SCHEMES["light"]["icon"]
    assert theme.SCHEMES["light"]["bg"] in window.styleSheet() or window.palette().window().color().lightness() > 180
    window.app.settings.theme = "dark"  # a pinned theme ignores the system
    window.apply_theme()
    system["scheme"] = "light"
    window.system_scheme_changed(Qt.ColorScheme.Light)
    assert theme.scheme() == "dark" and icons.FG == theme.SCHEMES["dark"]["icon"]
    window.close()


def test_the_1800_preview_is_the_editor_restricted_to_1800(tmp_path):
    from PySide6.QtWidgets import QApplication

    from annotoolbox.editor.desktop import desktopeditor, desktopislands

    QApplication.instance() or QApplication([])
    exe = tmp_path / "Bin" / "Win64" / "Anno1800.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    settings = Settings(anno1800=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    window = workbench.MainWindow(editorapp.App(tmp_path / "settings.json", games=("anno1800",)))
    assert "1800" in window.windowTitle() and "preview" in window.windowTitle()
    assert window.ribbon.tab_names() == RIBBON_TABS  # the rules check, no forest
    assert not {"road_pieces", "check_slots", "check_falls", "tool_plant"} & set(window.actions)
    assert "check_rules" in window.actions
    assert {"tool_place", "tool_select", "duplicate", "build", "tool_paint", "tool_water", "point_insert"} <= set(
        window.actions
    )
    menus = ["&File", "&Edit", "&Rendering", "&Tools", "&Layout", "&Help"]
    assert [a.text() for a in window.menuBar().actions()] == menus
    assert list(window.look_actions) == ["shadows", "particles", "particles_frozen"]
    assert window.docks["Material Gallery"].isVisibleTo(window)
    folders = [key for key, _ in desktopislands.folders_for(window.app.games)]
    assert folders == ["anno1800", "mods1800", "workspace"]
    assert desktopeditor.game_tools(window.app.games) == {
        "select",
        "place",
        "height",
        "paint",
        "water",
        "spray",
        "active",
        "surf",
    }
    window.close()


def test_a_grouped_island_list_folds_and_searches_across_its_groups():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    groups = {"a/one": "Old World", "a/two": "Old World", "b/three": "New World", "b/four": "Other"}
    picked = []
    lst = desktopislands.IslandList(
        list(groups), [("Open", picked.append)], group_of=groups.get, order=("Old World", "New World")
    )
    texts = [lst.list.item(i).text() for i in range(lst.list.count())]
    assert (
        texts[0].endswith("Old World  (2)") and texts[3].endswith("New World  (1)") and texts[5].endswith("Other  (1)")
    )
    lst._toggle(lst.list.item(0))  # fold the first group
    assert [lst.list.item(i).text() for i in range(lst.list.count())][1].endswith("New World  (1)")
    lst.search.setText("t")  # a search shows the matches of every group, folded or not
    shown = [lst.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.list.count())]
    assert [s for s in shown if s] == ["a/two", "b/three"]
    lst.list.setCurrentRow(0)  # a header: the buttons do nothing
    lst._act(picked.append)
    lst.list.setCurrentRow(1)
    lst._act(picked.append)
    assert picked == ["a/two"]


CONTINENTAL = "data/dlc01/sessions/islands/pool/moderate_c_01/moderate_c_01.a7m"


def test_a_continental_island_opens_without_blocking_the_window(tmp_path):
    """Installed: 1800's continental island read off the window's thread; the window's timer keeps ticking."""
    import time

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    qt = QApplication.instance() or QApplication([])
    settings = Settings(anno1800=root, workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods"))
    settings.save(tmp_path / "settings.json")
    main = workbench.MainWindow(editorapp.App(tmp_path / "settings.json"))
    ticks = [time.perf_counter()]
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start(20)
    main.open("anno1800", CONTINENTAL, wait=False)
    assert main.loading is not None and main.doc is None  # the reading runs off the window's thread
    start = time.perf_counter()
    while main.loading is not None and time.perf_counter() - start < 600:
        qt.processEvents()
        time.sleep(0.005)
    reading = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    timer.stop()
    assert main.doc is not None and main.doc.session.size >= 700
    # no freeze while reading (the last gap: the panels); 0.29 s unloaded before the collector and json pieces
    assert len(reading) > 20 and max(reading[:-1]) < 0.5
    main.close_island()


def test_the_gallery_offers_only_the_islands_own_games_vanilla_ground():
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication

    from annotoolbox.editor.desktop.desktoppanels import MaterialGallery

    QApplication.instance() or QApplication([])
    vanilla = [
        {"id": 1, "name": "sand", "set": "s", "game": "anno117", "diffuse": "a"},
        {"id": 2, "name": "grass", "set": "s", "game": "anno1800", "diffuse": "b"},
    ]
    for island, wanted in (({}, "sand"), ({"game_name": "Anno 1800"}, "grass")):
        doc = SimpleNamespace(session=SimpleNamespace(island=island, paint_materials={}))
        gallery = MaterialGallery(doc)
        gallery.fill([], vanilla)
        assert [item.data(256)[1]["name"] for item in gallery.items()] == [wanted]  # 117 and 1800 never mixed


def _open_fake(main, name: str = "isle"):
    """The synthetic island opened in ``main`` (read on the window's thread); its document."""

    def open_island(kind, name):
        main.app.editor = _Editor()
        return {"kind": "built", "name": name, "editable": True, "buildable": True}

    main.app.open = open_island
    main.open("built", name)
    return main.doc


def test_a_closed_island_is_let_go_by_the_window_and_its_view(tmp_path):
    """Every closed island stayed alive: its document as a child of the window, and the view's place tool kept its
    asset list (and through it the island) until the next one opened."""
    import gc
    import weakref

    from PySide6.QtCore import QCoreApplication, QEvent

    from annotoolbox.editor.desktop.desktopeditor import IslandDoc

    qt, main = _window(tmp_path)
    session = weakref.ref(_open_fake(main).session)
    assert main.view.placer.panel is main.doc.buildings
    main.close_island()
    main.background.join()  # the side data and the minimap, still being read
    qt.processEvents()
    main.app.editor = None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert main.findChildren(IslandDoc) == [] and main.view.placer.panel is None
    assert session() is None
    main.close()


def test_a_property_field_only_left_changes_nothing_and_an_edit_keeps_the_other_values(tmp_path):
    """A spin box reports "editing finished" when it is only tabbed through: the object took the field's rounded value
    as a move (an undo step and an edit record), and a river its own width as an edit."""
    qt, main = _window(tmp_path)
    doc = _open_fake(main)
    s, props = doc.session, doc.props
    rock = next(e for e in s.entries if e.kind == "object" and not e.marker)
    rock.position = [5.12345678, 0.7, 6.5]
    main.view.select(rock)
    for key in ("x", "y", "z", "yaw", "tx", "tz", "s"):
        props._edited(key)
    assert not s.undo.commands and not s.edits["objects"] and rock.position == [5.12345678, 0.7, 6.5]
    props.fields["z"].setValue(9.25)
    props._edited("z")
    assert rock.position == [5.12345678, 0.7, 9.25] and len(s.undo.commands) == 1  # x is not the field's 5.123
    main.view.tool = "water"
    main.view.select_water({"river": 0, "point": 0})
    for key in ("width", "level"):  # the lake's field is hidden for a river point
        props._edited(key)
    assert len(s.undo.commands) == 1 and not s.edits["rivers"] and not s.edits["lakes"]
    props.fields["width"].setValue(s.river(0)["widths"][0] + 1.5)
    props._edited("width")
    assert len(s.undo.commands) == 2 and "0" in s.edits["rivers"]
    s.undo.mark_clean()
    main.close()


@pytest.mark.parametrize(
    "request_, name",
    [
        (("anno1800", "data/sessions/islands/pool/moderate_l_01/moderate_l_01.a7m"), "moderate_l_01.a7m"),
        (("built", "n_s01"), "n_s01"),
        (("blank", "anno1404", "data/levels/islands/south_l_01.isd", True), "south_l_01.isd"),
        (("blank117", 192), "192"),
        (("file", "data\\pool\\isle.a7m", None, None), "isle.a7m"),
    ],
)
def test_the_status_bar_names_the_island_being_opened(request_, name):
    """ "Opening None" and "Opening True": the request's last part is the edit file or an option, not the island."""
    assert workbench.request_name(request_) == name


def test_a_minimap_paint_that_fails_is_said_and_the_last_picture_stays(tmp_path, monkeypatch):
    """A failed paint showed nothing and said nothing; without a background it stopped the island from opening."""
    from annotoolbox.editor.desktop import minimappanel

    qt, main = _window(tmp_path)
    doc = _open_fake(main)
    main.background.join()
    qt.processEvents()
    before, said = doc.minimap.picture.copy(), []
    monkeypatch.setattr(main, "say", said.append)
    monkeypatch.setattr(minimappanel.minimap, "paint", lambda *a, **k: 1 / 0)
    doc.minimap.repaint_map()
    main.background.join()
    qt.processEvents()
    assert len(said) == 1 and "ZeroDivisionError" in said[0]
    assert (doc.minimap.picture == before).all() and not doc.minimap._painting
    panel = minimappanel.MinimapPanel(doc.session, None, said.append)  # painted on the window's thread: the same
    assert len(said) == 2 and panel.picture is None
    main.close()


def test_closing_the_window_waits_for_the_job_and_the_island_being_read(tmp_path, monkeypatch):
    """The window closed at once: the job's and the reader's threads were cut off when the program ended (a build
    half written), and an island read late was attached to the closed window."""
    import threading
    import time

    from annotoolbox.editor.desktop import desktopjobs

    monkeypatch.setattr(desktopjobs, "ask_stop_wait_cancel", lambda *_a: desktopjobs.WAIT)  # the default answer
    qt, main = _window(tmp_path)
    ended = []

    def work(job):
        time.sleep(0.3)
        ended.append("job")

    def open_island(kind, name):
        time.sleep(0.3)
        ended.append("read")
        main.app.editor = _Editor()
        return {"kind": "built", "name": name, "editable": True, "buildable": True}

    main.app.open = open_island
    main.app._start("a build", work)
    main._open_after = "isle"  # what a build asks to open when it ends
    main.open("built", "isle", wait=False)
    assert main.loading is not None and main.busy_with() == "a build"
    assert main.close()
    assert sorted(ended) == ["job", "read"] and main.app.job.state == "done"
    assert main.loading is None and main.doc is None and not main.closing
    qt.processEvents()
    assert main.doc is None and main.loading is None  # the ended build opened nothing either
    assert not [t for t in threading.enumerate() if t in main.background._threads and t.is_alive()]


def test_closing_during_a_job_asks_to_stop_it_wait_or_stay(tmp_path, monkeypatch):
    """Wait is the path the window took before; Stop ends the job at its next step; Cancel keeps the window."""
    import threading
    import time

    from annotoolbox.editor.desktop import desktopjobs

    qt, main = _window(tmp_path)
    main.show()
    asked, answers, go = [], [desktopjobs.CANCEL], threading.Event()
    monkeypatch.setattr(desktopjobs, "ask_stop_wait_cancel", lambda _w, _t, text: asked.append(text) or answers[0])
    steps = []

    def work(job):
        for step in ("one", "two", "three"):
            job.log(step)
            steps.append(step)
            assert go.wait(30)
        job.note("installed")
        return "built"

    main.app._start("a build", work)
    assert not main.close() and main.isVisible() and "a build" in asked[0]  # Cancel: the window and the job stay
    assert main.app.job.state == "running"
    answers[0] = desktopjobs.STOP
    threading.Timer(0.2, go.set).start()  # the step under way ends while the window waits
    assert main.close() and len(asked) == 2
    assert main.app.job.state == "stopped" and steps == ["one"] and "stopped" in main.app.job.lines[-1]

    qt, main = _window(tmp_path)
    go.set()
    main.app._start("a build", lambda job: (time.sleep(0.2), job.log("done"))[1])
    answers[0] = desktopjobs.WAIT
    assert main.close() and main.app.job.state == "done" and main.app.job.lines == ["done"]
    assert main.close() and len(asked) == 3  # nothing runs any more: no question

    box_buttons = []
    monkeypatch.undo()
    from PySide6.QtWidgets import QMessageBox

    def exec_(box):
        box_buttons.extend((b.text(), b is box.defaultButton(), b is box.escapeButton()) for b in box.buttons())
        box.defaultButton().click()

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    assert desktopjobs.ask_stop_wait_cancel(None, "t", "a build runs") == desktopjobs.WAIT  # Enter waits
    assert sorted(box_buttons, key=lambda b: b[1:]) == [
        ("Stop the build", False, False),
        ("Cancel", False, True),
        ("Wait", True, False),
    ]


def test_a_closed_islands_gpu_objects_are_freed_on_opengl():
    """moderngl frees nothing on its own: every island opened in a session left its buffers, maps and programs on the
    GPU until the window closed, and so did each batch the renderer replaced."""
    import gc

    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from tests.test_editsession import island

    from annotoolbox.editor.desktop import desktoprender, desktopview
    from annotoolbox.editor.editsession import EditSession
    from annotoolbox.render import camera

    desktopview.collect_dropped(ctx)  # as the view sets its context up
    s = EditSession(island())
    s.finish_loading()
    r = desktoprender.Renderer(ctx, s, None)
    fbo = ctx.framebuffer([ctx.renderbuffer((64, 64), 4)], ctx.depth_renderbuffer((64, 64)))
    r.render(fbo, (64, 64), camera.OrbitCamera((8, 1, 8), 12), desktoprender.ViewOptions())
    gc.collect()
    desktopview.free_dropped(ctx)
    r.release()
    del r
    gc.collect()
    assert desktopview.free_dropped(ctx) > 20  # the terrain, the programs, the targets, the batches' buffers
    assert not ctx.objects
    assert not s.listeners


def test_a_look_made_again_for_another_quality_takes_the_compiled_shaders_source(monkeypatch):
    """The shaders are compiled once a renderer; the look made again after a quality change read them from that job
    and had no source of its own: its objects, decals, sea and post chain were never made, and the view stayed busy."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.editor.desktop import desktopgame
    from annotoolbox.infra import dxc
    from annotoolbox.models import shaderview

    class Source:
        def __init__(self, *a, **k) -> None:
            pass

        def __getattr__(self, name):
            return lambda *a, **k: {"vs": name, "ps": name}

    monkeypatch.setattr(shaderview, "ShaderSource", Source)
    monkeypatch.setattr(dxc.Dxc, "find", staticmethod(lambda **k: None))
    first, again = object.__new__(desktopgame.GameIsland), object.__new__(desktopgame.GameIsland)
    first.r = SimpleNamespace(editor=SimpleNamespace(files=SimpleNamespace(folder=None, archives=None)))
    got = first._compile()  # the renderer's loader keeps it: the second look is handed the same
    made = []
    again.source, again._make = None, lambda *parts: made.append(parts)
    again._adopt(got, "high")
    assert again.source is first.source and isinstance(again.source, Source) and again.quality == "high"
    assert len(made) == 1 and len(made[0]) == 8  # stages, shading, sky, detailed, pages, bake, shadow, native


def test_the_island_view_releases_its_renderer_when_its_device_goes_away():
    """A view moved to another window dropped its renderer unreleased: its session listener, loader threads and the
    game look's parts stayed with a device that was gone."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.editor.desktop.desktoprhiview import RhiIslandView3D
    from annotoolbox.editor.desktop.desktopview import IslandControls

    released = []
    view = SimpleNamespace(
        renderer=SimpleNamespace(release=lambda: released.append("renderer")),
        makeCurrent=lambda: None,
        doneCurrent=lambda: None,
        _pick=((4, 4), 1, 1),
        _picked=("readback", {}),
    )
    view._release_renderer = lambda: IslandControls._release_renderer(view)
    RhiIslandView3D.released(view, object())
    assert released == ["renderer"] and view.renderer is None and view._pick is None and view._picked is None
    RhiIslandView3D.released(view, object())  # told again with nothing to release
    assert released == ["renderer"]
