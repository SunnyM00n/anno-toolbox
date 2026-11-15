"""The sound editor's window (Qt): a game's banks on the left, the open bank's events, containers and music in a tree,
the selected object's facts and edit forms on the right; edits go on the bank's undo stack and are kept in the project.

``SoundWindow(settings, settings_path)`` is the suite's Sound app; ``open_game`` and ``open_bank`` are the entry points.
docs/sound-editor.md is the user's guide.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
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

from ..exporting import banks
from ..formats import bnkgraph, bnkmusic
from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import gameopen, widgets
from ..suite import project as suite_project
from ..suite.background import Background
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
TITLE = N_("Sound editor")
OLD_GAMES = ("anno2070", "anno1404", "anno1701")  # no Wwise banks: a sound database and loose clips
GAMES = {key: gametree.GAMES[key] for key in ("anno117", "anno1800", "anno2205", *OLD_GAMES)}
ROLE = Qt.ItemDataRole.UserRole
WAVE_BINS = 1000  # columns of peaks kept per clip; the view picks from them


class SoundWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.key, self.tree = "", None
        self.doc: BankDoc | None = None
        self.selected = 0
        self.shown = (0.0, 0.0, 0.0, 0.0)  # the settings boxes as last loaded
        self.kept: dict[str, set[str]] = {}  # per bank: the project files its last save made
        self.banks = QListWidget()
        self.filter = QLineEdit(placeholderText=tr("filter the banks"))
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
        self.playlist = QLineEdit(placeholderText=tr("child:weight, child:weight ..."))
        self.codec = QComboBox()
        self.codec.addItems(list(clipcodec.CODECS))
        self.status_label = QLabel()
        self.old = OldSoundPanel(self.statusBar().showMessage, self._cache())
        self.old.may_leave = self.may_leave
        self.pages = QStackedWidget()
        self.started: set[str] = set()  # the project's files of the older game's database when it was opened
        self._build()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("File > Open game, then pick a bank on the left."))

    # layout
    def _build(self) -> None:
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.addWidget(self.filter)
        lay.addWidget(self.banks)
        form_box = QWidget()
        form = QFormLayout(form_box)
        form.addRow(tr("volume (dB)"), self.volume)
        form.addRow(tr("pitch (cents)"), self.pitch)
        form.addRow(tr("random pitch from"), self.rand_lo)
        form.addRow(tr("random pitch to"), self.rand_hi)
        form.addRow(tr("playlist"), self.playlist)
        form.addRow(tr("encode a clip as"), self.codec)
        buttons = QWidget()
        blay = QVBoxLayout(buttons)
        formrows.add_buttons(
            blay,
            (
                (N_("Apply the settings"), self._apply),
                (N_("Apply the playlist"), self._apply_playlist),
                (N_("Play"), self._play),
                (N_("Replace the audio..."), self._replace),
                (N_("Add a sound here..."), self._add_sound),
                (N_("Add an event that plays this..."), self._add_event),
                (N_("New state or switch group..."), self._new_group),
            ),
        )
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
        menu = self.menuBar().addMenu(tr("&File"))
        games = menu.addMenu(tr("Open &game"))
        for key, name in GAMES.items():
            games.addAction(name, lambda k=key: self.open_game(k))
        menu.addAction(tr("&Save to the project"), self.save, QKeySequence.StandardKey.Save)
        widgets.edit_menu(self, self.undo, self.redo)
        self.filter.textChanged.connect(self._fill_banks)
        self.banks.itemActivated.connect(lambda item: self.open_bank(item.data(ROLE)))
        self.outline.currentItemChanged.connect(lambda item, _old: self._select(item.data(0, ROLE) if item else 0))

    # games and banks
    def project(self) -> suite_project.Project:
        return suite_project.current(self.settings)

    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) in the background; ``then``: a bank to open."""
        if not self.may_leave():
            return

        def prepare(tree):
            mine = projectpart.EDITS.folder(self.project(), key)
            return tree.over(mine, "project") if mine.is_dir() else tree

        def opened(tree) -> None:
            if isinstance(tree, Exception):
                self.statusBar().showMessage(tr("{game} did not open: {error}", game=GAMES.get(key, key), error=tree))
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
            self.statusBar().showMessage(tr("{name}: {count} banks", name=GAMES.get(key, key), count=len(self.banks)))
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

    def open_bank(self, path: str) -> None:
        if self.tree is None or not self.may_leave():
            return
        tree = self.tree
        info = banks.read_info(path, tree.exists, lambda p: tree.read(p).decode("utf-8", "replace"))
        try:
            self.doc = BankDoc(tree.read(path), path, info)
        except (ValueError, OSError) as exc:
            self.statusBar().showMessage(tr("{path} does not read: {error}", path=path, error=exc))
            return
        self._fill_outline()
        self.statusBar().showMessage(
            tr(
                "{path}: version {version}, {count} objects",
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
            top = QTreeWidgetItem([tr("{title} ({count})", title=tr(group), count=len(rows))])
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
                    table = "; ".join(tr("{value} plays {name}", value=v, name=n) for v, n in lay.table.items())
                    lines.append(tr("values: {table}", table=table))
            except containers.LayoutError as exc:
                lines.append(str(exc))
        self.facts.setPlainText("\n".join(lines))
        self._load_props()
        self._load_wave()
        self.music.load(self.doc, obj_id)
        self.curves.load(self.doc, obj_id)

    def _load_wave(self) -> None:
        """Draw the clip of the picked Sound or music track (``waveform``), or say why there is none."""
        try:
            data = self._media_of(self.selected)
        except (OSError, ValueError, KeyError):
            data = None
        if not data:
            self.wave.show_message(tr("no clip picked"))
            return
        key = hashlib.sha1(data).hexdigest()
        try:
            if key not in self.waves:
                self.waves = dict(list(self.waves.items())[-7:]) | {key: waveform.shape(data, WAVE_BINS)}
        except clipcodec.ClipError as exc:
            self.wave.show_message(str(exc))
            return
        self.wave.show_shape(self.waves[key], musicedits.track_marks(self.doc, self.selected))

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
            self.statusBar().showMessage(tr("cannot: {error}", error=exc))
            return False
        keep = self.selected
        self._fill_outline()
        self._select(keep)
        done = tr("{name} done (Ctrl+Z undoes it)", name=registry.label(name))
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
            self.statusBar().showMessage(tr("no setting was changed"))
            return
        self._edit("set_props", id=self.selected, props=props, ranges=ranges)

    def _apply_playlist(self) -> None:
        try:
            entries = [
                tuple(int(x) for x in part.split(":")) for part in self.playlist.text().split(",") if part.strip()
            ]
        except ValueError:
            self.statusBar().showMessage(tr("write the playlist as child:weight, child:weight"))
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
            self, tr("A WAV or Ogg file"), "", "Audio (*.wav *.ogg *.mp3 *.flac)"
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
            self.statusBar().showMessage(tr("cannot: {error}", error=exc))

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
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("Name of the new sound"))
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
        name, ok = QInputDialog.getText(self, tr(TITLE), tr("Name of the new event (the game posts it by name)"))
        if ok and name.strip():
            self._edit(
                "add_event", event=self.doc.new_id(name), action=self.doc.new_id(name + ".action"), target=self.selected
            )

    def _new_group(self) -> None:
        """Ask for a group (kind, name, values and, for a switch group, its game parameter and points) and add it."""
        if self.doc is None:
            return
        kinds = [tr("state group"), tr("switch group")]
        kind, ok = QInputDialog.getItem(self, tr(TITLE), tr("Kind of the group"), kinds, 0, False)
        name, named = QInputDialog.getText(self, tr(TITLE), tr("Name of the group (its id is the hash of the name)"))
        if not ok or not named or not name.strip():
            return
        text, _ = QInputDialog.getText(self, tr(TITLE), tr("Its values, separated by commas"))
        values = [v.strip() for v in text.split(",") if v.strip()]
        state = kind == kinds[0]
        parameter, points = "", []
        if not state:
            parameter, _ = QInputDialog.getText(self, tr(TITLE), tr("Game parameter that drives it (empty: none)"))
            if parameter.strip():
                rows, _ = QInputDialog.getText(self, tr(TITLE), tr("Points as value:switch, value:switch ..."))
                try:
                    points = [(float(x), s.strip(), 9) for x, s in (r.split(":") for r in rows.split(",") if r.strip())]
                except ValueError:
                    self.statusBar().showMessage(tr("write the points as value:switch, separated by commas"))
                    return
        plan = groupedits.plan_group(self.doc, "state" if state else "switch", name, values, parameter, points)
        if isinstance(plan, str):
            self.statusBar().showMessage(plan)
            self._fill_outline()
        else:
            self._edit(plan[0], **plan[1])

    def _play(self) -> None:
        data = self._media_of(self.selected)
        if not data:
            self.statusBar().showMessage(tr("nothing to play here: pick a sound or a music track"))
            return
        try:
            out, suffix = banks.playable(data, "x.wem")
        except banks.Unplayable as exc:
            self.statusBar().showMessage(tr("cannot play: {error}", error=exc))
            return
        target = Path(tempfile.gettempdir()) / f"soundedit-{hashlib.sha1(data).hexdigest()[:12]}{suffix}"
        target.write_bytes(out)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        if not hasattr(self, "_player"):
            self._player, self._out = QMediaPlayer(self), QAudioOutput(self)
            self._player.setAudioOutput(self._out)
        self._player.setSource(QUrl.fromLocalFile(str(target)))
        self._player.play()

    def _media_of(self, obj_id: int) -> bytes | None:
        """The clip of a sound or the first source of a music track: an edit's, the bank's, the loose file's."""
        doc = self.doc
        if doc is None or not obj_id:
            return None
        node = doc.graph.get(obj_id)
        media = node.source.media if node is not None and node.source is not None else None
        if media is None and doc.obj(obj_id).type == 11:
            media = self._track_media()
        if media is None:
            return None
        if media in doc.loose:
            return doc.loose[media].data
        if (data := doc.media(media)) is not None and not (node and node.source and node.source.stream):
            return data
        if self.tree is None:
            return doc.media(media)
        found = banks.media_file(doc.path, media, doc.info, self.tree.exists)
        return self.tree.read(found) if found else doc.media(media)

    # the stack and the project
    def _cache(self) -> Path:
        return Path(tempfile.gettempdir()) / "anno-toolbox-sound"

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
            QMessageBox.warning(self, tr(TITLE), tr("could not save: {error}", error=exc))
            return False
        return True

    def _save_bank(self) -> None:
        if self.doc is None or self.tree is None:
            self.statusBar().showMessage(tr("nothing changed"))
            return
        earlier = self.kept.get(self.doc.path, set())
        if not self.doc.changed and not earlier:
            self.statusBar().showMessage(tr("nothing changed"))
            return
        made = outputs.render(self.doc, self.tree)  # no files when every edit was undone: the earlier ones go
        notice = projectpart.save(self.project(), self.key, made.files, earlier)
        self.kept[self.doc.path] = set(made.files)
        self.doc.mark_saved()
        self.statusBar().showMessage(
            tr("kept {count} files in the project; {text}", count=len(made.files), text=notice.text)
            + "".join(f"; {p}" for p in made.problems[:3])
        )

    def _save_old(self) -> None:
        """Keep the older game's database and clips: what an earlier save of this session made and an undo took back
        goes again (to the project's copy from before the session, else away)."""
        doc, files = self.old.doc, self.old.doc.files()
        earlier = self.kept.get(doc.path, set())
        if not files and not earlier:
            self.statusBar().showMessage(tr("nothing changed"))
            return
        for path in earlier - set(files):
            if path in self.started and self.tree is not None:
                files[path] = self.tree.read(path)  # the tree has the project's copy over the game's file
        notice = projectpart.save(self.project(), self.key, files, earlier)
        self.kept[doc.path] = set(files)
        doc.mark_saved()
        self.statusBar().showMessage(
            tr("kept {count} files in the project; {text}", count=len(files), text=notice.text)
        )

    def unsaved(self) -> bool:
        """Whether the open bank (or older game's database) has edits the project does not have yet."""
        doc = self.old.doc if self._old_open() else self.doc
        return doc is not None and doc.unsaved

    def may_leave(self) -> bool:
        """Ask before unkept edits are dropped (another bank or game opened, the window closed): False when the person
        cancelled or the save failed."""
        if not self.unsaved():
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(self, tr(TITLE), tr("Keep the edits in the project first?"),
                                      buttons.Save | buttons.Discard | buttons.Cancel)  # fmt: skip
        if answer == buttons.Save:
            return self.save()
        return answer != buttons.Cancel

    def closeEvent(self, event) -> None:
        if not self.may_leave():
            event.ignore()
            return
        super().closeEvent(event)
