"""The UI editor's "Preview settings" panel (a dock of the View menu): shows and changes everything the preview assumes of
the player's game, on top of ``previewdefaults.Store`` (one store, the panel keeps none of its own). Every control writes
through ``Store.set`` and the store's listeners lay the preview out again; the panel reads the store back when it changes
(a reset, a play mode choice). What a control offers comes from ``previewcatalogue`` (read off the window's thread: the
panel says "loading" until it is there). docs/ui-editor.md, "Preview settings".

``PreviewPanel(store, catalogue, icons, note)`` is the widget (the tests build it alone); ``install`` makes the window's
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
from .playmode import PlayedList
from .previewcatalogue import Catalogue
from .previewprogress import ProgressionSection, ResearchSection
from .previewsections import CharactersSection, PlatformSection, PlayerSection, ValuesSection
from .previewsetup import SetupSection
from .previewwidgets import Icons, ResetButton, Section

TABS = (
    (N_("ui.preview_tab_player"), PlayerSection),
    (N_("ui.preview_tab_characters"), CharactersSection),
    (N_("ui.preview_tab_session"), SetupSection),
    (N_("ui.preview_tab_progression"), ProgressionSection),
    (N_("ui.preview_tab_research"), ResearchSection),
    (N_("ui.preview_tab_platform"), PlatformSection),
    (N_("ui.preview_tab_values"), ValuesSection),
)
TITLE = N_("ui.preview_settings")


class PreviewPanel(QWidget):
    def __init__(self, store: pd.Store | None = None, catalogue: Catalogue | None = None, icons: Icons | None = None,
                 note=None, loading: bool = False) -> None:  # fmt: skip
        super().__init__()
        self.store, self.icons, self.note_source = None, icons, note
        self.has_catalogue, self.loading = False, False
        self._catalogue = Catalogue()
        self.busy = 0  # above 0: the controls are being filled from the store, their signals are not changes
        self.sections: list[Section] = []
        self.resets: list[ResetButton] = []
        self.note = QLabel(tr("ui.preview_note_live"))
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.tabs = QTabWidget()
        self.keep = QCheckBox(tr("ui.preview_keep"))
        self.reset_all = QPushButton(tr("ui.preview_reset_all"))
        self.reset_played = QPushButton(tr("ui.preview_reset_played"))
        self.keep.setToolTip(tr("ui.preview_keep_tip"))
        self.reset_all.setToolTip(tr("ui.preview_reset_all_tip"))
        self.reset_played.setToolTip(tr("ui.preview_reset_played_tip"))
        self.keep.toggled.connect(self._keep_toggled)
        self.reset_all.clicked.connect(lambda _c=False: self.store and self.store.reset())
        self.reset_played.clicked.connect(lambda _c=False: self.store and self.store.reset(played=True))
        self.played = PlayedList()  # the choices Play mode made, as the store holds them
        lay = QVBoxLayout(self)
        for widget in (self.note, self.state, self.tabs, self.played, self.keep, self.reset_all, self.reset_played):
            lay.addWidget(widget)
        lay.setStretchFactor(self.tabs, 1)
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
    def catalogue(self) -> Catalogue:
        return self._catalogue

    def set_store(self, store: pd.Store | None, loading: bool = False) -> None:
        """Show ``store`` (a game just opened; None: no game), its choices still to be read when ``loading``."""
        if self.store is not None:
            self.store.listeners[:] = [fn for fn in self.store.listeners if fn != self._on_change]
        self.store, self.loading = store, loading
        self._catalogue, self.has_catalogue = Catalogue(), False
        if store is not None:
            store.listeners.append(self._on_change)
        self._rebuild()

    def set_catalogue(self, catalogue: Catalogue | None) -> None:
        """The game's choices arrived (None: there are none, the campaign's own are offered)."""
        self._catalogue, self.has_catalogue = catalogue or Catalogue(), catalogue is not None
        self.loading = False
        self._rebuild()

    def _on_change(self, _defaults) -> None:
        self.refresh()

    def _rebuild(self) -> None:
        current = self.tabs.currentIndex()
        for i in reversed(range(self.tabs.count())):
            page = self.tabs.widget(i)
            self.tabs.removeTab(i)
            page.deleteLater()  # the page and the section in it
        self.sections, self.resets = [], []
        if self.store is None:
            self.state.setText(tr("ui.preview_open_game"))
            self._enable_footer()
            return
        self.state.setText(tr("ui.preview_loading") if self.loading else "" if self.has_catalogue
                           else tr("ui.preview_no_data"))  # fmt: skip
        self.state.setVisible(bool(self.state.text()))
        self.busy += 1
        try:
            for title, cls in TABS:
                section = cls(self)
                section.build()
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(section)
                scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                self.tabs.addTab(scroll, tr(title))
                self.sections.append(section)
            self.refresh()
        finally:
            self.busy -= 1
        self.tabs.setCurrentIndex(min(max(current, 0), self.tabs.count() - 1))
        self._assign_keys()
        self._request_icons()

    def refresh(self) -> None:
        """Read the store into the controls."""
        if self.store is None:
            return
        self.busy += 1
        try:
            for section in self.sections:
                section.refresh()
            self.played.show_choices(self.store)
            self.keep.setChecked(self.store.per_project)
        finally:
            self.busy -= 1
        self._enable_footer()
        self.refresh_note()

    def refresh_note(self) -> None:
        """The note that live values or a recording win over these settings, while they are shown."""
        self.note.setVisible(bool(self.note_source and self.note_source()))

    # resets

    def reset_button(self, names: tuple[str, ...]) -> ResetButton:
        button = ResetButton(self, names)
        self.resets.append(button)
        return button

    def is_set(self, name: str) -> bool:
        """Whether a value differs from the campaign's: a field, ``options.<name>``, ``options.<prefix>*`` (any option
        of that prefix) or ``characters:<role>``."""
        d = self.store.defaults
        if name.startswith("characters:"):
            return name.split(":", 1)[1] in d.characters
        if name.endswith(".*"):
            return any(k.startswith(name[len("options.") : -1]) for k in d.options)
        if name.startswith("options."):
            return name[len("options.") :] in d.options
        return name in pd.changes(d)

    def reset_names(self, names: tuple[str, ...]) -> None:
        """Put the values ``names`` (as ``is_set`` takes them) back to the campaign's, kept and told once."""
        d, out = self.store.defaults, {}
        for name in names:
            if name.startswith("characters:"):
                cast = {r: g for r, g in d.characters.items() if r != name.split(":", 1)[1]}
                out["characters"] = cast or None
            elif name.endswith(".*"):
                out.update({f"options.{k}": None for k in d.options if k.startswith(name[len("options.") : -1])})
            else:
                out[name] = None
        self.store.set_many(out)

    def _enable_footer(self) -> None:
        store = self.store
        for button in self.resets:
            button.setEnabled(store is not None and self.is_set_any(button.names))
        self.reset_all.setEnabled(store is not None and bool(pd.changes(store.defaults)))
        self.reset_played.setEnabled(store is not None and bool(store.play_names()))
        can_keep = store is not None and store.project_file is not None
        self.keep.setEnabled(can_keep)
        self.keep.setToolTip(tr("ui.preview_keep_tip") if can_keep else tr("ui.preview_keep_none"))

    def is_set_any(self, names: tuple[str, ...]) -> bool:
        return any(self.is_set(n) for n in names)

    def _keep_toggled(self, on: bool) -> None:
        if self.store is not None and not self.busy and on != self.store.per_project:
            self.store.keep_in_project(on)
            self.refresh()

    # pictures and keys

    def _request_icons(self) -> None:
        if self.icons is None:
            return
        cat = self.catalogue
        paths = [o.icon for options in (cat.portraits, cat.emblems, cat.unlocks, cat.research, *cat.banner.values(),
                                        *cat.titles.values()) for o in options]  # fmt: skip
        paths += [c.portrait_icon for people in cat.characters.values() for c in people]
        paths += [c.portrait_icon for role in pd.CAMPAIGN.values() for c in role]
        self.icons.request(paths)

    def _icons_ready(self) -> None:
        for section in self.sections:
            section.update_icons()

    def _assign_keys(self) -> None:
        """Access keys no two of which clash: the tab titles and the footer first, each tab's labels and buttons
        with none of those."""
        top = [tr(t) for t, _cls in TABS] + [tr("ui.preview_keep"), tr("ui.preview_reset_all"),
                                              tr("ui.preview_reset_played")]  # fmt: skip
        keyed = widgets.access_keys(top)
        for i in range(self.tabs.count()):
            self.tabs.setTabText(i, keyed[i])
        self.keep.setText(keyed[-3])
        self.reset_all.setText(keyed[-2])
        self.reset_played.setText(keyed[-1])
        taken = widgets.access_letters(keyed)
        for section in self.sections:
            items = list(section.keyed)
            for item, text in zip(items, widgets.access_keys([w.text() for w in items], taken), strict=True):
                item.setText(text)


def install(window) -> PreviewPanel:
    """The window's panel (``window.preview_panel``); its note follows the live values and recordings of the window."""
    from . import livevalues

    def live() -> bool:
        return bool(livevalues.texts(window) or livevalues.overrides(window))

    window.preview_panel = PreviewPanel(icons=Icons(window.background), note=live)
    return window.preview_panel


def open_game(window, store: pd.Store, loading: bool) -> None:
    """A game was opened: its pictures are read for the panel's grids, and the panel shows its store."""
    from .pictures import Pictures
    from .previewwidgets import image_loader

    panel = window.preview_panel
    if panel.icons is not None and window.game is not None:
        panel.icons.reset(image_loader(Pictures(window.game.files)))
    panel.set_store(store, loading)
