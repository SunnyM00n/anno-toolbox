"""The Flash UI editor's window (Qt, the ``desktop`` extra): Anno 2205's UI movies listed, one open as a tree of its
elements, its first frame drawn, the selected element's properties edited, the edit file saved, a mod built.

``run(game, edits)`` starts it; ``CEditorWindow`` is the window over ``uidoc.CMovieDoc`` (the movie and its edits),
``uirender`` (the preview) and ``ui2205`` (the install, the build). Menu entries other code adds come from
``uiseam`` ("actions"). Usage: docs/ui/2205/flash-ui-editor.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
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
from ..suite import centralmenu, widgets
from ..suite.background import CBackground
from . import fonts2205, uiexportdialog, uirender, uiseam
from .uidoc import CMovieDoc, SNode
from .uiproject import CProject, changes

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


class CEditorWindow(QMainWindow):
    is_synchronous = False  # a test's: the game folder is indexed, and a build runs, on the caller's thread
    progress_text = Signal(str)  # said by a build or install from its thread

    def __init__(self, game: str = "", edits: str | None = None, last_game: bool = True) -> None:
        """``last_game`` False: only ``game`` opens, not the one this window remembers (the suite owns the folders)."""
        super().__init__()
        self.m_background = CBackground(self)
        self._m_asked = 0  # the folder asked for last: an older index is dropped
        self._m_job_running = False  # a build, install or uninstall is under way
        self.progress_text.connect(self.statusBar().showMessage)
        self.m_movie_paths: list[str] = []
        self.setWindowTitle(tr("ui.title.flash_ui_editor_anno_2205"))
        self.resize(1500, 900)
        self.settings = QSettings(QSettings.IniFormat, QSettings.UserScope, "flashui", "flashui")
        self.m_ui: ui2205.CUiFiles | None = None
        self.m_fonts: fonts2205.CFonts2205 | None = None  # the install's fonts, read once per opened folder
        self.game: Path | None = None
        self.m_project = CProject(Path(edits)) if edits and not Path(edits).is_file() else CProject()
        if edits and Path(edits).is_file():
            self.m_project = CProject.load(Path(edits))
        self.doc: CMovieDoc | None = None
        self.m_node: SNode | None = None
        self._m_atlas_pictures: dict[str, QImage] = {}
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
        self.m_filter = QLineEdit(placeholderText=tr("ui.filter_movies"))
        widgets.find_key(self, self.m_filter)
        self.m_filter.textChanged.connect(self._fill_movies)
        self.m_movies = QListWidget()
        self.m_movies.currentTextChanged.connect(self.open_movie)
        lay.addWidget(self.m_filter)
        lay.addWidget(self.m_movies)
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderHidden(True)
        self.m_tree.currentItemChanged.connect(self._select)
        self.m_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.m_tree.customContextMenuRequested.connect(self._tree_menu)
        left.addWidget(movies)
        left.addWidget(self.m_tree)
        left.setSizes([250, 650])
        dock = QDockWidget(tr("ui.movies_elements"))
        dock.setWidget(left)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        self.m_preview = QLabel(alignment=Qt.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.m_preview)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: #1e1e1e")
        self.m_preview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.m_preview.customContextMenuRequested.connect(
            lambda pos: self._picture_menu(self.m_preview.mapToGlobal(pos))
        )
        self.setCentralWidget(scroll)

        self.m_props = QWidget()
        self.m_form = QFormLayout(self.m_props)
        dock = QDockWidget(tr("common.properties"))
        dock.setWidget(self.m_props)
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.m_zoom = QComboBox()
        self.m_zoom.addItems([f"{int(z * 100)}%" for z in ZOOMS])
        self.m_zoom.setCurrentIndex(1)
        self.m_zoom.currentIndexChanged.connect(lambda _i: self._draw())
        self.statusBar().addPermanentWidget(self.m_zoom)

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
            if keys is QKeySequence.Save:
                centralmenu.mark(act, centralmenu.SAVE)
        _edit, self.m_undo_action, self.m_redo_action = widgets.edit_menu(self, self.undo, self.redo)
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
        self._m_asked += 1
        mine = self._m_asked

        def work() -> tuple[ui2205.CUiFiles, list[str]]:
            ui_files = ui2205.CUiFiles(GAMES["anno2205"].tree(path))
            return ui_files, ui_files.movies()

        self.statusBar().showMessage(tr("ui.reading_folder", path=path))
        self.m_background.run(
            work, lambda found: self._game_read(path, found) if mine == self._m_asked else None, self.is_synchronous
        )
        return True

    def _game_read(self, path: Path, found) -> None:
        if isinstance(found, Exception):
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=found))
            return
        self.game, (self.m_ui, self.m_movie_paths) = path, found
        self.settings.setValue("game", str(path))
        self._close_movie()
        self._m_atlas_pictures.clear()  # cut from the atlases of the folder before
        self.m_fonts = fonts2205.CFonts2205(self.m_ui)
        self._fill_movies()
        self.statusBar().showMessage(tr("ui.n_ui_movies_path", n=len(self.m_movie_paths), path=path))

    def _fill_movies(self) -> None:
        self.m_movies.clear()
        if self.m_ui is not None:
            text = self.m_filter.text().lower()
            self.m_movies.addItems([movie_path for movie_path in self.m_movie_paths if text in movie_path])

    def open_movie(self, path: str) -> None:
        if not path or self.m_ui is None:
            return
        self.statusBar().clearMessage()  # a "pictures not found" of the movie before must not stay
        try:
            self.doc = CMovieDoc.from_game(self.m_ui, path, self.m_project)
        except (feuread.CFeuError, feuedit.CEditError, ValueError, OSError) as e:
            self._close_movie()
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=e))
            return
        self._fill_tree()
        self._enable_history()

    def _close_movie(self) -> None:
        """No movie is open: its tree, properties and picture go."""
        self.doc = self.m_node = None
        self._fill_tree()
        self._clear_props()
        self.m_preview.clear()
        self._enable_history()

    def _fill_tree(self) -> None:
        self.m_tree.clear()
        if self.doc is None:
            return

        def add(parent, node: SNode) -> None:
            item = QTreeWidgetItem([node.label])
            item.setData(0, Qt.UserRole, node)
            parent.addChild(item) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(item)
            for child in node.children:
                add(item, child)

        add(self.m_tree, self.doc.tree())
        self.m_tree.topLevelItem(0).setExpanded(True)
        self.m_tree.setCurrentItem(self.m_tree.topLevelItem(0))

    def _select(self, item: QTreeWidgetItem | None, _old=None) -> None:
        self.m_node = item.data(0, Qt.UserRole) if item is not None else None
        self._fill_props()
        self._draw()

    def _tree_menu(self, position) -> None:
        item = self.m_tree.itemAt(position)
        if item is not None:
            self.m_tree.setCurrentItem(item)  # the menu acts on the row under the pointer
        self._picture_menu(self.m_tree.viewport().mapToGlobal(position))

    def _picture_menu(self, global_position) -> None:
        """Right click on an element or the preview: export the selected picture (when it is one)."""
        image = self.doc.picture_ref(self.m_node) if self.doc is not None else None
        menu = QMenu(self)
        act = menu.addAction(tr("ui.export_picture_dots"))
        act.setEnabled(image is not None)
        if menu.exec(global_position) is act and image is not None:
            uiexportdialog.export_one(self, image)

    # Preview

    def _owner(self) -> int:
        """What the preview draws for the selection: the character placed or selected, else the stage."""
        node = self.m_node
        if node is None or self.doc is None:
            return 0
        if node.kind == "place":
            owner, index = node.key
            tags = self.doc.m_movie.tags if owner == 0 else self.doc.m_defs[owner].children
            return feuread.read_placement(tags[index]).char_id or 0
        if node.kind in ("image", "icon", "text", "symbol") and node.key and node.key[0] in self.doc.m_defs:
            return node.key[0]
        return 0

    def _draw(self) -> None:
        if self.doc is None:
            return
        scale = ZOOMS[self.m_zoom.currentIndex()]
        choose = self.m_fonts.choose if self.m_fonts else None
        image, result = uirender.render(
            self.doc.m_movie, self._owner(), scale, self.picture, QColor(30, 30, 30), choose
        )
        self.m_preview.setPixmap(QPixmap.fromImage(image))
        notes = []
        if result.m_missing:
            notes.append(tr("ui.pictures_not_found_e_g", n=len(result.m_missing), name=sorted(result.m_missing)[0]))
        if result.m_unsupported:
            notes.append(tr("ui.preview_cannot_draw", what=", ".join(sorted(result.m_unsupported))))
        self.m_preview.setToolTip("\n".join(notes + (self.m_fonts.notes() if self.m_fonts else [])))
        if notes:
            self.statusBar().showMessage("; ".join(notes))

    def picture(self, image: feuread.SExternalImage) -> QImage | None:
        """The picture of an image tag: the edit file's replacement, the game's file, or cut from its atlas."""
        if self.m_ui is None:
            return None
        if image.atlas_name:
            if image.atlas_name in self.m_project.m_icons:
                return QImage(str(self.m_project.source(self.m_project.m_icons[image.atlas_name])))
            found = self.m_ui.atlas_entry(image.atlas_name)
            if found is None:
                return None
            atlas, entry = found
            if atlas not in self._m_atlas_pictures:
                self._m_atlas_pictures[atlas] = QImage.fromData(self.m_ui.file(atlas))
            x, y = entry.origin
            return self._m_atlas_pictures[atlas].copy(x, y, entry.width, entry.height)
        path = ui2205.image_path(image.path)
        if path in self.m_project.m_files:
            return QImage(str(self.m_project.source(self.m_project.m_files[path])))
        data = self.m_ui.image(image.path)
        return QImage.fromData(data) if data else None

    # Properties

    def _clear_props(self) -> None:
        while self.m_form.rowCount():
            self.m_form.removeRow(0)

    def _fill_props(self) -> None:
        self._clear_props()
        if self.doc is None or self.m_node is None:
            return
        props = self.doc.properties(self.m_node)
        for key, value in props.items():
            if key not in ("text", "strings"):
                name = tr(PROPERTY_NAMES[key]) if key in PROPERTY_NAMES else key
                self.m_form.addRow(name, QLabel(str(value), textInteractionFlags=Qt.TextSelectableByMouse))
        kind = self.m_node.kind
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
        self.m_form.addRow(button)
        return button

    def _text_editor(self, props: dict) -> None:
        box = QPlainTextEdit(props["text"])
        self.m_form.addRow(tr("ui.text"), box)
        self._button(tr("common.apply"), lambda: self._edit("text", props["id"], {"text": box.toPlainText()}))

    def _font_editor(self, props: dict) -> None:
        combo = QComboBox()
        files = sorted(path[len(ui2205.UI) :] for path in self.m_ui.paths(".ffd"))
        combo.addItems(files)
        combo.setCurrentText(props["file"].lower())
        self.m_form.addRow(tr("ui.font_file"), combo)
        self._button(
            tr("common.apply"), lambda: self._edit("font", props["id"], {"file": _font_name(combo.currentText())})
        )

    def _place_editor(self, props: dict) -> None:
        x, y = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((x, props["x"]), (y, props["y"])):
            spin.setRange(-100000, 100000)
            spin.setDecimals(2)
            spin.setValue(value)
        self.m_form.addRow(tr("ui.new_x_pixels"), x)
        self.m_form.addRow(tr("ui.new_y_pixels"), y)
        owner, index = self.m_node.key
        self._button(tr("common.apply"), lambda: self._edit("move", index, {"sprite": owner, "x": round(x.value() * 20),
                                                                 "y": round(y.value() * 20)}))  # fmt: skip

    def _strings_editor(self, props: dict) -> None:
        listing = QListWidget()
        listing.addItems([f"{i}: {string}" for i, string in enumerate(props["strings"], 1)])
        line = QLineEdit()
        listing.currentRowChanged.connect(lambda row: line.setText(props["strings"][row] if row >= 0 else ""))
        self.m_form.addRow(listing)
        self.m_form.addRow(tr("ui.new_text"), line)
        block = props["block"]
        self._button(tr("common.apply"), lambda: self._edit("string", listing.currentRow() + 1, {"text": line.text(), "block": block}))  # fmt: skip

    def _edit(self, operation: str, target: int, values: dict) -> None:
        try:
            self.doc.edit(feuedit.SEdit(operation, target, values))
        except (feuedit.CEditError, feuread.CFeuError, ValueError) as e:
            QMessageBox.warning(self, tr("common.edit"), str(e))
            return
        self.statusBar().showMessage(tr("ui.edited_save_edit_file_keep", op=operation))
        self._refresh()

    def _refresh(self) -> None:
        self._enable_history()
        current = self.m_tree.currentItem()
        path = []
        while current is not None:
            path.insert(0, current.parent().indexOfChild(current) if current.parent() else 0)
            current = current.parent()
        self._fill_tree()
        item = self.m_tree.topLevelItem(0)
        for row in path[1:]:
            if item is None or row >= item.childCount():
                break
            item.setExpanded(True)
            item = item.child(row)
        if item is not None:
            self.m_tree.setCurrentItem(item)

    def _replace_picture(self, game_path: str) -> None:
        name, _ = QFileDialog.getOpenFileName(self, tr("ui.picture"), "", tr("ui.png_pictures"))
        if name:
            self.m_project.m_files[game_path] = name
            self._draw()
            self.statusBar().showMessage(tr("ui.replaced_movies_naming_get_size", path=game_path))

    def _replace_icon(self, name: str, width_px: int, height_px: int) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, tr("ui.icon_width_x_height", width=width_px, height=height_px), "", tr("ui.png_pictures")
        )
        if not file:
            return
        size = QImage(file).size()
        if (size.width(), size.height()) != (width_px, height_px):
            QMessageBox.warning(
                self,
                tr("ui.icon"),
                tr(
                    "ui.icon_x_picture",
                    width=width_px,
                    height=height_px,
                    pw=size.width(),
                    ph=size.height(),
                ),
            )
            return
        self.m_project.m_icons[name] = file
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
            self.m_project = CProject.load(Path(name))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("ui.edit_file"), str(e))
            return
        if self.doc is not None:
            self.open_movie(self.doc.m_path)

    def save(self) -> bool:
        """The edit file written (asked for when it has no path yet); False when it was not."""
        if self.m_project.m_path is None:
            return self.save_as()
        return self._write(None)

    def save_as(self) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, tr("ui.edit_file"), "", tr("ui.edit_files_json"))
        return bool(name) and self._write(Path(name))

    def _write(self, path: Path | None) -> bool:
        try:
            self.statusBar().showMessage(tr("common.saved_path", path=self.m_project.save(path)))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("ui.edit_file"), tr("islands.could_not_save", error=e))
            return False
        return True

    def may_leave(self) -> bool:
        """Ask before unsaved edits are dropped (another edit file opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.m_project.unsaved:
            return True
        return widgets.ask_keep(self, tr("ui.edit_file"), tr("assets.save_edit_file_first"), self.save)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: no for a running install, else the question about unsaved edits."""
        if self._m_job_running:  # an install cut off half way would leave the game's archives in between
            self.statusBar().showMessage(tr("ui.job_running"))
            return False
        return self.may_leave()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
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
            self.m_project.m_files[self.doc.m_path] = name
            self.m_project.m_edits.pop(self.doc.m_path, None)  # the edits were made on the movie it replaces
            self.open_movie(self.doc.m_path)

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

    def _start_job(self, title: str, work, done=None) -> None:
        """``work()`` off the window's thread (its progress lines reach the status bar through ``progress_text``),
        then ``done(result)`` here; a failure is shown. One job at a time: a build and an install share the folders."""
        if self._m_job_running:
            self.statusBar().showMessage(tr("ui.job_running"))
            return
        self._m_job_running = True

        def finished(result) -> None:
            self._m_job_running = False
            if isinstance(result, Exception):
                QMessageBox.warning(self, tr(title), str(result))
            elif done is not None:
                done(result)

        self.m_background.run(work, finished, self.is_synchronous)

    def build(self) -> None:
        """Build the mod from the edit file (off the window's thread); the status bar follows it."""
        if self.m_ui is None or self.game is None or (out := self._out()) is None:
            return
        built, backup = out
        project, ui_files, game, say = self.m_project, self.m_ui, self.game, self.progress_text.emit

        def work() -> bool:
            files = changes(project, ui_files)
            if not files:
                return False
            ui2205.build(files, ui2205.base_index(game, backup), built, say)
            return True

        self._start_job(N_("common.build"), work, self._built)

    def _built(self, built_any: bool) -> None:
        if not built_any:
            self.statusBar().showMessage(tr("ui.edit_file_changes_nothing"))

    def _has_game(self) -> bool:
        """Whether a game folder is open; the status line says so when none is."""
        if self.game is None:
            self.statusBar().showMessage(tr("ui.open_game_folder_first"))
        return self.game is not None

    def install(self) -> None:
        if not self._has_game() or (out := self._out()) is None:
            return
        built, backup = out
        game, say = self.game, self.progress_text.emit
        self._start_job(N_("ui.install"), lambda: ui2205.install(built, game, backup, say))

    def uninstall(self) -> None:
        if not self._has_game() or (out := self._out()) is None:
            return
        _, backup = out
        game, say = self.game, self.progress_text.emit
        self._start_job(N_("ui.uninstall"), lambda: ui2205.uninstall(game, backup, say))

    def undo(self) -> None:
        if self.doc is not None and (label := self.doc.undo()) is not None:
            self.statusBar().showMessage(tr("common.undone_label", label=label), 5000)
            self._refresh()

    def redo(self) -> None:
        if self.doc is not None and (label := self.doc.redo()) is not None:
            self.statusBar().showMessage(tr("common.redone_label", label=label), 5000)
            self._refresh()

    def _enable_history(self) -> None:
        history = self.doc.m_history if self.doc is not None else None
        self.m_undo_action.setEnabled(history is not None and history.m_index > 0)
        self.m_redo_action.setEnabled(history is not None and history.m_index < len(history.m_commands))


def _font_name(file: str) -> str:
    """A font file as the movies name it (``roboto_default.ffd`` -> ``Roboto_Default.ffd``)."""
    return "_".join(part[:1].upper() + part[1:] for part in file.split("_"))


def run(game: str = "", edits: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = CEditorWindow(game, edits)
    window.show()
    return app.exec()
