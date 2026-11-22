"""The Texts window (Qt): a game's texts in two languages side by side, the filters over the rows, the picked text's
detail with its checks, and the project's translation saved, imported and exported.

``CTextWindow(settings, settings_path)`` is the suite's Texts app; ``open_game`` is the entry point. Everything that
reads a file runs off the window's thread (``suite.background``). docs/text-editor.md is the user's guide.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..assetedit import assetgame
from ..assetedit.gamefiles import LAYOUTS, CGameFiles
from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import centralmenu, gameopen, gamesync, widgets
from ..suite import project as suite_project
from ..suite.background import CBackground
from ..suite.clipplayer import CClipPlayer
from . import exchange, projectpart
from .detailpanel import CDetailPanel, SActions, SRowView
from .editfile import CEditFile
from .glossarydialog import CGlossaryDialog
from .rowsmodel import CRowsModel
from .textdoc import CHANGED, OUTDATED, STATUS_NAMES, STATUSES, UNTRANSLATED, CTextDoc, SFilter

APP = "texts"
TITLE = N_("texts.text_editor")
GAMES = dict(gametree.GAMES)
ALL = ""  # the filters' first entry: no filter


def language_title(language: str) -> str:
    return language.replace("_", " ").title()


class CTextWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.m_settings_path = settings, settings_path
        self.m_background = CBackground(self)
        self.m_key, self.doc = "", None
        self.m_generation = 0  # the filter run whose answer is still wanted
        self.m_model = CRowsModel()
        self.m_table = QTableView()
        self.m_table.setModel(self.m_model)
        self.m_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.m_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.m_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.m_table.verticalHeader().hide()
        self.m_table.horizontalHeader().setStretchLastSection(True)
        self.m_detail = CDetailPanel(SActions(self._apply, self._revert, self._fill_twins, self._play))
        self.m_source_box, self.m_target_box = QComboBox(), QComboBox()
        self.m_status_box, self.m_file_box, self.m_group_box = QComboBox(), QComboBox(), QComboBox()
        self.m_search = QLineEdit(placeholderText=tr("texts.search_id_texts_editor_name"))
        self.m_problems_only = QCheckBox(tr("texts.only_problems"))
        self.m_voiced_only = QCheckBox(tr("texts.only_spoken"))
        self.m_player = CClipPlayer(self)  # the media player is made at the first voice played
        self.m_timer = QTimer(self, singleShot=True, interval=250)
        self._build()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("texts.file_gt_open_game_pick"))

    # layout
    def _build(self) -> None:
        bar = QHBoxLayout()
        for label, box in ((N_("texts.source"), self.m_source_box), (N_("common.target"), self.m_target_box)):
            bar.addWidget(QLabel(tr(label)))
            bar.addWidget(box)
        self.m_status_box.addItem(tr("texts.every_status"), ALL)
        for status in STATUSES:
            self.m_status_box.addItem(tr(STATUS_NAMES[status]), status)
        self.m_file_box.addItem(tr("texts.every_file"), ALL)
        self.m_group_box.addItem(tr("texts.every_group"), ALL)
        for widget in (self.m_status_box, self.m_file_box, self.m_group_box, self.m_problems_only, self.m_voiced_only):
            bar.addWidget(widget)
        bar.addWidget(self.m_search, 1)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.addLayout(bar)
        top_layout.addWidget(self.m_table)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(self.m_detail)
        splitter.setSizes([450, 330])
        self.setCentralWidget(splitter)
        self._menus()
        for box in (self.m_status_box, self.m_file_box, self.m_group_box):
            box.currentIndexChanged.connect(lambda _index: self._refilter())
        for check in (self.m_problems_only, self.m_voiced_only):
            check.toggled.connect(lambda _checked: self._refilter())
        widgets.find_key(self, self.m_search)
        self.m_search.textChanged.connect(lambda _text: self.m_timer.start())
        self.m_timer.timeout.connect(self._refilter)
        self.m_source_box.activated.connect(lambda _index: self._switch())
        self.m_target_box.activated.connect(lambda _index: self._switch())
        self.m_table.selectionModel().currentRowChanged.connect(
            lambda current_index, _old: self._show(self.m_model.key_at(current_index.row()))
        )

    def _menus(self) -> None:
        menu = self.menuBar().addMenu(tr("common.menu.file"))
        games = centralmenu.mark(menu.addMenu(tr("common.menu.open_game")), centralmenu.GAME)
        for key, name in GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        centralmenu.mark(
            menu.addAction(tr("sound.save_to_project"), self.save, QKeySequence.StandardKey.Save), centralmenu.SAVE
        )
        menu.addSeparator()
        menu.addAction(tr("texts.import_translation_dots"), self.import_file)
        menu.addAction(tr("texts.export_shown_rows"), self.export_file)
        menu.addSeparator()
        menu.addAction(tr("texts.new_text_dots"), self.new_text)
        edit, _undo, _redo = widgets.edit_menu(self, self.undo, self.redo)
        edit.addSeparator()
        edit.addAction(tr("texts.revert_games_text_accel"), self._revert)
        edit.addAction(tr("texts.next_untranslated"), self.next_untranslated, QKeySequence("F3"))
        edit.addAction(tr("texts.glossary"), self.edit_glossary)
        edit.addAction(tr("texts.glyphs_target_language"), self.audit_glyphs)

    # opening
    def project(self) -> suite_project.CProject:
        return suite_project.current(self.settings)

    def open_game(self, key: str, is_waiting: bool = False) -> None:
        """Open an installed game (its folder from the settings); its text files are read in the background."""
        if not gamesync.may_switch(self, key, self.may_leave):  # asks; the suite's other apps follow
            return
        gameopen.open_installed(
            self,
            self.settings,
            key,
            self.m_background,
            then=lambda result: self._opened(key, result),
            is_waiting=is_waiting,
        )

    def _opened(self, key: str, tree) -> None:
        if isinstance(tree, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=GAMES.get(key, key), error=tree))
            return
        saved = self.settings.apps.setdefault(APP, {})
        path = projectpart.edits_path(self.project(), key)
        try:
            edits = CEditFile.load(path, key)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        cache = assetgame.cache_dir(self.settings.workspace)
        doc = CTextDoc(
            CGameFiles(lambda: tree, tree.m_stamp, cache),
            LAYOUTS[key],
            edits,
            saved.get("source", edits.m_source),
            saved.get("target", ""),
        )
        if not doc.m_languages:
            self.statusBar().showMessage(tr("texts.no_text_files", game=GAMES.get(key, key)))
            return
        self.m_key, self.doc, self.m_model.m_doc = key, doc, doc
        saved["game"] = key
        for box in (self.m_source_box, self.m_target_box):
            box.clear()
            for language in doc.m_languages:
                box.addItem(language_title(language), language)  # i18n: ok (a language's name)
        self.m_source_box.setCurrentIndex(self.m_source_box.findData(doc.m_source))
        self.m_target_box.setCurrentIndex(self.m_target_box.findData(doc.m_target))
        self.setWindowTitle(f"{tr(TITLE)} - {GAMES.get(key, key)}")
        self._load_pair()

    def _load_pair(self) -> None:
        doc = self.doc
        source, target = self.m_source_box.currentData(), self.m_target_box.currentData()
        self.setEnabled(False)
        self.statusBar().showMessage(tr("texts.reading_texts"))

        def work():
            doc.prepare(source, target)
            return doc

        def done(result) -> None:
            self.setEnabled(True)
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("texts.texts_not_read", error=result))
                for box, language in ((self.m_source_box, doc.m_source), (self.m_target_box, doc.m_target)):
                    box.setCurrentIndex(box.findData(language))  # the pair the document still holds
                return
            self.settings.apps.setdefault(APP, {}).update(source=source, target=target)
            self._fill_filters()
            self._refilter()
            self.m_detail.set_styles(doc.style_choices())
            self._read_style_links(doc)

        self.m_background.run(work, done)

    def _read_style_links(self, doc: CTextDoc) -> None:
        """Which text style draws which text (a scan of the UI files, kept in the cache): off the window's thread; the
        picked row is shown again when it is done."""
        if doc.m_links is None or doc.m_links.loaded:
            return

        def done(result) -> None:
            if not isinstance(result, Exception) and doc is self.doc:
                self._show(self.current_key())

        self.m_background.run(doc.prepare_styles, done)

    def _switch(self) -> None:
        if self.doc is not None:
            self._load_pair()

    def _fill_filters(self) -> None:
        for box, items, label in ((self.m_file_box, self.doc.files_listed(), N_("texts.every_file")),
                                  (self.m_group_box, self.doc.groups_listed(), N_("texts.every_group"))):  # fmt: skip
            box.blockSignals(True)
            box.clear()
            box.addItem(tr(label), ALL)
            for item in items:
                box.addItem(item, item)  # i18n: ok (a file or a group of the game)
            box.blockSignals(False)
        self.m_file_box.setVisible(self.m_file_box.count() > 2)
        self.m_group_box.setVisible(self.m_group_box.count() > 2)

    # the rows
    def _filter(self) -> SFilter:
        status = self.m_status_box.currentData()
        return SFilter(frozenset({status}) if status else frozenset(), self.m_file_box.currentData() or "",
                      self.m_group_box.currentData() or "", self.m_search.text(), self.m_problems_only.isChecked(),
                      self.m_voiced_only.isChecked())  # fmt: skip

    def _refilter(self, note: str = "") -> None:
        """Run the filters over the document off the window's thread; ``note`` stays in front of the counts in the
        status line (what an import did)."""
        if self.doc is None:
            return
        self.m_generation += 1
        mine, doc, flt = self.m_generation, self.doc, self._filter()

        def work():
            return doc.rows(flt), doc.counts()

        def done(result) -> None:
            if mine != self.m_generation or doc is not self.doc:
                return  # a newer filter run, or another game opened meanwhile
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("texts.texts_not_read", error=result))
                return
            ids, counts = result
            self.m_model.show(ids)
            line = tr(
                "texts.texts_shown_untranslated_outdated",
                shown=len(ids), all=sum(counts.values()), untranslated=counts[UNTRANSLATED],
                outdated=counts[OUTDATED], changed=counts[CHANGED],
            )  # fmt: skip
            self.statusBar().showMessage(f"{note}  |  {line}" if note else line)
            if ids:
                self.m_table.selectRow(0)

        self.m_background.run(work, done)

    def _show(self, key: str) -> None:
        if not key or self.doc is None:
            self.m_detail.show_row(SRowView())
            return
        doc, entry = self.doc, self.doc.entry(key)
        where = f"{key}   {entry.file}" if entry else key
        name = f"   {entry.name}" if entry and entry.name else ""
        group = f"   {tr('texts.group_id', id=entry.folder[-8:])}" if entry and entry.folder else ""
        voice = doc.voice(key)
        kind = doc.kind_label(key)
        self.m_detail.show_row(SRowView(
            key, where + name + group + (f"   [{kind}]" if kind else ""), doc.source_text(key), doc.target_text(key),
            doc.one_line, doc.glossary(), lambda text, style: doc.glyph_problems(text, style, key),
            self._twin_lines(key), self._speaker(voice), doc.playable(key),
            drawn_in=", ".join(doc.linked_names(key)), face=lambda style: doc.face(style, key),
        ))  # fmt: skip
        if voice is not None and voice.path:
            self._show_lips(key)

    @staticmethod
    def _speaker(voice) -> str:
        if voice is None:
            return ""
        line = f"{voice.speaker} ({voice.mood})" if voice.mood else voice.speaker
        return line + (tr("texts.speech_files_text", count=voice.variants) if voice.variants > 1 else "")

    def _show_lips(self, key: str) -> None:
        """The lip file of the picked text's voice, read off the window's thread, shown when the row is still picked."""
        doc = self.doc

        def done(result) -> None:
            if isinstance(result, str) and doc is self.doc and key == self.current_key():
                self.m_detail.m_lips_label.setText(result)
                self.m_detail.m_lips_label.setVisible(bool(result))

        self.m_background.run(lambda: doc.lips(key), done)

    def _twin_lines(self, key: str) -> list[str]:
        doc = self.doc
        return [
            f"{twin}   {doc.kind_label(twin) or tr('texts.main_line')}   {doc.source_text(twin)[:90]}"
            for twin in doc.twins_of(key)
        ]

    def current_key(self) -> str:
        return self.m_model.key_at(self.m_table.currentIndex().row())

    def next_untranslated(self) -> None:
        """Select the next row whose text is untranslated, looking from the picked row on (off the window's thread)."""
        doc, ids = self.doc, list(self.m_model.m_ids)
        start = max(self.m_table.currentIndex().row(), -1) + 1

        def work() -> int:
            return next(
                (
                    candidate
                    for candidate in [*range(start, len(ids)), *range(start)]
                    if doc.status(ids[candidate]) == UNTRANSLATED
                ),
                -1,
            )

        def done(row) -> None:
            if isinstance(row, int) and row >= 0:
                self.m_table.selectRow(row)
            else:
                self.statusBar().showMessage(tr("texts.no_untranslated_text_among_rows"))

        if doc is not None:
            self.m_background.run(work, done)

    # edits
    def _apply(self, text: str) -> None:
        key = self.current_key()
        if not key or self.doc is None:
            return
        status = self.doc.edit(key, text)
        problems = len([problem for problem in self.doc.problems(key) if problem.severity == "error"])
        self.m_model.refresh(key)
        self.statusBar().showMessage(tr("texts.id_status", id=key, status=tr(STATUS_NAMES[status]))
                                     + (tr("texts.n_problems", n=problems) if problems else ""))  # fmt: skip

    def _revert(self) -> None:
        key = self.current_key()
        if key and self.doc is not None:
            self.doc.revert(key)
            self.m_model.refresh(key)
            self._show(key)

    def undo(self) -> None:
        self._walk(self.doc.undo() if self.doc else "")

    def redo(self) -> None:
        self._walk(self.doc.redo() if self.doc else "")

    def _walk(self, label: str) -> None:
        if label:
            self.m_model.show(self.m_model.m_ids)
            self.statusBar().showMessage(tr("texts.done_again_undone", label=label))
            self._show(self.current_key())

    def new_text(self) -> None:
        """A text of the project's own: the id from the project's range, its words in the source language."""
        if self.doc is None:
            return
        doc = self.doc
        key, ok = QInputDialog.getText(
            self, tr("texts.new_text"), tr("texts.id_colon"), text=doc.free_id(self.project().guid_range[0])
        )
        key = key.strip()
        if not ok or not key:
            return  # cancelled: the text itself is not asked for
        if doc.has(key):
            self.statusBar().showMessage(tr("texts.id_text_already", id=key))
            return
        words, ok = QInputDialog.getMultiLineText(
            self, tr("texts.new_text"), tr("texts.the_text_in_language", language=doc.m_source)
        )
        if ok and words.strip():
            doc.edit(key, words, doc.m_source)
            self.m_search.setText(key)
            self._refilter()

    def edit_glossary(self) -> None:
        if self.doc is None:
            return
        dialog = CGlossaryDialog(self, self.doc.m_edits.m_glossary.get(self.doc.m_target, {}), self.doc.glossary().game)
        if dialog.exec():
            self.doc.m_edits.m_glossary[self.doc.m_target] = dialog.terms()
            self.doc.m_dirty = True
            self._show(self.current_key())

    # the project and files
    def save(self) -> bool:
        """The edits into the project; False (and the reason shown) when they could not be written."""
        if self.doc is None:
            return True
        try:
            project = self.project()
            self.doc.m_edits.save(projectpart.edits_path(project, self.m_key))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return False
        self.doc.m_dirty = False
        note = "" if self.m_key == project.game else " " + tr("texts.project_builds_kept_not_built", game=project.game)
        self.statusBar().showMessage(tr("texts.texts_kept_project", count=self.doc.m_edits.count()) + note)
        return True

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another game opened); True when there are none."""
        return self.doc is None or not self.doc.m_dirty or self._ask_save()

    def _ask_save(self) -> bool:
        """False when the person cancelled or the save failed (nothing is closed or opened)."""
        return widgets.ask_keep(self, tr(TITLE), tr("texts.keep_edits_project_first"), self.save)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about unkept edits, nothing released."""
        return self.doc is None or not self.doc.m_dirty or self._ask_save()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        self.m_player.stop()
        super().closeEvent(event)

    def import_file(self) -> None:
        if self.doc is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("texts.import_translation"), "", tr("texts.translations_csv_xlf_xliff_po")
        )
        if path:
            self.import_path(path)

    def import_path(self, path: str, is_waiting: bool = False) -> None:
        """Read a translation file and apply it, both off the window's thread; the question whether to add the ids the
        game does not have is asked in between."""
        doc = self.doc
        if doc is None:
            return

        def read():
            rows = exchange.read(exchange.format_of(path), Path(path).read_bytes())
            known = set(doc.ids())
            return rows, sum(1 for row in rows if row.id not in known)

        def got(result) -> None:
            if doc is not self.doc:
                return  # another game was opened while the file was read: its rows are not that game's
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("texts.path_not_read_error", path=path, error=result))
                return
            rows, unknown = result
            self._apply_rows(doc, rows, bool(unknown) and self.ask_add(unknown), is_waiting)

        self.m_background.run(read, got, is_waiting)

    def ask_add(self, unknown: int) -> bool:
        question = tr("texts.ids_not_game_add_new", n=unknown)
        return QMessageBox.question(self, tr(TITLE), question) == QMessageBox.StandardButton.Yes

    def _apply_rows(self, doc: CTextDoc, rows: list[exchange.SRow], add: bool, is_waiting: bool) -> None:
        self.setEnabled(False)

        def done(result) -> None:
            self.setEnabled(True)
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("assets.import_failed_error", error=result))
                return
            self._refilter(result.line())

        self.m_background.run(lambda: exchange.apply(doc, rows, add_new=add), done, is_waiting)

    def export_file(self) -> None:
        if self.doc is None:
            return
        name = f"{self.m_key}-{self.doc.m_target}"
        path, _ = QFileDialog.getSaveFileName(self, tr("texts.export_translation"), name + exchange.SUFFIXES["csv"],
                                              tr("texts.csv_xliff_xlf_po"))  # fmt: skip
        if path:
            self.export_path(path)

    def export_path(self, path: str, is_waiting: bool = False) -> None:
        """The rows the filters show, written to ``path`` (its suffix says the format) off the window's thread."""
        doc, ids, key = self.doc, list(self.m_model.m_ids), self.m_key
        try:
            fmt = exchange.format_of(path)
        except ValueError as exc:
            self.statusBar().showMessage(tr("texts.path_not_written_error", path=path, error=exc))
            return

        def work() -> int:
            rows = exchange.rows_of(doc, ids)
            Path(path).write_bytes(exchange.write(fmt, rows, key, doc.m_source, doc.m_target))
            return len(rows)

        def done(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("texts.path_not_written_error", path=path, error=result))
            else:
                self.statusBar().showMessage(tr("texts.rows_written_to", count=result, path=path))

        self.m_background.run(work, done, is_waiting)

    # twins, voices and glyphs
    def _fill_twins(self) -> None:
        key = self.current_key()
        if key and self.doc is not None:
            count = self.doc.fill_twins(key)
            self.m_model.show(self.m_model.m_ids)
            self.statusBar().showMessage(tr("texts.twin_lines_filled", count=count))

    def _play(self, side: str, is_waiting: bool = False) -> None:
        """Play the picked text's voice in the language of one side: the clip is read and decoded off the window's thread."""
        key, doc = self.current_key(), self.doc
        if not key or doc is None:
            return

        def work() -> Path:
            return doc.clip_file(key, side)

        def done(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("common.cannot_play_error", error=result))
                return
            self.m_player.start(result)

        self.m_background.run(work, done, is_waiting)

    def audit_glyphs(self) -> None:
        """Every character of the target language's texts that the game's fonts lack, counted (off the window's thread)."""
        doc = self.doc
        if doc is None:
            return
        language = doc.m_target

        def work():
            return doc.m_fonts.audit(language, (doc.target_text(k) for k in doc.ids()))

        def done(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("texts.glyphs_not_checked", error=result))
                return
            lines = []
            for character, count, other in result[:40]:
                reason = tr("texts.another_text_style_draws") if other else tr("texts.no_font_game_draws")
                lines.append(f"U+{ord(character):04X}  {character}  {tr('texts.n_texts', n=count)}  {reason}")
            QMessageBox.information(self, tr(TITLE), "\n".join(lines) or tr("texts.every_character_glyph"))

        self.m_background.run(work, done)
