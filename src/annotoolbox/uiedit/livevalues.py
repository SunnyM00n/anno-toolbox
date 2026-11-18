"""The UI editor's live values (Qt): "Take the live values from the running game" asks the live link what the open
file's widgets show in the game now (``ui.values``), the preview shows it in place of the sample values, and every
answer taken is recorded so that the values fill the preview later without the game.

``add_file_actions`` and ``add_view_action`` fill the menus; ``texts`` and ``overrides`` are what the window's layout
takes (``layout.Context.live_text``, ``live_values``). The values themselves are ``liverecord``. docs/ui-editor.md,
"Live values"; the request is docs/live-link.md section 17.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from ..infra.i18n import N_, tr, trn
from ..suite import linkscenes, livelink, widgets
from ..suite.settings import appdata
from . import elements, gamedata, liverecord, pictures

SETTING = "live_values"  # the View switch, in the app's settings
FROM_GAME, FROM_RECORDING = N_("ui.live_values_from_game"), N_("ui.live_values_from_recorded")


def recordings(window) -> liverecord.Recordings:
    return liverecord.Recordings(Path(window.settings.workspace or appdata()) / liverecord.FOLDER)


def asked(window) -> tuple[list[str], str] | None:
    """The UI states to ask and the open file's GUID (empty for a scene file, whose own widgets are asked)."""
    if window.game is None or window.doc is None or window.top is None:
        return None
    entry = window.game.files.entries.get(window.doc.key)
    path = entry.path if entry else window.doc.key
    own = linkscenes.scene_state(path)
    if own:
        return [own], ""
    return linkscenes.states_for([path]) or window.game.files.scene_states([path]), window.top.guid or ""


def take(window, wait: bool = False) -> None:
    """Ask the running game for what the open file's widgets show; the answer is held and recorded."""
    found = asked(window)
    if found is None:
        return
    states, file = found
    link = livelink.current()
    if not link.connected:
        window.statusBar().showMessage(tr("ui.no_game_linked_live_values"))
        return
    if not states:
        window.statusBar().showMessage(tr("ui.file_in_no_scene"))
        return
    key, game, build = window.doc.key, window.game.key, link.status().build
    window.statusBar().showMessage(tr("ui.asking_game_live_values"))
    window.background.run(
        lambda: link.ui_values(states, file), lambda reply: taken(window, reply, key, game, build), wait
    )


def taken(window, reply, key: str, game: str, build: str) -> None:
    """The game's answer to ``take``: held for the file it was asked for, recorded, the preview laid out again."""
    if isinstance(reply, Exception) or not reply.ok:
        why = getattr(reply, "message", "") or str(reply) or "?"
        window.statusBar().showMessage(tr("ui.game_gave_no_live_values", why=why))
        return
    now = datetime.now().isoformat(timespec="seconds")
    held = liverecord.from_reply(reply.data, key, game, build, now)
    try:
        recordings(window).save(held)
    except OSError as exc:
        window.statusBar().showMessage(tr("ui.live_values_not_recorded", error=exc))
    window.live_held, window.live_loaded = held, (game, key)
    window.statusBar().showMessage(trn("ui.took_live_values", len(held.widgets), states=", ".join(held.states)))
    _show_label(window, held)
    window._redraw()


def held(window) -> liverecord.Held | None:
    """The values held for the open file: taken in this run, else its recording (read once per file opened)."""
    if window.game is None or window.doc is None:
        return None
    here = (window.game.key, window.doc.key)
    if getattr(window, "live_loaded", None) != here:
        window.live_held, window.live_loaded = recordings(window).load(*here), here
        _show_label(window, window.live_held)
    return window.live_held


def _shown(window) -> liverecord.Held | None:
    action = getattr(window, "values_action", None)
    return held(window) if action is not None and action.isChecked() else None


def texts(window) -> dict[str, str]:
    found = _shown(window)
    return found.texts() if found else {}


def overrides(window) -> dict[str, dict]:
    found = _shown(window)
    return found.overrides() if found else {}


def use_recorded(window) -> None:
    """Read the open file's recording again (after it was taken over by the game's values, or the file changed)."""
    if window.game is None or window.doc is None:
        return
    window.live_loaded = None
    found = held(window)
    window.statusBar().showMessage(_describe(window, found) if found else tr("ui.no_recorded_values"))
    window._redraw()


def clear_recorded(window) -> None:
    if window.game is None or window.doc is None:
        return
    recordings(window).clear(window.game.key, window.doc.key)
    window.live_held, window.live_loaded = None, (window.game.key, window.doc.key)
    _show_label(window, None)
    window.statusBar().showMessage(tr("ui.recorded_values_cleared"))
    window._redraw()


