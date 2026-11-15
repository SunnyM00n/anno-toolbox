"""A feedback file read and played (``models.feedbackplay``), and its figures put into a scene (``viewer.figures``):
on a synthetic file, and with ``ANNO117_DIR`` / ``ANNO2070_DIR`` over the install's own files."""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.formats import isd2070
from annotoolbox.models import feedbackplay
from annotoolbox.viewer.modelscene import Scene
from annotoolbox.viewer.motion import at_time


def _dummy(name: str, ident: int, x: float, z: float, turn: float = 0.0) -> str:
    return (f"<i><Name>{name}</Name><Position><x>{x}</x><y>0</y><z>{z}</z></Position>"
            f"<Orientation><w>{math.cos(turn / 2)}</w><x>0</x><y>{math.sin(turn / 2)}</y><z>0</z></Orientation>"
            f"<Id>{ident}</Id></i>")  # fmt: skip


def _loop(n: int, elements: str, dummy: int = 1, visible: int = 1, reset: int = 0, fade: int = 0, group: str = "",
          sequence: int = 0) -> str:  # fmt: skip
    extra = (f"<StartDummyGroup>{group}</StartDummyGroup>" if group else "") + (
        f"<SequenceID>{sequence}</SequenceID>" if sequence else ""
    )
    return (f"<Loop{n}><DefaultState><DummyId>{dummy}</DummyId><Visible>{visible}</Visible>"
            f"<FadeVisibility>{fade}</FadeVisibility><ResetToDefaultEveryLoop>{reset}</ResetToDefaultEveryLoop>"
            f"{extra}</DefaultState>"
            f"<ElementContainer><Elements>{elements}</Elements></ElementContainer></Loop{n}>")  # fmt: skip


WALK = "<i><elementType>0</elementType><WalkSequence>7</WalkSequence><SpeedFactorF>0.5</SpeedFactorF>" \
       "<TargetDummyId>{target}</TargetDummyId><WalkFromCurrentPosition>1</WalkFromCurrentPosition></i>"  # fmt: skip
IDLE = "<i><elementType>1</elementType><m_IdleSequenceID>9</m_IdleSequenceID><MinPlayTime>2000</MinPlayTime></i>"
SPLINE = "<i><elementType>4</elementType><SplineName>path</SplineName><WalkSequence>7</WalkSequence></i>"
HIDE = "<i><elementType>6</elementType><ShowObject>0</ShowObject></i>"
FC = (
    "<DummyRoot><Groups><i><Name>g</Name><Dummies>" + _dummy("a", 1, 0, 0) + _dummy("b", 2, 1, 0) + "</Dummies>"
    "</i></Groups></DummyRoot>"
    "<SplineData><k>path</k><v><Name>path</Name><Id>5</Id><ControlPoints><i><x>1</x><y>0</y><z>0</z></i>"
    "<i><x>1</x><y>0</y><z>1</z></i></ControlPoints></v></SplineData>"
    "<FeedbackDefinition><FeedbackConfigs><i><MainObject>0</MainObject><FeedbackLoops><k>0</k><v>0</v><k>1</k><v>1</v>"
    "</FeedbackLoops><SequenceDefinitions>"
    "<i>" + _loop(0, IDLE) + _loop(1, IDLE) + "</i>"
    "<i>" + _loop(0, "") + _loop(1, WALK.format(target=2) + IDLE + SPLINE + HIDE + WALK.format(target=1)) + "</i>"
    "</SequenceDefinitions></i></FeedbackConfigs></FeedbackDefinition>"
)


def test_read_the_dummies_splines_and_actors():
    fb = feedbackplay.read(isd2070.parse(FC.encode()))
    assert set(fb.dummies) == {"a", "b"} and fb.by_id[2].position == (1.0, 0.0, 0.0)
    assert fb.splines["path"].points == ((1.0, 0.0, 0.0), (1.0, 0.0, 1.0))
    assert len(fb.actors) == 1 and set(fb.actors[0].states) == {0, 1}


