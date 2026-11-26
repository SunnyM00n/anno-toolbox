"""The asset viewer on a synthetic game of loose files: meshes as arrays, skins posed, a config's scene (the baked
``shared/`` copy, levels of detail, materials per level, nested configs, animations), sounds, textures, data files,
the panels' helpers, the window offscreen and the renderer in a standalone OpenGL context (skipped without one)."""

from __future__ import annotations

import math
import os
import struct
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from tests.rdmfiles import animation_file, skinned_file, static_file
from tests.viewer.viewerfakes import BANK, pcm, settle_texture_view, wall_texture
from tests.viewer.viewerfakes import PROP_PATH as PROP
from tests.viewer.viewerfakes import VIEWER_HOUSE_CFG as HOUSE
from tests.viewer.viewerfakes import VIEWER_SHARED_HOUSE_CFG as SHARED
from tests.viewer.viewerfakes import viewer_game as game  # noqa: F401

from annotoolbox.exporting import banks
from annotoolbox.formats.rdmread import SAnimation, SJoint, SKeyframe, SSkeleton, STrack
from annotoolbox.models import fileresolve, skinning
from annotoolbox.models.configtree import SNode, fnv1a32
from annotoolbox.models.mipchain import exposed_srgb8
from annotoolbox.models.rdmmesh import read_meshes
from annotoolbox.sources import gametree
from annotoolbox.suite import buildout
from annotoolbox.viewer import browser, dataview, modelscene, panels
from annotoolbox.viewer.modelfiles import CModelFiles

# meshes and skins


def test_a_mesh_file_reads_as_arrays_with_its_submesh_groups_and_skins():
    static = read_meshes(static_file())
    first = static.meshes[0]
    assert first.vertices.shape[1] == 8 and sorted(first.groups) == [0, 1] and not first.skinned
    assert np.allclose(np.linalg.norm(first.vertices[:, 3:6], axis=1), 1, atol=1e-3)
    skinned = read_meshes(skinned_file())
    body, door = skinned.meshes
    assert body.skinned and body.skeleton == 0 and np.allclose(body.weights.sum(axis=1), 1)
    assert not door.skinned and len(skinned.rdm.skeletons) == 2


def test_a_skin_rests_in_its_bind_pose_and_follows_an_animation():
    rig = skinning.CRig(read_meshes(skinned_file()).rdm.skeletons[0])
    assert np.allclose(rig.pose(None, 0.0), np.identity(4), atol=1e-5)
    anim = read_meshes(animation_file()).rdm.animations[0]
    moved = rig.pose(anim, 0.5)
    assert np.isfinite(moved).all() and not np.allclose(moved, np.identity(4), atol=1e-3)


def test_a_skin_without_an_animation_is_posed_in_its_bind_pose_for_every_joint():
    """The game's shaders read a matrix per joint: a skin that plays nothing gets the bind pose's (one per joint), not
    none (once a single copy, which left every piece but the first joint's unplaced: the 1800 clipper's debris)."""
    from types import SimpleNamespace

    from annotoolbox.viewer import modelrender

    joints = [SJoint(f"j{k}", (float(k), 0.0, 0.0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0)) for k in range(3)]
    file = SimpleNamespace(rdm=SimpleNamespace(skeletons=[SSkeleton(joints, 0)]))
    mesh = SimpleNamespace(skeleton=0)
    item = {"skinned": mesh, "file": file, "pose": modelrender.UNPOSED, "gpu": True, "joints": None, "matrices": None}
    renderer = modelrender.CModelRenderer.__new__(modelrender.CModelRenderer)
    renderer._clip = lambda node, state: (None, 0.0)
    renderer._m_frame, renderer._m_skin_rigs = {}, {}
    written = []
    texture = SimpleNamespace(height=3, write=written.append)
    renderer.m_ctx = SimpleNamespace(texture=lambda *a, **k: texture, NEAREST=0)
    renderer._pose(SimpleNamespace(node="Models/0"), item, None)
    assert item["matrices"].shape == (3, 4, 4) and np.allclose(item["matrices"], np.identity(4))
    assert item["pose"] is None and len(written) == 1  # posed once; the next frame keeps it
    renderer._pose(SimpleNamespace(node="Models/0"), item, None)
    assert len(written) == 1


def test_the_groups_of_a_part_share_one_pose_a_frame():
    from types import SimpleNamespace

    from annotoolbox.viewer import modelrender

    joints = [SJoint("j", (0.0, 0.0, 0.0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0))]
    file = SimpleNamespace(rdm=SimpleNamespace(skeletons=[SSkeleton(joints, 0)]))
    renderer = modelrender.CModelRenderer.__new__(modelrender.CModelRenderer)
    renderer._clip = lambda node, state: (None, 0.0)
    renderer._m_frame, renderer._m_skin_rigs = {}, {}
    renderer.m_ctx = SimpleNamespace(
        texture=lambda *a, **k: SimpleNamespace(height=1, write=lambda data: None), NEAREST=0
    )
    items = [
        {"skinned": SimpleNamespace(skeleton=0), "file": file, "pose": modelrender.UNPOSED, "gpu": True,
         "joints": None, "matrices": None}
        for _ in range(3)
    ]  # fmt: skip
    part = SimpleNamespace(node="Models/0")
    for item in items:
        renderer._pose(part, item, None)
    assert items[0]["matrices"] is items[1]["matrices"] is items[2]["matrices"]
    assert len(renderer._m_skin_rigs) == 1


