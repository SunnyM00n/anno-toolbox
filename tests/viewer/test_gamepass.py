"""The game's-shaders pass keeps what its parts failed to compile: a ground that did not compile stays False."""

import numpy as np

from annotoolbox.viewer.gamepass import CGamePass


def test_a_ground_that_fails_to_compile_stays_false_and_is_not_asked_again():
    asked = []

    def failing_part(self, key, compile_, make, what, instead, on_error=lambda: None):
        asked.append(key)
        on_error()
        return None

    gate = object.__new__(CGamePass)
    gate.m_ground = None
    gate._part = failing_part.__get__(gate)  # the compile error path of ``ownpass.COwnPass._part``
    assert gate._ground(np.eye(4)) is None
    assert gate.m_ground is False
    assert gate._ground(np.eye(4)) is None
    assert asked == ["ground"]
