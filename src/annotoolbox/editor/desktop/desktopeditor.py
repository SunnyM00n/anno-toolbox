"""The island open in the workbench (``workbench``): its edit session (``editsession``), the panels that show it
- Material Gallery, Object Groups (the model library), the objects on the island, the selection's properties,
layers, check results and history - and what the ribbon's buttons do to it. Plan and use:
``docs/island-editor.md``.
"""

from __future__ import annotations

import io

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import N_, tr
from ...render import quality
from ...suite import features
from .. import islandrules, terraintools
from ..editsession import EditSession, game_of
from .desktoppanels import LibraryPanel, MaterialGallery, ObjectsPanel, PropertiesPanel
from .desktopplace import PlaceablesPanel
from .desktopview import BRUSHES, TOOLS
from .minimappanel import MinimapPanel

LOOKS = (
    ("game_terrain", N_("Game terrain"), N_("the ground as the game draws it near the camera: rebuilt from the island's height pages (saved island)")),
    ("native", N_("The game's own shaders"), N_("the terrain by the game's own shaders, in game light (117: Direct3D 12; 1800 and 2205: Direct3D 11)")),
    ("look2070", N_("2070 look"), N_("islands edited for 2070: drawn as Anno 2070 draws them")),
    ("underwater", N_("Underwater look"), N_("the 2070 look seen under water: light absorbed by depth and distance, the sea's haze")),
    ("caustics", N_("Caustics"), N_("the underwater look's light patterns on the sea floor (an approximation)")),
)  # fmt: skip
LOOKS_2070 = ("look2070", "underwater", "caustics")  # the looks of islands edited for 2070
GAME_TERRAIN = ("anno1800",)  # other games whose islands have the game's near terrain (height pages) for the view
NO_TOOLS = {
    "anno117": {"blocker"},
    "anno2070": {"active", "surf"},
    "anno1404": {"forest", "active", "surf"},  # trees are objects: the spray places them
    "anno1701": {"forest", "blocker", "active", "surf"},
    "anno1800": {"forest", "blocker"},
    "anno2205": {"forest", "blocker", "active", "surf"},  # trees are props; no active map or surf in a session map
    "built": {"blocker", "active", "surf"},
    "file": {"blocker", "active", "surf"},
}
LAYER_NAMES = {  # the view's layers (``desktoprender.LAYERS``) as a person reads them
    "objects": N_("objects"),
    "markers": N_("markers"),
    "props": N_("effects"),
    "grass": N_("grass"),
    "forest": N_("forest"),
    "water": N_("water"),
}
CHECK_NAMES = {"rules": N_("rules"), "slots": N_("slots"), "falls": N_("falls")}  # the checks of the Grid tab
OTHER_NO_TOOLS = {"water", "forest", "place", "blocker", "spray", "active", "surf"}  # an island of another game


def game_tools(games: tuple[str, ...] | None) -> set[str]:
    """The tools any island of these games (None: all) offers."""
    kinds = games or tuple(NO_TOOLS)
    return set().union(*(set(TOOLS) - NO_TOOLS.get(k, OTHER_NO_TOOLS) for k in kinds))


def modes(info: dict, island: dict) -> dict:
    """What an opened island (``editorapp.App.island``) may do: its tools and how it is saved and built."""
    kind = info.get("kind")
    tools = set(TOOLS) - NO_TOOLS.get(kind, OTHER_NO_TOOLS)
    if not info.get("editable", True):
        tools = {"select"}
    game = island.get("game_name") or ("Anno 2070" if island.get("mode") == "2070" else "Anno 117")
    return {"tools": tools, "game": game, "buildable": info.get("buildable", kind in ("built", "anno117", "anno2070"))}