def test_keys_store_the_conjugate_rotation_and_move_their_joint():
    s = math.sin(math.pi / 4)
    rig = skinning.CRig(SSkeleton([SJoint("j", (0, 0, 0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0))], 0))
    turn = SAnimation("m", 1000, 30, [STrack("j", [SKeyframe((0.0, -s, 0.0, s), (1.0, 0.0, 0.0), 0.0)])])
    m = rig.pose(turn, 0.0)[0]
    assert np.allclose(m[:3, :3] @ [1, 0, 0], [0, 0, -1], atol=1e-6) and np.allclose(m[:3, 3], [1, 0, 0])


# a config's scene


def test_a_config_draws_its_shared_copy_its_materials_by_level_and_its_nested_files(game):  # noqa: F811
    scene = modelscene.build(game, HOUSE, "anno117")
    assert scene.m_config == SHARED and scene.m_lods == 2
    house = next(p for p in scene.m_parts if p.node == "Models/0")
    assert house.mesh.endswith("house_lod0.rdm") and house.material(0).diffuse.endswith("wall_diff_0.dds")
    crane = next(p for p in scene.m_parts if p.node == "Files/0/Models/0")
    assert crane.mesh == "data/base/graphics/props/crane.rdm"
    assert [(c.file, c.scale, c.loops) for c in crane.animations] == [("data/anim/crane_work.rdm", 5.0, 2)]
    assert np.allclose(crane.matrix[:3, 3], (4, 0, 0))
    assert any("data/missing.cfg" in n for n in scene.m_notes) and len(scene.m_sequences) == 1
    far = modelscene.build(game, HOUSE, "anno117", modelscene.SOptions(lod=3, shared=True))
    assert far.m_parts[0].mesh.endswith("house_lod1.rdm") and far.m_parts[0].material(0).name == "roof"
    plain = modelscene.build(game, HOUSE, "anno117", modelscene.SOptions(shared=False))
    assert plain.m_config == HOUSE and plain.m_parts[0].mesh.endswith("rdm/house_lod0.rdm")


def test_the_files_a_model_draws_load_once(game):  # noqa: F811
    files = CModelFiles(game, max_texture_px=2)
    w, h, comps, raw = files.texture("data/base/graphics/walls/wall_diff_0.dds")
    assert (w, h, comps) == (2, 2, 4) and raw[:4] == bytes([0, 255, 0, 255])  # the mip at or below the limit
    assert files.mesh("data/base/graphics/props/crane.rdm") is files.mesh("data/base/graphics/props/crane.rdm")
    assert files.animation("data/anim/crane_work.rdm").duration_ms == 2000
    assert fileresolve.decode(wall_texture())[0, 0].tolist() == [255, 0, 0, 255]


# sounds, textures, data, the panels' helpers


def test_a_bank_lists_its_sounds_and_its_info_names_them(game):  # noqa: F811
    listing = banks.bank_listing(game.read(BANK))
    assert [(s.id, s.codec, s.stream, s.media) for s in listing.sounds] == [
        (11, "PCM", "embedded", 55),
        (12, "PCM", "streamed", 66),
    ]
    assert listing.objects == {"Sound": 2, "Event": 1} and banks.wave_info(listing.embedded(55))["format"] == "PCM"
    info = banks.bank_info(game.read(BANK[:-4] + ".json").decode())
    assert info.name == "BNK_TEST" and info.media[66][0] == "door_open.wav" and info.events[13] == "evt_door"
    assert banks.media_file(BANK, 66, info, game.exists) == "data/sound/generatedsoundbanks/windows/media/66.wem"
    assert banks.bank_info("not json").name == ""


def test_clips_become_playable_files_or_say_why_not():
    wav, suffix = banks.playable(pcm(), "x.wem")
    assert suffix == ".wav" and wav[:4] == b"RIFF" and struct.unpack_from("<H", wav, 20)[0] == 1
    assert banks.wave_info(wav)["seconds"] == pytest.approx(0.1, abs=0.01)
    assert banks.playable(b"ID3...", "music.mp3") == (b"ID3...", ".mp3")
    xma = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 0x166, 1, 48000, 24000, 36, 4)
    with pytest.raises(banks.CUnplayable, match="no decoder"):
        banks.playable(xma + b"data" + struct.pack("<I", 0), "x.wem")


def test_texture_channels_data_kinds_and_the_browsers_index():
    from annotoolbox.viewer.textureview import channel_image, describe

    px = np.array([[[10, 20, 30, 0], [10, 20, 30, 255]]], np.uint8)
    assert channel_image(px, "G")[0, 0].tolist() == [20, 20, 20] and channel_image(px, "A")[0, 1].tolist() == [255] * 3
    assert channel_image(px, "RGBA")[0, 1].tolist() == [10, 20, 30] and channel_image(px, "RGBA")[0, 0, 0] in (150, 200)
    assert ("mip levels", "2") in describe(wall_texture())
    assert dataview.display_kind("a.xml", b"<a/>") == "text" and dataview.display_kind("a.bin", bytes(64)) == "bytes"
    assert dataview.hex_lines(b"AB\x00").startswith("00000000  41 42 00")
    index = browser.folder_index(["a/b/c.cfg", "a/d.dds", "e.txt"])
    assert index[""] == ({"a"}, ["e.txt"]) and index["a/"] == ({"b"}, ["d.dds"]) and index["a/b/"] == (set(), ["c.cfg"])
    assert browser.matches(["a/house_x.cfg", "a/house_y.dds"], "HOUSE cfg", ()) == ["a/house_x.cfg"]
    assert browser.matches(["a/house_x.cfg", "a/house_y.dds"], "house", (".dds",)) == ["a/house_y.dds"]