def test_an_actor_walks_plays_and_hides():
    fb = feedbackplay.read(isd2070.parse(FC.encode()))
    tl = feedbackplay.play(fb, fb.actors[0], 1)
    start = tl.at(0.0)
    assert start.position == (0.0, 0.0, 0.0) and start.sequence == 7 and start.visible
    halfway = tl.at(1.0)  # one unit at 0.5 a second
    assert halfway.position == pytest.approx((0.5, 0.0, 0.0))
    assert halfway.heading == pytest.approx(feedbackplay.facing(1.0, 0.0, 0.0))
    assert tl.at(3.0).sequence == 9 and tl.at(3.0).position == pytest.approx((1.0, 0.0, 0.0))
    on_spline = tl.at(4.0 + 0.5 / feedbackplay.WALK_PACE)
    assert on_spline.position == pytest.approx((1.0, 0.0, 0.5), abs=1e-6)
    hidden = tl.at(4.0 + 1.0 / feedbackplay.WALK_PACE + 0.5)
    assert not hidden.visible
    assert feedbackplay.play(fb, fb.actors[0], 0).at(5.0).sequence == 9  # the other state idles
    assert feedbackplay.play(fb, fb.actors[0], 7).at(5.0).sequence == 9  # a state the item lacks: its lowest


def test_the_play_count_uses_the_cycle():
    fb = feedbackplay.read(isd2070.parse(FC.encode()))
    tl = feedbackplay.play(fb, fb.actors[0], 0, cycle=lambda s: 5.0)
    assert tl.segments[0].end == pytest.approx(5.0)


def test_figures_walk_in_the_scene():
    from annotoolbox.viewer import figures

    root = isd2070.parse(FC.encode())
    found = figures.Figures.of(None, "anno117", root, 1, units=None)
    scene = Scene("x.cfg", "anno117")
    found.graft(scene)
    assert [p.node for p in scene.parts] == ["Figures/0/marker"]
    m = at_time(scene.parts[0].motion, 1.0, lambda _l: None)
    assert m[:3, 3] == pytest.approx((0.5, 0.0, 0.0))
    front = m[:3, :3] @ np.array([-1.0, 0.0, 0.0])  # a unit faces -x: it walks along +x
    assert front == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)
    gone = at_time(scene.parts[0].motion, 4.0 + 1.0 / feedbackplay.WALK_PACE + 0.5, lambda _l: None)
    assert np.allclose(gone[:3, :3], 0.0)  # hidden: drawn at no size
    assert found.animations(1.0) == {}


def _item(definitions: str, states: str = "<k>1</k><v>0</v>", extra: str = "", units: str = "") -> str:
    return (f"<i>{units}<MainObject>0</MainObject><FeedbackLoops>{states}</FeedbackLoops>"
            f"<SequenceDefinitions>{definitions}</SequenceDefinitions>{extra}</i>")  # fmt: skip


def _file(*items: str, dummies: str = "") -> isd2070.Node:
    head = (
        "<DummyRoot><Groups><i><Name>g</Name><Dummies>" + _dummy("a", 1, 0, 0) + _dummy("b", 2, 1, 0) + "</Dummies>"
        "</i><i><Name>low</Name><Dummies>" + dummies + "</Dummies></i></Groups></DummyRoot>"
    )
    return isd2070.parse((head + "<FeedbackDefinition><FeedbackConfigs>" + "".join(items) +
                          "</FeedbackConfigs></FeedbackDefinition>").encode())  # fmt: skip


def _wait(ms: int) -> str:
    return f"<i><elementType>2</elementType><MinTime>{ms}</MinTime><MaxTime>{ms}</MaxTime></i>"


BARRIER = "<i><elementType>7</elementType><BarrierId>3</BarrierId><TimeOffset>0</TimeOffset></i>"


def test_the_items_make_their_actors():
    one = "<i>" + _loop(0, _wait(1000)) + _loop(1, _wait(1000)) + "</i>"
    root = _file(
        _item(one, extra="<ActorCount>2</ActorCount><MaxActorCount>2</MaxActorCount>"),
        _item(one, extra="<CreateChance>-1</CreateChance>"),  # never made
        _item(one, extra="<MultiplyActorByDummyCount>g</MultiplyActorByDummyCount>"),
        _item(one).replace("<MainObject>0</MainObject>", "<MainObject>1</MainObject>"),
    )
    fb = feedbackplay.read(root)
    built = feedbackplay.build(fb)
    assert [(b.item, b.number, b.main) for b in built] == [
        (0, 0, False),
        (0, 1, False),
        (2, 0, False),
        (2, 1, False),
        (3, 0, True),
    ]  # the object itself: for the barriers
    assert feedbackplay.build(fb) == built  # a fixed seed: the same each time the view opens


