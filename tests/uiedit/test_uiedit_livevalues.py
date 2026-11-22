"""The UI editor's live values (``uiedit/liverecord``, ``uiedit/livevalues``) and the link's ``ui.values``: taken from a
fake game, shown in the preview, recorded, read back after a restart, filled with no game linked, stale where the
file changed, cleared; what the game assigned (an asset, an icon, a portrait) looked up in the install."""

from __future__ import annotations

import os
import uuid

import pytest
from tests import fakegame
from tests.uiedit.uieditfakes import (
    GONE,
    INNER,
    KINDS,
    SCENE,
    link_reply,
    open_scene,
    settle_window,
    take_and_use_offline,
    title_element,
)

from annotoolbox.suite import linkprotocol, livelink
from annotoolbox.uiedit import elements, liverecord, pictures

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# the values, with no window


def test_a_reply_keeps_only_the_shown_values():
    held = liverecord.from_reply(
        link_reply(
            {
                "guid": "g",
                "crc": "0000ABCD",
                "text": "1,234 t",
                "visible": False,
                "image": "icon_wood",
                "values": {"7": 2.5, "8": True, "x": 1},
                "class": "LabelWidget",
                "flags": "0x21",
            }
        ),  # fmt: skip
        "data/ui/x",
        "anno117",
        "392218104",
        "2026-11-17T15:00:00",
    )
    (w,) = held.widgets
    assert set(w) == {"guid", "crc", "text", "visible", "image", "values"}  # never a class, a flag or a matrix
    assert held.texts() == {"g": "1,234 t", "crc:0000abcd": "1,234 t"}
    values = held.overrides()["g"]
    assert values == {(liverecord.VISIBLE,): False, (liverecord.IMAGE_ID,): "icon_wood", (7,): 2.5, (8,): True}
    assert held.states == ["Composed"] and not held.cut


def test_a_recording_survives_a_restart_and_is_cleared(tmp_path):
    held = liverecord.from_reply(link_reply({"guid": "g", "text": "a"}), "data/ui/x", "anno117", "b1", "t1")
    liverecord.CRecordings(tmp_path).save(held)
    again = liverecord.CRecordings(tmp_path).load("anno117", "data/ui/x")  # a new run reads it back
    assert again is not None and again.recorded and again.widgets == held.widgets and again.build == "b1"
    assert liverecord.CRecordings(tmp_path).load("anno1800", "data/ui/x") is None  # per game
    newer = liverecord.from_reply(link_reply({"guid": "g", "text": "b"}), "data/ui/x", "anno117", "b2", "t2")
    liverecord.CRecordings(tmp_path).save(newer)  # taking again replaces it
    assert liverecord.CRecordings(tmp_path).load("anno117", "data/ui/x").texts() == {"g": "b"}
    liverecord.CRecordings(tmp_path).save(liverecord.SHeld("data/ui/y", "anno117"))
    assert liverecord.CRecordings(tmp_path).clear("anno117", "data/ui/x")
    assert liverecord.CRecordings(tmp_path).load("anno117", "data/ui/x") is None
    assert liverecord.CRecordings(tmp_path).clear_all() == 1


def test_a_recording_keeps_the_language_the_values_were_taken_in(tmp_path):
    from annotoolbox.uiedit import gamelanguages

    reply = {**link_reply({"guid": "g", "text": "Holz"}), "language": "german"}
    held = liverecord.from_reply(reply, "data/ui/x", "anno117", "b1", "t1")
    liverecord.CRecordings(tmp_path).save(held)
    again = liverecord.CRecordings(tmp_path).load("anno117", "data/ui/x")
    assert held.language == again.language == "german"
    assert gamelanguages.live_note(again, "english") == "German" and gamelanguages.live_note(again, "german") == ""
    plain = liverecord.from_reply(link_reply({"guid": "g"}), "data/ui/x", "anno117", "b1", "t1")
    assert plain.language == "" and gamelanguages.live_note(plain, "english") == ""  # an answer that names none