def test_a_fields_file_name_links_to_the_file_the_game_loads():
    assert panels.file_link("cModelDiffTex", "data\\x_diff.psd") == "data/x_diff_0.dds"
    assert panels.file_link("FileName", "data/a/b.fbx") == "data/a/b.rdm"
    assert panels.file_link("Name", "wall") == ""
    assert panels.node_label(SNode("MODEL", fields=[("FileName", "data/a/rdm/b_lod0.rdm")])) == "MODEL  b_lod0.rdm"


# the window and the renderer


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def viewer_window(tmp_path):
    from annotoolbox.suite.settings import CSettings
    from annotoolbox.viewer.viewerwindow import CViewerWindow

    return CViewerWindow(CSettings(workspace=str(tmp_path / "ws")), None)


@pytest.fixture
def window(qt, game, tmp_path):  # noqa: F811
    """The viewer's window on the synthetic game, closed after the test."""
    window = viewer_window(tmp_path)
    window.m_is_waiting = True  # the scene is built here, so a call returns with the file shown
    window.set_tree(game)
    yield window
    window.close()


def test_the_window_shows_each_kind_of_file_and_follows_links(window):
    window.open_path(HOUSE)
    assert window.m_pages.currentWidget() is window.m_model and window.m_scene.m_config == SHARED
    assert window.m_parts.topLevelItem(0).childCount() == 3  # Files, Models, Sequences
    assert window.m_sequences.m_choice.count() == 3  # the rest pose, the sequence, the crane's animation
    window.m_model.picked.emit("Files/0/Models/0")  # a click on a nested config's part selects its FILE row
    assert (
        window.m_parts.currentItem().data(0, panels.NODE_ROLE) == "Files/0"
        and window.m_model.m_state.selected == "Files/0"
    )
    window.m_props.open_file.emit("data/base/graphics/walls/wall_diff_0.dds")
    settle_texture_view(window.m_texture)
    assert window.m_pages.currentWidget() is window.m_texture and window.m_texture.m_pixels.shape == (4, 4, 4)
    window.open_path(BANK)
    assert window.m_pages.currentWidget() is window.m_sound and window.m_sound.m_table.rowCount() == 2
    assert window.m_sound.m_table.item(1, 4).text().endswith("media/66.wem")
    window.open_path("data/config/settings.xml")
    assert window.m_pages.currentWidget() is window.m_data and "<A>1</A>" in window.m_data.m_text.toPlainText()
    window.back()
    assert window.m_current == BANK
    window.open_path("data/not/there.cfg")
    assert window.m_current == BANK


HOOK = "data/base/graphics/props/hook.rdm"
WALL = "data/base/graphics/walls/wall_diff_0.dds"


@pytest.mark.parametrize("path", [HOOK, HOUSE])
def test_a_model_opened_or_reloaded_again_draws_again(window, path):
    window.open_path(path)
    window.open_path(path)  # the Checks panel met the scene of the same file: compared as the object, not by value
    assert window.m_pages.currentWidget() is window.m_model and window.m_checks.topLevelItemCount() == 2
    window.m_lod_actions[0].trigger()
    assert window.m_pages.currentWidget() is window.m_model


def test_another_game_starts_without_the_open_file_and_the_way_back_of_the_one_before(window, game, tmp_path):  # noqa: F811
    window.open_path(HOOK)
    window.open_path(WALL)
    (tmp_path / "mod").mkdir()
    window.set_tree(game.over(tmp_path / "mod"))  # the same game with a mod laid over it: the way back stays
    assert (window.m_current, window.m_scene, window.m_history) == ("", None, [HOOK])
    assert window.m_parts.topLevelItemCount() == 0
    window.back()
    assert window.m_current == HOOK and window.m_scene is not None
    (tmp_path / "other" / "data").mkdir(parents=True)
    (tmp_path / "other" / "data" / "a.txt").write_text("a", encoding="utf-8")
    other = gametree.loose_tree(tmp_path / "other", "Anno 1800")
    other.m_game = "anno1800"
    window.set_tree(other)
    assert (window.m_current, window.m_scene, window.m_history) == ("", None, [])
    window.m_lod_actions[1].trigger()  # nothing of the game before is reloaded
    window.back()
    assert window.m_current == "" and window.m_pages.currentWidget() is window.m_message


def test_a_file_opened_from_a_search_stays_revealed_when_the_search_would_have_run(qt, game):  # noqa: F811
    files = browser.CFileBrowser()
    files.set_tree(game)
    files.m_search.setText("hook")  # the search waits for the typing to stop
    files.reveal(HOOK)
    assert not files._m_timer.isActive()  # had it run, it would have built the tree anew, closed
    assert files.m_view.currentIndex().data(browser.PATH_ROLE) == HOOK