def test_a_barrier_waits_for_every_actor_of_the_file():
    walker = "<i>" + _loop(0, "") + _loop(1, WALK.format(target=2) + BARRIER + IDLE) + "</i>"
    waiter = "<i>" + _loop(0, "") + _loop(1, _wait(5000) + BARRIER + IDLE) + "</i>"
    fb = feedbackplay.read(_file(_item(walker), _item(waiter)))
    walk, wait = feedbackplay.play_all(fb, feedbackplay.build(fb), 1)
    assert walk.at(3.0).sequence == 7 and walk.at(3.0).position == pytest.approx((1.0, 0.0, 0.0))  # it stands
    assert walk.at(5.5).sequence == 9 and wait.at(5.5).sequence == 9  # both go on when the second arrives
    alone = feedbackplay.play(fb, fb.actors[0], 1)
    assert alone.at(3.0).sequence == 9  # by itself its own arrival opens it
    twice = "<i>" + _loop(0, BARRIER) + _loop(1, BARRIER + IDLE) + "</i>"  # counted twice, reached once
    held = feedbackplay.play_all(feedbackplay.read(_file(_item(twice))), [feedbackplay.Built(0, 0, None, 1)], 1)[0]
    assert held.at(100.0).sequence == 0 and held.held  # no timeout: it stands


def test_fades_and_the_height_rules():
    show = "<i><elementType>6</elementType><ShowObject>1</ShowObject><FadingMode>{m}</FadingMode>" \
           "<TimeOffset>500</TimeOffset></i>"  # fmt: skip
    definition = "<i>" + _loop(0, show.format(m=2) + IDLE, visible=0) + _loop(1, IDLE) + "</i>"
    fb = feedbackplay.read(_file(_item(definition)))
    tl = feedbackplay.play(fb, fb.actors[0], 1)
    assert not tl.at(0.2).visible and tl.at(1.0).alpha == pytest.approx(0.5) and tl.at(2.0).alpha == 1.0
    assert tl.at(1.0).sequence == 0 and tl.at(1.3).sequence == 9  # mode 2 stands 1.2 s for its fade
    at_once = feedbackplay.read(_file(_item(definition.replace("<FadingMode>2", "<FadingMode>0"))))
    assert feedbackplay.play(at_once, at_once.actors[0], 1).at(0.6).alpha == 1.0
    low = _dummy("under", 9, 2, 2).replace("<y>0</y>", "<y>-1</y>")
    fb = feedbackplay.read(
        _file(dummies=low.replace("<Id>9</Id>", "<Id>9</Id><HeightAdaptationMode>3</HeightAdaptationMode>"))
    )
    assert fb.dummies["under"].placed() == (2.0, 0.0, 2.0)  # never below the ground
    fb = feedbackplay.read(_file(dummies=low))
    assert fb.dummies["under"].placed() == (2.0, -1.0, 2.0)  # mode 0: the object's height


def test_a_2070_walk_goes_at_its_sequence_speed_and_a_spline_at_its_own():
    walk = "<i><elementType>0</elementType><WalkSequence>7</WalkSequence><TargetDummyId>2</TargetDummyId></i>"
    spline = SPLINE.replace("</SplineName>", "</SplineName><SpeedFactor>2048</SpeedFactor>")
    text = FC.replace(WALK.format(target=2) + IDLE + SPLINE + HIDE + WALK.format(target=1), walk + IDLE + spline)
    fb = feedbackplay.read(isd2070.parse(text.encode()))
    segments = feedbackplay.play(fb, fb.actors[0], 1, movement=feedbackplay.Movement(speeds={7: 0.25})).segments
    assert segments[0].end == pytest.approx(1.0 / 0.25)  # the unit's sequence Speed, no BaseSpeed in 2070
    walked = next(s for s in segments[1:] if len(s.path) > 1)  # to the spline's start, then along it
    assert walked.end - walked.start == pytest.approx(math.dist(*walked.path[:2]) / 0.5)  # 2048 / 4096 a second


