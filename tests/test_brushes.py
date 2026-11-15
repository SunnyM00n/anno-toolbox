"""The brushes' falloff curves."""

import numpy as np
import pytest

from annotoolbox.editor import brushes


@pytest.mark.parametrize("curve", brushes.CURVES)
def test_every_curve_is_full_at_the_centre_and_nothing_past_the_rim(curve):
    w = brushes.falloff([0.0, 1.0, 2.0, 2.5], 2.0, curve)
    assert w[0] == pytest.approx(1.0) and w[-1] == 0.0
    assert np.all(np.diff(w) <= 1e-12)  # never stronger further out


def test_the_smooth_curve_is_the_cosine_the_brushes_always_used():
    d = np.linspace(0, 3, 13)
    assert np.allclose(brushes.falloff(d, 3.0), 0.5 * (1 + np.cos(np.pi * d / 3.0)))


def test_the_curves_differ_halfway_and_an_unknown_one_is_refused():
    half = {c: float(brushes.falloff([1.0], 2.0, c)[0]) for c in brushes.CURVES}
    assert half["constant"] == 1 and half["linear"] == 0.5 and half["sharp"] == 0.25
    assert half["sphere"] == pytest.approx(0.866, abs=1e-3)
    with pytest.raises(ValueError):
        brushes.falloff([0.0], 1.0, "spiky")
