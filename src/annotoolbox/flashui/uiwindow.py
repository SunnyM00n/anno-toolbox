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
from . import uiexportdialog, uirender, uiseam
from .uidoc import MovieDoc, Node
from .uiproject import Project, changes

ZOOMS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
PROPERTY_NAMES = (  # the movie's property keys the form shows by their words; the keys stay the code's
    N_("id"), N_("path"), N_("width"), N_("height"), N_("game path"), N_("flags"), N_("file"), N_("name"),
    N_("languages"), N_("variable"), N_("font"), N_("color"), N_("box"), N_("html"), N_("character"), N_("depth"),
    N_("x"), N_("y"), N_("scale"), N_("class"), N_("block"),
)  # fmt: skip


class EditorWindow(QMainWindow):
    def __init__(self, game: str = "", edits: str | None = None, last_game: bool = True) -> None:
        """``last_game`` False: only ``game`` opens, not the one this window remembers (the suite owns the folders)."""
        super().__init__()
        self.setWindowTitle(tr("Flash UI editor - Anno 2205"))
        self.resize(1500, 900)
        self.settings = QSettings(QSettings.IniFormat, QSettings.UserScope, "flashui", "flashui")
        self.ui: ui2205.UiFiles | None = None
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
        self.filter = QLineEdit(placeholderText=tr("filter movies"))
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
        dock = QDockWidget(tr("Movies and elements"))
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
        dock = QDockWidget(tr("Properties"))
        dock.setWidget(self.props)
        dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.zoom = QComboBox()
        self.zoom.addItems([f"{int(z * 100)}%" for z in ZOOMS])
        self.zoom.setCurrentIndex(1)
        self.zoom.currentIndexChanged.connect(lambda _i: self._draw())
        self.statusBar().addPermanentWidget(self.zoom)

    def _build_menu(self) -> None:
        file = self.menuBar().addMenu(tr("&File"))
        for label, slot, keys in (
            (N_("Open game folder..."), self._ask_game, None),
            (N_("Open edit file..."), self._ask_edits, QKeySequence.Open),
            (N_("Save edit file"), self.save, QKeySequence.Save),
            (N_("Save edit file as..."), self.save_as, None),
            (N_("Export movie for a Flash tool..."), self._export_swf, None),
            (N_("Replace movie from a Flash tool..."), self._import_swf, None),
            (N_("Export images..."), lambda: uiexportdialog.export_images(self), None),
            (N_("Build mod..."), self.build, None),
            (N_("Choose the build folder..."), self.choose_out, None),
            (N_("Install into the game"), self.install, None),
            (N_("Uninstall from the game"), self.uninstall, None),
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
            menu = self.menuBar().addMenu(tr("&Tools"))
            for label, func in extra.values():
                act = QAction(label, self)
                act.triggered.connect(lambda _c=False, f=func: f(self))
                menu.addAction(act)

    # Game, movies, tree

    def open_game(self, folder: str) -> bool:
        path = Path(folder)
        if not GAMES["anno2205"].is_install(path):
            self.statusBar().showMessage(tr("{folder} is not an Anno 2205 folder", folder=folder))
            return False
        self.game, self.ui = path, ui2205.UiFiles(GAMES["anno2205"].tree(path))
        self.settings.setValue("game", str(path))
        self._fill_movies()
        self.statusBar().showMessage(tr("{n} UI movies in {path}", n=len(self.ui.movies()), path=path))
        return True

    def _fill_movies(self) -> None:
        self.movies.clear()
        if self.ui is not None:
            text = self.filter.text().lower()
            self.movies.addItems([m for m in self.ui.movies() if text in m])

    def open_movie(self, path: str) -> None:
        if not path or self.ui is None:
            return
        self.statusBar().clearMessage()  # a "pictures not found" of the movie before must not stay
        self.doc = MovieDoc.from_game(self.ui, path, self.project)
        self._fill_tree()
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
        act = menu.addAction(tr("Export this picture..."))
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
        image, r = uirender.render(self.doc.movie, self._owner(), scale, self.picture, QColor(30, 30, 30))
        self.preview.setPixmap(QPixmap.fromImage(image))
        if r.missing:
            self.statusBar().showMessage(
                tr("{n} pictures not found, e.g. {name}", n=len(r.missing), name=sorted(r.missing)[0])
            )

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
                name = tr(key) if key in PROPERTY_NAMES else key
                self.form.addRow(name, QLabel(str(value), textInteractionFlags=Qt.TextSelectableByMouse))
        kind = self.node.kind
        if kind == "text":
            self._text_editor(props)
        elif kind == "image":
            self._button(tr("Replace picture..."), lambda: self._replace_picture(props["game path"]))
        elif kind == "icon":
            self._button(
                tr("Replace icon..."), lambda: self._replace_icon(props["path"], props["width"], props["height"])
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
        self.form.addRow(tr("text"), box)
        self._button(tr("Apply"), lambda: self._edit("text", props["id"], {"text": box.toPlainText()}))

    def _font_editor(self, props: dict) -> None:
        combo = QComboBox()
        files = sorted(p[len(ui2205.UI) :] for p in self.ui.paths(".ffd"))
        combo.addItems(files)
        combo.setCurrentText(props["file"].lower())
        self.form.addRow(tr("font file"), combo)
        self._button(tr("Apply"), lambda: self._edit("font", props["id"], {"file": _font_name(combo.currentText())}))

    def _place_editor(self, props: dict) -> None:
        x, y = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, value in ((x, props["x"]), (y, props["y"])):
            spin.setRange(-100000, 100000)
            spin.setDecimals(2)
            spin.setValue(value)
        self.form.addRow(tr("new x (pixels)"), x)
        self.form.addRow(tr("new y (pixels)"), y)
        owner, index = self.node.key
        self._button(tr("Apply"), lambda: self._edit("move", index, {"sprite": owner, "x": round(x.value() * 20),
                                                                 "y": round(y.value() * 20)}))  # fmt: skip

    def _strings_editor(self, props: dict) -> None:
        listing = QListWidget()
        listing.addItems([f"{i}: {s}" for i, s in enumerate(props["strings"], 1)])
        line = QLineEdit()
        listing.currentRowChanged.connect(lambda row: line.setText(props["strings"][row] if row >= 0 else ""))
        self.form.addRow(listing)
        self.form.addRow(tr("new text"), line)
        block = props["block"]
        self._button(tr("Apply"), lambda: self._edit("string", listing.currentRow() + 1, {"text": line.text(), "block": block}))  # fmt: skip

    def _edit(self, op: str, target: int, values: dict) -> None:
        try:
            self.doc.edit(feuedit.Edit(op, target, values))
        except (feuedit.EditError, feuread.FeuError, ValueError) as e:
            QMessageBox.warning(self, tr("Edit"), str(e))
            return
        self.statusBar().showMessage(tr("{op} edited; save the edit file to keep it", op=op))
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
        name, _ = QFileDialog.getOpenFileName(self, tr("Picture"), "", tr("PNG pictures (*.png)"))
        if name:
            self.project.files[game_path] = name
            self._draw()
            self.statusBar().showMessage(
                tr("{path} replaced; the movies naming it get its size in the build", path=game_path)
            )

    def _replace_icon(self, name: str, width: int, height: int) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, tr("Icon of {width}x{height}", width=width, height=height), "", tr("PNG pictures (*.png)")
        )
        if not file:
            return
        size = QImage(file).size()
        if (size.width(), size.height()) != (width, height):
            QMessageBox.warning(
                self,
                tr("Icon"),
                tr(
                    "the icon is {width}x{height}, the picture {pw}x{ph}",
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
        folder = QFileDialog.getExistingDirectory(self, tr("Anno 2205 folder"))
        if folder:
            self.open_game(folder)

    def _ask_edits(self) -> None:
        if not self.may_leave():
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("Edit file"), "", tr("Edit files (*.json)"))
        if not name:
            return
        try:
            self.project = Project.load(Path(name))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Edit file"), str(e))
            return
        if self.doc is not None:
            self.open_movie(self.doc.path)

    def save(self) -> bool:
        """The edit file written (asked for when it has no path yet); False when it was not."""
        if self.project.path is None:
            return self.save_as()
        return self._write(None)

    def save_as(self) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, tr("Edit file"), "", tr("Edit files (*.json)"))
        return bool(name) and self._write(Path(name))

    def _write(self, path: Path | None) -> bool:
        try:
            self.statusBar().showMessage(tr("saved {path}", path=self.project.save(path)))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Edit file"), tr("could not save: {error}", error=e))
            return False
        return True

    def may_leave(self) -> bool:
        """Ask before unsaved edits are dropped (another edit file opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.project.unsaved:
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(self, tr("Edit file"), tr("Save the edit file first?"),
                                      buttons.Save | buttons.Discard | buttons.Cancel)  # fmt: skip
        if answer == buttons.Save:
            return self.save()
        return answer != buttons.Cancel

    def closeEvent(self, event) -> None:
        if not self.may_leave():
            event.ignore()
            return
        super().closeEvent(event)

    def _export_swf(self) -> None:
        if self.doc is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("Movie for a Flash tool"), "", tr("Flash movies (*.swf)"))
        if name:
            Path(name).write_bytes(feuwrite.resign(self.doc.data(), b"FWS"))

    def _import_swf(self) -> None:
        if self.doc is None:
            return
        name, _ = QFileDialog.getOpenFileName(
            self, tr("Movie saved by a Flash tool"), "", tr("Flash movies (*.swf *.feu)")
        )
        if name:
            self.project.files[self.doc.path] = name
            self.project.edits.pop(self.doc.path, None)  # the edits were made on the movie it replaces
            self.open_movie(self.doc.path)

    def choose_out(self) -> Path | None:
        """Ask for the build folder and keep it (an absolute path); None when the person cancelled."""
        name = QFileDialog.getExistingDirectory(self, tr("Folder for the build"))
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
                self.statusBar().showMessage(tr("the edit file changes nothing"))
                return None
            ui2205.build(files, ui2205.base_index(self.game, backup), built, self.statusBar().showMessage)
        except (ValueError, RuntimeError, OSError) as e:
            QMessageBox.warning(self, tr("Build"), str(e))
            return None
        return built

    def install(self) -> None:
        if (out := self._out()) is None:
            return
        built, backup = out
        try:
            ui2205.install(built, self.game, backup, self.statusBar().showMessage)
        except (RuntimeError, OSError, TypeError) as e:
            QMessageBox.warning(self, tr("Install"), str(e))

    def uninstall(self) -> None:
        if (out := self._out()) is None:
            return
        _, backup = out
        try:
            ui2205.uninstall(self.game, backup, self.statusBar().showMessage)
        except (RuntimeError, OSError, TypeError) as e:
            QMessageBox.warning(self, tr("Uninstall"), str(e))

    def undo(self) -> None:
        if self.doc is not None and (label := self.doc.undo()) is not None:
            self.statusBar().showMessage(tr("undone: {label}", label=label), 5000)
            self._refresh()

    def redo(self) -> None:
        if self.doc is not None and (label := self.doc.redo()) is not None:
            self.statusBar().showMessage(tr("redone: {label}", label=label), 5000)
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