class LayersPanel(QWidget):
    """What the view draws: its layers and the shadows (the view tab "Layers")."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc, view = doc, doc.view
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.boxes = {}
        for k, key in enumerate(view.opts.layers):
            cb = QCheckBox(tr(LAYER_NAMES.get(key, key)), checked=True)
            cb.toggled.connect(lambda on, key=key: self._layer(key, on))
            self.boxes[key] = cb
            grid.addWidget(cb, k // 3, k % 3)
        layout.addLayout(grid)
        layout.addStretch(1)

    def set_layer(self, key: str, on: bool) -> None:
        self.boxes[key].setChecked(on)

    def _layer(self, key: str, on: bool) -> None:
        self.doc.view.opts.layers[key] = on
        self.doc.view.update()


class ChecksPanel(QListWidget):
    """The checks' results (the view tab "Checks"); a click flies to the place."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        self._rows: dict[str, list] = {}
        self.itemClicked.connect(self._result)

    def show_results(self, check: str, rows: list[tuple[str, list | None]]) -> None:
        """One check's lines (the other check's stay): (text, Anno position to fly to)."""
        self._rows[check] = rows
        self.clear()
        for text, at in [(f"{tr(CHECK_NAMES.get(c, c))}: {t}", a) for c, rs in self._rows.items() for t, a in rs]:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, at)
            self.addItem(item)

    def _result(self, item) -> None:
        at = item.data(Qt.ItemDataRole.UserRole)
        if at:
            self.doc.shell.show_page("Viewport")
            self.doc.view.go_to(at[0], at[2], 18.0)


