"""The mod manager's archive-game side: what the editor installed, loose-file mods and the engine.ini switch (synthetic
folders; the installed games are only read, and only when ANNO*_DIR names them), and the Installs page offscreen."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from annotoolbox.modmgr import archivegames as ag
from annotoolbox.othergames import delivery2205
from annotoolbox.suite.settings import CSettings

INI = '{\n    "FileSystem": {\n        "InitFileVersion": 7,\n        "PreferLocalFiles": false\n    }\n}\n'
KEYS = {"anno2205": "ANNO2205_DIR", "anno2070": "ANNO2070_DIR", "anno1404": "ANNO1404_DIR", "anno1701": "ANNO1701_DIR"}


def make_game(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    (game / "data" / "config").mkdir(parents=True)
    (game / "data" / "config" / "tuning.xml").write_text("original", encoding="utf-8")
    return game


def make_mod(tmp_path: Path, name: str = "pack") -> Path:
    mod = tmp_path / name
    (mod / "data" / "config").mkdir(parents=True)
    (mod / "data" / "config" / "tuning.xml").write_text("changed", encoding="utf-8")
    (mod / "data" / "new" / "deep").mkdir(parents=True)
    (mod / "data" / "new" / "deep" / "x.txt").write_text("new", encoding="utf-8")
    return mod


def loose(tmp_path: Path, **kw) -> ag.CLooseGame:
    ini = tmp_path / "engine.ini"
    if not ini.exists():
        ini.write_text(INI, encoding="utf-8")
    return ag.CLooseGame("anno2205", make_game(tmp_path) if not (tmp_path / "game").exists() else tmp_path / "game",
                        tmp_path / "ws", ini=ini, **kw)  # fmt: skip


def test_loose_actions_stay_off_until_the_owner_confirms_the_game(tmp_path):
    g = loose(tmp_path)
    assert not g.confirmed and g.switch_state() is False
    with pytest.raises(ag.CLooseError, match="not confirmed"):
        g.set_switch(True)
    with pytest.raises(ag.CLooseError, match="not confirmed"):
        g.install_mod(make_mod(tmp_path))
    assert (tmp_path / "engine.ini").read_text(encoding="utf-8") == INI
    ag.LOOSE_CONFIRMED["anno2205"] = "2026-11-13"
    try:
        assert loose(tmp_path).confirmed
    finally:
        del ag.LOOSE_CONFIRMED["anno2205"]


def test_the_switch_changes_only_the_value_and_keeps_the_games_file_once(tmp_path):
    g = loose(tmp_path, force=True)
    g.set_switch(True)
    assert g.switch_state() is True
    assert (tmp_path / "engine.ini").read_text(encoding="utf-8") == INI.replace("false", "true")
    g.set_switch(False)
    g.set_switch(True)
    assert (g.m_root / ag.INI_KEPT).read_text(encoding="utf-8") == INI  # the first state, not a later one
    assert ag.with_flag("PreferLocalFiles = 0\n", True) == "PreferLocalFiles = 1\n"
    assert ag.with_flag("<PreferLocalFiles>1</PreferLocalFiles>", False) == "<PreferLocalFiles>0</PreferLocalFiles>"
    with pytest.raises(ag.CLooseError, match="does not name"):
        ag.with_flag("{}", True)


def test_a_loose_mod_goes_in_keeps_what_it_replaced_and_comes_out(tmp_path):
    g = loose(tmp_path, force=True)
    mod = make_mod(tmp_path)
    got = g.install_mod(mod)
    tuning = g.m_install / "data" / "config" / "tuning.xml"
    assert tuning.read_text(encoding="utf-8") == "changed" and (g.m_install / "data/new/deep/x.txt").is_file()
    assert got.name == "pack" and got.replaced == 1 and [m.name for m in g.mods()] == ["pack"]
    g.install_mod(mod)  # again: replaced in place, the game's own file still the one kept
    assert [m.name for m in g.mods()] == ["pack"]
    assert g.remove_mod("pack") == []
    assert tuning.read_text(encoding="utf-8") == "original"
    assert not (g.m_install / "data" / "new").exists() and g.mods() == []


def test_a_file_changed_since_the_install_stays_and_is_named(tmp_path):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    (g.m_install / "data/new/deep/x.txt").write_text("hand edited", encoding="utf-8")
    assert g.remove_mod("pack") == ["data/new/deep/x.txt"]
    assert (g.m_install / "data/new/deep/x.txt").read_text(encoding="utf-8") == "hand edited"


def test_two_mods_never_share_a_path_and_paths_stay_inside_the_game(tmp_path):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    with pytest.raises(ag.CLooseError, match="belongs to the loose mod pack"):
        g.install_mod(make_mod(tmp_path, "other"))
    for bad in ("../x", "/x", "C:/x", "a//b", "a/./b"):
        with pytest.raises(ag.CLooseError, match="inside"):
            ag._safe(bad)
    with pytest.raises(ag.CLooseError, match="plain name"):
        g.install_mod(make_mod(tmp_path, "ok"), name="../evil")
    with pytest.raises(ag.CLooseError, match="holds no files"):
        (tmp_path / "empty").mkdir()
        g.install_mod(tmp_path / "empty")


def link_dir(link: Path, target: Path) -> None:
    """A directory junction (Windows, no privilege needed) or a symlink."""
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if done.returncode:
            pytest.skip("no junction here")
    else:
        link.symlink_to(target, target_is_directory=True)


def test_names_windows_reads_differently_are_refused():
    for bad in ("data/a:stream", "data/a.", "data/a ", "data/a|b", "data/a?b", "data/<x>"):
        with pytest.raises(ag.CLooseError, match="inside"):
            ag._safe(bad)
    assert ag._safe("data\\config\\tuning.xml") == "data/config/tuning.xml"


def test_a_link_inside_the_install_cannot_lead_a_file_out_of_it(tmp_path):
    g = loose(tmp_path, force=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link_dir(g.m_install / "data" / "out", outside)
    mod = tmp_path / "evil"
    (mod / "data" / "out").mkdir(parents=True)
    (mod / "data" / "out" / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ag.CLooseError, match="inside"):
        g.install_mod(mod)
    assert list(outside.iterdir()) == [] and g.mods() == []


def test_an_install_that_fails_half_way_puts_everything_back(tmp_path):
    g = loose(tmp_path, force=True)
    (g.m_install / "data" / "new" / "deep" / "x.txt").mkdir(parents=True)  # a folder where the mod has a file
    with pytest.raises(ag.CLooseError, match="folder in the game"):
        g.install_mod(make_mod(tmp_path))
    tuning = g.m_install / "data" / "config" / "tuning.xml"
    assert tuning.read_text(encoding="utf-8") == "original" and g.mods() == []
    assert not (g.m_root / "originals" / "pack").exists()
    assert not list(g.m_install.rglob("*.part"))


def test_the_games_file_comes_back_even_when_the_mods_file_was_deleted(tmp_path):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    tuning = g.m_install / "data" / "config" / "tuning.xml"
    tuning.unlink()
    assert g.remove_mod("pack") == [] and tuning.read_text(encoding="utf-8") == "original"


def test_a_changed_file_keeps_its_original_and_a_lost_original_keeps_the_file(tmp_path):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    tuning = g.m_install / "data" / "config" / "tuning.xml"
    tuning.write_text("hand edited", encoding="utf-8")
    assert g.remove_mod("pack") == ["data/config/tuning.xml"] and tuning.read_text(encoding="utf-8") == "hand edited"
    kept = g.m_root / "originals" / "pack" / "data" / "config" / "tuning.xml"
    assert kept.read_text(encoding="utf-8") == "original"  # the game's file is not thrown away with the manifest
    g.install_mod(make_mod(tmp_path, "pack2"), name="again")  # (its own name: "again" owns nothing of pack)
    (g.m_root / "originals" / "again" / "data" / "config" / "tuning.xml").unlink()
    assert g.remove_mod("again") == ["data/config/tuning.xml"]
    assert tuning.read_text(encoding="utf-8") == "changed"  # not deleted: there is nothing to put in its place


def test_the_switch_keeps_the_encoding_and_puts_the_file_back_when_it_does_not_take(tmp_path, monkeypatch):
    ini = tmp_path / "engine.ini"
    raw = b"; caf\xe9\r\nPreferLocalFiles = 0\r\n"  # not UTF-8
    ini.write_bytes(raw)
    g = loose(tmp_path, force=True)
    g.set_switch(True)
    assert ini.read_bytes() == raw.replace(b"= 0", b"= 1") and not list(tmp_path.glob("*.part"))
    ini.write_bytes(ini.read_bytes() + b"; added by hand\r\n")
    mine = ini.read_bytes()
    monkeypatch.setattr(ag, "prefer_local", lambda _p: None)
    with pytest.raises(ag.CLooseError, match="did not take"):
        g.set_switch(False)
    assert ini.read_bytes() == mine  # as this call found it, not the first kept state
    assert (g.m_root / ag.INI_KEPT).read_bytes() == raw


def test_the_rows_say_per_game_what_is_installed(tmp_path):
    ws = tmp_path / "ws"
    game = tmp_path / "g2205"
    (game / "Bin" / "Win64").mkdir(parents=True)
    (game / "Bin" / "Win64" / "Anno2205.exe").write_bytes(b"")
    s = CSettings(anno2205=str(game), patch2205=str(game), workspace=str(ws))
    by = {r.key: r for r in ag.rows(s)}
    assert [r.key for r in ag.rows(s)] == list(ag.GAMES)
    assert by["anno2205"].state == "clean" and by["anno2070"].state == "no folder"
    (game / "maindata").mkdir()
    (game / "maindata" / "anno-toolbox-sessions.rda").write_bytes(b"x")
    kept = delivery2205.backup_file(game)  # outside the workspace, shared with the UI editor
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"db")
    row = {r.key: r for r in ag.rows(s)}["anno2205"]
    assert row.installed and [f.name for f in row.files] == ["anno-toolbox-sessions.rda", "file.db"]
    assert row.kept == [kept] and row.restore_label


def test_restore_is_the_deliverys_own(tmp_path, monkeypatch):
    from annotoolbox.editor import archivedelivery as ad

    seen = []
    fake = dataclasses.replace(ad.ISLAND_DELIVERIES["anno2070"], restore=lambda settings, log: seen.append(1) or True)
    monkeypatch.setitem(ad.ISLAND_DELIVERIES, "anno2070", fake)
    assert ag.restore(CSettings(workspace=str(tmp_path)), "anno2070") is True and seen == [1]


@pytest.mark.parametrize("key", KEYS)
def test_installed_games_are_only_read(key, tmp_path):
    folder = os.environ.get(KEYS[key])
    if not folder:
        pytest.skip(f"needs an installed game ({KEYS[key]})")
    s = CSettings(workspace=str(tmp_path), **{key: folder, "patch2205": folder if key == "anno2205" else ""})
    row = {r.key: r for r in ag.rows(s)}[key]
    assert row.state in ("clean", "installed")
    ini = ag.engine_ini(key)
    if ini is not None:
        assert ag.prefer_local(ini) in (True, False, None)


@pytest.fixture
def installs_page(tmp_path):
    """``installs_page(**settings)`` makes the Installs page offscreen for those settings (a workspace is given)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.modmgr.installspage import CInstallsPage
    from annotoolbox.suite.background import CBackground

    qt = QApplication.instance() or QApplication([])
    yield lambda **kw: CInstallsPage(
        CSettings(workspace=str(tmp_path / "ws"), **kw), CBackground(), is_synchronous=True
    )
    qt.processEvents()