def test_a_2070_figure_walks_at_its_units_ifo_speed():
    from tests.fakes import FakeFiles

    from annotoolbox.editor.assetmodels import Resolved
    from annotoolbox.viewer import figures

    class Units:
        def model_for(self, guid: int, variation: int = 0) -> Resolved:
            return Resolved("data/graphics/worker.cfg" if guid == 42 else "")

    walk = "<i><elementType>0</elementType><WalkSequence>7</WalkSequence><TargetDummyId>2</TargetDummyId></i>"
    root = isd2070.parse(
        FC.replace(WALK.format(target=2) + IDLE + SPLINE + HIDE + WALK.format(target=1), walk).encode()
    )
    item = next(n for n in root.iter() if n.find("SequenceDefinitions") is not None)
    unit = isd2070.Node("GuidVariationList", data=struct.pack("<2i", 42, 0))
    item.children.append(isd2070.Node("AssetVariationList", children=[unit]))
    ifo = b"<Info><Sequence><Id>7</Id><Duration>1000</Duration><Speed>1024</Speed></Sequence></Info>"
    files = FakeFiles({"data/graphics/worker.cfg": b"", "data/graphics/worker.ifo": ifo})
    found = figures.Figures.of(files, "anno2070", root, 1, units=Units())
    assert found.figures[0].unit is None  # the empty cfg builds no scene: a marker, still at its unit's pace
    assert found.figures[0].timeline.segments[0].end == pytest.approx(1.0 / 0.25)  # 1024 / 4096 a second
    assert figures.ifo_speeds(files, "data/graphics/worker.cfg") == {7: 1024.0}  # the newer games store it plain


def test_a_group_dummy_the_pace_the_turn_and_a_walk_chain():
    group = "<DummyGroup>CDATA[" + "\x0c" + "]</DummyGroup>"
    del group  # a binary value is written by the tree: build it there
    walk = WALK.format(target=0).replace("<SpeedFactorF>0.5</SpeedFactorF>", "<SpeedFactorF>0</SpeedFactorF>")
    definition = "<i>" + _loop(0, "") + _loop(1, walk + IDLE) + "</i>"
    root = _file(_item(definition), dummies=_dummy("far", 9, 2, 0))
    element = next(n for n in root.iter() if n.name == "i" and n.find("WalkSequence") is not None)
    element.children.append(isd2070.Node("DummyGroup", data=struct.pack("<3i", 1, 1, -1)))  # Groups[1]
    fb = feedbackplay.read(root)
    move = feedbackplay.Movement(2.0, 90.0, 45.0, {7: 0.25})
    walked = feedbackplay.play(fb, fb.actors[0], 1, movement=move).segments[0]
    assert walked.path[-1] == (2.0, 0.0, 0.0)  # the group's dummy
    assert walked.end == pytest.approx(2.0 / (0.25 * 2.0))  # a walk with 0: its sequence's Speed x BaseSpeed
    fast = WALK.format(target=2) + WALK.format(target=1).replace("0.5</SpeedFactorF>", "5</SpeedFactorF>")
    fb = feedbackplay.read(_file(_item("<i>" + _loop(0, "") + _loop(1, fast + IDLE) + "</i>")))
    tl = feedbackplay.play(fb, fb.actors[0], 1, movement=feedbackplay.Movement(1.0, 90.0, 45.0))
    first, second = tl.segments[0], tl.segments[1]
    assert first.end == pytest.approx(2.0) and second.end - second.start == pytest.approx(2.0)  # the first's pace
    back = tl.at(first.end + 0.5).heading  # turning round at 90 degrees a second: half way after a second
    assert abs(abs(back - feedbackplay.facing(1.0, 0.0, 0.0)) - math.radians(45)) < 0.05


@pytest.mark.parametrize("game", ["anno117", "anno2070", "anno1800", "anno2205"])
def test_the_games_feedback_files_play(game):
    from annotoolbox.sources.gametree import open_game
    from annotoolbox.viewer import figures

    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {INSTALL_VARS[game]}")
    tree = open_game(game, Path(folder))
    played = held = 0
    paths = [p for p in tree.paths if p.endswith(".fc")][:150]
    for path in paths:
        fb = feedbackplay.read(isd2070.parse(tree.read(path)))
        built = feedbackplay.build(fb)
        for state in sorted({s for a in fb.actors for s in a.states}):
            for tl in feedbackplay.play_all(fb, built, state):
                poses = [tl.at(float(t)) for t in range(0, 60, 3)]
                assert all(math.isfinite(v) for p in poses for v in p.position), path
                held += tl.held
                played += 1
    assert played > 50
    units = figures.units_of(tree, game)
    with_unit = 0
    for path in [p for p in paths if b"GuidVariationList" in tree.read(p)][:12]:
        found = figures.Figures.of(tree, game, isd2070.parse(tree.read(path)), 1, units)
        with_unit += sum(f.unit is not None for f in found.figures)
        for f in found.figures:
            assert all(math.isfinite(v) for v in f.walk.at_time(7.0).ravel()), path
    assert with_unit > 0 or game == "anno2070"
    print(f"{game}: {played} actors played, {held} standing at the end, {with_unit} figures with their unit")


