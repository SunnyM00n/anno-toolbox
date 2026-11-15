"""Particle effects in the editor's 3D view: an effect file's sprites at a time, their quads, and how a model
config hands them to the view (synthetic effects only)."""

from __future__ import annotations

import io
import time
from types import SimpleNamespace

import numpy as np
import pytest

from annotoolbox.editor import modelparts
from annotoolbox.formats import rdp117
from annotoolbox.formats.rdp117 import BakedParticle, RdpMaterial
from annotoolbox.models import modelread
from annotoolbox.render import camera, cull, particledraw, particlefx
from annotoolbox.render.modellibrary import ModelLibrary
from annotoolbox.render.particledraw import Placed

PEP = "<PEPValueMin>0</PEPValueMin><PEPValueMax>1000</PEPValueMax><PEPValueDef>100</PEPValueDef><PEPVarianz>0</PEPVarianz><PEPSize>0</PEPSize>"
EFC = (
    "<Effect0><Texture0>data/fx/maps/fx.png</Texture0><Name>steam</Name><EffectType>1</EffectType>"
    "<Technique>1</Technique><Delay>0</Delay><LifeTime>1000</LifeTime><Duration>1000</Duration><Loop>1</Loop>"
    f"<Bound>1</Bound><OffsetX>0</OffsetX><OffsetY>0.5</OffsetY><OffsetZ>0</OffsetZ>{PEP}"
    "<AtlasTilingU>0.5</AtlasTilingU><AtlasTilingV>0.25</AtlasTilingV><AtlasOffsetU>0.25</AtlasOffsetU>"
    "<AtlasOffsetV>0</AtlasOffsetV><Alignment>0</Alignment></Effect0>"
    "<Effect1><Texture0>data/fx/maps/fx.png</Texture0><Name>mesh</Name><EffectType>2</EffectType><LifeTime>500</LifeTime>"
    "<Loop>1</Loop></Effect1>"
)


def particle(start: int, ticks: int, frames: int = 10, rise: float = 1.0) -> BakedParticle:
    """A particle that moves ``rise`` metres along x each tick, alive from frame ``start`` for ``ticks`` ticks."""
    return BakedParticle(
        start, (start + ticks - 1) % frames, 0,
        [(rise * k, 0.0, 0.0) for k in range(ticks)], [(1.0, 1.0, 1.0)] * ticks,
        [(0.0, 0.0, 0.0)] * ticks, [(1.0, 1.0, 1.0, 1.0)] * ticks,
    )  # fmt: skip


def strip(*parts: BakedParticle, additive: bool = False, looped: bool = True, name: str = "t") -> particlefx.Effect:
    material = RdpMaterial(additive=additive, crop=(0.25, 0.0, 0.5, 0.25))
    return particlefx.Effect.from_particles(name, list(parts), [material], "data/fx/maps/fx_0.dds", 10, 100.0, looped)


# The strip


def test_a_particle_is_alive_from_its_first_to_its_last_tick_and_wraps_past_the_end():
    fx = strip(particle(2, 4), particle(8, 4))  # frames 2..5, and 8, 9, 0, 1
    alive = [len(fx.at(frame * 0.1 + 0.001)) for frame in range(10)]
    assert alive == [1, 1, 1, 1, 1, 1, 0, 0, 1, 1]
    assert len(fx.at(10.301)) == len(fx.at(0.301))  # the strip repeats
    assert len(fx.at(0.15, speed=2.0)) == len(fx.at(0.3))  # played twice as fast


def test_a_particle_fades_in_and_out_of_nothing_and_ticks_are_blended():
    fx = strip(particle(2, 4))
    assert fx.at(0.2005).color[0, 3] == pytest.approx(0, abs=0.01)  # the first tick is blank
    assert fx.at(0.3005).color[0, 3] == pytest.approx(1, abs=0.01)
    assert fx.at(0.5005).color[0, 3] == pytest.approx(0, abs=0.01)  # and so is the last
    assert fx.at(0.35).position[0, 0] == pytest.approx(1.5, abs=0.01)  # half way from tick 1 to tick 2


def test_an_empty_strip_has_nothing_alive():
    assert len(strip().at(1.0)) == 0 and strip().seconds == pytest.approx(1.0)


# The files


