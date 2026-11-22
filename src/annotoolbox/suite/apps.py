"""The suite's apps: the island editor, the asset editor, the viewer, the UI editor (the interface files of every game,
Flash movies of 2205 included), the mod manager, the object builder, the mod diff, the text editor, the map editor, the
savegame editor, the particle editor, and what an extension adds at ``features.APPS``. Each app's modules are imported
when its page is first made, so the window opens without them.
``parts`` lists each app's share of a project's mod (``suite.project``); each app's ``links`` are what it answers
for the others (``suite.links``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..infra.i18n import N_, tr
from ..render import glformat, rhiview
from ..sources import gametree
from . import appplugins, features, links, project
from . import carriedpart as carried
from .extspecs import SAppSpec
from .gamesync import SAppGame
from .shell import CSuiteWindow


def _islands(shell: CSuiteWindow):
    from ..editor import editorapp
    from ..editor.desktop.workbench import CMainWindow

    window = CMainWindow(app=editorapp.CApp(shell.m_settings_path, settings=shell.m_settings), lists_wait=False)
    window.show_game(shell.m_settings.game)  # the Islands panel on the suite's game, once its lists are read
    return window


def _open_island_of(page, game: str, other: str) -> tuple[object, str]:
    """(the open island's document, "") when the island editor has an island of ``game`` open, else (None, why not);
    ``other`` is the id of the sentence for an island of another game (it names ``{open}`` and the wanted game as
    ``{model}`` or ``{asset}``)."""
    from ..editor.editsession import game_of

    doc = getattr(page, "m_doc", None)
    if doc is None:
        return None, tr("common.open_island_first")
    open_game = game_of({}, kind=doc.m_info.get("kind") or "")
    if open_game != game:
        return None, tr(other, open=open_game, model=game, asset=game)
    return doc, ""


def _islands_place(page, game: str, cfg: str) -> str:
    """Pick a model config for the Place tool of the open island; says why not where it cannot."""
    doc, why = _open_island_of(page, game, N_("common.open_island_s_model"))
    if doc is None:
        return why
    buildings = getattr(doc, "m_buildings", None)  # the island's asset list (desktopplace)
    guid = buildings.guid_of(cfg) if buildings is not None else None
    if guid is not None:  # a model of an asset: placed as that asset, on its grid
        return buildings.choose(guid)
    name = cfg.rsplit("/", 1)[-1].removesuffix(".cfg")
    doc.m_view.set_pick({"name": name, "cfg": cfg, "group": "from the viewer", "title": cfg})
    doc.say(tr("common.place_click_island", name=name))
    return ""


def _islands_place_asset(page, game: str, guid: int) -> str:
    """Pick an asset for the Place tool of the open island (``desktopplace.CPlaceablesPanel.choose``); "" or why not."""
    doc, why = _open_island_of(page, game, N_("common.open_island_s_asset"))
    return why if doc is None else doc.m_buildings.choose(int(guid))


def _islands_open(page, game: str, island: str) -> bool:
    """Open the island a game's edit request names (its path or file name) in the island editor; False when the editor
    has no such island of that game (``CApp.find_island``)."""
    app = getattr(page, "m_app", None)
    found = app.find_island(game, island) if app is not None else None
    if found is None:
        return False
    page.open(*found, is_waiting=False)
    return True


def _islands_selection(page, selection: dict) -> bool:
    """Select, on the open island, the objects the game has selected, and fly there; False when it holds none of them."""
    from ..editor.gameselection import entries_for

    doc = getattr(page, "m_doc", None)
    found = entries_for(doc.m_session, selection) if doc is not None else []
    if not found:
        return False
    doc.m_view.select(found[0])
    doc.m_view.focus_on(found)
    doc.say(tr("common.game_selected_name", name=found[0].base.get("label") or found[0].cfg))
    return True


def _assets(shell: CSuiteWindow):
    from ..assetedit.assetwindow import CAssetWindow

    window = CAssetWindow(shell.m_settings, shell.m_settings_path)
    game = shell.m_settings.apps.get("assets", {}).get("game")
    if game:  # the game last open, again
        window.open(game)
    return window


def _assets_show(page, game: str, guid: int) -> str:
    """Show an asset of ``game``; the game opens in the background first when another one (or none) is open, and the
    answer is then "pending" (a link counts it as found); "" when the open game has no such asset."""
    result = page.show_asset_of(game, guid)
    return "" if result == "missing" else result


def _asset_files(_page, game: str) -> tuple[str, ...]:
    """The game's asset files, empty for a game the asset editor does not know."""
    from ..assetedit.gamefiles import LAYOUTS

    return LAYOUTS[game].assets if game in LAYOUTS else ()


def _viewer(shell: CSuiteWindow):
    """The asset viewer on the game (and the file) last open in it."""
    from ..viewer.viewerwindow import APP, CViewerWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.m_settings.renderer))  # the island view's, beside it
    window = CViewerWindow(shell.m_settings, shell.m_settings_path, is_gl_host=island_api == "opengl")
    last = shell.m_settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _viewer_open(page, game: str, path: str) -> bool:
    """Open a file of the game in the viewer."""
    page.open_file(game, path)
    return True