def test_a_loop_that_resets_starts_every_pass_at_its_default_state():
    def played(reset: int, fade: int = 0):
        loops = "<i>" + _loop(0, "") + _loop(1, WALK.format(target=2) + HIDE, reset=reset, fade=fade) + "</i>"
        text = FC.split("<FeedbackDefinition>")[0] + (
            "<FeedbackDefinition><FeedbackConfigs><i><MainObject>0</MainObject><FeedbackLoops><k>1</k><v>0</v>"
            "</FeedbackLoops><SequenceDefinitions>" + loops + "</SequenceDefinitions></i></FeedbackConfigs>"
            "</FeedbackDefinition>")  # fmt: skip
        fb = feedbackplay.read(isd2070.parse(text.encode()))
        return fb, feedbackplay.play(fb, fb.actors[0], 1)

    _, plain = played(0)
    assert not plain.at(5.0).visible  # hidden by the first pass and never shown again
    fb, again = played(1, fade=1)
    loop = fb.actors[0].states[1][1]
    assert loop.reset and loop.fade
    second = again.at(2.0 + 1.5)  # the first pass takes 2 s (one unit at 0.5 a second): a fade in, on its way again
    assert second.alpha > 0.5 and second.position[0] < 1.0


def test_the_actors_of_a_multiplied_item_start_at_the_dummies_of_the_start_group():
    loops = "<i>" + _loop(0, _wait(1000), dummy=0, group="g") + _loop(1, _wait(1000), dummy=0, group="g") + "</i>"
    fb = feedbackplay.read(_file(_item(loops, extra="<MultiplyActorByDummyCount>g</MultiplyActorByDummyCount>")))
    assert fb.actors[0].states[1][0].start_group == "g"
    built = feedbackplay.build(fb)
    lines = feedbackplay.play_all(fb, built, 1)
    assert [tl.at(0.5).position for tl in lines] == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]  # dummy n for actor n
    alone = "<i>" + _loop(0, _wait(1000), dummy=0, group="nothing") + "</i>"  # a group the file does not hold
    fb = feedbackplay.read(_file(_item(alone)))
    assert feedbackplay.play(fb, fb.actors[0], 1).at(0.5).position == (0.0, 0.0, 0.0)  # the object's origin


def test_a_loops_default_sequence_plays_until_an_element_picks_one():
    loops = "<i>" + _loop(0, _wait(1000), sequence=777) + _loop(1, _wait(1000), sequence=777) + "</i>"
    fb = feedbackplay.read(_file(_item(loops)))
    assert fb.actors[0].states[1][0].sequence == 777
    assert feedbackplay.play(fb, fb.actors[0], 1).at(0.5).sequence == 777
    plain = "<i>" + _loop(0, _wait(1000)) + _loop(1, _wait(1000)) + "</i>"
    fb = feedbackplay.read(_file(_item(plain)))
    assert feedbackplay.play(fb, fb.actors[0], 1).at(0.5).sequence == 0


@pytest.mark.skipif(not os.environ.get(INSTALL_VARS["anno117"]), reason="needs ANNO117_DIR")
def test_117s_start_groups_are_numbered_in_order_and_named_by_the_multiplied_item():
    from annotoolbox.sources.gametree import open_game

    tree = open_game("anno117", Path(os.environ[INSTALL_VARS["anno117"]]))
    groups = same = other = 0
    for path in [p for p in tree.paths if p.endswith(".fc")][:300]:
        fb = feedbackplay.read(isd2070.parse(tree.read(path)))
        for actor in fb.actors:
            for loops in actor.states.values():
                for loop in loops:
                    members = fb.groups.get(loop.start_group) if loop.start_group else None
                    if not members:
                        continue
                    groups += 1
                    same += actor.by_dummies == loop.start_group
                    other += bool(actor.by_dummies) and actor.by_dummies != loop.start_group
                    suffixes = [m.name.rsplit("_", 1)[-1] for m in members]
                    assert suffixes == [str(k) for k in range(len(members))], (path, loop.start_group)
    assert groups > 100 and same > 10 * other  # nearly every start group is the group its item multiplies by