def test_a_widget_whose_values_are_no_table_or_no_numbers_is_passed_over():
    odd = {"guid": "g", "values": ["not", "a", "table"]}
    wide = {"guid": "h", "values": {"7": float("inf"), "8": float("nan"), "9": 1010017.0}}
    held = liverecord.from_reply(link_reply(odd, wide), "k", "anno117", "", "")
    assert "g" not in held.overrides() and set(held.overrides()["h"]) == {(7,), (8,), (9,)}
    rows = liverecord.assigned(held, lambda guid: ("Wood Cutter", "w.png"), lambda image: True)
    assert [(r.what, r.ref) for r in rows] == [("asset", "1010017")]  # no number that is none is looked up


def test_stale_widgets_are_counted():
    held = liverecord.from_reply(
        link_reply({"guid": "a"}, {"guid": "b", "crc": "00000001"}, {"guid": "c"}), "k", "g", "", ""
    )
    assert held.unmatched({"a", "crc:00000001"}) == 1  # b matches by its name CRC, c by nothing


def test_what_the_game_assigned_is_looked_up_in_the_install():
    # the image ids as the game's dumped scenes name them (2026-11-17): backslash paths, render targets, an unknown id
    held = liverecord.from_reply(
        link_reply(
            {"guid": "1", "name": "Building", "values": {"12": 106143, "13": 3, "14": True}},
            {"guid": "2", "name": "Icon", "image": "data\\ui\\fhd\\main\\icons\\icon_wood.png"},
            {"guid": "3", "name": "Governor", "image": "rtt_166413"},
            {"guid": "4", "name": "Portrait", "image": "governor_7"},
        ),
        "k",
        "anno117",
        "",
        "",
    )
    assets = {106143: ("Wood Cutter", "data/ui/2kimages/main/3dicons/icon_wood_cutter.png")}
    files = {"data/ui/4k/main/icons/icon_wood_0.dds"}  # the install's picture of that path (pictures.candidates)
    rows = liverecord.assigned(held, assets.get, lambda image: any(p in files for p in pictures.candidates(image)))
    asset, icon, rendered, portrait = rows
    assert (asset.what, asset.ref, asset.name, asset.icon) == ("asset", "106143", *assets[106143])
    assert (icon.what, icon.icon, icon.note) == ("image", "data\\ui\\fhd\\main\\icons\\icon_wood.png", "")
    assert (rendered.what, rendered.icon, rendered.note) == ("image", "", liverecord.MADE_AT_RUN_TIME)
    assert (portrait.what, portrait.note) == ("portrait", liverecord.NO_FILE)
    assert len(rows) == 4  # 3 and True are no asset GUIDs


def test_the_largest_dumped_file_fits_a_recording(tmp_path):
    """The shape of the largest file the game's 136 dumped scenes place (counts and lengths only, texts made up): 146
    labels with texts of 159 characters, 40 of them with 42 values each; recorded and read back whole."""
    widgets = [
        {"guid": str(uuid.UUID(int=i)), "crc": f"{i:08x}", "visible": i % 5 != 0, "text": "x" * 159,
         **({"values": {str(1000 + k): (k % 2 == 1) if k % 2 else float(k) for k in range(42)}} if i < 40 else {})}
        for i in range(146)
    ]  # fmt: skip
    held = liverecord.from_reply(link_reply(*widgets), "data/ui/x", "anno117", "b", "t")
    liverecord.CRecordings(tmp_path).save(held)
    back = liverecord.CRecordings(tmp_path).load("anno117", "data/ui/x")
    assert len(back.widgets) == 146 and len(back.texts()) == 2 * 146 and len(back.overrides()[widgets[0]["guid"]]) == 43
    assert liverecord.CRecordings(tmp_path).path("anno117", "data/ui/x").stat().st_size < 100_000


# the client


def test_the_client_asks_for_the_values_of_the_open_file():
    link = fakegame.connect(fakegame.FakeGame(kinds=KINDS))
    try:
        link.game.answers["ui.values"] = (livelink.OK, "", link_reply({"guid": "g", "text": "x"}))
        reply = link.ui_values(["HUD"], INNER)
        assert reply.is_ok and reply.data["scenes"][0]["widgets"] == [{"guid": "g", "text": "x"}]
        assert link.game.requests[-1] == ("ui.values", {"states": ["HUD"], "file": INNER})
        assert link.ui_values([]).status == livelink.REFUSED
        assert linkprotocol.check_request("ui.values", {"file": INNER})  # the states are required
    finally:
        fakegame.disconnect(link)
    narrow = fakegame.connect(fakegame.FakeGame(kinds=livelink.KINDS))
    try:
        assert narrow.ui_values(["HUD"]).status == livelink.UNAVAILABLE and narrow.game.names() == ["subscribe"]
    finally:
        fakegame.disconnect(narrow)
    assert livelink.CNotConnected().ui_values(["HUD"]).status == livelink.NOT_CONNECTED


