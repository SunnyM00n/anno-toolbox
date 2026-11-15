"""The RDM toolchain's commands: ``inspect``, ``diff``, ``export``, ``import``, ``validate``, ``relayout``, ``layouts``,
``granny`` (a glTF as a Granny ``.gr2`` for 1404 and 1701).

``register(sub)`` adds them to a parser (``anno-toolbox rdm ...``, and the Toolbox's ``rdm`` group); ``main`` is
the command line of the toolchain on its own. Every command takes files and folders (searched recursively); the
rules live in this package (docs/rdm-gltf.md, docs/commands.md "rdm").
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GLTF_SUFFIXES = (".glb", ".gltf")


def register(sub: argparse._SubParsersAction) -> None:
    _inspect(sub)
    _diff(sub)
    _export(sub)
    _import(sub)
    _validate(sub)
    _relayout(sub)
    _granny(sub)
    sp = sub.add_parser("layouts", help="list the vertex layouts the games ship and what uses them")
    sp.set_defaults(func=cmd_layouts)


def _granny(sub) -> None:
    gr = sub.add_parser(
        "granny",
        help="glTF 2.0 to a Granny .gr2 (Anno 1404, 1701): meshes, skeleton, animations (docs/rdm-gltf.md)",
    )
    gr.add_argument("input", type=Path, help="a .glb or .gltf")
    gr.add_argument("-o", "--out", type=Path, default=None, help="the .gr2 to write (default: beside the input)")
    gr.add_argument(
        "--game", choices=("anno1404", "anno1701"), default="anno1404", help="whose units: 1701 counts 32 a tile"
    )
    gr.add_argument("--axes", choices=("gltf", "anno"), default=None, help="as the glTF says, else gltf")
    gr.add_argument(
        "--place",
        default="",
        metavar="GAME_FOLDER",
        help="also write the model config and info file that place it (1701: .mcf, .info; 1404: .cfg, .ifo), "
        "naming the .gr2 as lying in this folder of the game's files, say data/graphics/objects/custom",
    )
    gr.add_argument("--force", action="store_true", help="overwrite a file that exists")
    gr.set_defaults(func=cmd_granny)


def _paths(sp: argparse.ArgumentParser, what: str) -> None:
    sp.add_argument("inputs", nargs="+", type=Path, help=f"{what} files, or folders searched recursively")


def _common_out(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("-o", "--out", type=Path, default=None, help="the output file (one input) or folder")
    sp.add_argument("--force", action="store_true", help="overwrite files that exist (default: leave them, say so)")


def _inspect(sub) -> None:
    sp = sub.add_parser("inspect", help="list every element of .rdm, .glb or .gltf files")
    _paths(sp, ".rdm, .glb or .gltf")
    sp.add_argument("--values", action="store_true", help="add a checksum of every vertex element, index list, track")
    sp.set_defaults(func=cmd_inspect)


def _diff(sub) -> None:
    sp = sub.add_parser("diff", help="compare two files element by element (a glTF as the .rdm its import gives)")
    sp.add_argument("a", type=Path)
    sp.add_argument("b", type=Path)
    sp.add_argument("--values", action="store_true", help="compare the values too (checksums), not only the structure")
    sp.set_defaults(func=cmd_diff)


def _export(sub) -> None:
    sp = sub.add_parser("export", help=".rdm to glTF 2.0: meshes, submeshes, materials, skeletons, animations")
    _paths(sp, ".rdm mesh")
    _common_out(sp)
    sp.add_argument(
        "--form",
        choices=("glb", "gltf", "gltfmin", "embedded"),
        default="glb",
        help="one .glb (default), .gltf + .bin (gltfmin: its JSON on one line), or .gltf with the buffer inside",
    )
    sp.add_argument("--anim", type=Path, action="append", default=[], help="an animation .rdm to include (repeatable)")
    sp.add_argument(
        "--anims", type=Path, default=None, help="a folder: include every animation file that moves the mesh"
    )
    sp.add_argument("--lods", action="store_true", help="include the mesh's other LOD files (<name>_lod1.rdm ...)")
    sp.add_argument(
        "--texture",
        action="append",
        default=[],
        metavar="[MATERIAL=]FILE",
        help="a PNG, JPEG or DDS for a material's base colour (all materials without MATERIAL=)",
    )
    sp.add_argument(
        "--axes",
        choices=("gltf", "anno"),
        default="gltf",
        help="gltf: mirrored on x into glTF's right-handed space (default); anno: the stored values",
    )
    sp.add_argument(
        "--strict",
        action="store_true",
        help="unit normals and tangents, weights summing to one, as glTF validators want (an import then rounds back within one step)",
    )
    sp.set_defaults(func=cmd_export)


def _import(sub) -> None:
    sp = sub.add_parser("import", help="glTF 2.0 to .rdm: the mesh file and its animation files")
    _paths(sp, ".glb or .gltf")
    _common_out(sp)
    sp.add_argument(
        "--layout",
        default=None,
        help="vertex layout of every mesh (rdm layouts); default: the one the file names or its attributes ask for",
    )
    sp.add_argument(
        "--like",
        type=Path,
        default=None,
        help="an .rdm whose layouts, material order, names and stored values to follow",
    )
    sp.add_argument("--mesh", default=None, help="only the glTF mesh of this index or name")
    sp.add_argument("--no-transform", action="store_true", help="do not bake the node transforms into static meshes")
    sp.add_argument(
        "--axes", choices=("gltf", "anno"), default=None, help="default: what the file says, else gltf (mirrored on x)"
    )
    sp.add_argument(
        "--material-order", default="", help="material names in the order the model's cfg lists them, comma separated"
    )
    sp.add_argument(
        "--joint-names",
        choices=("name", "index"),
        default="name",
        help="name joints by their node name (default) or their index",
    )
    sp.add_argument(
        "--fps",
        type=int,
        default=None,
        help="frame rate of animations that name none (default: from the keys, else 30)",
    )
    sp.set_defaults(func=cmd_import)


def _validate(sub) -> None:
    sp = sub.add_parser("validate", help="hold .rdm files against the conventions of Anno 117's own files")
    _paths(sp, ".rdm")
    sp.add_argument(
        "--layouts-from",
        type=Path,
        default=None,
        help="a folder of a game's own .rdm files: its layouts instead of the built-in table",
    )
    sp.add_argument("--loose-box", action="store_true", help="accept a stored box larger than the vertices' own")
    sp.set_defaults(func=cmd_validate)


def _relayout(sub) -> None:
    sp = sub.add_parser("relayout", help="rewrite .rdm files into the layouts 117 uses (a 2070 model for 117, say)")
    _paths(sp, ".rdm")
    _common_out(sp)
    sp.set_defaults(func=cmd_relayout)


# Handlers


def _read(path: Path):
    from ..formats.rdmread import read_rdm

    return read_rdm(path.read_bytes())


def _files(path: Path, opts=None) -> list[tuple[str, object]]:
    """The RDM files a path holds: an .rdm itself, a glTF as its import gives them."""
    if path.suffix.lower() not in GLTF_SUFFIXES:
        return [(path.name, _read(path))]
    from ..formats.gltf import read_gltf
    from . import gltfrdm

    return gltfrdm.import_gltf(read_gltf(path), path.with_suffix(".rdm").name, opts).files


def _inputs(paths: list[Path], suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for p in paths:
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.suffix.lower() in suffixes)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(p)
    return out


def cmd_inspect(args: argparse.Namespace) -> int:
    from ..formats.gltf import read_gltf
    from . import listing

    for path in _inputs(args.inputs, (".rdm", *GLTF_SUFFIXES)):
        print(f"== {path}")
        if path.suffix.lower() in GLTF_SUFFIXES:
            print("\n".join(listing.describe_gltf(read_gltf(path))))
            for name, rdm in _files(path):
                print(f"-- the import writes {name}:")
                print("\n".join(listing.describe(rdm, args.values)))
        else:
            print("\n".join(listing.describe(_read(path), args.values)))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from . import listing

    sides = []
    for path, other in ((args.a, args.b), (args.b, args.a)):
        files = _files(path)
        if len(files) > 1:  # a glTF of several files: the one named like the other side, else the first mesh file
            files = [f for f in files if f[0] == other.with_suffix(".rdm").name] or [f for f in files if f[1].meshes][
                :1
            ]
        sides.append([line for _name, rdm in files for line in listing.describe(rdm, args.values)])
    found = listing.diff(sides[0], sides[1], (str(args.a), str(args.b)))
    print("\n".join(found) if found else "the same in every element" + (" and value" if args.values else ""))
    return 1 if found else 0


def _animations_for(rdm, folder: Path | None, explicit: list[Path]) -> list[Path]:
    """The animation files to include: the named ones, and those in ``folder`` whose every keyed track of some
    entry names a joint of the mesh."""
    found = list(explicit)
    if folder is None:
        return found
    joints = {j.name for s in rdm.skeletons for j in s.joints}
    for path in sorted(folder.rglob("*.rdm")):
        try:
            anims = _read(path).animations
        except ValueError:
            continue
        for a in anims:
            keyed = [t.joint for t in a.tracks if t.keys]
            if keyed and all(j in joints for j in keyed) and path not in found:
                found.append(path)
                break
    return found


def _lod_chain(path: Path) -> list[Path]:
    m = re.fullmatch(r"(.*_lod)(\d)", path.stem)
    if not m:
        return [path]
    return [p for k in range(10) if (p := path.with_name(f"{m.group(1)}{k}{path.suffix}")).is_file()]


def _textures(specs: list[str]) -> tuple[list, list[str]]:
    from . import images

    out, notes = [], []
    for spec in specs:
        material, _, file = spec.rpartition("=") if "=" in spec and not Path(spec).is_file() else ("", "", spec)
        tex, note = images.texture(Path(file), material or None)
        out.append(tex)
        notes += [note] if note else []
    return out, notes


def cmd_export(args: argparse.Namespace) -> int:
    from . import batch, rdmgltf

    suffix = ".glb" if args.form == "glb" else ".gltf"
    textures, tex_notes = _textures(args.texture)
    opts = rdmgltf.Options(axes=args.axes, strict=args.strict)

    def work(job: batch.Job):
        paths = _lod_chain(job.source) if args.lods else [job.source]
        sources = [rdmgltf.Source(p.name, _read(p)) for p in paths]
        if not any(s.rdm.meshes for s in sources):
            return {}, ["no mesh: an animation file goes along with the mesh it moves (--anim, --anims)"]
        anims = [rdmgltf.Source(p.name, _read(p)) for p in _animations_for(sources[0].rdm, args.anims, args.anim)]
        b, notes = rdmgltf.export(sources, anims, textures, opts)
        return b.files(job.target, args.form), notes + tex_notes

    return _run(batch.jobs(args.inputs, args.out, (".rdm",), suffix), work, args.force)


def cmd_import(args: argparse.Namespace) -> int:
    from . import actions, batch
    from .gltfdoc import Options

    like = _read(args.like) if args.like else None
    order = tuple(n.strip() for n in args.material_order.split(",") if n.strip())
    opts = Options(args.axes, args.layout, args.mesh, not args.no_transform, args.joint_names, order, args.fps, like)

    def work(job: batch.Job):
        report = actions.import_model(job.source, job.target.name, opts)
        return {job.target.parent / name: data for name, data in report.files.items()}, report.notes

    return _run(batch.jobs(args.inputs, args.out, GLTF_SUFFIXES, ".rdm"), work, args.force)


def cmd_relayout(args: argparse.Namespace) -> int:
    from ..models import rdmnative
    from . import batch, conventions

    def work(job: batch.Job):
        rdm = _read(job.source)
        kind = conventions.classify(rdm)
        if kind == "animation":
            data, notes = rdmnative.convert_animation(rdm)
        elif kind in ("static", "skinned"):
            done = rdmnative.convert_skinned(rdm) if kind == "skinned" else rdmnative.convert_static(rdm)
            data, notes = done.data, done.notes
        else:
            raise ValueError(f"a {kind} file: relayout takes static, skinned and animation files")
        lines = [f"filled: {x}" for x in notes.filled] + [f"approximated: {x}" for x in notes.approximated]
        return {job.target: data}, lines + [f"DROPPED: {x}" for x in notes.dropped]

    out = args.out
    if out is None:
        raise ValueError("relayout needs --out (it never writes over its inputs)")
    return _run(batch.jobs(args.inputs, out, (".rdm",), ".rdm"), work, args.force)


def cmd_validate(args: argparse.Namespace) -> int:
    from . import conventions

    layouts = None
    if args.layouts_from:
        layouts = conventions.layouts_of(p.read_bytes() for p in sorted(args.layouts_from.rglob("*.rdm")))
        print(f"layouts read from {args.layouts_from}: {len(layouts)}")
    bad = 0
    paths = _inputs(args.inputs, (".rdm",))
    for path in paths:
        data = path.read_bytes()
        try:
            kind = conventions.classify(_read(path))
            found = conventions.deviations(data, kind, layouts, strict=not args.loose_box)
        except ValueError as exc:
            kind, found = "unreadable", [str(exc)]
        bad += bool(found)
        print(f"{'ok  ' if not found else 'DEVI'} {path} ({kind})")
        for line in found:
            print(f"     - {line}")
    print(f"{len(paths)} files, {bad} deviate")
    return 1 if bad else 0


def cmd_granny(args: argparse.Namespace) -> int:
    from ..formats.grannyread import GrannyError
    from . import gltfdoc, grannygltf

    out = args.out or args.input.with_suffix(".gr2")
    try:
        data, notes = grannygltf.gltf_to_granny(
            args.input, grannygltf.GAME_UNITS[args.game], gltfdoc.Options(axes=args.axes)
        )
    except (gltfdoc.GltfImportError, GrannyError, ValueError, OSError) as exc:
        print(f"{args.input}: not converted ({exc})", file=sys.stderr)
        return 1
    placing = {}
    if args.place:
        game_path = f"{args.place.replace(chr(92), '/').strip('/')}/{out.name}".lower()
        placing = grannygltf.config_files(data, game_path, args.game)
    written = {out: data} | {out.with_name(n.rsplit("/", 1)[-1]): t.encode("latin-1") for n, t in placing.items()}
    taken = [p for p in written if p.exists()]
    if taken and not args.force:
        print(f"{taken[0]} exists (--force overwrites it)", file=sys.stderr)
        return 1
    for note in notes:
        print("  -", note)
    for path, content in written.items():
        path.write_bytes(content.replace(b"\n", b"\r\n") if path != out else content)  # the games' text files: CRLF
        print(f"wrote {path} ({len(content)} bytes)")
    return 0


def cmd_layouts(_args: argparse.Namespace) -> int:
    from ..formats import rdmlayout

    for name, what in rdmlayout.KNOWN.items():
        print(f"{name:32} {rdmlayout.stride_of(rdmlayout.parse(name)):3} bytes  {what}")
    return 0


def _run(todo, work, force: bool) -> int:
    from . import batch

    def report(r: batch.Result) -> None:
        if r.status == "written":
            print(f"wrote {', '.join(str(p) for p in r.written)}")
        elif r.status == "skipped":
            print(f"skipped {r.job.source}")
        else:
            print(f"{r.status.upper()} {r.job.source}: {r.error}")
        for note in r.notes:
            print(f"  - {note}")

    if not todo:
        raise ValueError("no input file found")
    results = batch.run(todo, work, force, report)
    print(batch.summary(results))
    return 0 if all(r.status in ("written", "skipped") for r in results) else 1


def main(argv: list[str] | None = None, prog: str = "rdm") -> int:
    """The toolchain's own command line: one line on stderr and exit 1 for an error a user can act on."""
    p = argparse.ArgumentParser(prog=prog, description="Read, check and convert Anno .rdm models (glTF 2.0 both ways).")
    register(p.add_subparsers(dest="command", required=True))
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, ImportError) as exc:
        print(f"error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}", file=sys.stderr)
        return 1