def _viewer_export(page, game: str, paths: list[str]) -> bool:
    """Export the files from the viewer; whether it did."""
    return page.export_files(game, paths)


def _ui(shell: CSuiteWindow):
    """The UI editor: one page for Anno 117's, 1800's, 1404's and 1701's files, one for 2205's Flash movies, each made
    when first shown; the Flash editor's edit file is the open project's."""
    from ..flashui.uiwindow import CEditorWindow
    from ..uiedit import uihost
    from ..uiedit.oldpage import COldWindow
    from ..uiedit.uiwindow import CUiWindow

    def flash():
        edits = project.folder_of(shell.m_settings) / project.UI_EDITS / "anno2205.json"
        game = (
            shell.m_settings.anno2205 if shell.m_settings.anno2205 and Path(shell.m_settings.anno2205).is_dir() else ""
        )
        return CEditorWindow(game, str(edits), last_game=False)

    host = uihost.CUiHost(
        shell.m_settings,
        {
            uihost.PHOENIX: lambda: CUiWindow(shell.m_settings, shell.m_settings_path),
            uihost.FLASH: flash,
            uihost.OLD: lambda: COldWindow(shell.m_settings),
        },
    )
    host.show_last()
    return host


def _objects(shell: CSuiteWindow):
    """The object builder on the game and the object last open in it."""
    from ..objbuild.objwindow import APP, CObjectWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.m_settings.renderer))
    window = CObjectWindow(shell.m_settings, shell.m_settings_path, is_gl_host=island_api == "opengl")
    last = shell.m_settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _objects_open(page, game: str, path: str) -> bool:
    """Open a file of the game in the object builder."""
    page.open_file(game, path)
    return True


def _mods(shell: CSuiteWindow):
    """The mod manager on the game and profile last open in it."""
    from ..modmgr.window import CModManagerWindow

    return CModManagerWindow(shell.m_settings, shell.m_settings_path)


def _mods_show(page, game: str, mod_ids: list[str]) -> bool:
    """Show ``game``'s mods in the Mods app, the first of ``mod_ids`` that is installed selected (the Saves app's)."""
    page.open_game(game)
    for mod_id in mod_ids:
        card = page.m_manager.m_catalog.by_id(mod_id)
        if card is not None:
            page.m_tree.select_folder(card.folder)
            break
    return True


def _saves(shell: CSuiteWindow):
    """The savegame editor on the game last open in it."""
    from ..saveedit.savewindow import CSavesWindow

    return CSavesWindow(shell.m_settings, shell.m_settings_path)


def _moddiff(shell: CSuiteWindow):
    """The mod diff on the game and question last open in it."""
    from ..moddiff.window import CModDiffWindow

    return CModDiffWindow(shell.m_settings, shell.m_settings_path)


def _moddiff_open(page, mode: str, game: str, first_side: str, second_side: str, profile: str) -> bool:
    """Show a question another app asked (the Mods app's) in the Mod diff and run it."""
    from ..moddiff.jobs import SJob

    page.open_job(SJob(mode, game, first_side, second_side, profile))
    return True


def _sound(shell: CSuiteWindow):
    """The sound editor on the game last open in it."""
    from ..soundedit.soundwindow import APP, CSoundWindow

    window = CSoundWindow(shell.m_settings, shell.m_settings_path)
    game = shell.m_settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _texts(shell: CSuiteWindow):
    """The text editor on the game last open in it."""
    from ..textedit.textwindow import APP, CTextWindow

    window = CTextWindow(shell.m_settings, shell.m_settings_path)
    game = shell.m_settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _maps(shell: CSuiteWindow):
    """The map editor on the game last open in it."""
    from ..mapedit.mapwindow import APP, CMapWindow

    window = CMapWindow(shell.m_settings, shell.m_settings_path)
    game = shell.m_settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _particles(shell: CSuiteWindow):
    """The particle editor on the game and the effect last open in it."""
    from ..particleedit.particlewindow import APP, CParticleWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.m_settings.renderer))
    window = CParticleWindow(shell.m_settings, shell.m_settings_path, is_gl_host=island_api == "opengl")
    last = shell.m_settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _converter(shell: CSuiteWindow):
    """The Converter app: single files, assets and islands into other formats (docs/converter-app.md)."""
    from ..converterapp.window import make

    return make(shell)


