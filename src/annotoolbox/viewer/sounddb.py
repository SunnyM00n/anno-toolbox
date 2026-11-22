"""The viewer's page for the older games' sound files: a ``sounddatabase.xml`` (its sounds, the lists the game draws
from, the ambients), a model's ``.snd`` timeline and a spoken line's ``.sp`` envelope.

``CSoundDbView(parent, cache)`` is the entry: ``show_database(path, data, files)``, ``show_timeline(path, data, files)``,
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
from ..suite.background import CBackground
from ..suite.clipplayer import CClipPlayer
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


class CEnvelopeBars(QWidget):
    """A ``.sp`` envelope as bars, one per level."""

    def __init__(self) -> None:
        super().__init__()
        self.m_levels = b""
        self.setMinimumHeight(120)

    def show_levels(self, levels: bytes) -> None:
        self.m_levels = levels
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.m_levels:
            return
        width = self.width() / len(self.m_levels)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3b82c4"))
        for k, level in enumerate(self.m_levels):
            bar_top = self.height() * (1 - level / 255)
            painter.drawRect(QRectF(k * width, bar_top, max(width - 0.5, 0.5), self.height() - bar_top))


class CSoundDbView(QWidget):
    def __init__(self, parent=None, cache: Path | None = None) -> None:
        super().__init__(parent)
        self.m_cache, self.m_files = cache, None
        self._m_speech = ""  # the clip a speech envelope belongs to
        self.m_db = sounddb.SSoundDb()
        self.m_background = CBackground(self)
        self._m_shown = 0  # raised by every page change: a database that comes late for an earlier file is dropped
        self.m_player = CClipPlayer(self)
        self.m_summary = QLabel(wordWrap=True)
        self.m_status = QLabel()
        self.m_bars = CEnvelopeBars()
        self.m_tables = {name: read_only_table(columns) for name, columns in TABLES.items()}
        self.m_tabs = QTabWidget()
        for name, table in self.m_tables.items():
            self.m_tabs.tabBar().setTabData(self.m_tabs.addTab(table, tr(TABLE_TITLES[name])), name)
            table.cellDoubleClicked.connect(lambda _r, _c: self._play())
        play, stop = QPushButton(tr("common.play")), QPushButton(tr("viewer.stop"))
        play.clicked.connect(self._play)
        stop.clicked.connect(self._stop)
        button_row = QHBoxLayout()
        for w in (play, stop):
            button_row.addWidget(w)
        button_row.addWidget(self.m_status, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.m_summary)
        layout.addLayout(button_row)
        layout.addWidget(self.m_bars)
        layout.addWidget(self.m_tabs, 1)

    def _row(self, name: str, cells: list[str], sound: int | None = None) -> None:
        add_row(self.m_tables[name], cells, sound)

    def _reset(self, files, show: str) -> None:
        self.m_files = files
        self._m_shown += 1
        for table in self.m_tables.values():
            table.setRowCount(0)
        self.m_bars.show_levels(b"")
        for k in range(self.m_tabs.count()):
            self.m_tabs.setTabVisible(k, True)
        self.m_bars.setVisible(show == "speech")
        self.m_tabs.setVisible(show != "speech")
        self.m_status.setText("")

    def _file_of(self, sound: int) -> str:
        found = self.m_db.sounds.get(sound)
        return found.filename if found else tr("viewer.not_database")

    # what is shown
    def show_database(self, path: str, data: bytes, files) -> None:
        """The three tables of a database: sounds, lists (every member), ambients (every channel)."""
        self._reset(files, "database")
        try:
            self.m_db = database = sounddb.read(data)
        except ValueError as exc:
            self.m_summary.setText(f"{path}: {exc}")
            return
        self.m_summary.setText(
            tr("viewer.sounds_lists_ambients_groups", path=path,
               sounds=len(database.sounds), lists=len(database.lists), ambients=len(database.ambients), groups=len(database.groups))
        )  # fmt: skip
        for sound_id, sound in database.sounds.items():
            self._row("Sounds", [str(sound_id), sound.filename, tr(sounddb.TYPES.get(sound.number("SoundType"), "")),
                                     str(sound.number("MasterVolume")), str(sound.number("MaxCount")),
                                     f"{sound.number('MinDistance')}-{sound.number('MaxDistance')}", str(sound.number("SoundGroupID"))], sound_id)  # fmt: skip
        for list_id, entry in database.lists.items():
            for event, members in entry.events.items():
                for sound_id, weight in members:
                    self._row("Lists", [str(list_id), "" if event == sounddb.NO_EVENT else str(event), str(sound_id),
                                            str(weight), self._file_of(sound_id)], sound_id)  # fmt: skip
        for ambient_id, ambient in database.ambients.items():
            for k, channel in enumerate(ambient.channels):
                self._row("Ambients", [str(ambient_id), ambient.name, str(k), f"{channel.min_pause}-{channel.max_pause}",
                                           ", ".join(f"{sound} ({w})" for sound, w in channel.members)])  # fmt: skip
        for table in self.m_tables.values():
            table.resizeColumnsToContents()
        self.m_tabs.setCurrentIndex(0)

    def show_timeline(self, path: str, data: bytes, files, is_waiting: bool = False) -> None:
        """A ``.snd``: per sequence its length and the sounds on it; the ids are read in the game's database, off the
        window's thread (the rows are filled when it is in; ``is_waiting``: here, a test)."""
        self._reset(files, "timeline")
        try:
            timeline = sndtimeline.parse(data.decode("utf-8", "replace"))
        except ValueError as exc:
            self.m_summary.setText(f"{path}: {exc}")
            return
        shown = self._m_shown

        def fill(database) -> None:
            if shown == self._m_shown:  # the page still shows this timeline
                self.m_db = database if isinstance(database, sounddb.SSoundDb) else sounddb.SSoundDb()
                self._timeline_rows(timeline)

        self.m_background.run(lambda: self._database_of(files), fill, is_waiting)
        placed = sum(len(rows) for rows in timeline.placements.values())
        self.m_summary.setText(
            tr("viewer.sequences_sounds_placed", path=path,
               sequences=len(timeline.durations), placed=placed)
        )  # fmt: skip
        for name in ("Sounds", "Lists", "Ambients"):
            self.m_tabs.setTabVisible(self.m_tabs.indexOf(self.m_tables[name]), False)
        self.m_tabs.setCurrentWidget(self.m_tables["Timeline"])

    def _timeline_rows(self, timeline: sndtimeline.STimeline) -> None:
        for seq in sorted(set(timeline.durations) | set(timeline.placements)):
            length = str(timeline.durations.get(seq, ""))
            for place in timeline.placements.get(seq) or [None]:
                start = "" if place is None else (tr("viewer.whole_sequence") if place.at_ms < 0 else str(place.at_ms))
                sound = "" if place is None else str(place.sound)
                self._row("Timeline", [str(seq), length, start, sound, self._file_of(place.sound) if place else ""],
                          place.sound if place else None)  # fmt: skip
        self.m_tables["Timeline"].resizeColumnsToContents()

    def show_speech(self, path: str, data: bytes, files) -> None:
        """A ``.sp``: the loudness of a spoken line, 20 steps a second; Play plays the line (the MP3 beside it)."""
        self._reset(files, "speech")
        try:
            envelope = spenvelope.read(data)
        except ValueError as exc:
            self.m_summary.setText(f"{path}: {exc}")
            return
        self.m_bars.show_levels(envelope.levels)
        self.m_summary.setText(
            tr(
                "viewer.levels_every_ms_seconds",
                path=path,
                count=len(envelope.levels),
                ms=envelope.interval_ms,
                seconds=envelope.seconds,
            )  # fmt: skip
        )
        self._m_speech = path[: -len(".sp")] + ".mp3"

    def _database_of(self, files) -> sounddb.SSoundDb:
        """The game's database for a timeline's ids: the add-on's where the game has one, else the base game's."""
        for path in sorted(
            (file_path for file_path in files.m_paths if file_path.endswith("/sounddatabase.xml")), reverse=True
        ):
            try:
                return sounddb.read(files.read(path))
            except (ValueError, OSError):
                continue
        return sounddb.SSoundDb()

    # playing
    def _selected_file(self) -> str:
        if self.m_bars.isVisible():
            return self._m_speech
        name = self.m_tabs.tabBar().tabData(self.m_tabs.currentIndex())
        table = self.m_tables.get(name)
        item = table.item(table.currentRow(), 0) if table is not None and table.currentRow() >= 0 else None
        sound = item.data(ROLE) if item is not None else None
        return self._file_of(sound) if sound is not None else ""

    def _play(self) -> None:
        path = self._selected_file()
        if not path or self.m_files is None or not self.m_files.exists(path):
            self.m_status.setText(tr("viewer.nothing_play_pick_row_whose"))
            return
        why = self.m_player.play(path, self.m_files.read(path), self.m_cache / "sounds" if self.m_cache else None)
        self.m_status.setText(why or tr("viewer.playing_name", name=path))

    def _stop(self) -> None:
        self.m_player.stop()
        self.m_status.setText(tr("viewer.stopped"))
