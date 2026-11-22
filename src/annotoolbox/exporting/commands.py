"""The ``export`` command: files and folders of loose game files into their stored and converted forms, with an index.

``register(sub)`` adds it to a parser (the Toolbox's command line); ``cmd_export`` is its handler. A file given
on the command line is exported under its own name, a folder's files under their path inside it. The forms are
``--format`` keys (``exporting.kinds``): ``raw`` is always made. docs/exporting.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import kinds, run
from .index import write_index


def register(sub: argparse._SubParsersAction) -> None:
    keys = sorted({form.key for k in kinds.KINDS for form in kinds.formats_for(k)})
    parser = sub.add_parser(
        "export", help="export game files as stored and in converted forms (PNG, glTF, WAV, JSON, ...)"
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="files, or folders searched recursively")
    parser.add_argument(
        "-o", "--out", type=Path, required=True, help="the output folder (index.json and index.csv in it)"
    )
    parser.add_argument("--format", default="raw", help=f"comma separated forms: {', '.join(keys)} (default: raw)")
    parser.add_argument("--kind", default="", help="only files of this kind (model, texture, sound, ...)")
    parser.add_argument("--force", action="store_true", help="overwrite files that exist")
    parser.add_argument("--all-mips", action="store_true", help="a texture's every mip level, not the largest")
    parser.add_argument("--hdr-form", choices=("exr", "hdr"), default="exr", help="the form of a float texture")
    parser.add_argument(
        "--rdm-target",
        choices=("anno2070", "anno2205", "anno1800", "anno117"),
        default="anno117",
        help="the game a Granny .gr2's rdm form is written for",
    )
    parser.set_defaults(func=cmd_export)


def _sources(inputs: list[Path]) -> list[tuple[run.CFolderSource, list[str]]]:
    """One folder source per input: a file is read from its folder, a folder's files by their path inside it."""
    out = []
    for input_path in inputs:
        if input_path.is_dir():
            source = run.CFolderSource(input_path)
            out.append((source, source.under("")))
        elif input_path.is_file():
            out.append((run.CFolderSource(input_path.parent), [input_path.name]))
        else:
            raise FileNotFoundError(input_path)
    return out


def cmd_export(args: argparse.Namespace) -> int:
    formats = {form.strip() for form in args.format.split(",") if form.strip()}
    options = run.SOptions(all_mips=args.all_mips, hdr_form=args.hdr_form, rdm_target=args.rdm_target, force=args.force)
    if message := run.missing_tools(formats):
        print(message)
    entries: list[run.SEntry] = []
    claimed: dict[str, str] = {}  # the names made so far: two files making one name never overwrite each other
    for source, paths in _sources(args.inputs):
        paths = [file_path for file_path in paths if not args.kind or kinds.kind_of(file_path) == args.kind]
        for path in paths:
            entries += run.export_path(source, path, formats, args.out, options, claimed)
    args.out.mkdir(parents=True, exist_ok=True)
    write_index(args.out, entries)
    for entry in entries:
        print(f"{entry.status.upper():8} {entry.source} -> {', '.join(entry.files) or '-'}")
        for note in entry.notes:
            print(f"  - {note}")
    print(f"{len(entries)} files; index in {args.out}")
    return 1 if any(entry.status == "failed" for entry in entries) else 0
