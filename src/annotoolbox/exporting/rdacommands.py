"""The ``rda`` command group: look into, unpack and pack Anno ``.rda`` archives (V1.1, V2.0, V2.2).

``register(sub)`` adds it to a parser (the Toolbox's ``rda`` group); ``formats/rda.py`` does the work; the variants
and their flags are in ``docs/rda-format.md``.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
from pathlib import Path

from ..formats import rda

FLAG_NAMES = {
    "stored": 0,
    "compressed": rda.FLAG_COMPRESSED,
    "encrypted": rda.FLAG_ENCRYPTED,
    "resident": rda.FLAG_CONTIGUOUS,
    "deleted": rda.FLAG_DELETED,
}
DEFAULT_FLAGS = {"1.1": "compressed", "2.0": "compressed,encrypted", "2.2": "compressed"}


def register(sub: argparse._SubParsersAction) -> None:
    rda_parser = sub.add_parser("rda", help="list, unpack and pack Anno .rda archives (docs/rda-format.md)")
    rsub = rda_parser.add_subparsers(dest="rda_command", required=True)

    list_parser = rsub.add_parser("list", help="the paths of an archive (the version is detected)")
    list_parser.add_argument("archive", type=Path)
    list_parser.add_argument(
        "pattern", nargs="*", help="only paths matching one of these shell patterns (case-sensitive)"
    )
    list_parser.add_argument("--long", action="store_true", help="flags, sizes and modification time before each path")
    list_parser.add_argument("--deleted", action="store_true", help="list the paths of its deleted blocks instead")
    list_parser.set_defaults(func=cmd_rda_list)

    info_parser = rsub.add_parser("info", help="the version, the blocks (per flags) and the counts of an archive")
    info_parser.add_argument("archive", type=Path)
    info_parser.add_argument("--blocks", action="store_true", help="one line per block")
    info_parser.set_defaults(func=cmd_rda_info)

    extract_parser = rsub.add_parser("extract", help="write the members of an archive under a folder")
    extract_parser.add_argument("archive", type=Path)
    extract_parser.add_argument("out", type=Path, help="the folder the members are written under")
    extract_parser.add_argument(
        "pattern", nargs="*", help="only paths matching one of these shell patterns (default: all)"
    )
    extract_parser.set_defaults(func=cmd_rda_extract)

    pack_parser = rsub.add_parser("pack", help="write a folder as an archive")
    pack_parser.add_argument("folder", type=Path)
    pack_parser.add_argument("archive", type=Path)
    pack_parser.add_argument(
        "--version", required=True, choices=sorted(DEFAULT_FLAGS), help="the archive version to write"
    )
    pack_parser.add_argument(
        "--flags",
        help="block flags, comma separated: stored, compressed, encrypted, resident (default: compressed,encrypted "
        "for 2.0, compressed otherwise; 1.1 takes stored or compressed)",
    )
    pack_parser.add_argument(
        "--level", type=int, default=1, choices=range(10), help="zlib level (default 1, the games' own)"
    )
    pack_parser.add_argument(
        "--mtime", type=int, default=None, help="one modification time (unix seconds) for every member"
    )
    pack_parser.add_argument(
        "--lowercase", action="store_true", help="lower-case the member paths (the game looks them up so)"
    )
    pack_parser.set_defaults(func=cmd_rda_pack)


def describe_flags(flags: int) -> str:
    names = [name for name, bit in FLAG_NAMES.items() if bit and flags & bit]
    return "+".join(names) or "stored"


def _matches(path: str, patterns: list[str]) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def cmd_rda_list(args: argparse.Namespace) -> int:
    arc = rda.CRdaArchive(args.archive)
    if args.deleted:
        for path in sorted(deleted_path for deleted_path in arc.deleted if _matches(deleted_path, args.pattern)):
            print(path)
        return 0
    for entry in arc:
        if _matches(entry.path, args.pattern):
            if args.long:
                print(
                    f"{describe_flags(entry.flags):22} {entry.size:>12} {entry.compressed_size:>12} {entry.mtime:>11} {entry.path}"
                )
            else:
                print(entry.path)
    return 0


def cmd_rda_info(args: argparse.Namespace) -> int:
    arc = rda.CRdaArchive(args.archive)
    print(f"{args.archive.name}: version {arc.version.name}, {len(arc)} members, {len(arc.deleted)} deleted paths")
    if arc.version is rda.V1_1:
        kinds = collections.Counter(describe_flags(entry.flags) for entry in arc)
        print("  member data: " + ", ".join(f"{count} {k}" for k, count in sorted(kinds.items())))
        return 0
    per_flags: collections.Counter[int] = collections.Counter()
    files: collections.Counter[int] = collections.Counter()
    for block in arc.blocks():
        per_flags[block.flags] += 1
        files[block.flags] += block.file_count
        if args.blocks:
            print(
                f"  block @{block.offset} {describe_flags(block.flags)} files={block.file_count} headers={block.headers_size}"
                f"/{block.headers_decoded_size} blob={block.blob_stored_size}/{block.blob_size} next={block.next_offset}"
            )
    for flags in sorted(per_flags):
        print(f"  flags {flags} ({describe_flags(flags)}): {per_flags[flags]} blocks, {files[flags]} files")
    return 0


def _safe_dest(out: Path, path: str) -> Path:
    """Where member ``path`` goes under ``out``; a path that leaves the folder is an error (archives can be hostile)."""
    root = out.resolve()
    dest = (root / path).resolve()
    if dest == root or not dest.is_relative_to(root):
        raise rda.CRdaError(f"member {path!r} would be written outside {out}")
    return dest


def cmd_rda_extract(args: argparse.Namespace) -> int:
    arc = rda.CRdaArchive(args.archive)
    paths = [entry.path for entry in arc if _matches(entry.path, args.pattern)]
    if args.pattern and not paths:
        raise rda.CRdaError(f"no member matches {' '.join(args.pattern)}")
    for path in paths:
        arc.extract(path, _safe_dest(args.out, path))
    print(f"extracted {len(paths)} of {len(arc)} members to {args.out}")
    return 0


def parse_flags(text: str) -> int:
    flags = 0
    for name in filter(None, (token.strip() for token in text.split(","))):
        if name not in FLAG_NAMES or name == "deleted":
            raise rda.CRdaError(f"unknown block flag {name!r} (stored, compressed, encrypted, resident)")
        flags |= FLAG_NAMES[name]
    return flags


def cmd_rda_pack(args: argparse.Namespace) -> int:
    if not args.folder.is_dir():
        raise FileNotFoundError(args.folder)
    flags = parse_flags(args.flags if args.flags is not None else DEFAULT_FLAGS[args.version])
    files = sorted(path for path in args.folder.rglob("*") if path.is_file())
    members: list[tuple[str, Path]] = []
    stamps: dict[str, int] = {}
    for path in files:
        name = path.relative_to(args.folder).as_posix()
        name = name.lower() if args.lowercase else name
        members.append((name, path))
        stamps[name] = int(path.stat().st_mtime)
    stamp = args.mtime if args.mtime is not None else stamps
    if args.version == "1.1":
        if flags & ~rda.FLAG_COMPRESSED:
            raise rda.CRdaError("V1.1 archives are stored or compressed (--flags stored|compressed)")
        rda.write_v11_file(args.archive, members, stamp, compress=bool(flags))
    else:
        resident = bool(flags & rda.FLAG_CONTIGUOUS)
        with rda.CRdaWriter(args.archive, rda.version_named(args.version), stamp, allow_resident=resident) as writer:
            writer.add_block(members, flags, args.level)
    print(f"wrote {len(members)} members to {args.archive} (V{args.version}, {describe_flags(flags)})")
    return 0
