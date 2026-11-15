"""The asset viewer on a synthetic game of loose files: meshes as arrays, skins posed, a config's scene (the baked
``shared/`` copy, levels of detail, materials per level, nested configs, animations), sounds, textures, data files,
the panels' helpers, the window offscreen and the renderer in a standalone OpenGL context (skipped without one)."""

from __future__ import annotations

import array
import json
import math
import os
import struct
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from tests.rdmsamples import animation_file, skinned_file, static_file
from tests.test_ddsdecode import dx10, header

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk
from annotoolbox.formats.rdmread import Animation, Joint, Keyframe, Skeleton, Track
from annotoolbox.models import fileresolve, skinning
from annotoolbox.models.configtree import Node, fnv1a32
from annotoolbox.models.mipchain import exposed_srgb8
from annotoolbox.models.rdmmesh import read_meshes
from annotoolbox.sources import gametree
from annotoolbox.viewer import browser, dataview, modelscene, panels
from annotoolbox.viewer.modelfiles import ModelFiles

HOUSE = "data/base/graphics/house/house.cfg"
SHARED = "shared/base/graphics/house/house.cfg"
PROP = "data/base/graphics/props/prop.cfg"
BANK = "data/sound/generatedsoundbanks/windows/1.bnk"
MODEL = """<Config><ConfigType>MAIN</ConfigType>
<Files><Config><ConfigType>FILE</ConfigType><FileName>{prop}</FileName>
<Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.x>4</Position.x></Config></Transformer>
</Config><Config><ConfigType>FILE</ConfigType><FileName>data/missing.cfg</FileName></Config></Files>
<Models><Config><ConfigType>MODEL</ConfigType><FileName>{mesh}</FileName>
<Materials><Config><ConfigType>MATERIAL</ConfigType><Name>wall</Name><ShaderID>8</ShaderID>
<cModelDiffTex>data/base/graphics/walls/wall_diff.psd</cModelDiffTex><METALLIC_TEX_ENABLED>1</METALLIC_TEX_ENABLED>
<cModelMetallicTex>data/base/graphics/walls/wall_diff.psd</cModelMetallicTex><DYE_MASK_ENABLED>1</DYE_MASK_ENABLED>
<cDyeMask>data/base/graphics/walls/wall_diff.psd</cDyeMask><GLOW_ENABLED>1</GLOW_ENABLED><cEmissiveColor>3</cEmissiveColor>
<cGlossinessFactor>0.5</cGlossinessFactor></Config>
<Config><ConfigType>MATERIAL</ConfigType><Name>roof</Name><ShaderID>8</ShaderID></Config></Materials>
<MaterialLODInfos><i><Indices><i>0</i><i>1</i></Indices></i><i><Indices><i>1</i><i>0</i></Indices></i></MaterialLODInfos>
</Config></Models>
<Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>100691457</SequenceID><Looped>1</Looped>
<Track><TrackElement><Type>2</Type><StartTime>0</StartTime><EndTime>500</EndTime></TrackElement></Track>
</Config></Sequences></Config>"""
PROP_CFG = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/base/graphics/props/crane.fbx</FileName>
<Animations><Config><ConfigType>ANIMATION</ConfigType><FileName>data/anim/crane_work.fbx</FileName>
<LoopCount>2</LoopCount><Scale>5</Scale></Config></Animations></Config>
<Config><ConfigType>MODEL</ConfigType><FileName>data/base/graphics/props/hook.rdm</FileName>
<Transformer><Config><ConfigType>BONELINK_TRANSFORM</ConfigType><ModelID>0</ModelID><BoneHash>{hand}</BoneHash></Config>
<Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.y>1</Position.y></Config></Transformer></Config>
<Config><ConfigType>MODEL</ConfigType><FileName>data/base/graphics/props/wheel.rdm</FileName>
<Transformer><Config><ConfigType>ANIMATION_TRANSFORM</ConfigType><Rotation.x>1</Rotation.x>
<RotationSpeed>0.5</RotationSpeed></Config></Transformer></Config></Models>
<Lights><Config><ConfigType>LIGHT</ConfigType><Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType>
<Position.y>2</Position.y></Config></Transformer><Type>0</Type><Diffuse.r>1</Diffuse.r><Diffuse.g>0.5</Diffuse.g>
<Diffuse.b>0.25</Diffuse.b><Diffuse.a>3</Diffuse.a><Range>4</Range><Falloff>2</Falloff><GraphDuration>2</GraphDuration>
<ColorGraph><i><t>0</t><c.r>1</c.r><c.g>1</c.g><c.b>1</c.b><c.a>1</c.a></i><i><t>1</t><c.r>1</c.r><c.g>1</c.g>
<c.b>1</c.b><c.a>0</c.a></i></ColorGraph></Config></Lights>
<Decals><Config><ConfigType>DECAL</ConfigType><Extents.x>2</Extents.x><Extents.y>0.25</Extents.y><Extents.z>1</Extents.z>
<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>1</ShaderID>
<cModelDiffTex>data/base/graphics/walls/wall_diff.psd</cModelDiffTex><cOpacity>0.5</cOpacity></Config></Materials>
</Config></Decals>
<Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>251691777</SequenceID><Looped>1</Looped>
<Track><TrackElement><Type>0</Type><StartTime>0</StartTime><EndTime>2000</EndTime><ModelID>0</ModelID>
<AnimationID>0</AnimationID></TrackElement></Track></Config></Sequences></Config>"""


def wall_texture() -> bytes:
    """A 4 x 4 RGBA8 DDS with two mip levels: red, then green."""
    return header(4, 4, 2) + dx10(28) + bytes([255, 0, 0, 255]) * 16 + bytes([0, 255, 0, 255]) * 4


def pcm(seconds: float = 0.1) -> bytes:
    return bnk.pcm_wem(array.array("h", [0, 1000] * int(24000 * seconds)), 48000, 1)


def bank_file() -> bytes:
    """A bank with an embedded PCM sound (media 55), a streamed one (media 66) and an event."""
    sound = lambda stream, media: struct.pack("<IBI", bnk.PCM_PLUGIN, stream, media) + bytes(8)  # noqa: E731
    blob = pcm()
    head = bnk.Chunk(b"BKHD", struct.pack("<III", 172, 1, 0))
    bank = bnk.SoundBank([head, bnk.Chunk(b"DIDX", b""), bnk.Chunk(b"DATA", blob), bnk.Chunk(b"HIRC", b"")])
    bank.hirc = [bnk.HircObject(2, 11, sound(0, 55)), bnk.HircObject(2, 12, sound(1, 66)), bnk.HircObject(4, 13, b"")]
    bank.media = [bnk.MediaEntry(55, 0, len(blob))]
    return bnk.write(bank)


def bank_json() -> str:
    media = [{"Id": "66", "ShortName": "door_open.wav", "Path": "Media/66.wem"}]
    return json.dumps({"SoundBanksInfo": {"SoundBanks": [{"ShortName": "BNK_TEST", "Media": media,
                                                          "Events": [{"Id": "13", "Name": "evt_door"}]}]}})  # fmt: skip


@pytest.fixture
def game(tmp_path):
    files = {
        HOUSE: MODEL.format(prop=PROP, mesh="data/base/graphics/house/rdm/house_lod0.fbx"),
        SHARED: MODEL.format(prop=PROP, mesh="shared/base/graphics/house/rdm/house_lod0.rdm"),
        "shared/base/graphics/house/rdm/house_lod0.rdm": static_file(),
        "shared/base/graphics/house/rdm/house_lod1.rdm": static_file(),
        PROP: PROP_CFG.replace("{hand}", str(fnv1a32("hand"))),
        "data/base/graphics/props/hook.rdm": static_file(),
        "data/base/graphics/props/wheel.rdm": static_file(),
        "data/base/graphics/props/crane.rdm": skinned_file(),
        "data/anim/crane_work.rdm": animation_file(),
        "data/base/graphics/walls/wall_diff_0.dds": wall_texture(),
        BANK: bank_file(),
        BANK[:-4] + ".json": bank_json(),
        "data/sound/generatedsoundbanks/windows/media/66.wem": pcm(0.2),
        "data/config/settings.xml": "<Settings><A>1</A></Settings>",
    }
    for rel, data in files.items():
        p = tmp_path / "117" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    tree = gametree.loose_tree(tmp_path / "117", "Anno 117")
    tree.game = "anno117"
    return tree


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
    rig = skinning.Rig(read_meshes(skinned_file()).rdm.skeletons[0])
    assert np.allclose(rig.pose(None, 0.0), np.identity(4), atol=1e-5)
    anim = read_meshes(animation_file()).rdm.animations[0]
    moved = rig.pose(anim, 0.5)
    assert np.isfinite(moved).all() and not np.allclose(moved, np.identity(4), atol=1e-3)


def test_a_skin_without_an_animation_is_posed_in_its_bind_pose_for_every_joint():
    """The game's shaders read a matrix per joint: a skin that plays nothing gets the bind pose's (one per joint), not
    none (once a single copy, which left every piece but the first joint's unplaced: the 1800 clipper's debris)."""
    from types import SimpleNamespace

    from annotoolbox.viewer import modelrender

    joints = [Joint(f"j{k}", (float(k), 0.0, 0.0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0)) for k in range(3)]
    file = SimpleNamespace(rdm=SimpleNamespace(skeletons=[Skeleton(joints, 0)]))
    mesh = SimpleNamespace(skeleton=0)
    item = {"skinned": mesh, "file": file, "pose": modelrender.UNPOSED, "gpu": True, "joints": None, "matrices": None}
    renderer = modelrender.ModelRenderer.__new__(modelrender.ModelRenderer)
    renderer._clip = lambda node, state: (None, 0.0)
    written = []
    texture = SimpleNamespace(height=3, write=written.append)
    renderer.ctx = SimpleNamespace(texture=lambda *a, **k: texture, NEAREST=0)
    renderer._pose(SimpleNamespace(node="Models/0"), item, None)
    assert item["matrices"].shape == (3, 4, 4) and np.allclose(item["matrices"], np.identity(4))
    assert item["pose"] is None and len(written) == 1  # posed once; the next frame keeps it
    renderer._pose(SimpleNamespace(node="Models/0"), item, None)
    assert len(written) == 1


def test_keys_store_the_conjugate_rotation_and_move_their_joint():
    s = math.sin(math.pi / 4)
    rig = skinning.Rig(Skeleton([Joint("j", (0, 0, 0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0))], 0))
    turn = Animation("m", 1000, 30, [Track("j", [Keyframe((0.0, -s, 0.0, s), (1.0, 0.0, 0.0), 0.0)])])
    m = rig.pose(turn, 0.0)[0]
    assert np.allclose(m[:3, :3] @ [1, 0, 0], [0, 0, -1], atol=1e-6) and np.allclose(m[:3, 3], [1, 0, 0])


# a config's scene


def test_a_config_draws_its_shared_copy_its_materials_by_level_and_its_nested_files(game):
    scene = modelscene.build(game, HOUSE, "anno117")
    assert scene.config == SHARED and scene.lods == 2
    house = next(p for p in scene.parts if p.node == "Models/0")
    assert house.mesh.endswith("house_lod0.rdm") and house.material(0).diffuse.endswith("wall_diff_0.dds")
    crane = next(p for p in scene.parts if p.node == "Files/0/Models/0")
    assert crane.mesh == "data/base/graphics/props/crane.rdm"
    assert [(c.file, c.scale, c.loops) for c in crane.animations] == [("data/anim/crane_work.rdm", 5.0, 2)]
    assert np.allclose(crane.matrix[:3, 3], (4, 0, 0))
    assert any("data/missing.cfg" in n for n in scene.notes) and len(scene.sequences) == 1
    far = modelscene.build(game, HOUSE, "anno117", modelscene.Options(lod=3, shared=True))
    assert far.parts[0].mesh.endswith("house_lod1.rdm") and far.parts[0].material(0).name == "roof"
    plain = modelscene.build(game, HOUSE, "anno117", modelscene.Options(shared=False))
    assert plain.config == HOUSE and plain.parts[0].mesh.endswith("rdm/house_lod0.rdm")


def test_the_files_a_model_draws_load_once(game):
    files = ModelFiles(game, texture_max=2)
    w, h, comps, raw = files.texture("data/base/graphics/walls/wall_diff_0.dds")
    assert (w, h, comps) == (2, 2, 4) and raw[:4] == bytes([0, 255, 0, 255])  # the mip at or below the limit
    assert files.mesh("data/base/graphics/props/crane.rdm") is files.mesh("data/base/graphics/props/crane.rdm")
    assert files.animation("data/anim/crane_work.rdm").duration_ms == 2000
    assert fileresolve.decode(wall_texture())[0, 0].tolist() == [255, 0, 0, 255]


# sounds, textures, data, the panels' helpers


def test_a_bank_lists_its_sounds_and_its_info_names_them(game):
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
    with pytest.raises(banks.Unplayable, match="no decoder"):
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
    assert panels.node_label(Node("MODEL", fields=[("FileName", "data/a/rdm/b_lod0.rdm")])) == "MODEL  b_lod0.rdm"


# the window and the renderer


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_window_shows_each_kind_of_file_and_follows_links(qt, game, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(game)
    window.open_path(HOUSE)
    assert window.pages.currentWidget() is window.model and window.scene.config == SHARED
    assert window.parts.topLevelItem(0).childCount() == 3  # Files, Models, Sequences
    assert window.sequences.choice.count() == 3  # the rest pose, the sequence, the crane's animation
    window.model.picked.emit("Files/0/Models/0")  # a click on a nested config's part selects its FILE row
    assert (
        window.parts.currentItem().data(0, panels.NODE_ROLE) == "Files/0" and window.model.state.selected == "Files/0"
    )
    window.props.open_file.emit("data/base/graphics/walls/wall_diff_0.dds")
    assert window.pages.currentWidget() is window.texture and window.texture.pixels.shape == (4, 4, 4)
    window.open_path(BANK)
    assert window.pages.currentWidget() is window.sound and window.sound.table.rowCount() == 2
    assert window.sound.table.item(1, 4).text().endswith("media/66.wem")
    window.open_path("data/config/settings.xml")
    assert window.pages.currentWidget() is window.data and "<A>1</A>" in window.data.text.toPlainText()
    window.back()
    assert window.current == BANK
    window.open_path("data/not/there.cfg")
    assert window.current == BANK
    window.close()


def test_another_app_opens_a_file_in_the_viewer_through_the_suite(qt, game, tmp_path):
    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.settings import Settings

    window = shell.SuiteWindow(list(apps.BUILTIN), Settings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    viewer = window.show_app("viewer")
    viewer.set_tree(game)
    window.show_app("assets")
    assert window.view_file("anno117", "data/base/graphics/walls/wall_diff_0.dds")
    assert window.current == "viewer" and viewer.pages.currentWidget() is viewer.texture
    window.close()


def test_the_renderer_draws_a_model_with_its_skin_posed_offscreen(game):
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import ModelCamera, ModelRenderer, ViewState

    scene = modelscene.build(game, HOUSE, "anno117")
    renderer = ModelRenderer(ctx, ModelFiles(game))
    renderer.show(scene)
    fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
    camera = ModelCamera((0, 0, 0), 5.0)
    state = ViewState(time=0.5, animation={"Files/0/Models/0": ("data/anim/crane_work.rdm", 0.0)})
    for _ in range(500):  # the files load in the background
        renderer.render(fbo, (96, 64), camera, state)
        if not renderer.busy() and renderer.box is not None and len(renderer.items) == len(scene.parts):
            break
        time.sleep(0.01)
    camera.frame(*renderer.box)
    pictures = {}
    # the game's own shaders cannot draw here (no game pass on this device): the plain fallback draws everything
    for look, night in (("editor", False), ("shaders", False), ("shaders", True)):
        state.look, state.night = look, night
        renderer.render(fbo, (96, 64), camera, state)
        px = np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3)
        assert px.std() > 1.0  # something drawn, not one flat colour
        pictures[look, night] = px.astype(int)
    assert renderer._lights[0] == 1 and len(renderer._decals) == 1  # the light and the decal drawn by the fallback
    assert pictures["shaders", True].mean() < pictures["shaders", False].mean() - 5  # the night is darker
    crane = next(k for k, p in enumerate(scene.parts) if p.node == "Files/0/Models/0")
    assert any(item["pose"] is not None for item in renderer.items[crane] if item["skinned"] is not None)
    hook = next(p for p in scene.parts if p.mesh.endswith("hook.rdm"))
    renderer._frame = {}
    moving = renderer.place(hook, state)
    renderer._frame = {}
    assert moving is not None and not np.allclose(moving, renderer.place(hook, ViewState()))  # it follows the arm
    hits = {renderer.pick((96, 64), camera, state, x, y) for x in range(8, 96, 8) for y in range(8, 64, 8)}
    assert hits - {None} <= {p.node for p in scene.parts} and len(hits - {None}) >= 1  # a click finds a part
    renderer.release()


def test_parts_spin_hang_on_joints_and_clips_keep_their_scale_and_loops(game):
    from annotoolbox.viewer import motion

    scene = modelscene.build(game, HOUSE, "anno117")
    wheel = next(p for p in scene.parts if p.mesh.endswith("wheel.rdm"))
    spin = next(s for s in wheel.motion if isinstance(s, motion.Spin))
    assert spin.axis == pytest.approx((1, 0, 0)) and spin.rate == pytest.approx(-0.5)  # the engine's sense
    turned = motion.at_time(wheel.motion, 1.0, lambda link: None)
    assert np.allclose(turned[:3, :3] @ [0, 1, 0], [0, math.cos(-0.5), math.sin(-0.5)], atol=1e-6)
    hook = next(p for p in scene.parts if p.mesh.endswith("hook.rdm"))
    link = hook.motion[0]
    assert isinstance(link, motion.Linked) and link.host == "Files/0/Models/0" and link.hash == fnv1a32("hand")
    rig = skinning.Rig(read_meshes(skinned_file()).rdm.skeletons[0])
    joint = rig.world(None, 0.0)[rig.hashes[link.hash]]
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
    assert [(p.node, p.mesh) for p in scene.parts] == [("Models/0", "data/gfx/house.gr2"),
                                                      ("Files/0/Models/0", "data/gfx/flag.gr2")]  # fmt: skip
    assert np.allclose(scene.parts[1].matrix[:3, 3], (0, 3, 0)) and any("missing.cfg" in n for n in scene.notes)


def test_a_scene_reads_the_metal_map_lights_and_decals(game):
    scene = modelscene.build(game, HOUSE, "anno117")
    wall = next(p for p in scene.parts if p.node == "Models/0").material(0)
    assert wall.metal.endswith("wall_diff_0.dds")  # the forward games' own shaders read it
    (light,) = scene.lights
    assert (
        light.node == "Files/0/Lights/0" and light.color == (3.0, 1.5, 0.75) and (light.range, light.falloff) == (4, 2)
    )
    assert np.allclose(light.matrix[:3, 3], (4, 2, 0))  # inside the nested file, at its place
    assert light.color_at(0.0) == pytest.approx((3.0, 1.5, 0.75)) and light.color_at(1.0) == pytest.approx(
        (1.5, 0.75, 0.375)
    )
    (decal,) = scene.decals
    assert decal.extents == (2.0, 1.0) and decal.material.opacity == 0.5 and decal.material.diffuse.endswith("_0.dds")


def test_an_hdr_cube_shows_at_an_exposure_and_as_a_cross(qt):
    from tests.test_ddsdecode import dx10, header

    from annotoolbox.viewer.textureview import TextureView, cube_cross

    assert exposed_srgb8(np.array([[[0.25, 1.0, 4.0, 2.0]]], np.float32), 1.0).tolist() == [[[188, 255, 255, 255]]]
    faces = [np.full((2, 2, 4), k, np.uint8) for k in range(6)]
    cross = cube_cross(faces)
    assert cross.shape == (6, 8, 4) and cross[0, 2, 0] == 2 and cross[2, 4, 0] == 0 and cross[2, 0, 0] == 1
    assert cross[0, 0, 3] == 0  # no face there
    half = np.array([0.5, 2.0, 8.0, 1.0], np.float16).tobytes()
    cube = bytearray(header(1, 1, 1) + dx10(10) + half * 6)
    cube[136:140] = (4).to_bytes(4, "little")  # the DX10 header's cube flag
    view = TextureView()
    view.show_texture("sky.dds", bytes(cube))
    assert not view.exposure.isHidden() and not view.cross.isHidden() and view.pixels.shape == (1, 1, 4)
    view.exposure.setValue(-3.0)
    assert (
        view.pixels[0, 0, :3].tolist()
        == exposed_srgb8(np.array([[[0.5, 2, 8, 1]]], np.float32), -3.0)[0, 0, :3].tolist()
    )
    view.cross.setChecked(True)
    assert view.pixels.shape == (3, 4, 4)


def test_a_skin_posed_on_the_gpu_draws_as_the_cpu_poses_it(game):
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import ModelCamera, ModelRenderer, ViewState

    scene = modelscene.build(game, PROP, "anno117")
    state = ViewState(time=0.7, shadows=False, ground=False, animation={"Models/0": ("data/anim/crane_work.rdm", 0.0)})
    pictures = []
    for gpu in (False, True):
        renderer = ModelRenderer(ctx, ModelFiles(game))
        renderer.gpu_skin = gpu
        renderer.show(scene)
        fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
        camera = ModelCamera((0, 0, 0), 5.0)
        for _ in range(500):
            renderer.render(fbo, (96, 64), camera, state)
            if not renderer.busy() and renderer.box is not None and len(renderer.items) == len(scene.parts):
                break
            time.sleep(0.01)
        camera.frame(*renderer.box)
        renderer.render(fbo, (96, 64), camera, state)
        pictures.append(np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3).astype(int))
        if gpu:  # the same view at rest: the pose moved something
            renderer.render(fbo, (96, 64), camera, ViewState(shadows=False, ground=False))
            rest = np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3).astype(int)
            assert np.abs(rest - pictures[-1]).mean() > 0.5 and pictures[-1].std() > 1.0
        crane = renderer.items[0]
        assert all(item["gpu"] == gpu for item in crane if item["skinned"] is not None)
        renderer.release()
    assert np.abs(pictures[0] - pictures[1]).mean() < 1.0  # the same pose either way


def test_particles_and_decals_test_the_depth_but_never_write_it(game):
    """moderngl keeps depth writes per framebuffer (its context has no switch): a sprite that wrote its quad's depth
    hid the sprites drawn after it behind a square (2205's air buoy)."""
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from annotoolbox.viewer.modelrender import ModelCamera, ModelRenderer, ViewState

    renderer = ModelRenderer(ctx, ModelFiles(game))
    renderer.show(modelscene.build(game, PROP, "anno117"))
    masks = []
    renderer._draw_particles = lambda *args: masks.append(renderer._targets["msaa"].depth_mask)
    fbo = ctx.framebuffer([ctx.renderbuffer((32, 32), 4)], ctx.depth_renderbuffer((32, 32)))
    renderer.render(fbo, (32, 32), ModelCamera((0, 0, 0), 5.0), ViewState())
    assert masks == [False] and renderer._targets["msaa"].depth_mask
    renderer.release()


def test_a_root_the_clip_does_not_name_stands_at_the_origin_and_other_joints_keep_their_bind():
    joints = [Joint("Bip01", (0, -1, 0), (0, 0, 0, 1), None, (0, 0, 0), (0, 0, 0)),
              Joint("Bip01 Pelvis", (0, -1, 0), (0, 0, 0, 1), 0, (0, 0, 0), (0, 0, 0)),
              Joint("Bip01 Spine", (0, -1.5, 0), (0, 0, 0, 1), 1, (0, 0, 0), (0, 0, 0))]  # fmt: skip
    rig = skinning.Rig(Skeleton(joints, 0))
    assert np.allclose(rig.world(None, 0.0)[0][:3, 3], (0, 1, 0))  # the bind pose: the root at the hip's height
    clip = Animation("kid", 1000, 30, [Track("Bip01 Pelvis", [Keyframe((0, 0, 0, 1), (0, 0.9, 0), 0.0)])])
    world = rig.world(clip, 0.0)
    assert np.allclose(world[0][:3, 3], 0) and np.allclose(world[1][:3, 3], (0, 0.9, 0))
    assert np.allclose(world[2][:3, 3], (0, 1.4, 0))  # the spine keeps its bind offset above the pelvis


def test_the_view_switches_come_back_on_and_the_hardware_entry_flips(qt, game, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(game)
    shadows = window.toggles["shadows"]
    shadows.trigger()
    assert window.model.state.shadows is False
    shadows.trigger()
    assert window.model.state.shadows is True  # the slot got the checked state, not a default False
    window.toggles["night"].trigger()
    assert window.model.state.night is True
    window.rendering.rebuild()
    hardware = next(a for a in window.rendering.menu.actions() if a.text().startswith("&Hardware"))
    was = window.settings.hardware
    hardware.trigger()
    assert window.settings.hardware is (not was)
    window.close()


def test_condition_bits_are_held_by_hand_from_the_view_menu(qt, game, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(game)
    window._show_model(modelscene.build(game, PROP, "anno117"))
    window.bits_menu.aboutToShow.emit()
    texts = [a.text() for a in window.bits_menu.actions() if a.text()]
    assert texts[:3] == ["Bit 2: DestructionTransform", "Bit 3: Selected", "Bit 4: Unselected"]
    next(a for a in window.bits_menu.actions() if a.text() == "Bit 3: Selected").trigger()
    assert window.held == 8 and window.model.state.states.bits & 8
    window.bits_menu.aboutToShow.emit()
    assert next(a for a in window.bits_menu.actions() if a.text() == "Bit 3: Selected").isChecked()
    next(a for a in window.bits_menu.actions() if a.text() == "&Release all").trigger()
    assert window.held == 0 and not window.model.state.states.bits
    window.close()


def test_a_model_config_opens_in_the_object_builder_from_the_files_menu(qt, game, tmp_path):
    from annotoolbox.suite import links
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    alone = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    assert not alone.edit_action.isVisible()  # no object builder beside it: no entry
    alone.close()
    asked = []
    links.provide(links.EDIT_OBJECT, lambda g, p: asked.append((g, p)) or True)
    try:
        window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
        window.set_tree(game)
        assert window.edit_action.isVisible()
        window.current = "data/a/b.fbx"
        window.edit_action.trigger()
        assert not asked  # a mesh is no object
        window.current = "data/a/b.cfg"
        window.edit_action.trigger()
        assert asked == [(game.game, "data/a/b.cfg")]
        window.close()
    finally:
        links.withdraw(links.EDIT_OBJECT)


def test_imported_models_are_kept_whole_in_the_project_and_go_into_the_mod(tmp_path):
    from annotoolbox.suite.project import Project
    from annotoolbox.viewer import projectpart

    project = Project(tmp_path / "proj", mod_id="my-mod")
    assert not projectpart.PART.has_work(project)
    projectpart.EDITS.save_files(project, "anno117", {"/data/a/x.rdm": b"rdm"})
    assert projectpart.EDITS.saved_files(project)["data/a/x.rdm"].read_bytes() == b"rdm"
    assert projectpart.PART.has_work(project)
    assert projectpart.PART.render(project, None, lambda _m: None) == {"data/a/x.rdm": b"rdm"}
    with pytest.raises(ValueError):
        projectpart.EDITS.save_files(project, "anno117", {"../x.rdm": b"no"})


def test_the_plain_look_fades_every_games_lights_by_one_falloff():
    from annotoolbox.viewer import plainshading

    lamp = modelscene.PlacedLight("l", np.identity(4), (1.0, 0.5, 0.25), 8.0, 2.0)
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
    from annotoolbox.viewer.modelrender import ModelRenderer, ViewState

    def part(node: str, when=()):
        return N(node=node, when=when, times=())

    failed = Future()
    failed.set_result(OSError("no mesh"))
    scene = N(parts=[part("house"), part("water", (1 << 3,)), part("broken")])
    r = ModelRenderer.__new__(ModelRenderer)
    r.scene, r.items, r.loader, r.box = scene, {0: []}, N(futures={"mesh:2": failed}, pending=lambda: 0), (0, 1)
    state = ViewState()
    assert r.shown_parts_in(state)  # the water is hidden by its bit, the broken part failed
    del r.loader.futures["mesh:2"]
    assert not r.shown_parts_in(state)  # the broken part's mesh is still asked for
    r.loader.futures["mesh:2"] = failed
    r.items = {}
    assert not r.shown_parts_in(state)  # the house is not in yet

    r.items, framed, drawn = {0: []}, [], []

    class View:
        def __init__(self, backend):
            self.renderer, self.failed, self.ctx = None, "", None

        def show_scene(self, scene, files):
            self.renderer = r

        def picture(self, w, h):
            drawn.append(len(framed))
            return ("picture", len(framed))

        def frame_model(self):
            framed.append(True)

        release = close = deleteLater = lambda self: None

    monkeypatch.setattr(modelview, "RhiModelView", View)
    loads = iter([True, False])  # a file comes in on the first frame: that frame is not quiet
    r.busy, r.changed = lambda: 0, lambda: next(loads, False)
    start = time.monotonic()
    image = modelview.rhi_picture(None, scene, state, (8, 8), "d3d11", wait=30.0)
    assert framed == [True] and image == ("picture", 1) and time.monotonic() - start < 5.0
    assert drawn == [0, 0, 1, 1]  # a frame loading, the framing frame, then two quiet ones
