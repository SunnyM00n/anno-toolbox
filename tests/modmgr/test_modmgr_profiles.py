"""The mod manager's profile files: read and written without losing a line, the profiles folder, profile.txt."""

from __future__ import annotations

import pytest
from tests.modmgr.modmgrfakes import ACTIVE, PROFILE

from annotoolbox.modloader import profilefile as pf
from annotoolbox.modmgr.profiles import CProfileError, CProfileStore, name_problem


@pytest.mark.parametrize("text", [PROFILE, PROFILE.replace("\n", "\r\n"), PROFILE.rstrip("\n"), "", "\n", "a\n\n\n"])
def test_a_profile_round_trips_byte_for_byte(text):
    assert pf.CProfileText(text).to_text() == text


def test_the_games_own_layout_is_read():
    p = pf.CProfileText(PROFILE)
    assert [(e.mod_id, e.is_on, e.note) for e in p.entries()] == [
        ("alpha", False, ""),
        ("beta", True, ""),
        ("gamma", False, "not installed"),
    ]  # the commented DisplayName line is a comment, not a mod
    assert p.enable_new is False and p.display_name == "Mod Profile"
    assert p.state("beta") is True and p.state("alpha") is False and p.state("nope") is None
    assert p.to_profile("x").on == ["beta"] and p.to_profile().off == ["alpha", "gamma"]


def test_switching_a_mod_touches_only_its_line_and_keeps_the_note():
    p = pf.CProfileText(PROFILE)
    assert p.set_state("gamma", True) and p.set_state("beta", False)
    assert p.set_state("beta", False) is False  # already so
    lines = p.to_text().splitlines()
    assert "gamma # not installed" in lines and "# beta" in lines
    assert [ln for ln in PROFILE.splitlines() if ln not in ("beta", "# gamma # not installed")] == [
        ln for ln in lines if ln not in ("# beta", "gamma # not installed")
    ]


def test_a_mod_the_profile_does_not_list_is_added_after_the_last_mod_line():
    p = pf.CProfileText(PROFILE)
    p.set_state("delta", True)
    p.set_state("eps", False)
    assert p.to_text().splitlines()[-2:] == ["delta", "# eps"]
    q = pf.CProfileText("## only a comment\n")
    q.set_state("a", True)
    assert q.to_text() == "## only a comment\na\n"


def test_remove_enable_new_and_the_display_name():
    p = pf.CProfileText(PROFILE)
    assert p.remove("alpha") and p.state("alpha") is None and not p.remove("alpha")
    assert p.set_enable_new(True) and p.enable_new is True and "\nEnableNewMods\n" in p.to_text()
    p.set_display_name("Mine")
    assert p.display_name == "Mine"
    bare = pf.CProfileText("a\n")
    bare.set_enable_new(False)
    assert bare.enable_new is False


def test_hash_spellings_are_all_switched_off_lines():
    p = pf.CProfileText("#a\n#  b # c\n  # d\n")
    assert [(e.mod_id, e.is_on) for e in p.entries()] == [("a", False), ("b", False), ("d", False)]
    p.set_state("a", True)
    p.set_state("b", True)
    assert p.to_text() == "a\nb # c\n  # d\n"


def test_profile_txt_keeps_its_comments_and_its_missing_final_newline():
    a = pf.CActiveFile(ACTIVE)
    assert a.name == "default" and a.to_text() == ACTIVE
    a.set_name("other")
    assert a.to_text() == ACTIVE.replace("ActiveProfile default", "ActiveProfile other")
    a.set_name("")
    assert a.name == "" and a.to_text().endswith("ActiveProfile ")
    assert pf.CActiveFile("## nothing\n").name is None


def test_a_new_profile_has_the_games_layout():
    p = pf.new_profile("Mine", on_ids=["a", "b"], off_ids=["c"], enable_new=True)
    text = p.to_text()
    assert text.startswith("## Download mods at: ") and "\nEnableNewMods\nDisplayName Mine\n\na\nb\n# c\n" in text
    assert p.display_name == "Mine" and p.ids(True) == ["a", "b"] and p.ids(False) == ["c"] and p.enable_new is True
    assert pf.CProfileText(pf.new_profile("x", eol="\r\n").to_text()).eol == "\r\n"


def test_names_a_file_cannot_hold_are_refused():
    assert name_problem("good name 1") == ""
    for bad in ("", " x", "x ", "a/b", "a:b", "x" * 65, "con", "nul.txt", "dot."):
        assert name_problem(bad), bad


@pytest.fixture
def store(tmp_path):
    return CProfileStore(tmp_path / "mods")