def test_a_baked_file_reads_back_as_it_was_baked():
    parts = [particle(1, 5, rise=0.5), particle(4, 4, rise=0.25)]
    data = rdp117.write_rdp(
        rdp117.quantise(parts, [RdpMaterial(additive=True, crop=(0, 0, 0.5, 0.5))], "data/fx/maps/fx.psd", 10, True)
    )
    fx = particlefx.load_rdp(data, "x.rdp")
    assert fx.texture == "data/fx/maps/fx_0.dds" and fx.looped and fx.materials[0].additive  # the file the game loads
    assert fx.frame_count == 10 and fx.frame_ms == pytest.approx(rdp117.FRAME_TIME_MS) and len(fx.start) == 2
    sprites = fx.at(5.01 * rdp117.FRAME_TIME_MS / 1000)  # frame 5: one at its last tick, the other at its second
    assert sorted(sprites.position[:, 0].round(2)) == [0.25, 2.0]
    assert sprites.size[0, 0] == pytest.approx(1.0, abs=0.01)
    texture_ref = "data\\fx\\maps\\fx.dds"
    assert rdp117.texture_file(texture_ref) == texture_ref  # a .dds is loaded as it is


def test_a_vanilla_files_materials_name_their_own_textures():
    parts = [particle(1, 4)]
    data = rdp117.write_rdp(rdp117.quantise(parts, [RdpMaterial()], "data/fx/maps/head.psd", 10, True))
    data = data.replace(b"<TexturePath>data/fx/maps/head.psd</>\r\n", b"")  # as shipped: no header texture ...
    movie = rb"<MovieTexPath>data\fx\maps\sheet.psd</>" + b"\r\n<MovieFPS>12.0</>\r\n<Additive>"
    fx = particlefx.load_rdp(data.replace(b"<Additive>", movie))
    assert fx.texture == "" and fx.texture_of(0) == "data/fx/maps/sheet_0.dds"  # ... a texture per material
    assert fx.materials[0].movie_fps == 12.0 and fx.materials[0].movie_texture.endswith("sheet.psd")


def test_a_2070_effect_file_is_simulated_per_emitter_and_what_is_not_a_sprite_is_listed():
    effects, left = particlefx.load_efc(EFC, "data/fx/steam.efc", lambda name: "data/fx/maps/fx_0.dds")
    assert len(effects) == 1 and effects[0].looped and effects[0].materials[0].additive
    assert effects[0].texture == "data/fx/maps/fx_0.dds" and effects[0].materials[0].crop == (0.25, 0.0, 0.25, 0.25)
    assert len(left) == 1 and "data/fx/steam.efc #1" in left[0]
    assert len(effects[0].at(3.0)) > 5  # a steady stream, not one puff
    again, _ = particlefx.load_efc(EFC, "data/fx/steam.efc", lambda name: name)
    assert np.array_equal(again[0].position, effects[0].position)  # the same effect every time


# The quads


def test_a_fixed_quad_is_turned_as_the_game_builds_it():
    right, up = particlefx.quad_axes(np.zeros((1, 3), "f4"))
    assert np.allclose(right, [[1, 0, 0]]) and np.allclose(up, [[0, 0, 1]])  # width along x, height along z
    right, up = particlefx.quad_axes(np.array([[0.0, np.pi / 2, 0.0]], "f4"))  # a turn about y, negated
    assert np.allclose(right, [[0, 0, 1]], atol=1e-6) and np.allclose(up, [[-1, 0, 0]], atol=1e-6)


def test_the_colour_lift_of_a_material_is_the_games():
    assert particledraw.lift(0) == 1.0 and particledraw.lift(13) == pytest.approx(1.8, abs=0.02)


def test_a_flipbook_shows_the_tile_its_particles_age_is_at():
    tiles = particledraw.sheet_tiles((0.0, 0.0, 1.0, 1.0), (8, 8, 64), np.array([0.0, 0.5, 1.0]))
    assert tiles.tolist() == [[0, 0, 0.125, 0.125], [0, 0.5, 0.125, 0.125], [0.875, 0.875, 0.125, 0.125]]
    half = particledraw.sheet_tiles((0.5, 0.0, 0.5, 1.0), (2, 1, 2), np.array([0.9]))  # laid over a crop
    assert half.tolist() == [[0.75, 0.0, 0.25, 1.0]]


