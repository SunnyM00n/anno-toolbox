"""The further height brushes: noise, terrace, ramp and spike removal as strokes of the edit session."""

import numpy as np
import pytest
from tests.editor.editorfakes import EDIT_SIZE as SIZE
from tests.editor.editorfakes import edit_island as island

from annotoolbox.editor import terraintools
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.islands import islandedits


def stroke(s: CEditSession, mode: str, points, radius: float = 3.0, strength: float = 0.5) -> None:
    s.begin_height(mode, radius, strength, *points[0])
    for p in points[1:]:
        s.height_to(*p)
    s.end_height()


def reload(s: CEditSession, tmp_path) -> CEditSession:
    """The session again from the edit file it saved: what the build would read."""
    path = tmp_path / "edits.json"
    islandedits.save(path, s.m_edits)
    return CEditSession(island(), islandedits.load(path))


def test_noise_is_repeatable_bounded_and_stays_in_the_brush():
    s = CEditSession(island())
    stroke(s, "noise", [(8.0, 8.0)], radius=3.0, strength=1.0)
    d = s.m_corner_deltas.copy()
    assert d.any() and np.abs(d).max() <= terraintools.NOISE_AMPLITUDE + 1e-6
    zz, xx = np.mgrid[0 : SIZE + 1, 0 : SIZE + 1]
    assert not d[np.hypot(xx - 8, zz - 8) > 3].any()
    again = CEditSession(island())
    stroke(again, "noise", [(8.0, 8.0)], radius=3.0, strength=1.0)
    assert np.array_equal(d, again.m_corner_deltas)  # the same place always gives the same relief
    stroke(s, "noise", [(8.0, 8.0)], radius=3.0, strength=1.0)
    assert np.abs(s.m_corner_deltas).max() > np.abs(d).max()  # a second stroke deepens it


def test_value_noise_is_smooth_and_in_range():
    zz, xx = np.mgrid[0:20, 0:20]
    n = terraintools.value_noise(xx / 2, zz / 2)
    assert n.min() >= -1 and n.max() <= 1 and n.std() > 0.1
    assert np.abs(np.diff(n, axis=1)).max() < 0.5  # neighbouring half tiles differ little


def test_terrace_pulls_the_ground_towards_the_nearest_step():
    s = CEditSession(island(), {"heights": {"8,8": 0.6}})  # 0.7 + 0.6 = 1.3, 0.3 over the step at 1.0
    before = s.corner_heights()[8, 8]
    stroke(s, "terrace", [(8.0, 8.0)], radius=2.0, strength=1.0)
    after = s.corner_heights()[8, 8]
    assert abs(after - 1.0) < abs(before - 1.0)
    for _ in range(12):
        stroke(s, "terrace", [(8.0, 8.0)], radius=2.0, strength=1.0)
    assert s.corner_heights()[8, 8] == pytest.approx(1.0, abs=0.02)


def test_spike_removal_puts_a_lone_spike_on_its_neighbours_and_leaves_a_slope():
    s = CEditSession(island(), {"heights": {"8,8": 3.0}})
    assert s.corner_heights()[8, 8] == pytest.approx(3.7)
    stroke(s, "despike", [(8.0, 8.0)], radius=2.0, strength=0.5)
    assert s.corner_heights()[8, 8] == pytest.approx(0.7, abs=0.01)
    assert "8,8" not in s.m_edits["heights"] or abs(s.m_edits["heights"]["8,8"]) < 0.01
    slope = CEditSession(island(), {"heights": {f"{z},{x}": 0.1 * x for z in range(4, 13) for x in range(4, 13)}})
    other = slope.m_corner_deltas.copy()
    stroke(slope, "despike", [(8.0, 8.0)], radius=4.0, strength=1.0)
    assert np.array_equal(slope.m_corner_deltas, other)  # a gentle ramp is no spike


def test_a_ramp_joins_the_two_ends_heights_and_follows_the_last_point_of_the_drag():
    s = CEditSession(island())
    sea, land = s.corner_heights()[8, 1], s.corner_heights()[8, 8]
    assert sea == pytest.approx(-2.0) and land == pytest.approx(0.7)
    s.begin_height("ramp", 1.5, 0.5, 1.0, 8.0)
    s.height_to(12.0, 3.0)  # a sidestep: the preview must not keep it
    s.height_to(8.0, 8.0)
    s.end_height()
    line = s.corner_heights()[8, 1:9]
    assert line[0] == pytest.approx(sea, abs=0.01) and line[-1] == pytest.approx(land, abs=0.01)
    assert np.all(np.diff(line) >= -1e-6)  # a steady climb
    assert line[3] == pytest.approx(sea + (land - sea) * 3 / 7, abs=0.05)
    assert s.m_corner_deltas[3, 12] == 0 and s.m_corner_deltas[12, 3] == 0  # the sidestep left nothing behind


def test_a_ramp_with_no_length_changes_nothing():
    s = CEditSession(island())
    stroke(s, "ramp", [(5.0, 5.0)])
    assert not s.m_corner_deltas.any() and not s.m_edits["heights"]


@pytest.mark.parametrize("mode", terraintools.MODES)
def test_every_stroke_is_one_undoable_height_edit_that_round_trips_the_edit_file(mode, tmp_path):
    s = CEditSession(island(), {"heights": {"8,8": 0.9, "7,7": 0.9}})
    base = s.m_corner_deltas.copy()
    stroke(s, mode, [(6.0, 8.0), (8.0, 8.0), (10.0, 8.0)])
    assert len(s.m_undo.m_commands) == 1
    assert not np.array_equal(s.m_corner_deltas, base)
    again = reload(s, tmp_path)
    assert np.allclose(again.m_corner_deltas, s.m_corner_deltas, atol=1e-3)  # the edit file keeps it to a thousandth
    assert s.m_undo.undo() == "height"
    assert np.array_equal(s.m_corner_deltas, base)
    s.m_undo.redo()
    assert np.allclose(s.m_corner_deltas, again.m_corner_deltas, atol=1e-3)


def test_the_brush_falloff_applies_to_the_new_modes_too():
    smooth, constant = CEditSession(island()), CEditSession(island())
    constant.m_falloff = "constant"
    for s in (smooth, constant):
        stroke(s, "noise", [(8.0, 8.0)], radius=4.0, strength=1.0)
    assert np.abs(constant.m_corner_deltas).sum() > np.abs(smooth.m_corner_deltas).sum()


def test_an_unknown_stamp_is_refused():
    with pytest.raises(ValueError):
        terraintools.delta("flatten", np.zeros((4, 4)), (0, 3, 0, 3), np.ones((4, 4)), 0.5)
