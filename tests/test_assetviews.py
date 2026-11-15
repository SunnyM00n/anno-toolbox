"""Saved balancing views (assetedit/views.py) and the balancing panel's use of them, the game's own value of a changed
cell, and the printed report and word counts of an asset (assetedit/report.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest
from tests.fakes import asset_game

from annotoolbox.assetedit import balancing
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.views import ViewStore


def test_a_view_is_saved_listed_replaced_and_removed(tmp_path):
    path = tmp_path / "asset-views" / "anno117.json"
    store = ViewStore.load(path)
    assert store.names == []
    store.put("Costs", ["Building/Upkeep", " Product/Price "], "House")
    store.put("alpha", ["Standard/Name"])
    store.put("Costs", ["Building/Upkeep"], "House")  # the same name: replaced
    store.save()
    again = ViewStore.load(path)
    assert again.names == ["alpha", "Costs"] and again.views["Costs"].columns == ["Building/Upkeep"]
    assert again.views["Costs"].label() == "Costs (House)" and again.views["alpha"].label() == "alpha"
    assert again.remove("alpha") and not again.remove("alpha")
    again.save()
    assert ViewStore.load(path).names == ["Costs"]
    assert not list(path.parent.glob("*.part"))


def test_a_view_needs_a_name_and_columns_and_a_damaged_file_is_an_empty_store(tmp_path):
    store = ViewStore(tmp_path / "v.json")
    for name, columns in (("", ["A/B"]), ("x", []), ("x", ["  "])):
        with pytest.raises(ValueError):
            store.put(name, columns)
    with pytest.raises(ValueError):
        ViewStore().save()
    (tmp_path / "v.json").write_text("{not json", encoding="utf-8")
    assert ViewStore.load(tmp_path / "v.json").names == []
    (tmp_path / "w.json").write_text(json.dumps({"views": [{"name": "ok", "columns": ["A/B"]}, {"name": "bad"}, 3]}))
    assert ViewStore.load(tmp_path / "w.json").names == ["ok"]
    assert ViewStore.load(tmp_path / "missing.json").names == []


@pytest.fixture
def doc(tmp_path):
    return open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()


def test_a_cell_the_change_set_changed_remembers_the_games_value(doc):
    doc.set_value(2002, "Building/Upkeep", "12")
    doc.set_value(2002, "Standard/Name", "Large Hut")
    cells = {g: c for g, _n, c in balancing.grid(doc, [2001, 2002], ["Building/Upkeep", "Standard/Name"])}
    assert [(c.text, c.was) for c in cells[2002]] == [("12", "9"), ("Large Hut", "Big Hut")]
    assert [c.was for c in cells[2001]] == [None, None]  # an asset the change set leaves alone
    doc.set_value(2002, "Building/Upkeep", "9")
    assert balancing.grid(doc, [2002], ["Building/Upkeep"])[0][2][0].was is None  # back to the game's value


def test_the_panel_offers_saves_and_forgets_views(tmp_path, doc):
    from PySide6.QtWidgets import QApplication

    from annotoolbox.assetedit.assettabs import BalancingPanel

    QApplication.instance() or QApplication([])
    panel = BalancingPanel()
    assert not panel.save_button.isEnabled()  # no game open: nothing to save to
    panel.set_store(ViewStore.load(tmp_path / "views" / "anno117.json"))
    panel.present(doc, [2001, 2002])
    panel.columns.setText("Building/Upkeep, Standard/Name")
    assert panel.save_view("Costs") == "Costs"
    assert panel.saved.count() == 2 and panel.saved.currentText() == "Costs (House)"
    panel.columns.setText("")
    panel.saved.setCurrentIndex(0)
    panel.saved.setCurrentIndex(1)
    panel._pick(1)
    assert panel.paths() == ["Building/Upkeep", "Standard/Name"] and panel.table.rowCount() == 2
    assert ViewStore.load(tmp_path / "views" / "anno117.json").names == ["Costs"]
    panel.delete_view()
    assert panel.saved.count() == 1 and ViewStore.load(tmp_path / "views" / "anno117.json").names == []
    panel.columns.setText("")
    assert panel.save_view("nothing") is None  # no columns, no view


def test_an_assets_effective_values_carry_their_origin_and_the_games_value(doc):
    from annotoolbox.assetedit import report

    doc.set_value(2002, "Building/Upkeep", "12")
    rows = {r.path: r for r in report.effective_rows(doc, 2002, original=True)}
    assert (rows["Building/Upkeep"].value, rows["Building/Upkeep"].origin, rows["Building/Upkeep"].was) == (
        "12",
        "own",
        "9",
    )
    assert rows["Building/Level"].value == "Low" and rows["Building/Level"].origin == "template"
    assert rows["Building/Level"].was is None
    assert rows["Building/BuildCost/Item[1]/Product"].value == "1001"  # a list of the base asset
    assert rows["Building/BuildCost/Item[1]/Product"].origin == "base"
    assert rows["Standard/Name"].value == "Big Hut" and rows["Building/Kinds"].value == "GameObject"
    plain = {r.path: r for r in report.effective_rows(doc, 2002)}
    assert plain["Building/Upkeep"].was is None


def test_a_list_the_asset_sets_replaces_the_base_assets_list_in_the_report(doc):
    from annotoolbox.assetedit import report
    from annotoolbox.assetedit.xmltree import parse

    doc.set_block(
        2002,
        "Building/BuildCost",
        parse("<BuildCost><Item><Product>1002</Product><Amount>2</Amount></Item></BuildCost>"),
    )
    rows = {r.path: r for r in report.effective_rows(doc, 2002, original=True)}
    assert (
        rows["Building/BuildCost/Item[1]/Product"].value == "1002"
        and rows["Building/BuildCost/Item[1]/Product"].was == "1001"
    )
    assert not [p for p in rows if "Item[2]" in p]


def test_the_words_of_an_assets_texts_and_of_a_group(doc):
    from annotoolbox.assetedit import report

    game = {"-1001": "A small hut", "-1002": "two more"}
    rows = report.asset_texts(doc, 2001, game, {})
    assert rows == [("Standard/InfoDescription", "-1001", "A small hut")] and report.words_of(rows) == 3
    edited = report.asset_texts(doc, 2001, game, {"-1001": "A very small old hut"})
    assert report.words_of(edited) == 5
    assert report.group_words(doc, [2001, 2002, 9999], game) == 3  # 2002 names no text of its own; 9999 does not exist
    assert report.words_of([]) == 0


def test_the_page_of_an_asset_and_of_a_table_are_html_with_the_values_and_texts(doc):
    from annotoolbox.assetedit import report

    doc.set_value(2001, "Building/Level", "High")
    page = report.asset_html(doc, 2001, {"-1001": "A <small> hut"}, "english")
    assert "<h1>Hut</h1>" in page and "GUID 2001" in page and "Building/Level" in page
    assert "Game&#x27;s" in page or "Game's" in page  # the changed value brings the game's column
    assert "A &lt;small&gt; hut" in page and "(3 words)" in page  # text is escaped, words counted
    plain = report.asset_html(doc, 1001)  # no game, no texts: still a page
    assert "Texts in" not in plain and "Game" not in plain
    table = report.table_html("Costs", ["Building/Upkeep"], balancing.grid(doc, [2001, 2002], ["Building/Upkeep"]))
    assert "Big Hut (2002)" in table and "2 assets" in table and "<th>Building/Upkeep</th>" in table


def test_the_window_prints_an_asset_and_a_table_into_pdf_files(tmp_path):
    from PySide6.QtWidgets import QApplication

    from annotoolbox.assetedit.assetwindow import AssetWindow
    from annotoolbox.suite.settings import Settings

    QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"))
    window = AssetWindow(settings, tmp_path / "settings.json")
    assert not window.print_asset(to_pdf=True, pdf=tmp_path / "none.pdf")  # no game open yet
    window.open("anno117", wait=True)
    assert not window.print_asset(to_pdf=True, pdf=tmp_path / "none.pdf")  # no asset picked
    window.go_to(2001)
    assert window.print_asset(to_pdf=True, pdf=tmp_path / "hut.pdf")
    assert (tmp_path / "hut.pdf").read_bytes().startswith(b"%PDF")
    assert not window.print_table(to_pdf=True, pdf=tmp_path / "t.pdf")  # no columns chosen
    window.balancing.columns.setText("Building/Upkeep")
    window.tabs.setCurrentWidget(window.balancing)
    assert window.print_table(to_pdf=True, pdf=tmp_path / "t.pdf") and (tmp_path / "t.pdf").stat().st_size > 500
    assert window.balancing.store is not None and window.balancing.save_button.isEnabled()
    window.texts.set_languages(("english",))
    window.tabs.setCurrentWidget(window.texts)
    assert "words in this asset's texts" in window.texts.words.text()
    window.close()
