"""The UI editor's "Preview settings" panel (a dock of the View menu): shows and changes everything the preview assumes of
the player's game, on top of ``previewdefaults.CStore`` (one store, the panel keeps none of its own). Every control writes
through ``CStore.set`` and the store's listeners lay the preview out again; the panel reads the store back when it changes
(a reset, a play mode choice). What a control offers comes from ``previewcatalogue`` (read off the window's thread: the
panel says "loading" until it is there). docs/ui-editor.md, "Preview settings".

``CPreviewPanel(store, catalogue, icons, note)`` is the widget (the tests build it alone); ``install`` makes the window's
and ``open_game`` hands it a game's store. The tabs are ``previewsections``, ``previewsetup`` and ``previewprogress``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite import widgets
from . import previewdefaults as pd
from .playmode import CPlayedList
from .previewcatalogue import SCatalogue
from .previewprogress import CProgressionSection, CResearchSection
from .previewsections import CCharactersSection, CParticipantSection, CPlatformSection, CValuesSection
from .previewsetup import CSetupSection
from .previewwidgets import CIcons, CResetButton, CSection

TABS = (
    (N_("ui.preview_tab_participant"), CParticipantSection),
    (N_("ui.preview_tab_characters"), CCharactersSection),
    (N_("ui.preview_tab_session"), CSetupSection),
    (N_("ui.preview_tab_progression"), CProgressionSection),
    (N_("ui.preview_tab_research"), CResearchSection),
    (N_("ui.preview_tab_platform"), CPlatformSection),
    (N_("ui.preview_tab_values"), CValuesSection),
)
TITLE = N_("ui.preview_settings")


class CPreviewPanel(QWidget):
    def __init__(self, store: pd.CStore | None = None, catalogue: SCatalogue | None = None, icons: CIcons | None = None,
                 note=None, loading: bool = False) -> None:  # fmt: skip
        super().__init__()
        self.m_store, self.m_icons, self.m_note_source = None, icons, note
        self.m_has_catalogue, self.m_loading = False, False
        self._m_catalogue = SCatalogue()
        self.m_busy = 0  # above 0: the controls are being filled from the store, their signals are not changes
        self.m_sections: list[CSection] = []
        self.m_resets: list[CResetButton] = []
        self.m_note = QLabel(tr("ui.preview_note_live"))
        self.m_note.setWordWrap(True)
        self.m_note.setVisible(False)
        self.m_state = QLabel()
        self.m_state.setWordWrap(True)
        self.m_tabs = QTabWidget()
        self.m_keep = QCheckBox(tr("ui.preview_keep"))
        self.m_reset_all = QPushButton(tr("ui.preview_reset_all"))
        self.m_reset_played = QPushButton(tr("ui.preview_reset_played"))
        self.m_keep.setToolTip(tr("ui.preview_keep_tip"))
        self.m_reset_all.setToolTip(tr("ui.preview_reset_all_tip"))
        self.m_reset_played.setToolTip(tr("ui.preview_reset_played_tip"))
        self.m_keep.toggled.connect(self._keep_toggled)
        self.m_reset_all.clicked.connect(lambda _c=False: self.m_store and self.m_store.reset())
        self.m_reset_played.clicked.connect(lambda _c=False: self.m_store and self.m_store.reset(played=True))
        self.m_played = CPlayedList()  # the choices Play mode made, as the store holds them
        lay = QVBoxLayout(self)
        for widget in (
            self.m_note,
            self.m_state,
            self.m_tabs,
            self.m_played,
            self.m_keep,
            self.m_reset_all,
            self.m_reset_played,
        ):
            lay.addWidget(widget)
        lay.setStretchFactor(self.m_tabs, 1)
        if icons is not None:
            icons.ready.connect(self._icons_ready)
        if store is not None:
            self.set_store(store, loading)
        if catalogue is not None:
            self.set_catalogue(catalogue)
        elif store is None:
            self._rebuild()

    # what the panel shows

    @property
    def catalogue(self) -> SCatalogue:
        return self._m_catalogue

    def set_store(self, store: pd.CStore | None, loading: bool = False) -> None:
        """Show ``store`` (a game just opened; None: no game), its choices still to be read when ``loading``."""
        if self.m_store is not None:
            self.m_store.m_listeners[:] = [
                listener for listener in self.m_store.m_listeners if listener != self._on_change
            ]
        self.m_store, self.m_loading = store, loading
        self._m_catalogue, self.m_has_catalogue = SCatalogue(), False
        if store is not None:
            store.m_listeners.append(self._on_change)
        self._rebuild()

    def set_catalogue(self, catalogue: SCatalogue | None) -> None:
        """The game's choices arrived (None: there are none, the campaign's own are offered)."""
        self._m_catalogue, self.m_has_catalogue = catalogue or SCatalogue(), catalogue is not None
        self.m_loading = False
        self._rebuild()

    def _on_change(self, _defaults) -> None:
        self.refresh()

    def _rebuild(self) -> None:
        current = self.m_tabs.currentIndex()
        for i in reversed(range(self.m_tabs.count())):
            page = self.m_tabs.widget(i)
            self.m_tabs.removeTab(i)
            page.deleteLater()  # the page and the section in it
        self.m_sections, self.m_resets = [], []
        if self.m_store is None:
            self.m_state.setText(tr("ui.preview_open_game"))
            self._enable_footer()
            return
        self.m_state.setText(tr("ui.preview_loading") if self.m_loading else "" if self.m_has_catalogue
                           else tr("ui.preview_no_data"))  # fmt: skip
        self.m_state.setVisible(bool(self.m_state.text()))
        self.m_busy += 1
        try:
            for title, cls in TABS:
                section = cls(self)
                section.build()
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(section)
                scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                self.m_tabs.addTab(scroll, tr(title))
                self.m_sections.append(section)
            self.refresh()
        finally:
            self.m_busy -= 1
        self.m_tabs.setCurrentIndex(min(max(current, 0), self.m_tabs.count() - 1))
        self._assign_keys()
        self._request_icons()

    def refresh(self) -> None:
        """Read the store into the controls."""
        if self.m_store is None:
            return
        self.m_busy += 1
        try:
            for section in self.m_sections:
                section.refresh()
            self.m_played.show_choices(self.m_store)
            self.m_keep.setChecked(self.m_store.m_per_project)
        finally:
            self.m_busy -= 1
        self._enable_footer()
        self.refresh_note()

    def refresh_note(self) -> None:
        """The note that live values or a recording win over these settings, while they are shown."""
        self.m_note.setVisible(bool(self.m_note_source and self.m_note_source()))

    # resets

    def reset_button(self, names: tuple[str, ...]) -> CResetButton:
        button = CResetButton(self, names)
        self.m_resets.append(button)
        return button

    def is_set(self, name: str) -> bool:
        """Whether a value differs from the campaign's: a field, ``options.<name>``, ``options.<prefix>*`` (any option
        of that prefix) or ``characters:<role>``."""
        defaults = self.m_store.m_defaults
        if name.startswith("characters:"):
            return name.split(":", 1)[1] in defaults.characters
        if name.endswith(".*"):
            return any(k.startswith(name[len("options.") : -1]) for k in defaults.options)
        if name.startswith("options."):
            return name[len("options.") :] in defaults.options
        return name in pd.changes(defaults)

    def reset_names(self, names: tuple[str, ...]) -> None:
        """Put the values ``names`` (as ``is_set`` takes them) back to the campaign's, kept and told once."""
        defaults, out = self.m_store.m_defaults, {}
        for name in names:
            if name.startswith("characters:"):
                cast = {
                    role: character for role, character in defaults.characters.items() if role != name.split(":", 1)[1]
                }
                out["characters"] = cast or None
            elif name.endswith(".*"):
                out.update({f"options.{k}": None for k in defaults.options if k.startswith(name[len("options.") : -1])})
            else:
                out[name] = None
        self.m_store.set_many(out)

    def _enable_footer(self) -> None:
        store = self.m_store
        for button in self.m_resets:
            button.setEnabled(store is not None and self.is_set_any(button.m_names))
        self.m_reset_all.setEnabled(store is not None and bool(pd.changes(store.m_defaults)))
        self.m_reset_played.setEnabled(store is not None and bool(store.play_names()))
        can_keep = store is not None and store.m_project_file is not None
        self.m_keep.setEnabled(can_keep)
        self.m_keep.setToolTip(tr("ui.preview_keep_tip") if can_keep else tr("ui.preview_keep_none"))

    def is_set_any(self, names: tuple[str, ...]) -> bool:
        return any(self.is_set(name) for name in names)

    def _keep_toggled(self, on: bool) -> None:
        if self.m_store is not None and not self.m_busy and on != self.m_store.m_per_project:
            self.m_store.keep_in_project(on)
            self.refresh()

    # pictures and keys

    def _request_icons(self) -> None:
        if self.m_icons is None:
            return
        catalogue = self.catalogue
        paths = [option.icon for options in (catalogue.portraits, catalogue.emblems, catalogue.unlocks, catalogue.research, *catalogue.banner.values(),
                                        *catalogue.titles.values()) for option in options]  # fmt: skip
        paths += [character.portrait_icon for people in catalogue.characters.values() for character in people]
        paths += [character.portrait_icon for role in pd.CAMPAIGN.values() for character in role]
        self.m_icons.request(paths)

    def _icons_ready(self) -> None:
        for section in self.m_sections:
            section.update_icons()

    def _assign_keys(self) -> None:
        """Access keys no two of which clash: the tab titles and the footer first, each tab's labels and buttons
        with none of those."""
        top = [tr(title) for title, _cls in TABS] + [tr("ui.preview_keep"), tr("ui.preview_reset_all"),
                                              tr("ui.preview_reset_played")]  # fmt: skip
        keyed = widgets.access_keys(top)
        for i in range(self.m_tabs.count()):
            self.m_tabs.setTabText(i, keyed[i])
        self.m_keep.setText(keyed[-3])
        self.m_reset_all.setText(keyed[-2])
        self.m_reset_played.setText(keyed[-1])
        taken = widgets.access_letters(keyed)
        for section in self.m_sections:
            items = list(section.m_keyed)
            for item, text in zip(items, widgets.access_keys([action.text() for action in items], taken), strict=True):
                item.setText(text)


def install(window) -> CPreviewPanel:
    """The window's panel (``window.preview_panel``); its note follows the live values and recordings of the window."""
    from . import livevalues

    def live() -> bool:
        return bool(livevalues.texts(window) or livevalues.overrides(window))

    window.preview_panel = CPreviewPanel(icons=CIcons(window.m_background), note=live)
    return window.preview_panel


def open_game(window, store: pd.CStore, loading: bool) -> None:
    """A game was opened: its pictures are read for the panel's grids, and the panel shows its store."""
    from .pictures import CPictures
    from .previewwidgets import image_loader

    panel = window.preview_panel
    if panel.m_icons is not None and window.game is not None:
        panel.m_icons.reset(image_loader(CPictures(window.game.m_files)))
    panel.set_store(store, loading)
