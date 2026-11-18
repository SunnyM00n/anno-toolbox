"""The Flash UI editor's window (Qt, the ``desktop`` extra): Anno 2205's UI movies listed, one open as a tree of its
elements, its first frame drawn, the selected element's properties edited, the edit file saved, a mod built.

``run(game, edits)`` starts it; ``EditorWindow`` is the window over ``uidoc.MovieDoc`` (the movie and its edits),
``uirender`` (the preview) and ``ui2205`` (the install, the build). Menu entries other code adds come from
``uiseam`` ("actions"). Usage: docs/ui/2205/flash-ui-editor.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..formats import feuedit, feuread, feuwrite
from ..infra.i18n import N_, tr
from ..othergames import ui2205
from ..othergames.installs import GAMES
from ..suite import widgets
from ..suite.background import Background
from . import fonts2205, uiexportdialog, uirender, uiseam
from .uidoc import MovieDoc, Node
from .uiproject import Project, changes

ZOOMS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
PROPERTY_NAMES = {  # the movie's property keys -> the words the form shows for them
    "id": N_("ui.id"),
    "path": N_("common.path"),
    "width": N_("ui.width"),
    "height": N_("common.height"),
    "game path": N_("ui.game_path"),
    "flags": N_("ui.flags"),
    "file": N_("common.file_lower"),
    "name": N_("common.name_lower"),
    "languages": N_("ui.languages"),
    "variable": N_("ui.variable"),
    "font": N_("common.font"),
    "color": N_("ui.color"),
    "box": N_("ui.box"),
    "html": N_("ui.html"),
    "character": N_("ui.character"),
    "depth": N_("ui.depth"),
    "x": N_("ui.x"),
    "y": N_("ui.y"),
    "scale": N_("common.scale"),
    "class": N_("ui.class"),
    "block": N_("ui.block"),
}


class EditorWindow(QMainWindow):
    sync = False  # a test's: the game folder is indexed on the caller's thread

    def __init__(self, game: str = "", edits: str | None = None, last_game: bool = True) -> None:
        """``last_game`` False: only ``game`` opens, not the one this window remembers (the suite owns the folders)."""
        super().__init__()
        self.background = Background(self)
        self._asked = 0  # the folder asked for last: an older index is dropped
        self.movie_paths: list[str] = []
        self.setWindowTitle(tr("ui.title.flash_ui_editor_anno_2205"))
        self.resize(1500, 900)
        self.settings = QSettings(QSettings.IniFormat, QSettings.UserScope, "flashui", "flashui")
        self.ui: ui2205.UiFiles | None = None
        self.fonts: fonts2205.Fonts2205 | None = None  # the install's fonts, read once per opened folder
        self.game: Path | None = None
        self.project = Project(Path(edits)) if edits and not Path(edits).is_file() else Project()
        if edits and Path(edits).is_file():
            self.project = Project.load(Path(edits))
        self.doc: MovieDoc | None = None
        self.node: Node | None = None
        self._atlas_pictures: dict[str, QImage] = {}
        self._build_ui()
        self._build_menu()
        folder = game or (str(self.settings.value("game", "")) if last_game else "")
        if folder:
            self.open_game(folder)

    # Layout

    def _build_ui(self) -> None:
        left = QSplitter(Qt.Vertical)
        movies = QWidget()
        lay = QVBoxLayout(movies)
        lay.setContentsMargins(0, 0, 0, 0)
        self.filter = QLineEdit(placeholderText=tr("ui.filter_movies"))
        widgets.find_key(self, self.filter)
        self.filter.textChanged.connect(self._fill_movies)
        self.movies = QListWidget()
        self.movies.currentTextChanged.connect(self.open_movie)
        lay.addWidget(self.filter)
        lay.addWidget(self.movies)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._select)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        left.addWidget(movies)
        left.addWidget(self.tree)
        left.setSizes([250, 650])
        dock = QDockWidget(tr("ui.movies_elements"))
        dock.setWidget(left)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        self.preview = QLabel(alignment=Qt.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.preview)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: #1e1e1e")
        self.preview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(lambda pos: self._picture_menu(self.preview.mapToGlobal(pos)))
        self.setCentralWidget(scroll)

        self.props = QWidget()
        self.form = QFormLayout(self.props)
        dock = QDockWidget(tr("common.properties"))
        dock.setWidget(self.props)
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.zoom = QComboBox()
        self.zoom.addItems([f"{int(z * 100)}%" for z in ZOOMS])
        self.zoom.setCurrentIndex(1)
        self.zoom.currentIndexChanged.connect(lambda _i: self._draw())
        self.statusBar().addPermanentWidget(self.zoom)

    def _build_menu(self) -> None:
        file = self.menuBar().addMenu(tr("common.menu.file"))
        for label, slot, keys in (
            (N_("ui.open_game_folder"), self._ask_game, None),
            (N_("ui.open_edit_file"), self._ask_edits, QKeySequence.Open),
            (N_("ui.save_edit_file"), self.save, QKeySequence.Save),
            (N_("ui.save_edit_file_dots"), self.save_as, None),
            (N_("ui.export_movie_flash_tool"), self._export_swf, None),
            (N_("ui.replace_movie_flash_tool"), self._import_swf, None),
            (N_("ui.export_images_dots"), lambda: uiexportdialog.export_images(self), None),
            (N_("ui.build_mod"), self.build, None),
            (N_("ui.choose_build_folder"), self.choose_out, None),
            (N_("ui.install_game"), self.install, None),
            (N_("ui.uninstall_game"), self.uninstall, None),
        ):
            act = QAction(tr(label), self)
            if keys is not None:
                act.setShortcut(keys)
            act.triggered.connect(slot)
            file.addAction(act)
        _edit, self.undo_action, self.redo_action = widgets.edit_menu(self, self.undo, self.redo)
        self._enable_history()
        extra = uiseam.extensions("actions")
        if extra:
            menu = self.menuBar().addMenu(tr("common.menu.tools"))
            for label, func in extra.values():
                act = QAction(label, self)
                act.triggered.connect(lambda _c=False, f=func: f(self))
                menu.addAction(act)

    # Game, movies, tree

    def open_game(self, folder: str) -> bool:
        """Open an Anno 2205 folder: its archives are indexed off the window's thread, the movies listed when that
        is done; the folder asked for last is the one opened. False when it is no such folder."""
        path = Path(folder)
        if not GAMES["anno2205"].is_install(path):
            self.statusBar().showMessage(tr("ui.not_anno_2205_folder", folder=folder))
            return False
        self._asked += 1
        mine = self._asked

        def work() -> tuple[ui2205.UiFiles, list[str]]:
            ui = ui2205.UiFiles(GAMES["anno2205"].tree(path))
            return ui, ui.movies()

        self.statusBar().showMessage(tr("ui.reading_folder", path=path))
        self.background.run(
            work, lambda found: self._game_read(path, found) if mine == self._asked else None, self.sync
        )
        return True

    def _game_read(self, path: Path, found) -> None:
        if isinstance(found, Exception):
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=found))
            return
        self.game, (self.ui, self.movie_paths) = path, found
        self.settings.setValue("game", str(path))
        self._close_movie()
        self._atlas_pictures.clear()  # cut from the atlases of the folder before
        self.fonts = fonts2205.Fonts2205(self.ui)
        self._fill_movies()
        self.statusBar().showMessage(tr("ui.n_ui_movies_path", n=len(self.movie_paths), path=path))

    def _fill_movies(self) -> None:
        self.movies.clear()
        if self.ui is not None:
            text = self.filter.text().lower()
            self.movies.addItems([m for m in self.movie_paths if text in m])

    def open_movie(self, path: str) -> None:
        if not path or self.ui is None:
            return
        self.statusBar().clearMessage()  # a "pictures not found" of the movie before must not stay
        try:
            self.doc = MovieDoc.from_game(self.ui, path, self.project)
        except (feuread.FeuError, feuedit.EditError, ValueError, OSError) as e:
            self._close_movie()
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=e))
            return
        self._fill_tree()
        self._enable_history()

    def _close_movie(self) -> None:
        """No movie is open: its tree, properties and picture go."""
        self.doc = self.node = None
        self._fill_tree()
        self._clear_props()
        self.preview.clear()
        self._enable_history()

    def _fill_tree(self) -> None:
        self.tree.clear()
        if self.doc is None:
            return

        def add(parent, node: Node) -> None:
            item = QTreeWidgetItem([node.label])
            item.setData(0, Qt.UserRole, node)
            parent.addChild(item) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(item)
            for child in node.children:
                add(item, child)

        add(self.tree, self.doc.tree())
        self.tree.topLevelItem(0).setExpanded(True)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _select(self, item: QTreeWidgetItem | None, _old=None) -> None:
        self.node = item.data(0, Qt.UserRole) if item is not None else None
        self._fill_props()
        self._draw()

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is not None:
            self.tree.setCurrentItem(item)  # the menu acts on the row under the pointer
        self._picture_menu(self.tree.viewport().mapToGlobal(pos))

    def _picture_menu(self, global_pos) -> None:
        """Right click on an element or the preview: export the selected picture (when it is one)."""
        img = self.doc.picture_ref(self.node) if self.doc is not None else None
        menu = QMenu(self)
        act = menu.addAction(tr("ui.export_picture_dots"))
        act.setEnabled(img is not None)
        if menu.exec(global_pos) is act and img is not None:
            uiexportdialog.export_one(self, img)

    # Preview

    def _owner(self) -> int:
        """What the preview draws for the selection: the character placed or selected, else the stage."""
        node = self.node
        if node is None or self.doc is None:
            return 0
        if node.kind == "place":
            owner, index = node.key
            tags = self.doc.movie.tags if owner == 0 else self.doc.defs[owner].children
            return feuread.read_placement(tags[index]).char_id or 0
        if node.kind in ("image", "icon", "text", "symbol") and node.key and node.key[0] in self.doc.defs:
            return node.key[0]
        return 0

    def _draw(self) -> None:
        if self.doc is None:
            return
        scale = ZOOMS[self.zoom.currentIndex()]
        choose = self.fonts.choose if self.fonts else None
        image, r = uirender.render(self.doc.movie, self._owner(), scale, self.picture, QColor(30, 30, 30), choose)
        self.preview.setPixmap(QPixmap.fromImage(image))
        notes = []
        if r.missing:
            notes.append(tr("ui.pictures_not_found_e_g", n=len(r.missing), name=sorted(r.missing)[0]))
        if r.unsupported:
            notes.append(tr("ui.preview_cannot_draw", what=", ".join(sorted(r.unsupported))))
        self.preview.setToolTip("\n".join(notes + (self.fonts.notes() if self.fonts else [])))
        if notes:
            self.statusBar().showMessage("; ".join(notes))

    def picture(self, img: feuread.ExternalImage) -> QImage | None:
        """The picture of an image tag: the edit file's replacement, the game's file, or cut from its atlas."""
        if self.ui is None:
            return None
        if img.atlas_name:
            if img.atlas_name in self.project.icons:
                return QImage(str(self.project.source(self.project.icons[img.atlas_name])))
            found = self.ui.atlas_entry(img.atlas_name)
            if found is None:
                return None
            atlas, entry = found
            if atlas not in self._atlas_pictures:
                self._atlas_pictures[atlas] = QImage.fromData(self.ui.file(atlas))
            x, y = entry.origin
            return self._atlas_pictures[atlas].copy(x, y, entry.width, entry.height)
        path = ui2205.image_path(img.path)
        if path in self.project.files:
            return QImage(str(self.project.source(self.project.files[path])))
        data = self.ui.image(img.path)
        return QImage.fromData(data) if data else None

    # Properties

    def _clear_props(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)

    def _fill_props(self) -> None:
        self._clear_props()
        if self.doc is None or self.node is None:
            return
        props = self.doc.properties(self.node)
        for key, value in props.items():
            if key not in ("text", "strings"):
                name = tr(PROPERTY_NAMES[key]) if key in PROPERTY_NAMES else key
                self.form.addRow(name, QLabel(str(value), textInteractionFlags=Qt.TextSelectableByMouse))
        kind = self.node.kind
        if kind == "text":
            self._text_editor(props)
        elif kind == "image":
            self._button(tr("ui.replace_picture"), lambda: self._replace_picture(props["game path"]))
        elif kind == "icon":
            self._button(
                tr("ui.replace_icon"), lambda: self._replace_icon(props["path"], props["width"], props["height"])
            )
        elif kind == "font":
            self._font_editor(props)
        elif kind == "place":
            self._place_editor(props)
        elif kind == "code":
            self._strings_editor(props)

    def _button(self, label: str, slot) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(slot)
        self.form.addRow(button)
        return button

    def _text_editor(self, props: dict) -> None:
        box = QPlainTextEdit(props["text"])
        self.form.addRow(tr("ui.text"), box)
        self._button(tr("common.apply"), lambda: self._edit("text", props["id"], {"text": box.toPlainText()}))

    def _font_editor(self, props: dict) -> None:
        combo = QComboBox()
        files = sorted(p[len(ui2205.UI) :] for p in self.ui.paths(".ffd"))
        combo.addItems(files)
        combo.setCurrentText(props["file"].lower())
        self.form.addRow(tr("ui.font_file"), combo)
        self._button(
            tr("common.apply"), lambda: self._edit("font", props["id"], {"file": _font_name(combo.currentText())})
        )

    def _place_editor(self, props: dict) -> None:
        x, y = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((x, props["x"]), (y, props["y"])):
            spin.setRange(-100000, 100000)
            spin.setDecimals(2)
            spin.setValue(value)
        self.form.addRow(tr("ui.new_x_pixels"), x)
        self.form.addRow(tr("ui.new_y_pixels"), y)
        owner, index = self.node.key
        self._button(tr("common.apply"), lambda: self._edit("move", index, {"sprite": owner, "x": round(x.value() * 20),
                                                                 "y": round(y.value() * 20)}))  # fmt: skip

    def _strings_editor(self, props: dict) -> None:
        listing = QListWidget()
        listing.addItems([f"{i}: {s}" for i, s in enumerate(props["strings"], 1)])
        line = QLineEdit()
        listing.currentRowChanged.connect(lambda row: line.setText(props["strings"][row] if row >= 0 else ""))
        self.form.addRow(listing)
        self.form.addRow(tr("ui.new_text"), line)
        block = props["block"]
        self._button(tr("common.apply"), lambda: self._edit("string", listing.currentRow() + 1, {"text": line.text(), "block": block}))  # fmt: skip

    def _edit(self, op: str, target: int, values: dict) -> None:
        try:
            self.doc.edit(feuedit.Edit(op, target, values))
        except (feuedit.EditError, feuread.FeuError, ValueError) as e:
            QMessageBox.warning(self, tr("common.edit"), str(e))
            return
        self.statusBar().showMessage(tr("ui.edited_save_edit_file_keep", op=op))
        self._refresh()

    def _refresh(self) -> None:
        self._enable_history()
        current = self.tree.currentItem()
        path = []
        while current is not None:
            path.insert(0, current.parent().indexOfChild(current) if current.parent() else 0)
            current = current.parent()
        self._fill_tree()
        item = self.tree.topLevelItem(0)
        for row in path[1:]:
            if item is None or row >= item.childCount():
                break
            item.setExpanded(True)
            item = item.child(row)
        if item is not None:
            self.tree.setCurrentItem(item)

    def _replace_picture(self, game_path: str) -> None:
        name, _ = QFileDialog.getOpenFileName(self, tr("ui.picture"), "", tr("ui.png_pictures"))
        if name:
            self.project.files[game_path] = name
            self._draw()
            self.statusBar().showMessage(tr("ui.replaced_movies_naming_get_size", path=game_path))

    def _replace_icon(self, name: str, width: int, height: int) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, tr("ui.icon_width_x_height", width=width, height=height), "", tr("ui.png_pictures")
        )
        if not file:
            return
        size = QImage(file).size()
        if (size.width(), size.height()) != (width, height):
            QMessageBox.warning(
                self,
                tr("ui.icon"),
                tr(
                    "ui.icon_x_picture",
                    width=width,
                    height=height,
                    pw=size.width(),
                    ph=size.height(),
                ),
            )
            return
        self.project.icons[name] = file
        self._draw()

    # Files and the build

    def _ask_game(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("ui.anno_2205_folder"))
        if folder:
            self.open_game(folder)

    def _ask_edits(self) -> None:
        if not self.may_leave():
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("ui.edit_file"), "", tr("ui.edit_files_json"))
        if not name:
            return
        try:
            self.project = Project.load(Path(name))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("ui.edit_file"), str(e))
            return
        if self.doc is not None:
            self.open_movie(self.doc.path)

    def save(self) -> bool:
        """The edit file written (asked for when it has no path yet); False when it was not."""
        if self.project.path is None:
            return self.save_as()
        return self._write(None)

    def save_as(self) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, tr("ui.edit_file"), "", tr("ui.edit_files_json"))
        return bool(name) and self._write(Path(name))

    def _write(self, path: Path | None) -> bool:
        try:
            self.statusBar().showMessage(tr("common.saved_path", path=self.project.save(path)))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("ui.edit_file"), tr("islands.could_not_save", error=e))
            return False
        return True

    def may_leave(self) -> bool:
        """Ask before unsaved edits are dropped (another edit file opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.project.unsaved:
            return True
        return widgets.ask_keep(self, tr("ui.edit_file"), tr("assets.save_edit_file_first"), self.save)

    def closeEvent(self, event) -> None:
        if not self.may_leave():
            event.ignore()
            return
        super().closeEvent(event)

    def _export_swf(self) -> None:
        if self.doc is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("ui.movie_flash_tool"), "", tr("ui.flash_movies_swf"))
        if name:
            try:
                Path(name).write_bytes(feuwrite.resign(self.doc.data(), b"FWS"))
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, tr("common.export"), tr("texts.path_not_written_error", path=name, error=e))

    def _import_swf(self) -> None:
        if self.doc is None:
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("ui.movie_saved_flash_tool"), "", tr("ui.flash_movies_swf_feu"))
        if name:
            self.project.files[self.doc.path] = name
            self.project.edits.pop(self.doc.path, None)  # the edits were made on the movie it replaces
            self.open_movie(self.doc.path)

    def choose_out(self) -> Path | None:
        """Ask for the build folder and keep it (an absolute path); None when the person cancelled."""
        name = QFileDialog.getExistingDirectory(self, tr("ui.folder_build"))
        if not name:
            return None
        base = Path(name).resolve()
        self.settings.setValue("out", str(base))
        return base

    def _out(self) -> tuple[Path, Path] | None:
        """The build, and where an earlier version kept the game's ``file.db`` (``delivery2205`` moves it out), in the
        build folder (asked for the first time; a stored folder that is not an absolute path counts as none); None
        when the person cancelled."""
        stored = str(self.settings.value("out", "") or "")
        base = Path(stored) if stored and Path(stored).is_absolute() else self.choose_out()
        if base is None:
            return None
        return base / ui2205.OUT_NAME, base / f"{ui2205.OUT_NAME}-kept" / "file.db"

    def build(self) -> Path | None:
        if self.ui is None or self.game is None or (out := self._out()) is None:
            return None
        built, backup = out
        try:
            files = changes(self.project, self.ui)
            if not files:
                self.statusBar().showMessage(tr("ui.edit_file_changes_nothing"))
                return None
            ui2205.build(files, ui2205.base_index(self.game, backup), built, self.statusBar().showMessage)
        except (ValueError, RuntimeError, OSError) as e:
            QMessageBox.warning(self, tr("common.build"), str(e))
            return None
        return built

    def _has_game(self) -> bool:
        """Whether a game folder is open; the status line says so when none is."""
        if self.game is None:
            self.statusBar().showMessage(tr("ui.open_game_folder_first"))
        return self.game is not None

    def install(self) -> None:
        if not self._has_game() or (out := self._out()) is None:
            return
        built, backup = out
        try:
            ui2205.install(built, self.game, backup, self.statusBar().showMessage)
        except (RuntimeError, OSError, TypeError) as e:
            QMessageBox.warning(self, tr("ui.install"), str(e))

    def uninstall(self) -> None:
        if not self._has_game() or (out := self._out()) is None:
            return
        _, backup = out
        try:
            ui2205.uninstall(self.game, backup, self.statusBar().showMessage)
        except (RuntimeError, OSError, TypeError) as e:
            QMessageBox.warning(self, tr("ui.uninstall"), str(e))

    def undo(self) -> None:
        if self.doc is not None and (label := self.doc.undo()) is not None:
            self.statusBar().showMessage(tr("common.undone_label", label=label), 5000)
            self._refresh()

    def redo(self) -> None:
        if self.doc is not None and (label := self.doc.redo()) is not None:
            self.statusBar().showMessage(tr("common.redone_label", label=label), 5000)
            self._refresh()

    def _enable_history(self) -> None:
        history = self.doc.history if self.doc is not None else None
        self.undo_action.setEnabled(history is not None and history.index > 0)
        self.redo_action.setEnabled(history is not None and history.index < len(history.commands))


def _font_name(file: str) -> str:
    """A font file as the movies name it (``roboto_default.ffd`` -> ``Roboto_Default.ffd``)."""
    return "_".join(part[:1].upper() + part[1:] for part in file.split("_"))


def run(game: str = "", edits: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = EditorWindow(game, edits)
    window.show()
    return app.exec()
