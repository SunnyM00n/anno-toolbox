import io
import os
from pathlib import Path

import pytest

from annotoolbox.flashui import __main__ as flashui_main
from annotoolbox.flashui import uiseam
from annotoolbox.flashui.uidoc import MovieDoc
from annotoolbox.flashui.uiproject import Project, changes
from annotoolbox.formats import feuassets, feuedit, feuread, feuwrite
from annotoolbox.othergames import ui2205
from annotoolbox.othergames.index2205 import FILE_DB, ArchiveIndex
from fakes import FakeFiles
from test_feu import ICON, PICTURE
from test_ui2205 import MOVIE, PNG_PATH, make_game, ui_files


def _png(size, color) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_the_edit_file_keeps_edits_and_replacements_and_comes_back(tmp_path: Path):
    project = Project(tmp_path / "mod" / "edits.json")
    project.add(MOVIE, feuedit.Edit("text", 5, {"text": "Hallo"}))
    project.files[PNG_PATH] = "pictures/button.png"
    project.icons[ICON] = "icon.png"
    again = Project.load(project.save())
    assert again.to_json() == project.to_json()
    assert again.source("pictures/button.png") == tmp_path / "mod" / "pictures" / "button.png"
    assert again.undo(MOVIE).values == {"text": "Hallo"} and again.undo(MOVIE) is None
    (tmp_path / "bad.json").write_text('{"format": 9}', encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        Project.load(tmp_path / "bad.json")


def test_a_build_writes_the_edited_movie_the_picture_its_size_and_the_icon(tmp_path: Path):
    files = ui_files(_png((4, 2), (0, 0, 0, 255)))
    files["data/ui/blobs/icons.png"] = _png((4, 4), (0, 0, 0, 0))
    (tmp_path / "button.png").write_bytes(_png((6, 3), (255, 0, 0, 255)))
    (tmp_path / "icon.png").write_bytes(_png((2, 2), (0, 255, 0, 255)))
    project = Project(tmp_path / "edits.json")
    project.add(MOVIE, feuedit.Edit("text", 5, {"text": "Hallo"}))
    project.files[PNG_PATH] = "button.png"
    project.icons[ICON] = "icon.png"
    out = changes(project, ui2205.UiFiles(FakeFiles(files)))
    assert set(out) == {MOVIE, PNG_PATH, ui2205.texture_path(PNG_PATH), feuassets.SIZES_PATH,
                        "data/ui/blobs/icons.png", "data/ui/blobs/icons_0.dds"}  # fmt: skip
    movie = feuread.read(out[MOVIE])
    assert feuedit.texts(movie)[0].text == "Hallo"
    image = next(img for _, img in feuedit.images(movie) if img.path == PICTURE)
    assert (image.width, image.height) == (6, 3)  # the movie learns the replaced picture's size
    assert feuassets.PngSizes(out[feuassets.SIZES_PATH]).get(PNG_PATH) == (6, 3)
    w, h, atlas = feuassets.dds_rgba(out["data/ui/blobs/icons_0.dds"])
    pixel = lambda x, y: atlas[(y * w + x) * 4 : (y * w + x) * 4 + 4]  # noqa: E731
    assert (w, h) == (4, 4) and pixel(1, 1) == bytes([0, 255, 0, 255]) and pixel(0, 0) == bytes(4)
    (tmp_path / "icon.png").write_bytes(_png((3, 3), (0, 0, 0, 255)))
    with pytest.raises(ValueError, match="3x3"):
        changes(project, ui2205.UiFiles(FakeFiles(files)))


def test_a_movie_from_a_flash_tool_replaces_the_games(tmp_path: Path):
    files = ui_files()
    swf = feuwrite.resign(files[MOVIE], b"FWS")
    (tmp_path / "menu.swf").write_bytes(swf)
    project = Project(tmp_path / "edits.json", files={MOVIE: "menu.swf"})
    assert project.movie_bytes(ui2205.UiFiles(FakeFiles(files)), MOVIE) == files[MOVIE]


def test_a_movie_opens_as_a_tree_of_its_elements_with_their_properties():
    doc = MovieDoc(MOVIE, ui_files()[MOVIE], Project(), ui2205.UiFiles(FakeFiles(ui_files())))
    root = doc.tree()
    assert [c.label.split(" (")[0] for c in root.children] == ["Stage", "Images", "Fonts", "Texts", "Classes", "Code"]
    stage = root.children[0]
    assert [c.kind for c in stage.children] == ["place", "place"]
    assert stage.children[0].label.startswith('"mcRoot" DefineSprite 6')
    assert [c.label.split(" ")[0] for c in stage.children[0].children] == ['"mcButton"', "ExternalImage"]
    icon = next(n for n in root.children[1].children if n.kind == "icon")
    assert doc.properties(icon)["game path"] == "data/ui/blobs/icons.png"
    assert doc.properties(root.children[3].children[0])["text"] == "Hello"
    assert doc.properties(stage.children[0])["x"] == 10.0
    assert doc.properties(root.children[5].children[0])["strings"] == ("OnBackClicked", "Title")


def test_an_edit_goes_into_the_edit_file_and_undo_takes_it_back():
    project = Project()
    doc = MovieDoc(MOVIE, ui_files()[MOVIE], project)
    doc.edit(feuedit.Edit("text", 5, {"text": "Neu"}))
    assert feuedit.texts(doc.movie)[0].text == "Neu" and len(project.edits[MOVIE]) == 1
    with pytest.raises(feuedit.EditError):
        doc.edit(feuedit.Edit("text", 77, {"text": "x"}))
    assert len(project.edits[MOVIE]) == 1  # a bad edit is not kept
    doc.undo()
    assert feuedit.texts(doc.movie)[0].text == "Hello" and doc.data() == ui_files()[MOVIE]


def test_redo_makes_an_undone_edit_again_and_a_new_edit_ends_the_redo():
    project = Project()
    doc = MovieDoc(MOVIE, ui_files()[MOVIE], project)
    assert doc.undo() is None and doc.redo() is None
    doc.edit(feuedit.Edit("text", 5, {"text": "Neu"}))
    assert doc.undo() == "text" and project.edits[MOVIE] == []
    assert doc.redo() == "text" and feuedit.texts(doc.movie)[0].text == "Neu" and len(project.edits[MOVIE]) == 1
    doc.undo()
    doc.edit(feuedit.Edit("text", 5, {"text": "Andere"}))
    assert doc.redo() is None and feuedit.texts(doc.movie)[0].text == "Andere"


def test_the_edits_of_the_edit_file_can_be_undone_and_redone_too():
    project = Project()
    first = MovieDoc(MOVIE, ui_files()[MOVIE], project)
    first.edit(feuedit.Edit("text", 5, {"text": "Neu"}))
    again = MovieDoc(MOVIE, ui_files()[MOVIE], project)  # opened again: the edit comes from the edit file
    assert not again.history.dirty and feuedit.texts(again.movie)[0].text == "Neu"
    assert again.undo() == "text" and feuedit.texts(again.movie)[0].text == "Hello"
    assert again.redo() == "text" and feuedit.texts(again.movie)[0].text == "Neu"


def test_the_seam_lists_what_is_registered():
    uiseam.register("actions", "test", ("Say hello", lambda w: None))
    try:
        assert uiseam.extensions("actions")["test"][0] == "Say hello"
    finally:
        uiseam.unregister("actions", "test")
    assert "test" not in uiseam.extensions("actions")
    with pytest.raises(KeyError):
        uiseam.register("nowhere", "x", None)


def test_the_commands_list_extract_record_and_build(tmp_path: Path, monkeypatch, capsys):
    game = make_game(tmp_path, ui_files())
    monkeypatch.setenv("ANNO2205_DIR", str(game))
    assert flashui_main.main(["list"]) == 0
    assert MOVIE in capsys.readouterr().out
    assert flashui_main.main(["info", "test_menu", "--strings"]) == 0
    assert "OnBackClicked" in capsys.readouterr().out
    assert flashui_main.main(["extract", "test_menu", "--to", str(tmp_path / "x")]) == 0
    assert (tmp_path / "x" / "test_menu.swf").read_bytes()[:3] == b"FWS"
    swf = tmp_path / "x" / "test_menu.swf"
    assert flashui_main.main(["from-swf", str(swf), str(tmp_path / "back.feu")]) == 0
    assert (tmp_path / "back.feu").read_bytes() == ui_files()[MOVIE]
    edits = tmp_path / "mod" / "edits.json"
    assert flashui_main.main(["set", str(edits), "text", "test_menu", "Hallo", "--id", "5"]) == 0
    assert flashui_main.main(["build", str(edits), "--out", str(tmp_path / "out")]) == 0
    built = tmp_path / "out" / ui2205.OUT_NAME
    entry = ArchiveIndex((built / FILE_DB).read_bytes()).entry(MOVIE)
    data = (built / ui2205.ARCHIVE).read_bytes()[entry.position : entry.position + entry.size]
    assert feuedit.texts(feuread.read(data))[0].text == "Hallo"
    assert flashui_main.main(["install", "--out", str(tmp_path / "out")]) == 0
    assert ui2205.is_ours((game / FILE_DB).read_bytes())
    assert flashui_main.main(["uninstall", "--out", str(tmp_path / "out")]) == 0
    assert not ui2205.is_ours((game / FILE_DB).read_bytes())


def test_the_preview_draws_the_stage_and_a_sprite_offscreen():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from annotoolbox.flashui import uirender

    QApplication.instance() or QApplication([])  # widgets: a later test opens the window in the same process
    movie = feuread.read(ui_files()[MOVIE])
    red = QImage(4, 2, QImage.Format_ARGB32)
    red.fill(QColor(255, 0, 0))
    image, r = uirender.render(movie, 0, 1.0, lambda img: red if img.path == PICTURE else None)
    assert (image.width(), image.height()) == (1920, 1080) and r.missing == set()
    assert image.pixelColor(10 + 5 + 2, 15 + 1).red() == 255  # the sprite at 10,15; its shape 5 px right of it
    sprite, _ = uirender.render(movie, 6, 1.0, lambda img: red)
    assert 6 <= sprite.width() <= 12 and sprite.height() >= 2


def test_the_window_opens_a_game_a_movie_and_edits_it(tmp_path: Path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from annotoolbox.flashui.uiwindow import EditorWindow

    QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))  # not the user's
    game = make_game(tmp_path, ui_files())
    window = EditorWindow(str(game), str(tmp_path / "edits.json"))
    assert window.movies.count() == 1
    window.movies.setCurrentRow(0)
    assert window.tree.topLevelItem(0).text(0) == "test_menu.feu"
    window._edit("text", 5, {"text": "Hallo"})
    assert feuedit.texts(window.doc.movie)[0].text == "Hallo"
    assert window.undo_action.isEnabled() and not window.redo_action.isEnabled()
    window.undo()
    assert feuedit.texts(window.doc.movie)[0].text == "Hello" and window.redo_action.isEnabled()
    window.redo()
    assert feuedit.texts(window.doc.movie)[0].text == "Hallo" and not window.redo_action.isEnabled()
    window.undo()
    window.save()
    assert Project.load(tmp_path / "edits.json").edits == {}  # the undone edit is not saved
    window.close()