def test_a_picture_that_does_not_read_says_so_in_its_facts_and_a_list_that_cannot_be_written_too(
    window, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QFileDialog

    from annotoolbox.viewer import referencetools, textureview

    assert textureview.describe(b"no picture")[0][0] == "format" and len(textureview.describe(b"")) == 1
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path), ""))  # a folder: no file
    monkeypatch.setattr(referencetools.refgraph, "unreferenced_csv", lambda *a: "path")
    window.m_references.m_graph = object()
    window.m_references.export_csv()
    assert "was not written" in window.statusBar().currentMessage()
    window.statusBar().clearMessage()
    window.m_model.resize(8, 8)
    window.m_pages.setCurrentWidget(window.m_model)
    window._save_picture()
    assert "was not written" in window.statusBar().currentMessage()


def test_another_app_opens_a_file_in_the_viewer_through_the_suite(qt, game, tmp_path):  # noqa: F811
    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.settings import CSettings

    window = shell.CSuiteWindow(list(apps.BUILTIN), CSettings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    viewer = window.show_app("viewer")
    viewer.m_is_waiting = True
    viewer.set_tree(game)
    window.show_app("assets")
    assert window.view_file("anno117", "data/base/graphics/walls/wall_diff_0.dds")
    assert window.m_current == "viewer" and viewer.m_pages.currentWidget() is viewer.m_texture
    window.close()


def test_the_renderer_draws_a_model_with_its_skin_posed_offscreen(game):  # noqa: F811
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import CModelCamera, CModelRenderer, SViewState

    scene = modelscene.build(game, HOUSE, "anno117")
    renderer = CModelRenderer(ctx, CModelFiles(game))
    renderer.show(scene)
    fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
    camera = CModelCamera((0, 0, 0), 5.0)
    state = SViewState(time=0.5, animation={"Files/0/Models/0": ("data/anim/crane_work.rdm", 0.0)})
    for _ in range(500):  # the files load in the background
        renderer.render(fbo, (96, 64), camera, state)
        if not renderer.busy() and renderer.m_box is not None and len(renderer.m_items) == len(scene.m_parts):
            break
        time.sleep(0.01)
    camera.frame(*renderer.m_box)
    pictures = {}
    # the game's own shaders cannot draw here (no game pass on this device): the plain fallback draws everything
    for look, night in (("editor", False), ("shaders", False), ("shaders", True)):
        state.look, state.night = look, night
        renderer.render(fbo, (96, 64), camera, state)
        px = np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3)
        assert px.std() > 1.0  # something drawn, not one flat colour
        pictures[look, night] = px.astype(int)
    assert renderer._m_lights[0] == 1 and len(renderer._m_decals) == 1  # the light and the decal drawn by the fallback
    assert pictures["shaders", True].mean() < pictures["shaders", False].mean() - 5  # the night is darker
    crane = next(k for k, p in enumerate(scene.m_parts) if p.node == "Files/0/Models/0")
    assert any(item["pose"] is not None for item in renderer.m_items[crane] if item["skinned"] is not None)
    hook = next(p for p in scene.m_parts if p.mesh.endswith("hook.rdm"))
    renderer._m_frame = {}
    moving = renderer.place(hook, state)
    renderer._m_frame = {}
    assert moving is not None and not np.allclose(moving, renderer.place(hook, SViewState()))  # it follows the arm
    hits = {renderer.pick((96, 64), camera, state, x, y) for x in range(8, 96, 8) for y in range(8, 64, 8)}
    assert hits - {None} <= {p.node for p in scene.m_parts} and len(hits - {None}) >= 1  # a click finds a part
    renderer.release()


def test_the_shadow_map_stays_while_only_the_camera_moves_and_is_drawn_again_when_a_pose_changes(game):  # noqa: F811
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import CModelCamera, CModelRenderer, SViewState

    renderer = CModelRenderer(ctx, CModelFiles(game))
    renderer.show(modelscene.build(game, HOUSE, "anno117"))
    fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
    camera = CModelCamera((0, 0, 0), 5.0)
    state = SViewState(time=0.5, animation={"Files/0/Models/0": ("data/anim/crane_work.rdm", 0.0)})
    for _ in range(500):  # the files load in the background
        renderer.render(fbo, (96, 64), camera, state)
        if not renderer.busy() and renderer.m_box is not None:
            break
        time.sleep(0.01)
    camera.frame(*renderer.m_box)
    renderer.render(fbo, (96, 64), camera, state)
    first = renderer._m_shadow_taken
    for _ in range(200):  # a map still arriving in the background makes the shadow anew once; wait until it stands
        renderer.render(fbo, (96, 64), camera, state)
        if renderer._m_shadow_taken is first:
            break
        first = renderer._m_shadow_taken
        time.sleep(0.01)
    assert first is not None
    camera.m_yaw_rad += 0.4
    renderer.render(fbo, (96, 64), camera, state)
    assert renderer._m_shadow_taken is first  # the camera moved: the sun's picture is the same
    state.time = 1.25
    renderer.render(fbo, (96, 64), camera, state)
    assert renderer._m_shadow_taken is not first  # the crane's arm is elsewhere
    renderer.release()