def test_a_movie_texture_wider_than_high_is_a_strip_of_square_frames():
    fx = strip(particle(2, 4))
    fx.materials[0].movie_texture = "data/fx/maps/strip.psd"
    fx.material_textures = ["data/fx/maps/strip_0.dds"]
    textures = {"fx:data/fx/maps/strip_0.dds": (1024, 256, 4, b"")}
    library = SimpleNamespace(texture=textures.get)
    layer = particledraw.ParticleLayer(None, library, lambda: [], lambda key, srgb: None)
    assert layer._flipbook(_placed(), [fx]).sheet == (4, 1, 4)
    assert layer._flipbook(_placed(sheet=(8, 8, 64)), [fx]).sheet == (8, 8, 64)  # a config's own sheet stays
    textures["fx:data/fx/maps/strip_0.dds"] = (256, 256, 4, b"")
    assert layer._flipbook(_placed(), [fx]).sheet is None


def _placed(file="a.rdp", **kw) -> Placed:
    return Placed(file, np.identity(4, "f4"), **kw)


def _where(x: float, scale: float = 1.0) -> np.ndarray:
    m = np.identity(4, "f4") * scale
    m[3, 3], m[0, 3] = 1.0, x
    return m


def test_a_quad_stands_where_the_model_does_at_the_models_scale():
    fx = strip(particle(2, 4, rise=0.0))
    rows = particledraw.collect([([fx], _placed(), np.stack([_where(5.0, 2.0)]))], 0.3005, np.zeros(3))
    ((key, quads),) = rows.items()
    assert key == ("data/fx/maps/fx_0.dds", False) and quads.shape == (1, particledraw.FLOATS)
    assert quads[0, 0:3] == pytest.approx([5, 0, 0]) and quads[0, 9:11] == pytest.approx(
        [2, 2]
    )  # centre, width, height
    assert quads[0, 15:19] == pytest.approx([0.25, 0, 0.5, 0.25])  # the atlas tile


def test_additive_effects_are_grouped_apart_and_blended_ones_drawn_far_to_near():
    near, far = _where(1.0), _where(9.0)
    blended = strip(particle(2, 4, rise=0.0))
    glow = strip(particle(2, 4, rise=0.0), additive=True)
    groups = particledraw.collect([([blended, glow], _placed(), np.stack([near, far]))], 0.3005, np.zeros(3))
    assert set(groups) == {("data/fx/maps/fx_0.dds", False), ("data/fx/maps/fx_0.dds", True)}
    assert groups[("data/fx/maps/fx_0.dds", False)][:, 0].tolist() == [9.0, 1.0]  # the far one first


def test_a_frame_has_a_budget_and_a_one_shot_waits_for_its_start():
    fx = strip(particle(2, 4, rise=0.0))
    mats = np.stack([_where(float(x)) for x in range(8)])
    rows = particledraw.collect([([fx], _placed(), mats)], 0.3005, np.zeros(3), budget=3)
    assert sum(len(q) for q in rows.values()) == 3 and rows[("data/fx/maps/fx_0.dds", False)][:, 0].max() == 2
    once = strip(particle(2, 4, rise=0.0), looped=False)
    assert particledraw.collect([([once], _placed(started=False), mats)], 0.3005, np.zeros(3)) == {}
    assert particledraw.collect([([once], _placed(started=True), mats)], 0.3005, np.zeros(3))


def test_only_the_instances_near_and_in_view_are_picked_nearest_first():
    mats = np.stack([_where(30.0), _where(10.0), _where(900.0), _where(20.0, scale=1e-9)])
    eye = np.zeros(3, "f4")
    assert particledraw.pick_instances(mats, eye, None).tolist() == [1, 0]  # far and hidden ones are out
    cam = camera.OrbitCamera((10.0, 0.0, 0.0), 4.0)
    planes = cull.frustum_planes(cam.proj(1.5) @ cam.view())
    behind = np.identity(4, "f4")
    behind[:3, 3] = cam.eye() + (cam.eye() - cam.target) * 25  # in range, at the camera's back
    mats = np.stack([behind, _where(10.0)])
    assert particledraw.pick_instances(mats, cam.eye(), planes).tolist() == [1]


# What a config hands over