def _particles_open(page, game: str, path: str, model: str = "") -> bool:
    """Open an effect of the game in the particle editor."""
    page.open_effect(game, path, model)
    return True


def _app_games(app: str):
    """The games an app can show, from the app's own list (its modules load with its page)."""
    if app == "assets":
        from ..assetedit.gamefiles import LAYOUTS as games
    elif app == "ui":
        from ..uiedit import uiframe, uihost

        games = (*uiframe.GAMES, uihost.FLASH_GAME, uihost.OLD_GAME)
    elif app == "mods":
        from ..modmgr.manager import GAMES as games
    elif app == "sound":
        from ..soundedit.soundwindow import GAMES as games
    elif app == "texts":
        from ..textedit.textwindow import GAMES as games
    elif app == "moddiff":
        from ..moddiff.jobs import ARCHIVE_GAMES as games
    elif app == "maps":
        from ..mapedit.mapwindow import GAMES as games
    elif app == "saves":
        from ..saveedit.locations import LOCATIONS as games
    else:  # the island editor, the viewer, the object builder, the particle editor: every game
        games = gametree.GAMES
    return games


def _box_open(page, key: str) -> None:
    """Choose ``key`` in an app's box of games; a box that lacks it now (the Mod diff's question) says so."""
    index = page.m_game_box.findData(key)
    if index >= 0:
        page.m_game_box.setCurrentIndex(index)
    else:
        page.statusBar().showMessage(tr("common.game_sync_not_shown", game=gametree.GAMES.get(key, key)))


def _ui_open(host, key: str) -> None:
    """The UI app's page for ``key`` to the front; the Phoenix or old-interface page opens the game unless it is on it
    already (the old-interface tab keeps 1404 and 1701 while it is in front)."""
    from ..uiedit import oldpage, uihost

    kind = uihost.OLD if key in oldpage.OLD_GAMES and host.m_current == uihost.OLD else uihost.kind_of(key)
    page = host.show_kind(kind)
    if kind in (uihost.PHOENIX, uihost.OLD) and (page.game is None or page.game.m_key != key):
        page.open_game(key)


def _ui_may_leave(host, key: str) -> bool:
    """Only the Phoenix page leaves a game for another: 2205's page holds its one game whatever is selected."""
    from ..uiedit import uihost

    page = host.m_pages.get(uihost.PHOENIX)
    if page is None or page.game is None or uihost.kind_of(key) != uihost.PHOENIX or page.game.m_key == key:
        return True
    return page.may_leave()


def _ui_remember(state: dict, game: str) -> None:
    """Remember the UI game's page kind and game in the app's saved ``state``."""
    from ..uiedit import uihost

    state["kind"] = uihost.kind_of(game)
    if state["kind"] == uihost.PHOENIX:
        state["game"] = game
    elif state["kind"] == uihost.OLD:
        state["old"] = game


def _app_game(app: str, current, open_game=lambda page, key: page.open_game(key), **more) -> SAppGame:
    """An app's side of the suite's game selection (``gamesync``; suite.md "One game for every app")."""
    return SAppGame(app, lambda: _app_games(app), current, open_game, **more)


def _tree_game(page) -> str:
    """The game the page's file tree shows, "" without a tree."""
    return page.m_tree.m_game if page.m_tree is not None else ""


APP_GAMES = {
    # the Islands panel lists every game: its tab is turned, the open island stays
    "islands": _app_game("islands", lambda page: page.shown_game(), lambda page, key: page.show_game(key),
                     remember=lambda _state, _game: None),
    "assets": _app_game("assets", lambda page: page.m_game.m_layout.key if page.m_game is not None else "", lambda page, key: page.open(key),
                    may_leave=lambda page, _key: page.confirm_close()),
    "viewer": _app_game("viewer", _tree_game),
    "ui": _app_game("ui", lambda host: host.game(), _ui_open, may_leave=_ui_may_leave, remember=_ui_remember,
                say=lambda host, text: host.m_pages[host.m_current].statusBar().showMessage(text)),
    "mods": _app_game("mods", lambda page: page.m_manager.m_game, needs_folder=False),
    "objects": _app_game("objects", _tree_game, may_leave=lambda page, _key: page.confirm_discard()),
    "sound": _app_game("sound", lambda page: page.m_key, may_leave=lambda page, _key: page.may_leave()),
    "texts": _app_game("texts", lambda page: page.m_key, may_leave=lambda page, _key: page.may_leave()),
    "moddiff": _app_game("moddiff", lambda page: page.m_game_box.currentData() or "", _box_open, needs_folder=False),
    "maps": _app_game("maps", lambda page: page.m_key, may_leave=lambda page, _key: page._may_leave()),
    "saves": _app_game("saves", lambda page: page.m_game_box.currentData() or "", _box_open, needs_folder=False),
    "particles": _app_game("particles", _tree_game, may_leave=lambda page, _key: page.confirm_discard()),
}  # fmt: skip