def test_parts_spin_hang_on_joints_and_clips_keep_their_scale_and_loops(game):  # noqa: F811
    from annotoolbox.viewer import motion

    scene = modelscene.build(game, HOUSE, "anno117")
    wheel = next(p for p in scene.m_parts if p.mesh.endswith("wheel.rdm"))
    spin = next(s for s in wheel.motion if isinstance(s, motion.SSpin))
    assert spin.axis == pytest.approx((1, 0, 0)) and spin.rate == pytest.approx(-0.5)  # the engine's sense
    turned = motion.at_time(wheel.motion, 1.0, lambda link: None)
    assert np.allclose(turned[:3, :3] @ [0, 1, 0], [0, math.cos(-0.5), math.sin(-0.5)], atol=1e-6)
    hook = next(p for p in scene.m_parts if p.mesh.endswith("hook.rdm"))
    link = hook.motion[0]
    assert isinstance(link, motion.SLinked) and link.host == "Files/0/Models/0" and link.hash == fnv1a32("hand")
    rig = skinning.CRig(read_meshes(skinned_file()).rdm.skeletons[0])
    joint = rig.world(None, 0.0)[rig.m_hashes[link.hash]]
    at = motion.at_time(hook.motion, 0.0, lambda found: np.identity(4) @ joint)
    assert np.allclose(at[:3, 3], joint[:3, :3] @ [0, 1, 0] + joint[:3, 3])  # the part's place is the joint's frame
    assert skinning.clip_time(1.0, 2000, scale=5.0) == pytest.approx(0.2)
    assert skinning.clip_time(25.0, 2000, scale=5.0, loops=2) == pytest.approx(2.0, abs=1e-4)  # held at the end
    assert skinning.clip_time(1.0, 2000, scale=0.0) is None and skinning.clip_time(3.0, 2000) == pytest.approx(1.0)


def test_a_ray_through_a_pixel_meets_the_nearest_triangle():
    from annotoolbox.render import picking
    from annotoolbox.render.camera import look_at, perspective

    view_proj = perspective(1.0, 1.0, 0.1, 100.0) @ look_at(np.array([0, 0, 5.0], "f4"), np.zeros(3, "f4"))
    origin, direction = picking.ray(view_proj, (100, 100), 50, 50)
    assert np.allclose(direction, [0, 0, -1], atol=1e-5)
    square = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [-1, -1, -2], [1, -1, -2], [0, 1, -2]], float)
    tris = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6]])
    assert picking.nearest(origin, direction, square, tris) == pytest.approx(4.9, abs=1e-3)  # from the near plane
    assert picking.nearest(origin, direction, square, tris[2:]) == pytest.approx(6.9, abs=1e-3)
    assert picking.nearest(*picking.ray(view_proj, (100, 100), 2, 2), square, tris) is None


def test_1404s_nested_configs_are_followed_at_their_place(tmp_path):
    house = (r"<m_Config><m_Models><m_Config><m_FileName>data\gfx\house.gr2</m_FileName>"
             r"<m_ConfigType>MODEL</m_ConfigType></m_Config></m_Models><m_Files><m_Config>"
             r"<m_FileName>data\gfx\flag.cfg</m_FileName><m_ConfigType>FILE</m_ConfigType><m_Transformer><m_Config>"
             r"<m_Position.y>3</m_Position.y><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config>"
             r"</m_Transformer></m_Config></m_Files><m_ConfigType>ROOT</m_ConfigType></m_Config>")  # fmt: skip
    flag = house.replace("house.gr2", "flag.gr2").replace("flag.cfg", "missing.cfg")
    files = {"data/gfx/house.cfg": house, "data/gfx/flag.cfg": flag, "data/gfx/house.gr2": "", "data/gfx/flag.gr2": ""}
    for rel, text in files.items():  # the meshes only have to be there: the scene draws what the game has
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    scene = modelscene.build(gametree.loose_tree(tmp_path), "data/gfx/house.cfg", "anno1404")
    assert [(p.node, p.mesh) for p in scene.m_parts] == [("Models/0", "data/gfx/house.gr2"),
                                                      ("Files/0/Models/0", "data/gfx/flag.gr2")]  # fmt: skip
    assert np.allclose(scene.m_parts[1].matrix[:3, 3], (0, 3, 0)) and any("missing.cfg" in n for n in scene.m_notes)


def test_a_newer_games_particles_started_by_its_sequence_carry_their_slot(tmp_path):
    """A particle a sequence's effect element names (``ParticleID``: its place in ``Particles``) is started by that
    sequence; the others run on. A nested file's are never marked (the view plays only the config's own sequences)."""
    particle = "<Config><ConfigType>PARTICLE</ConfigType><FileName>data/fx/{}.rdp</FileName></Config>"
    cfg = ("<Config><ConfigType>MAIN</ConfigType><Particles>" + particle.format("wake") + particle.format("shot")
           + "</Particles><Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>1005</SequenceID><Track>"
           "<TrackElement><Type>1</Type><StartTime>0</StartTime><EndTime>3000</EndTime><ParticleID>1</ParticleID>"
           "</TrackElement></Track></Config></Sequences></Config>")  # fmt: skip
    for rel, text in {"data/ship.cfg": cfg, "data/fx/wake.rdp": "", "data/fx/shot.rdp": ""}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    scene = modelscene.build(gametree.loose_tree(tmp_path), "data/ship.cfg", "anno1800")
    assert [(e.node, e.started) for e in scene.m_effects] == [("Particles/0", -1), ("Particles/1", 1)]


