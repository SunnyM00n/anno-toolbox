"""The forward games' shadow fit (``render.shadowforward``): the light's box holds the corners, and the sun vector the
caller hands over stays as it was."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.render import shadowforward


def test_the_fit_leaves_the_callers_light_direction_alone():
    down = np.array([1.0, -2.0, 0.5])
    corners = np.array([[0.0, 0.0, 0.0], [10.0, 5.0, 10.0]])
    found = shadowforward.fit(down, corners)
    assert down.tolist() == [1.0, -2.0, 0.5]  # not normalised in place
    assert found.values["cVecShadowVPDN"][1] < 0  # depth runs along the light's travel (down)
