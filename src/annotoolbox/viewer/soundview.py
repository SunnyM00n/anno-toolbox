"""The viewer's sound view: a Wwise bank's sounds, events and music tracks in tables (names from the bank's info file,
codec, embedded or streamed, the media file; each event's sounds, walked through its containers, with the switch
and state choices of its containers), a clip (``.wem``,
``.wav``, ``.mp3``) described and played.

``CSoundView(parent, cache)`` is the entry: ``show_bank(path, data, files)``, ``show_clip(path, data)``. Playing goes
through Qt's media player; ``banks.playable`` says what cannot be decoded. A clip is written to ``cache`` to play.
"""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..exporting import banks
from ..infra.i18n import N_, tr
from ..suite.clipplayer import CClipPlayer
from ..suite.widgets import add_row, read_only_table

COLUMNS = {
    "Sounds": (
        N_("viewer.sound"),
        N_("common.name_lower"),
        N_("viewer.codec"),
        N_("viewer.media"),
        N_("common.file_lower"),
    ),
    "Events": (
        N_("viewer.event"),
        N_("common.name_lower"),
        N_("viewer.sounds_lower"),
        N_("viewer.read"),
        N_("viewer.choices"),
    ),
    "Music": (
        N_("viewer.track"),
        N_("viewer.segment"),
        N_("viewer.seconds"),
        N_("viewer.media"),
        N_("common.file_lower"),
    ),
}
TABLE_TITLES = {
    "Sounds": N_("viewer.sounds"),
    "Events": N_("common.events"),
    "Music": N_("common.music"),
}


