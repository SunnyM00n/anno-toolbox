"""The suite's apps: the island editor, the asset editor, the 2205 UI editor, the viewer, the UI editor for 117 and
1800, the mod manager, the object builder, the mod diff, the text editor, the map editor, the savegame editor, the
particle editor, and what an extension adds at ``features.APPS``. Each app's modules are imported when its page is first made, so the window opens without them.
``parts`` lists each app's share of a project's mod (``suite.project``); each app's ``links`` are what it answers
for the others (``suite.links``).
"""

from __future__ import annotations

from pathlib import Path

from ..infra.i18n import N_, tr
from ..render import glformat, rhiview
from . import features, links, project
from .extspecs import AppSpec
from .shell import SuiteWindow


def _islands(shell: SuiteWindow):
    from ..editor import editorapp
    from ..editor.desktop.workbench import MainWindow

    return MainWindow(app=editorapp.App(shell.settings_path, settings=shell.settings))


def _islands_place(page, game: str, cfg: str) -> str:
    """Pick a model config for the Place tool of the open island; says why not where it cannot."""
    from ..editor.editsession import game_of

    doc = getattr(page, "doc", None)
    if doc is None:
        return tr("open an island first")
    open_game = game_of({}, kind=doc.info.get("kind") or "")
    if open_game != game:
        return tr("the open island is {open}'s, the model {model}'s", open=open_game, model=game)
    buildings = getattr(doc, "buildings", None)  # the island's asset list (desktopplace)
    guid = buildings.guid_of(cfg) if buildings is not None else None
    if guid is not None:  # a model of an asset: placed as that asset, on its grid
        return buildings.choose(guid)
    name = cfg.rsplit("/", 1)[-1].removesuffix(".cfg")
    doc.view.set_pick({"name": name, "cfg": cfg, "group": "from the viewer", "title": cfg})
    doc.say(tr("place {name}: click on the island", name=name))
    return ""


def _islands_place_asset(page, game: str, guid: int) -> str:
    """Pick an asset for the Place tool of the open island (``desktopplace.PlaceablesPanel.choose``); "" or why not."""
    from ..editor.editsession import game_of

    doc = getattr(page, "doc", None)
    if doc is None:
        return tr("open an island first")
    open_game = game_of({}, kind=doc.info.get("kind") or "")
    if open_game != game:
        return tr("the open island is {open}'s, the asset {asset}'s", open=open_game, asset=game)
    return doc.buildings.choose(int(guid))


def _islands_open(page, game: str, island: str) -> bool:
    """Open the island a game's edit request names (its path or file name) in the island editor; False when the editor
    has no such island of that game (``App.find_island``)."""
    app = getattr(page, "app", None)
    found = app.find_island(game, island) if app is not None else None
    if found is None:
        return False
    page.open(*found)
    return True


def _islands_selection(page, selection: dict) -> bool:
    """Select, on the open island, the objects the game has selected, and fly there; False when it holds none of them."""
    from ..editor.gameselection import entries_for

    doc = getattr(page, "doc", None)
    found = entries_for(doc.session, selection) if doc is not None else []
    if not found:
        return False
    doc.view.select(found[0])
    doc.view.focus_on(found)
    doc.say(tr("the game selected {name}", name=found[0].base.get("label") or found[0].cfg))
    return True


def _assets(shell: SuiteWindow):
    from ..assetedit.assetwindow import AssetWindow

    window = AssetWindow(shell.settings, shell.settings_path)
    game = shell.settings.apps.get("assets", {}).get("game")
    if game:  # the game last open, again
        window.open(game)
    return window


def _assets_show(page, game: str, guid: int) -> str:
    """Show an asset of ``game``; the game opens in the background first when another one (or none) is open, and the
    answer is then "pending" (a link counts it as found); "" when the open game has no such asset."""
    result = page.show_asset_of(game, guid)
    return "" if result == "missing" else result


def _asset_files(_page, game: str) -> tuple[str, ...]:
    from ..assetedit.gamefiles import LAYOUTS

    return LAYOUTS[game].assets if game in LAYOUTS else ()


