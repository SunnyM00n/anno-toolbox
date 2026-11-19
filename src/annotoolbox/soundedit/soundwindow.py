"""The sound editor's window (Qt): a game's banks on the left, the open bank's events, containers and music in a tree,
the selected object's facts and edit forms on the right; edits go on the bank's undo stack and are kept in the project.

``SoundWindow(settings, settings_path)`` is the suite's Sound app; ``open_game`` and ``open_bank`` are the entry points.
docs/sound-editor.md is the user's guide.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..assetedit import assetgame
from ..exporting import banks
from ..formats import bnkgraph, bnkmusic
from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import gameopen, gamesync, widgets
from ..suite import project as suite_project
from ..suite.background import Background
from ..suite.clipplayer import ClipPlayer
from . import (
    audioedits,
    clipcodec,
    containers,
    formrows,
    groupedits,
    musicedits,
    nodebase,
    outline,
    outputs,
    projectpart,
    registry,
    waveform,
)
from .bankdoc import BankDoc
from .curvepanel import CurvePanel
from .musicpanel import MusicPanel
from .oldpanel import OldSoundPanel
from .registry import EditError
from .wavewidget import WaveformView

APP = "sound"
TITLE = N_("sound.sound_editor")
OLD_GAMES = ("anno2070", "anno1404", "anno1701")  # no Wwise banks: a sound database and loose clips
GAMES = {key: gametree.GAMES[key] for key in ("anno117", "anno1800", "anno2205", *OLD_GAMES)}
ROLE = Qt.ItemDataRole.UserRole
WAVE_BINS = 1000  # columns of peaks kept per clip; the view picks from them
MEDIA_ERRORS = (OSError, ValueError, KeyError, IndexError, struct.error)  # a clip that does not read


class SoundWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.sync = False  # a test's: the reads run on the caller's thread
        self.asked = {"bank": 0, "wave": 0, "play": 0}  # the latest request of each kind: an older answer is dropped
        self.player = ClipPlayer(self)
        self.key, self.tree = "", None
        self.doc: BankDoc | None = None
        self.selected = 0
        self.shown = (0.0, 0.0, 0.0, 0.0)  # the settings boxes as last loaded
        self.kept: dict[str, set[str]] = {}  # per bank: the project files its last save made
        self.banks = QListWidget()
        self.filter = QLineEdit(placeholderText=tr("sound.filter_banks"))
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.facts = QPlainTextEdit(readOnly=True)
        self.wave = WaveformView()
        self.music = MusicPanel(self._edit, self.statusBar().showMessage)
        self.curves = CurvePanel(self._edit, self.statusBar().showMessage)
        self.waves: dict[str, waveform.Shape] = {}  # the shapes drawn lately, by the clip's hash
        self.volume, self.pitch = QDoubleSpinBox(), QDoubleSpinBox()
        self.rand_lo, self.rand_hi = QDoubleSpinBox(), QDoubleSpinBox()
        for box, lo, hi in (
            (self.volume, -96, 24),
            (self.pitch, -2400, 2400),
            (self.rand_lo, -2400, 2400),
            (self.rand_hi, -2400, 2400),
        ):
            box.setRange(lo, hi)
        self.playlist = QLineEdit(placeholderText=tr("sound.child_weight"))
        self.codec = QComboBox()
        self.codec.addItems(list(clipcodec.CODECS))
        self.status_label = QLabel()
        self.old = OldSoundPanel(self.statusBar().showMessage, self._cache())
        self.old.may_leave = self.may_leave
        self.pages = QStackedWidget()
        self.started: set[str] = set()  # the project's files of the older game's database when it was opened
        self._build()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("sound.file_gt_open_game_pick"))

    # layout
    def _build(self) -> None:
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.addWidget(self.filter)
        lay.addWidget(self.banks)
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.addRow(tr("sound.volume_db"), self.volume)
        form.addRow(tr("sound.pitch_cents"), self.pitch)
        form.addRow(tr("sound.random_pitch"), self.rand_lo)
        form.addRow(tr("sound.random_pitch_to"), self.rand_hi)
        form.addRow(tr("sound.playlist"), self.playlist)
        form.addRow(tr("sound.encode_clip"), self.codec)
        buttons = QWidget()
        blay = QVBoxLayout(buttons)
        made = formrows.add_buttons(
            blay,
            (
                (N_("sound.apply_settings"), self._apply),
                (N_("sound.apply_playlist"), self._apply_playlist),
                (N_("sound.play_clip"), self._play),
                (N_("sound.replace_audio_dots"), self._replace),
                (N_("sound.add_sound_here"), self._add_sound),
                (N_("sound.add_event_plays"), self._add_event),
                (N_("sound.new_state_switch_group"), self._new_group),
            ),
        )
        self.play_button = made[2]
        self.play_button.setShortcut(QKeySequence("Ctrl+Space"))
        self.play_button.setToolTip(tr("sound.tip.play_clip"))
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self.wave)
        rlay.addWidget(self.facts, 1)
        rlay.addWidget(self.music)
        rlay.addWidget(self.curves)
        rlay.addWidget(form_box)
        rlay.addWidget(buttons)
        rlay.addWidget(self.status_label)
        split = QSplitter()
        for w in (left, self.outline, right):
            split.addWidget(w)
        split.setSizes([300, 450, 450])
        self.pages.addWidget(split)
        self.pages.addWidget(self.old)
        self.setCentralWidget(self.pages)
        menu = self.menuBar().addMenu(tr("common.menu.file"))
        games = menu.addMenu(tr("common.menu.open_game"))
        for key, name in GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        menu.addAction(tr("sound.save_to_project"), self.save, QKeySequence.StandardKey.Save)
        widgets.edit_menu(self, self.undo, self.redo)
        widgets.find_key(self, self.filter)
        self.filter.textChanged.connect(self._fill_banks)
        self.banks.itemActivated.connect(lambda item: self.open_bank(item.data(ROLE)))
        self.outline.currentItemChanged.connect(lambda item, _old: self._select(item.data(0, ROLE) if item else 0))

    # games and banks
    def project(self) -> suite_project.Project:
        return suite_project.current(self.settings)

    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a bank to open."""
        if not gamesync.may_switch(self, key, self.may_leave):  # asks; the suite's other apps follow
            return

        def prepare(tree):
            mine = projectpart.EDITS.folder(self.project(), key)
            return tree.over(mine, "project") if mine.is_dir() else tree

        def opened(tree) -> None:
            if isinstance(tree, Exception):
                self.statusBar().showMessage(tr("common.game_not_open_error", game=GAMES.get(key, key), error=tree))
                return
            self.key, self.tree = key, tree
            self.doc = None  # asked about in ``may_leave`` before the game was opened
            self._fill_outline()
            self.settings.apps.setdefault(APP, {})["game"] = key
            self.pages.setCurrentIndex(1 if key in OLD_GAMES else 0)
            if key in OLD_GAMES:
                self.started = set(projectpart.EDITS.saved_files(self.project(), key))
                self.old.open_game(tree)
                return
            self._fill_banks()
            self.statusBar().showMessage(
                tr("sound.name_count_banks", name=GAMES.get(key, key), count=self.banks.count())
            )
            if then:
                self.open_bank(then)

        gameopen.open_installed(self, self.settings, key, self.background, then=opened, prepare=prepare, wait=wait)

    def _fill_banks(self) -> None:
        self.banks.clear()
        if self.tree is None:
            return
        words = self.filter.text().lower().split()
        for path in self.tree.paths:
            if path.endswith(".bnk") and all(w in path for w in words):
                short = path.split("/windows/")[-1]
                self.banks.addItem(short)
                self.banks.item(self.banks.count() - 1).setData(ROLE, path)

    def _latest(self, kind: str, done):
        """``done`` as a callback that runs only while no later request of ``kind`` was made."""
        self.asked[kind] += 1
        mine = self.asked[kind]
        return lambda result: done(result) if self.asked[kind] == mine else None

    def open_bank(self, path: str) -> None:
        """Read a bank off the window's thread and show it; the bank asked for last is the one shown."""
        if self.tree is None or not self.may_leave():
            return
        tree = self.tree

        def work() -> BankDoc:
            info = banks.read_info(path, tree.exists, lambda p: tree.read(p).decode("utf-8", "replace"))
            return BankDoc(tree.read(path), path, info)

        self.statusBar().showMessage(tr("sound.reading_bank", path=path))
        self.background.run(work, self._latest("bank", lambda doc: self._bank_read(path, tree, doc)), self.sync)

    def _bank_read(self, path: str, tree, doc: BankDoc | Exception) -> None:
        if tree is not self.tree:
            return  # another game was opened while it was read
        if isinstance(doc, Exception):
            if not isinstance(doc, (ValueError, OSError)):
                raise doc
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=doc))
            return
        self.doc = doc
        self._fill_outline()
        self.statusBar().showMessage(
            tr(
                "sound.path_version_count_objects",
                path=path,
                version=self.doc.version,
                count=len(self.doc.bank.hirc),
            )
        )

    def _fill_outline(self) -> None:
        self.outline.clear()
        if self.doc is None:
            return
        for group, rows in outline.outline(self.doc).items():
            top = QTreeWidgetItem([tr("common.title_count", title=tr(group), count=len(rows))])
            self.outline.addTopLevelItem(top)
            for row in rows:
                self._add_row(top, row)

    def _add_row(self, parent: QTreeWidgetItem, row: outline.Row) -> None:
        item = QTreeWidgetItem(parent, [row.title])
        item.setData(0, ROLE, row.id)
        for child in row.children:
            self._add_row(item, child)

    # the selected object
    def _select(self, obj_id: int) -> None:
        self.selected = obj_id
        if self.doc is None or not obj_id:
            return
        lines = outline.describe(self.doc, obj_id)
        node = self.doc.graph.get(obj_id)
        if node is not None and node.exact and node.type in (bnkgraph.RANSEQ, bnkgraph.MIXER, bnkgraph.SWITCH):
            try:
                lay = containers.read_layout(self.doc.obj(obj_id), node, self.doc.version)
                self.playlist.setText(", ".join(f"{c}:{w}" for c, w in lay.playlist))
                if lay.table:
                    table = "; ".join(tr("sound.value_plays_name", value=v, name=n) for v, n in lay.table.items())
                    lines.append(tr("sound.values_table", table=table))
            except containers.LayoutError as exc:
                lines.append(str(exc))
        self.facts.setPlainText("\n".join(lines))
        self._load_props()
        self._load_wave()
        self.music.load(self.doc, obj_id)
        self.curves.load(self.doc, obj_id)

    def _load_wave(self) -> None:
        """Draw the clip of the picked Sound or music track (``waveform``), or say why there is none. The clip
        is read and decoded off the window's thread; the selection made last is the one drawn."""
        picked, kept = self.selected, dict(self.waves)

        def work() -> tuple[str, waveform.Shape] | str:
            try:
                data = self._media_of(picked)
            except MEDIA_ERRORS:
                data = None
            if not data:
                return tr("common.no_clip_picked")
            key = hashlib.sha1(data).hexdigest()
            try:
                return key, kept.get(key) or waveform.shape(data, WAVE_BINS)
            except clipcodec.ClipError as exc:
                return str(exc)

        self.wave.show_message(tr("sound.reading_clip"))
        self.background.run(work, self._latest("wave", lambda found: self._wave_read(picked, found)), self.sync)

    def _wave_read(self, picked: int, found) -> None:
        if isinstance(found, Exception):
            raise found
        if isinstance(found, str):
            self.wave.show_message(found)
            return
        key, shape = found
        self.waves = dict(list(self.waves.items())[-7:]) | {key: shape}
        self.wave.show_shape(shape, musicedits.track_marks(self.doc, picked) if self.doc is not None else [])

    def _load_props(self) -> None:
        for box in (self.volume, self.pitch, self.rand_lo, self.rand_hi):
            box.setValue(0)
        self.shown = self._values()
        try:
            block = nodebase.read_props(self.doc.obj(self.selected), self.doc.version)
        except (nodebase.PropError, EditError):
            return
        version = self.doc.version
        pitch = nodebase.prop_id(version, "pitch")
        self.volume.setValue(block.float(nodebase.prop_id(version, "volume")))
        self.pitch.setValue(block.float(pitch))
        span = block.range(pitch) or (0.0, 0.0)
        self.rand_lo.setValue(span[0])
        self.rand_hi.setValue(span[1])
        self.shown = self._values()  # what the boxes made of the file's numbers (clamped, rounded)

    def _values(self) -> tuple[float, float, float, float]:
        return tuple(b.value() for b in (self.volume, self.pitch, self.rand_lo, self.rand_hi))  # type: ignore[return-value]

    def _edit(self, name: str, **args) -> bool:
        if self.doc is None:
            return False
        try:
            note = self.doc.do(name, **args)
        except EditError as exc:
            self.statusBar().showMessage(tr("sound.cannot_error", error=exc))
            return False
        keep = self.selected
        self._fill_outline()
        self._select(keep)
        done = tr("sound.done_ctrl_plus_z_undoes", name=registry.label(name))
        self.statusBar().showMessage(f"{done}; {note}" if isinstance(note, str) and note else done)
        return True

    def _apply(self) -> None:
        """Write the boxes the user changed; a value the form clamped or rounded stays as the file has it."""
        version = self.doc.version if self.doc else 172
        pitch = nodebase.prop_id(version, "pitch")
        volume, pitch_value, lo, hi = self._values()
        was_volume, was_pitch, was_lo, was_hi = self.shown
        props, ranges = {}, {}
        if volume != was_volume:
            props[nodebase.prop_id(version, "volume")] = volume or None
        if pitch_value != was_pitch:
            props[pitch] = pitch_value or None
        if (lo, hi) != (was_lo, was_hi):
            ranges[pitch] = (lo, hi) if (lo, hi) != (0.0, 0.0) else None
        if not props and not ranges:
            self.statusBar().showMessage(tr("sound.no_setting_changed"))
            return
        self._edit("set_props", id=self.selected, props=props, ranges=ranges)

    def _apply_playlist(self) -> None:
        try:
            entries = [
                tuple(int(x) for x in part.split(":")) for part in self.playlist.text().split(",") if part.strip()
            ]
        except ValueError:
            self.statusBar().showMessage(tr("sound.write_playlist_child_weight"))
            return
        self._edit("set_playlist", container=self.selected, entries=entries)

    # audio
    def _clip(self, path: str) -> bytes | None:
        """A file made into a ``.wem`` in the codec picked (the status line says why not)."""
        try:
            clip = clipcodec.read_clip(Path(path).read_bytes(), path)
            return clipcodec.encode(clip, self.codec.currentText(), self.doc.version)
        except (OSError, clipcodec.ClipError) as exc:
            self.statusBar().showMessage(str(exc))
            return None

    def _pick_clip(self) -> bytes | None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("sound.wav_ogg_file"), "", "Audio (*.wav *.ogg *.mp3 *.flac)"
        )  # i18n: ok (a file filter)
        return self._clip(path) if path else None

    def _replace(self) -> None:
        if self.doc is None or not (wem := self._pick_clip()):
            return
        node = self.doc.graph.get(self.selected)
        try:
            if node is not None and node.source is not None:
                self._edit(
                    "replace_audio", sound=self.selected, wem=wem, media=audioedits.plan_media(self.doc, self.selected)
                )
            else:
                self._edit(
                    "replace_track_audio", track=self.selected, index=0, wem=wem, media=self._track_media(),
                    markers=self.music.fit.currentText(),
                )  # fmt: skip
        except EditError as exc:
            self.statusBar().showMessage(tr("sound.cannot_error", error=exc))

    def _track_media(self) -> int:
        return bnkmusic.parse_track(self.doc.obj(self.selected)).sources[0].source_id & 0xFFFFFFFF

    def _add_sound(self) -> None:
        if self.doc is None:
            return
        node = self.doc.graph.get(self.selected)
        parent = self.selected if node is not None and node.type != bnkgraph.SOUND else (node.parent if node else 0)
        template = next(
            (s for s in self.doc.graph.values() if s.source is not None and (s.parent == parent or not parent)), None
        )
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("sound.name_new_sound"))
        if not ok or not name.strip() or template is None or not (wem := self._pick_clip()):
            return
        value = None
        switch = self.doc.graph[parent].switch if parent in self.doc.graph else None
        if switch is not None:
            value = next(iter(switch.branches), None)
        self._edit("add_sound", sound=self.doc.new_id(name), template=template.id, parent=parent, wem=wem,
                   media=self.doc.new_id(name + ".wem"), value=value)  # fmt: skip

    def _add_event(self) -> None:
        if self.doc is None or not self.selected:
            return
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("sound.name_new_event_game_posts"))
        if ok and name.strip():
            self._edit(
                "add_event", event=self.doc.new_id(name), action=self.doc.new_id(name + ".action"), target=self.selected
            )

    def _new_group(self) -> None:
        """Ask for a group (kind, name, values and, for a switch group, its game parameter and points) and add it."""
        if self.doc is None:
            return
        kinds = [tr("sound.state_group"), tr("sound.switch_group")]
        kind, ok = QInputDialog.getItem(self, tr(TITLE), tr("sound.kind_group"), kinds, 0, False)
        if not ok:
            return
        name = self._ask(tr("sound.name_group_id_hash"))
        if not name:
            return
        text = self._ask(tr("sound.values_separated_commas"))
        if text is None:
            return
        values = formrows.entries(text)
        state = kind == kinds[0]
        parameter, points = "", []
        if not state:
            parameter = self._ask(tr("sound.game_parameter_drives_empty_none"))
            if parameter is None:
                return
            if parameter:
                rows = self._ask(tr("sound.points_value_switch"))
                if rows is None:
                    return
                try:
                    points = [(float(x), s.strip(), 9) for x, s in (r.split(":") for r in formrows.entries(rows))]
                except ValueError:
                    self.statusBar().showMessage(tr("sound.write_points_value_switch_separated"))
                    return
        plan = groupedits.plan_group(self.doc, "state" if state else "switch", name, values, parameter, points)
        if isinstance(plan, str):
            self.statusBar().showMessage(plan)
            self._fill_outline()
        else:
            self._edit(plan[0], **plan[1])

    def _ask(self, label: str) -> str | None:
        """One line asked of the person, stripped; None when they cancelled."""
        text, ok = QInputDialog.getText(self, tr(TITLE), label)
        return text.strip() if ok else None

    def _play(self) -> None:
        """Play the picked clip: read and decoded off the window's thread, the one asked for last is played."""
        picked, cache = self.selected, self._cache()

        def work() -> Path | str:
            try:
                data = self._media_of(picked)
                if not data:
                    return tr("sound.nothing_play_here_pick_sound")
                return self.player.prepare("x.wem", data, cache)
            except MEDIA_ERRORS as exc:  # the clip did not read, or its decoded copy could not be written
                return tr("common.cannot_play_error", error=exc)

        self.statusBar().showMessage(tr("sound.reading_clip"))
        self.background.run(work, self._latest("play", self._play_read), self.sync)

    def _play_read(self, found: Path | str | Exception) -> None:
        if isinstance(found, Exception):
            raise found
        if isinstance(found, str):
            self.statusBar().showMessage(found)
            return
        self.statusBar().clearMessage()
        self.player.start(found)

    def _media_of(self, obj_id: int) -> bytes | None:
        """The clip of a sound or the first source of a music track: an edit's, the bank's, the loose file's."""
        doc, tree = self.doc, self.tree  # read once: a worker thread calls this while the window goes on
        if doc is None or not obj_id:
            return None
        node = doc.graph.get(obj_id)
        media = node.source.media if node is not None and node.source is not None else None
        if media is None and doc.obj(obj_id).type == 11:
            media = bnkmusic.parse_track(doc.obj(obj_id)).sources[0].source_id & 0xFFFFFFFF
        if media is None:
            return None
        if media in doc.loose:
            return doc.loose[media].data
        if (data := doc.media(media)) is not None and not (node and node.source and node.source.stream):
            return data
        if tree is None:
            return doc.media(media)
        found = banks.media_file(doc.path, media, doc.info, tree.exists)
        return tree.read(found) if found else doc.media(media)

    # the stack and the project
    def _cache(self) -> Path:
        """Where a played clip is written, once per content: the workspace cache's ``sounds``, which the other apps
        that play a clip share (the temp folder kept every clip for good)."""
        return assetgame.cache_dir(self.settings.workspace) / "sounds"

    def _old_open(self) -> bool:
        return self.pages.currentIndex() == 1 and self.old.doc is not None

    def _refill_old(self) -> None:
        self.old.fill(self.old.selected)
        self.old.show_sound(self.old.selected)

    def undo(self) -> None:
        if self._old_open():
            if self.old.doc.undo():
                self._refill_old()
            return
        if self.doc is not None and self.doc.undo():
            self._fill_outline()
            self._select(self.selected)

    def redo(self) -> None:
        if self._old_open():
            if self.old.doc.redo():
                self._refill_old()
            return
        if self.doc is not None and self.doc.redo():
            self._fill_outline()
            self._select(self.selected)

    def save(self) -> bool:
        """Keep the edits in the project: the bank in every language folder it lives in, the loose clips. False (and
        the reason shown) when they could not be written."""
        try:
            if self._old_open():
                self._save_old()
            else:
                self._save_bank()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr(TITLE), tr("islands.could_not_save", error=exc))
            return False
        return True

    def _save_bank(self) -> None:
        if self.doc is None or self.tree is None:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        earlier = self.kept.get(self.doc.path, set())
        if not self.doc.changed and not earlier:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        made = outputs.render(self.doc, self.tree)  # no files when every edit was undone: the earlier ones go
        notice = projectpart.save(self.project(), self.key, made.files, earlier)
        self.kept[self.doc.path] = set(made.files)
        self.doc.mark_saved()
        self.statusBar().showMessage(
            tr("sound.kept_files_project", count=len(made.files), text=notice.text)
            + "".join(f"; {p}" for p in made.problems[:3])
        )

    def _save_old(self) -> None:
        """Keep the older game's database and clips: what an earlier save of this session made and an undo took back
        goes again (to the project's copy from before the session, else away)."""
        doc, files = self.old.doc, self.old.doc.files()
        earlier = self.kept.get(doc.path, set())
        if not files and not earlier:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        for path in earlier - set(files):
            if path in self.started and self.tree is not None:
                files[path] = self.tree.read(path)  # the tree has the project's copy over the game's file
        notice = projectpart.save(self.project(), self.key, files, earlier)
        self.kept[doc.path] = set(files)
        doc.mark_saved()
        self.statusBar().showMessage(tr("sound.kept_files_project", count=len(files), text=notice.text))

    def unsaved(self) -> bool:
        """Whether the open bank (or older game's database) has edits the project does not have yet."""
        doc = self.old.doc if self._old_open() else self.doc
        return doc is not None and doc.unsaved

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another bank or game opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.unsaved():
            return True
        return widgets.ask_keep(self, tr(TITLE), tr("texts.keep_edits_project_first"), self.save)

    def closeEvent(self, event) -> None:
        if not self.may_leave():
            event.ignore()
            return
        self.player.stop()
        self.old.stop()
        super().closeEvent(event)