def test_the_window_asks_before_unsaved_edits_are_dropped(tmp_path: Path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from annotoolbox.flashui.uiwindow import EditorWindow

    QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    window = EditorWindow(str(make_game(tmp_path, ui_files())), str(tmp_path / "edits.json"))
    window.movies.setCurrentRow(0)
    window._edit("text", 5, {"text": "Hallo"})
    assert window.project.unsaved
    answers = [QMessageBox.StandardButton.Cancel]
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: answers[0])
    opened: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (opened.append("asked") or "", ""))
    window._ask_edits()
    assert opened == [] and window.project.edits  # cancelled: no other edit file, the edits stay
    window.show()
    assert not window.close() and window.isVisible()
    answers[0] = QMessageBox.StandardButton.Save
    assert window.close() and not window.project.unsaved
    assert Project.load(tmp_path / "edits.json").edits  # kept on the way out


def test_a_cancelled_build_folder_is_not_kept_and_can_be_chosen_again(tmp_path: Path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog

    from annotoolbox.flashui.uiwindow import EditorWindow

    QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    window = EditorWindow(last_game=False)
    window.settings.setValue("out", ".")  # what a cancelled dialog of an earlier version kept
    answers = [""]
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_k: answers[0])
    assert window._out() is None and window.settings.value("out") == "."  # asked again; cancelled: nothing kept
    answers[0] = str(tmp_path / "build")
    built, kept = window._out()
    assert built.is_absolute() and kept.is_absolute() and built.parent == (tmp_path / "build").resolve()
    answers[0] = str(tmp_path / "other")
    assert (
        window.choose_out() == (tmp_path / "other").resolve()
        and window._out()[0].parent == (tmp_path / "other").resolve()
    )
    window.close()


def test_an_edit_file_is_written_whole_and_a_bom_reads(tmp_path: Path, monkeypatch):
    project = Project(tmp_path / "edits.json")
    project.files["a.png"] = "a.png"
    project.save()
    monkeypatch.setattr(Path, "write_text", lambda *_a, **_k: pytest.fail("not written through a temporary file"))
    project.files["b.png"] = "b.png"
    project.save()
    raw = (tmp_path / "edits.json").read_bytes()
    (tmp_path / "edits.json").write_bytes(b"\xef\xbb\xbf" + raw)
    assert Project.load(tmp_path / "edits.json").files == {"a.png": "a.png", "b.png": "b.png"}
