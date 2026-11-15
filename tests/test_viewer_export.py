"""The viewer's export, import and inspect tools offscreen on a synthetic game: the dialog's choices, the files each
export takes (open file, selection, folder, filter), a run with its summary, the Inspect panel and its comparison, and
the glTF import dialog with its report."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests import rdmsamples
from tests.conftest import make_dxt1_dds

from annotoolbox.rdm import commands as rdm_commands
from annotoolbox.sources import gametree

RDM = "data/graphics/house/house.rdm"
DDS = "data/graphics/house/house_diff_0.dds"
XML = "data/config/settings.xml"
FILES = {RDM: rdmsamples.static_file(), DDS: make_dxt1_dds(), XML: b"<Settings><A>1</A></Settings>"}


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    root = tmp_path / "game"
    for path, data in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(gametree.loose_tree(root, "test"))
    yield window
    window.close()


def test_the_file_menu_has_the_export_and_import_entries(window):
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()[0].menu().actions()]
    assert "Export..." in titles and "Import a model (glTF to .rdm)..." in titles
    assert "Export the files the filter shows..." in titles
    assert "Inspect" in window.docks


def test_which_files_each_export_takes(window):
    window.open_path(DDS)
    assert window.exports.open_file() == [DDS]
    window.browser.search.setText("house")
    window.browser._run_search()
    assert sorted(window.exports.filtered()) == sorted([RDM, DDS])
    window.browser.search.setText("")
    window.browser.kind.setCurrentText("Models")
    assert window.exports.filtered() == [RDM]
    window.browser.search.setText("config")
    window.browser.kind.setCurrentText("All files")
    window.browser._run_search()
    window.browser.found.setCurrentRow(0)
    window.browser.found.item(0).setSelected(True)
    assert window.exports.selection() == [XML]


def test_the_dialog_offers_the_forms_of_the_kinds_present_and_remembers_them(window, tmp_path):
    from annotoolbox.viewer.exportdialog import ExportDialog, kinds_summary

    assert kinds_summary([RDM, DDS, XML]).startswith("3 files:")
    state: dict = {}
    dialog = ExportDialog([RDM, DDS], state, "Export", window)
    assert {"raw", "glb", "gltf", "png", "png16", "exr", "hdr"} <= set(dialog.boxes)
    assert "csv" not in dialog.boxes  # no data file among the paths
    assert dialog.formats() == {"raw"}
    dialog.boxes["png"].setChecked(True)
    dialog.boxes["glb"].setChecked(True)
    dialog.depth16.setChecked(True)
    dialog.folder.setText(str(tmp_path / "out"))
    dialog._accept()
    assert state["formats"] == ["glb", "png", "raw"] and state["options"]["png_depth"] == 16
    again = ExportDialog([DDS], state, "Export", window)
    assert again.boxes["png"].isChecked()


def test_an_export_writes_the_forms_and_the_index_and_says_how_it_went(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.exporting import run
    from annotoolbox.viewer.exportdialog import ExportRun

    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    out = tmp_path / "out"
    job = ExportRun(window.tree, [RDM, DDS, XML], {"raw", "glb", "png", "json"}, run.Options(), out, window)
    job._work()
    assert (out / RDM).read_bytes() == FILES[RDM] and (out / "data/graphics/house/house.glb").is_file()
    assert (out / "data/graphics/house/house_diff_0.png").is_file()
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert {e["source"] for e in index} == {RDM, DDS, XML}
    assert said and "3 exported" in said[0] and "index.json" in said[0]


def test_the_inspect_panel_lists_an_rdm_and_compares_it_with_its_glTF(window, tmp_path):
    window.open_path(RDM)
    panel = window.exports.inspect
    assert panel.lines and "mesh" in "\n".join(panel.lines).lower()
    plain = len(panel.view.toPlainText())
    panel.values.setChecked(True)
    assert len(panel.view.toPlainText()) > plain  # the checksums are added
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "house.rdm").write_bytes(FILES[RDM])
    assert rdm_commands.main(["export", str(tmp_path / "in"), "-o", str(tmp_path / "glb")]) == 0
    panel.compare_with(tmp_path / "glb" / "house.glb")
    assert "the same in every element and value" in panel.view.toPlainText()
    other = tmp_path / "other.rdm"
    other.write_bytes(rdmsamples.one_mesh_file())
    panel.compare_with(other)
    assert "differences" in panel.note.text() and "the same" not in panel.view.toPlainText()
    window.open_path(XML)
    assert not panel.lines  # only an .rdm is listed


def test_the_import_dialog_writes_the_rdm_and_reports_what_it_did(window, tmp_path):
    from annotoolbox.viewer.rdmpanel import ImportDialog

    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "house.rdm").write_bytes(FILES[RDM])
    assert rdm_commands.main(["export", str(tmp_path / "in"), "-o", str(tmp_path / "glb")]) == 0
    state: dict = {}
    dialog = ImportDialog(window, state, str(tmp_path / "glb" / "house.glb"))
    dialog.target.setText(str(tmp_path / "back"))
    dialog.run()
    assert "wrote" in dialog.report.toPlainText()
    assert (tmp_path / "back" / "house.rdm").read_bytes() == FILES[RDM]  # nothing added, nothing lost
    dialog.run()  # a second run leaves the file alone
    assert "left alone" in dialog.report.toPlainText()
    assert state["import_folder"] == str(tmp_path / "back")
    dialog.source.setText(str(tmp_path / "missing.glb"))
    dialog.run()
    assert "Pick a glTF" in dialog.report.toPlainText()
    broken = tmp_path / "broken.glb"
    broken.write_bytes(b"glTF not really")
    dialog.source.setText(str(broken))
    dialog.run()
    assert dialog.report.toPlainText().startswith("REFUSED")


def test_another_app_asks_the_viewer_to_export_through_the_suite(qt, tmp_path, monkeypatch):
    from annotoolbox.suite import apps, links, shell
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.exporttools import ExportTools

    root = tmp_path / "game"
    for path, data in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    window = shell.SuiteWindow(list(apps.BUILTIN), Settings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    viewer = window.show_app("viewer")
    tree = gametree.loose_tree(root, "test")
    tree.game = "anno117"
    viewer.set_tree(tree)
    asked = []
    monkeypatch.setattr(ExportTools, "export_paths", lambda self, paths, title: asked.append(paths) or True)
    window.show_app("assets")
    assert links.available(links.EXPORT_FILES)
    assert links.export_files("anno117", [DDS])
    assert asked == [[DDS]]
    window.close()