def _ui2205(shell: SuiteWindow):
    """The Flash UI editor on the 2205 folder of the settings, its edit file in the open project."""
    from ..flashui.uiwindow import EditorWindow

    edits = project.folder_of(shell.settings) / project.UI_EDITS / "anno2205.json"
    game = shell.settings.anno2205 if shell.settings.anno2205 and Path(shell.settings.anno2205).is_dir() else ""
    return EditorWindow(game, str(edits), last_game=False)


def _viewer(shell: SuiteWindow):
    """The asset viewer on the game (and the file) last open in it."""
    from ..viewer.viewerwindow import APP, ViewerWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.settings.renderer))  # the island view's, beside it
    window = ViewerWindow(shell.settings, shell.settings_path, hosts_gl=island_api == "opengl")
    last = shell.settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _viewer_open(page, game: str, path: str) -> bool:
    page.open_file(game, path)
    return True


def _viewer_export(page, game: str, paths: list[str]) -> bool:
    return page.export_files(game, paths)


def _ui(shell: SuiteWindow):
    """The UI editor for 117's and 1800's Phoenix files, on the game last open in it."""
    from ..uiedit.uiwindow import APP, UiWindow

    window = UiWindow(shell.settings, shell.settings_path)
    game = shell.settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _objects(shell: SuiteWindow):
    """The object builder on the game and the object last open in it."""
    from ..objbuild.objwindow import APP, ObjectWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.settings.renderer))
    window = ObjectWindow(shell.settings, shell.settings_path, hosts_gl=island_api == "opengl")
    last = shell.settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _objects_open(page, game: str, path: str) -> bool:
    page.open_file(game, path)
    return True


def _mods(shell: SuiteWindow):
    """The mod manager on the game and profile last open in it."""
    from ..modmgr.window import ModManagerWindow

    return ModManagerWindow(shell.settings, shell.settings_path)


def _mods_show(page, game: str, mod_ids: list[str]) -> bool:
    """Show ``game``'s mods in the Mods app, the first of ``mod_ids`` that is installed selected (the Saves app's)."""
    page.open_game(game)
    for mod_id in mod_ids:
        card = page.manager.catalog.by_id(mod_id)
        if card is not None:
            page.tree.select_folder(card.folder)
            break
    return True


def _saves(shell: SuiteWindow):
    """The savegame editor on the game last open in it."""
    from ..saveedit.savewindow import SavesWindow

    return SavesWindow(shell.settings, shell.settings_path)


def _moddiff(shell: SuiteWindow):
    """The mod diff on the game and question last open in it."""
    from ..moddiff.window import ModDiffWindow

    return ModDiffWindow(shell.settings, shell.settings_path)


def _moddiff_open(page, mode: str, game: str, a: str, b: str, profile: str) -> bool:
    """Show a question another app asked (the Mods app's) in the Mod diff and run it."""
    from ..moddiff.jobs import Job

    page.open_job(Job(mode, game, a, b, profile))
    return True


def _sound(shell: SuiteWindow):
    """The sound editor on the game last open in it."""
    from ..soundedit.soundwindow import APP, SoundWindow

    window = SoundWindow(shell.settings, shell.settings_path)
    game = shell.settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _texts(shell: SuiteWindow):
    """The text editor on the game last open in it."""
    from ..textedit.textwindow import APP, TextWindow

    window = TextWindow(shell.settings, shell.settings_path)
    game = shell.settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _maps(shell: SuiteWindow):
    """The map editor on the game last open in it."""
    from ..mapedit.mapwindow import APP, MapWindow

    window = MapWindow(shell.settings, shell.settings_path)
    game = shell.settings.apps.get(APP, {}).get("game")
    if game:
        window.open_game(game)
    return window


def _particles(shell: SuiteWindow):
    """The particle editor on the game and the effect last open in it."""
    from ..particleedit.particlewindow import APP, ParticleWindow

    island_api = rhiview.api_of(rhiview.chosen(shell.settings.renderer))
    window = ParticleWindow(shell.settings, shell.settings_path, hosts_gl=island_api == "opengl")
    last = shell.settings.apps.get(APP, {})
    if last.get("game"):
        window.open_game(last["game"], last.get("file", ""))
    return window


def _particles_open(page, game: str, path: str, model: str = "") -> bool:
    page.open_effect(game, path, model)
    return True


