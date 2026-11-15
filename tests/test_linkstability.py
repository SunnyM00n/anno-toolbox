"""The suite's side of the live link's stability guards (docs/live-link.md, "Asset reloads"): the game's stall warning,
the note that placed objects keep stale data, and what the suite never offers."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests import fakegame

from annotoolbox.suite import linkannounce, linkclient, livelink


def wait_for(condition, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def link():
    """A link to a stand-in game over a socket pair, as the suite's current link; the game is on ``link.game``."""
    channel, game = fakegame.pair()
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    mine.game = game
    livelink.use(mine)
    yield mine
    mine.close()
    game.drop()
    livelink.reset()


def test_the_stall_warning_shows_in_the_status_until_the_game_answers_again(link):
    seen = []
    remove = livelink.on(livelink.STALLED, seen.append)
    link.game.push(livelink.STALLED, {"seconds": 31, "message": "no UI frame for 31 s"})
    assert wait_for(lambda: seen) and seen[0]["seconds"] == 31
    text = link.status().text
    assert "hang" in text and text.startswith("game link: anno117") and link.connected
    assert link.reload(livelink.AMBIENT).ok  # the game answers: it lives
    assert link.status().note == "" and "hang" not in link.status().text
    remove()


def test_an_asset_reload_the_engine_confirmed_says_that_placed_objects_keep_their_old_data(link):
    link.game.answers["reload.assets"] = (livelink.DONE, "patched in 3 s", {"confirmed": True, "staleObjects": True})
    notice = linkannounce.announce((livelink.ASSETS,), mod="m")
    assert notice.sent and notice.text.startswith("game reloaded: assets")
    assert "keep the data from before the reload" in notice.text and "save and load" in notice.text
    link.game.answers["reload.assets"] = (livelink.DONE, "", {})
    assert "keep the data" not in linkannounce.announce((livelink.ASSETS,), mod="m").text  # an old game says nothing


def test_a_reload_the_game_found_busy_is_not_called_a_reload(link):
    link.game.answers["reload.assets"] = (livelink.BUSY, "the game is saving: ask again in a moment", {})
    notice = linkannounce.announce((livelink.ASSETS,), mod="m")
    assert "game reloaded" not in notice.text and "the game is saving" in notice.text


def test_the_suite_offers_no_data_reload_of_the_game_for_1800():
    suite = Path(livelink.__file__).parent
    offenders = [p.name for p in suite.glob("*.py") if "reloadData" in p.read_text(encoding="utf-8")]
    assert offenders == []