PARTICLE_CFG = """<Config><ConfigType>MAIN</ConfigType><Particles>
<Config><ConfigType>PARTICLE</ConfigType><Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType>
<Position.x>1</Position.x><Position.y>2</Position.y><Position.z>3</Position.z><Scale>2</Scale></Config></Transformer>
<FileName>data\\fx\\steam.rdp</FileName><TimeScale>2</TimeScale><Alpha>0.5</Alpha>
<Color><x>1</x><y>0.5</y><z>0.25</z></Color></Config>
<Config><ConfigType>PARTICLE</ConfigType><Transformer><Config><ConfigType>VISIBILITY_TRANSFORM</ConfigType>
<Conditions>2</Conditions></Config></Transformer><FileName>data/fx/burning.rdp</FileName></Config>
<Config><ConfigType>PARTICLE</ConfigType><Transformer><Config><ConfigType>BONELINK_TRANSFORM</ConfigType>
<BoneHash>7</BoneHash></Config></Transformer><FileName>data/fx/sparks.rdp</FileName></Config>
</Particles></Config>"""


def test_a_117_config_places_its_effects_except_those_of_a_state_or_a_bone():
    (fx,) = modelparts.model_parts(PARTICLE_CFG)["effects"]
    assert fx["file"] == "data/fx/steam.rdp" and fx["position"] == [1, 2, 3] and fx["scale"] == [2.0] * 3
    assert fx["speed"] == 0.5 and fx["alpha"] == 0.5 and fx["color"] == [1.0, 0.5, 0.25]  # a time scale of 2 is slow


CFG_2070 = r"""<m_Config><m_Effects>
<m_Config><m_FileName>data\fx\steam.efc</m_FileName><m_ConfigType>EFFECT</m_ConfigType></m_Config>
<m_Config><m_FileName>data\fx\sparks.efc</m_FileName><m_ConfigType>EFFECT</m_ConfigType>
<m_Transformer><m_Config><m_ConfigType>BONELINK_TRANSFORM</m_ConfigType></m_Config></m_Transformer></m_Config>
<m_Config><m_FileName>data\fx\fire.efc</m_FileName><m_ConfigType>EFFECT</m_ConfigType>
<m_Transformer><m_Config><m_StartDamage>200</m_StartDamage><m_ConfigType>DAMAGE_TRANSFORM</m_ConfigType></m_Config>
</m_Transformer></m_Config></m_Effects>
<m_Files><m_Config><m_FileName>data\fx\flame.cfg</m_FileName><m_ConfigType>FILE</m_ConfigType>
<m_Transformer><m_Config><m_StartDamage>200</m_StartDamage><m_ConfigType>DAMAGE_TRANSFORM</m_ConfigType></m_Config>
</m_Transformer></m_Config>
<m_Config><m_FileName>data\fx\lamp.cfg</m_FileName><m_ConfigType>FILE</m_ConfigType></m_Config></m_Files>
<m_Sequences><m_Config><m_SequenceID>1</m_SequenceID><m_Looped>1</m_Looped><m_Speed>1</m_Speed>
<Track><TrackID>1</TrackID><TrackElement><Type>1</Type><m_StartTime>0</m_StartTime><m_EndTime>500</m_EndTime>
<m_EffectID>0</m_EffectID></TrackElement></Track><m_ConfigType>SEQUENCE</m_ConfigType></m_Config></m_Sequences>
<m_ConfigType>ROOT</m_ConfigType></m_Config>"""


def test_a_2070_config_places_the_effects_of_its_rest_pose():
    model = modelread.read_config(CFG_2070, lambda name: "", "data/x.cfg")
    assert [(e.file, e.started) for e in model.effects] == [("data/fx/steam.efc", True)]  # a sequence starts it
    assert [path for path, _ in model.files] == ["data/fx/lamp.cfg"]  # the burning flames are for a damaged object


# The library and the layer


def _png(width: int = 4) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (width, width), (255, 255, 255, 255)).save(out, "PNG")
    return out.getvalue()


