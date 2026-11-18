"""An object's states in the viewer (``viewer.states``, ``viewer.motion``): a sequence's condition tracks switch the
parts, effects, lights and decals a ``VISIBILITY_TRANSFORM`` shows and run the conditioned spins, a
``DAYNIGHT_TRANSFORM``'s time slice follows the view's night, bits are held by hand; an ``ANIMATION_TRANSFORM``'s
position motion and alignment (docs/engine-animation.md "Spinning parts", "Time of day ...")."""

from __future__ import annotations

import math

import numpy as np
import pytest

from annotoolbox.models import configtree
from annotoolbox.models.configtree import Sequence, TrackElement
from annotoolbox.viewer import motion, states
from annotoolbox.viewer.states import States

COMMON01, COMMON02 = 32, 64


def _sequence(looped: bool = True) -> Sequence:
    """4 s long: Common01 on from 1 s to 3 s, Common02 from 2 s to 4 s."""
    track = [TrackElement(2, 1000, 3000, condition=COMMON01), TrackElement(2, 2000, 4000, condition=COMMON02)]
    return Sequence("Sequences/0", 1, looped, 1.0, [track])


def test_the_condition_tracks_hold_their_bits_in_their_windows():
    seq = _sequence()
    assert States.of(seq, 0.5).bits == 0
    assert States.of(seq, 1.5).bits == COMMON01
    assert States.of(seq, 2.5).bits == COMMON01 | COMMON02
    assert States.of(seq, 5.5).bits == COMMON01  # looped: 1.5 s into the second pass
    assert States.of(None, 2.0, held=COMMON02).bits == COMMON02


def test_a_part_shows_while_each_of_its_masks_has_a_bit_on():
    at = States.of(_sequence(), 1.5)
    assert at.shown((COMMON01,)) and at.shown((COMMON01 | COMMON02,)) and not at.shown((COMMON02,))
    assert not at.shown((COMMON01, COMMON02))  # a part in a nested file shown under another mask


def test_a_conditioned_motion_runs_only_while_its_bits_are_on():
    seq = _sequence()
    assert States.of(seq, 2.5).on_seconds(COMMON01) == pytest.approx(1.5)
    assert States.of(seq, 6.0).on_seconds(COMMON01) == pytest.approx(3.0)  # 2 s in the first pass, 1 s now
    assert States.of(seq, 3.5).on_seconds(COMMON01 | COMMON02) == pytest.approx(2.5)  # the union: 1 s .. 3.5 s
    assert States.of(seq, 3.5).on_seconds(0) == 3.5
    spin = motion.Spin((0.0, 1.0, 0.0), 1.0, when=COMMON01)
    m = motion.at_time((spin,), 2.5, lambda _l: None, motion.Env(States.of(seq, 2.5)))
    assert np.allclose(m, spin.matrix(1.5))
    held = States.of(seq, 2.5, held=COMMON02)  # a bit held by hand counts all along
    assert held.bits == COMMON01 | COMMON02 and held.on_seconds(COMMON02) == 2.5
    assert held.on_seconds(COMMON01) == pytest.approx(1.5)  # the track's window alone


