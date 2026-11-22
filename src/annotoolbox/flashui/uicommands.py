"""The Flash UI editor's commands: list and look into Anno 2205's UI movies, record edits, build and install a mod.

``register(sub)`` adds the subcommands to an argparse group (the editor's own entry, ``python -m`` on this
package, and any host program use the same); each ``cmd_*`` is one subcommand. The game folder comes from
``--game`` or the ``ANNO2205_DIR`` environment variable (docs/formats/anno2205-ui-format.md, "Tools").
"""

from __future__ import annotations

import argparse
import os
import sys
import zlib
from pathlib import Path

from ..formats import feuabc, feuedit, feuread, feuwrite
from ..othergames import ui2205
from ..othergames.installs import GAMES
from . import uiexport
from .uiproject import CProject, changes


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def game_folder(args: argparse.Namespace) -> Path:
    folder = getattr(args, "game", None) or os.environ.get("ANNO2205_DIR")
    if not folder:
        raise SystemExit("error: no game folder: --game <Anno 2205 folder> or the ANNO2205_DIR environment variable")
    path = Path(folder)
    if not GAMES["anno2205"].is_install(path):
        raise SystemExit(f"error: {path} is not an Anno 2205 install (no Bin/Win64/Anno2205.exe)")
    return path


def open_ui(args: argparse.Namespace) -> ui2205.CUiFiles:
    return ui2205.CUiFiles(GAMES["anno2205"].tree(game_folder(args)))


def movie_path(name: str) -> str:
    """``title_screen``, ``title_screen.feu`` or ``data/ui/title_screen.feu`` -> the game path."""
    name = name.replace("\\", "/").lower()
    if not name.startswith(ui2205.UI):
        name = ui2205.UI + name
    return name if name.endswith(".feu") else name + ".feu"


def _load(path: Path) -> CProject:
    return CProject.load(path) if path.is_file() else CProject(path)