class _Editor:
    """What the library asks of an editor: a model with an effect, an effect file and its texture."""

    def __init__(self) -> None:
        self.read: list[str] = []

    def model(self, cfg: str) -> dict:
        if cfg == "outer.cfg":  # a model that nests one with an effect, 2 m to the side
            nested = {"cfg": "inner.cfg", "position": [2, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}
            return {"parts": [], "files": [nested], "effects": []}
        effect = {"file": "a.rdp", "position": [0, 1, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1], "speed": 2.0}
        return {"parts": [], "files": [], "effects": [effect]}

    def effect(self, path: str) -> list:
        self.read.append(path)
        return [strip(particle(2, 4, rise=0.0))]

    def effect_texture_png(self, path: str) -> bytes:
        return _png()


def _wait(done, seconds: float = 5.0) -> None:
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        time.sleep(0.01)
    assert done()


def test_the_library_lists_a_models_effects_and_loads_their_files_only_when_asked():
    editor = _Editor()
    library = ModelLibrary(editor, workers=2)
    try:
        library.request(["outer.cfg"])
        _wait(lambda: library.parts("outer.cfg") is not None and library.placed_effects("outer.cfg"))
        (placed,) = library.placed_effects("outer.cfg")
        assert placed.file == "a.rdp" and placed.speed == 2.0 and placed.matrix[:3, 3].tolist() == [2, 1, 0]
        assert editor.read == [] and library.effect("a.rdp") is None  # nothing is read until the view asks
        library.request_effects("outer.cfg")
        _wait(lambda: library.effect("a.rdp") and not library.effects_pending())
        assert editor.read == ["a.rdp"] and library.texture("fx:data/fx/maps/fx_0.dds")[:2] == (4, 4)
        library.request_effects("outer.cfg")
        assert editor.read == ["a.rdp"]  # a file is read once
    finally:
        library.close()


def _context():
    moderngl = pytest.importorskip("moderngl")
    try:
        return moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")


def _batch(cfg: str, *at: float, layer: str = "objects") -> SimpleNamespace:
    mats = np.stack([_where(x) for x in at])
    return SimpleNamespace(key=(layer, cfg), count=len(at), cells=cull.InstanceCells(mats))


class _Library:
    def __init__(self, fx: particlefx.Effect) -> None:
        self.fx = fx

    def request_effects(self, cfg: str) -> None:
        pass

    def placed_effects(self, cfg: str) -> list[Placed]:
        return [Placed("a.rdp", np.identity(4, "f4"))] if cfg == "fire.cfg" else []

    def effect(self, path: str) -> list:
        return [self.fx]

    def effects_pending(self) -> int:
        return 0


def test_the_layer_draws_glowing_quads_where_the_models_stand():
    ctx = _context()
    w, h = 160, 100
    fbo = ctx.framebuffer([ctx.renderbuffer((w, h), 4, dtype="f4")], ctx.depth_renderbuffer((w, h)))
    white = ctx.texture((2, 2), 4, bytes([255] * 16))
    fx = strip(particle(2, 4, rise=0.0), additive=True)
    fx.materials[0].crop = (0.0, 0.0, 1.0, 1.0)
    batches = [_batch("fire.cfg", 10.0), _batch("rock.cfg", 12.0), _batch("fire.cfg", 11.0, layer="grass")]
    layer = particledraw.ParticleLayer(ctx, _Library(fx), lambda: batches, lambda key, srgb: white)
    cam = camera.OrbitCamera((10.0, 0.0, 0.0), 10.0)
    view_proj = cam.matrix(w / h)

    def frame(now: float = 1.0, **options):
        fbo.use()
        fbo.clear(0, 0, 0, 1)
        opts = SimpleNamespace(particles=True, layers={}, **{"particle_time": 0.3005, **options})
        moving = layer.draw(opts, cam, view_proj, None, now)
        return moving, np.frombuffer(fbo.read(components=4, dtype="f4"), "f4").reshape(h, w, 4)

    moving, img = frame()
    assert not moving and layer.quads == 1  # the grass and the model without effects carry none
    assert img[h // 2, w // 2, 0] > 0.5 and img[2, 2, 0] == 0  # a glow at the pivot, nothing in the corner
    moving, _ = frame(now=0.3005, particle_time=None)
    assert moving  # a live clock keeps the view drawing
    layer.release()
    assert not particledraw.ParticleLayer(ctx, None, lambda: [], lambda key, srgb: None).draw(
        SimpleNamespace(particles=True), cam, view_proj, None, 0.0
    )  # no models, no library: nothing to draw
