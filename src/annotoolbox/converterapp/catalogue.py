"""The entries of the Converter app: every command of the command line that turns a single file, asset or island into
another format, by category, and what an extension adds (``features.CONVERSIONS``).

``BASELINE`` are this edition's own; ``entries()`` adds the registered ones and ``by_category()`` groups them in the
order of ``CATEGORIES``. Each entry names its command's words: the form is made from that command's own parser
(``commandform``). The table of every entry and its command: docs/converter-app.md.
"""

from __future__ import annotations

from ..infra.i18n import N_
from ..suite import features
from ..suite.extspecs import SConversion

CATEGORIES = {
    "models": N_("convert.cat.models"),
    "textures": N_("convert.cat.textures"),
    "sounds": N_("convert.cat.sounds"),
    "videos": N_("convert.cat.videos"),
    "ui": N_("convert.cat.ui"),
    "texts": N_("convert.cat.texts"),
    "islands": N_("convert.cat.islands"),
    "archives": N_("convert.cat.archives"),
    "data": N_("convert.cat.data"),
    "assets": N_("convert.cat.assets"),
}
# Each entry's title and summary, by its key (written out: the language files' check reads the ids from the code).
TEXTS = {
    "rdm_gltf": (N_("convert.rdm_gltf"), N_("convert.rdm_gltf_summary")),
    "gltf_rdm": (N_("convert.gltf_rdm"), N_("convert.gltf_rdm_summary")),
    "rdm_relayout": (N_("convert.rdm_relayout"), N_("convert.rdm_relayout_summary")),
    "granny_rdm": (N_("convert.granny_rdm"), N_("convert.granny_rdm_summary")),
    "granny_gltf": (N_("convert.granny_gltf"), N_("convert.granny_gltf_summary")),
    "gltf_granny": (N_("convert.gltf_granny"), N_("convert.gltf_granny_summary")),
    "texture_png": (N_("convert.texture_png"), N_("convert.texture_png_summary")),
    "picture_png": (N_("convert.picture_png"), N_("convert.picture_png_summary")),
    "texture_bc7": (N_("convert.texture_bc7"), N_("convert.texture_bc7_summary")),
    "sound_audio": (N_("convert.sound_audio"), N_("convert.sound_audio_summary")),
    "bank_audio": (N_("convert.bank_audio"), N_("convert.bank_audio_summary")),
    "audio_wem": (N_("convert.audio_wem"), N_("convert.audio_wem_summary")),
    "movie_video": (N_("convert.movie_video"), N_("convert.movie_video_summary")),
    "phoenix_json": (N_("convert.phoenix_json"), N_("convert.phoenix_json_summary")),
    "flash_movie": (N_("convert.flash_movie"), N_("convert.flash_movie_summary")),
    "loca_csv": (N_("convert.loca_csv"), N_("convert.loca_csv_summary")),
    "island_maps": (N_("convert.island_maps"), N_("convert.island_maps_summary")),
    "island_port": (N_("convert.island_port"), N_("convert.island_port_summary")),
    "rda_extract": (N_("convert.rda_extract"), N_("convert.rda_extract_summary")),
    "rda_pack": (N_("convert.rda_pack"), N_("convert.rda_pack_summary")),
    "data_json": (N_("convert.data_json"), N_("convert.data_json_summary")),
    "effect_json": (N_("convert.effect_json"), N_("convert.effect_json_summary")),
}
RDM_GAMES = ("anno117", "anno1800", "anno2205", "anno2070")
GRANNY_GAMES = ("anno1404", "anno1701")
ALL_GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")


def _rdm(sub) -> None:
    from ..rdm import commands

    commands.register(sub)


def _export(sub) -> None:
    from ..exporting import commands

    commands.register(sub)


def _rda(sub) -> None:
    from ..exporting import rdacommands

    rdacommands.register(sub)


def _island(sub) -> None:
    from ..editor import islandcommands

    islandcommands.register(sub)


def _flash(sub) -> None:
    from ..flashui import uicommands

    uicommands.register(sub)


def _own(sub) -> None:
    from . import commands

    commands.register(sub)


EXPORT_OPTIONS = frozenset({"rdm_target", "all_mips", "hdr_form", "kind"})  # what only some kinds of export read


def exported(
    key: str, category: str, kind: str, forms: tuple[str, ...], games=ALL_GAMES, keep=frozenset()
) -> SConversion:
    """An entry of the ``export`` command for one kind of file (``exporting.kinds``) in one of ``forms``."""
    from ..exporting import kinds

    return entry(key, category, ("export",), _export, ("export",), suffixes=kinds.suffixes(kind), games=games,
                 fixed=("--kind", kind), choices={"format": forms}, hidden=EXPORT_OPTIONS - keep)  # fmt: skip


def _rdm_entry(key: str, word: str, suffixes: tuple[str, ...], games=RDM_GAMES, **more) -> SConversion:
    return entry(key, "models", ("rdm", word), _rdm, (word,), suffixes=suffixes, games=games, **more)


