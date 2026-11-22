"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the stored
preview defaults: each test drives the reader with the real class, or a stand-in carrying the real class's names."""

from __future__ import annotations


def test_preview_defaults_saved_by_the_released_toolbox_keep_the_players_look():
    from annotoolbox.uiedit import previewdefaults

    stored = {"player_name": "Marcia", "player_portrait": 2, "player_emblem": 3, "player_colour": 4}  # v0.2's keys
    defaults = previewdefaults.with_changes(stored)
    assert (defaults.participant_name, defaults.participant_portrait) == ("Marcia", 2)
    assert (defaults.participant_emblem, defaults.participant_color) == (3, 4)
    assert previewdefaults.with_changes(previewdefaults.changes(defaults)) == defaults  # saved again by the new names
