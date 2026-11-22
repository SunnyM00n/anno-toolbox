"""The sound editor's page for the older games (Qt): the sounds of a ``sounddatabase.xml`` in a table, the values of the
picked one in a form, the lists that draw it with their weights, Play, Replace the clip and Add a sound.

``COldSoundPanel(say, cache)`` is the widget; ``open_database(tree, path)`` fills it, ``doc`` is the open
``olddoc.COldSoundDoc`` (the window saves its ``files()``). docs/sound-editor.md, "The older games".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..exporting import sounds as export
from ..formats import sounddb
from ..infra.i18n import N_, tr
from ..suite.background import CBackground
from ..suite.clipplayer import CClipPlayer
from ..suite.widgets import ROLE, add_row, read_only_table
from . import olddoc
from .timelinepanel import CTimelinePanel

COLUMNS = (
    N_("ui.id"),
    N_("common.file_lower"),
    N_("sound.type"),
    N_("common.volume"),
    N_("sound.distance"),
    N_("common.group_lower"),
)
NUMBERS = (  # (field, label, lowest, highest): the boxes of the form
    ("MasterVolume", N_("sound.volume_0_100"), 0, 100), ("MaxCount", N_("sound.most_once"), 0, 1000),
    ("MinDistance", N_("sound.full_volume_up_zoom"), 0, 100), ("MaxDistance", N_("sound.silent_zoom"), 0, 100),
    ("Prio", N_("sound.priority"), 0, 100), ("RandomPitch", N_("sound.random_pitch_percent"), -100, 100),
    ("SoundFlags", N_("ui.flags"), 0, 255), ("XZDistanceFactor", N_("sound.horizontal_reach_percent"), 0, 10000),
    ("SoundGroupID", N_("sound.sound_group_1_none"), -1, 100000),
)  # fmt: skip


def new_sound_path(beside: str, suffix: str, taken: set[str]) -> str:
    """A free ``new_sound`` path next to the file ``beside`` (the folder of the sound that was picked)."""
    folder = beside.rsplit("/", 1)[0] + "/" if "/" in beside else ""
    stem = f"{folder}new_sound"
    return next(
        candidate
        for candidate in (f"{stem}{suffix}", *(f"{stem}{k}{suffix}" for k in range(2, 10_000)))
        if candidate not in taken
    )


class COldSoundPanel(QWidget):
    def __init__(
        self, say: Callable[[str], None], cache: Path | None = None, changed: Callable[[], None] | None = None
    ):
        super().__init__()
        self.m_say, self.m_cache, self.m_changed = say, cache, changed or (lambda: None)
        self.m_may_leave: Callable[[], bool] = lambda: True  # asked before another database replaces unkept edits
        self.m_tree = None
        self.m_doc: olddoc.COldSoundDoc | None = None
        self.m_player = CClipPlayer(self)
        self.m_background = CBackground(self)
        self.m_busy = False  # a clip is being prepared off the window's thread
        self.m_databases = QComboBox()
        self.m_filter = QLineEdit(placeholderText=tr("sound.filter_id_file_type"))
        self.m_table = read_only_table(COLUMNS)
        self.m_file = QLineEdit()
        self.m_type = QComboBox()
        for number, name in sounddb.TYPES.items():
            self.m_type.addItem(f"{number}: {tr(name)}", number)
        self.m_boxes = {field: QSpinBox() for field, *_rest in NUMBERS}
        for field, _label, lo, hi in NUMBERS:
            self.m_boxes[field].setRange(lo, hi)
        self.m_lists = QLabel(wordWrap=True)
        self.m_members = QTableWidget(0, 3)
        self.m_members.setHorizontalHeaderLabels([tr("viewer.list"), tr("viewer.event"), tr("common.weight")])
        self.m_facts = QLabel(wordWrap=True)
        self.m_timelines = CTimelinePanel(say, self.m_changed)
        self._build()

    def _build(self) -> None:
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.addRow(tr("common.file_lower"), self.m_file)
        form.addRow(tr("viewer.kind"), self.m_type)
        for field, label, _lo, _hi in NUMBERS:
            form.addRow(tr(label), self.m_boxes[field])
        buttons = QHBoxLayout()
        for text, call in (
            (N_("common.apply"), self.apply), (N_("common.play"), self.play), (N_("sound.replace_clip"), self.replace_clip),
            (N_("sound.add_sound_dots"), self.add_sound), (N_("sound.replace_spoken_line"), self.replace_speech),
            (N_("sound.add_to_list"), self.add_to_list), (N_("sound.apply_weights"), self.apply_weights),
        ):  # fmt: skip
            button = QPushButton(tr(text))
            button.clicked.connect(call)
            buttons.addWidget(button)
        right = QWidget()
        lay = QVBoxLayout(right)
        lay.addWidget(self.m_facts)
        lay.addWidget(form_box)
        lay.addLayout(buttons)
        lay.addWidget(self.m_lists)
        lay.addWidget(self.m_members, 1)
        top = QHBoxLayout()
        top.addWidget(self.m_databases, 1)
        top.addWidget(self.m_filter, 1)
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.addLayout(top)
        llay.addWidget(self.m_table, 1)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([700, 500])
        tabs = QTabWidget()
        tabs.addTab(split, tr("viewer.sounds"))
        tabs.addTab(self.m_timelines, tr("sound.timelines"))
        QVBoxLayout(self).addWidget(tabs)
        self.m_filter.textChanged.connect(lambda _t: self.fill())
        self.m_table.currentCellChanged.connect(lambda row, *_r: self.show_sound(self._id_at(row)))
        self.m_table.cellDoubleClicked.connect(lambda _r, _c: self.play())
        self.m_databases.activated.connect(lambda _i: self._pick_database())

    # opening
    def open_game(self, tree) -> None:
        """List the game's databases (the last one listed is opened: 1404's add-on one is the larger)."""
        self.m_tree = tree
        paths = olddoc.database_paths(tree.m_paths)
        self.m_databases.clear()
        for path in paths:
            self.m_databases.addItem(path, path)
        if paths:
            self.m_databases.setCurrentIndex(len(paths) - 1)
            self.open_database(tree, paths[-1])
        else:
            self._close_database()
            self.m_say(tr("sound.game_no_sound_database"))

    def _close_database(self) -> None:
        """No database is open: the table and the timelines of the one before go."""
        self.m_doc = None
        self.m_timelines.open_document(None)
        self.fill()

    def _pick_database(self) -> None:
        """The database picked in the list, once ``may_leave`` let the open one go (else the list shows it again)."""
        path = self.m_databases.currentData()
        if self.m_doc is not None and path != self.m_doc.m_path and not self.m_may_leave():
            self.m_databases.setCurrentIndex(self.m_databases.findData(self.m_doc.m_path))
            return
        if self.m_doc is None or path != self.m_doc.m_path:
            self.open_database(self.m_tree, path)

    def open_database(self, tree, path: str) -> None:
        try:
            self.m_doc = olddoc.COldSoundDoc(tree, path)
            self.m_doc.db  # noqa: B018  (read now, so a file that does not read is said here)
        except (ValueError, OSError) as exc:
            self._close_database()
            self.m_say(tr("common.path_not_read_error", path=path, error=exc))
            return
        self.m_timelines.open_document(self.m_doc)
        self.fill()
        self.m_say(tr("sound.path_count_sounds_lists", path=path, count=len(self.m_doc.db.sounds),
                    lists=len(self.m_doc.db.lists)))  # fmt: skip

    def fill(self, keep: int | None = None) -> None:
        self.m_timelines.refresh()
        self.m_table.setRowCount(0)
        if self.m_doc is None:
            return
        words = self.m_filter.text().lower().split()
        row_of = None
        for sid, sound in self.m_doc.db.sounds.items():
            kind = sounddb.TYPES.get(sound.number("SoundType"), "")
            cells = [str(sid), sound.filename, kind, str(sound.number("MasterVolume")),
                     f"{sound.number('MinDistance')}-{sound.number('MaxDistance')}", str(sound.number("SoundGroupID"))]  # fmt: skip
            if words and not all(word in " ".join(cells).lower() for word in words):
                continue
            row = add_row(self.m_table, cells, sid)
            row_of = row if sid == keep else row_of
        self.m_table.resizeColumnsToContents()
        if row_of is not None:
            self.m_table.selectRow(row_of)

    def _id_at(self, row: int) -> int | None:
        item = self.m_table.item(row, 0) if row >= 0 else None
        return item.data(ROLE) if item is not None else None

    @property
    def selected(self) -> int | None:
        return self._id_at(self.m_table.currentRow())

    def show_sound(self, sid: int | None) -> None:
        sound = self.m_doc.db.sounds.get(sid) if self.m_doc and sid is not None else None
        if sound is None:  # no row (a filter, another database): the facts of the sound before go
            self.m_facts.clear()
            self.m_lists.clear()
            self.m_members.setRowCount(0)
            return
        self.m_file.setText(sound.filename)
        self.m_type.setCurrentIndex(max(0, self.m_type.findData(sound.number("SoundType"))))
        for field, box in self.m_boxes.items():
            box.setValue(sound.number(field))
        self.m_facts.setText(tr("sound.sound_id_path", id=sid, path=sound.filename)
                           + ("" if self.m_doc.has_clip(sid) else " · " + tr("sound.no_such_file")))  # fmt: skip
        rows = [(lid, event, entry_weight) for lid, entry in self.m_doc.db.lists.items() for event, ms in entry.events.items()
                for sound_id, entry_weight in ms if sound_id == sid]  # fmt: skip
        self.m_lists.setText(tr("sound.lists_draw_sound", n=len(rows)))
        self.m_members.setRowCount(len(rows))
        for row_index, (lid, event, weight) in enumerate(rows):
            for column, text in enumerate((str(lid), "" if event == sounddb.NO_EVENT else str(event), str(weight))):
                item = QTableWidgetItem(text)
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsEditable
                    if column == 2
                    else item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                self.m_members.setItem(row_index, column, item)

    # edits
    def _do(self, call: Callable[[], object], done: str) -> bool:
        keep = self.selected
        try:
            call()
        except olddoc.COldEditError as exc:
            self.m_say(tr("sound.cannot_error", error=exc))
            return False
        self.fill(keep)
        self.m_say(done + " " + tr("sound.ctrl_plus_z_undoes"))
        self.m_changed()
        return True

    def apply(self) -> None:
        sid = self.selected
        if self.m_doc is None or sid is None:
            return
        sound = self.m_doc.db.sounds[sid]
        shown = {
            field: max(lo, min(hi, sound.number(field))) for field, _label, lo, hi in NUMBERS
        }  # what the box could hold
        values: dict[str, object] = {
            field: box.value() for field, box in self.m_boxes.items() if box.value() != shown[field]
        }
        if self.m_type.currentData() != sound.number("SoundType"):
            values["SoundType"] = self.m_type.currentData()
        if self.m_file.text().strip().replace("\\", "/") != sound.filename:
            values["Filename"] = self.m_file.text().strip()
        if not values:
            self.m_say(tr("sound.no_value_changed"))
            return
        self._do(lambda: self.m_doc.set_fields(sid, **values), tr("sound.changed_values_sound", id=sid))

    def apply_weights(self) -> None:
        sid = self.selected
        if self.m_doc is None or sid is None:
            return

        try:  # every weight read first: a cell that is no number leaves the lists as they are
            cells = [
                [self.m_members.item(row_index, column).text() for column in range(3)]
                for row_index in range(self.m_members.rowCount())
            ]
            rows = [(int(lid), int(event) if event else sounddb.NO_EVENT, int(weight)) for lid, event, weight in cells]
        except ValueError:
            self.m_say(tr("sound.weight_whole_number"))
            return

        def make() -> None:
            for lid, event, new in rows:
                old = next(
                    (
                        entry_weight
                        for sound_id, entry_weight in self.m_doc.db.lists[lid].events[event]
                        if sound_id == sid
                    ),
                    None,
                )
                if old is not None and new != old:
                    self.m_doc.set_weight(lid, sid, new, event)

        self._do(make, tr("sound.changed_weights_sound", id=sid))

    def add_to_list(self) -> None:
        """The picked sound joins a list the game draws from (an added sound plays only once a list draws it)."""
        sid = self.selected
        if self.m_doc is None or sid is None:
            return
        choices = {
            (f"{lid}" if event == sounddb.NO_EVENT else f"{lid} / {event}"): (lid, event)
            for lid, entry in self.m_doc.db.lists.items()
            for event in entry.events
        }
        text, ok = QInputDialog.getItem(self, tr("sound.add_to_list"), tr("sound.list_event_draws_sound"),
                                        list(choices), 0, False)  # fmt: skip
        if not ok:
            return
        weight, ok = QInputDialog.getInt(self, tr("sound.add_to_list"), tr("sound.weight_list"), 50, 0, 1000)
        if ok:
            lid, event = choices[text]
            self._do(
                lambda: self.m_doc.add_member(lid, sid, weight, event),
                tr("sound.sound_joined_list", id=sid, list=text),
            )
            self.show_sound(sid)

    def play(self) -> None:
        if self.m_doc is None or self.selected is None:
            return
        try:
            path, data = self.m_doc.clip(self.selected)
        except (OSError, ValueError) as exc:  # the archive does not give the file up
            self.m_say(str(exc))
            return
        why = self.m_player.play(path, data, self.m_cache) if data else tr("sound.nothing_play_file_not_game")
        self.m_say(why or tr("viewer.playing_name", name=path))

    def _pick(self) -> tuple[bytes, str] | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("sound.wav_mp3_ogg_file"),
            "",
            "Audio (*.wav *.ogg *.mp3 *.flac)",  # i18n: ok (a file filter)
        )
        if not path:
            return None
        try:
            return Path(path).read_bytes(), Path(path).suffix.lower()
        except OSError as exc:
            self.m_say(str(exc))
            return None

    def _convert(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """``work`` (preparing a clip: ffmpeg may run) off the window's thread, then ``done(result)`` on it."""
        if self.m_busy:
            self.m_say(tr("sound.clip_still_converted"))
            return

        def finish(result: object) -> None:
            self.m_busy = False
            if isinstance(result, olddoc.COldEditError):
                self.m_say(tr("sound.cannot_error", error=result))
            elif isinstance(result, Exception):
                self.m_say(str(result))
            else:
                done(result)

        self.m_busy = True
        self.m_say(tr("sound.converting_clip"))
        self.m_background.run(work, finish)

    def replace_clip(self) -> None:
        sid, picked = self.selected, self._pick() if self.m_doc is not None and self.selected is not None else None
        if picked is None or sid is None:
            return
        clip, suffix = picked
        doc = self.m_doc
        try:
            target = doc.target_of(sid, suffix)  # the one read of the document, here: the thread only converts bytes
        except olddoc.COldEditError as exc:
            return self.m_say(tr("sound.cannot_error", error=exc))

        def done(data) -> None:
            if doc is self.m_doc:
                self._do(lambda: doc.put_clip(sid, target, data), tr("sound.sound_plays_new_clip", id=sid))
                self.show_sound(sid)

        self._convert(lambda: olddoc.convert_clip(target, clip, suffix), done)

    def replace_speech(self) -> None:
        """Pick a spoken line of the game, then the clip that says it now."""
        if self.m_doc is None:
            return
        known = set(self.m_tree.m_paths)
        lines = sorted(path for path in known if path.endswith(".sp") and f"{path[:-3]}.mp3" in known)
        line, ok = QInputDialog.getItem(self, tr("sound.spoken_line"), tr("sound.line_replace_mp3"),
                                        [f"{path[:-3]}.mp3" for path in lines], 0, False)  # fmt: skip
        picked = self._pick() if ok and line else None
        if picked is None:
            return
        clip, suffix = picked
        doc = self.m_doc

        def done(files) -> None:
            if doc is self.m_doc:
                self._do(lambda: doc.put_files("replace_speech", files), tr("sound.says_new_clip", path=line))

        self._convert(lambda: doc.prepare_speech(line, clip, suffix), done)

    def add_sound(self) -> None:
        picked = self._pick() if self.m_doc is not None else None
        sid = self.selected
        if picked is None or self.m_doc is None:
            return
        clip, suffix = picked
        doc = self.m_doc
        beside = doc.db.sounds[sid].filename if sid is not None else self.m_file.text()
        taken = {sound.filename for sound in doc.db.sounds.values()} | set(doc.clips)
        name = new_sound_path(beside, ".mp3" if suffix == ".mp3" else ".wav", taken)

        def convert() -> bytes:
            if suffix in (".wav", ".mp3"):
                return clip
            try:
                return export.to_wav(clip, suffix)
            except (export.CToolMissing, ValueError) as exc:
                raise olddoc.COldEditError(str(exc)) from exc

        def done(data) -> None:
            if doc is self.m_doc:
                self._do(lambda: doc.add_sound(name, data, sid), tr("sound.added_sound_plays", path=name))

        self._convert(convert, done)

    def stop(self) -> None:
        self.m_player.stop()
