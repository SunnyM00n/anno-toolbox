"""The asset editor window's exchange with files (``CAssetWindow`` takes them in as ``CExchangeActions``): a mod folder,
an archive mod or a mod profile imported as a change set, the balancing table and the texts out and in as CSV, an
asset or the table printed. Use: ``docs/asset-editor.md``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..infra.i18n import N_, tr
from ..modloader import mods as modlist
from ..suite import project
from . import archiveimport, balancing, checks, modimport, printing, report

TITLE = N_("assets.asset_editor")


class CExchangeActions:
    """The menu actions over the window's ``doc``, ``game``, ``settings``, ``state`` and panels."""

    # importing a mod
    def import_mod(self, folder: Path | None = None, is_waiting: bool = False) -> None:
        """A mod folder's ModOps applied to the game and opened as a change set (``docs/tool-suite.md`` 17)."""
        if folder is None:
            start = self.m_settings.mods if self.m_doc.m_changes.m_game == "anno117" else self.m_settings.mods1800
            name = QFileDialog.getExistingDirectory(self, tr("assets.mod_folder_import", title=tr(TITLE)), start or "")
            if not name:
                return
            folder = Path(name)
        self._import(lambda: modimport.import_folder(self.m_game, folder, None), is_waiting)

    def import_archive(self, archive: Path | None = None, is_waiting: bool = False) -> None:
        """An archive mod of an older game compared with the game's files and opened as a change set."""
        if archive is None:
            name, _ = QFileDialog.getOpenFileName(
                self, tr("assets.archive_mod_import", title=tr(TITLE)), "", "Archives (*.rda)"
            )  # i18n: ok (a file filter)
            if not name:
                return
            archive = Path(name)
        if not str(self.m_settings.workspace or "").strip():  # a relative folder would be the current one
            self.say(tr("moddiff.workspace_not_set_settings"))
            return
        carry_to = Path(self.m_settings.workspace) / "imported-archives" / archive.stem
        self._import(lambda: archiveimport.import_archive(self.m_game, archive, carry_to), is_waiting)

    def import_profile(self, profile: Path | None = None, is_waiting: bool = False) -> None:
        """The mods a profile switches on, in the loader's order, as one change set."""
        mods = Path(self.m_settings.mods) if self.m_settings.mods else None
        if profile is None:
            active = modlist.active_profile(mods) if mods else None
            start = str(active or (mods / "profiles" if mods else ""))
            name, _ = QFileDialog.getOpenFileName(
                self, tr("assets.title_mod_profile", title=tr(TITLE)), start, tr("assets.profiles_txt")
            )
            if not name:
                return
            profile = Path(name)
        game_dir = Path(self.m_settings.anno117) if self.m_settings.anno117 else None
        folders = [root.path for root in modlist.loader_roots("anno117", game_dir, mods or Path(profile).parent.parent)]
        self._import(lambda: modimport.import_profile(self.m_game, profile, folders), is_waiting)

    def _import(self, work, is_waiting: bool) -> None:
        if not self.confirm_close():
            return
        self.say(tr("assets.importing_first_import_large_mod"))
        if is_waiting:
            return self._imported(work())
        self.m_background.run(work, self._imported)

    def _imported(self, result) -> None:
        if isinstance(result, Exception):
            QMessageBox.warning(
                self, tr(TITLE), tr("assets.import_failed_error", error=f"{type(result).__name__}: {result}")
            )
            return
        key = self.m_game.m_layout.key
        path = project.folder_of(self.m_settings) / project.ASSET_EDITS / f"{key}-{result.changes.m_mod_id}.json"
        self.state.setdefault("edits", {})[key] = str(path)
        self._attach(self.m_game.document(result.changes, path))
        self.m_doc.m_undo.m_clean = -1  # not saved yet
        self.sync()
        result.session = None  # the patched documents: large, and the change set holds what matters
        self.m_last_import = result
        lines = result.lines()
        self.m_findings.present(
            [checks.SFinding(None, "import", checks.WARNING, event.line()) for event in result.dropped]
        )
        self.m_docks["Checks"].raise_()
        box = QMessageBox(QMessageBox.Icon.Information, tr(TITLE), "\n".join(lines[:4]), parent=self)
        box.setDetailedText("\n".join(lines))
        box.setInformativeText(tr("assets.saved_change_set_file_gt"))
        box.show()
        self.say(lines[1])

    # tables
    def _file(self, save: bool, what: str) -> Path | None:
        dialog = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        name, _ = dialog(
            self, tr("assets.title_what", title=tr(TITLE), what=what), "", "CSV (*.csv)"
        )  # i18n: ok (a file filter)
        return Path(name) if name else None

    def _read_table(self, path: Path) -> str | None:
        """A table file's text (a spreadsheet's byte order mark dropped); None, and said, when it does not read."""
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            self._failed(error)
            return None

    def _write_table(self, path: Path, text: str) -> bool:
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as error:  # open in a spreadsheet, a folder that takes no file
            self._failed(error)
            return False
        return True

    def export_csv(self, path: Path | None = None) -> None:
        path = path or self._file(True, tr("assets.balancing_table"))
        if path:
            text = balancing.export_csv(self.m_doc, self.m_tree.selected_group(), self.m_balancing.paths())
            self._write_table(path, text)

    def import_csv(self, path: Path | None = None) -> None:
        path = path or self._file(False, tr("assets.balancing_table"))
        text = self._read_table(path) if path else None
        if text is not None:
            problems = balancing.import_csv(self.m_doc, text)
            self.m_findings.present([checks.SFinding(None, "csv", checks.WARNING, problem) for problem in problems])

    def export_texts(self, path: Path | None = None) -> None:
        path = path or self._file(True, tr("assets.texts"))
        if path:
            lang = self.m_texts.m_language.currentText()
            texts = self.m_game.texts(lang)
            out = balancing.export_texts(texts, self.m_doc.m_changes.m_texts.get(lang))
            if self._write_table(path, out):
                self.say(tr("assets.n_texts_words", n=len(texts), words=balancing.words(texts)))

    def import_texts(self, path: Path | None = None) -> None:
        path = path or self._file(False, tr("assets.texts"))
        text = self._read_table(path) if path else None
        if text is not None:
            lang = self.m_texts.m_language.currentText()
            count = balancing.import_texts(self.m_doc, lang, text, self.m_game.texts(lang))
            self.say(tr("assets.n_texts_changed", n=count))

    # printing
    def print_asset(self, to_pdf: bool = False, pdf: Path | None = None) -> bool:
        """The asset shown as a page (values with their origins, the game's value where changed, texts and their
        words) to a printer, or to a PDF file (``pdf``, else asked for)."""
        if self.m_doc is None or self.m_guid is None or not self.m_doc.exists(self.m_guid):
            self.say(tr("assets.pick_asset_print_first"))
            return False
        language = self.m_texts.m_language.currentText()
        html = report.asset_html(self.m_doc, self.m_guid, self.m_game.texts(language), language)
        return self._print(html, to_pdf, pdf, f"{self.m_doc.name(self.m_guid) or self.m_guid}.pdf")

    def print_table(self, to_pdf: bool = False, pdf: Path | None = None) -> bool:
        """The balancing table as it stands (its group's assets against the columns) to a printer or a PDF file."""
        columns = self.m_balancing.paths()
        if self.m_doc is None or not columns:
            self.say(tr("assets.choose_balancingviews_columns_first"))
            return False
        guids = (self.m_tree.selected_group() or [])[:500]
        title = f"{self.m_game.m_layout.name}: " + ", ".join(columns)
        html = report.table_html(title, columns, balancing.grid(self.m_doc, guids, columns))
        return self._print(html, to_pdf, pdf, "balancing.pdf")

    def _print(self, html: str, to_pdf: bool, pdf: Path | None, name: str) -> bool:
        if to_pdf and pdf is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                tr("assets.title_pdf_file", title=tr(TITLE)),
                name,
                "PDF (*.pdf)",  # i18n: ok (a file filter)
            )
            if not chosen:
                return False
            pdf = Path(chosen)
        done = printing.print_html(self, html, pdf)
        if done:
            self.say(tr("common.wrote_path", path=pdf) if pdf is not None else tr("assets.sent_to_printer"))
        return done
