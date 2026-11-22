"""The island open in the workbench (``workbench``): its edit session (``editsession``), the panels that show it
- Material Gallery, Object Groups (the model library), the objects on the island, the selection's properties,
layers, check results and history - and what the ribbon's buttons do to it. Plan and use:
``docs/island-editor.md``.
"""

from __future__ import annotations

import io
import math

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMessageBox

from ...infra.i18n import N_, tr
from ...render import nativelook, quality
from ...suite import features
from .. import islandrules, terraintools
from ..editsession import CEditSession, game_of
from .desktoppanels import CLibraryPanel, CMaterialGallery, CObjectsPanel, CPropertiesPanel
from .desktopplace import CPlaceablesPanel
from .desktopslotstab import SLOT_GAMES
from .desktopview import BRUSHES, TOOLS
from .minimappanel import CMinimapPanel

LOOKS = (
    ("game_terrain", N_("islands.game_terrain"), N_("islands.ground_game_draws_near_camera")),
    ("native", N_("common.games_own_shaders"), N_("islands.terrain_games_own_shaders_game")),
    ("look2070", N_("islands.2070_look"), N_("islands.islands_edited_2070_drawn_anno")),
    ("underwater", N_("islands.underwater_look"), N_("islands.2070_look_seen_under_water")),
    ("caustics", N_("islands.caustics"), N_("islands.underwater_looks_light_patterns_sea")),
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
CHECK_NAMES = {
    "rules": N_("islands.rules"),
    "slots": N_("islands.slots"),
    "falls": N_("islands.falls"),
}  # the checks of the Grid tab
OTHER_NO_TOOLS = {"water", "forest", "place", "blocker", "spray", "active", "surf"}  # an island of another game


def look_blocker(key: str) -> str:
    """Why the look ``key`` cannot be switched on on this system ("" when it can): the game's own shaders are
    Direct3D shaders (docs/linux.md "Phase 3 done")."""
    return tr("common.game_look_windows_only") if key == "native" and not nativelook.direct3d_here() else ""


def shipped_state(backend: str) -> tuple[bool, bool, str]:
    """View > Rendering > The game's shipped shaders for a view on ``backend``: (enabled, checked, why it cannot be
    on, "" when it can). Off draws 117's game look the earlier way, every phase (``render.nativegpu.want``)."""
    from ...render import nativegpu

    why = nativegpu.blocker(backend) if nativelook.direct3d_here() else tr("common.game_look_windows_only")
    if why:
        return False, False, tr("islands.shipped_shaders_cannot", reason=why[:300])
    return True, nativegpu.wanted(), ""


def set_shipped(shell, is_on: bool) -> None:
    """The switch kept in the settings and applied to the open view: its game look made again the other way."""
    from ...render import nativegpu

    shell.m_app.set_settings({"native_shaders": bool(is_on)})
    nativegpu.want(is_on)
    renderer = getattr(shell.m_view, "m_renderer", None)
    if renderer is not None:
        renderer.remake_game()
    shell.m_view.update()
    shell.sync()


def game_tools(games: tuple[str, ...] | None) -> set[str]:
    """The tools any island of these games (None: all) offers."""
    kinds = games or tuple(NO_TOOLS)
    return set().union(*(set(TOOLS) - NO_TOOLS.get(k, OTHER_NO_TOOLS) for k in kinds))


def modes(island_info: dict, island: dict) -> dict:
    """What an opened island (``editorapp.CApp.island``) may do: its tools and how it is saved and built."""
    kind = island_info.get("kind")
    tools = set(TOOLS) - NO_TOOLS.get(kind, OTHER_NO_TOOLS)
    if not island_info.get("editable", True):
        tools = {"select"}
    game = island.get("game_name") or ("Anno 2070" if island.get("mode") == "2070" else "Anno 117")
    return {
        "tools": tools,
        "game": game,
        "buildable": island_info.get("buildable", kind in ("built", "anno117", "anno2070")),
    }


class CChecksPanel(QListWidget):
    """The checks' results (the view tab "Checks"); a click flies to the place."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        self._m_rows: dict[str, list] = {}
        self.itemClicked.connect(self._result)

    def show_results(self, check: str, rows: list[tuple[str, list | None]]) -> None:
        """One check's lines (the other check's stay): (text, Anno position to fly to)."""
        self._m_rows[check] = rows
        self.clear()
        for text, target in [
            (f"{tr(CHECK_NAMES.get(check_name, check_name))}: {line}", target)
            for check_name, check_rows in self._m_rows.items()
            for line, target in check_rows
        ]:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.addItem(item)

    def _result(self, item) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target:
            self.m_doc.m_shell.show_page("Viewport")
            self.m_doc.m_view.go_to(target[0], target[2], 18.0)


class CHistoryPanel(QListWidget):
    """The undo stack: a click goes back (or forward) to that change."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        self.itemClicked.connect(self._jump)

    def fill(self) -> None:
        stack = self.m_doc.m_session.m_undo
        self.clear()
        self.addItem(tr("common.as_opened"))
        for k, label in enumerate(stack.labels()):
            self.addItem(f"{label}" + ("  · " + tr("common.saved") if k + 1 == stack.m_clean else ""))
        undone, text = self.palette().placeholderText(), self.palette().text()
        for k in range(self.count()):
            self.item(k).setForeground(undone if k > stack.m_index else text)
        self.setCurrentRow(stack.m_index)

    def _jump(self, item) -> None:
        stack, want = self.m_doc.m_session.m_undo, self.row(item)
        while stack.m_index > want and stack.undo():
            pass
        while stack.m_index < want and stack.redo():
            pass


