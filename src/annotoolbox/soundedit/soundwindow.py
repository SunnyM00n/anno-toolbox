"""The sound editor's window (Qt): a game's banks on the left, the open bank's events, containers and music in a tree,
the selected object's facts and edit forms on the right; edits go on the bank's undo stack and are kept in the project.

``CSoundWindow(settings, settings_path)`` is the suite's Sound app; ``open_game`` and ``open_bank`` are the entry points.
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
from ..suite import appfolders, centralmenu, gameopen, gamesync, widgets
from ..suite import project as suite_project
from ..suite.background import CBackground
from ..suite.clipplayer import CClipPlayer
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
from .bankdoc import CBankDoc
from .curvepanel import CCurvePanel
from .musicpanel import CMusicPanel
from .oldpanel import COldSoundPanel
from .registry import CEditError
from .wavewidget import CWaveformView

APP = "sound"
TITLE = N_("sound.sound_editor")
OLD_GAMES = ("anno2070", "anno1404", "anno1701")  # no Wwise banks: a sound database and loose clips
GAMES = {key: gametree.GAMES[key] for key in ("anno117", "anno1800", "anno2205", *OLD_GAMES)}
ROLE = Qt.ItemDataRole.UserRole
WAVE_BINS = 1000  # columns of peaks kept per clip; the view picks from them
MEDIA_ERRORS = (OSError, ValueError, KeyError, IndexError, struct.error)  # a clip that does not read


class CSoundWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_background = CBackground(self)
        self.m_is_synchronous = False  # a test's: the reads run on the caller's thread
        self.m_asked = {"bank": 0, "wave": 0, "play": 0}  # the latest request of each kind: an older answer is dropped
        self.m_player = CClipPlayer(self)
        self.m_key, self.m_tree = "", None
        self.m_doc: CBankDoc | None = None
        self.m_selected = 0
        self.m_shown = (0.0, 0.0, 0.0, 0.0)  # the settings boxes as last loaded
        self.m_kept: dict[tuple[str, str], set[str]] = {}  # per game and bank: the project files its last save made
        self.m_banks = QListWidget()
        self.m_filter = QLineEdit(placeholderText=tr("sound.filter_banks"))
        self.m_outline = QTreeWidget()
        self.m_outline.setHeaderHidden(True)
        self.m_facts = QPlainTextEdit(readOnly=True)
        self.m_wave = CWaveformView()
        self.m_music = CMusicPanel(self._edit, self.statusBar().showMessage)
        self.m_curves = CCurvePanel(self._edit, self.statusBar().showMessage)
        self.m_waves: dict[str, waveform.SShape] = {}  # the shapes drawn lately, by the clip's hash
        self.m_volume, self.m_pitch = QDoubleSpinBox(), QDoubleSpinBox()
        self.m_rand_lo, self.m_rand_hi = QDoubleSpinBox(), QDoubleSpinBox()
        for box, lo, hi in (
            (self.m_volume, -96, 24),
            (self.m_pitch, -2400, 2400),
            (self.m_rand_lo, -2400, 2400),
            (self.m_rand_hi, -2400, 2400),
        ):
            box.setRange(lo, hi)
        self.m_playlist = QLineEdit(placeholderText=tr("sound.child_weight"))
        self.m_codec = QComboBox()
        self.m_codec.addItems(list(clipcodec.CODECS))
        self.m_old = COldSoundPanel(self.statusBar().showMessage, self._cache())
        self.m_old.m_may_leave = self.may_leave
        self.m_pages = QStackedWidget()
        self.m_started: set[str] = set()  # the project's files of the older game's database when it was opened
        self._build()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("sound.file_gt_open_game_pick"))

    # layout
    def _build(self) -> None:
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.addWidget(self.m_filter)
        lay.addWidget(self.m_banks)
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.addRow(tr("sound.volume_db"), self.m_volume)
        form.addRow(tr("sound.pitch_cents"), self.m_pitch)
        form.addRow(tr("sound.random_pitch"), self.m_rand_lo)
        form.addRow(tr("sound.random_pitch_to"), self.m_rand_hi)
        form.addRow(tr("sound.playlist"), self.m_playlist)
        form.addRow(tr("sound.encode_clip"), self.m_codec)
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
        self.m_play_button = made[2]
        self.m_play_button.setShortcut(QKeySequence("Ctrl+Space"))
        self.m_play_button.setToolTip(tr("sound.tip.play_clip"))
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self.m_wave)
        rlay.addWidget(self.m_facts, 1)
        rlay.addWidget(self.m_music)
        rlay.addWidget(self.m_curves)
        rlay.addWidget(form_box)
        rlay.addWidget(buttons)
        split = QSplitter()
        for widget in (left, self.m_outline, right):
            split.addWidget(widget)
        split.setSizes([300, 450, 450])
        self.m_pages.addWidget(split)
        self.m_pages.addWidget(self.m_old)
        self.setCentralWidget(self.m_pages)
        menu = self.menuBar().addMenu(tr("common.menu.file"))
        games = centralmenu.mark(menu.addMenu(tr("common.menu.open_game")), centralmenu.GAME)
        for key, name in GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        centralmenu.mark(
            menu.addAction(tr("sound.save_to_project"), self.save, QKeySequence.StandardKey.Save), centralmenu.SAVE
        )
        widgets.edit_menu(self, self.undo, self.redo)
        widgets.find_key(self, self.m_filter)
        self.m_filter.textChanged.connect(self._fill_banks)
        self.m_banks.itemActivated.connect(lambda item: self.open_bank(item.data(ROLE)))
        self.m_outline.currentItemChanged.connect(
            lambda item, _old: self._select(item.data(0, ROLE) or 0 if item else 0)
        )

    # games and banks
    def project(self) -> suite_project.CProject:
        return suite_project.current(self.m_settings)

    def open_game(self, key: str, then: str = "", is_waiting: bool = False) -> None:
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
            self.m_key, self.m_tree = key, tree
            self.m_doc = None  # asked about in ``may_leave`` before the game was opened
            self._fill_outline()
            self.m_settings.apps.setdefault(APP, {})["game"] = key
            self.m_pages.setCurrentIndex(1 if key in OLD_GAMES else 0)
            if key in OLD_GAMES:
                self.m_started = set(appfolders.edited(self.project(), projectpart.EDITS, key))  # not the imported base
                self.m_old.open_game(tree)
                return
            self._fill_banks()
            self.statusBar().showMessage(
                tr("sound.name_count_banks", name=GAMES.get(key, key), count=self.m_banks.count())
            )
            if then:
                self.open_bank(then)

        gameopen.open_installed(
            self, self.m_settings, key, self.m_background, then=opened, prepare=prepare, is_waiting=is_waiting
        )

    def _fill_banks(self) -> None:
        self.m_banks.clear()
        if self.m_tree is None:
            return
        words = self.m_filter.text().lower().split()
        for path in self.m_tree.m_paths:
            if path.endswith(".bnk") and all(word in path for word in words):
                short = path.split("/windows/")[-1]
                self.m_banks.addItem(short)
                self.m_banks.item(self.m_banks.count() - 1).setData(ROLE, path)

    def _latest(self, kind: str, done):
        """``done`` as a callback that runs only while no later request of ``kind`` was made."""
        self.m_asked[kind] += 1
        mine = self.m_asked[kind]
        return lambda result: done(result) if self.m_asked[kind] == mine else None

    def open_bank(self, path: str) -> None:
        """Read a bank off the window's thread and show it; the bank asked for last is the one shown."""
        if self.m_tree is None or not self.may_leave():
            return
        tree = self.m_tree

        def work() -> CBankDoc:
            bank_info = banks.read_info(
                path, tree.exists, lambda file_path: tree.read(file_path).decode("utf-8", "replace")
            )
            return CBankDoc(tree.read(path), path, bank_info)

        self.statusBar().showMessage(tr("sound.reading_bank", path=path))
        self.m_background.run(
            work, self._latest("bank", lambda doc: self._bank_read(path, tree, doc)), self.m_is_synchronous
        )

    def _bank_read(self, path: str, tree, doc: CBankDoc | Exception) -> None:
        if tree is not self.m_tree:
            return  # another game was opened while it was read
        if isinstance(doc, Exception):
            if not isinstance(
                doc, (*MEDIA_ERRORS, StopIteration)
            ):  # StopIteration: an empty bank (the reader leaks it)
                raise doc
            self.statusBar().showMessage(tr("common.path_not_read_error", path=path, error=doc))
            return
        self.m_doc = doc
        self._fill_outline()
        self.statusBar().showMessage(
            tr(
                "sound.path_version_count_objects",
                path=path,
                version=self.m_doc.m_version,
                count=len(self.m_doc.m_bank.hirc),
            )
        )

    def _fill_outline(self) -> None:
        self.m_outline.clear()
        if self.m_doc is None:
            return
        for group, rows in outline.outline(self.m_doc).items():
            top = QTreeWidgetItem([tr("common.title_count", title=tr(group), count=len(rows))])
            self.m_outline.addTopLevelItem(top)
            for row in rows:
                self._add_row(top, row)

    def _add_row(self, parent: QTreeWidgetItem, row: outline.SRow) -> None:
        item = QTreeWidgetItem(parent, [row.title])
        item.setData(0, ROLE, row.id)
        for child in row.children:
            self._add_row(item, child)

    # the selected object
    def _select(self, obj_id: int) -> None:
        self.m_selected = obj_id
        if self.m_doc is None or not obj_id:
            return
        lines = outline.describe(self.m_doc, obj_id)
        node = self.m_doc.graph.get(obj_id)
        if node is not None and node.exact and node.type in (bnkgraph.RANSEQ, bnkgraph.MIXER, bnkgraph.SWITCH):
            try:
                lay = containers.read_layout(self.m_doc.hirc_object(obj_id), node, self.m_doc.m_version)
                self.m_playlist.setText(", ".join(f"{child}:{weight}" for child, weight in lay.playlist))
                if lay.table:
                    table = "; ".join(
                        tr("sound.value_plays_name", value=value, name=name) for value, name in lay.table.items()
                    )
                    lines.append(tr("sound.values_table", table=table))
            except containers.CLayoutError as exc:
                lines.append(str(exc))
        self.m_facts.setPlainText("\n".join(lines))
        self._load_props()
        self._load_wave()
        self.m_music.load(self.m_doc, obj_id)
        self.m_curves.load(self.m_doc, obj_id)

    def _load_wave(self) -> None:
        """Draw the clip of the picked Sound or music track (``waveform``), or say why there is none. The clip
        is read and decoded off the window's thread; the selection made last is the one drawn."""
        picked, kept = self.m_selected, dict(self.m_waves)

        def work() -> tuple[str, waveform.SShape] | str:
            try:
                data = self._media_of(picked)
            except MEDIA_ERRORS:
                data = None
            if not data:
                return tr("common.no_clip_picked")
            key = hashlib.sha1(data).hexdigest()
            try:
                return key, kept.get(key) or waveform.shape(data, WAVE_BINS)
            except clipcodec.CClipError as exc:
                return str(exc)

        self.m_wave.show_message(tr("sound.reading_clip"))
        self.m_background.run(
            work, self._latest("wave", lambda found: self._wave_read(picked, found)), self.m_is_synchronous
        )

    def _wave_read(self, picked: int, found) -> None:
        if isinstance(found, Exception):
            raise found
        if isinstance(found, str):
            self.m_wave.show_message(found)
            return
        key, shape = found
        self.m_waves = dict(list(self.m_waves.items())[-7:]) | {key: shape}
        self.m_wave.show_shape(shape, musicedits.track_marks(self.m_doc, picked) if self.m_doc is not None else [])

    def _load_props(self) -> None:
        for box in (self.m_volume, self.m_pitch, self.m_rand_lo, self.m_rand_hi):
            box.setValue(0)
        self.m_shown = self._values()
        try:
            block = nodebase.read_props(self.m_doc.hirc_object(self.m_selected), self.m_doc.m_version)
        except (nodebase.CPropError, CEditError):
            return
        version = self.m_doc.m_version
        pitch = nodebase.prop_id(version, "pitch")
        self.m_volume.setValue(block.float(nodebase.prop_id(version, "volume")))
        self.m_pitch.setValue(block.float(pitch))
        span = block.range(pitch) or (0.0, 0.0)
        self.m_rand_lo.setValue(span[0])
        self.m_rand_hi.setValue(span[1])
        self.m_shown = self._values()  # what the boxes made of the file's numbers (clamped, rounded)

    def _values(self) -> tuple[float, float, float, float]:
        return tuple(box.value() for box in (self.m_volume, self.m_pitch, self.m_rand_lo, self.m_rand_hi))  # type: ignore[return-value]

    def _edit(self, name: str, **args) -> bool:
        if self.m_doc is None:
            return False
        try:
            note = self.m_doc.do(name, **args)
        except CEditError as exc:
            self.statusBar().showMessage(tr("sound.cannot_error", error=exc))
            return False
        keep = self.m_selected
        self._fill_outline()
        self._select(keep)
        done = tr("sound.done_ctrl_plus_z_undoes", name=registry.label(name))
        self.statusBar().showMessage(f"{done}; {note}" if isinstance(note, str) and note else done)
        return True

    def _apply(self) -> None:
        """Write the boxes the user changed; a value the form clamped or rounded stays as the file has it."""
        version = self.m_doc.m_version if self.m_doc else 172
        pitch = nodebase.prop_id(version, "pitch")
        volume, pitch_value, lo, hi = self._values()
        was_volume, was_pitch, was_lo, was_hi = self.m_shown
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
        self._edit("set_props", id=self.m_selected, props=props, ranges=ranges)

    def _apply_playlist(self) -> None:
        try:
            entries = [
                tuple(int(x) for x in part.split(":")) for part in self.m_playlist.text().split(",") if part.strip()
            ]
        except ValueError:
            self.statusBar().showMessage(tr("sound.write_playlist_child_weight"))
            return
        self._edit("set_playlist", container=self.m_selected, entries=entries)

    # audio
    def _clip(self, path: str) -> bytes | None:
        """A file made into a ``.wem`` in the codec picked (the status line says why not)."""
        try:
            clip = clipcodec.read_clip(Path(path).read_bytes(), path)
            return clipcodec.encode(clip, self.m_codec.currentText(), self.m_doc.m_version)
        except (OSError, clipcodec.CClipError) as exc:
            self.statusBar().showMessage(str(exc))
            return None

    def _pick_clip(self) -> bytes | None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("sound.wav_ogg_file"), "", "Audio (*.wav *.ogg *.mp3 *.flac)"
        )  # i18n: ok (a file filter)
        return self._clip(path) if path else None

    def _replace(self) -> None:
        if self.m_doc is None or not (wem := self._pick_clip()):
            return
        node = self.m_doc.graph.get(self.m_selected)
        try:
            if node is not None and node.source is not None:
                self._edit(
                    "replace_audio",
                    sound=self.m_selected,
                    wem=wem,
                    media=audioedits.plan_media(self.m_doc, self.m_selected),
                )
            else:
                self._edit(
                    "replace_track_audio", track=self.m_selected, index=0, wem=wem, media=self._track_media(),
                    markers=self.m_music.m_fit.currentText(),
                )  # fmt: skip
        except CEditError as exc:
            self.statusBar().showMessage(tr("sound.cannot_error", error=exc))

    def _track_media(self) -> int:
        return bnkmusic.parse_track(self.m_doc.hirc_object(self.m_selected)).m_sources[0].source_id & 0xFFFFFFFF

    def _add_sound(self) -> None:
        if self.m_doc is None:
            return
        node = self.m_doc.graph.get(self.m_selected)
        parent = self.m_selected if node is not None and node.type != bnkgraph.SOUND else (node.parent if node else 0)
        template = next(
            (
                sound
                for sound in self.m_doc.graph.values()
                if sound.source is not None and (sound.parent == parent or not parent)
            ),
            None,
        )
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("sound.name_new_sound"))
        if not ok or not name.strip() or template is None or not (wem := self._pick_clip()):
            return
        value = None
        switch = self.m_doc.graph[parent].switch if parent in self.m_doc.graph else None
        if switch is not None:
            value = next(iter(switch.branches), None)
        self._edit("add_sound", sound=self.m_doc.new_id(name), template=template.id, parent=parent, wem=wem,
                   media=self.m_doc.new_id(name + ".wem"), value=value)  # fmt: skip

    def _add_event(self) -> None:
        if self.m_doc is None or not self.m_selected:
            return
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("sound.name_new_event_game_posts"))
        if ok and name.strip():
            self._edit(
                "add_event",
                event=self.m_doc.new_id(name),
                action=self.m_doc.new_id(name + ".action"),
                target=self.m_selected,
            )

    def _new_group(self) -> None:
        """Ask for a group (kind, name, values and, for a switch group, its game parameter and points) and add it."""
        if self.m_doc is None:
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
                    points = [
                        (float(x), label.strip(), 9) for x, label in (row.split(":") for row in formrows.entries(rows))
                    ]
                except ValueError:
                    self.statusBar().showMessage(tr("sound.write_points_value_switch_separated"))
                    return
        plan = groupedits.plan_group(self.m_doc, "state" if state else "switch", name, values, parameter, points)
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
        picked, cache = self.m_selected, self._cache()

        def work() -> Path | str:
            try:
                data = self._media_of(picked)
                if not data:
                    return tr("sound.nothing_play_here_pick_sound")
                return self.m_player.prepare("x.wem", data, cache)
            except MEDIA_ERRORS as exc:  # the clip did not read, or its decoded copy could not be written
                return tr("common.cannot_play_error", error=exc)

        self.statusBar().showMessage(tr("sound.reading_clip"))
        self.m_background.run(work, self._latest("play", self._play_read), self.m_is_synchronous)

    def _play_read(self, found: Path | str | Exception) -> None:
        if isinstance(found, Exception):
            raise found
        if isinstance(found, str):
            self.statusBar().showMessage(found)
            return
        self.statusBar().clearMessage()
        self.m_player.start(found)

    def _media_of(self, obj_id: int) -> bytes | None:
        """The clip of a sound or the first source of a music track: an edit's, the bank's, the loose file's."""
        doc, tree = self.m_doc, self.m_tree  # read once: a worker thread calls this while the window goes on
        if doc is None or not obj_id:
            return None
        node = doc.graph.get(obj_id)
        media = node.source.media if node is not None and node.source is not None else None
        if media is None and doc.hirc_object(obj_id).type == 11:
            media = bnkmusic.parse_track(doc.hirc_object(obj_id)).m_sources[0].source_id & 0xFFFFFFFF
        if media is None:
            return None
        if media in doc.m_loose:
            return doc.m_loose[media].data
        if (data := doc.media(media)) is not None and not (node and node.source and node.source.stream):
            return data
        if tree is None:
            return doc.media(media)
        found = banks.media_file(doc.m_path, media, doc.m_bank_info, tree.exists)
        return tree.read(found) if found else doc.media(media)

    # the stack and the project
    def _cache(self) -> Path:
        """Where a played clip is written, once per content: the workspace cache's ``sounds``, which the other apps
        that play a clip share (the temp folder kept every clip for good)."""
        return assetgame.cache_dir(self.m_settings.workspace) / "sounds"

    def _old_open(self) -> bool:
        return self.m_pages.currentIndex() == 1 and self.m_old.m_doc is not None

    def _refill_old(self) -> None:
        self.m_old.fill(self.m_old.selected)
        self.m_old.show_sound(self.m_old.selected)

    def undo(self) -> None:
        if self._old_open():
            if self.m_old.m_doc.undo():
                self._refill_old()
            return
        if self.m_doc is not None and self.m_doc.undo():
            self._fill_outline()
            self._select(self.m_selected)

    def redo(self) -> None:
        if self._old_open():
            if self.m_old.m_doc.redo():
                self._refill_old()
            return
        if self.m_doc is not None and self.m_doc.redo():
            self._fill_outline()
            self._select(self.m_selected)

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
        if self.m_doc is None or self.m_tree is None:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        kept_key = (self.m_key, self.m_doc.m_path)
        earlier = self.m_kept.get(kept_key, set())
        if not self.m_doc.changed and not earlier:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        made = outputs.render(self.m_doc, self.m_tree)  # no files when every edit was undone: the earlier ones go
        notice = projectpart.save(self.project(), self.m_key, made.files, earlier)
        self.m_kept[kept_key] = set(made.files)
        self.m_doc.mark_saved()
        self.statusBar().showMessage(
            tr("sound.kept_files_project", count=len(made.files), text=notice.text)
            + "".join(f"; {problem}" for problem in made.problems[:3])
        )

    def _save_old(self) -> None:
        """Keep the older game's database and clips: what an earlier save of this session made and an undo took back
        goes again (to the project's copy from before the session, else away)."""
        doc, files = self.m_old.m_doc, self.m_old.m_doc.files()
        kept_key = (self.m_key, doc.m_path)
        earlier = self.m_kept.get(kept_key, set())
        if not files and not earlier:
            self.statusBar().showMessage(tr("common.nothing_changed"))
            return
        for path in earlier - set(files):
            if path in self.m_started and self.m_tree is not None:
                files[path] = self.m_tree.read(path)  # the tree has the project's copy over the game's file
        notice = projectpart.save(self.project(), self.m_key, files, earlier)
        self.m_kept[kept_key] = set(files)
        doc.mark_saved()
        self.statusBar().showMessage(tr("sound.kept_files_project", count=len(files), text=notice.text))

    def unsaved(self) -> bool:
        """Whether the open bank (or older game's database) has edits the project does not have yet."""
        doc = self.m_old.m_doc if self._old_open() else self.m_doc
        return doc is not None and doc.unsaved

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another bank or game opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.unsaved():
            return True
        return widgets.ask_keep(self, tr(TITLE), tr("texts.keep_edits_project_first"), self.save)

    def can_close(self) -> bool:
        """The Toolbox's first step of closing: the question about unkept edits, nothing released."""
        return self.may_leave()

    def closeEvent(self, event) -> None:
        if not widgets.may_close(self):
            event.ignore()
            return
        self.m_player.stop()
        self.m_old.stop()
        super().closeEvent(event)
