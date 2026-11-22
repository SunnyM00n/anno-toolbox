"""The viewer's export, import and inspect tools offscreen on a synthetic game: the dialog's choices, the files each
export takes (open file, selection, folder, filter), a run with its summary, the Inspect panel and its comparison, and
the glTF import dialog with its report."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests import rdmfiles
from tests.ddsfiles import make_dxt1_dds

from annotoolbox.rdm import commands as rdm_commands
from annotoolbox.sources import gametree

RDM = "data/graphics/house/house.rdm"
DDS = "data/graphics/house/house_diff_0.dds"
XML = "data/config/settings.xml"
FILES = {RDM: rdmfiles.static_file(), DDS: make_dxt1_dds(), XML: b"<Settings><A>1</A></Settings>"}


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt, tmp_path):
    from annotoolbox.suite.settings import CSettings
    from annotoolbox.viewer.viewerwindow import CViewerWindow

    root = tmp_path / "game"
    for path, data in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    window = CViewerWindow(CSettings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(gametree.loose_tree(root, "test"))
    yield window
    window.close()


def test_the_file_menu_has_the_export_and_import_entries(window):
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()[0].menu().actions()]
    assert "Export…" in titles and "Import a model (glTF to .rdm)…" in titles
    assert "Export the files the filter shows…" in titles
    assert "Inspect" in window.m_docks


def test_which_files_each_export_takes(window):
    window.open_path(DDS)
    assert window.m_exports.open_file() == [DDS]
    window.m_browser.m_search.setText("house")
    window.m_browser._run_search()
    assert sorted(window.m_exports.filtered()) == sorted([RDM, DDS])
    window.m_browser.m_search.setText("")
    window.m_browser.m_kind.setCurrentText("Models")
    assert window.m_exports.filtered() == [RDM]
    window.m_browser.m_search.setText("config")
    window.m_browser.m_kind.setCurrentText("All files")
    window.m_browser._run_search()
    window.m_browser.m_found.setCurrentRow(0)
    window.m_browser.m_found.item(0).setSelected(True)
    assert window.m_exports.selection() == [XML]


def test_the_dialog_offers_the_forms_of_the_kinds_present_and_remembers_them(window, tmp_path):
    from annotoolbox.viewer.exportdialog import CExportDialog, kinds_summary

    assert kinds_summary([RDM, DDS, XML]).startswith("3 files:")
    state: dict = {}
    dialog = CExportDialog([RDM, DDS], state, "Export", window)
    assert {"raw", "glb", "gltf", "png", "png16", "exr", "hdr"} <= set(dialog.m_boxes)
    assert "csv" not in dialog.m_boxes  # no data file among the paths
    assert dialog.formats() == {"raw"}
    dialog.m_boxes["png"].setChecked(True)
    dialog.m_boxes["glb"].setChecked(True)
    dialog.m_depth16.setChecked(True)
    dialog.m_folder.setText(str(tmp_path / "out"))
    dialog._accept()
    assert state["formats"] == ["glb", "png", "raw"] and state["options"]["png_depth"] == 16
    again = CExportDialog([DDS], state, "Export", window)
    assert again.m_boxes["png"].isChecked()


def test_an_export_writes_the_forms_and_the_index_and_says_how_it_went(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.exporting import run
    from annotoolbox.viewer.exportdialog import CExportRun

    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    out = tmp_path / "out"
    job = CExportRun(window.m_tree, [RDM, DDS, XML], {"raw", "glb", "png", "json"}, run.SOptions(), out, window)
    job._work()
    assert (out / RDM).read_bytes() == FILES[RDM] and (out / "data/graphics/house/house.glb").is_file()
    assert (out / "data/graphics/house/house_diff_0.png").is_file()
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert {e["source"] for e in index} == {RDM, DDS, XML}
    assert said and "3 exported" in said[0] and "index.json" in said[0]


def test_a_finished_export_tells_its_owner_and_is_deleted(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox

    from annotoolbox.exporting import run
    from annotoolbox.viewer.exportdialog import CExportRun

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    job = CExportRun(window.m_tree, [DDS], {"raw"}, run.SOptions(), tmp_path / "o", window)
    ended = []
    job.done.connect(ended.append)
    job._work()
    assert ended == [job]
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    import shiboken6

    assert not shiboken6.isValid(job)


def test_the_inspect_panel_lists_an_rdm_and_compares_it_with_its_glTF(window, tmp_path):
    window.open_path(RDM)
    panel = window.m_exports.m_inspect
    assert panel.m_lines and "mesh" in "\n".join(panel.m_lines).lower()
    plain = len(panel.m_view.toPlainText())
    panel.m_values.setChecked(True)
    assert len(panel.m_view.toPlainText()) > plain  # the checksums are added
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "house.rdm").write_bytes(FILES[RDM])
    assert rdm_commands.main(["export", str(tmp_path / "in"), "-o", str(tmp_path / "glb")]) == 0
    panel.compare_with(tmp_path / "glb" / "house.glb")
    assert "the same in every element and value" in panel.m_view.toPlainText()
    other = tmp_path / "other.rdm"
    other.write_bytes(rdmfiles.one_mesh_file())
    panel.compare_with(other)
    assert "differences" in panel.m_note.text() and "the same" not in panel.m_view.toPlainText()
    window.open_path(XML)
    assert not panel.m_lines  # only an .rdm is listed


def test_the_import_dialog_writes_the_rdm_and_reports_what_it_did(window, tmp_path):
    from annotoolbox.viewer.rdmpanel import CImportDialog

    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "house.rdm").write_bytes(FILES[RDM])
    assert rdm_commands.main(["export", str(tmp_path / "in"), "-o", str(tmp_path / "glb")]) == 0
    state: dict = {}
    dialog = CImportDialog(window, state, str(tmp_path / "glb" / "house.glb"))
    dialog.m_target.setText(str(tmp_path / "back"))
    dialog.run()
    assert "wrote" in dialog.m_report.toPlainText()
    assert (tmp_path / "back" / "house.rdm").read_bytes() == FILES[RDM]  # nothing added, nothing lost
    dialog.run()  # a second run leaves the file alone
    assert "left alone" in dialog.m_report.toPlainText()
    assert state["import_folder"] == str(tmp_path / "back")
    dialog.m_source.setText(str(tmp_path / "missing.glb"))
    dialog.run()
    assert "Pick a glTF" in dialog.m_report.toPlainText()
    broken = tmp_path / "broken.glb"
    broken.write_bytes(b"glTF not really")
    dialog.m_source.setText(str(broken))
    dialog.run()
    assert dialog.m_report.toPlainText().startswith("REFUSED")


def test_another_app_asks_the_viewer_to_export_through_the_suite(qt, tmp_path, monkeypatch):
    from annotoolbox.suite import apps, links, shell
    from annotoolbox.suite.settings import CSettings
    from annotoolbox.viewer.exporttools import CExportTools

    root = tmp_path / "game"
    for path, data in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    window = shell.CSuiteWindow(list(apps.BUILTIN), CSettings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    viewer = window.show_app("viewer")
    tree = gametree.loose_tree(root, "test")
    tree.m_game = "anno117"
    viewer.set_tree(tree)
    asked = []
    monkeypatch.setattr(CExportTools, "export_paths", lambda self, paths, title: asked.append(paths) or True)
    window.show_app("assets")
    assert links.available(links.EXPORT_FILES)
    assert links.export_files("anno117", [DDS])
    assert asked == [[DDS]]
    window.close()


def test_a_cancelled_export_does_not_show_its_progress_again_and_ends_with_its_window(qt, tmp_path):
    """A step reported after Cancel set the dialog's value, which shows it again; and an export whose window was
    destroyed went on to its last file."""
    import shiboken6
    from PySide6.QtWidgets import QWidget

    from annotoolbox.exporting import run
    from annotoolbox.viewer.exportdialog import CExportRun

    parent = QWidget()
    job = CExportRun(None, ["a.dds", "b.dds"], {"raw"}, run.SOptions(), tmp_path, parent)
    job._step(1, 2, "a.dds")
    assert job.m_dialog.value() == 1 and job.m_dialog.labelText() == "a.dds"
    job.m_dialog.canceled.emit()  # the Cancel button
    assert job.m_stop.is_set() and job.m_dialog.wasCanceled()
    job._step(2, 2, "b.dds")  # the file that was under way when Cancel came
    assert job.m_dialog.labelText() == "a.dds" and not job.m_dialog.isVisible()

    other = CExportRun(None, ["a.dds"], {"raw"}, run.SOptions(), tmp_path, parent)
    assert not other.m_stop.is_set()
    shiboken6.delete(parent)  # the window goes, and the run with it
    assert other.m_stop.is_set()
    other._tell(other.m_signals.step, 1, 1, "a.dds")  # the worker's next report: no error for the window that went