def open_session(editor, island_info: dict) -> tuple:
    """(editor, island, session): the opened island's data and its edit session, made off the window's thread when
    the island is large (``workbench.CMainWindow._load``)."""
    from . import desktopgame  # noqa: F401  # imported here, not by the window's thread when the look is chosen (1.4 s)

    island = editor.island_data()
    island["readonly"] = not island_info.get("editable", True)  # the app decides (the island's JSON is cached)
    session = CEditSession(island, editor.edits(), is_readonly=not island_info.get("editable", True))
    session.finish_loading()
    prime = getattr(editor, "slot_assets", None)
    if prime is not None and game_of(island, kind=str(island_info.get("kind", ""))) in SLOT_GAMES:
        try:  # the Slots list reads the game's asset files: here, not on the window's thread
            prime()
        except Exception:  # the list says why itself when it asks again
            pass
    return editor, island, session


class CIslandDoc(QObject):
    """One island open in the workbench: its edit session and panels, and what the ribbon's buttons do to it.
    The workbench's view is loaded with it and emptied again by ``close``. ``info``: what the app opened
    (``editorapp.CApp.island``); ``opened``: how it was asked for, so the same request only shows it again."""

    def __init__(self, shell, title: str, island_info: dict, opened: tuple | None = None, prepared=None) -> None:
        super().__init__(shell)
        self.m_shell, self.m_info, self.m_title, self.m_opened = shell, island_info, title, opened
        self.m_view, self.m_background = shell.m_view, shell.m_background
        self.m_alive = True
        self.m_editor, island, self.m_session = prepared or open_session(shell.m_app.m_editor, island_info)
        self.m_modes = modes(island_info, island)
        self.m_own_materials: list[dict] = []
        self.m_view.load(self.m_session, self.m_editor)
        self.apply_quality()
        self.apply_game_look()
        self.m_view.m_allowed = self.m_modes["tools"]
        self.m_gallery = CMaterialGallery(self)
        self.m_library = CLibraryPanel(self)
        self.m_buildings = CPlaceablesPanel(self)
        self.m_objects = CObjectsPanel(self)
        self.m_props = CPropertiesPanel(self)
        self.m_checks = CChecksPanel(self)
        self.m_history = CHistoryPanel(self)
        self.m_minimap = CMinimapPanel(self.m_session, self.m_background, self.say)
        self._m_links = [
            (self.m_view.selectionChanged, self._selected),
            (self.m_view.waterChanged, self.m_props.show_water),
            (self.m_view.toolChanged, self._tool),
        ]
        for signal, slot in self._m_links:
            signal.connect(slot)
        self.m_session.m_undo.m_listeners.append(self._changed)
        self.m_session.m_listeners.append(self._structure)
        self.m_objects.fill()
        self.m_history.fill()
        counts = island.get("objects", []), island.get("props", []), (island.get("forest") or {}).get("tiles", [])
        self.say(
            tr(
                "islands.objects_props_forest_tiles",
                objects=len(counts[0]),
                props=len(counts[1]),
                tiles=len(counts[2]),
            )
        )
        self.m_side_loading = True  # the materials and the model list are read in the background (``_load_side_data``)
        self._load_side_data()
        self.report_notes()

    def loading_text(self) -> str:
        """What the island still loads in the background, for the status bar ("" when nothing): the ground materials
        and the model list, then the models and textures the view draws."""
        parts = [tr("islands.loading_materials_models_list")] if self.m_side_loading else []
        renderer = getattr(self.m_view, "m_renderer", None)
        left = renderer.busy() if renderer is not None else 0
        if left:
            parts.append(tr("islands.loading_n_models_textures", n=left))
        return ", ".join(parts)

    def report_notes(self) -> None:
        """What the island's reader left out (``IslandEditor.notes``): into the Log, the first in the status bar."""
        notes = getattr(self.m_editor, "m_notes", None) or []
        for note in notes:
            self.m_shell.m_log.appendPlainText(note)
        if notes:
            more = " " + tr("islands.plus_more_log", n=len(notes) - 1) if len(notes) > 1 else ""
            self.say(notes[0] + more)

    # what the workbench asks
    def state(self) -> dict:
        """What the ribbon needs to know (``desktopribbon.available``)."""
        session, view = self.m_session, self.m_view
        editable = not session.m_readonly
        return {
            "island": True,
            "editable": editable,
            "tools": set(self.m_modes["tools"]),
            "undo": session.m_undo.m_index > 0,
            "redo": session.m_undo.m_index < len(session.m_undo.m_commands),
            "buildable": self.m_modes["buildable"] and editable,
            "selection": editable and view.m_selected is not None and view.m_selected.alive,
            "river_point": editable and view.river_selected(),
            "slots": not (session.m_island.get("mode") == "2070" or session.m_island.get("game")),
            "brush": editable and bool(self.m_modes["tools"] & set(BRUSHES)),
            "kind117": editable and self.m_info.get("kind") == "anno117",
        }

    def game_key(self) -> str:
        """The game the island follows ("anno117", ...): whose rendering quality the view draws at."""
        return game_of(self.m_session.m_island, kind=str(self.m_info.get("kind", "")))

    def apply_quality(self) -> None:
        """The view at the rendering quality the settings keep for the island's game (``render.quality``: 117's and
        1800's; the older games at their best)."""
        game = self.game_key()
        stored = getattr(self.m_shell.m_app.m_settings, "quality", {}).get(game)
        self.m_view.m_opts.quality = quality.resolve(game, stored) if game in quality.GAMES else None
        self.m_view.update()

    def apply_game_look(self) -> None:
        """An island of a game with a look of its own shaders other than 117's (``desktopgame.LOOKS``: 1800, 2205)
        opens in it where the view draws on Direct3D (12, the default, or 11), 1800's with the game's terrain (the
        rule for 117 of 2026-11-13: the editor's look is the fallback; island-1800-2205-view.md 4)."""
        from .desktopgame import LOOKS as GAME_LOOKS

        game = self.m_session.m_island.get("game")
        if game in GAME_LOOKS and getattr(self.m_view, "m_backend", "gl") in ("d3d12", "d3d11"):
            self.m_view.m_opts.native = True
            self.m_view.m_opts.game_terrain = game in GAME_TERRAIN
            self.m_view.update()

    def set_layer(self, key: str, is_on: bool) -> None:
        """Draw (or leave out) one of the view's layers (``desktoprender.LAYERS``): the ribbon's Layers group."""
        self.m_view.m_opts.layers[key] = is_on
        self.m_view.update()

    def look_state(self) -> dict[str, tuple[bool, bool]]:
        """Per look and ``shadows``: (enabled, checked). 117's rules are for 117's islands, built or vanilla, and the
        game's own shaders also for the games with a look of theirs (with the game's terrain where the island has
        it); the 2070 look for islands edited for 2070."""
        from .desktopgame import LOOKS as GAME_LOOKS

        island, options = self.m_session.m_island, self.m_view.m_opts
        game = island.get("game")
        out = {"shadows": (True, options.shadows), "particles": (True, options.particles)}
        out["particles_frozen"] = (options.particles, options.particle_time is not None)
        for key, _label, _tip in LOOKS:
            is_enabled = (key in LOOKS_2070) == (island.get("mode") == "2070") and not game
            is_enabled = (
                is_enabled
                or (key == "native" and game in GAME_LOOKS)
                or (key == "game_terrain" and game in GAME_TERRAIN)
            )
            is_enabled = is_enabled and not look_blocker(key)
            out[key] = (is_enabled and (options.underwater or key != "caustics"), getattr(options, key))
        return out

    def offers_2070_looks(self) -> bool:
        """Whether the view offers the 2070 looks at all (``LOOKS_2070``): only for an island of 2070;
        ``look_state`` says whether they can be switched on."""
        return game_of(self.m_session.m_island) == "anno2070"

    def window_title(self) -> str:
        dirty = " *" if self.m_session.m_undo.dirty and not self.m_session.m_readonly else ""
        return f"{self.m_title}{dirty} · {self.m_modes['game']}"

    def badge(self) -> str:
        options = self.m_view.m_opts
        parts = [
            label
            for key, label in (
                ("game_terrain", tr("islands.terrain_lower")),
                ("native", tr("islands.its_shaders")),
            )
            if getattr(options, key)
        ]  # fmt: skip
        look = (
            tr("islands.2070_underwater_look")
            if options.underwater
            else tr("islands.2070_look")
            if options.look2070
            else tr("islands.games_rules_parts", parts=", ".join(parts))
            if parts and self.m_session.m_island.get("game")
            else tr("islands.117_rules_parts", parts=", ".join(parts))
            if parts
            else tr("islands.editor_look")
        )
        game = self.m_modes["game"]
        head = (
            tr("islands.island_view_only", game=game)
            if self.m_session.m_readonly
            else tr("islands.game_island", game=game)
        )
        return f"{head} · {look}"

    # ribbon actions
    def say(self, text: str) -> None:
        self.m_last_said = text  # what the status bar goes back to when the progress line ends
        self.m_shell.say(text)

    def set_look(self, key: str, is_on: bool) -> None:
        options = self.m_view.m_opts
        if key == "particles_frozen":  # hold the effects at the moment of the last frame
            renderer = self.m_view.m_renderer
            options.particle_time = (renderer.m_particles.m_clock if renderer is not None else 0.0) if is_on else None
            self.m_view.update()
            self.m_shell.sync()
            return
        if is_on and key == "underwater":  # it is that look, seen from below
            options.look2070 = True
        if is_on and key == "native":
            options.look2070 = options.underwater = options.caustics = False
        if not is_on and key in ("look2070", "underwater"):
            options.caustics = False
            if key == "look2070":
                options.underwater = False
        setattr(options, key, is_on)
        self.m_view.update()
        self.m_shell.sync()

    def set_tool(self, action: str) -> None:
        """A tool button (``tool_raise``, ``tool_plant``, ``tool_paint``, ...): the tool, and its brush mode."""
        name, brush = action.removeprefix("tool_"), self.m_view.m_brush
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
        self.m_view.set_tool(name)
        self.m_shell.sync()

    def set_gizmo(self, action: str) -> None:
        mode, tilt = {
            "gizmo_move": ("translate", False),
            "gizmo_turn": ("rotate", False),
            "gizmo_tilt": ("rotate", True),
            "gizmo_scale": ("scale", False),
        }[action]
        self.m_view.set_gizmo(mode, tilt)
        self.m_shell.sync()

    def set_brush(self, key: str, value) -> None:
        self.m_view.m_brush[key] = value

    def set_target(self, index: int) -> None:
        self.m_session.set_target("new" if index else "replace")

    def goto(self, text: str) -> None:
        try:
            values = [float(token) for token in text.replace(",", " ").split()]
        except ValueError:
            return
        if len(values) >= 2 and all(
            math.isfinite(x) for x in values
        ):  # ("nan" and "inf" parse as numbers: no camera there)
            self.m_view.go_to(
                values[0], values[1], values[2] if len(values) > 2 else 25.0, values[3] if len(values) > 3 else 0.0
            )

    def deselect(self) -> None:
        self.m_view.select(None)
        self.m_view.select_water(None)

    def focus(self) -> None:
        if self.m_view.m_selected is not None:
            self.m_view.focus_on([self.m_view.m_selected])

    def undo(self) -> None:
        label = self.m_session.m_undo.undo()
        self.say(tr("common.undone_label", label=label) if label else tr("islands.nothing_to_undo"))
        if self.m_view.m_selected is not None and not self.m_view.m_selected.alive:
            self.m_view.select(None)

    def redo(self) -> None:
        label = self.m_session.m_undo.redo()
        self.say(tr("common.redone_label", label=label) if label else tr("islands.nothing_to_redo"))

    def save(self) -> bool:
        if self.m_session.m_readonly:
            return False
        try:
            self.m_session.save(self.m_editor)
        except Exception as exc:
            QMessageBox.warning(self.m_shell, tr("common.save"), tr("islands.could_not_save", error=exc))
            return False
        self.say(tr("common.saved_path", path=self.m_editor.m_edits_path))
        return True

    def build(self) -> None:
        if self.m_session.m_undo.dirty and not self.save():
            return
        self.m_shell.build_opened(self.m_info)

    def run_check(self, name: str) -> None:
        """Run the check ``name`` (the editor's own ``rules``, or one registered at ``features.CHECKS``) and list what
        it found in the Checks tab."""
        check = islandrules.RULES if name == "rules" else features.extension(features.CHECKS, name)
        if check is None:
            self.say(tr("islands.check_not_available_edition", name=name))
            return
        if check.saying:
            self.say(tr(check.saying))

        def done(result):
            if not self.m_alive:
                return
            if isinstance(result, Exception):
                self.say(tr("islands.failure_error", failure=tr(check.failure), error=result))
                return
            rows, message = check.report(result)
            self.m_view.m_checks[name] = result
            self.m_checks.show_results(name, rows)
            self.say(message)
            self.m_view.update()

        if check.background:
            self.m_background.run(lambda: check.run(self.m_session, self.m_editor), done)
            return
        try:
            result = check.run(self.m_session, self.m_editor)
        except Exception as exc:
            result = exc
        done(result)

    # closing
    def confirm_close(self) -> bool:
        """Offer to save unsaved edits; False when the user cancels (or saving fails)."""
        if not self.m_session.m_undo.dirty or self.m_session.m_readonly:
            return True
        keys = QMessageBox.StandardButton
        box = QMessageBox(
            QMessageBox.Icon.Question, tr("islands.unsaved_edits"), tr("islands.save_edits_before_closing"),
            keys.Save | keys.Discard | keys.Cancel, self.m_shell,
        )  # fmt: skip
        for key, text in (
            (keys.Save, tr("common.save")),
            (keys.Discard, tr("islands.discard")),
            (keys.Cancel, tr("common.cancel")),
        ):
            box.button(key).setText(text)
        answer = keys(box.exec())
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return answer != QMessageBox.StandardButton.Save or self.save()

    def close(self) -> None:
        """Let go of the view and of the window (which kept every closed island's session alive as its child); the
        panels are the workbench's to drop."""
        self.m_alive = False
        for signal, slot in self._m_links:
            signal.disconnect(slot)
        self._m_links = []
        self.m_view.unload()
        self.setParent(None)
        self.deleteLater()

    # events
    def _tool(self, name: str) -> None:
        self.m_props.show_entry(self.m_view.m_selected)
        self.m_shell.sync()
        if name == "paint":
            self.m_shell.raise_dock("Material Gallery")

    def _selected(self, entry) -> None:
        self.m_props.show_entry(entry)
        self.m_shell.sync()

    def _changed(self) -> None:
        self.m_history.fill()
        self.m_props.show_entry(self.m_view.m_selected)
        self.m_shell.sync()
        self.m_view.update()

    def _structure(self, change) -> None:
        if (
            change.structure
            or change.forest
            or any(not entry.visible or entry.added is not None for entry in change.entries)
        ):
            self.m_objects.refresh()
        self.m_view.update()

    # side data
    def _load_side_data(self) -> None:
        """The materials and the library, read in the background; what cannot be read is said, not hidden."""
        editor, tools = self.m_editor, self.m_modes["tools"]

        def side():
            own = editor.materials()
            problems, vanilla, library = [], [], {}
            if "paint" in tools:
                try:
                    vanilla = editor.vanilla_materials()
                except Exception as exc:
                    problems.append(tr("islands.no_117_ground_materials", error=f"{type(exc).__name__}: {exc}"))
            if "place" in tools:
                try:
                    library = editor.library()
                except Exception as exc:
                    problems.append(tr("islands.no_model_library", error=f"{type(exc).__name__}: {exc}"))
            return own, vanilla, library, problems

        def done(result):
            self.m_side_loading = False
            if not self.m_alive:
                return
            if isinstance(result, Exception):
                self.say(tr("islands.no_ground_materials", error=result))
                return
            self._show_side_data(*result)

        self.m_background.run(side, done)

    def _show_side_data(self, own: list, vanilla: list, library: dict, problems: list[str]) -> None:
        """What ``_load_side_data`` read: the problems into the Log, the materials into the session and the gallery,
        the library into its dock."""
        for problem in problems:
            self.m_shell.m_log.appendPlainText(problem)
        if problems:
            self.say(problems[0])
        self.m_own_materials = own
        for material_id, picked in self.m_session.m_edits.get("materials", {}).items():  # 117 materials picked before
            material = next((candidate for candidate in vanilla if candidate["file"] == picked.get("file")), None)
            if material is not None:
                self.m_session.m_paint_materials.setdefault(int(material_id), {**material, "id": int(material_id)})
        self.m_session.set_own_materials(own)
        self.m_gallery.fill(own, vanilla)
        self.m_library.set_data(library)
        self._swatches(own + vanilla)

    def _swatches(self, materials: list[dict]) -> None:
        """The gallery's pictures, handed over in batches as they are read: the first opening of an island converts
        each texture once, which takes a while for a game's whole set."""
        editor, batch = self.m_editor, 12

        def load():
            from PIL import Image

            out = {}
            for number, material in enumerate(materials, 1):
                try:
                    image = Image.open(io.BytesIO(editor.texture_png(material["diffuse"], is_opaque=True))).convert(
                        "RGB"
                    )
                    out[material["diffuse"]] = image.resize((44, 44)).tobytes()
                except Exception:  # a texture that does not load keeps the plain entry (the Checks tab says why)
                    pass
                if number % batch == 0 and out:
                    self.m_background.done.emit(show, out)
                    out = {}
            return out

        def show(result):
            if not self.m_alive or isinstance(result, Exception):
                return
            for item in self.m_gallery.items():
                raw = result.get(item.data(Qt.ItemDataRole.UserRole)[1].get("diffuse"))
                if raw:
                    item.setIcon(
                        QIcon(QPixmap.fromImage(QImage(raw, 44, 44, 44 * 3, QImage.Format.Format_RGB888).copy()))
                    )

        self.m_background.run(load, show)
