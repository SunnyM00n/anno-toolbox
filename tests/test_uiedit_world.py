"""The UI preview's effects that read the 3D scene (background blur, world darken) and the island shading, on
synthetic scenes and pictures, drawn offscreen."""

from __future__ import annotations

import math
import os

import numpy as np
import pytest

from annotoolbox.models import mipchain
from annotoolbox.uiedit import effects, layout, worldfx, worldscene
from annotoolbox.uiedit.pictures import Picture


def _scene(left=(200, 40, 40), right=(40, 40, 200), size=(40, 20)) -> worldscene.Scene:
    """A scene whose left half is one colour and whose right half another: a hard edge to blur."""
    px = np.zeros((size[1], size[0], 3), np.uint8)
    px[:, : size[0] // 2], px[:, size[0] // 2 :] = left, right
    return worldscene.Scene("two-halves", px)


def _item(**fx) -> layout.DrawItem:
    return layout.DrawItem("image", layout.IDENTITY, (40.0, 20.0), effects=fx)


def test_the_effect_templates_are_told_apart_by_their_properties():
    assert effects.kind_of(set()) == "background_blur"  # the template with no properties
    assert effects.kind_of({"Color", "Intensity"}) == "world_darken"
    assert effects.kind_of({"Color", "Intensity", "Opaque"}) == "tint"  # the tint keeps its own
    island = {"ForegroundColor", "BackgroundColor", "HighlightFactor", "HighlightContrast", "Angle"}
    assert effects.kind_of(island) == "island_shading"
    assert set(effects.drawn({"background_blur": {}, "world_darken": {"Intensity": 0.0}})) == {"background_blur"}


def test_the_blur_is_the_mean_of_five_by_five_pixels_with_the_edge_repeated():
    one = np.zeros((9, 9, 1), np.float32)
    one[4, 4] = 25.0
    out = worldscene.box_blur(one, 2)
    assert out[4, 4, 0] == pytest.approx(1.0) and out[2, 2, 0] == pytest.approx(1.0) and out[1, 4, 0] == 0.0
    assert out.sum() == pytest.approx(25.0)
    flat = np.full((6, 7, 3), 0.4, np.float32)
    assert np.allclose(worldscene.box_blur(flat, 2), 0.4)  # clamped edges keep a flat picture flat


def test_the_stand_in_scene_has_sky_sea_and_land_and_is_always_the_same():
    a, b = worldscene.stand_in((320, 180)), worldscene.stand_in((320, 180))
    assert a.srgb.shape == (180, 320, 3) and np.array_equal(a.srgb, b.srgb)
    sky, sea, land = a.srgb[10, 160].astype(int), a.srgb[170, 10].astype(int), a.srgb[120, 160].astype(int)
    assert sky[2] > sky[0] and sea[2] > sea[0] and land[1] > land[2]  # blue over green over brown by position
    assert a.srgb.std() > 20


def test_a_scene_is_read_from_a_picture_file_and_a_bad_file_is_refused(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage

    img = QImage(8, 4, QImage.Format.Format_RGB32)
    img.fill(QColor(10, 200, 30))
    img.save(str(tmp_path / "shot.png"))
    scene = worldscene.load(tmp_path / "shot.png", (16, 8))
    assert scene.srgb.shape == (8, 16, 3) and tuple(scene.srgb[3, 5]) == (10, 200, 30)
    (tmp_path / "bad.png").write_bytes(b"nothing")
    with pytest.raises(ValueError):
        worldscene.load(tmp_path / "bad.png")


def test_the_background_blur_shows_the_blurred_scene_where_the_picture_has_alpha():
    scene = _scene()
    cover = np.zeros((4, 8, 4), np.uint8)
    cover[:, :4, 3] = 3  # any alpha counts, however faint; the right half has none
    item = _item(background_blur={})
    flat, pic = worldfx.shade(item, Picture("cover", cover), scene, (40.0, 20.0))
    assert flat.alpha == 1.0 and flat.color == (255, 255, 255) and "background_blur" not in flat.effects
    assert pic.rgba[5, 5, 3] == 255 and pic.rgba[5, 35, 3] == 0  # opaque inside the alpha, clear outside
    edge = pic.rgba[5, 19, :3].astype(int)  # next to the colour edge the blur has mixed red and blue
    assert edge[0] > 60 and edge[2] > 60
    plain = scene.srgb[5, 19].astype(int)
    assert plain[2] == 40 and edge[2] != 40  # the unblurred scene has no blue there
    assert tuple(pic.rgba[5, 2, :3]) == (200, 40, 40)  # far from the edge the colour is the scene's own


def test_the_world_darken_multiplies_by_its_colour_in_linear_light_by_its_share():
    scene = _scene(left=(200, 200, 200), right=(200, 200, 200))
    shown = lambda **fx: worldfx.shade(_item(**fx), None, scene, (40.0, 20.0))[1].rgba[5, 5]  # noqa: E731
    assert tuple(shown(world_darken={"Color": (0.0, 0.0, 0.0), "Intensity": 1.0})[:3]) == (0, 0, 0)
    assert tuple(shown(world_darken={"Color": (0.0, 0.0, 0.0), "Intensity": 0.0})[:3]) == (200, 200, 200)
    half = shown(world_darken={"Color": (0.0, 0.0, 0.0), "Intensity": 0.5})
    lin = float(mipchain.srgb_to_linear(np.uint8(200)))
    assert half[0] == mipchain.linear_to_srgb8(np.float32(lin / 2)) and half[3] == 255
    tinted = shown(world_darken={"Color": (1.0, 0.0, 0.0), "Intensity": 1.0})
    assert tinted[0] == 200 and tinted[1] == 0 and tinted[2] == 0  # a red colour keeps the red


def test_the_blur_and_the_darken_work_together_and_a_picture_that_is_missing_covers_the_widget():
    scene = _scene()
    _, pic = worldfx.shade(_item(background_blur={}, world_darken={"Color": (0, 0, 0), "Intensity": 1.0}),
                           None, scene, (40.0, 20.0))  # fmt: skip
    assert pic.rgba[..., 3].min() == 255 and pic.rgba[..., :3].max() == 0


def test_the_picture_of_a_moved_widget_samples_the_scene_where_it_stands():
    scene = _scene()
    item = layout.DrawItem("image", (1.0, 0.0, 0.0, 1.0, 30.0, 0.0), (10.0, 20.0), effects={"world_darken": {
        "Color": (1.0, 1.0, 1.0), "Intensity": 1.0}})  # fmt: skip
    _, pic = worldfx.shade(item, None, scene, (40.0, 20.0))
    assert tuple(pic.rgba[3, 3, :3]) == (40, 40, 200)  # the right half of the scene, at x 30 to 40
    scene_now = worldfx.shade(item, None, _scene(right=(0, 255, 0)), (40.0, 20.0))[1]
    assert scene_now.key != pic.key and scene_now.rgba[3, 3, 1] == 255  # another scene is another picture


def test_the_hatch_weights_hand_each_layer_on_to_the_next_as_the_intensity_rises():
    i = np.linspace(0.0, 1.0, 61)
    weights = worldfx.hatch_weights(i)
    assert np.allclose(sum(weights), np.clip(i * 6, 0, 1))  # the shares add up to what the first layer would have
    assert all(w.min() >= -1e-9 for w in weights)
    assert np.allclose([w[0] for w in weights], 0) and weights[5][-1] == 1.0  # deep water has none, the coast the last
    peaks = [int(np.argmax(w)) for w in weights]
    assert peaks == sorted(peaks)


def test_the_texel_rates_and_the_angle_follow_the_widget_s_matrix():
    assert worldfx.texel_rates(layout.IDENTITY, (64.0, 64.0), (0, 0, 1, 1), (64, 64)) == (1.0, 1.0, 0.0)
    half = worldfx.texel_rates((2.0, 0.0, 0.0, 2.0, 0.0, 0.0), (64.0, 64.0), (0, 0, 1, 1), (64, 64))
    assert half[:2] == (0.5, 0.5)
    quarter = math.pi / 2
    turned = (math.cos(quarter), math.sin(quarter), -math.sin(quarter), math.cos(quarter), 0.0, 0.0)
    rx, ry, angle = worldfx.texel_rates(turned, (64.0, 64.0), (0, 0, 1, 1), (64, 64))
    assert rx == pytest.approx(1.0) and ry == pytest.approx(1.0) and abs(angle) == pytest.approx(quarter)


def _island_picture() -> Picture:
    """Blue is the distance to the coast: 0 at the left edge, rising to 255; the alpha is 255 but at the top row."""
    rgba = np.zeros((16, 64, 4), np.uint8)
    rgba[..., 2] = np.linspace(0, 255, 64).astype(np.uint8)[None, :]
    rgba[..., 3] = 255
    rgba[0, :, 3] = 0
    return Picture("island", rgba)


def test_the_island_shading_draws_the_border_in_ink_and_open_land_in_paper():
    ink, paper = (0.1, 0.1, 0.1), (0.9, 0.9, 0.8)
    item = layout.DrawItem("image", layout.IDENTITY, (64.0, 16.0), effects={"island_shading": {
        "ForegroundColor": ink, "BackgroundColor": paper, "HighlightFactor": 0.0, "HighlightContrast": 0.3,
        "Angle": 0.8}})  # fmt: skip
    flat, pic = worldfx.shade(item, _island_picture(), _scene(), (64.0, 16.0))
    assert flat.alpha == 1.0 and "island_shading" not in flat.effects
    ink8 = mipchain.linear_to_srgb8(worldfx._linear(np.array(ink)))
    paper8 = mipchain.linear_to_srgb8(worldfx._linear(np.array(paper)))
    assert tuple(pic.rgba[8, 0, :3]) == tuple(ink8)  # at the coast: the border colour
    assert tuple(pic.rgba[8, 63, :3]) == tuple(paper8)  # far inland: no hatching, no border
    assert pic.rgba[0, 5, 3] == 0 and pic.rgba[8, 5, 3] == 255  # the picture's own alpha stays
    mid = pic.rgba[8, 20:40, :3].astype(int)
    assert mid.min() < paper8.min() and mid.max() > ink8.max()  # in between the strokes show


def test_a_scene_effect_is_drawn_only_where_there_is_a_scene():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import canvas

    QApplication.instance() or QApplication([])
    frame = layout.Frame((40.0, 20.0), [layout.DrawItem("image", layout.IDENTITY, (20.0, 20.0), effects={
        "background_blur": {}, "world_darken": {"Color": (0.0, 0.0, 0.0), "Intensity": 1.0}})])  # fmt: skip
    over = canvas.render(frame, None, 1.0, world=_scene())
    assert over.pixelColor(5, 5).getRgb()[:3] == (0, 0, 0)  # the darkened world where the widget is
    assert over.pixelColor(35, 5).getRgb()[:3] == (40, 40, 200)  # the scene itself beside it
    without = canvas.render(frame, None, 1.0)
    assert without.pixelColor(5, 5).getRgb()[:3] != (0, 0, 0)  # no scene: the widget is the grey stand-in


def test_the_preview_shows_a_scene_behind_the_frame_and_takes_it_away():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit.preview import Preview

    QApplication.instance() or QApplication([])
    view = Preview()
    view.resize(120, 80)
    view.show_frame(layout.Frame((40.0, 20.0), []), None)
    view.set_world(_scene())
    shot = view.grab().toImage()
    inside = shot.pixelColor(int(view.offset.x() + 5 * view.zoom), int(view.offset.y() + 5 * view.zoom))
    assert inside.getRgb()[:3] == (200, 40, 40)
    view.set_world(None)
    assert view.world is None and view._world_image is None


class _FakeRenderer:
    box, ticks = (0, 0, 0), 0

    def busy(self) -> int:
        self.ticks += 1
        return 1 if self.ticks < 3 else 0  # the files come in after two frames

    def shown_parts_in(self, state) -> bool:
        return True


class _FakeView:
    """Stands in for the viewer's offscreen view: a picture of a colour, the renderer busy for two frames."""

    made: list = []

    def __init__(self, backend: str) -> None:
        self.backend, self.failed, self.renderer, self.sizes, self.framed, self.released = backend, "", None, [], 0, 0
        _FakeView.made.append(self)

    def show_scene(self, scene, files) -> None:
        self.renderer = _FakeRenderer()

    def picture(self, w: int, h: int):
        from PySide6.QtGui import QColor, QImage

        self.sizes.append((w, h))
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(20, 120, 60))
        return img

    def frame_model(self) -> None:
        self.framed += 1

    def release(self) -> None:
        self.released += 1

    def close(self) -> None:
        pass

    def deleteLater(self) -> None:
        pass


def _drawn(monkeypatch, view=_FakeView, size=(64, 36)):
    """A ``ModelStill`` run to its end with a stand-in view; what ``done`` got, and the view."""
    import time
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import worldrender

    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(worldrender.modelscene, "build", lambda tree, path, game: SimpleNamespace(path=path))
    monkeypatch.setattr(worldrender, "ModelFiles", lambda tree: None)
    got = []
    model = worldrender.Model(SimpleNamespace(paths=[]), "anno117", "data/x/graphics/a.cfg", view)
    still = worldrender.Still(model, got.append, size)
    still.start("d3d12", wait=True)
    deadline = time.time() + 10
    while not got and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    return got, _FakeView.made[-1] if _FakeView.made else None


def test_a_model_is_drawn_small_until_its_files_are_in_then_framed_at_full_size(monkeypatch):
    got, view = _drawn(monkeypatch)
    (scene,) = got
    assert isinstance(scene, worldscene.Scene) and scene.key == "model:data/x/graphics/a.cfg"
    assert scene.srgb.shape == (1080, 1920, 3) and tuple(scene.srgb[5, 5]) == (20, 120, 60)  # the reference frame
    assert view.sizes[:2] == [(320, 180)] * 2 and view.sizes[-1] == (64, 36) and view.framed == 1
    assert view.released == 1  # the device's resources go with the view


def test_a_model_that_fails_or_finds_no_device_reports_why(monkeypatch):
    from annotoolbox.uiedit import worldrender

    class Failing(_FakeView):
        def show_scene(self, scene, files) -> None:
            super().show_scene(scene, files)
            self.failed = "the shader did not compile"

    got, view = _drawn(monkeypatch, Failing)
    assert isinstance(got[0], RuntimeError) and "did not compile" in str(got[0]) and view.released == 1
    monkeypatch.setattr(worldrender, "device", lambda: None)
    told = []
    worldrender.Still(worldrender.Model(None, "anno117", "x.cfg"), told.append).start()
    assert isinstance(told[0], RuntimeError)
    tree = type("T", (), {"paths": ["data/a/graphics/b.cfg", "data/a/config/c.cfg", "data/a/graphics/d.rdm"]})()
    assert worldrender.configs(tree) == ["data/a/graphics/b.cfg"]


class _FakeIslandView(_FakeView):
    """Stands in for the island editor's view: ``load`` an island, its renderer made with the first frame."""

    def __init__(self, backend: str) -> None:
        from annotoolbox.editor.desktop.desktoprender import ViewOptions

        super().__init__(backend)
        self.opts, self.loaded, self.closed = ViewOptions(scheme="dark"), None, 0

    def load(self, session, editor) -> None:
        self.loaded = (session, editor)

    def picture(self, w: int, h: int):
        self.renderer = self.renderer or _FakeRenderer()
        return super().picture(w, h)

    def close(self) -> None:
        self.closed += 1


def test_an_island_is_drawn_by_the_island_view_in_the_games_look_without_the_editors_markers(monkeypatch):
    """The island is read off the window's thread by the island editor's own route, read only, then drawn."""
    import time
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import worldrender

    qt = QApplication.instance() or QApplication([])
    session = SimpleNamespace(island={}, anno2070_editing=False)
    island = worldrender.Island(None, "anno117", "data/x/islands/a.a7m", _FakeIslandView, lambda: ("editor", session))
    got = []
    still = worldrender.Still(island, got.append, (64, 36))  # kept while it draws
    still.start("d3d12", wait=True)
    deadline = time.time() + 10
    while not got and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    view = _FakeView.made[-1]
    assert got[0].key == "island:data/x/islands/a.a7m" and view.loaded == (session, "editor")
    assert (
        view.opts.native and view.opts.game_terrain and not view.opts.layers["markers"] and view.opts.scheme == "dark"
    )
    assert view.closed == 1 and view.released == 0  # the island view frees its renderer when it closes


class _FakeStill:
    made: list = []

    def __init__(self, subject, done) -> None:
        self.subject, self.done, self.cancelled = subject, done, False
        _FakeStill.made.append(self)

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True


def test_an_island_whose_loading_count_stays_the_same_is_taken_as_in(monkeypatch):
    """A part of an island that never comes in must not hold the picture back until the wait runs out."""
    from types import SimpleNamespace

    from annotoolbox.uiedit import worldrender

    clock = [100.0]  # a clock of its own: Windows' monotonic clock moves in 15.6 ms steps
    monkeypatch.setattr(worldrender, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    counts = iter([3, 2, 1, 1, 1, 1])
    view = SimpleNamespace(renderer=SimpleNamespace(busy=lambda: next(counts)))
    island = worldrender.Island(None, "anno117", "a.a7m")
    assert [island.ready(view) for _ in range(3)] == [False] * 3 and not island.ready(view)
    clock[0] += worldrender.QUIET + 1
    assert island.ready(view)  # the same count for longer than QUIET
    assert worldrender.Island(None, "anno117", "a.a7m").ready(SimpleNamespace(renderer=None)) is False


CAMERA_XML = b"""<!-- two roots and "--" rules in a comment, as the game's file
-------- -->
<Normal>
  <Presets>
    <Preset ID="12" Height="50" Pitch="0.8" Fov="0.7" EdgeScrollingSpeed="37.0f"/>
    <!-- <Preset ID="13" Height="999" Pitch="0.1" Fov="0.1"/> -->
    <Preset ID="13" Height="60" Pitch="0.8" Fov="0.7" EdgeScrollingSpeed="40.0f"/>
  </Presets>
  <Settings DefaultPreset="13" ZoomStep="2.0"></Settings>
</Normal>
<Simple><Presets><Preset ID="13" Height="15" Pitch="0.65" Fov="0.7"/></Presets><Settings DefaultPreset="13"/></Simple>
"""


def test_the_games_default_camera_step_is_read_from_its_camera_file():
    pytest.importorskip("PySide6")
    from tests.fakes import FakeFiles

    from annotoolbox.uiedit import worldrender

    got = worldrender.game_camera(FakeFiles({"data/base/config/game/camera.xml": CAMERA_XML}))
    assert got == worldrender.GameCamera(60.0, 0.8, 0.7)  # Normal's default step, not the commented one or Simple's
    assert worldrender.game_camera(FakeFiles({"data/config/game/camera.xml": CAMERA_XML})) == got  # 1800's path
    assert worldrender.game_camera(FakeFiles({})) is None
    no_step = CAMERA_XML.replace(b'DefaultPreset="13" Zoom', b'DefaultPreset="40" Zoom')
    assert worldrender.game_camera(FakeFiles({"data/config/game/camera.xml": no_step})) is None


def test_an_island_is_drawn_from_the_games_camera_step_at_its_middle_on_its_ground(monkeypatch):
    """The eye stands the step's height over the island's middle, looking down at its pitch; the heading is kept."""
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    from annotoolbox.render.camera import OrbitCamera
    from annotoolbox.uiedit import worldrender

    def view_of(start):
        view = SimpleNamespace(opts=SimpleNamespace(scheme="dark"), camera=start, fov=45.0, loaded=None)
        view.to_view = lambda p: np.array([200.0 - p[0], p[1], p[2]])  # the island view's mirrored x
        view.load = lambda session, editor: setattr(view, "loaded", session)
        return view

    session = SimpleNamespace(island={}, anno2070_editing=False, size=200, height_at=lambda x, z: 12.0)
    view = view_of(OrbitCamera((0, 0, 0), 300))
    view.camera.yaw = 0.3
    island = worldrender.Island(None, "anno117", "a.a7m", open=lambda: ("editor", session), tree=None)
    worldrender.GameCamera(60.0, 0.8, 0.7).place(view, session)
    eye, target = view.camera.eye(), view.camera.target
    assert np.allclose(target, (100, 12, 100)) and math.isclose(eye[1] - target[1], 60.0, rel_tol=1e-5)
    assert math.isclose(view.camera.yaw, 0.3) and math.isclose(view.fov, math.degrees(0.7))
    low = SimpleNamespace(**{**vars(session), "height_at": lambda x, z: -3.0})  # its middle under the sea
    worldrender.GameCamera(60.0, 0.8, 0.7).place(view, low)
    assert view.camera.target[1] == 0.0
    plain = view_of(OrbitCamera((5, 5, 5), 99))
    island.show(plain, island.read())  # no camera file: the island view's own start stays
    assert plain.loaded is session and tuple(plain.camera.target) == (5, 5, 5)


def test_a_scene_drawn_after_the_switch_went_off_or_another_was_chosen_is_not_shown(monkeypatch):
    from types import SimpleNamespace

    pytest.importorskip("PySide6")
    from annotoolbox.uiedit import worldmenu, worldrender

    monkeypatch.setattr(worldrender, "Still", _FakeStill)
    shown, checked = [], [True]
    preview = SimpleNamespace(world=None)
    preview.set_world = lambda s: (shown.append(s), setattr(preview, "world", s))
    status = SimpleNamespace(showMessage=lambda *a: None)
    action = SimpleNamespace(isChecked=lambda: checked[0], setChecked=lambda v: checked.__setitem__(0, v))
    window = SimpleNamespace(settings=SimpleNamespace(apps={}), world_action=action, preview=preview,
                             statusBar=lambda: status, game=SimpleNamespace(tree=None, key="anno117"))  # fmt: skip
    menu = worldmenu.WorldMenu(window)
    menu._chosen("world_model", "a.cfg")
    first = _FakeStill.made[-1]
    checked[0] = False
    menu.toggled()  # off while the model is drawn
    first.done(_scene())
    assert first.cancelled and preview.world is None
    checked[0] = True
    menu._chosen("world_island", "data/x/islands/a.a7m")
    second = _FakeStill.made[-1]
    assert isinstance(second.subject, worldrender.Island) and "world_model" not in window.settings.apps["ui"]
    menu._chosen("world_model", "b.cfg")
    third = _FakeStill.made[-1]
    second.done(_scene(left=(1, 2, 3)))  # superseded: its picture is not shown
    third.done(_scene(left=(9, 9, 9)))
    assert second.cancelled and preview.world is shown[-1] and tuple(preview.world.srgb[0, 0]) == (9, 9, 9)


DRAWN_MODEL = r"""
import json, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.uiedit import worldfx, worldrender
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/roman/buildings/residences/residence_tier01_estate06/residence_tier01_estate06.cfg"
got = []
still = worldrender.Still(worldrender.Model(tree, "anno117", cfg), got.append, size=(480, 270))  # kept while it draws
still.start()
deadline = time.time() + 600
while not got and time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)
scene = got[0]
if isinstance(scene, Exception):
    raise scene
print(json.dumps({"std": float(scene.srgb.std()), "blurred": float(np.abs(scene.blurred - scene.linear).mean())}))
"""


def test_a_model_of_the_installed_game_is_drawn_as_the_scene_behind_the_ui():
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if not _backends():
        pytest.skip("no QRhi device")
    got = _child(DRAWN_MODEL.replace("sys.argv[1]", repr(root)), timeout=900)
    assert got["std"] > 5.0 and got["blurred"] > 0.0  # a picture with detail, which the blur changes


DRAWN_ISLAND = r"""
import json, sys, time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.suite.settings import Settings
from annotoolbox.uiedit import worldrender
app = QApplication([])
settings = Settings(anno117=sys.argv[1], workspace=sys.argv[2], project="")
names = worldrender.islands(settings, "anno117")
path = next(p for p in names if "/pool/" in p)
got = []
tree = open_game("anno117", Path(sys.argv[1]))  # from the game's camera step
island = worldrender.Island(settings, "anno117", path, look={}, tree=tree)  # the editor's look: no shader compiles
still = worldrender.Still(island, got.append, size=(480, 270))
still.start()
deadline = time.time() + 900
while not got and time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)
scene = got[0]
if isinstance(scene, Exception):
    raise scene
print(json.dumps({"islands": len(names), "key": scene.key, "std": float(scene.srgb.std())}))
"""


def test_an_island_of_the_installed_game_is_drawn_as_the_scene_behind_the_ui(tmp_path):
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if not _backends():
        pytest.skip("no QRhi device")
    script = DRAWN_ISLAND.replace("sys.argv[1]", repr(root)).replace("sys.argv[2]", repr(str(tmp_path / "ws")))
    got = _child(script, timeout=1200)
    assert got["islands"] > 10 and got["key"].startswith("island:") and got["std"] > 5.0  # an island with detail


@pytest.mark.parametrize(("variable", "game", "step"), [("ANNO117_DIR", "anno117", (60.0, 0.8, 0.7)),
                                                         ("ANNO1800_DIR", "anno1800", (60.0, 0.875, 0.55))])  # fmt: skip
def test_the_installed_games_default_camera_step(variable, game, step):
    from pathlib import Path

    pytest.importorskip("PySide6")
    root = os.environ.get(variable)
    if not root:
        pytest.skip(f"needs an installed game ({variable})")
    from annotoolbox.sources.gametree import open_game
    from annotoolbox.uiedit import worldrender

    assert worldrender.game_camera(open_game(game, Path(root))) == worldrender.GameCamera(*step)