def test_a_scene_reads_the_metal_map_lights_and_decals(game):  # noqa: F811
    scene = modelscene.build(game, HOUSE, "anno117")
    wall = next(p for p in scene.m_parts if p.node == "Models/0").material(0)
    assert wall.metal.endswith("wall_diff_0.dds")  # the forward games' own shaders read it
    (light,) = scene.m_lights
    assert (
        light.node == "Files/0/Lights/0" and light.color == (3.0, 1.5, 0.75) and (light.range, light.falloff) == (4, 2)
    )
    assert np.allclose(light.matrix[:3, 3], (4, 2, 0))  # inside the nested file, at its place
    assert light.color_at(0.0) == pytest.approx((3.0, 1.5, 0.75)) and light.color_at(1.0) == pytest.approx(
        (1.5, 0.75, 0.375)
    )
    (decal,) = scene.m_decals
    assert decal.extents == (2.0, 1.0) and decal.material.opacity == 0.5 and decal.material.diffuse.endswith("_0.dds")


def test_a_2070_light_with_a_spin_keeps_its_motion():
    from types import SimpleNamespace

    from annotoolbox.models import cfg2070

    spinning = cfg2070.SLight({"Range": 3.0}, None, False, spins=[cfg2070.SSpin((0.0, 1.0, 0.0), 1.0)])
    still = cfg2070.SLight({"Range": 3.0}, None, False)
    cfg = SimpleNamespace(lights=[spinning, still], decals=[])
    scene = modelscene.CScene("x.cfg", "anno2070")
    modelscene._lights_and_decals2070(cfg, (), "", scene, None)
    assert scene.m_lights[0].motion and not scene.m_lights[1].motion  # a spinning light turns, as a newer game's does


def test_an_hdr_cube_shows_at_an_exposure_and_as_a_cross(qt):
    from tests.ddsfiles import dx10, header

    from annotoolbox.viewer.textureview import CTextureView, cube_cross

    assert exposed_srgb8(np.array([[[0.25, 1.0, 4.0, 2.0]]], np.float32), 1.0).tolist() == [[[188, 255, 255, 255]]]
    faces = [np.full((2, 2, 4), k, np.uint8) for k in range(6)]
    cross = cube_cross(faces)
    assert cross.shape == (6, 8, 4) and cross[0, 2, 0] == 2 and cross[2, 4, 0] == 0 and cross[2, 0, 0] == 1
    assert cross[0, 0, 3] == 0  # no face there
    half = np.array([0.5, 2.0, 8.0, 1.0], np.float16).tobytes()
    cube = bytearray(header(1, 1, 1) + dx10(10) + half * 6)
    cube[136:140] = (4).to_bytes(4, "little")  # the DX10 header's cube flag
    view = CTextureView()
    view.show_texture("sky.dds", bytes(cube))
    settle_texture_view(view)
    assert not view.m_exposure.isHidden() and not view.m_cross.isHidden() and view.m_pixels.shape == (1, 1, 4)
    view.m_exposure.setValue(-3.0)
    settle_texture_view(view)
    assert (
        view.m_pixels[0, 0, :3].tolist()
        == exposed_srgb8(np.array([[[0.5, 2, 8, 1]]], np.float32), -3.0)[0, 0, :3].tolist()
    )
    view.m_cross.setChecked(True)
    settle_texture_view(view)
    assert view.m_pixels.shape == (3, 4, 4)


def test_a_picture_that_does_not_decode_leaves_no_earlier_picture_behind(qt):
    import io

    from PIL import Image

    from annotoolbox.viewer.textureview import CTextureView

    png = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(png, "PNG")
    view = CTextureView()
    view.show_texture("a.png", png.getvalue())
    settle_texture_view(view)
    assert view.m_pixels is not None and not view.m_picture.pixmap().isNull()
    view.show_texture("broken.png", b"not a picture")
    settle_texture_view(view)
    assert view.m_pixels is None and view.m_picture.pixmap().isNull() and "broken.png" in view.m_info.text()


def test_the_texture_views_scroll_area_leaves_qts_scroll_method_alone(qt):
    from PySide6.QtWidgets import QScrollArea

    from annotoolbox.viewer.textureview import CTextureView

    view = CTextureView()
    assert callable(view.scroll) and isinstance(view.m_scroll, QScrollArea)


def test_a_model_file_read_by_two_threads_is_kept_once():
    from annotoolbox.viewer.modelfiles import CModelFiles

    files = CModelFiles(None)
    first = files._once("mesh", "a", lambda: files._once("mesh", "a", lambda: "stored first") and "read late")
    assert first == "stored first" and files._once("mesh", "a", lambda: "never") == "stored first"


def test_a_skin_posed_on_the_gpu_draws_as_the_cpu_poses_it(game):  # noqa: F811
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import CModelCamera, CModelRenderer, SViewState

    scene = modelscene.build(game, PROP, "anno117")
    state = SViewState(time=0.7, shadows=False, ground=False, animation={"Models/0": ("data/anim/crane_work.rdm", 0.0)})
    pictures = []
    for gpu in (False, True):
        renderer = CModelRenderer(ctx, CModelFiles(game))
        renderer.m_gpu_skin = gpu
        renderer.show(scene)
        fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
        camera = CModelCamera((0, 0, 0), 5.0)
        for _ in range(500):
            renderer.render(fbo, (96, 64), camera, state)
            if not renderer.busy() and renderer.m_box is not None and len(renderer.m_items) == len(scene.m_parts):
                break
            time.sleep(0.01)
        camera.frame(*renderer.m_box)
        renderer.render(fbo, (96, 64), camera, state)
        pictures.append(np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3).astype(int))
        if gpu:  # the same view at rest: the pose moved something
            renderer.render(fbo, (96, 64), camera, SViewState(shadows=False, ground=False))
            rest = np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3).astype(int)
            assert np.abs(rest - pictures[-1]).mean() > 0.5 and pictures[-1].std() > 1.0
        crane = renderer.m_items[0]
        assert all(item["gpu"] == gpu for item in crane if item["skinned"] is not None)
        renderer.release()
    assert np.abs(pictures[0] - pictures[1]).mean() < 1.0  # the same pose either way