def entry(key: str, category: str, command: tuple[str, ...], register, words: tuple[str, ...], **more) -> SConversion:
    """An entry with its title and summary from ``TEXTS``."""
    return SConversion(key, category, *TEXTS[key], command, register, words, **more)


def _flash_movies(settings) -> list[tuple[str, str]]:
    """2205's UI movies, for the source list (the game's archives are read: off the window's thread)."""
    from pathlib import Path

    from ..othergames import ui2205
    from ..othergames.installs import GAMES

    if not settings.anno2205:
        return []
    return [(movie, movie) for movie in ui2205.CUiFiles(GAMES["anno2205"].tree(Path(settings.anno2205))).movies()]


def _region_ids() -> tuple[str, ...]:
    from ..islands import regions

    return tuple(region.id for region in regions.REGIONS)


def port_values(settings, values: dict, game: str) -> dict:
    """The island port's game and installs from the settings: the source game's and the target region's."""
    from ..islands import regions

    target = next((region for region in regions.REGIONS if region.id == values.get("to")), None)
    out = {"game": game, "src": getattr(settings, game, "")}
    if target is not None and target.game != game:
        out["dst"] = getattr(settings, target.game, "")
    return out


def baseline() -> list[SConversion]:
    """This edition's entries, in the order the app lists them within their category."""

    def own(key, category, words, suffixes, **more):
        return entry(key, category, words, _own, words, suffixes=suffixes, **more)

    from . import commands

    return [
        _rdm_entry("rdm_gltf", "export", (".rdm",)),
        _rdm_entry("gltf_rdm", "import", (".glb", ".gltf"), games=()),
        _rdm_entry("rdm_relayout", "relayout", (".rdm",), folders={"anno117": "anno117"}),
        _rdm_entry("granny_rdm", "from-granny", (".gr2",), games=GRANNY_GAMES, folders={"anno117": "anno117"}),
        exported("granny_gltf", "models", "model", ("glb", "gltf"), games=GRANNY_GAMES),
        _rdm_entry("gltf_granny", "granny", (".glb", ".gltf"), games=(), inputs="input", output_file=".gr2"),
        exported("texture_png", "textures", "texture", ("png", "png16", "exr", "hdr"), keep={"all_mips", "hdr_form"}),
        exported("picture_png", "textures", "picture", ("png",)),
        own("texture_bc7", "textures", ("texture", "bc7"), (*commands.PICTURES, ".dds")),
        exported("sound_audio", "sounds", "sound", ("wav", "ogg")),
        exported("bank_audio", "sounds", "bank", ("wav", "ogg", "json")),
        own("audio_wem", "sounds", ("sound", "wem"), commands.AUDIO),
        exported("movie_video", "videos", "movie", ("mp4", "webm")),
        exported("phoenix_json", "ui", "ui", ("json",), games=("anno117", "anno1800")),
        entry(
            "flash_movie",
            "ui",
            ("flashui", "extract"),
            _flash,
            ("extract",),
            inputs="movie",
            output="to",
            source="values",
            games=("anno2205",),
            folders={"game": "anno2205"},
            output_subfolder=True,
            candidates=_flash_movies,
        ),
        exported("loca_csv", "texts", "text", ("csv",)),
        exported("island_maps", "islands", "island", ("json", "csv", "png16", "exr")),
        entry(
            "island_port",
            "islands",
            ("island", "port"),
            _island,
            ("port",),
            inputs="islands",
            source="game_paths",
            suffixes=(".a7m",),
            games=("anno117", "anno1800"),
            per_input=False,
            choices={"to": _region_ids()},
            hidden=frozenset({"game"}),  # the game chosen above the sources
            complete=port_values,
        ),
        entry(
            "rda_extract",
            "archives",
            ("rda", "extract"),
            _rda,
            ("rda", "extract"),
            inputs="archive",
            suffixes=(".rda",),
            output_subfolder=True,
        ),
        entry(
            "rda_pack",
            "archives",
            ("rda", "pack"),
            _rda,
            ("rda", "pack"),
            inputs="folder",
            output="archive",
            source="folder",
            output_file=".rda",
        ),
        exported("data_json", "data", "data", ("json", "csv")),
        exported("effect_json", "data", "effect", ("json",)),
    ]


def entries() -> list[SConversion]:
    """This edition's entries, then what an extension registers (a registered key replaces this edition's)."""
    added = {conversion.key: conversion for conversion in features.extensions(features.CONVERSIONS).values()}
    return [added.pop(conversion.key, conversion) for conversion in baseline()] + list(added.values())


def by_category(found: list[SConversion]) -> list[tuple[str, list[SConversion]]]:
    """``found`` grouped in the order of ``CATEGORIES`` (a category with nothing in it left out)."""
    return [
        (category, [conversion for conversion in found if conversion.category == category])
        for category in CATEGORIES
        if any(conversion.category == category for conversion in found)
    ]