class CSoundView(QWidget):
    """A bank's tables or one clip, with Play and Stop."""

    def __init__(self, parent=None, cache: Path | None = None) -> None:
        super().__init__(parent)
        self.m_cache = cache
        self.m_files = None
        self.m_bank = ""
        self.m_listing: banks.SBankListing | None = None
        self.m_bank_info = banks.SBankInfo()
        self.m_clips: list[tuple[str, bytes]] = []  # per sound row: (name, the clip's bytes or b"" to read the file)
        self.m_event_rows: list[int] = []
        self.m_choices: dict[int, int] = {}  # the group -> value picked for the selected event
        self.m_music_rows: list[tuple[str, bytes]] = []
        self.m_summary = QLabel(wordWrap=True)
        self.m_tables = {name: read_only_table(columns) for name, columns in COLUMNS.items()}
        self.m_table = self.m_tables["Sounds"]
        self.m_tabs = QTabWidget()
        for name, table in self.m_tables.items():
            self.m_tabs.tabBar().setTabData(self.m_tabs.addTab(table, tr(TABLE_TITLES[name])), name)
            table.cellDoubleClicked.connect(lambda _r, _c: self._play_selected())
        self.m_play = QPushButton(tr("common.play"))
        self.m_stop = QPushButton(tr("viewer.stop"))
        self.m_status = QLabel()
        button_row = QHBoxLayout()
        for w in (self.m_play, self.m_stop):
            button_row.addWidget(w)
        button_row.addWidget(self.m_status, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.m_summary)
        layout.addLayout(button_row)
        layout.addWidget(self.m_tabs, 1)
        self.m_choice_form = QFormLayout()
        self.m_choice_note = QLabel(wordWrap=True)
        layout.addWidget(self.m_choice_note)
        layout.addLayout(self.m_choice_form)
        self.m_tables["Events"].currentCellChanged.connect(lambda row, *_rest: self._show_choices(row, reset=True))
        self.m_play.clicked.connect(self._play_selected)
        self.m_stop.clicked.connect(self._stop)
        self._m_player = CClipPlayer(self)
        self._m_clip: tuple[str, bytes] | None = None

    def show_bank(self, path: str, data: bytes, files) -> None:
        """A bank: its header and objects above, its sounds, events and music tracks in the tables."""
        self.m_files, self._m_clip, self.m_bank = files, None, path
        self.m_listing = listing = banks.bank_listing(data)
        self.m_bank_info = bank_info = banks.read_info(
            path, files.exists, lambda file_path: files.read(file_path).decode("utf-8", "replace")
        )
        kinds = ", ".join(f"{count} {k}" for k, count in sorted(listing.objects.items(), key=lambda entry: -entry[1]))
        self.m_summary.setText(
            tr(
                "viewer.bank_version_id_sounds_embedded",
                name=bank_info.name or path,
                version=listing.version,
                id=listing.bank_id,
                sounds=len(listing.sounds),
                media=len(listing.media),
                events=len(listing.events),
                named=len(bank_info.events),
                objects=kinds,
            )
        )
        self._fill_sounds(path, listing, bank_info, files)
        self._fill_events(listing, bank_info)
        self._fill_music(path, listing, bank_info, files)
        self.m_status.setText(tr("viewer.double_click_row_select_press"))

    def _clip_of(self, path: str, media: int, stream: str, bank_info, files) -> tuple[str, tuple[str, bytes]]:
        """(where the media is, (name, bytes or b"" to read the file)) of one media id."""
        listing = self.m_listing
        streamed = banks.media_file(path, media, bank_info, files.exists) if stream != "embedded" else ""
        if streamed:  # the whole clip; a bank holds only its first part (the prefetch)
            return streamed, (streamed, b"")
        if media in listing.media:
            where = tr("viewer.bank_start_only") if stream != "embedded" else tr("viewer.in_the_bank")
            return where, (f"{media}.wem", listing.embedded(media))
        return tr("viewer.not_found"), ("", b"")

    def _fill_sounds(self, path: str, listing, bank_info, files) -> None:
        table = self.m_tables["Sounds"]
        table.setRowCount(0)
        self.m_clips = []
        for sound in listing.sounds:
            where, clip = self._clip_of(path, sound.media, sound.stream, bank_info, files)
            add_row(
                table,
                [
                    str(sound.id),
                    bank_info.media.get(sound.media, ("", ""))[0],
                    sound.codec,
                    f"{sound.media} ({sound.stream})",
                    where,
                ],
            )
            self.m_clips.append(clip)
        table.resizeColumnsToContents()

    def _fill_events(self, listing, bank_info) -> None:
        table = self.m_tables["Events"]
        table.setRowCount(0)
        self.m_event_rows = []
        for event in sorted(listing.events, key=lambda event_id: bank_info.events.get(event_id, "~")):
            add_row(table, [str(event), bank_info.events.get(event, ""), "", "", ""])
            self.m_event_rows.append(event)
        table.resizeColumnsToContents()

    def _fill_music(self, path: str, listing, bank_info, files) -> None:
        table = self.m_tables["Music"]
        table.setRowCount(0)
        self.m_music_rows = []
        for track in banks.music_tracks(listing):
            for media, stream in track.sources or ((0, ""),):
                where, clip = (
                    self._clip_of(path, media, stream, bank_info, files)
                    if media
                    else (tr("viewer.no_source_read"), ("", b""))
                )
                add_row(table, [str(track.id), str(track.segment or ""), f"{track.seconds:.1f}" if track.seconds else "",
                                  f"{media} ({stream})" if media else "", where])  # fmt: skip
                self.m_music_rows.append(clip)
        table.resizeColumnsToContents()

    def event_clips(self, event: int) -> tuple[list[tuple[str, bytes]], bool]:
        """The clips an event can play (its sounds and music tracks, walked) and whether the walk read every step."""
        walk = banks.walk_event(self.m_listing, event, self.m_choices)
        found, exact = walk.sounds, walk.exact
        clips = []
        for item in found:
            media, stream = (item.media, item.stream) if isinstance(item, banks.SBankSound) else (item, "streamed")
            _where, clip = self._clip_of(self.m_bank, media, stream, self.m_bank_info, self.m_files)
            if clip[0]:
                clips.append(clip)
        return clips, exact

    def _show_choices(self, row: int, reset: bool = False) -> None:
        """The selected event's switch and state groups as drop-downs (``every value`` plays every branch), and the
        switches and states the event sets itself."""
        if reset:
            self.m_choices = {}
        while self.m_choice_form.rowCount():  # taken, not removed: this runs inside a drop-down's own signal
            taken = self.m_choice_form.takeRow(0)
            for item in (taken.labelItem, taken.fieldItem):
                if item is not None and item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
        if not 0 <= row < len(self.m_event_rows) or self.m_listing is None:
            self.m_choice_note.setText("")
            return
        walk = banks.walk_event(self.m_listing, self.m_event_rows[row], self.m_choices)
        for group, found in walk.groups.items():
            box = QComboBox()
            box.addItem(tr("viewer.every_value"), None)
            for value in found.values:
                name = self.m_bank_info.group_label(group, value)
                box.addItem(tr("viewer.value_start", value=name) if value == found.default else name, value)
            box.setCurrentIndex(max(0, box.findData(self.m_choices.get(group))))
            box.currentIndexChanged.connect(lambda _i, g=group, b=box: self._pick(row, g, b.currentData()))
            group_name = self.m_bank_info.group_label(group)
            label = (
                tr("viewer.state_name", name=group_name)
                if found.state_group
                else tr("viewer.switch_name", name=group_name)
            )
            self.m_choice_form.addRow(label, box)
        sets = [
            f"{self.m_bank_info.group_label(assignment.group)} = {self.m_bank_info.group_label(assignment.group, assignment.value)}"
            for assignment in walk.sets
        ]
        self.m_choice_note.setText(tr("viewer.event_sets", sets=", ".join(sets)) if sets else "")

    def _pick(self, row: int, group: int, value: int | None) -> None:
        if value is None:
            self.m_choices.pop(group, None)
        else:
            self.m_choices[group] = value
        clips, exact = self.event_clips(self.m_event_rows[row])
        table = self.m_tables["Events"]
        table.item(row, 2).setText(str(len(clips)))
        self._show_choices(row)

    def show_clip(self, path: str, data: bytes) -> None:
        """One clip: its facts in the summary, Play plays it."""
        for table in self.m_tables.values():
            table.setRowCount(0)
        self.m_clips, self.m_event_rows, self.m_music_rows, self._m_clip = [], [], [], (path, data)
        try:
            wave_facts = banks.wave_info(data) if not path.lower().endswith((".mp3", ".ogg")) else {"format": path[-3:]}
            facts = ", ".join(
                f"{k} {round(value, 2) if isinstance(value, float) else value}" for k, value in wave_facts.items()
            )
        except banks.CUnplayable as exc:
            facts = str(exc)
        self.m_summary.setText(f"{path}: {facts}")
        self.m_status.setText("")

    def _selected_clip(self) -> tuple[str, bytes] | None:
        tab = self.m_tabs.tabBar().tabData(self.m_tabs.currentIndex())
        row = self.m_tables[tab].currentRow()
        if tab == "Sounds" and 0 <= row < len(self.m_clips):
            return self.m_clips[row]
        if tab == "Music" and 0 <= row < len(self.m_music_rows):
            return self.m_music_rows[row]
        if tab == "Events" and 0 <= row < len(self.m_event_rows):
            clips, exact = self.event_clips(self.m_event_rows[row])
            table = self.m_tables["Events"]
            table.item(row, 2).setText(str(len(clips)))
            table.item(row, 3).setText(tr("viewer.exactly") if exact else tr("viewer.partly_guessed"))
            table.item(row, 4).setText(
                ", ".join(
                    f"{self.m_bank_info.group_label(g)} = {self.m_bank_info.group_label(g, v)}"
                    for g, v in self.m_choices.items()
                )
            )
            return random.choice(clips) if clips else ("", b"")  # a random container picks one, so does the view
        return self._m_clip

    def _play_selected(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            return
        name, data = clip
        if not data and self.m_files is not None and name and self.m_files.exists(name):
            data = self.m_files.read(name)
        if data:
            self.play_clip(name, data)
        else:
            self.m_status.setText(tr("viewer.nothing_play_media_file_not"))

    def play_clip(self, name: str, data: bytes) -> None:
        """Decode a clip into the cache and play it; the status says why when it cannot."""
        why = self._m_player.play(name, data, self.m_cache / "sounds" if self.m_cache is not None else None)
        self.m_status.setText(why or tr("viewer.playing_name", name=name))

    def _stop(self) -> None:
        self._m_player.stop()
        self.m_status.setText(tr("viewer.stopped"))