def _listed_on_seconds(seq: Sequence, seconds: float, mask: int) -> float:
    """The time ``mask`` was on, from every window of every pass since the start: the slow way, to compare with."""
    length = seq.duration_ms / 1000 / seq.speed
    passes = int(seconds // length) if seq.looped else 0
    spans = sorted(
        (e.start_ms / 1000 / seq.speed + k * length, min(e.end_ms / 1000 / seq.speed + k * length, seconds))
        for k in range(passes + 1)
        for track in seq.tracks
        for e in track
        if e.condition & mask
    )
    total, reach = 0.0, float("-inf")
    for a, b in spans:
        a = max(a, reach)
        if b > a:
            total += b - a
            reach = b
    return total


@pytest.mark.parametrize("looped", [True, False])
@pytest.mark.parametrize("ends_ms", [(3000, 4000), (5500, 4000), (13000, 2500)])  # windows that outlast their pass too
def test_a_loop_left_open_costs_no_more_than_one_pass_and_counts_the_same(looped, ends_ms):
    track = [
        TrackElement(2, 1000, ends_ms[0], condition=COMMON01),
        TrackElement(2, 2000, ends_ms[1], condition=COMMON02),
    ]
    seq = Sequence("Sequences/0", 1, looped, 2.0, [track])
    for seconds in (0.0, 0.7, 1.9, 2.0, 3.3, 6.1, 9.99, 41.3):
        for mask in (COMMON01, COMMON02, COMMON01 | COMMON02):
            expected = _listed_on_seconds(seq, seconds, mask)
            assert States.of(seq, seconds).on_seconds(mask) == pytest.approx(expected), (seconds, mask)
    assert len(States.of(seq, 36000.0).windows) == 2  # hours later: still the two windows of one pass


def test_the_position_motion_is_the_engines_sine_in_the_parents_space():
    bob = motion.Bob((1.0, 2.0, 3.0), 2.0, False, np.identity(4))
    for t in (0.0, 0.3, 1.1):
        phase = 2.0 * t
        expected = [math.sin(phase), 2 * math.cos(phase), -3 * math.sin(phase)]
        assert np.allclose(bob.matrix(t)[:3, 3], expected)
    local = np.identity(4)
    local[:3, :3] = motion.turn((0.0, 1.0, 0.0), math.pi / 2)[:3, :3]
    local[:3, 3] = (5.0, 0.0, 0.0)
    bob = motion.Bob((1.0, 0.0, 0.0), 1.0, False, local)
    m = motion.at_time((local, bob), math.pi / 2, lambda _l: None)
    assert np.allclose(m[:3, 3], (6.0, 0.0, 0.0))  # +1 along the parent's x, whatever the node's own turn


def test_alignment_turns_the_parts_x_to_the_wind_and_keeps_its_tilt():
    tilt = motion.turn((1.0, 0.0, 0.0), 0.3)
    align = motion.Align(2, tilt)
    m = motion.at_time((tilt, align), 0.0, lambda _l: None, motion.Env(wind=(0.0, 1.0)))
    assert np.allclose(m[:3, 0], (0.0, 0.0, 1.0), atol=1e-9)  # +x along the wind (+z)
    assert m[:3, 1] @ (0.0, 1.0, 0.0) == pytest.approx(math.cos(0.3))  # the tilt about its own x kept


def test_alignment_4_stands_upright_facing_the_camera():
    back = np.array([0.6, 0.5, 0.62])
    back /= np.linalg.norm(back)
    up = np.cross(back, np.cross((0.0, 1.0, 0.0), back))
    cam = np.stack([np.cross(up, back), up / np.linalg.norm(up), back], 1)
    m = motion.at_time((np.identity(4), motion.Align(4, np.identity(4))), 0.0, lambda _l: None, motion.Env(camera=cam))
    assert np.allclose(m[:3, 1], (0.0, 1.0, 0.0)) and abs(m[1, 2]) < 1e-9  # upright, turned to the camera


def _transform(kind: str, **fields) -> str:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return f"<Transformer><Config><ConfigType>{kind}</ConfigType>{body}</Config></Transformer>"


STATES_CFG = f"""<Config><ConfigType>MAIN</ConfigType>
<Files><Config><ConfigType>FILE</ConfigType><FileName>data/x/torch.cfg</FileName>
{_transform("DAYNIGHT_TRANSFORM", TimeSlice=1, Conditions=0)}</Config></Files>
<Lights><Config><ConfigType>LIGHT</ConfigType>{_transform("VISIBILITY_TRANSFORM", Conditions=COMMON01)}<Range>3</Range>
</Config><Config><ConfigType>LIGHT</ConfigType>
{_transform("DAYNIGHT_TRANSFORM", TimeSlice=2, Conditions=COMMON02, FadeTime=2)}<Range>3</Range></Config></Lights>
<Particles><Config><ConfigType>PARTICLE</ConfigType><FileName>data/x/smoke.rdp</FileName>
{_transform("VISIBILITY_TRANSFORM", Conditions=COMMON02)}</Config></Particles>
<Decals><Config><ConfigType>DECAL</ConfigType><Extents.x>1</Extents.x><Extents.z>1</Extents.z>
{_transform("DAYNIGHT_TRANSFORM", TimeSlice=4)}<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>2</ShaderID>
<cDiffuseOpacity>0</cDiffuseOpacity></Config></Materials></Config></Decals></Config>"""
TORCH_CFG = f"""<Config><ConfigType>MAIN</ConfigType><Lights><Config><ConfigType>LIGHT</ConfigType>
{_transform("VISIBILITY_TRANSFORM", Conditions=COMMON01)}<Range>2</Range></Config></Lights></Config>"""


@pytest.fixture
def scene(tmp_path):
    from annotoolbox.sources import gametree
    from annotoolbox.viewer import modelscene

    for rel, text in {"data/x/x.cfg": STATES_CFG, "data/x/torch.cfg": TORCH_CFG, "data/x/smoke.rdp": "-"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return modelscene.build(gametree.loose_tree(tmp_path), "data/x/x.cfg", "anno117")


def test_effects_lights_and_decals_carry_their_states_and_their_nested_files(scene):
    masked, lamp, torch = scene.lights
    assert masked.when == (COMMON01,) and lamp.times == ((2, COMMON02, 2.0),)
    assert torch.node.startswith("Files/0/") and torch.when == (COMMON01,) and torch.times == ((1, 0, 0.0),)
    (smoke,) = scene.effects
    (decal,) = scene.decals
    assert smoke.when == (COMMON02,) and decal.times == ((4, 0, 0.0),)
    assert decal.material.opacity == 0  # a terrain decal's cDiffuseOpacity: only its normal map on the ground
    assert states.bits_read(scene) == [2, 3, 4, 5, 6]  # the engine's own three, Common01, Common02


def test_the_view_shows_a_thing_by_its_masks_and_the_time_of_day(scene):
    from annotoolbox.viewer.modelrender import ViewState

    masked, lamp, torch = scene.lights
    (decal,) = scene.decals
    day = ViewState(states=States.of(None, 0.0, held=COMMON01))
    assert day.shows(masked) and not day.shows(torch) and day.shows(decal) and not day.shows(lamp)
    night = ViewState(night=True, states=States.of(None, 0.0, held=COMMON01 | COMMON02))
    assert night.shows(torch) and night.shows(lamp) and not night.shows(decal)
    assert not ViewState(night=True, states=States.of(None, 0.0, held=COMMON01)).shows(lamp)  # lit while working
    assert not ViewState(hidden=frozenset({"Lights"}), states=day.states).shows(masked)


def test_time_slices_pair_up_as_night_and_day():
    on = [states.slice_on(s, states.MIDNIGHT) for s in range(7)]
    assert on == [True, True, True, False, False, False, True]
    assert [states.slice_on(s, states.NOON) for s in range(7)] == [False, False, False, True, True, True, True]
    assert states.bit_name(3, "anno117") == "Selected" and states.bit_name(3, "anno1800") == "3"


@pytest.mark.parametrize(
    ("day_time", "expected"),
    [  # the evening of a symmetric sun (sunset 0.75): the three pairs switch 2.4 hours apart
        (0.64, [False, False, False, True, True, True]),  # the core of the day
        (0.70, [False, False, True, False, True, True]),  # 2: the torches lit before the sunset
        (0.80, [False, True, True, False, False, True]),  # 1: the sun down
        (0.86, [True, True, True, False, False, False]),  # 0: deep night
        (0.20, [False, True, True, False, False, True]),  # the dawn mirrors the dusk
    ],
)
def test_the_three_pairs_of_time_slices_switch_at_the_engines_ramps(day_time, expected):
    """engine-animation "The slices, the fade and the offset": 1/4 at sunrise and sunset, 2/3 2.4 hours inside the
    day, 0/5 2.4 hours outside it; each pair complementary."""
    on = [states.slice_on(s, day_time) for s in range(6)]
    assert on == expected
    assert all(on[s] != on[5 - s] for s in range(3))
    assert states.slice_on(4, 0.7499) and not states.slice_on(4, 0.7499, offset=0.05)  # the offset delays a switch


def test_a_time_slice_fades_over_its_fade_time_after_the_night_switches(scene):
    """The engine's fade towards on or off by the frame's time over ``FadeTime``; the view's switch of Night starts
    it (the light's colour scaled by it), a slice without a fade time switches at once."""
    from annotoolbox.viewer.modelrender import ViewState

    assert states.slice_fade(2, 2.0, True, 0.5) == pytest.approx(0.25)  # lighting at night: a quarter after 0.5 s
    assert states.slice_fade(2, 2.0, False, 0.5) == pytest.approx(0.75)  # going out by day
    assert states.slice_fade(2, 2.0, True, 5.0) == 1.0 and states.slice_fade(2, 0.0, True, 0.0) == 1.0
    assert states.slice_fade(6, 2.0, True, 0.0) == 1.0  # a slice on all day long does not fade
    _masked, lamp, torch = scene.lights
    held = States.of(None, 0.0, held=COMMON01 | COMMON02)
    view = ViewState(night=True, states=held, now=11.0, night_changed=10.0)
    assert view.fade(lamp) == pytest.approx(0.5) and view.shows(lamp) and view.fading(2.0)
    assert view.light_color(lamp) == pytest.approx(tuple(0.5 * c for c in lamp.color_at(0.0)))
    assert view.fade(torch) == 1.0  # no fade time: at once
    out = ViewState(night=False, states=held, now=12.0, night_changed=10.0)
    assert out.fade(lamp) == 0.0 and not out.shows(lamp) and out.fading(2.5)
    assert not out.fading(2.0)  # the scene's longest fade has run
    assert not ViewState(states=held, now=14.0, night_changed=10.0).fading(3.0)


def test_the_clock_alignments_turn_a_part_about_its_own_z_by_the_local_clock():
    """engine-animation "Spinning parts": 5 the hour hand (h x 30 + min x 0.5), 6 the minute, 7 the second hand,
    whole minutes and seconds; the part keeps its own turn and turns after it by -angle about its own +z."""
    clock = (15, 30, 45)
    assert motion.hand_angle(5, clock) == pytest.approx(105.0)  # 3:30: 90 + 15 degrees
    assert motion.hand_angle(6, clock) == 180.0 and motion.hand_angle(7, clock) == 270.0
    assert motion.hand_angle(5, (12, 0, 0)) == 360.0 and motion.hand_angle(5, (0, 0, 0)) == 0.0
    own = motion.turn((0.0, 1.0, 0.0), 0.4)
    own[:3, :3] *= 2.0  # scaled: the hand turns, the scale stays
    m = motion.at_time((own, motion.Align(6, own)), 0.0, lambda _l: None, motion.Env(clock=clock))
    expected = own[:3, :3] / 2.0 @ motion.turn((0.0, 0.0, 1.0), -math.pi)[:3, :3] * 2.0
    assert np.allclose(m[:3, :3], expected)
    still = motion.at_time((own, motion.Align(6, own)), 0.0, lambda _l: None, motion.Env())
    assert np.allclose(still, own)  # no clock (an offscreen picture): 12 o'clock, the part as authored
    assert motion.on_the_clock((own, motion.Align(7, own))) and not motion.on_the_clock((own, motion.Align(2, own)))


def test_a_config_names_the_visibility_mask_and_the_motion():
    cfg = """<Config><Models><Config><ConfigType>MODEL</ConfigType><FileName>a.rdm</FileName>
<Transformer><Config><ConfigType>VISIBILITY_TRANSFORM</ConfigType><Conditions>32</Conditions></Config>
<Config><ConfigType>ANIMATION_TRANSFORM</ConfigType><PositionOffset.y>0.5</PositionOffset.y><PositionSpeed>2</PositionSpeed>
<Alignment>2</Alignment><Conditions>64</Conditions></Config></Transformer></Config></Models></Config>"""
    node = configtree.read_tree(cfg).section_of("Models")[0]
    assert configtree.shown_when(node) == COMMON01 and not configtree.shown_always(node, on_joints=True)
    (spec,) = configtree.animation_transforms(node)
    assert spec.offset == (0.0, 0.5, 0.0) and spec.position_speed == 2 and spec.alignment == 2
    assert spec.conditions == COMMON02 and spec.speed == 1.0


def test_a_part_scaled_to_nothing_moves_by_no_step_instead_of_failing_the_scene():
    """A node with ``Scale`` 0 has no inverse placement: its position motion and alignment raised, so the config did
    not open (the rest pose is worked out when the scene is built) or the frame did not draw."""
    nothing = np.diag([0.0, 0.0, 0.0, 1.0])
    steps = (nothing, motion.Align(2, nothing), motion.Bob((1.0, 0.0, 0.0), 1.0, False, nothing))
    assert np.allclose(motion.rest(steps), nothing)
    assert np.isfinite(motion.at_time(steps, 0.4, lambda _link: None, motion.Env(wind=(0.0, 1.0)))).all()
