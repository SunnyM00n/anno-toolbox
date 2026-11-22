"""An island's session settings read, written and edited (``islands.sessionsettings``)."""

import struct

from tests.editor.editorfakes import edit_island

from annotoolbox.editor import islandrules
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import sessionsettings


def _gm() -> STag:
    return STag(
        "GameSessionManager",
        [
            STag(
                "SessionSettings",
                [
                    SAttrib("GlobalAmbientName", "amb_roman_01".encode("utf-16-le")),
                    SAttrib("PlayableArea", struct.pack("<4i", 0, 0, 320, 320)),
                    SAttrib("VegetationPropSetName", b"ls_heartlands_01_arid"),
                ],
            )
        ],
    )


def test_the_settings_read_as_plain_values_and_an_island_without_them_reads_empty():
    assert sessionsettings.read(_gm()) == {
        "ambient": "amb_roman_01",
        "vegetation": "ls_heartlands_01_arid",
        "playable_area": [0, 0, 320, 320],
    }
    assert sessionsettings.read(STag("GameSessionManager", [])) == {}


def test_a_setting_is_written_in_the_attributes_own_form_and_an_unfit_one_is_listed():
    gm = _gm()
    edit = {"ambient": "amb_celtic_01", "playable_area": [16, 16, 300, 300], "vegetation": " ", "sky": "x"}
    report = sessionsettings.apply(gm, edit, 320)
    assert sorted(report["set"]) == ["ambient", "playable_area"]
    assert report["unmatched"] == ["the session's vegetation is empty", "session sky"]
    assert sessionsettings.read(gm)["ambient"] == "amb_celtic_01" and sessionsettings.read(gm)["playable_area"] == [
        16,
        16,
        300,
        300,
    ]
    assert sessionsettings.apply(gm, {"playable_area": [0, 0, 400, 320]}, 320)["unmatched"] == [
        "the playable area (0, 0, 400, 320) must lie inside the 320-tile grid, corners in order"
    ]
    assert sessionsettings.apply(gm, {"playable_area": ["a", 1, 2, 3]}, 320)["set"] == []
    assert sessionsettings.apply(STag("GameSessionManager", []), {"ambient": "x"}, 320)["unmatched"] == [
        "session ambient"
    ]


def test_the_session_changes_a_setting_undoes_it_and_drops_an_edit_equal_to_the_islands_own():
    s = CEditSession(edit_island(session={"ambient": "amb_roman_01", "playable_area": [0, 0, 16, 16]}))
    assert s.session_value("ambient") == "amb_roman_01"
    s.set_session("ambient", "amb_celtic_01")
    assert s.session_value("ambient") == "amb_celtic_01" and s.m_edits["session"] == {"ambient": "amb_celtic_01"}
    s.m_undo.undo()
    assert "session" not in s.m_edits and s.session_value("ambient") == "amb_roman_01"
    s.m_undo.redo()
    s.set_session("ambient", "amb_roman_01")  # back to the island's own: no edit left
    assert "session" not in s.m_edits
    s.set_session("playable_area", [0, 0, 99, 16])
    errors = [f.message for f in islandrules.check(s) if f.level == "error"]
    assert "the playable area (0, 0, 99, 16) must lie inside the 16-tile grid, corners in order" in errors


def test_a_tuple_equal_to_the_islands_own_area_is_no_edit_a_bad_key_or_a_readonly_island_takes_none():
    own = {"ambient": "amb_roman_01", "playable_area": [0, 0, 16, 16]}
    s = CEditSession(edit_island(session=own))
    s.set_session("playable_area", (0, 0, 16, 16))  # the same four numbers, as a tuple
    assert "session" not in s.m_edits
    area = [0, 0, 8, 16]
    s.set_session("playable_area", area)
    area[2] = 99  # the caller's list is its own
    assert s.m_edits["session"] == {"playable_area": [0, 0, 8, 16]}
    s.set_session("weather", "rain")
    assert s.m_edits["session"] == {"playable_area": [0, 0, 8, 16]}
    held = CEditSession(edit_island(session=own), is_readonly=True)
    held.set_session("ambient", "amb_celtic_01")
    assert "session" not in held.m_edits and held.m_undo.m_index == 0