def test_create_copy_rename_delete(store):
    assert store.names() == [] and store.active() == "default"
    store.create("Beta", enabled_ids=["a"])
    store.create("alpha", disabled_ids=["b"], enable_new=True)
    assert store.names() == ["alpha", "Beta"]
    store.copy("Beta", "Beta copy")
    assert store.read("Beta copy").ids(True) == ["a"] and store.read("Beta copy").display_name == "Beta copy"
    store.rename("Beta copy", "Gamma")
    assert store.names() == ["alpha", "Beta", "Gamma"] and not store.exists("Beta copy")
    assert store.read("Gamma").display_name == "Gamma"
    store.rename("Gamma", "gamma")  # only the case
    assert "gamma" in store.names() and "Gamma" not in store.names()
    store.delete("gamma")
    assert store.names() == ["alpha", "Beta"]
    assert not list(store.folder.glob(".*.tmp"))  # nothing half written is left


def test_refusals_say_why(store):
    store.create("one")
    with pytest.raises(CProfileError, match="exists already"):
        store.create("ONE")
    with pytest.raises(CProfileError, match="cannot hold"):
        store.create("a/b")
    with pytest.raises(CProfileError, match="cannot be read"):
        store.read("missing")
    with pytest.raises(CProfileError, match="no profile named"):
        store.set_active("missing")


def test_the_active_profile_is_written_in_place_and_followed_by_a_rename(store):
    store.create("one")
    store.create("two")
    store.set_active("one")
    assert (store.m_mods_dir / "profile.txt").read_text().rstrip().endswith("ActiveProfile one")
    assert (store.m_mods_dir / "profile.txt").read_text().startswith('## Change the value after "ActiveProfile "')
    with pytest.raises(CProfileError, match="active profile"):
        store.delete("one")
    store.rename("one", "uno")
    assert store.active() == "uno"
    store.set_active("")
    assert store.active() == ""
    store.delete("uno")


def test_a_profile_txt_with_a_txt_suffix_and_a_game_written_one_are_read(store):
    store.m_mods_dir.mkdir()
    (store.m_mods_dir / "profile.txt").write_text(ACTIVE.replace("default", "x.txt"), encoding="utf-8")
    assert store.active() == "x"


def test_a_mod_listed_on_and_off_counts_by_its_last_line_everywhere():
    p = pf.CProfileText("EnableNewMods\nfoo\n# foo\nbar\n")
    assert p.state("foo") is False
    prof = p.to_profile()
    assert "foo" in prof.off and "foo" not in prof.on and prof.on == ["bar"]
    from annotoolbox.modloader import mods

    read = mods.read_profile("EnableNewMods\nfoo\n# foo\n")
    assert read.off == ["foo"] and read.on == []


def test_two_active_profile_lines_the_last_counts_and_a_change_reaches_both():
    a = pf.CActiveFile("ActiveProfile first\nActiveProfile second\n")
    assert a.name == "second" and a.profile == "second"
    a.set_name("b")
    assert a.name == "b" and a.to_text() == "ActiveProfile b\nActiveProfile b\n"
    assert pf.CActiveFile("## no line\n").profile == pf.DEFAULT and pf.CActiveFile("ActiveProfile \n").profile == ""


def test_a_save_keeps_the_mark_the_line_ends_and_the_encoding_of_every_untouched_line():
    mixed = b"\xef\xbb\xbfEnableNewMods\r\na\nb\r\n# c\n"
    p = pf.CProfileText.from_bytes(mixed)
    assert p.to_bytes() == mixed
    p.set_state("a", False)
    assert p.to_bytes() == b"\xef\xbb\xbfEnableNewMods\r\n# a\nb\r\n# c\n"
    ansi = "DisplayName caf\xe9\r\n# note \xe9\r\nx\r\n".encode("cp1252")
    q = pf.CProfileText.from_bytes(ansi)
    q.set_state("x", False)
    assert q.to_bytes() == "DisplayName caf\xe9\r\n# note \xe9\r\n# x\r\n".encode("cp1252")
    q.set_display_name("Łodz")  # a letter cp1252 lacks: the file becomes UTF-8, nothing is lost
    assert "Łodz".encode() in q.to_bytes()


def test_the_store_reads_and_writes_bytes_without_changing_untouched_lines(store):
    store.create("p")
    path = store.path("p")
    path.write_bytes(b"EnableNewMods\r\nalpha\nbeta # c\xe9\r\n")
    text = store.read("p")
    text.set_state("alpha", False)
    store.save("p", text)
    assert path.read_bytes() == b"EnableNewMods\r\n# alpha\nbeta # c\xe9\r\n"
