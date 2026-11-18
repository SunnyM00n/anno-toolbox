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
    keys = sorted({f.key for k in kinds.KINDS for f in kinds.formats_for(k)})
    sp = sub.add_parser("export", help="export game files as stored and in converted forms (PNG, glTF, WAV, JSON, ...)")
    sp.add_argument("inputs", nargs="+", type=Path, help="files, or folders searched recursively")
    sp.add_argument("-o", "--out", type=Path, required=True, help="the output folder (index.json and index.csv in it)")
    sp.add_argument("--format", default="raw", help=f"comma separated forms: {', '.join(keys)} (default: raw)")
    sp.add_argument("--kind", default="", help="only files of this kind (model, texture, sound, ...)")
    sp.add_argument("--force", action="store_true", help="overwrite files that exist")
    sp.add_argument("--all-mips", action="store_true", help="a texture's every mip level, not the largest")
    sp.add_argument("--hdr-form", choices=("exr", "hdr"), default="exr", help="the form of a float texture")
    sp.add_argument(
        "--rdm-target",
        choices=("anno2070", "anno2205", "anno1800", "anno117"),
        default="anno117",
        help="the game a Granny .gr2's rdm form is written for",
    )
    sp.set_defaults(func=cmd_export)


def _sources(inputs: list[Path]) -> list[tuple[run.FolderSource, list[str]]]:
    """One folder source per input: a file is read from its folder, a folder's files by their path inside it."""
    out = []
    for p in inputs:
        if p.is_dir():
            src = run.FolderSource(p)
            out.append((src, src.under("")))
        elif p.is_file():
            out.append((run.FolderSource(p.parent), [p.name]))
        else:
            raise FileNotFoundError(p)
    return out


def cmd_export(args: argparse.Namespace) -> int:
    formats = {f.strip() for f in args.format.split(",") if f.strip()}
    options = run.Options(all_mips=args.all_mips, hdr_form=args.hdr_form, rdm_target=args.rdm_target, force=args.force)
    if message := run.missing_tools(formats):
        print(message)
    entries: list[run.Entry] = []
    for source, paths in _sources(args.inputs):
        paths = [p for p in paths if not args.kind or kinds.kind_of(p) == args.kind]
        for path in paths:
            entries += run.export_path(source, path, formats, args.out, options)
    args.out.mkdir(parents=True, exist_ok=True)
    write_index(args.out, entries)
    for e in entries:
        print(f"{e.status.upper():8} {e.source} -> {', '.join(e.files) or '-'}")
        for note in e.notes:
            print(f"  - {note}")
    print(f"{len(entries)} files; index in {args.out}")
    return 1 if any(e.status == "failed" for e in entries) else 0
