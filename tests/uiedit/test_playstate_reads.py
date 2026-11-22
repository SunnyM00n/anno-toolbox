"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in Play mode:
each test drives the reader with the real class, or a stand-in carrying the real class's names."""

from __future__ import annotations

from types import SimpleNamespace


def test_play_mode_gives_the_design_scope_its_own_host_back(monkeypatch):
    from annotoolbox.uiedit import playstate

    monkeypatch.setattr(playstate, "_start", lambda *args: False)
    monkeypatch.setattr(playstate, "_pass_on", lambda *args: False)
    design = SimpleNamespace(m_host="the design's host")
    scope = SimpleNamespace(m_host="the placing's host", play_base=(design, set(), None))
    playstate.settle(scope)
    assert design.m_host == "the design's host"
