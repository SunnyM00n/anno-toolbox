"""The map editor's window (Qt): a game's maps on the left, the open map from above in the middle, the selection's
and the map's fields and the checks on the right; every edit on the map's undo stack, kept in the project or written
as a mod folder. Reading (a map, an island added or swapped, another map's complex, an island's slots), growing and
writing run off the window's thread.

``MapWindow(settings, settings_path)`` is the suite's Maps app; ``open_game`` and ``open_map`` are the entry points.
docs/map-editor.md is the user's guide.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import gameopen, links, widgets
from ..suite import project as suite_project
from ..suite.background import Background
from . import grow, mapdoc, mapmod, placing, pool, projectpart, variants
from .mapdialogs import (
    DetailsDialog,
    GrowDialog,
    IslandDialog,
    MirrorDialog,
    NewMapDialog,
    ReportDialog,
    SlotDialog,
    VariantsDialog,
)
from .mappanel import MapPanel
from .mapview import MapView
from .template import KIND_FIXED, KIND_SLOT, KIND_START, new_element

APP = "maps"
TITLE = N_("Map editor")
GAMES = {key: gametree.GAMES[key] for key in ("anno117", "anno1800", "anno2070", "anno1404", "anno1701", "anno2205")}
ROLE = Qt.ItemDataRole.UserRole
VARIANTS_KEPT = "#levels"  # the project files the three difficulties of a map made, kept apart from the map's own


class MapWindow(QMainWindow):
    def __init__(self, settings, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.key, self.tree, self.maps = "", None, None
        self.paths: list[str] = []
        self.doc: mapdoc.MapDoc | None = None
        self.saved: bytes | None = None  # the map as last kept (None: never)
        self.kept: dict[str, set[str]] = {}  # per map: the project files its last save made
        self.choice = mapmod.Choice()
        self.last_variant_findings: list[str] = []  # what the checks found in the last three difficulties
        self.filter = QLineEdit(placeholderText=tr("filter the maps"))
        self.list = QListWidget()
        self.view = MapView()
        self.panel = MapPanel(self._apply_element, self._apply_map, self._details)
        self._build()
        self.setWindowTitle(tr(TITLE))
        self.statusBar().showMessage(tr("File > Open game, then pick a map on the left."))

    # layout and menus
    def _build(self) -> None:
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.addWidget(self.filter)
        lay.addWidget(self.list)
        split = QSplitter()
        for w, stretch in ((left, 0), (self.view, 1), (self.panel, 0)):
            split.addWidget(w)
            split.setStretchFactor(split.count() - 1, stretch)
        split.setSizes([300, 900, 340])
        self.setCentralWidget(split)
        self.filter.textChanged.connect(self._fill_list)
        self.list.itemActivated.connect(lambda item: self.open_map(item.data(ROLE)))
        self.view.edited.connect(self._edited)
        self.view.selection_changed.connect(self._selected)
        self.panel.findings.itemDoubleClicked.connect(lambda item: self.view.select(list(item.data(ROLE) or ())))
        self._menus()

    def _menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu(tr("&File"))
        games = file.addMenu(tr("Open game"))
        for key, title in GAMES.items():
            games.addAction(title, lambda k=key: self.open_game(k))  # i18n: ok
        file.addAction(tr("&Save to the project"), self.save, QKeySequence.StandardKey.Save)
        file.addAction(tr("Write as a new map..."), self.save_as_new)
        file.addAction(tr("Write easy, medium and hard..."), self.write_variants)
        file.addAction(tr("Write as a mod folder..."), self.write_mod_folder)
        edit, _undo, _redo = widgets.edit_menu(self, self.undo, self.redo)
        edit.addAction(tr("Rotate a quarter turn"), self.view.rotate_selected, QKeySequence("R"))
        edit.addAction(tr("Remove"), self.view.remove_selected, QKeySequence.StandardKey.Delete)
        edit.addAction(tr("Duplicate"), self._duplicate, QKeySequence("Ctrl+D"))
        edit.addAction(tr("Select the complex"), self._select_complex)
        edit.addAction(tr("Mine slots and fertilities..."), self._details)
        add = bar.addMenu(tr("&Add"))
        add.addAction(tr("A random slot..."), self._add_slot)
        add.addAction(tr("A start position"), self._add_start)
        add.addAction(tr("An island of the game..."), self._add_island)
        add.addAction(tr("An island file on disk..."), self._add_island_file)
        add.addAction(tr("A complex of another map..."), self._add_complex)
        mapm = bar.addMenu(tr("&Map"))
        mapm.addAction(tr("Grow the map..."), self._grow)
        mapm.addAction(tr("Show the last growth's report"), self._show_report)
        mapm.addAction(tr("Mirrored copies..."), self._mirror)
        mapm.addAction(tr("Fit the map"), self.view.fit, QKeySequence("Home"))
        mapm.addAction(tr("Open the island in the Islands app"), self._open_in_islands)

    # games and maps
    def project(self) -> suite_project.Project:
        return suite_project.current(self.settings)

    def open_game(self, key: str, then: str = "", wait: bool = False) -> None:
        """Open an installed game (its folder from the settings) off the window's thread; ``then``: a map to open."""

        def prepare(tree):
            mine = projectpart.EDITS.folder(self.project(), key)
            tree = tree.over(mine, "project") if mine.is_dir() else tree
            return tree, pool.GameMaps.read(tree, key), mapdoc.map_paths(tree, key)

        def opened(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("{game} did not open: {error}", game=GAMES.get(key, key), error=result))
                return
            self.tree, self.maps, self.paths = result
            self.key = key
            self.settings.apps.setdefault(APP, {})["game"] = key
            self._fill_list()
            self.statusBar().showMessage(tr("{name}: {count} maps", name=GAMES.get(key, key), count=len(self.paths)))
            if then:
                self.open_map(then, wait=wait)

        gameopen.open_installed(self, self.settings, key, self.background, then=opened, prepare=prepare, wait=wait)

    def _fill_list(self) -> None:
        self.list.clear()
        words = self.filter.text().lower().split()
        for path in self.paths:
            if all(w in path for w in words):
                item = QListWidgetItem(path.rsplit("/", 1)[-1])
                item.setToolTip(path)
                item.setData(ROLE, path)
                self.list.addItem(item)

    def open_map(self, path: str, wait: bool = False) -> None:
        if self.tree is None or not self._may_leave():
            return
        if self.key in mapdoc.SESSION_GAMES:
            shown = links.follow(links.OPEN_ISLAND, self.key, path) if links.available(links.OPEN_ISLAND) else False
            self.statusBar().showMessage(
                tr("Anno 2205's sessions are one baked map each: nothing to place; the Islands app edits them")
                + (tr(" (opened there)") if shown else "")
            )
            return
        tree, key, maps = self.tree, self.key, self.maps

        def work():
            doc = mapdoc.open_map(tree, key, path, maps)  # reads every island's grid once, here
            return (
                doc,
                doc.check(),
                self._pictures(doc, [e.map_file_path for e in doc.elements if e.kind == KIND_FIXED]),
            )

        self.statusBar().showMessage(tr("Opening {path}...", path=path))
        self.background.run(work, self._opened, wait=wait)

    def _opened(self, result) -> None:
        if isinstance(result, Exception):
            self.statusBar().showMessage(tr("the map did not open: {error}", error=result))
            return
        doc, findings, pictures = result
        self.doc, self.saved, self.choice = doc, None, mapmod.Choice()
        self.view.pictures.clear()
        self.view.set_doc(doc)
        for path, data in pictures.items():
            self.view.set_picture(path, data)
        self.panel.show_map(doc)
        self.panel.show_element(None)
        self._show_findings(findings)
        self.setWindowTitle(f"{doc.path.rsplit('/', 1)[-1]} · {tr(TITLE)}")
        counts = {k: sum(1 for e in doc.elements if e.kind == k) for k in (KIND_FIXED, KIND_SLOT, KIND_START)}
        self.statusBar().showMessage(
            tr(
                "{path}: {fixed} fixed islands, {slots} random slots, {starts} start positions",
                path=doc.path,
                fixed=counts[KIND_FIXED],
                slots=counts[KIND_SLOT],
                starts=counts[KIND_START],
            )
        )

    @staticmethod
    def _pictures(doc, paths: list[str]) -> dict[str, bytes]:
        """The map pictures of island files, with their grids read into the document's cache on the way."""
        out = {}
        for path in set(paths):
            doc.info(path)
            pic = doc.picture(new_element(KIND_FIXED, 0, 0, map_file_path=path))
            if pic:
                out[path] = doc.tree.read(pic)
        return out

    def _with_islands(self, paths: list[str], then) -> None:
        """Read island files (grids, pictures) off the window's thread, then run ``then()`` on it."""
        doc = self.doc

        def done(result) -> None:
            if isinstance(result, dict):
                for path, data in result.items():
                    self.view.set_picture(path, data)
            if self.doc is doc:
                then()

        self.background.run(lambda: self._pictures(doc, paths), done)

    # edits
    def _edited(self) -> None:
        if self.doc is None:
            return
        self._show_findings(self.doc.check())
        self._selected(self.view.selected)
        self.panel.show_map(self.doc)
        self.view.update()

    def _selected(self, indexes: list) -> None:
        e = self.doc.elements[indexes[0]] if self.doc is not None and len(indexes) == 1 else None
        self.panel.show_element(e, self.doc.size_index(e) if e is not None else None)

    def _show_findings(self, findings) -> None:
        self.panel.findings.clear()
        self.view.flagged = {i for f in findings for i in f.elements}
        for f in findings:
            item = QListWidgetItem(f"{tr(f.severity)}: {f.text}")
            item.setData(ROLE, list(f.elements))
            self.panel.findings.addItem(item)
        if not findings:
            self.panel.findings.addItem(tr("no findings"))

    def _one(self) -> int | None:
        return self.view.selected[0] if self.doc is not None and len(self.view.selected) == 1 else None

    def _apply_element(self, values: dict, wait: bool = False) -> None:
        index = self._one()
        if index is None:
            return
        e = self.doc.elements[index]
        if e.kind != KIND_SLOT:  # the size box shows a fixed island's own class; only a slot stores one
            values = {k: v for k, v in values.items() if k != "size"}
        if e.kind == KIND_FIXED and values.get("map_file_path", e.map_file_path) != e.map_file_path:
            path = values["map_file_path"]
            self._with_islands([path], lambda: self._set_values(index, values))  # its grid read off the thread
            if wait:
                self.background.join()
            return
        self._set_values(index, values)

    def _set_values(self, index: int, values: dict) -> None:
        if self.doc is not None and index < len(self.doc.elements):
            self.doc.set_values(index, values)
            self._edited()

    def _apply_map(self, values: dict) -> None:
        if self.doc is None:
            return
        (w0, h0), (w1, h1) = self.doc.size, values["size"]
        if (w1, h1) != (w0, h0):
            if w1 < w0 or h1 < h0:
                self.statusBar().showMessage(tr("a map only grows; to shrink it, start from a smaller template"))
                return
            self._run_grow((0, 0, w1 - w0, h1 - h0), True)
            return
        self.doc.set_area(playable=values["playable"], initial=values["initial"])
        self._edited()

    def undo(self) -> None:
        if self.doc is not None and self.doc.undo():
            self.view.select([])
            self._edited()

    def redo(self) -> None:
        if self.doc is not None and self.doc.redo():
            self.view.select([])
            self._edited()

    def _centre(self) -> tuple[float, float]:
        return self.view.to_map(self.view.rect().center().toPointF())

    def _add(self, element) -> None:
        index = self.doc.add(element)
        self.view.select([index])
        self._edited()

    def _add_slot(self) -> None:
        if self.doc is None or not (self.doc.is_template or self.doc.record.random):
            self.statusBar().showMessage(tr("random slots belong to map templates and random-map templates"))
            return
        dialog = SlotDialog(self.doc.size_names(), self)
        if dialog.exec():
            size, kind = dialog.values()
            self._add(new_element(KIND_SLOT, *self._centre(), size=size, type=kind))

    def _add_start(self) -> None:
        if self.doc is not None:
            self._add(new_element(KIND_START, *self._centre()))

    def _add_island(self) -> None:
        if self.doc is None:
            return
        dialog = IslandDialog(self.maps.pool if self.doc.is_template else [], self.maps.island_files, self)
        if not self.doc.is_template:
            dialog.every.setChecked(True)
        if dialog.exec() and dialog.chosen():
            self._add_fixed(dialog.chosen())

    def _add_fixed(self, path: str, wait: bool = False) -> None:
        """A fixed island at the view's centre, once its grid and picture are read off the window's thread."""
        x, z = self._centre()
        randomize = self.doc.is_template
        self._with_islands(
            [path],
            lambda: self._add(new_element(KIND_FIXED, x, z, map_file_path=path, randomize_fertilities=randomize)),
        )
        if wait:
            self.background.join()

    def _add_island_file(self) -> None:
        """An island from a folder (a project's build, another mod): its game path is the part from ``data/``."""
        if self.doc is None:
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("An island file"), "", tr("Island files (*.a7m *.isd)"))
        if not name:
            return
        local = Path(name).as_posix()
        cut = local.lower().find("/data/")
        if cut < 0:
            self.statusBar().showMessage(tr("the file must lie in a folder named data, as in a mod"))
            return
        root, game_path = Path(local[:cut]), local[cut + 1 :].lower()
        loose = gametree.loose_tree(root, "island")
        x, z = self._centre()
        doc = self.doc

        def work():
            picture = pool.picture_path(loose, game_path)
            return pool.read_island_info(loose, game_path), loose.read(picture) if picture else b""

        def done(result) -> None:
            if isinstance(result, Exception) or self.doc is not doc:
                self.statusBar().showMessage(tr("the island did not read: {error}", error=result))
                return
            doc.extra_files[game_path] = result[0]
            self._add(new_element(KIND_FIXED, x, z, map_file_path=game_path, randomize_fertilities=doc.is_template))
            if result[1]:
                self.view.set_picture(game_path, result[1])

        self.background.run(work, done)

    def _duplicate(self) -> None:
        if self.doc is not None and self.view.selected:
            self.view.select(self.doc.duplicate(list(self.view.selected)))
            self._edited()

    def _select_complex(self) -> None:
        index = self._one()
        if index is not None:
            self.view.select(placing.complex_of(index, self.doc.complexes()))

    def _mirror(self) -> None:
        if self.doc is None or not self.view.selected:
            self.statusBar().showMessage(tr("select what to mirror first"))
            return
        dialog = MirrorDialog(self)
        if dialog.exec():
            mode, copies = dialog.values()
            self.view.select(self.doc.mirror(list(self.view.selected), mode, copies))
            self._edited()

    def _add_complex(self) -> None:
        """A complex of another map of the game, read off the window's thread, placed at the view's centre."""
        if self.doc is None:
            return
        path, ok = QInputDialog.getItem(self, tr("A complex of another map"), tr("Map:"), self.paths, 0, False)
        if not ok:
            return
        tree, key, maps, doc = self.tree, self.key, self.maps, self.doc

        def work():
            other = mapdoc.open_map(tree, key, path, maps)
            return other, other.complexes()

        def done(result) -> None:
            if isinstance(result, Exception) or self.doc is not doc:
                self.statusBar().showMessage(tr("the map did not open: {error}", error=result))
                return
            other, groups = result
            if not groups:
                self.statusBar().showMessage(tr("{path} has no complex of islands", path=path))
                return
            self._place_complex(other, groups)

        self.background.run(work, done)

    def _place_complex(self, other: mapdoc.MapDoc, groups: list[list[int]]) -> None:
        labels = [
            tr("{count} islands at {x}, {z}", count=len(g), x=other.elements[g[0]].x, z=other.elements[g[0]].z)
            for g in groups
        ]
        label, ok = QInputDialog.getItem(self, tr("A complex of another map"), tr("Complex:"), labels, 0, False)
        if not ok:
            return
        chosen = [other.elements[i].copy() for i in groups[labels.index(label)]]
        x0, z0 = min(e.x for e in chosen), min(e.z for e in chosen)
        cx, cz = self._centre()
        for e in chosen:
            e.x, e.z = e.x - x0 + int(cx) // 8 * 8, e.z - z0 + int(cz) // 8 * 8
            self.doc.extra_files.setdefault(e.map_file_path, other.info(e.map_file_path))
        self.view.select(self.doc.add_all(chosen))
        self.doc.learn_seams(chosen)
        self._load_pictures([e.map_file_path for e in chosen])
        self._edited()

    def _load_pictures(self, paths: list[str]) -> None:
        self._with_islands(paths, lambda: None)

    def _details(self, wait: bool = False) -> None:
        """The selected fixed island's mine slots and areas: its slot objects read off the window's thread."""
        index = self._one()
        if index is None or self.doc.elements[index].kind != KIND_FIXED or not self.doc.is_template:
            self.statusBar().showMessage(tr("select one fixed island of a map template"))
            return
        doc, tree, e = self.doc, self.tree, self.doc.elements[index]
        field = doc.record.mine_field or {"anno117": "MineSlotActivation", "anno1800": "MineSlotMapping"}[doc.game]

        def done(result) -> None:
            if isinstance(result, Exception) or self.doc is not doc:
                return
            dialog = DetailsDialog(e, result, field, doc.maps, self)
            if not wait and dialog.exec():
                doc.set_values(index, dialog.values())
                self._edited()

        self.background.run(lambda: pool.island_slots(tree, e.map_file_path, doc.maps.slot_assets), done, wait=wait)

    def _open_in_islands(self) -> None:
        index = self._one()
        e = self.doc.elements[index] if index is not None else None
        if e is None or e.kind != KIND_FIXED or not links.available(links.OPEN_ISLAND):
            self.statusBar().showMessage(tr("select one fixed island; the Islands app opens it when it runs beside"))
            return
        if not links.follow(links.OPEN_ISLAND, self.key, e.map_file_path):
            self.statusBar().showMessage(tr("the Islands app does not know {path}", path=e.map_file_path))

    # growing
    def _grow(self) -> None:
        if self.doc is None:
            return
        dialog = GrowDialog(self.doc.size, self)
        if dialog.exec():
            self._run_grow(*dialog.values())

    def _run_grow(self, sides: tuple[int, int, int, int], widen: bool, wait: bool = False) -> None:
        try:
            grow.check_sides(sides)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        doc, tree = self.doc, self.tree

        def work():
            scripts = {}
            if doc.is_template:
                asset = doc.maps.asset_of(doc.path)
                names = [doc.path.rsplit("/", 1)[-1].rsplit(".", 1)[0], str(asset.guid) if asset else ""]
                scripts = grow.scripts_naming(tree, names)
            return doc.grow(sides, widen, scripts)

        def done(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("the map did not grow: {error}", error=result))
                return
            self.view.fit()
            self._edited()
            self.statusBar().showMessage(
                tr(
                    "grown to {w} x {h}: {moved} references moved, {kept} kept",
                    w=self.doc.size[0],
                    h=self.doc.size[1],
                    moved=len(result.report.moved()),
                    kept=len(result.report.kept()),
                )  # fmt: skip
            )
            if not wait:
                ReportDialog(result.report.lines(), self).exec()

        self.statusBar().showMessage(tr("Growing the map (the session file too)..."))
        self.background.run(work, done, wait=wait)

    def _show_report(self) -> None:
        if self.doc is None or self.doc.grown is None:
            self.statusBar().showMessage(tr("the map has not grown"))
            return
        ReportDialog(self.doc.grown.report.lines(), self).exec()

    # keeping and writing
    def _keep(self, files_of, kept_key: str, wait: bool = False, done_text: str = "", after=None) -> None:
        """Keep the files ``files_of()`` makes (off the window's thread) in the project (``map-edits``); what an
        earlier keep under ``kept_key`` made and this one lacks goes; ``after()`` runs on the window's thread."""
        doc, project = self.doc, self.project()
        earlier = self.kept.get(kept_key, set())

        def work():
            files = files_of()
            report = doc.grown.report.to_json() if doc.grown else ""
            projectpart.save(project, self.key, files, earlier, report)
            return files

        def done(result) -> None:
            if isinstance(result, Exception):
                self.statusBar().showMessage(tr("not kept: {error}", error=result))
                return
            self.kept[kept_key] = set(result)
            if kept_key == doc.path:
                self.saved = doc.render()
            self.statusBar().showMessage(done_text or tr("kept {count} files in the project", count=len(result)))
            if after is not None:
                after()

        self.background.run(work, done, wait=wait)

    def save(self, wait: bool = False) -> None:
        """Keep the map's files in the project (``map-edits``); what an earlier save made and this one lacks goes."""
        if self.doc is None:
            self.statusBar().showMessage(tr("nothing changed"))
            return
        doc, choice = self.doc, self.choice
        self._keep(lambda: mapmod.map_files(doc, choice) if (doc.changed() or choice.as_new) else {}, doc.path, wait)

    def _new_guids(self, names: list[str]) -> dict[str, int]:
        """The GUIDs offered for new maps: each its earlier one, else the highest free ones of the project's range."""
        project = self.project()
        kept = mapmod.read_guids(project.folder / projectpart.EDITS.edits)
        taken, out = mapmod.taken_guids(project), {}
        for name in names:
            out[name] = mapmod.suggest_guid(project.guid_range, taken | set(out.values()), name, kept)
        return out

    def _guid_refused(self, picked: dict[str, int]) -> bool:
        """Show why a new map may not take its GUID; True when one is refused (also two maps with one GUID)."""
        project = self.project()
        folder = project.folder / projectpart.EDITS.edits
        kept, taken = mapmod.read_guids(folder), mapmod.taken_guids(project)
        if len(set(picked.values())) < len(picked):
            self.statusBar().showMessage(tr("two new maps cannot share a GUID"))
            return True
        for name, guid in picked.items():
            problem = mapmod.guid_problem(guid, project.guid_range, taken, kept, name)
            if problem:
                self.statusBar().showMessage(problem)
                return True
        for name, guid in picked.items():
            mapmod.keep_guid(folder, name, guid)
        return False

    def save_as_new(self) -> None:
        if self.doc is None or not self.doc.is_template:
            self.statusBar().showMessage(tr("a new map is a map template of 117 or 1800"))
            return
        project = self.project()
        name = self.doc.path.rsplit("/", 1)[-1].rsplit(".", 1)[0] + "_edited"
        dialog = NewMapDialog(name, self._new_guids([name])[name], self)
        if not dialog.exec():
            return
        name, guid = dialog.values()
        if self._guid_refused({name: guid}):
            return
        self.choice = mapmod.Choice(True, name, guid, project.mod_id)
        self.save()

    def write_variants(self, picked: tuple | None = None, wait: bool = False) -> None:
        """The map's three difficulties kept in the project (``picked``: the dialog's values, for a test)."""
        if self.doc is None or not self.doc.is_template:
            self.statusBar().showMessage(tr("difficulties are map templates of 117 and 1800"))
            return
        doc, project = self.doc, self.project()
        base = doc.path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_medium", "")
        if picked is None:
            offered = self._new_guids([f"{base}_{level.key}" for level in variants.LEVELS])
            siblings = all(doc.tree.exists(variants.sibling(doc.path, k) or "-") for k in ("easy", "hard"))
            guids = {level.key: offered[f"{base}_{level.key}"] for level in variants.LEVELS}
            dialog = VariantsDialog(base, guids, siblings, self)
            if not dialog.exec():
                return
            picked = dialog.values()
        as_new, name, guids = picked
        if as_new and self._guid_refused({f"{name}_{k}": g for k, g in guids.items()}):
            return
        choice = mapmod.Choice(as_new, name, 0, project.mod_id)
        found: dict = {}

        def files_of():
            files, findings = mapmod.variant_files(doc, choice, guids)
            found.update(findings)
            return files

        def after() -> None:  # what the checks find in each level, shown once the files are kept
            problems = [f"{tr(level)}: {f.text}" for level, fs in found.items() for f in fs]
            self.last_variant_findings = problems
            if problems and not wait:
                ReportDialog(problems, self).exec()

        self._keep(files_of, doc.path + VARIANTS_KEPT, wait, tr("kept the three difficulties in the project"), after)

    def write_mod_folder(self) -> None:
        if self.doc is None:
            return
        folder = QFileDialog.getExistingDirectory(self, tr("The folder to write the mod into"))
        if not folder:
            return
        doc, choice = self.doc, self.choice
        mod_id = (
            choice.mod_id if choice.as_new else "map-" + doc.path.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", "-")
        )

        def work():
            report = doc.grown.report.to_json() if doc.grown else ""
            return mapmod.write_mod(Path(folder), mapmod.map_files(doc, choice), mod_id, mod_id, report)

        def done(result) -> None:
            text = (
                tr("not written: {error}", error=result)
                if isinstance(result, Exception)
                else tr("written: {folder}", folder=result)
            )
            self.statusBar().showMessage(text)

        self.background.run(work, done)

    def _may_leave(self) -> bool:
        """Ask before an unkept edited map is left (another map opened, the window closed)."""
        if self.doc is None or not self.doc.undos or (self.saved is not None and self.doc.render() == self.saved):
            return True
        answer = QMessageBox.question(
            self,
            tr(TITLE),
            tr("The map has changes that are not kept in the project. Keep them first?"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.save(wait=True)
        return answer != QMessageBox.StandardButton.Cancel

    def closeEvent(self, event) -> None:
        if not self._may_leave():
            event.ignore()
            return
        self.background.join(2.0)
        super().closeEvent(event)