def cmd_list(args: argparse.Namespace) -> int:
    ui_files = open_ui(args)
    for path in ui_files.movies():
        movie = ui_files.movie(path)
        print(f"{path}  v{movie.version}  {len(feuedit.texts(movie))} texts  {len(feuedit.images(movie))} images  "
              f"{len(feuedit.fonts(movie))} fonts")  # fmt: skip
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    ui_files = open_ui(args)
    movie = ui_files.movie(movie_path(args.movie))
    min_x, max_x, min_y, max_y = movie.stage
    print(f"signature {movie.signature.decode()}  version {movie.version}  stage {(max_x - min_x) / 20:g}x{(max_y - min_y) / 20:g}  "
          f"{movie.rate:g} fps  {movie.frame_count} frames  {len(movie.tags)} tags")  # fmt: skip
    for font in feuedit.fonts(movie):
        print(f"font {font.font_id}: {font.name} ({font.file})")
    for code, image in feuedit.images(movie):
        print(
            f"{'icon' if code == feuread.ATLAS_IMAGE else 'image'} {image.char_id}: {image.path} {image.width}x{image.height}"
        )
    for text in feuedit.texts(movie):
        print(f"text {text.char_id}: {text.text!r} font {text.font_id} {text.variable}".rstrip())
    for block, tag in enumerate(feuedit.abc_tags(movie)):
        strings = feuabc.strings(feuread.read_abc(tag).abc)
        print(f"code block {block}: {len(strings)} strings")
        if args.strings:
            for i, string in enumerate(strings, 1):
                print(f"  {i}: {string!r}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """The movie (and for a Flash tool, its re-signed copy) and the pictures it names."""
    ui_files = open_ui(args)
    path = movie_path(args.movie)
    raw = ui_files.file(path)
    out = Path(args.to)
    out.mkdir(parents=True, exist_ok=True)
    stem = path.rsplit("/", 1)[-1][: -len(".feu")]
    (out / f"{stem}.feu").write_bytes(raw)
    (out / f"{stem}.swf").write_bytes(feuwrite.resign(raw, b"FWS"))
    count = 0
    for _, image in feuedit.images(feuread.read(raw)):
        data = ui_files.image(image.path)
        if data is not None:
            target = out / "pictures" / uiexport.safe_name(ui2205.image_path(image.path)[len(ui2205.UI) :])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
    print(f"{stem}.feu, {stem}.swf (for a Flash tool) and {count} pictures in {out}")
    return 0


def cmd_export_images(args: argparse.Namespace) -> int:
    """Pictures as PNG files below a folder, with an index (``uiexport``): movies', shared, atlas icons."""
    exporter = uiexport.CExporter(open_ui(args))
    items: list[uiexport.SItem] = []
    if args.all_movies:
        items += exporter.all_movie_items()
    else:
        for movie in args.movie or []:
            items += exporter.movie_items(movie_path(movie), uiexport.movie_stem(movie_path(movie)) + "/")
    if args.pictures or args.shared:
        items += exporter.shared_items()
    if args.atlases or args.shared:
        items += exporter.atlas_items()
    for ref in args.picture or []:
        items.append(exporter.item_by_name(ref))
    if not items:
        return _fail("nothing to export: give --movie, --all-movies, --pictures, --atlases, --shared or --picture")
    done = exporter.export(items, Path(args.to), lambda n, total, name: n % 250 == 0 and print(f"{n}/{total} {name}"))
    print(f"{uiexport.summary(done)} in {args.to}")
    return 0


def cmd_to_swf(args: argparse.Namespace) -> int:
    Path(args.output).write_bytes(feuwrite.resign(Path(args.input).read_bytes(), b"FWS"))
    return 0


def cmd_from_swf(args: argparse.Namespace) -> int:
    """A movie saved by a Flash tool back to the game's signature (a compressed one is unpacked first)."""
    raw = Path(args.input).read_bytes()
    try:
        if raw[:3] == b"CWS":
            raw = b"FWS" + raw[3:8] + zlib.decompress(raw[8:])
        if raw[:3] not in feuread.FLASH_SIGNATURES:
            return _fail(f"{args.input}: not an uncompressed or zlib-compressed Flash movie ({raw[:3]!r})")
        data = feuwrite.resign(raw, feuread.SIGNATURE)
        feuread.read(data)  # it must read as the game's movies do
    except (zlib.error, feuread.CFeuError) as exc:
        return _fail(f"{args.input}: {exc}")
    Path(args.output).write_bytes(data)
    return 0


def _replacement(name: str) -> str:
    """A replacement file as the edit file keeps it: absolute, as typed it is read from the working folder, while the
    edit file's names are read from its own folder (``CProject.source``)."""
    return str(Path(name).resolve())


def cmd_set(args: argparse.Namespace) -> int:
    """One edit or replacement into the edit file."""
    project = _load(Path(args.edits))
    if args.what == "text":
        project.add(movie_path(args.movie), feuedit.SEdit("text", args.id, {"text": args.value}))
    elif args.what == "font":
        project.add(movie_path(args.movie), feuedit.SEdit("font", args.id, {"file": args.value}))
    elif args.what == "picture":
        project.m_files[args.movie.lower()] = _replacement(args.value)
    elif args.what == "icon":
        project.m_icons[args.movie] = _replacement(args.value)
    elif args.what == "movie":
        project.m_files[movie_path(args.movie)] = _replacement(args.value)
    print(f"saved {project.save()}")
    return 0


def _out(args: argparse.Namespace) -> tuple[Path, Path]:
    """The build, and where an earlier version kept the game's ``file.db`` (``delivery2205`` moves it out)."""
    base = Path(args.out).resolve()
    return base / ui2205.OUT_NAME, base / f"{ui2205.OUT_NAME}-kept" / "file.db"


def cmd_build(args: argparse.Namespace) -> int:
    edits = Path(args.edits)
    if not edits.is_file():
        return _fail(f"{edits}: no such edit file")
    ui_files = open_ui(args)
    project = CProject.load(edits)
    files = changes(project, ui_files)
    if not files:
        print("the edit file changes nothing")
        return 0
    built, backup = _out(args)
    if args.loose:
        for path in ui2205.write_loose(files, built / "loose"):
            print(path)
        return 0
    ui2205.build(files, ui2205.base_index(game_folder(args), backup), built)
    for path in sorted(files):
        print(f"  {path}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    built, backup = _out(args)
    ui2205.install(built, game_folder(args), backup)
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    _, backup = _out(args)
    ui2205.uninstall(game_folder(args), backup)
    return 0


def cmd_editor(args: argparse.Namespace) -> int:
    from .uiwindow import run

    folder = getattr(args, "game", None) or os.environ.get("ANNO2205_DIR") or ""
    return run(folder, args.edits)


def register(sub) -> None:
    """The subcommands, into an ``add_subparsers`` group."""

    def add(name: str, func, help_: str) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=help_)
        parser.add_argument("--game", help="the Anno 2205 folder (default: ANNO2205_DIR)")
        parser.set_defaults(func=func)
        return parser

    add("list", cmd_list, "the UI movies of the install")
    parser = add("info", cmd_info, "a movie's fonts, images, texts and code")
    parser.add_argument("movie")
    parser.add_argument("--strings", action="store_true", help="list the code's string constants")
    parser = add("extract", cmd_extract, "a movie, its copy for a Flash tool and its pictures")
    parser.add_argument("movie")
    parser.add_argument("--to", required=True)
    parser = add("export-images", cmd_export_images, "pictures as PNG files with an index")
    parser.add_argument("--to", required=True, help="the folder to write into")
    parser.add_argument("--movie", action="append", help="a movie's pictures (a sub-folder per movie; repeatable)")
    parser.add_argument("--all-movies", action="store_true", help="every movie's pictures")
    parser.add_argument("--pictures", action="store_true", help="the shared pictures the movies point at (shared/)")
    parser.add_argument("--atlases", action="store_true", help="the atlases cut into their icons (atlases/<atlas>/)")
    parser.add_argument("--shared", action="store_true", help="--pictures and --atlases")
    parser.add_argument("--picture", action="append", help="one picture: its game path or a movie's image name")
    for name, func, what in (("to-swf", cmd_to_swf, "for a Flash tool"), ("from-swf", cmd_from_swf, "for the game")):
        parser = add(name, func, f"re-sign a movie {what}")
        parser.add_argument("input")
        parser.add_argument("output")
    parser = add("set", cmd_set, "record an edit in an edit file")
    parser.add_argument("edits", help="the edit file (JSON; created when missing)")
    parser.add_argument("what", choices=("text", "font", "picture", "icon", "movie"))
    parser.add_argument("movie", help="the movie; for picture the picture's game path, for icon the atlas icon's name")
    parser.add_argument("value", help="the text, the font file, or the file that replaces")
    parser.add_argument("--id", type=int, default=0, help="the text field's or font's id (text, font)")
    for name, func, what in (("build", cmd_build, "build"), ("install", cmd_install, "install"),
                             ("uninstall", cmd_uninstall, "uninstall")):  # fmt: skip
        parser = add(name, func, f"{what} the mod (our archive and the archive index)")
        if name == "build":
            parser.add_argument("edits")
            parser.add_argument("--loose", action="store_true", help="loose files instead of the archive")
        parser.add_argument("--out", required=True, help="the folder the build goes into")
    parser = add("editor", cmd_editor, "the editor window")
    parser.add_argument("edits", nargs="?", help="an edit file to open")