def test_the_installs_page_lists_the_games_and_gates_the_loose_actions(tmp_path, installs_page):
    page = installs_page(anno2205=str(make_game(tmp_path)))
    assert page.m_installs.topLevelItemCount() == 4 and page.m_mods.topLevelItemCount() == 0
    assert not page.m_switch_button.isEnabled() and not page.m_add_button.isEnabled()
    assert "Not confirmed" in page.m_loose_state.text() or "nicht bestätigt" in page.m_loose_state.text()
    ag.LOOSE_CONFIRMED["anno2205"] = "test"
    try:
        page.show_loose()
        assert page.m_add_button.isEnabled()
    finally:
        del ag.LOOSE_CONFIRMED["anno2205"]


@pytest.mark.parametrize("text", ["[1]", '"x"', '{"name": "m", "installed": "", "files": 5, "replaced": {}}', "{"])
def test_a_manifest_that_is_not_one_is_left_out_of_the_list(tmp_path, text):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    (g.m_root / "mods" / "broken.json").write_text(text, encoding="utf-8")
    assert [m.name for m in g.mods()] == ["pack"]


def test_a_path_in_other_letter_case_is_the_same_file_of_the_game(tmp_path):
    g = loose(tmp_path, force=True)
    g.install_mod(make_mod(tmp_path))
    other = tmp_path / "other"
    (other / "Data" / "New" / "deep").mkdir(parents=True)
    (other / "Data" / "New" / "deep" / "X.TXT").write_text("theirs", encoding="utf-8")
    with pytest.raises(ag.CLooseError, match="belongs to the loose mod pack"):
        g.install_mod(other)
    assert (tmp_path / "game" / "data" / "new" / "deep" / "x.txt").read_text(encoding="utf-8") == "new"


def test_a_game_without_a_folder_set_offers_no_loose_actions(installs_page, monkeypatch):
    monkeypatch.setitem(ag.LOOSE_CONFIRMED, "anno2205", "test")
    page = installs_page()
    assert not page.m_add_button.isEnabled() and not page.m_remove_button.isEnabled()  # no folder is not "this folder"
