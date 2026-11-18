"""The suite's settings: what is missing for the games, and where the data folder is, on temp folders only."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from annotoolbox.paths import local_data_dir
from annotoolbox.suite import settings as st


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_the_two_main_games_are_checked_by_their_install_records(tmp_path):
    s = st.Settings(anno117=str(tmp_path / "117"), anno2070=str(tmp_path / "2070"), workspace=str(tmp_path / "w"))
    found = s.problems()
    assert any("Anno 117 not found" in p and "Anno117.exe" in p for p in found)
    assert any("Anno 2070 not found" in p and "Anno5.exe" in p and "data0.rda" in p for p in found)
    touch(tmp_path / "117" / "Bin" / "Win64" / "Anno117.exe")
    touch(tmp_path / "2070" / "Anno5.exe")
    assert any("Anno 2070 not found" in p for p in s.problems())  # 2205 has an exe and data0.rda too: both are needed
    touch(tmp_path / "2070" / "maindata" / "data0.rda")
    s.mods = str(tmp_path / "mods")
    assert s.problems() == []
    assert st.is_2070(tmp_path / "2070") and not st.is_2070(tmp_path / "117")


def test_the_settings_folder_is_the_tools_data_folder():
    assert st.appdata() == local_data_dir()


def test_a_cut_short_settings_file_is_set_aside_and_a_bom_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "detect", dict)
    path = tmp_path / st.SETTINGS_FILE
    path.write_bytes(b'{"theme": "dark", "work')  # a crash in the middle of an earlier save
    s = st.Settings.load(path)
    assert s.theme == "system" and not path.exists()
    assert [p.name.startswith(f"{st.SETTINGS_FILE}.unreadable-") for p in tmp_path.iterdir()] == [True]
    path.write_bytes(b'\xef\xbb\xbf{"theme": "dark"}')  # saved by an editor that writes a byte order mark
    assert st.Settings.load(path).theme == "dark"


def test_a_value_of_another_kind_in_the_file_is_left_at_its_default(tmp_path, monkeypatch):
    """A file edited by hand: ``"apps": null`` stopped the window at its first ``settings.apps.get``."""
    monkeypatch.setattr(st, "detect", dict)
    path = tmp_path / st.SETTINGS_FILE
    path.write_text('{"apps": null, "layout": [], "hardware": "no", "theme": "dark", "more": 1}', encoding="utf-8")
    s = st.Settings.load(path)
    assert (s.apps, s.layout, s.hardware, s.theme) == ({}, {}, True, "dark")


def test_a_folder_that_cannot_be_listed_is_no_candidate(tmp_path, monkeypatch):
    """A games folder of another user (or a drive without a disc) raised out of the search, and the window with it."""
    (tmp_path / "Anno 1404").mkdir()
    (tmp_path / "Other").mkdir()
    assert st.anno_folders(tmp_path) == [tmp_path / "Anno 1404"]
    assert st.anno_folders(tmp_path / "absent") == []

    def denied(_self):
        raise PermissionError("access is denied")

    monkeypatch.setattr(Path, "iterdir", denied)
    monkeypatch.setattr(st, "registry_installs", list)
    assert st.anno_folders(tmp_path) == [] and st.candidates() == []


def test_the_settings_are_saved_whole_or_not_at_all(tmp_path, monkeypatch):
    path = tmp_path / st.SETTINGS_FILE
    st.Settings(theme="dark").save(path)

    def full(_fd):
        raise OSError("the disk is full")

    monkeypatch.setattr(os, "fsync", full)  # the new copy fails before it is complete on disk
    with pytest.raises(OSError):
        st.Settings(theme="light").save(path)
    assert '"theme": "dark"' in path.read_text(encoding="utf-8") and [p.name for p in tmp_path.iterdir()] == [path.name]


def test_the_projects_and_the_ui_editors_files_are_saved_whole_or_not_at_all(tmp_path, monkeypatch):
    from annotoolbox.suite.project import Project
    from annotoolbox.uiedit.viewmodels import FieldNames, Samples

    project, names, samples = (
        Project(tmp_path / "p", mod_id="m"),
        FieldNames(tmp_path / "n.json"),
        Samples(tmp_path / "s.json"),
    )
    names.set(1, "A.b")
    samples.values["k"] = 1
    before = {}
    for item, path in ((project, tmp_path / "p" / "project.json"), (names, names.path), (samples, samples.path)):
        item.save()
        before[path] = path.read_bytes()

    def full(_fd):
        raise OSError("the disk is full")

    monkeypatch.setattr(os, "fsync", full)
    project.name, names.names[1], samples.values["k"] = "other", "C.d", 2
    for item in (project, names, samples):
        with pytest.raises(OSError):
            item.save()
    assert all(path.read_bytes() == data for path, data in before.items())


def test_the_look_for_the_games_is_made_once_a_day_and_when_asked(tmp_path, monkeypatch):
    """Few people have every game: the look over the registry and the drives ran at every start of every app."""
    from datetime import date

    looks: list[int] = []
    found: dict[str, str] = {}
    monkeypatch.setattr(st, "detect", lambda: looks.append(1) or dict(found))
    path, day = tmp_path / st.SETTINGS_FILE, date(2026, 11, 17)
    first = st.Settings.load(path, day)
    assert looks == [1] and first.last_game_scan == "2026-11-17" and first.anno1800 == ""
    first.save(path)
    found["anno1800"] = "D:/Anno 1800"  # installed meanwhile
    assert st.Settings.load(path, day).anno1800 == "" and looks == [1]  # the same day: no second look
    asked = st.Settings.load(path, day)
    asked.find_games()  # the setup page: now
    assert asked.anno1800 == "D:/Anno 1800" and looks == [1, 1]
    assert st.Settings.load(path, date(2026, 11, 18)).anno1800 == "D:/Anno 1800" and looks == [1, 1, 1]
    full = st.Settings(mods="m", **dict.fromkeys(st.installs.GAMES, "x"))
    full.find_games()
    assert looks == [1, 1, 1]  # nothing unset: nothing to look for