BUILTIN = (
    AppSpec("islands", N_("Islands"), "app_islands", N_("the island editor: islands of every Anno, edit, build"), _islands,
            glformat.prepare, {links.PLACE_MODEL: _islands_place, links.PLACE_ASSET: _islands_place_asset,
                               links.OPEN_ISLAND: _islands_open, links.GAME_SELECTION: _islands_selection},
            frozenset({links.GAME_SELECTION})),  # following the game's selection never takes the window from the player
    AppSpec("assets", N_("Assets"), "app_assets", N_("the asset editor: a game's assets in forms from its schema, as a mod"),
            _assets, links={links.SHOW_ASSET: _assets_show, links.ASSET_FILES: _asset_files},
            quiet_links=frozenset({links.ASSET_FILES})),
    AppSpec("ui2205", N_("UI 2205"), "app_ui", N_("the Flash UI editor: Anno 2205's interface movies, edited and built"),
            _ui2205),
    AppSpec("viewer", N_("Viewer"), "app_viewer", N_("the asset viewer: any game's models, textures, effects and sounds"),
            _viewer, glformat.prepare, {links.VIEW_FILE: _viewer_open, links.EXPORT_FILES: _viewer_export}),
    AppSpec("ui", N_("UI"), "app_phoenix", N_("the UI editor: Anno 117's and 1800's interface files, previewed, bound, edited"),
            _ui),
    AppSpec("mods", N_("Mods"), "app_mods", N_("the mod manager: Anno 117's and 1800's mods, profiles, load order, problems"),
            _mods, links={links.SHOW_MODS: _mods_show}),
    AppSpec("objects", N_("Objects"), "app_objects", N_("the object builder: a model config's parts, materials, sequences and boxes edited live, "
                                                      "saved as a mod"),
            _objects, glformat.prepare, {links.EDIT_OBJECT: _objects_open}),
    AppSpec("sound", N_("Sound"), "app_sound", N_("the sound editor: Wwise banks of Anno 117, 1800 and 2205, edited and saved "
                                                  "as a mod"),
            _sound),
    AppSpec("texts", N_("Texts"), "app_texts", N_("the text editor: a game's texts in two languages side by side, a translation "
                                                  "checked, saved as a mod"),
            _texts),
    AppSpec("moddiff", N_("Mod diff"), "app_moddiff", N_("the mod diff: what a mod changes, two versions, conflicts between mods, "
                                                        "archive mods against the game"),
            _moddiff, links={links.DIFF_MODS: _moddiff_open}),
    AppSpec("maps", N_("Maps"), "app_maps", N_("the map editor: a game's sessions as maps, islands and slots placed, grown on "
                                                "any side, saved as a mod"),
            _maps),
    AppSpec("saves", N_("Saves"), "app_saves", N_("the savegame editor: every game's saves read, the known values changed, "
                                                  "written as a new file beside the original"),
            _saves),
    AppSpec("particles", N_("Particles"), "app_particles", N_("the particle editor: any game's effects, their emitters, "
                                                              "curves and colours edited, played live, saved as a mod"),
            _particles, glformat.prepare, {links.EDIT_EFFECT: _particles_open}),
)  # fmt: skip


def available() -> list[AppSpec]:
    """The built-in apps, then the ones an extension registers (``features.APPS``)."""
    return [*BUILTIN, *features.extensions(features.APPS).values()]


def parts() -> list[project.Part]:
    """Each app's share of a project's mod, in the order the build merges them (islands, assets, the 2205 UI, the
    117 and 1800 UI, sound, the imported models, the object builder's configs, the effects, the texts, the maps)."""
    from ..assetedit import projectpart as assets
    from ..editor import projectpart as islands
    from ..flashui import projectpart as ui2205
    from ..mapedit import projectpart as maps
    from ..objbuild import projectpart as objects
    from ..particleedit import projectpart as particles
    from ..soundedit import projectpart as sound
    from ..textedit import projectpart as texts
    from ..uiedit import projectpart as ui
    from ..viewer import projectpart as models

    return [
        islands.PART,
        assets.PART,
        ui2205.PART,
        ui.PART,
        sound.PART,
        models.PART,
        objects.PART,
        particles.PART,
        texts.PART,
        maps.PART,
    ]


def deliveries() -> dict[str, project.Delivery]:
    """How the games that load archives take a project (``suite.project.Delivery``)."""
    from ..editor.archivedelivery import DELIVERIES

    return dict(DELIVERIES)
