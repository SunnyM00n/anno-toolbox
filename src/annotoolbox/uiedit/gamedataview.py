"""The UI editor's game data (Qt side): View > Fill from the game's data, the resolver built off the window's thread
when a game opens, the scene and the language set before each layout, and the Properties rows that say where the
selected element's values came from.

``add_view_action`` fills the View menu; ``game_opened``, ``before_layout`` and ``origin_rows`` are the window's
calls. The resolver is ``gamedata``; docs/ui-editor.md "Game data in the preview".
"""

from __future__ import annotations

from ..infra.i18n import tr
from ..suite import widgets
from ..suite.settings import appdata
from . import elements, gamedata, gamelanguages, livevalues

SETTING = "game_data"  # the View switch, in the app's settings


def add_view_action(window, view_menu, app: str):
    """View > Fill from the game's data (kept in the app's settings, on by default)."""
    window.game_data = None
    return widgets.kept_switch(window, view_menu, tr("ui.fill_from_game_data"), lambda: toggled(window), (app, SETTING))


def toggled(window) -> None:
    window._when_idle(lambda: (_attach(window), window._redraw()))


def _attach(window) -> None:
    if window.m_ctx is not None:
        is_on = getattr(window, "game_data_action", None) is not None and window.game_data_action.isChecked()
        gamedata.attach(window.m_ctx, window.game_data if is_on else None)


def build(settings, game) -> gamedata.CGameData | None:
    """The resolver of an open game (off the window's thread): its assets through the asset editor's index, its text
    tables, the field names of its index folder and the user's, the data classes' kinds. None without assets."""
    from ..assetedit import assetgame

    index = assetgame.installed_index(settings, game.m_key, settings.workspace or appdata())
    if index is None:
        return None
    folder = assetgame.installed_folder(settings, game.m_key)
    assets = gamedata.CAssets.from_index(index, game.m_files.texts)
    from ..othergames import installs

    executable = installs.GAMES[game.m_key].exes[0] if game.m_key in installs.GAMES else ""
    table = gamedata.CFieldTable.load(settings.index_dir(game.m_key) / "ui", game=game.m_key,
                                     build=gamedata.installed_build(folder, executable) if executable else None)  # fmt: skip
    found = gamedata.CGameData(assets, table, gamedata.stub_kinds(game.m_models))
    found.m_groups = gamedata.stub_groups(game.m_models)
    found.m_classes = gamedata.class_fields(game.m_models)
    return found


def game_opened(window, game) -> None:
    """A game opened: its languages read now, its resolver built in the background (the preview lays out again
    with it once it is there)."""
    window.game_data, window.game_languages = None, gamelanguages.installed(game.m_tree)
    window.game_data_names = window.m_names.short()

    def built(found) -> None:
        if game is not window.game or isinstance(found, Exception) or found is None:
            return
        found.m_table.m_names.update(window.game_data_names)
        window.game_data = found
        window._when_idle(lambda: (_attach(window), window._redraw()))

    window.m_background.run(lambda: build(window.settings, game), built)


def before_layout(window) -> None:
    """The scene and the language of the next layout (``layout.Context.language``, the resolver's)."""
    ctx = window.m_ctx
    if ctx is None:
        return
    ctx.m_language = gamelanguages.chosen(getattr(ctx, "m_defaults", None), getattr(window, "game_languages", None))
    game = getattr(ctx, "m_game_data", None)
    if game is not None and window.doc is not None:
        game.m_scene, game.m_language = window.doc.m_key, ctx.m_language
    note = gamelanguages.live_note(livevalues.held(window) if window.game is not None else None, ctx.m_language)
    label = getattr(window, "values_label", None)
    if label is not None and note and tr("ui.live_language", language=note) not in label.text():
        label.setText(f"{label.text()} {tr('ui.live_language', language=note)}".strip())
    if label is not None and game is not None and game.m_table.stale and tr("ui.field_names_stale") not in label.text():
        label.setText(f"{label.text()} {tr('ui.field_names_stale')}".strip())


def origin_rows(window, element: elements.SElement | None) -> list[tuple[str, str, str]]:
    """The Properties rows for the selected element: (what it gets, the value, where the value came from)."""
    if element is None or not element.guid or window.doc is None or window.m_ctx is None:
        return []
    from . import bindings

    held = livevalues.held(window)
    live = ""
    ctx = window.m_ctx  # a running game's widget is matched by GUID or by its name CRC (``elements.keys``)
    if (
        elements.held_for(ctx.m_live_text, element) is not None
        or elements.held_for(ctx.m_live_values, element) is not None
    ):
        live = tr("ui.origin_recorded") if held is not None and held.recorded else tr("ui.origin_live")
    words = {"placeholder": tr("ui.origin_placeholder"), "sample": tr("ui.origin_sample")}
    rows = []
    for end, value, origin in gamedata.origins(window.doc.m_key, bindings.read(window.doc.m_root), element.guid,
                                               window.m_samples, live):  # fmt: skip
        if origin.startswith("game: "):
            origin = tr("ui.origin_game", where=origin[6:])
        rows.append((window._describe_end(end), "" if value is None else str(value), words.get(origin, origin)))
    return rows