def clear_all(window) -> None:
    sure = QMessageBox.question(window, window.windowTitle(), tr("ui.clear_all_recorded_values_q"))
    if sure != QMessageBox.StandardButton.Yes:
        return
    gone = recordings(window).clear_all()
    window.live_held, window.live_loaded = None, None
    _show_label(window, None)
    window.statusBar().showMessage(trn("ui.recordings_deleted", gone))
    window._redraw()


def asset_lookup(settings, key: str):
    """``guid -> (name, icon)`` of the installed game's assets, from the asset index the asset editor keeps (built
    once, then cached); None when the game's folder is not set or the game has no asset files of this kind."""
    from ..assetedit import assetgame

    index = assetgame.installed_index(settings, key, settings.workspace or appdata())
    if index is None:
        return None
    assets = gamedata.Assets.from_index(index, lambda _language: {})

    def look(guid: int) -> tuple[str, str] | None:
        entry = index.by_guid.get(guid)
        return (entry.name, assets.icon(guid) or "") if entry is not None else None

    return look


def list_assigned(window, wait: bool = False) -> None:
    """The assets, images and portraits the held values name, with the installed game's names and icons."""
    found = held(window)
    if found is None:
        window.statusBar().showMessage(tr("ui.no_recorded_values"))
        return
    settings, key, tree = window.settings, window.game.key, window.game.tree

    def work():
        look = asset_lookup(settings, key) or (lambda _guid: None)
        return liverecord.assigned(found, look, lambda image: any(tree.exists(p) for p in pictures.candidates(image)))

    window.statusBar().showMessage(tr("ui.looking_up_assigned"))
    window.background.run(work, lambda rows: _show_assigned(window, rows), wait)


def _show_assigned(window, rows) -> None:
    if isinstance(rows, Exception):
        window.statusBar().showMessage(tr("ui.assigned_not_looked_up", error=rows))
        return
    window.assigned_rows = rows  # kept for a test and for the dialog's life
    dialog = QDialog(window, windowTitle=tr("ui.assigned_by_game"))
    table = QTreeWidget()
    table.setHeaderLabels(
        [tr("ui.element"), tr("ui.kind"), tr("ui.assigned_ref"), tr("common.name"), tr("ui.assigned_icon")]
    )
    words = {"asset": tr("ui.assigned_asset"), "image": tr("ui.assigned_image"), "portrait": tr("ui.assigned_portrait")}
    for r in rows:
        notes = {liverecord.NO_FILE: tr("ui.assigned_no_file"), liverecord.MADE_AT_RUN_TIME: tr("ui.assigned_run_time")}
        where = notes.get(r.note, r.icon)
        table.addTopLevelItem(QTreeWidgetItem([r.widget, words.get(r.what, r.what), r.ref, r.name, where]))
    QVBoxLayout(dialog).addWidget(table)
    dialog.resize(900, 420)
    window.statusBar().showMessage(trn("ui.assigned_found", len(rows)))
    dialog.show()


def element_keys(top: elements.Element | None) -> set[str]:
    """The keys the open file's elements are matched by (instance GUID, ``crc:<name CRC>``)."""
    keys: set[str] = set()
    for el in top.walk() if top is not None else ():
        keys.update(elements.keys(el))
    return keys


def _describe(window, found: liverecord.Held) -> str:
    """Where the values are from, and how many no longer match the file (stale)."""
    stale = found.unmatched(element_keys(window.top))
    text = tr(
        FROM_RECORDING if found.recorded else FROM_GAME,
        build=found.build or "?",
        taken=found.taken.replace("T", " "),
        n=len(found.widgets),
    )
    if stale:
        text += " " + trn("ui.live_values_stale", stale)
    if found.cut:
        text += " " + tr("ui.live_values_cut")
    return text


def _show_label(window, found: liverecord.Held | None) -> None:
    label = getattr(window, "values_label", None)
    if label is None:
        return
    label.setText(_describe(window, found) if found is not None else "")
    label.setVisible(found is not None)


def add_file_actions(window, file_menu) -> None:
    """The File menu's commands of the live values."""
    window.take_values_action = file_menu.addAction(tr("ui.take_live_values"), lambda: take(window))
    file_menu.addAction(tr("ui.use_recorded_values"), lambda: use_recorded(window))
    file_menu.addAction(tr("ui.list_assigned"), lambda: list_assigned(window))
    file_menu.addAction(tr("ui.clear_recorded_values_file"), lambda: clear_recorded(window))
    file_menu.addAction(tr("ui.clear_all_recorded_values"), lambda: clear_all(window))


def add_view_action(window, view_menu, app: str):
    """The View menu's switch for the held values (kept in the app's settings, on by default), and the status bar's
    note of where they are from; returns the action."""
    window.live_held, window.live_loaded = None, None
    window.values_label = QLabel()
    window.values_label.setVisible(False)
    window.statusBar().addPermanentWidget(window.values_label)
    return widgets.kept_switch(window, view_menu, tr("ui.show_live_values"), window._redraw, (app, SETTING))