def test_particles_and_decals_test_the_depth_but_never_write_it(game):  # noqa: F811
    """moderngl keeps depth writes per framebuffer (its context has no switch): a sprite that wrote its quad's depth
    hid the sprites drawn after it behind a square (2205's air buoy)."""
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import CModelCamera, CModelRenderer, SViewState

    renderer = CModelRenderer(ctx, CModelFiles(game))
    renderer.show(modelscene.build(game, PROP, "anno117"))
    masks = []
    renderer._draw_particles = lambda *args: masks.append(renderer._m_targets["msaa"].depth_mask)
    fbo = ctx.framebuffer([ctx.renderbuffer((32, 32), 4)], ctx.depth_renderbuffer((32, 32)))
    renderer.render(fbo, (32, 32), CModelCamera((0, 0, 0), 5.0), SViewState())
    assert masks == [False] and renderer._m_targets["msaa"].depth_mask
    renderer.release()


def test_a_root_the_clip_does_not_name_stands_at_the_origin_and_other_joints_keep_their_bind():
    joints = [SJoint("Bip01", (0, -1, 0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0)),
              SJoint("Bip01 Pelvis", (0, -1, 0), (0, 0, 0, 1), 0, (0, 0, 0), (0, 0, 0)),
              SJoint("Bip01 Spine", (0, -1.5, 0), (0, 0, 0, 1), 1, (0, 0, 0), (0, 0, 0))]  # fmt: skip
    rig = skinning.CRig(SSkeleton(joints, 0))
    assert np.allclose(rig.world(None, 0.0)[0][:3, 3], (0, 1, 0))  # the bind pose: the root at the hip's height
    clip = SAnimation("kid", 1000, 30, [STrack("Bip01 Pelvis", [SKeyframe((0, 0, 0, 1), (0, 0.9, 0), 0.0)])])
    world = rig.world(clip, 0.0)
    assert np.allclose(world[0][:3, 3], 0) and np.allclose(world[1][:3, 3], (0, 0.9, 0))
    assert np.allclose(world[2][:3, 3], (0, 1.4, 0))  # the spine keeps its bind offset above the pelvis


def test_the_view_switches_come_back_on_and_the_hardware_entry_flips(window):
    shadows = window.m_toggles["shadows"]
    shadows.trigger()
    assert window.m_model.m_state.shadows is False
    shadows.trigger()
    assert window.m_model.m_state.shadows is True  # the slot got the checked state, not a default False
    window.m_toggles["night"].trigger()
    assert window.m_model.m_state.night is True
    window.m_rendering.rebuild()
    hardware = next(a for a in window.m_rendering.m_menu.actions() if a.text().startswith("&Hardware"))
    was = window.m_settings.hardware
    hardware.trigger()
    assert window.m_settings.hardware is (not was)


def test_condition_bits_are_held_by_hand_from_the_view_menu(window, game):  # noqa: F811
    window._show_model(modelscene.build(game, PROP, "anno117"))
    window.m_bits_menu.aboutToShow.emit()
    texts = [a.text() for a in window.m_bits_menu.actions() if a.text()]
    assert texts[:3] == ["Bit 2: DestructionTransform", "Bit 3: Selected", "Bit 4: Unselected"]
    next(a for a in window.m_bits_menu.actions() if a.text() == "Bit 3: Selected").trigger()
    assert window.m_held == 8 and window.m_model.m_state.states.bits & 8
    window.m_bits_menu.aboutToShow.emit()
    assert next(a for a in window.m_bits_menu.actions() if a.text() == "Bit 3: Selected").isChecked()
    next(a for a in window.m_bits_menu.actions() if a.text() == "&Release all").trigger()
    assert window.m_held == 0 and not window.m_model.m_state.states.bits


