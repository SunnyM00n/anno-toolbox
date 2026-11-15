"""The interface language's core (infra/i18n.py): marking, plurals, places, numbers, the fall-back to English."""

import json

import pytest

from annotoolbox.infra import i18n


@pytest.fixture
def german(tmp_path, monkeypatch):
    sections = {
        "meta": {"name": "Deutsch"},
        "b": {"Saved {name}": "{name} gespeichert", "{n} island": ["{n} Insel", "{n} Inseln"]},
        "a": {"Open": "Öffnen", "menu|Open": "Datei öffnen", "One building": ["Ein Gebäude", "{n} Gebäude"]},
    }
    (tmp_path / "de.json").write_text(i18n.dump_file(sections), encoding="utf-8")
    monkeypatch.setattr(i18n, "LANG_DIR", tmp_path)
    i18n.use("de")
    return tmp_path


def test_english_is_the_default_and_the_text_itself():
    assert i18n.current() == "en"
    assert i18n.tr("Open") == "Open"
    assert i18n.tr("Saved {name}", name="x") == "Saved x"
    assert i18n.trn("{n} island", "{n} islands", 1) == "1 island"
    assert i18n.trn("{n} island", "{n} islands", 2) == "2 islands"
    assert i18n.N_("Grid") == "Grid"


def test_german_looks_up_context_places_and_counts(german):
    assert i18n.tr("Open") == "Öffnen"
    assert i18n.tr("Open", context="menu") == "Datei öffnen"
    assert i18n.tr("Saved {name}", name="Mia") == "Mia gespeichert"
    assert i18n.trn("{n} island", "{n} islands", 1) == "1 Insel"
    assert i18n.trn("{n} island", "{n} islands", 5) == "5 Inseln"
    assert i18n.trn("One building", "{n} buildings", 1) == "Ein Gebäude"
    assert i18n.trn("One building", "{n} buildings", 3) == "3 Gebäude"
    assert not i18n.missing()


def test_a_missing_entry_shows_english_and_is_listed(german):
    assert i18n.tr("Not there {x}", x=1) == "Not there 1"
    assert i18n.missing() == {"Not there {x}"}
    i18n.use("en")
    assert not i18n.missing()


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
    assert '    "Open": "Öffnen",' in lines and json.loads(text)["meta"]["name"] == "Deutsch"
    assert i18n.languages() == {"en": "English", "de": "Deutsch"}
    (german / "fr.json").write_text(i18n.dump_file({"meta": {"name": "Français"}}), encoding="utf-8")
    assert list(i18n.languages()) == ["en", "de", "fr"] and i18n.use("fr") == "fr"


def test_alternatives_join_in_the_languages_word_order(german, tmp_path):
    assert i18n.alternatives([]) == "" and i18n.alternatives(["land"]) == "land"
    assert i18n.alternatives(["land", "coast"]) == "land or coast"  # the catalog lacks it: English shows
    assert i18n.missing() == {"{a} or {b}"}
    sections = {"meta": {"name": "x"}, "a": {"{a} or {b}": "{a} oder {b}"}}
    (tmp_path / "de.json").write_text(i18n.dump_file(sections), encoding="utf-8")
    i18n.use("de")
    assert i18n.alternatives(["Land", "Küste", "Wasser"]) == "Land oder Küste oder Wasser"
    i18n.use("en")
    assert i18n.alternatives(["land", "coast", "water"]) == "land or coast or water"


def test_the_language_variable_overrides_the_setting(german, monkeypatch):
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    assert i18n.use("de") == "de"
    monkeypatch.setenv(i18n.ENV_VAR, "en")
    assert i18n.use("de") == "en"
