"""The sound editor's page for the older games (Qt): the sounds of a ``sounddatabase.xml`` in a table, the values of the
picked one in a form, the lists that draw it with their weights, Play, Replace the clip and Add a sound.

``OldSoundPanel(say, cache)`` is the widget; ``open_database(tree, path)`` fills it, ``doc`` is the open
``olddoc.OldSoundDoc`` (the window saves its ``files()``). docs/sound-editor.md, "The older games".
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
from ..suite.background import Background
from ..suite.clipplayer import ClipPlayer
from ..suite.widgets import ROLE, add_row, read_only_table
from . import olddoc
from .timelinepanel import TimelinePanel

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
    return next(n for n in (f"{stem}{suffix}", *(f"{stem}{k}{suffix}" for k in range(2, 10_000))) if n not in taken)


class OldSoundPanel(QWidget):
    def __init__(
        self, say: Callable[[str], None], cache: Path | None = None, changed: Callable[[], None] | None = None
    ):
        super().__init__()
        self.say, self.cache, self.changed = say, cache, changed or (lambda: None)
        self.may_leave: Callable[[], bool] = lambda: True  # asked before another database replaces unkept edits
        self.tree = None
        self.doc: olddoc.OldSoundDoc | None = None
        self.player = ClipPlayer(self)
        self.background = Background(self)
        self.busy = False  # a clip is being prepared off the window's thread
        self.databases = QComboBox()
        self.filter = QLineEdit(placeholderText=tr("sound.filter_id_file_type"))
        self.table = read_only_table(COLUMNS)
        self.file = QLineEdit()
        self.type = QComboBox()
        for number, name in sounddb.TYPES.items():
            self.type.addItem(f"{number}: {tr(name)}", number)
        self.boxes = {field: QSpinBox() for field, *_rest in NUMBERS}
        for field, _label, lo, hi in NUMBERS:
            self.boxes[field].setRange(lo, hi)
        self.lists = QLabel(wordWrap=True)
        self.members = QTableWidget(0, 3)
        self.members.setHorizontalHeaderLabels([tr("viewer.list"), tr("viewer.event"), tr("common.weight")])
        self.facts = QLabel(wordWrap=True)
        self.timelines = TimelinePanel(say, self.changed)
        self._build()

    def _build(self) -> None:
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.addRow(tr("common.file_lower"), self.file)
        form.addRow(tr("viewer.kind"), self.type)
        for field, label, _lo, _hi in NUMBERS:
            form.addRow(tr(label), self.boxes[field])
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
        lay.addWidget(self.facts)
        lay.addWidget(form_box)
        lay.addLayout(buttons)
        lay.addWidget(self.lists)
        lay.addWidget(self.members, 1)
        top = QHBoxLayout()
        top.addWidget(self.databases, 1)
        top.addWidget(self.filter, 1)
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.addLayout(top)
        llay.addWidget(self.table, 1)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([700, 500])
        tabs = QTabWidget()
        tabs.addTab(split, tr("viewer.sounds"))
        tabs.addTab(self.timelines, tr("sound.timelines"))
        QVBoxLayout(self).addWidget(tabs)
        self.filter.textChanged.connect(lambda _t: self.fill())
        self.table.currentCellChanged.connect(lambda row, *_r: self.show_sound(self._id_at(row)))
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.play())
        self.databases.activated.connect(lambda _i: self._pick_database())

    # opening
    def open_game(self, tree) -> None:
        """List the game's databases (the last one listed is opened: 1404's add-on one is the larger)."""
        self.tree = tree
        paths = olddoc.database_paths(tree.paths)
        self.databases.clear()
        for path in paths:
            self.databases.addItem(path, path)
        if paths:
            self.databases.setCurrentIndex(len(paths) - 1)
            self.open_database(tree, paths[-1])
        else:
            self._close_database()
            self.say(tr("sound.game_no_sound_database"))

    def _close_database(self) -> None:
        """No database is open: the table and the timelines of the one before go."""
        self.doc = None
        self.timelines.open_document(None)
        self.fill()

    def _pick_database(self) -> None:
        """The database picked in the list, once ``may_leave`` let the open one go (else the list shows it again)."""
        path = self.databases.currentData()
        if self.doc is not None and path != self.doc.path and not self.may_leave():
            self.databases.setCurrentIndex(self.databases.findData(self.doc.path))
            return
        if self.doc is None or path != self.doc.path:
            self.open_database(self.tree, path)

    def open_database(self, tree, path: str) -> None:
        try:
            self.doc = olddoc.OldSoundDoc(tree, path)
            self.doc.db  # noqa: B018  (read now, so a file that does not read is said here)
        except (ValueError, OSError) as exc:
            self._close_database()
            self.say(tr("common.path_not_read_error", path=path, error=exc))
            return
        self.timelines.open_document(self.doc)
        self.fill()
        self.say(tr("sound.path_count_sounds_lists", path=path, count=len(self.doc.db.sounds),
                    lists=len(self.doc.db.lists)))  # fmt: skip

    def fill(self, keep: int | None = None) -> None:
        self.timelines.refresh()
        self.table.setRowCount(0)
        if self.doc is None:
            return
        words = self.filter.text().lower().split()
        row_of = None
        for sid, sound in self.doc.db.sounds.items():
            kind = sounddb.TYPES.get(sound.number("SoundType"), "")
            cells = [str(sid), sound.filename, kind, str(sound.number("MasterVolume")),
                     f"{sound.number('MinDistance')}-{sound.number('MaxDistance')}", str(sound.number("SoundGroupID"))]  # fmt: skip
            if words and not all(w in " ".join(cells).lower() for w in words):
                continue
            row = add_row(self.table, cells, sid)
            row_of = row if sid == keep else row_of
        self.table.resizeColumnsToContents()
        if row_of is not None:
            self.table.selectRow(row_of)

    def _id_at(self, row: int) -> int | None:
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(ROLE) if item is not None else None

    @property
    def selected(self) -> int | None:
        return self._id_at(self.table.currentRow())

    def show_sound(self, sid: int | None) -> None:
        sound = self.doc.db.sounds.get(sid) if self.doc and sid is not None else None
        if sound is None:
            return
        self.file.setText(sound.filename)
        self.type.setCurrentIndex(max(0, self.type.findData(sound.number("SoundType"))))
        for field, box in self.boxes.items():
            box.setValue(sound.number(field))
        self.facts.setText(tr("sound.sound_id_path", id=sid, path=sound.filename)
                           + ("" if self.doc.has_clip(sid) else " · " + tr("sound.no_such_file")))  # fmt: skip
        rows = [(lid, ev, w) for lid, entry in self.doc.db.lists.items() for ev, ms in entry.events.items()
                for s, w in ms if s == sid]  # fmt: skip
        self.lists.setText(tr("sound.lists_draw_sound", n=len(rows)))
        self.members.setRowCount(len(rows))
        for r, (lid, ev, weight) in enumerate(rows):
            for c, text in enumerate((str(lid), "" if ev == sounddb.NO_EVENT else str(ev), str(weight))):
                item = QTableWidgetItem(text)
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsEditable if c == 2 else item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                self.members.setItem(r, c, item)

    # edits
    def _do(self, call: Callable[[], object], done: str) -> bool:
        keep = self.selected
        try:
            call()
        except olddoc.OldEditError as exc:
            self.say(tr("sound.cannot_error", error=exc))
            return False
        self.fill(keep)
        self.say(done + " " + tr("sound.ctrl_plus_z_undoes"))
        self.changed()
        return True

    def apply(self) -> None:
        sid = self.selected
        if self.doc is None or sid is None:
            return
        sound = self.doc.db.sounds[sid]
        shown = {f: max(lo, min(hi, sound.number(f))) for f, _label, lo, hi in NUMBERS}  # what the box could hold
        values: dict[str, object] = {f: b.value() for f, b in self.boxes.items() if b.value() != shown[f]}
        if self.type.currentData() != sound.number("SoundType"):
            values["SoundType"] = self.type.currentData()
        if self.file.text().strip().replace("\\", "/") != sound.filename:
            values["Filename"] = self.file.text().strip()
        if not values:
            self.say(tr("sound.no_value_changed"))
            return
        self._do(lambda: self.doc.set_fields(sid, **values), tr("sound.changed_values_sound", id=sid))

    def apply_weights(self) -> None:
        sid = self.selected
        if self.doc is None or sid is None:
            return

        try:  # every weight read first: a cell that is no number leaves the lists as they are
            cells = [[self.members.item(r, c).text() for c in range(3)] for r in range(self.members.rowCount())]
            rows = [(int(lid), int(ev) if ev else sounddb.NO_EVENT, int(weight)) for lid, ev, weight in cells]
        except ValueError:
            self.say(tr("sound.weight_whole_number"))
            return

        def make() -> None:
            for lid, ev, new in rows:
                old = next((w for s, w in self.doc.db.lists[lid].events[ev] if s == sid), None)
                if old is not None and new != old:
                    self.doc.set_weight(lid, sid, new, ev)

        self._do(make, tr("sound.changed_weights_sound", id=sid))

    def add_to_list(self) -> None:
        """The picked sound joins a list the game draws from (an added sound plays only once a list draws it)."""
        sid = self.selected
        if self.doc is None or sid is None:
            return
        choices = {
            (f"{lid}" if ev == sounddb.NO_EVENT else f"{lid} / {ev}"): (lid, ev)
            for lid, entry in self.doc.db.lists.items()
            for ev in entry.events
        }
        text, ok = QInputDialog.getItem(self, tr("sound.add_to_list"), tr("sound.list_event_draws_sound"),
                                        list(choices), 0, False)  # fmt: skip
        if not ok:
            return
        weight, ok = QInputDialog.getInt(self, tr("sound.add_to_list"), tr("sound.weight_list"), 50, 0, 1000)
        if ok:
            lid, ev = choices[text]
            self._do(
                lambda: self.doc.add_member(lid, sid, weight, ev),
                tr("sound.sound_joined_list", id=sid, list=text),
            )
            self.show_sound(sid)

    def play(self) -> None:
        if self.doc is None or self.selected is None:
            return
        path, data = self.doc.clip(self.selected)
        why = self.player.play(path, data, self.cache) if data else tr("sound.nothing_play_file_not_game")
        self.say(why or tr("viewer.playing_name", name=path))

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
            self.say(str(exc))
            return None

    def _convert(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """``work`` (preparing a clip: ffmpeg may run) off the window's thread, then ``done(result)`` on it."""
        if self.busy:
            self.say(tr("sound.clip_still_converted"))
            return

        def finish(result: object) -> None:
            self.busy = False
            if isinstance(result, olddoc.OldEditError):
                self.say(tr("sound.cannot_error", error=result))
            elif isinstance(result, Exception):
                self.say(str(result))
            else:
                done(result)

        self.busy = True
        self.say(tr("sound.converting_clip"))
        self.background.run(work, finish)

    def replace_clip(self) -> None:
        sid, picked = self.selected, self._pick() if self.doc is not None and self.selected is not None else None
        if picked is None or sid is None:
            return
        clip, suffix = picked
        doc = self.doc
        try:
            target = doc.target_of(sid, suffix)  # the one read of the document, here: the thread only converts bytes
        except olddoc.OldEditError as exc:
            return self.say(tr("sound.cannot_error", error=exc))

        def done(data) -> None:
            if doc is self.doc:
                self._do(lambda: doc.put_clip(sid, target, data), tr("sound.sound_plays_new_clip", id=sid))
                self.show_sound(sid)

        self._convert(lambda: olddoc.convert_clip(target, clip, suffix), done)

    def replace_speech(self) -> None:
        """Pick a spoken line of the game, then the clip that says it now."""
        if self.doc is None:
            return
        known = set(self.tree.paths)
        lines = sorted(p for p in known if p.endswith(".sp") and f"{p[:-3]}.mp3" in known)
        line, ok = QInputDialog.getItem(self, tr("sound.spoken_line"), tr("sound.line_replace_mp3"),
                                        [f"{p[:-3]}.mp3" for p in lines], 0, False)  # fmt: skip
        picked = self._pick() if ok and line else None
        if picked is None:
            return
        clip, suffix = picked
        doc = self.doc

        def done(files) -> None:
            if doc is self.doc:
                self._do(lambda: doc.put_files("replace_speech", files), tr("sound.says_new_clip", path=line))

        self._convert(lambda: doc.prepare_speech(line, clip, suffix), done)

    def add_sound(self) -> None:
        picked = self._pick() if self.doc is not None else None
        sid = self.selected
        if picked is None or self.doc is None:
            return
        clip, suffix = picked
        doc = self.doc
        beside = doc.db.sounds[sid].filename if sid is not None else self.file.text()
        taken = {s.filename for s in doc.db.sounds.values()} | set(doc.clips)
        name = new_sound_path(beside, ".mp3" if suffix == ".mp3" else ".wav", taken)

        def convert() -> bytes:
            if suffix in (".wav", ".mp3"):
                return clip
            try:
                return export.to_wav(clip, suffix)
            except (export.ToolMissing, ValueError) as exc:
                raise olddoc.OldEditError(str(exc)) from exc

        def done(data) -> None:
            if doc is self.doc:
                self._do(lambda: doc.add_sound(name, data, sid), tr("sound.added_sound_plays", path=name))

        self._convert(convert, done)

    def stop(self) -> None:
        self.player.stop()