def test_a_model_config_opens_in_the_object_builder_from_the_files_menu(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from annotoolbox.suite import links

    monkeypatch.setattr(links, "_g_providers", {})  # what an earlier test's suite shell provided is not here
    alone = viewer_window(tmp_path)
    assert not alone.m_edit_action.isVisible()  # no object builder beside it: no entry
    alone.close()
    asked = []
    links.provide(links.EDIT_OBJECT, lambda g, p: asked.append((g, p)) or True)
    try:
        window = viewer_window(tmp_path)
        window.set_tree(game)
        assert window.m_edit_action.isVisible()
        window.m_current = "data/a/b.fbx"
        window.m_edit_action.trigger()
        assert not asked  # a mesh is no object
        window.m_current = "data/a/b.cfg"
        window.m_edit_action.trigger()
        assert asked == [(game.m_game, "data/a/b.cfg")]
        window.close()
    finally:
        links.withdraw(links.EDIT_OBJECT)


def test_imported_models_are_kept_whole_in_the_project_and_go_into_the_mod(tmp_path):
    from annotoolbox.suite.project import CProject
    from annotoolbox.viewer import projectpart

    project = CProject(tmp_path / "proj", mod_id="my-mod")
    assert not projectpart.PART.has_work(project)
    projectpart.EDITS.save_files(project, "anno117", {"/data/a/x.rdm": b"rdm"})
    assert projectpart.EDITS.saved_files(project)["data/a/x.rdm"].read_bytes() == b"rdm"
    assert projectpart.PART.has_work(project)
    assert buildout.read_all(projectpart.PART.render(project, None, lambda _m: None)) == {"data/a/x.rdm": b"rdm"}
    with pytest.raises(ValueError):
        projectpart.EDITS.save_files(project, "anno117", {"../x.rdm": b"no"})


def test_the_plain_look_fades_every_games_lights_by_one_falloff():
    from annotoolbox.viewer import plainshading

    lamp = modelscene.SPlacedLight("l", np.identity(4), (1.0, 0.5, 0.25), 8.0, 2.0)
    count, _pos, col, reach = plainshading.light_uniforms([lamp], [np.identity(4)], lambda light: (0.5, 0.25, 0.0),
                                                          np.zeros(3))  # fmt: skip
    assert count == 1 and np.frombuffer(reach, "f4")[:2].tolist() == [8.0, 2.0]
    assert np.frombuffer(col, "f4")[:3].tolist() == [0.5, 0.25, 0.0]  # the colour the frame gives it (faded)
    assert "dye" not in plainshading.LIT_FS and "envTex" not in plainshading.LIT_FS  # no game's material imitated


def test_the_offscreen_picture_waits_only_for_the_parts_the_state_shows(qt, monkeypatch):
    """A part a condition bit hides (a fountain's water, the lobster farm's foam) is never read: the picture waited
    the whole time for it and came back unframed; it frames once the shown parts are in (or failed to read)."""
    from concurrent.futures import Future
    from types import SimpleNamespace as N

    from annotoolbox.viewer import modelview
    from annotoolbox.viewer.modelrender import CModelRenderer, SViewState

    def part(node: str, when=()):
        return N(node=node, when=when, times=())

    failed = Future()
    failed.set_result(OSError("no mesh"))
    scene = N(m_parts=[part("house"), part("water", (1 << 3,)), part("broken")])
    r = CModelRenderer.__new__(CModelRenderer)
    r.m_scene, r.m_items, r.m_loader, r.m_box = (
        scene,
        {0: []},
        N(m_futures={"mesh:2": failed}, pending=lambda: 0),
        (0, 1),
    )
    state = SViewState()
    assert r.shown_parts_in(state)  # the water is hidden by its bit, the broken part failed
    del r.m_loader.m_futures["mesh:2"]
    assert not r.shown_parts_in(state)  # the broken part's mesh is still asked for
    r.m_loader.m_futures["mesh:2"] = failed
    r.m_items = {}
    assert not r.shown_parts_in(state)  # the house is not in yet

    r.m_items, framed, drawn = {0: []}, [], []

    class View:
        def __init__(self, backend):
            self.m_renderer, self.m_failed, self.m_ctx = None, "", None

        def show_scene(self, scene, files):
            self.m_renderer = r

        def picture(self, w, h):
            drawn.append(len(framed))
            return ("picture", len(framed))

        def frame_model(self):
            framed.append(True)

        release = close = deleteLater = lambda self: None

    monkeypatch.setattr(modelview, "CRhiModelView", View)
    loads = iter([True, False])  # a file comes in on the first frame: that frame is not quiet
    r.busy, r.changed = lambda: 0, lambda: next(loads, False)
    start = time.monotonic()
    image = modelview.rhi_picture(None, scene, state, (8, 8), "d3d11", wait_s=30.0)
    assert framed == [True] and image == ("picture", 1) and time.monotonic() - start < 5.0
    assert drawn == [0, 0, 1, 1]  # a frame loading, the framing frame, then two quiet ones


def test_a_folder_of_loose_files_is_listed_off_the_windows_thread(qt, window, tmp_path, monkeypatch):
    """Listing an unpacked game's hundreds of thousands of files held the window; it now runs in the background, the
    status line says so, and a folder that cannot be listed says why."""
    import threading

    folder = tmp_path / "loose"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "a.xml").write_text("<a/>", encoding="utf-8")
    listed, real = [], gametree.loose_tree

    def loose_tree(path):
        listed.append(threading.current_thread())
        return real(path)

    monkeypatch.setattr(gametree, "loose_tree", loose_tree)
    game = window.m_tree.m_game  # noqa: F811
    window.open_folder(folder)
    assert "loose" in window.statusBar().currentMessage() and window.m_tree.m_name != "loose"  # still the game's
    window.m_background.join()
    qt.processEvents()
    assert listed and listed[0] is not threading.main_thread()
    assert window.m_tree.m_name == "loose" and window.m_tree.m_game == game and window.m_tree.exists("data/a.xml")

    def refused(path):
        raise PermissionError("no access")

    monkeypatch.setattr(gametree, "loose_tree", refused)
    window.open_folder(folder, is_waiting=True)
    assert "no access" in window.statusBar().currentMessage() and window.m_tree.m_name == "loose"
