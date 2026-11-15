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
    s = sub.add_parser("rda", help="list, unpack and pack Anno .rda archives (docs/rda-format.md)")
    rsub = s.add_subparsers(dest="rda_command", required=True)

    ls = rsub.add_parser("list", help="the paths of an archive (the version is detected)")
    ls.add_argument("archive", type=Path)
    ls.add_argument("pattern", nargs="*", help="only paths matching one of these shell patterns (case-sensitive)")
    ls.add_argument("--long", action="store_true", help="flags, sizes and modification time before each path")
    ls.add_argument("--deleted", action="store_true", help="list the paths of its deleted blocks instead")
    ls.set_defaults(func=cmd_rda_list)

    info = rsub.add_parser("info", help="the version, the blocks (per flags) and the counts of an archive")
    info.add_argument("archive", type=Path)
    info.add_argument("--blocks", action="store_true", help="one line per block")
    info.set_defaults(func=cmd_rda_info)

    ex = rsub.add_parser("extract", help="write the members of an archive under a folder")
    ex.add_argument("archive", type=Path)
    ex.add_argument("out", type=Path, help="the folder the members are written under")
    ex.add_argument("pattern", nargs="*", help="only paths matching one of these shell patterns (default: all)")
    ex.set_defaults(func=cmd_rda_extract)

    pk = rsub.add_parser("pack", help="write a folder as an archive")
    pk.add_argument("folder", type=Path)
    pk.add_argument("archive", type=Path)
    pk.add_argument("--version", required=True, choices=sorted(DEFAULT_FLAGS), help="the archive version to write")
    pk.add_argument(
        "--flags",
        help="block flags, comma separated: stored, compressed, encrypted, resident (default: compressed,encrypted "
        "for 2.0, compressed otherwise; 1.1 takes stored or compressed)",
    )
    pk.add_argument("--level", type=int, default=1, choices=range(10), help="zlib level (default 1, the games' own)")
    pk.add_argument("--mtime", type=int, default=None, help="one modification time (unix seconds) for every member")
    pk.add_argument("--lowercase", action="store_true", help="lower-case the member paths (the game looks them up so)")
    pk.set_defaults(func=cmd_rda_pack)


def describe_flags(flags: int) -> str:
    names = [name for name, bit in FLAG_NAMES.items() if bit and flags & bit]
    return "+".join(names) or "stored"


def _matches(path: str, patterns: list[str]) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(path, p) for p in patterns)


def cmd_rda_list(args: argparse.Namespace) -> int:
    arc = rda.RdaArchive(args.archive)
    if args.deleted:
        for path in sorted(p for p in arc.deleted if _matches(p, args.pattern)):
            print(path)
        return 0
    for e in arc:
        if _matches(e.path, args.pattern):
            if args.long:
                print(f"{describe_flags(e.flags):22} {e.size:>12} {e.compressed_size:>12} {e.mtime:>11} {e.path}")
            else:
                print(e.path)
    return 0


def cmd_rda_info(args: argparse.Namespace) -> int:
    arc = rda.RdaArchive(args.archive)
    print(f"{args.archive.name}: version {arc.version.name}, {len(arc)} members, {len(arc.deleted)} deleted paths")
    if arc.version is rda.V1_1:
        kinds = collections.Counter(describe_flags(e.flags) for e in arc)
        print("  member data: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        return 0
    per_flags: collections.Counter[int] = collections.Counter()
    files: collections.Counter[int] = collections.Counter()
    for b in arc.blocks():
        per_flags[b.flags] += 1
        files[b.flags] += b.num_files
        if args.blocks:
            print(
                f"  block @{b.offset} {describe_flags(b.flags)} files={b.num_files} headers={b.headers_size}"
                f"/{b.headers_decoded_size} blob={b.blob_stored_size}/{b.blob_size} next={b.next_offset}"
            )
    for flags in sorted(per_flags):
        print(f"  flags {flags} ({describe_flags(flags)}): {per_flags[flags]} blocks, {files[flags]} files")
    return 0


def _safe_dest(out: Path, path: str) -> Path:
    """Where member ``path`` goes under ``out``; a path that leaves the folder is an error (archives can be hostile)."""
    root = out.resolve()
    dest = (root / path).resolve()
    if dest == root or not dest.is_relative_to(root):
        raise rda.RdaError(f"member {path!r} would be written outside {out}")
    return dest


def cmd_rda_extract(args: argparse.Namespace) -> int:
    arc = rda.RdaArchive(args.archive)
    paths = [e.path for e in arc if _matches(e.path, args.pattern)]
    if args.pattern and not paths:
        raise rda.RdaError(f"no member matches {' '.join(args.pattern)}")
    for path in paths:
        arc.extract(path, _safe_dest(args.out, path))
    print(f"extracted {len(paths)} of {len(arc)} members to {args.out}")
    return 0


def parse_flags(text: str) -> int:
    flags = 0
    for name in filter(None, (t.strip() for t in text.split(","))):
        if name not in FLAG_NAMES or name == "deleted":
            raise rda.RdaError(f"unknown block flag {name!r} (stored, compressed, encrypted, resident)")
        flags |= FLAG_NAMES[name]
    return flags


def cmd_rda_pack(args: argparse.Namespace) -> int:
    if not args.folder.is_dir():
        raise FileNotFoundError(args.folder)
    flags = parse_flags(args.flags if args.flags is not None else DEFAULT_FLAGS[args.version])
    files = sorted(p for p in args.folder.rglob("*") if p.is_file())
    members: list[tuple[str, Path]] = []
    stamps: dict[str, int] = {}
    for p in files:
        name = p.relative_to(args.folder).as_posix()
        name = name.lower() if args.lowercase else name
        members.append((name, p))
        stamps[name] = int(p.stat().st_mtime)
    stamp = args.mtime if args.mtime is not None else stamps
    if args.version == "1.1":
        if flags & ~rda.FLAG_COMPRESSED:
            raise rda.RdaError("V1.1 archives are stored or compressed (--flags stored|compressed)")
        rda.write_v11_file(args.archive, members, stamp, compress=bool(flags))
    else:
        resident = bool(flags & rda.FLAG_CONTIGUOUS)
        with rda.RdaWriter(args.archive, rda.version_named(args.version), stamp, allow_resident=resident) as w:
            w.add_block(members, flags, args.level)
    print(f"wrote {len(members)} members to {args.archive} (V{args.version}, {describe_flags(flags)})")
    return 0
