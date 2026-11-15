"""The viewer's sound view: a Wwise bank's sounds, events and music tracks in tables (names from the bank's info file,
codec, embedded or streamed, the media file; each event's sounds, walked through its containers, with the switch
and state choices of its containers), a clip (``.wem``,
``.wav``, ``.mp3``) described and played.

``SoundView(parent, cache)`` is the entry: ``show_bank(path, data, files)``, ``show_clip(path, data)``. Playing goes
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
from ..suite.clipplayer import ClipPlayer
from ..suite.widgets import add_row, read_only_table

COLUMNS = {
    N_("Sounds"): (N_("sound"), N_("name"), N_("codec"), N_("media"), N_("file")),
    N_("Events"): (N_("event"), N_("name"), N_("sounds"), N_("read"), N_("choices")),
    N_("Music"): (N_("track"), N_("segment"), N_("seconds"), N_("media"), N_("file")),
}


class SoundView(QWidget):
    """A bank's tables or one clip, with Play and Stop."""

    def __init__(self, parent=None, cache: Path | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.files = None
        self.bank = ""
        self.listing: banks.BankListing | None = None
        self.info = banks.BankInfo()
        self.clips: list[tuple[str, bytes]] = []  # per sound row: (name, the clip's bytes or b"" to read the file)
        self.event_rows: list[int] = []
        self.choices: dict[int, int] = {}  # the group -> value picked for the selected event
        self.music_rows: list[tuple[str, bytes]] = []
        self.summary = QLabel(wordWrap=True)
        self.tables = {name: read_only_table(columns) for name, columns in COLUMNS.items()}
        self.table = self.tables["Sounds"]
        self.tabs = QTabWidget()
        for name, table in self.tables.items():
            self.tabs.tabBar().setTabData(self.tabs.addTab(table, tr(name)), name)
            table.cellDoubleClicked.connect(lambda _r, _c: self._play_selected())
        self.play = QPushButton(tr("Play"))
        self.stop = QPushButton(tr("Stop"))
        self.status = QLabel()
        bar = QHBoxLayout()
        for w in (self.play, self.stop):
            bar.addWidget(w)
        bar.addWidget(self.status, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addLayout(bar)
        lay.addWidget(self.tabs, 1)
        self.choice_form = QFormLayout()
        self.choice_note = QLabel(wordWrap=True)
        lay.addWidget(self.choice_note)
        lay.addLayout(self.choice_form)
        self.tables["Events"].currentCellChanged.connect(lambda row, *_rest: self._show_choices(row, reset=True))
        self.play.clicked.connect(self._play_selected)
        self.stop.clicked.connect(self._stop)
        self._player = ClipPlayer(self)
        self._clip: tuple[str, bytes] | None = None

    def show_bank(self, path: str, data: bytes, files) -> None:
        """A bank: its header and objects above, its sounds, events and music tracks in the tables."""
        self.files, self._clip, self.bank = files, None, path
        self.listing = listing = banks.bank_listing(data)
        self.info = info = banks.read_info(path, files.exists, lambda p: files.read(p).decode("utf-8", "replace"))
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(listing.objects.items(), key=lambda kv: -kv[1]))
        self.summary.setText(
            tr(
                "{name}: bank version {version}, id {id}; {sounds} sounds, {media} embedded media, "
                "{events} events ({named} named). Objects: {objects}",
                name=info.name or path,
                version=listing.version,
                id=listing.bank_id,
                sounds=len(listing.sounds),
                media=len(listing.media),
                events=len(listing.events),
                named=len(info.events),
                objects=kinds,
            )
        )
        self._fill_sounds(path, listing, info, files)
        self._fill_events(listing, info)
        self._fill_music(path, listing, info, files)
        self.status.setText(tr("double-click a row, or select it and press Play"))

    def _clip_of(self, path: str, media: int, stream: str, info, files) -> tuple[str, tuple[str, bytes]]:
        """(where the media is, (name, bytes or b"" to read the file)) of one media id."""
        listing = self.listing
        streamed = banks.media_file(path, media, info, files.exists) if stream != "embedded" else ""
        if streamed:  # the whole clip; a bank holds only its first part (the prefetch)
            return streamed, (streamed, b"")
        if media in listing.media:
            where = tr("in the bank (its start only)") if stream != "embedded" else tr("in the bank")
            return where, (f"{media}.wem", listing.embedded(media))
        return tr("not found"), ("", b"")

    def _fill_sounds(self, path: str, listing, info, files) -> None:
        table = self.tables["Sounds"]
        table.setRowCount(0)
        self.clips = []
        for s in listing.sounds:
            where, clip = self._clip_of(path, s.media, s.stream, info, files)
            add_row(table, [str(s.id), info.media.get(s.media, ("", ""))[0], s.codec, f"{s.media} ({s.stream})", where])
            self.clips.append(clip)
        table.resizeColumnsToContents()

    def _fill_events(self, listing, info) -> None:
        table = self.tables["Events"]
        table.setRowCount(0)
        self.event_rows = []
        for event in sorted(listing.events, key=lambda e: info.events.get(e, "~")):
            add_row(table, [str(event), info.events.get(event, ""), "", "", ""])
            self.event_rows.append(event)
        table.resizeColumnsToContents()

    def _fill_music(self, path: str, listing, info, files) -> None:
        table = self.tables["Music"]
        table.setRowCount(0)
        self.music_rows = []
        for track in banks.music_tracks(listing):
            for media, stream in track.sources or ((0, ""),):
                where, clip = (
                    self._clip_of(path, media, stream, info, files) if media else (tr("no source read"), ("", b""))
                )
                add_row(table, [str(track.id), str(track.segment or ""), f"{track.seconds:.1f}" if track.seconds else "",
                                  f"{media} ({stream})" if media else "", where])  # fmt: skip
                self.music_rows.append(clip)
        table.resizeColumnsToContents()

    def event_clips(self, event: int) -> tuple[list[tuple[str, bytes]], bool]:
        """The clips an event can play (its sounds and music tracks, walked) and whether the walk read every step."""
        walk = banks.walk_event(self.listing, event, self.choices)
        found, exact = walk.sounds, walk.exact
        clips = []
        for item in found:
            media, stream = (item.media, item.stream) if isinstance(item, banks.BankSound) else (item, "streamed")
            _where, clip = self._clip_of(self.bank, media, stream, self.info, self.files)
            if clip[0]:
                clips.append(clip)
        return clips, exact

    def _show_choices(self, row: int, reset: bool = False) -> None:
        """The selected event's switch and state groups as drop-downs (``every value`` plays every branch), and the
        switches and states the event sets itself."""
        if reset:
            self.choices = {}
        while self.choice_form.rowCount():  # taken, not removed: this runs inside a drop-down's own signal
            taken = self.choice_form.takeRow(0)
            for item in (taken.labelItem, taken.fieldItem):
                if item is not None and item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
        if not 0 <= row < len(self.event_rows) or self.listing is None:
            self.choice_note.setText("")
            return
        walk = banks.walk_event(self.listing, self.event_rows[row], self.choices)
        for group, found in walk.groups.items():
            box = QComboBox()
            box.addItem(tr("(every value)"), None)
            for value in found.values:
                name = self.info.group_label(group, value)
                box.addItem(tr("{value} (start)", value=name) if value == found.default else name, value)
            box.setCurrentIndex(max(0, box.findData(self.choices.get(group))))
            box.currentIndexChanged.connect(lambda _i, g=group, b=box: self._pick(row, g, b.currentData()))
            group_name = self.info.group_label(group)
            label = tr("state {name}", name=group_name) if found.state_group else tr("switch {name}", name=group_name)
            self.choice_form.addRow(label, box)
        sets = [f"{self.info.group_label(a.group)} = {self.info.group_label(a.group, a.value)}" for a in walk.sets]
        self.choice_note.setText(tr("the event sets: {sets}", sets=", ".join(sets)) if sets else "")

    def _pick(self, row: int, group: int, value: int | None) -> None:
        if value is None:
            self.choices.pop(group, None)
        else:
            self.choices[group] = value
        clips, exact = self.event_clips(self.event_rows[row])
        table = self.tables["Events"]
        table.item(row, 2).setText(str(len(clips)))
        self._show_choices(row)

    def show_clip(self, path: str, data: bytes) -> None:
        """One clip: its facts in the summary, Play plays it."""
        for table in self.tables.values():
            table.setRowCount(0)
        self.clips, self.event_rows, self.music_rows, self._clip = [], [], [], (path, data)
        try:
            info = banks.wave_info(data) if not path.lower().endswith((".mp3", ".ogg")) else {"format": path[-3:]}
            facts = ", ".join(f"{k} {round(v, 2) if isinstance(v, float) else v}" for k, v in info.items())
        except banks.Unplayable as exc:
            facts = str(exc)
        self.summary.setText(f"{path}: {facts}")
        self.status.setText("")

    def _selected_clip(self) -> tuple[str, bytes] | None:
        tab = self.tabs.tabBar().tabData(self.tabs.currentIndex())
        row = self.tables[tab].currentRow()
        if tab == "Sounds" and 0 <= row < len(self.clips):
            return self.clips[row]
        if tab == "Music" and 0 <= row < len(self.music_rows):
            return self.music_rows[row]
        if tab == "Events" and 0 <= row < len(self.event_rows):
            clips, exact = self.event_clips(self.event_rows[row])
            table = self.tables["Events"]
            table.item(row, 2).setText(str(len(clips)))
            table.item(row, 3).setText(tr("exactly") if exact else tr("partly guessed"))
            table.item(row, 4).setText(
                ", ".join(
                    f"{self.info.group_label(g)} = {self.info.group_label(g, v)}" for g, v in self.choices.items()
                )
            )
            return random.choice(clips) if clips else ("", b"")  # a random container picks one, so does the view
        return self._clip

    def _play_selected(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            return
        name, data = clip
        if not data and self.files is not None and name and self.files.exists(name):
            data = self.files.read(name)
        if data:
            self.play_clip(name, data)
        else:
            self.status.setText(tr("nothing to play: the media file is not in the game's files"))

    def play_clip(self, name: str, data: bytes) -> None:
        """Decode a clip into the cache and play it; the status says why when it cannot."""
        why = self._player.play(name, data, self.cache / "sounds" if self.cache is not None else None)
        self.status.setText(why or tr("playing {name}", name=name))

    def _stop(self) -> None:
        self._player.stop()
        self.status.setText(tr("stopped"))
