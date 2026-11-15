import csv
import json
import os
from pathlib import Path

import pytest

from annotoolbox.flashui import __main__ as flashui_main
from annotoolbox.flashui import uiexport
from annotoolbox.flashui.uidoc import MovieDoc
from annotoolbox.flashui.uiproject import Project
from annotoolbox.formats import feuassets, imagewrite
from annotoolbox.othergames import ui2205
from fakes import FakeFiles
from test_feu import ICON, PICTURE
from test_ui2205 import MOVIE, PNG_PATH, make_game, ui_files

PIXELS = bytes(i % 256 for i in range(4 * 4 * 4))  # a 4 x 4 atlas, every byte different


def export_files(png: bool = True) -> dict[str, bytes]:
    files = ui_files()
    files["data/ui/blobs/icons_0.dds"] = feuassets.rgba_dds(4, 4, PIXELS)
    if png:
        files[PNG_PATH] = imagewrite.png_bytes(4, 2, bytes(range(32)))
    else:
        del files[PNG_PATH]
        files[ui2205.texture_path(PNG_PATH)] = feuassets.rgba_dds(4, 2, bytes(range(32)))
    files["data/ui/extractedfirefiles/uiassets/unused.png"] = imagewrite.png_bytes(1, 1, bytes(4))
    return files


def exporter(files: dict[str, bytes]) -> uiexport.Exporter:
    return uiexport.Exporter(ui2205.UiFiles(FakeFiles(files)))


def test_png_files_are_written_and_read_back():
    png = imagewrite.png_bytes(2, 1, bytes(range(8)))
    assert uiexport.png_size(png) == (2, 1)
    pil = pytest.importorskip("PIL.Image")
    import io

    assert pil.open(io.BytesIO(png)).convert("RGBA").tobytes() == bytes(range(8))


def test_a_movie_exports_its_picture_and_its_atlas_icon_cut_out(tmp_path: Path):
    ex = exporter(export_files())
    done = ex.export(ex.movie_items(MOVIE, "test_menu/"), tmp_path)
    assert {i.name: i.status for i in done} == {
        "test_menu/uiassets/button_up.png": "ok",
        "test_menu/icons/icon_a.png": "ok",
    }
    assert uiexport.png_size((tmp_path / "test_menu/icons/icon_a.png").read_bytes()) == (2, 2)
    # the icon is the 2 x 2 block at pixel 1, 1 of the atlas
    assert uiexport.crop(4, PIXELS, 1, 1, 2, 2) == PIXELS[20:28] + PIXELS[36:44]
    rows = list(csv.DictReader((tmp_path / "index.csv").open(encoding="utf-8")))
    assert rows[0]["movies"] == "test_menu.feu" and rows[0]["width"] == "4"
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))[1]["kind"] == "icon"


def test_a_picture_without_a_png_comes_from_its_texture_and_a_missing_one_is_listed(tmp_path: Path):
    ex = exporter(export_files(png=False))
    done = ex.export([ex.item_by_name(PICTURE), ex.item_by_name("data/ui/nowhere.png")], tmp_path)
    assert [i.status for i in done] == ["ok", "missing"]
    assert uiexport.png_size((tmp_path / done[0].name).read_bytes()) == (4, 2)


def test_the_shared_pictures_name_the_movies_that_use_them_and_the_atlas_is_cut_by_its_entries(tmp_path: Path):
    ex = exporter(export_files())
    shared = {i.name: i for i in ex.shared_items()}
    assert shared["shared/uiassets/button_up.png"].movies == [MOVIE]
    assert shared["shared/uiassets/unused.png"].movies == []
    atlas = ex.atlas_items()
    assert [i.name for i in atlas] == ["atlases/icons/icons/icon_a.png"] and atlas[0].movies == [MOVIE]
    assert ex.item_by_name(ICON).kind == "icon"
    assert {i.name for i in ex.all_movie_items()} == {"test_menu/uiassets/button_up.png", "test_menu/icons/icon_a.png"}


def test_the_export_command_runs_in_a_batch(tmp_path: Path, monkeypatch, capsys):
    game = make_game(tmp_path, export_files())
    monkeypatch.setenv("ANNO2205_DIR", str(game))
    out = tmp_path / "pics"
    assert flashui_main.main(["export-images", "--to", str(out), "--movie", "test_menu", "--shared"]) == 0
    assert "pictures written" in capsys.readouterr().out
    assert (out / "test_menu/icons/icon_a.png").is_file() and (out / "shared/uiassets/unused.png").is_file()
    assert (out / "atlases/icons/icons/icon_a.png").is_file() and (out / "index.csv").is_file()
    assert flashui_main.main(["export-images", "--to", str(out)]) == 2


def test_a_tree_row_knows_its_picture():
    doc = MovieDoc(MOVIE, ui_files()[MOVIE], Project(), ui2205.UiFiles(FakeFiles(ui_files())))
    root = doc.tree()
    picture = next(n for n in root.children[1].children if n.kind == "image")
    assert doc.picture_ref(picture).path == PICTURE
    assert doc.picture_ref(root.children[0]) is None and doc.picture_ref(None) is None


def test_the_window_offers_the_export_and_writes_one_picture(tmp_path: Path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog

    from annotoolbox.flashui import uiexportdialog
    from annotoolbox.flashui.uiwindow import EditorWindow

    QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    window = EditorWindow(str(make_game(tmp_path, export_files())), str(tmp_path / "edits.json"))
    window.movies.setCurrentRow(0)
    images = window.tree.topLevelItem(0).child(1)
    window.tree.setCurrentItem(images.child(0))
    target = tmp_path / "one.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert uiexportdialog.export_one(window, window.doc.picture_ref(window.node)) == target
    assert uiexport.png_size(target.read_bytes()) == (4, 2)
    dialog = uiexportdialog.ExportDialog(window)
    assert dialog.boxes["selected"].isChecked()
    assert [i.name for i in dialog.items(uiexport.Exporter(window.ui))] == ["selected/button_up.png"]
    window.close()


GAME = os.environ.get("ANNO2205_DIR")


@pytest.mark.skipif(not GAME, reason="needs the installed game: ANNO2205_DIR")
def test_the_installed_game_exports_every_picture(tmp_path: Path):
    from annotoolbox.othergames.installs import GAMES

    ex = uiexport.Exporter(ui2205.UiFiles(GAMES["anno2205"].tree(Path(GAME))))
    done = ex.export(ex.shared_items() + ex.atlas_items(), tmp_path)
    assert sum(1 for i in done if i.status == "ok") > 1600
    assert not [i for i in done if i.status.startswith("failed")]
