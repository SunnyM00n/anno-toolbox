"""Anno 2205's archive index as one delivery (othergames/delivery2205.py): the edited sessions and the changed UI
install and uninstall in any order, and the index always points at what is installed."""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest
from tests.othergames.othergamesfakes import MOVIE, index_for

from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.formats.rda import CRdaArchive
from annotoolbox.othergames import delivery2205, ui2205
from annotoolbox.othergames.index2205 import FILE_DB, CArchiveIndex

SESSION = "data/sessions/maps/moon/moon.a7m|gamedata.data"
LOG = [].append


def _game(tmp_path: Path) -> tuple[Path, bytes]:
    game = tmp_path / "game"
    (game / "maindata").mkdir(parents=True)
    own = index_for([MOVIE, SESSION])
    (game / FILE_DB).write_bytes(own)
    return game, own


def _builds(tmp_path: Path, own: bytes) -> tuple[Path, Path]:
    """The UI editor's build and the island editor's, each in its own folder (each with the index it made)."""
    ui = tmp_path / "ui" / ui2205.OUT_NAME
    ui2205.build({MOVIE: b"changed movie"}, own, ui, LOG)
    sessions = tmp_path / "islands" / a7m_edit2205.OUT_NAME
    for rel, data in a7m_edit2205.pack({SESSION: b"edited session"}, own).items():
        (sessions / rel).parent.mkdir(parents=True, exist_ok=True)
        (sessions / rel).write_bytes(data)
    return ui, sessions


def _reads(game: Path, path: str) -> bytes:
    """What the game reads for ``path``: the bytes its index entry points at (in an archive of ours), else the
    archive the entry names."""
    index = CArchiveIndex((game / FILE_DB).read_bytes())
    entry, names = index.entry(path), index.archive_names()
    archive = names[entry.archive]
    if archive not in delivery2205.ARCHIVES:
        return archive.encode()
    member = CRdaArchive(game / archive).entries[path]
    assert (entry.position, entry.size) == (member.offset, member.size)  # the index names the member's bytes
    return CRdaArchive(game / archive).read(path)


def _check(game: Path, ui: bool, sessions: bool) -> None:
    index = CArchiveIndex((game / FILE_DB).read_bytes())
    for archive in delivery2205.listed((game / FILE_DB).read_bytes()):
        assert (game / archive).is_file(), f"the index points at {archive}, which is not there"
    assert _reads(game, MOVIE) == (b"changed movie" if ui else b"maindata/data0.rda")
    assert _reads(game, SESSION) == (b"edited session" if sessions else b"maindata/data0.rda")
    assert (ui2205.ARCHIVE in index.archive_names()) == ui
    assert (a7m_edit2205.ARCHIVE in index.archive_names()) == sessions


@pytest.mark.parametrize("first", ["ui", "sessions"])
@pytest.mark.parametrize("out_first", ["ui", "sessions"])
def test_ui_and_sessions_install_and_uninstall_in_any_order(tmp_path: Path, first: str, out_first: str):
    game, own = _game(tmp_path)
    ui, sessions = _builds(tmp_path, own)
    steps = {
        "ui": (lambda: ui2205.install(ui, game, log=LOG), lambda: ui2205.uninstall(game, log=LOG)),
        "sessions": (
            lambda: a7m_edit2205.install(sessions, game, log=LOG),
            lambda: a7m_edit2205.uninstall(game, log=LOG),
        ),
    }
    second = "sessions" if first == "ui" else "ui"
    steps[first][0]()
    steps[second][0]()
    _check(game, ui=True, sessions=True)
    assert delivery2205.backup_file(game).read_bytes() == own  # the game's own, never one of ours
    out_second = "sessions" if out_first == "ui" else "ui"
    assert steps[out_first][1]()
    _check(game, ui=out_first != "ui", sessions=out_first != "sessions")
    assert not (game / (ui2205.ARCHIVE if out_first == "ui" else a7m_edit2205.ARCHIVE)).exists()
    assert steps[out_second][1]()
    assert (game / FILE_DB).read_bytes() == own and not delivery2205.backup_file(game).exists()


def test_install_ui_install_islands_uninstall_ui_uninstall_islands(tmp_path: Path):
    """The order the bug check named: the index used to point at the removed UI archive at the end."""
    game, own = _game(tmp_path)
    ui, sessions = _builds(tmp_path, own)
    ui2205.install(ui, game, log=LOG)
    a7m_edit2205.install(sessions, game, log=LOG)
    ui2205.uninstall(game, log=LOG)
    _check(game, ui=False, sessions=True)
    a7m_edit2205.uninstall(game, log=LOG)
    assert (game / FILE_DB).read_bytes() == own
    assert not any((game / a).exists() for a in delivery2205.ARCHIVES)


def test_a_repaired_index_is_kept_afresh_and_a_stray_archive_taken_out(tmp_path: Path):
    """The launcher's file check puts the game's own index back: the next install keeps that one, not the old copy."""
    game, own = _game(tmp_path)
    ui, sessions = _builds(tmp_path, own)
    ui2205.install(ui, game, log=LOG)
    repaired = index_for([MOVIE, SESSION, "data/patched.txt"])  # a game update in between
    (game / FILE_DB).write_bytes(repaired)
    assert delivery2205.uninstall(game, ui2205.ARCHIVE, LOG) and not (game / ui2205.ARCHIVE).exists()
    a7m_edit2205.install(sessions, game, log=LOG)
    assert delivery2205.backup_file(game).read_bytes() == repaired
    a7m_edit2205.uninstall(game, log=LOG)
    assert (game / FILE_DB).read_bytes() == repaired


def test_every_order_of_two_installs_and_two_uninstalls_ends_with_the_games_own_index(tmp_path: Path):
    game, own = _game(tmp_path)
    ui, sessions = _builds(tmp_path, own)
    actions = {
        "+ui": lambda: ui2205.install(ui, game, log=LOG),
        "+sessions": lambda: a7m_edit2205.install(sessions, game, log=LOG),
        "-ui": lambda: ui2205.uninstall(game, log=LOG),
        "-sessions": lambda: a7m_edit2205.uninstall(game, log=LOG),
    }
    for order in permutations(actions):
        if order.index("+ui") > order.index("-ui") or order.index("+sessions") > order.index("-sessions"):
            continue
        for name in order:
            actions[name]()
            for archive in delivery2205.listed((game / FILE_DB).read_bytes()):
                assert (game / archive).is_file(), (order, name)
        assert (game / FILE_DB).read_bytes() == own, order
