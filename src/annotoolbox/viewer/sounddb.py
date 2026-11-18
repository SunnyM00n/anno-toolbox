"""The viewer's page for the older games' sound files: a ``sounddatabase.xml`` (its sounds, the lists the game draws
from, the ambients), a model's ``.snd`` timeline and a spoken line's ``.sp`` envelope.

``SoundDbView(parent, cache)`` is the entry: ``show_database(path, data, files)``, ``show_timeline(path, data, files)``,
``show_speech(path, data, files)``; ``files`` reads the game's files by path (the clips to play, the database a
timeline's ids belong to). docs/viewer.md, "Sounds of the older games"; the formats are in docs/sounds.md.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..formats import sndtimeline, sounddb, spenvelope
from ..infra.i18n import N_, tr
from ..suite.background import Background
from ..suite.clipplayer import ClipPlayer
from ..suite.widgets import ROLE, add_row, read_only_table

TABLES = {
    "Sounds": (
        N_("ui.id"),
        N_("common.file_lower"),
        N_("viewer.kind"),
        N_("common.volume"),
        N_("viewer.at_once"),
        N_("viewer.zoom_range"),
        N_("common.group_lower"),
    ),
    "Lists": (
        N_("viewer.list"),
        N_("viewer.event"),
        N_("viewer.sound"),
        N_("common.weight"),
        N_("common.file_lower"),
    ),
    "Ambients": (
        N_("viewer.ambient"),
        N_("common.name_lower"),
        N_("viewer.channel_lower"),
        N_("viewer.pause_ms"),
        N_("viewer.sounds_lower"),
    ),
    "Timeline": (
        N_("viewer.sequence"),
        N_("viewer.length_ms"),
        N_("viewer.at_ms"),
        N_("viewer.sound"),
        N_("common.file_lower"),
    ),
}
TABLE_TITLES = {
    "Sounds": N_("viewer.sounds"),
    "Lists": N_("viewer.lists"),
    "Ambients": N_("viewer.ambients"),
    "Timeline": N_("viewer.timeline"),
}


class EnvelopeBars(QWidget):
    """A ``.sp`` envelope as bars, one per level."""

    def __init__(self) -> None:
        super().__init__()
        self.levels = b""
        self.setMinimumHeight(120)

    def show_levels(self, levels: bytes) -> None:
        self.levels = levels
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.levels:
            return
        width = self.width() / len(self.levels)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3b82c4"))
        for k, level in enumerate(self.levels):
            top = self.height() * (1 - level / 255)
            painter.drawRect(QRectF(k * width, top, max(width - 0.5, 0.5), self.height() - top))


class SoundDbView(QWidget):
    def __init__(self, parent=None, cache: Path | None = None) -> None:
        super().__init__(parent)
        self.cache, self.files = cache, None
        self._speech = ""  # the clip a speech envelope belongs to
        self.db = sounddb.SoundDb()
        self.background = Background(self)
        self._shown = 0  # raised by every page change: a database that comes late for an earlier file is dropped
        self.player = ClipPlayer(self)
        self.summary = QLabel(wordWrap=True)
        self.status = QLabel()
        self.bars = EnvelopeBars()
        self.tables = {name: read_only_table(columns) for name, columns in TABLES.items()}
        self.tabs = QTabWidget()
        for name, table in self.tables.items():
            self.tabs.tabBar().setTabData(self.tabs.addTab(table, tr(TABLE_TITLES[name])), name)
            table.cellDoubleClicked.connect(lambda _r, _c: self._play())
        play, stop = QPushButton(tr("common.play")), QPushButton(tr("viewer.stop"))
        play.clicked.connect(self._play)
        stop.clicked.connect(self._stop)
        bar = QHBoxLayout()
        for w in (play, stop):
            bar.addWidget(w)
        bar.addWidget(self.status, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addLayout(bar)
        lay.addWidget(self.bars)
        lay.addWidget(self.tabs, 1)

    def _row(self, name: str, cells: list[str], sound: int | None = None) -> None:
        add_row(self.tables[name], cells, sound)

    def _reset(self, files, show: str) -> None:
        self.files = files
        self._shown += 1
        for table in self.tables.values():
            table.setRowCount(0)
        self.bars.show_levels(b"")
        for k in range(self.tabs.count()):
            self.tabs.setTabVisible(k, True)
        self.bars.setVisible(show == "speech")
        self.tabs.setVisible(show != "speech")
        self.status.setText("")

    def _file_of(self, sound: int) -> str:
        found = self.db.sounds.get(sound)
        return found.filename if found else tr("viewer.not_database")

    # what is shown
    def show_database(self, path: str, data: bytes, files) -> None:
        """The three tables of a database: sounds, lists (every member), ambients (every channel)."""
        self._reset(files, "database")
        try:
            self.db = db = sounddb.read(data)
        except ValueError as exc:
            self.summary.setText(f"{path}: {exc}")
            return
        self.summary.setText(
            tr("viewer.sounds_lists_ambients_groups", path=path,
               sounds=len(db.sounds), lists=len(db.lists), ambients=len(db.ambients), groups=len(db.groups))
        )  # fmt: skip
        for sid, s in db.sounds.items():
            self._row("Sounds", [str(sid), s.filename, tr(sounddb.TYPES.get(s.number("SoundType"), "")),
                                     str(s.number("MasterVolume")), str(s.number("MaxCount")),
                                     f"{s.number('MinDistance')}-{s.number('MaxDistance')}", str(s.number("SoundGroupID"))], sid)  # fmt: skip
        for lid, entry in db.lists.items():
            for event, members in entry.events.items():
                for sid, weight in members:
                    self._row("Lists", [str(lid), "" if event == sounddb.NO_EVENT else str(event), str(sid),
                                            str(weight), self._file_of(sid)], sid)  # fmt: skip
        for aid, amb in db.ambients.items():
            for k, channel in enumerate(amb.channels):
                self._row("Ambients", [str(aid), amb.name, str(k), f"{channel.min_pause}-{channel.max_pause}",
                                           ", ".join(f"{s} ({w})" for s, w in channel.members)])  # fmt: skip
        for table in self.tables.values():
            table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(0)

    def show_timeline(self, path: str, data: bytes, files, wait: bool = False) -> None:
        """A ``.snd``: per sequence its length and the sounds on it; the ids are read in the game's database, off the
        window's thread (the rows are filled when it is in; ``wait``: here, a test)."""
        self._reset(files, "timeline")
        try:
            timeline = sndtimeline.parse(data.decode("utf-8", "replace"))
        except ValueError as exc:
            self.summary.setText(f"{path}: {exc}")
            return
        shown = self._shown

        def fill(db) -> None:
            if shown == self._shown:  # the page still shows this timeline
                self.db = db if isinstance(db, sounddb.SoundDb) else sounddb.SoundDb()
                self._timeline_rows(timeline)

        self.background.run(lambda: self._database_of(files), fill, wait)
        placed = sum(len(rows) for rows in timeline.placements.values())
        self.summary.setText(
            tr("viewer.sequences_sounds_placed", path=path,
               sequences=len(timeline.durations), placed=placed)
        )  # fmt: skip
        for name in ("Sounds", "Lists", "Ambients"):
            self.tabs.setTabVisible(self.tabs.indexOf(self.tables[name]), False)
        self.tabs.setCurrentWidget(self.tables["Timeline"])

    def _timeline_rows(self, timeline: sndtimeline.Timeline) -> None:
        for seq in sorted(set(timeline.durations) | set(timeline.placements)):
            length = str(timeline.durations.get(seq, ""))
            for place in timeline.placements.get(seq) or [None]:
                at = "" if place is None else (tr("viewer.whole_sequence") if place.at < 0 else str(place.at))
                sound = "" if place is None else str(place.sound)
                self._row("Timeline", [str(seq), length, at, sound, self._file_of(place.sound) if place else ""],
                          place.sound if place else None)  # fmt: skip
        self.tables["Timeline"].resizeColumnsToContents()

    def show_speech(self, path: str, data: bytes, files) -> None:
        """A ``.sp``: the loudness of a spoken line, 20 steps a second; Play plays the line (the MP3 beside it)."""
        self._reset(files, "speech")
        try:
            envelope = spenvelope.read(data)
        except ValueError as exc:
            self.summary.setText(f"{path}: {exc}")
            return
        self.bars.show_levels(envelope.levels)
        self.summary.setText(
            tr(
                "viewer.levels_every_ms_seconds",
                path=path,
                count=len(envelope.levels),
                ms=envelope.interval_ms,
                seconds=envelope.seconds,
            )  # fmt: skip
        )
        self._speech = path[: -len(".sp")] + ".mp3"

    def _database_of(self, files) -> sounddb.SoundDb:
        """The game's database for a timeline's ids: the add-on's where the game has one, else the base game's."""
        for path in sorted((p for p in files.paths if p.endswith("/sounddatabase.xml")), reverse=True):
            try:
                return sounddb.read(files.read(path))
            except (ValueError, OSError):
                continue
        return sounddb.SoundDb()

    # playing
    def _selected_file(self) -> str:
        if self.bars.isVisible():
            return self._speech
        name = self.tabs.tabBar().tabData(self.tabs.currentIndex())
        table = self.tables.get(name)
        item = table.item(table.currentRow(), 0) if table is not None and table.currentRow() >= 0 else None
        sound = item.data(ROLE) if item is not None else None
        return self._file_of(sound) if sound is not None else ""

    def _play(self) -> None:
        path = self._selected_file()
        if not path or self.files is None or not self.files.exists(path):
            self.status.setText(tr("viewer.nothing_play_pick_row_whose"))
            return
        why = self.player.play(path, self.files.read(path), self.cache / "sounds" if self.cache else None)
        self.status.setText(why or tr("viewer.playing_name", name=path))

    def _stop(self) -> None:
        self.player.stop()
        self.status.setText(tr("viewer.stopped"))