# the window


def test_the_window_takes_records_and_fills_offline(window, monkeypatch):
    take_and_use_offline(window, monkeypatch)


def test_a_hidden_widget_shows_as_the_game_shows_it(window, monkeypatch):
    from annotoolbox.uiedit import livevalues

    open_scene(window, SCENE)
    settle_window(window)
    title = title_element(window)
    held = liverecord.from_reply(
        link_reply({"guid": title.guid, "visible": False}), window.doc.m_key, "anno117", "", ""
    )
    livevalues.recordings(window).save(held)
    livevalues.use_recorded(window)
    settle_window(window)
    assert not any(i.kind == "text" and i.owner == id(title.node) for i in window.m_frame.items)


def test_the_window_lists_what_the_game_assigned(window, monkeypatch):
    from annotoolbox.uiedit import livevalues

    open_scene(window, SCENE)
    settle_window(window)
    held = liverecord.from_reply(
        link_reply(
            {"guid": "1", "name": "Panel", "values": {"5": 1010017}}, {"guid": "2", "name": "Portrait", "image": "p7"}
        ),
        window.doc.m_key,
        "anno117",
        "",
        "",
    )
    livevalues.recordings(window).save(held)
    livevalues.use_recorded(window)
    monkeypatch.setattr(livevalues, "asset_lookup", lambda settings, key: {1010017: ("Wood Cutter", "w.png")}.get)
    livevalues.list_assigned(window, is_waiting=True)
    rows = window.assigned_rows
    assert [(r.what, r.ref, r.name) for r in rows] == [("asset", "1010017", "Wood Cutter"), ("portrait", "p7", "")]
    assert rows[1].note == "no file"  # the test install has no picture of that name


def test_element_keys_are_what_the_preview_matches(window):
    from annotoolbox.uiedit import livevalues

    open_scene(window, SCENE)
    settle_window(window)
    title = title_element(window)
    keys = livevalues.element_keys(window.m_top)
    assert title.guid in keys and (elements.crc_key(title) is None or elements.crc_key(title) in keys)


def test_held_values_are_found_by_guid_then_by_name_crc(window):
    open_scene(window, SCENE)
    settle_window(window)
    title = title_element(window)
    crc = elements.crc_key(title)
    assert crc == elements.crc_name(int(crc[4:], 16)) and elements.keys(title) == (title.guid, crc)
    assert elements.held_for({}, title) is None and elements.held_for({"other": 1}, title) is None
    assert elements.held_for({crc: "by crc"}, title) == "by crc"
    assert elements.held_for({crc: "by crc", title.guid: "by guid"}, title) == "by guid"
    held = liverecord.from_reply(
        link_reply({"guid": GONE, "crc": crc[4:].upper(), "text": "x"}), "k", "anno117", "b", "t"
    )
    assert crc in held.texts()  # the answer's CRC and the file's are one spelling


def test_a_value_matched_by_its_name_crc_is_named_live_in_the_properties(window):
    from annotoolbox.uiedit import gamedata, gamedataview

    open_scene(window, SCENE)
    settle_window(window)
    title = title_element(window)
    seen = []
    gamedata_origins = gamedata.origins

    def origins(key, read, guid, samples, live=""):
        seen.append(live)
        return gamedata_origins(key, read, guid, samples, live)

    window.m_ctx.m_live_text = {elements.crc_key(title): "from the game"}  # a run-time handle is no file GUID
    try:
        gamedata.origins = origins
        gamedataview.origin_rows(window, title)
    finally:
        gamedata.origins = gamedata_origins
    assert seen and seen[0]  # the origin of its values is the game, not a sample


@pytest.fixture(autouse=True)
def _no_link_left():
    yield
    livelink.reset()
