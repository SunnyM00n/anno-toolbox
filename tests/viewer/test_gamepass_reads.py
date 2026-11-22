"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the
viewer's game pass: each test drives the reader with the real class, or a stand-in carrying the real class's names."""

from __future__ import annotations

from types import SimpleNamespace


def test_a_skins_bind_pose_reads_the_game_models_mesh():
    from annotoolbox.viewer.gamepass import _palette

    model = SimpleNamespace(m_mesh=SimpleNamespace(bone_count=lambda per_vertex: 3))  # as gamedraw.CGameModel
    palette = _palette({"skinned": True}, model)
    assert palette.shape == (3, 4, 4)


def test_the_small_waves_read_the_forward_models_water_and_fields():
    """1800's game look asked every drawn model whether it is model water by its old name (``water``) and crashed on
    the first frame of any model."""
    from annotoolbox.viewer.forwardpass import CForwardPass

    holder = SimpleNamespace(
        WAVES=False, m_waves=None, m_renderer=SimpleNamespace(m_files=SimpleNamespace(m_files=None))
    )
    found = [
        SimpleNamespace(m_water=True, m_fields={}),
        SimpleNamespace(m_water=False, m_fields={}),
    ]  # as CForwardModel
    assert CForwardPass._small_waves(holder, found, 1.0) is None  # Ocean waves off: the flat stand-in