BUILTIN = (
    SAppSpec("islands", N_("common.islands"), "app_islands", N_("common.island_editor_islands_every_anno"), _islands,
            glformat.prepare, {links.PLACE_MODEL: _islands_place, links.PLACE_ASSET: _islands_place_asset,
                               links.OPEN_ISLAND: _islands_open, links.GAME_SELECTION: _islands_selection},
            frozenset({links.GAME_SELECTION})),  # following the game's selection never takes the window from the player
    SAppSpec("assets", N_("common.assets"), "app_assets", N_("common.asset_editor_games_assets_forms"),
            _assets, links={links.SHOW_ASSET: _assets_show, links.ASSET_FILES: _asset_files},
            quiet_links=frozenset({links.ASSET_FILES})),
    SAppSpec("viewer", N_("common.viewer"), "app_viewer", N_("common.asset_viewer_any_games_models"),
            _viewer, glformat.prepare, {links.VIEW_FILE: _viewer_open, links.EXPORT_FILES: _viewer_export}),
    SAppSpec("ui", N_("common.ui"), "app_phoenix", N_("common.ui_editor_interface_files_anno"),
            _ui),
    SAppSpec("mods", N_("common.mods"), "app_mods", N_("common.mod_manager_anno_117s_1800s"),
            _mods, links={links.SHOW_MODS: _mods_show}),
    SAppSpec("objects", N_("common.objects"), "app_objects", N_("common.object_builder_model_configs_parts"),
            _objects, glformat.prepare, {links.EDIT_OBJECT: _objects_open}),
    SAppSpec("sound", N_("sound.sound"), "app_sound", N_("common.sound_editor_wwise_banks_anno"),
            _sound),
    SAppSpec("texts", N_("ui.texts"), "app_texts", N_("common.text_editor_games_texts_two"),
            _texts),
    SAppSpec("moddiff", N_("common.mod_diff"), "app_moddiff", N_("common.mod_diff_what_changes_two"),
            _moddiff, links={links.DIFF_MODS: _moddiff_open}),
    SAppSpec("maps", N_("maps.maps"), "app_maps", N_("maps.map_editor_games_sessions_maps"),
            _maps),
    SAppSpec("saves", N_("saves.saves"), "app_saves", N_("saves.savegame_editor_every_games_saves"),
            _saves),
    SAppSpec("particles", N_("islands.particles"), "app_particles", N_("particles.particle_editor_any_games_effects"),
            _particles, glformat.prepare, {links.EDIT_EFFECT: _particles_open}),
    SAppSpec("converter", N_("convert.converter"), "app_converter", N_("convert.app_summary"), _converter),
)  # fmt: skip


def _taken() -> set[str]:
    """The keys the built-in apps and the extensions use: an external app (``appplugins``) cannot have one."""
    return {app.key for app in BUILTIN} | {app.key for app in features.extensions(features.APPS).values()}


def available() -> list[SAppSpec]:
    """The built-in apps, each with its side of the game selection, then the ones an extension registers
    (``features.APPS``), then the apps other people added (``appplugins``, docs/app-api.md)."""
    builtin = [replace(app, game=APP_GAMES.get(app.key)) for app in BUILTIN]
    added = appplugins.specs(appplugins.loaded(_taken()))
    return [*builtin, *features.extensions(features.APPS).values(), *added]


def parts() -> list[project.SPart]:
    """Each app's share of a project's mod, in the order the build merges them (the imported mod's carried files,
    islands, assets, the UI, sound, the imported models, the object builder's configs, the effects, the texts, the
    maps)."""
    from ..assetedit import handoverdialog
    from ..assetedit import projectpart as assets
    from ..editor import projectpart as islands
    from ..mapedit import projectpart as maps
    from ..objbuild import projectpart as objects
    from ..particleedit import projectpart as particles
    from ..soundedit import projectpart as sound
    from ..textedit import projectpart as texts
    from ..uiedit import projectpart as ui
    from ..viewer import projectpart as models

    return [
        carried.PART,
        islands.PART,
        replace(assets.PART, handover=handoverdialog.HANDOVER),
        ui.UI_PART,
        sound.PART,
        models.PART,
        objects.PART,
        particles.PART,
        texts.PART,
        maps.PART,
        *appplugins.parts(appplugins.loaded(_taken())),
    ]


def deliveries() -> dict[str, project.SDelivery]:
    """How the games that load archives take a project (``suite.project.SDelivery``): the islands' delivery, with the
    imported archive mods in front (``archivemodbuild``)."""
    from ..editor.archivedelivery import DELIVERIES
    from . import archivemodbuild

    return archivemodbuild.deliveries(DELIVERIES)
