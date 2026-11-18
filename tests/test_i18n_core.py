"""The interface language's core (infra/i18n.py): string ids, plurals, places, numbers, the fall-back to English."""

import json

import pytest

from annotoolbox.infra import i18n

ENGLISH = {
    "meta": {"name": "English"},
    "a": {
        "a.open": "Open",
        "a.menu.open": "Open",
        "a.buildings": ["One building", "{n} buildings"],
        "a.only_english": "Only English {x}",
    },
    "b": {"b.saved": "Saved {name}", "b.islands": ["{n} island", "{n} islands"], "b.braces": "a {literal} brace"},
}
GERMAN = {
    "meta": {"name": "Deutsch"},
    "b": {"b.saved": "{name} gespeichert", "b.islands": ["{n} Insel", "{n} Inseln"]},
    "a": {"a.open": "Öffnen", "a.menu.open": "Datei öffnen", "a.buildings": ["Ein Gebäude", "{n} Gebäude"]},
}


@pytest.fixture
def files(tmp_path, monkeypatch):
    (tmp_path / "en.json").write_text(i18n.dump_file(ENGLISH), encoding="utf-8")
    (tmp_path / "de.json").write_text(i18n.dump_file(GERMAN), encoding="utf-8")
    monkeypatch.setattr(i18n, "LANG_DIR", tmp_path)
    i18n.use("en")
    return tmp_path


@pytest.fixture
def german(files):
    i18n.use("de")
    return files


def test_english_is_the_default_and_a_language_file_like_the_others(files):
    assert i18n.current() == "en"
    assert i18n.tr("a.open") == "Open"
    assert i18n.tr("b.saved", name="x") == "Saved x"
    assert i18n.trn("b.islands", 1) == "1 island" and i18n.trn("b.islands", 2) == "2 islands"
    assert i18n.tr("b.braces") == "a {literal} brace"  # without fields the text is as it is in the file
    assert i18n.N_("a.open") == "a.open"
    assert not i18n.missing()


def test_german_looks_up_ids_places_and_counts(german):
    assert i18n.tr("a.open") == "Öffnen"
    assert i18n.tr("a.menu.open") == "Datei öffnen"  # one English word, two meanings: two ids
    assert i18n.tr("b.saved", name="Mia") == "Mia gespeichert"
    assert i18n.trn("b.islands", 1) == "1 Insel" and i18n.trn("b.islands", 5) == "5 Inseln"
    assert i18n.trn("a.buildings", 1) == "Ein Gebäude" and i18n.trn("a.buildings", 3) == "3 Gebäude"
    assert i18n.tr(i18n.N_("a.open")) == "Öffnen"
    assert not i18n.missing()


def test_an_id_a_language_lacks_shows_english_and_is_listed(german):
    assert i18n.tr("a.only_english", x=1) == "Only English 1"
    assert i18n.missing() == {"a.only_english"}
    i18n.use("en")
    assert not i18n.missing() and i18n.tr("a.only_english", x=2) == "Only English 2" and not i18n.missing()


def test_an_unknown_id_and_a_text_that_is_none_pass_as_they_are_and_are_listed(files):
    assert i18n.tr("a.not_there") == "a.not_there"
    assert i18n.tr("Anno 1800") == "Anno 1800"  # a name handed to tr(variable) that is no string id
    assert i18n.missing() == {"a.not_there", "Anno 1800"}


def test_the_english_text_of_an_id_whatever_the_language(german):
    assert i18n.english("a.open") == "Open" and i18n.english("b.saved", name="x") == "Saved x"
    assert i18n.english("a.buildings") == "One building" and i18n.english("no id") == "no id"
    assert not i18n.missing()


def test_an_id_has_a_section_and_a_shape():
    assert i18n.is_id("saves.emperor") and i18n.is_id("islands.menu.file") and i18n.is_id("common.3d_view")
    assert not any(map(i18n.is_id, ("Open", "open", "Saves.emperor", "saves.", "saves.Emperor", "a b.c", "{n} file")))
    assert i18n.section_of("islands.menu.file") == "islands"


def test_use_takes_the_environment_over_the_setting_and_ignores_unknown_codes(german, monkeypatch):
    assert i18n.use("xx") == "en"
    assert i18n.use("DE") == "de"
    monkeypatch.setenv(i18n.ENV_VAR, "en")
    assert i18n.use("de") == "en"


def test_numbers_follow_the_language(german):
    assert i18n.number(1234.5, 1) == "1.234,5"
    assert i18n.number(1234567) == "1.234.567"
    i18n.use("en")
    assert i18n.number(1234.5, 1) == "1,234.5"


def test_places_of_a_format_string():
    assert i18n.places("{a} of {b} {a}") == {"a", "b"}
    assert i18n.places("none") == set()


def test_settings_load_chooses_the_language(tmp_path):
    from annotoolbox.suite.settings import Settings

    path = tmp_path / "s.json"
    Settings(language="de", workspace=str(tmp_path)).save(path)
    assert Settings.load(path).language == "de" and i18n.current() == "de"
    path.write_text(json.dumps({"language": "fr"}), encoding="utf-8")
    assert Settings.load(path).language == "en"


def test_a_language_file_is_sorted_one_entry_per_line_and_found(german):
    text = (german / "de.json").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[1].strip() == '"meta": {' and lines.index('  "a": {') < lines.index('  "b": {')
    assert '    "a.menu.open": "Datei öffnen",' in lines and json.loads(text)["meta"]["name"] == "Deutsch"
    assert i18n.languages() == {"en": "English", "de": "Deutsch"}
    (german / "fr.json").write_text(i18n.dump_file({"meta": {"name": "Français"}}), encoding="utf-8")
    assert list(i18n.languages()) == ["en", "de", "fr"] and i18n.use("fr") == "fr"
    assert i18n.tr("a.open") == "Open"  # a language file that is just begun: everything in English


def test_alternatives_join_in_the_languages_word_order():
    assert i18n.alternatives([]) == "" and i18n.alternatives(["land"]) == "land"
    assert i18n.alternatives(["land", "coast", "water"]) == "land or coast or water"
    i18n.use("de")
    assert i18n.alternatives(["Land", "Küste", "Wasser"]) == "Land oder Küste oder Wasser"


def test_the_language_variable_overrides_the_setting(german, monkeypatch):
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    assert i18n.use("de") == "de"
    monkeypatch.setenv(i18n.ENV_VAR, "en")
    assert i18n.use("de") == "en"