class HistoryPanel(QListWidget):
    """The undo stack: a click goes back (or forward) to that change."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        self.itemClicked.connect(self._jump)

    def fill(self) -> None:
        stack = self.doc.session.undo
        self.clear()
        self.addItem(tr("(as opened)"))
        for k, label in enumerate(stack.labels()):
            self.addItem(f"{label}" + ("  · " + tr("saved") if k + 1 == stack.clean else ""))
        undone, text = self.palette().placeholderText(), self.palette().text()
        for k in range(self.count()):
            self.item(k).setForeground(undone if k > stack.index else text)
        self.setCurrentRow(stack.index)

    def _jump(self, item) -> None:
        stack, want = self.doc.session.undo, self.row(item)
        while stack.index > want and stack.undo():
            pass
        while stack.index < want and stack.redo():
            pass


def open_session(editor, info: dict) -> tuple:
    """(editor, island, session): the opened island's data and its edit session, made off the window's thread when
    the island is large (``workbench.MainWindow._load``)."""
    island = editor.island_data()
    island["readonly"] = not info.get("editable", True)  # the app decides (the island's JSON is cached)
    session = EditSession(island, editor.edits(), readonly=not info.get("editable", True))
    session.finish_loading()
    return editor, island, session


class IslandDoc(QObject):
    """One island open in the workbench: its edit session and panels, and what the ribbon's buttons do to it.
    The workbench's view is loaded with it and emptied again by ``close``. ``info``: what the app opened
    (``editorapp.App.island``); ``opened``: how it was asked for, so the same request only shows it again."""

    def __init__(self, shell, title: str, info: dict, opened: tuple | None = None, prepared=None) -> None:
        super().__init__(shell)
        self.shell, self.info, self.title, self.opened = shell, info, title, opened
        self.view, self.background = shell.view, shell.background
        self.alive = True
        self.editor, island, self.session = prepared or open_session(shell.app.editor, info)
        self.modes = modes(info, island)
        self.own_materials: list[dict] = []
        self.view.load(self.session, self.editor)
        self.apply_quality()
        self.apply_game_look()
        self.view.allowed = self.modes["tools"]
        self.gallery = MaterialGallery(self)
        self.library = LibraryPanel(self)
        self.buildings = PlaceablesPanel(self)
        self.objects = ObjectsPanel(self)
        self.props = PropertiesPanel(self)
        self.layers = LayersPanel(self)
        self.checks = ChecksPanel(self)
        self.history = HistoryPanel(self)
        self.minimap = MinimapPanel(self.session, self.background)
        self._links = [
            (self.view.selectionChanged, self._selected),
            (self.view.waterChanged, self.props.show_water),
            (self.view.toolChanged, self._tool),
        ]
        for signal, slot in self._links:
            signal.connect(slot)
        self.session.undo.listeners.append(self._changed)
        self.session.listeners.append(self._structure)
        self.objects.fill()
        self.history.fill()
        counts = island.get("objects", []), island.get("props", []), (island.get("forest") or {}).get("tiles", [])
        self.say(
            tr(
                "{objects} objects · {props} props · {tiles} forest tiles",
                objects=len(counts[0]),
                props=len(counts[1]),
                tiles=len(counts[2]),
            )
        )
        self._load_side_data()
        self.report_notes()

    def report_notes(self) -> None:
        """What the island's reader left out (``IslandEditor.notes``): into the Log, the first in the status bar."""
        notes = getattr(self.editor, "notes", None) or []
        for note in notes:
            self.shell.log.appendPlainText(note)
        if notes:
            more = " " + tr("(+{n} more in the Log)", n=len(notes) - 1) if len(notes) > 1 else ""
            self.say(notes[0] + more)

    # what the workbench asks
    def state(self) -> dict:
        """What the ribbon needs to know (``desktopribbon.available``)."""
        s, v = self.session, self.view
        editable = not s.readonly
        return {
            "island": True,
            "editable": editable,
            "tools": set(self.modes["tools"]),
            "undo": s.undo.index > 0,
            "redo": s.undo.index < len(s.undo.commands),
            "buildable": self.modes["buildable"] and editable,
            "selection": editable and v.selected is not None and v.selected.alive,
            "river_point": editable and v.river_selected(),
            "slots": not (s.island.get("mode") == "2070" or s.island.get("game")),
            "brush": editable and bool(self.modes["tools"] & set(BRUSHES)),
            "kind117": editable and self.info.get("kind") == "anno117",
        }

    def game_key(self) -> str:
        """The game the island follows ("anno117", ...): whose rendering quality the view draws at."""
        return game_of(self.session.island, kind=str(self.info.get("kind", "")))

    def apply_quality(self) -> None:
        """The view at the rendering quality the settings keep for the island's game (``render.quality``: 117's and
        1800's; the older games at their best)."""
        game = self.game_key()
        stored = getattr(self.shell.app.settings, "quality", {}).get(game)
        self.view.opts.quality = quality.resolve(game, stored) if game in quality.GAMES else None
        self.view.update()

    def apply_game_look(self) -> None:
        """An island of a game with a look of its own shaders other than 117's (``desktopgame.LOOKS``: 1800, 2205)
        opens in it where the view draws on Direct3D (12, the default, or 11), 1800's with the game's terrain (the
        owner's rule for 117 of 2026-11-13: the editor's look is the fallback; island-1800-2205-view.md 4)."""
        from .desktopgame import LOOKS as GAME_LOOKS

        game = self.session.island.get("game")
        if game in GAME_LOOKS and getattr(self.view, "backend", "gl") in ("d3d12", "d3d11"):
            self.view.opts.native = True
            self.view.opts.game_terrain = game in GAME_TERRAIN
            self.view.update()

    def look_state(self) -> dict[str, tuple[bool, bool]]:
        """Per look and ``shadows``: (enabled, checked). 117's rules are for 117's islands, built or vanilla, and the
        game's own shaders also for the games with a look of theirs (with the game's terrain where the island has
        it); the 2070 look for islands edited for 2070."""
        from .desktopgame import LOOKS as GAME_LOOKS

        island, o = self.session.island, self.view.opts
        game = island.get("game")
        out = {"shadows": (True, o.shadows), "particles": (True, o.particles)}
        out["particles_frozen"] = (o.particles, o.particle_time is not None)
        for key, _label, _tip in LOOKS:
            ok = (key in LOOKS_2070) == (island.get("mode") == "2070") and not game
            ok = ok or (key == "native" and game in GAME_LOOKS) or (key == "game_terrain" and game in GAME_TERRAIN)
            out[key] = (ok and (o.underwater or key != "caustics"), getattr(o, key))
        return out

    def window_title(self) -> str:
        dirty = " *" if self.session.undo.dirty and not self.session.readonly else ""
        return f"{self.title}{dirty} · {self.modes['game']}"

    def badge(self) -> str:
        o = self.view.opts
        parts = [
            n
            for k, n in (
                ("game_terrain", tr("terrain")),
                ("native", tr("its shaders")),
            )
            if getattr(o, k)
        ]  # fmt: skip
        look = (
            tr("2070 underwater look")
            if o.underwater
            else tr("2070 look")
            if o.look2070
            else tr("the game's rules: {parts}", parts=", ".join(parts))
            if parts and self.session.island.get("game")
            else tr("117 rules: {parts}", parts=", ".join(parts))
            if parts
            else tr("editor look")
        )
        game = self.modes["game"]
        head = (
            tr("<b>{game} island (view only)</b>", game=game)
            if self.session.readonly
            else tr("<b>{game} island</b>", game=game)
        )
        return f"{head} · {look}"

    # ribbon actions
    def say(self, text: str) -> None:
        self.shell.say(text)

    def set_look(self, key: str, on: bool) -> None:
        o = self.view.opts
        if key == "particles_frozen":  # hold the effects at the moment of the last frame
            renderer = self.view.renderer
            o.particle_time = (renderer.particles.clock if renderer is not None else 0.0) if on else None
            self.view.update()
            self.shell.sync()
            return
        if on and key == "underwater":  # it is that look, seen from below
            o.look2070 = True
        if on and key == "native":
            o.look2070 = o.underwater = o.caustics = False
        if not on and key in ("look2070", "underwater"):
            o.caustics = False
            if key == "look2070":
                o.underwater = False
        setattr(o, key, on)
        self.view.update()
        self.shell.sync()

    def set_tool(self, action: str) -> None:
        """A tool button (``tool_raise``, ``tool_plant``, ``tool_paint``, ...): the tool, and its brush mode."""
        name, brush = action.removeprefix("tool_"), self.view.brush
        if name in ("raise", "lower", "flatten", "smooth", *terraintools.MODES):
            brush["mode"], name = name, "height"
        elif name in ("plant", "clear"):
            brush["plant"], name = name == "plant", "forest"
        elif name in ("block", "free"):
            brush["plant"], name = name == "block", "blocker"
        elif name in ("cell_on", "cell_off", "ford_on", "ford_off", "area_on", "area_off", "herd_on", "herd_off"):
            brush["plant"], brush["grid"], name = name.endswith("_on"), name.split("_")[0], "active"
        elif name == "zone":
            brush["grid"], name = "environment", "active"
        elif name in ("areakind", "tint"):
            brush["grid"], name = name, "active"
        elif name in ("surf_on", "surf_off"):
            brush["plant"], name = name == "surf_on", "surf"
        self.view.set_tool(name)
        self.shell.sync()

    def set_gizmo(self, action: str) -> None:
        mode, tilt = {
            "gizmo_move": ("translate", False),
            "gizmo_turn": ("rotate", False),
            "gizmo_tilt": ("rotate", True),
            "gizmo_scale": ("scale", False),
        }[action]
        self.view.set_gizmo(mode, tilt)
        self.shell.sync()

    def set_brush(self, key: str, value) -> None:
        self.view.brush[key] = value

    def set_target(self, index: int) -> None:
        self.session.set_target("new" if index else "replace")

    def goto(self, text: str) -> None:
        try:
            v = [float(t) for t in text.replace(",", " ").split()]
        except ValueError:
            return
        if len(v) >= 2:
            self.view.go_to(v[0], v[1], v[2] if len(v) > 2 else 25.0, v[3] if len(v) > 3 else 0.0)

    def deselect(self) -> None:
        self.view.select(None)
        self.view.select_water(None)

    def focus(self) -> None:
        if self.view.selected is not None:
            self.view.focus_on([self.view.selected])

    def undo(self) -> None:
        label = self.session.undo.undo()
        self.say(tr("undone: {label}", label=label) if label else tr("nothing to undo"))
        if self.view.selected is not None and not self.view.selected.alive:
            self.view.select(None)

    def redo(self) -> None:
        label = self.session.undo.redo()
        self.say(tr("redone: {label}", label=label) if label else tr("nothing to redo"))

    def save(self) -> bool:
        if self.session.readonly:
            return False
        try:
            self.session.save(self.editor)
        except Exception as exc:
            QMessageBox.warning(self.shell, tr("Save"), tr("could not save: {error}", error=exc))
            return False
        self.say(tr("saved {path}", path=self.editor.edits_path))
        return True

    def build(self) -> None:
        if self.session.undo.dirty and not self.save():
            return
        self.shell.build_opened(self.info)

    def run_check(self, name: str) -> None:
        """Run the check ``name`` (the editor's own ``rules``, or one registered at ``features.CHECKS``) and list what
        it found in the Checks tab."""
        check = islandrules.RULES if name == "rules" else features.extension(features.CHECKS, name)
        if check is None:
            self.say(tr("the {name} check is not available in this edition", name=name))
            return
        if check.saying:
            self.say(tr(check.saying))

        def done(result):
            if not self.alive:
                return
            if isinstance(result, Exception):
                self.say(tr("{failure}: {error}", failure=tr(check.failure), error=result))
                return
            rows, message = check.report(result)
            self.view.checks[name] = result
            self.checks.show_results(name, rows)
            self.say(message)
            self.view.update()

        if check.background:
            self.background.run(lambda: check.run(self.session, self.editor), done)
            return
        try:
            result = check.run(self.session, self.editor)
        except Exception as exc:
            result = exc
        done(result)

    # closing
    def confirm_close(self) -> bool:
        """Offer to save unsaved edits; False when the user cancels (or saving fails)."""
        if not self.session.undo.dirty or self.session.readonly:
            return True
        keys = QMessageBox.StandardButton
        box = QMessageBox(
            QMessageBox.Icon.Question, tr("Unsaved edits"), tr("Save the edits before closing?"),
            keys.Save | keys.Discard | keys.Cancel, self.shell,
        )  # fmt: skip
        for key, text in ((keys.Save, tr("Save")), (keys.Discard, tr("Discard")), (keys.Cancel, tr("Cancel"))):
            box.button(key).setText(text)
        answer = keys(box.exec())
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return answer != QMessageBox.StandardButton.Save or self.save()

    def close(self) -> None:
        """Let go of the view; the panels are the workbench's to drop."""
        self.alive = False
        for signal, slot in self._links:
            signal.disconnect(slot)
        self.view.unload()

    # events
    def _tool(self, name: str) -> None:
        self.props.show_entry(self.view.selected)
        self.shell.sync()
        if name == "paint":
            self.shell.raise_dock("Material Gallery")

    def _selected(self, e) -> None:
        self.props.show_entry(e)
        self.shell.sync()

    def _changed(self) -> None:
        self.history.fill()
        self.props.show_entry(self.view.selected)
        self.shell.sync()
        self.view.update()

    def _structure(self, change) -> None:
        if change.structure or change.forest or any(not e.visible or e.added is not None for e in change.entries):
            self.objects.refresh()
        self.view.update()

    # side data
    def _load_side_data(self) -> None:
        """The materials and the library, read in the background; what cannot be read is said, not hidden."""
        editor, tools = self.editor, self.modes["tools"]

        def side():
            own = editor.materials()
            problems, vanilla, library = [], [], {}
            if "paint" in tools:
                try:
                    vanilla = editor.vanilla_materials()
                except Exception as exc:
                    problems.append(tr("no 117 ground materials: {error}", error=f"{type(exc).__name__}: {exc}"))
            if "place" in tools:
                try:
                    library = editor.library()
                except Exception as exc:
                    problems.append(tr("no model library: {error}", error=f"{type(exc).__name__}: {exc}"))
            return own, vanilla, library, problems

        def done(result):
            if not self.alive:
                return
            if isinstance(result, Exception):
                self.say(tr("no ground materials: {error}", error=result))
                return
            self._show_side_data(*result)

        self.background.run(side, done)

    def _show_side_data(self, own: list, vanilla: list, library: dict, problems: list[str]) -> None:
        """What ``_load_side_data`` read: the problems into the Log, the materials into the session and the gallery,
        the library into its dock."""
        for p in problems:
            self.shell.log.appendPlainText(p)
        if problems:
            self.say(problems[0])
        self.own_materials = own
        for k, v in self.session.edits.get("materials", {}).items():  # 117 materials picked before
            m = next((x for x in vanilla if x["file"] == v.get("file")), None)
            if m is not None:
                self.session.paint_materials.setdefault(int(k), {**m, "id": int(k)})
        self.session.set_own_materials(own)
        self.gallery.fill(own, vanilla)
        self.library.set_data(library)
        self._swatches(own + vanilla)

    def _swatches(self, materials: list[dict]) -> None:
        """The gallery's pictures, handed over in batches as they are read: the first opening of an island converts
        each texture once, which takes a while for a game's whole set."""
        editor, batch = self.editor, 12

        def load():
            from PIL import Image

            out = {}
            for n, m in enumerate(materials, 1):
                try:
                    img = Image.open(io.BytesIO(editor.texture_png(m["diffuse"], opaque=True))).convert("RGB")
                    out[m["diffuse"]] = img.resize((44, 44)).tobytes()
                except Exception:  # a texture that does not load keeps the plain entry (the Checks tab says why)
                    pass
                if n % batch == 0 and out:
                    self.background.done.emit(show, out)
                    out = {}
            return out

        def show(result):
            if not self.alive or isinstance(result, Exception):
                return
            for item in self.gallery.items():
                raw = result.get(item.data(Qt.ItemDataRole.UserRole)[1].get("diffuse"))
                if raw:
                    item.setIcon(
                        QIcon(QPixmap.fromImage(QImage(raw, 44, 44, 44 * 3, QImage.Format.Format_RGB888).copy()))
                    )

        self.background.run(load, show)
