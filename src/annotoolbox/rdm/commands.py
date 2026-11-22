"""The RDM toolchain's commands: ``inspect``, ``diff``, ``export``, ``import``, ``validate``, ``relayout``, ``layouts``,
``granny`` (a glTF as a Granny ``.gr2`` for 1404 and 1701), ``from-granny`` (a ``.gr2`` as ``.rdm`` files).

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


def register(subparsers: argparse._SubParsersAction) -> None:
    _inspect(subparsers)
    _diff(subparsers)
    _export(subparsers)
    _import(subparsers)
    _validate(subparsers)
    _relayout(subparsers)
    _granny(subparsers)
    _from_granny(subparsers)
    parser = subparsers.add_parser("layouts", help="list the vertex layouts the games ship and what uses them")
    parser.set_defaults(func=cmd_layouts)


def _granny(subparsers) -> None:
    parser = subparsers.add_parser(
        "granny",
        help="glTF 2.0 to a Granny .gr2 (Anno 1404, 1701): meshes, skeleton, animations (docs/rdm-gltf.md)",
    )
    parser.add_argument("input", type=Path, help="a .glb or .gltf")
    parser.add_argument("-o", "--out", type=Path, default=None, help="the .gr2 to write (default: beside the input)")
    parser.add_argument(
        "--game", choices=("anno1404", "anno1701"), default="anno1404", help="whose units: 1701 counts 32 a tile"
    )
    parser.add_argument("--axes", choices=("gltf", "anno"), default=None, help="as the glTF says, else gltf")
    parser.add_argument(
        "--place",
        default="",
        metavar="GAME_FOLDER",
        help="also write the model config and info file that place it (1701: .mcf, .info; 1404: .cfg, .ifo), "
        "naming the .gr2 as lying in this folder of the game's files, say data/graphics/objects/custom",
    )
    parser.add_argument("--force", action="store_true", help="overwrite a file that exists")
    parser.set_defaults(func=cmd_granny)


def _from_granny(subparsers) -> None:
    parser = subparsers.add_parser(
        "from-granny",
        help="Granny .gr2 (Anno 1404, 1701) straight to .rdm for 2070, 2205, 1800 or 117: meshes, skins, animations",
    )
    _paths(parser, ".gr2")
    _common_out(parser)
    parser.add_argument("--to", required=True, choices=("anno2070", "anno2205", "anno1800", "anno117"), help="the game")
    parser.add_argument(
        "--anims",
        type=Path,
        default=None,
        help="a folder of .gr2 files: each animation whose tracks name only bones of the model is written as a file",
    )
    parser.add_argument(
        "--axes",
        choices=("same", "turn"),
        default="same",
        help="same: both games are y up, back = -z (default); turn: half a turn about y",
    )
    parser.add_argument("--source", choices=("anno1404", "anno1701"), default=None, help="default: read off the file")
    parser.add_argument(
        "--scale", type=float, default=0.0, help="divide every length by this (default: the tiles' ratio)"
    )
    for hook in _model_options():
        hook.add_options(parser)
    parser.set_defaults(func=cmd_from_granny)


def _model_options() -> list:
    """What an extension adds to the model commands (the ``--reskin`` option; none in the public edition)."""
    from ..suite import features

    try:
        return list(features.extensions(features.MODEL_OPTIONS).values())
    except ImportError:  # the light install (no numpy) loads no extension: the commands run without the option
        return []


def _paths(parser: argparse.ArgumentParser, what: str) -> None:
    parser.add_argument("inputs", nargs="+", type=Path, help=f"{what} files, or folders searched recursively")


def _common_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--out", type=Path, default=None, help="the output file (one input) or folder")
    parser.add_argument("--force", action="store_true", help="overwrite files that exist (default: leave them, say so)")


def _inspect(subparsers) -> None:
    parser = subparsers.add_parser("inspect", help="list every element of .rdm, .glb or .gltf files")
    _paths(parser, ".rdm, .glb or .gltf")
    parser.add_argument(
        "--values", action="store_true", help="add a checksum of every vertex element, index list, track"
    )
    parser.set_defaults(func=cmd_inspect)


def _diff(subparsers) -> None:
    parser = subparsers.add_parser(
        "diff", help="compare two files element by element (a glTF as the .rdm its import gives)"
    )
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument(
        "--values", action="store_true", help="compare the values too (checksums), not only the structure"
    )
    parser.set_defaults(func=cmd_diff)


def _export(subparsers) -> None:
    parser = subparsers.add_parser(
        "export", help=".rdm to glTF 2.0: meshes, submeshes, materials, skeletons, animations"
    )
    _paths(parser, ".rdm mesh")
    _common_out(parser)
    parser.add_argument(
        "--form",
        choices=("glb", "gltf", "gltfmin", "embedded"),
        default="glb",
        help="one .glb (default), .gltf + .bin (gltfmin: its JSON on one line), or .gltf with the buffer inside",
    )
    parser.add_argument(
        "--anim", type=Path, action="append", default=[], help="an animation .rdm to include (repeatable)"
    )
    parser.add_argument(
        "--anims", type=Path, default=None, help="a folder: include every animation file that moves the mesh"
    )
    parser.add_argument("--lods", action="store_true", help="include the mesh's other LOD files (<name>_lod1.rdm ...)")
    parser.add_argument(
        "--texture",
        action="append",
        default=[],
        metavar="[MATERIAL=]FILE",
        help="a PNG, JPEG or DDS for a material's base colour (all materials without MATERIAL=)",
    )
    parser.add_argument(
        "--axes",
        choices=("gltf", "anno"),
        default="gltf",
        help="gltf: mirrored on x into glTF's right-handed space (default); anno: the stored values",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="unit normals and tangents, weights summing to one, as glTF validators want (an import then rounds back within one step)",
    )
    parser.set_defaults(func=cmd_export)


def _import(subparsers) -> None:
    parser = subparsers.add_parser("import", help="glTF 2.0 to .rdm: the mesh file and its animation files")
    _paths(parser, ".glb or .gltf")
    _common_out(parser)
    parser.add_argument(
        "--layout",
        default=None,
        help="vertex layout of every mesh (rdm layouts); default: the one the file names or its attributes ask for",
    )
    parser.add_argument(
        "--like",
        type=Path,
        default=None,
        help="an .rdm whose layouts, material order, names and stored values to follow",
    )
    parser.add_argument("--mesh", default=None, help="only the glTF mesh of this index or name")
    parser.add_argument(
        "--no-transform", action="store_true", help="do not bake the node transforms into static meshes"
    )
    parser.add_argument(
        "--axes", choices=("gltf", "anno"), default=None, help="default: what the file says, else gltf (mirrored on x)"
    )
    parser.add_argument(
        "--material-order", default="", help="material names in the order the model's cfg lists them, comma separated"
    )
    parser.add_argument(
        "--joint-names",
        choices=("name", "index"),
        default="name",
        help="name joints by their node name (default) or their index",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=None,
        help="frame rate of animations that name none (default: from the keys, else 30)",
    )
    parser.set_defaults(func=cmd_import)


def _validate(subparsers) -> None:
    parser = subparsers.add_parser("validate", help="hold .rdm files against the conventions of Anno 117's own files")
    _paths(parser, ".rdm")
    parser.add_argument(
        "--layouts-from",
        type=Path,
        default=None,
        help="a folder of a game's own .rdm files: its layouts instead of the built-in table",
    )
    parser.add_argument("--loose-box", action="store_true", help="accept a stored box larger than the vertices' own")
    parser.set_defaults(func=cmd_validate)


def _relayout(subparsers) -> None:
    parser = subparsers.add_parser(
        "relayout", help="rewrite .rdm files into the layouts 117 uses (a 2070 model for 117, say)"
    )
    _paths(parser, ".rdm")
    _common_out(parser)
    for hook in _model_options():
        hook.add_options(parser)
    parser.set_defaults(func=cmd_relayout)


# Handlers


def _read(path: Path):
    from ..formats.rdmread import read_rdm

    return read_rdm(path.read_bytes())


def _files(path: Path, options=None) -> list[tuple[str, object]]:
    """The RDM files a path holds: an .rdm itself, a glTF as its import gives them."""
    if path.suffix.lower() not in GLTF_SUFFIXES:
        return [(path.name, _read(path))]
    from ..formats.gltf import read_gltf
    from . import gltfrdm

    return gltfrdm.import_gltf(read_gltf(path), path.with_suffix(".rdm").name, options).files


def _inputs(paths: list[Path], suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for path in paths:
        if path.is_dir():
            out += sorted(file for file in path.rglob("*") if file.suffix.lower() in suffixes and file.is_file())
        elif path.is_file():
            out.append(path)
        else:
            raise FileNotFoundError(path)
    return out


def cmd_inspect(arguments: argparse.Namespace) -> int:
    from ..formats.gltf import read_gltf
    from . import listing

    for path in _inputs(arguments.inputs, (".rdm", *GLTF_SUFFIXES)):
        print(f"== {path}")
        if path.suffix.lower() in GLTF_SUFFIXES:
            print("\n".join(listing.describe_gltf(read_gltf(path))))
            for name, rdm in _files(path):
                print(f"-- the import writes {name}:")
                print("\n".join(listing.describe(rdm, arguments.values)))
        else:
            print("\n".join(listing.describe(_read(path), arguments.values)))
    return 0


def cmd_diff(arguments: argparse.Namespace) -> int:
    from . import listing

    sides = []
    for path, other in ((arguments.a, arguments.b), (arguments.b, arguments.a)):
        files = _files(path)
        if len(files) > 1:  # a glTF of several files: the one named like the other side, else the first mesh file
            files = [entry for entry in files if entry[0] == other.with_suffix(".rdm").name] or [
                entry for entry in files if entry[1].meshes
            ][:1]
        sides.append([line for _name, rdm in files for line in listing.describe(rdm, arguments.values)])
    found = listing.diff(sides[0], sides[1], (str(arguments.a), str(arguments.b)))
    print("\n".join(found) if found else "the same in every element" + (" and value" if arguments.values else ""))
    return 1 if found else 0


def _animations_for(rdm, folder: Path | None, explicit: list[Path]) -> list[Path]:
    """The animation files to include: the named ones, and those in ``folder`` whose every keyed track of some
    entry names a joint of the mesh."""
    found = list(explicit)
    if folder is None:
        return found
    joints = {j.name for skeleton in rdm.skeletons for j in skeleton.joints}
    for path in sorted(folder.rglob("*.rdm")):
        try:
            animations = _read(path).animations
        except ValueError:
            continue
        for animation in animations:
            keyed = [track.joint for track in animation.tracks if track.keys]
            if keyed and all(j in joints for j in keyed) and path not in found:
                found.append(path)
                break
    return found


def _lod_chain(path: Path) -> list[Path]:
    match = re.fullmatch(r"(.*_lod)(\d)", path.stem)
    if not match:
        return [path]
    return [lod_path for k in range(10) if (lod_path := path.with_name(f"{match.group(1)}{k}{path.suffix}")).is_file()]


def _textures(specs: list[str]) -> tuple[list, list[str]]:
    from . import images

    out, notes = [], []
    for spec in specs:
        material, _, file = spec.rpartition("=") if "=" in spec and not Path(spec).is_file() else ("", "", spec)
        texture, note = images.texture(Path(file), material or None)
        out.append(texture)
        notes += [note] if note else []
    return out, notes


def cmd_export(arguments: argparse.Namespace) -> int:
    from . import batch, rdmgltf

    suffix = ".glb" if arguments.form == "glb" else ".gltf"
    textures, texture_notes = _textures(arguments.texture)
    options = rdmgltf.SOptions(axes=arguments.axes, strict=arguments.strict)

    def work(job: batch.SJob):
        paths = _lod_chain(job.source) if arguments.lods else [job.source]
        sources = [rdmgltf.SSource(path.name, _read(path)) for path in paths]
        if not any(source.rdm.meshes for source in sources):
            return {}, ["no mesh: an animation file goes along with the mesh it moves (--anim, --anims)"]
        animations = [
            rdmgltf.SSource(path.name, _read(path))
            for path in _animations_for(sources[0].rdm, arguments.anims, arguments.anim)
        ]
        document, notes = rdmgltf.export(sources, animations, textures, options)
        return document.files(job.target, arguments.form), notes + texture_notes

    return _run(batch.jobs(arguments.inputs, arguments.out, (".rdm",), suffix), work, arguments.force)


def cmd_import(arguments: argparse.Namespace) -> int:
    from . import actions, batch
    from .gltfdoc import SOptions

    like = _read(arguments.like) if arguments.like else None
    order = tuple(part.strip() for part in arguments.material_order.split(",") if part.strip())
    options = SOptions(
        arguments.axes,
        arguments.layout,
        arguments.mesh,
        not arguments.no_transform,
        arguments.joint_names,
        order,
        arguments.fps,
        like,
    )

    def work(job: batch.SJob):
        report = actions.import_model(job.source, job.target.name, options)
        return {job.target.parent / name: data for name, data in report.files.items()}, report.notes

    return _run(batch.jobs(arguments.inputs, arguments.out, GLTF_SUFFIXES, ".rdm"), work, arguments.force)


def cmd_relayout(arguments: argparse.Namespace) -> int:
    from ..models import rdmnative
    from . import batch, conventions

    out = arguments.out
    if out is None:
        raise ValueError("relayout needs --out (it never writes over its inputs)")
    hook = next(iter(_model_options()), None)
    palette = hook.start(arguments) if hook else None

    def work(job: batch.SJob):
        rdm = _read(job.source)
        kind = conventions.classify(rdm)
        if kind == "animation":
            data, notes = rdmnative.convert_animation(rdm)
        elif kind in ("static", "skinned"):
            done = rdmnative.convert_skinned(rdm) if kind == "skinned" else rdmnative.convert_static(rdm)
            data, notes = done.data, done.notes
        else:
            raise ValueError(f"a {kind} file: relayout takes static, skinned and animation files")
        lines = [f"filled: {x}" for x in notes.m_filled] + [f"approximated: {x}" for x in notes.m_approximated]
        files = {job.target: data}
        if palette is not None:
            more, said = (
                hook.configs(palette, job.source.name, data, job.target)
                if kind == "static"
                else ({}, ["not reskinned: a skinned model or an animation is not a ground model"])
            )
            files |= more
            lines += said
        return files, lines + [f"DROPPED: {x}" for x in notes.m_dropped]

    return _run(batch.jobs(arguments.inputs, out, (".rdm",), ".rdm"), work, arguments.force)


def cmd_validate(arguments: argparse.Namespace) -> int:
    from . import conventions

    layouts = None
    if arguments.layouts_from:
        layouts = conventions.layouts_of(
            rdm_file.read_bytes() for rdm_file in sorted(arguments.layouts_from.rglob("*.rdm"))
        )
        print(f"layouts read from {arguments.layouts_from}: {len(layouts)}")
    bad = 0
    paths = _inputs(arguments.inputs, (".rdm",))
    for path in paths:
        data = path.read_bytes()
        try:
            kind = conventions.classify(_read(path))
            found = conventions.deviations(data, kind, layouts, strict=not arguments.loose_box)
        except ValueError as error:
            kind, found = "unreadable", [str(error)]
        bad += bool(found)
        print(f"{'ok  ' if not found else 'DEVI'} {path} ({kind})")
        for line in found:
            print(f"     - {line}")
    print(f"{len(paths)} files, {bad} deviate")
    return 1 if bad else 0


def cmd_granny(arguments: argparse.Namespace) -> int:
    from ..formats.grannyread import CGrannyError
    from . import gltfdoc, grannygltf

    out = arguments.out or arguments.input.with_suffix(".gr2")
    try:
        data, notes = grannygltf.gltf_to_granny(
            arguments.input, grannygltf.GAME_UNITS[arguments.game], gltfdoc.SOptions(axes=arguments.axes)
        )
    except (gltfdoc.CGltfImportError, CGrannyError, ValueError, OSError) as error:
        print(f"{arguments.input}: not converted ({error})", file=sys.stderr)
        return 1
    placing = {}
    if arguments.place:
        game_path = f"{arguments.place.replace(chr(92), '/').strip('/')}/{out.name}".lower()
        placing = grannygltf.config_files(data, game_path, arguments.game)
    written = {out: data} | {
        out.with_name(name.rsplit("/", 1)[-1]): text.encode("latin-1") for name, text in placing.items()
    }
    taken = [existing for existing in written if existing.exists()]
    if taken and not arguments.force:
        print(f"{taken[0]} exists (--force overwrites it)", file=sys.stderr)
        return 1
    for note in notes:
        print("  -", note)
    for path, content in written.items():
        path.write_bytes(content.replace(b"\n", b"\r\n") if path != out else content)  # the games' text files: CRLF
        print(f"wrote {path} ({len(content)} bytes)")
    return 0


def cmd_from_granny(arguments: argparse.Namespace) -> int:
    from . import batch, grannyrdm

    options = grannyrdm.SOptions(arguments.to, arguments.source or "", arguments.scale, arguments.axes)
    folder = sorted(arguments.anims.rglob("*.gr2")) if arguments.anims else []
    read: dict[Path, bytes] = {}
    hook = next(iter(_model_options()), None)
    palette = hook.start(arguments) if hook and arguments.to == "anno117" else None

    def work(job: batch.SJob):
        data = job.source.read_bytes()
        extra = grannyrdm.animation_files(
            ((path.stem, read.setdefault(path, path.read_bytes())) for path in folder if path != job.source),
            grannyrdm.bone_names(data),
        )
        made = grannyrdm.convert(data, job.target.stem, options, extra)
        files = {job.target.parent / name: body for name, body in made.files.items()}
        notes = list(made.notes)
        if palette is not None and job.target.name in made.files:
            more, said = hook.configs(palette, job.source.name, made.files[job.target.name], job.target)
            files |= more
            notes += said
        return files, notes

    return _run(batch.jobs(arguments.inputs, arguments.out, (".gr2",), ".rdm"), work, arguments.force)


def cmd_layouts(_args: argparse.Namespace) -> int:
    from ..formats import rdmlayout

    for name, what in rdmlayout.KNOWN.items():
        print(f"{name:32} {rdmlayout.stride_of(rdmlayout.parse(name)):3} bytes  {what}")
    return 0


def _run(todo, work, force: bool) -> int:
    from . import batch

    def report(result: batch.SResult) -> None:
        if result.status == "written":
            print(f"wrote {', '.join(str(path) for path in result.written)}")
        elif result.status == "skipped":
            print(f"skipped {result.job.source}")
        else:
            print(f"{result.status.upper()} {result.job.source}: {result.error}")
        for note in result.notes:
            print(f"  - {note}")

    if not todo:
        raise ValueError("no input file found")
    results = batch.run(todo, work, force, report)
    print(batch.summary(results))
    return 0 if all(result.status in ("written", "skipped") for result in results) else 1


def main(argv: list[str] | None = None, prog: str = "rdm") -> int:
    """The toolchain's own command line: one line on stderr and exit 1 for an error a user can act on."""
    parser = argparse.ArgumentParser(
        prog=prog, description="Read, check and convert Anno .rdm models (glTF 2.0 both ways)."
    )
    register(parser.add_subparsers(dest="command", required=True))
    arguments = parser.parse_args(argv)
    try:
        return arguments.func(arguments)
    except (OSError, ValueError, ImportError) as error:
        print(f"error: {str(error).splitlines()[0] if str(error) else type(error).__name__}", file=sys.stderr)
        return 1
