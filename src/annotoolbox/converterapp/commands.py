"""The way back into the games' formats on the command line: ``texture bc7`` (a picture or any game's DDS as a BC7
DDS with its mip chain, the layout Anno 117's own textures have) and ``sound wem`` (a WAV, Ogg, MP3 or FLAC as a Wwise
``.wem`` in PCM, ADPCM or Vorbis).

``register(sub)`` adds both groups (the Toolbox's command line and the Converter app). Each command takes files and
folders, writes into ``-o`` and leaves a file that exists unless ``--force``. docs/converter-app.md, "The commands".
"""

from __future__ import annotations

import argparse
from pathlib import Path

PICTURES = (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".gif")
AUDIO = (".wav", ".ogg", ".mp3", ".flac")
NEWEST_BANK = 172  # the newest bank layout family (formats/bnkgraph): the version a .wem is written for by default


def register(sub: argparse._SubParsersAction) -> None:
    texture = sub.add_parser("texture", help="textures into the games' formats: a picture or DDS as BC7 with mips")
    texture_commands = texture.add_subparsers(dest="texture_command", required=True)
    bc7 = texture_commands.add_parser("bc7", help="pictures or DDS files as BC7 DDS files with their full mip chain")
    bc7.add_argument("inputs", nargs="+", type=Path, help="picture or .dds files, or folders searched recursively")
    bc7.add_argument("-o", "--out", type=Path, required=True, help="the output folder")
    bc7.add_argument(
        "--srgb", action="store_true", help="a colour texture: the DDS says sRGB, the mips filter in linear"
    )
    bc7.add_argument("--force", action="store_true", help="overwrite files that exist")
    bc7.set_defaults(func=cmd_bc7)
    sound = sub.add_parser("sound", help="sounds into the games' formats: audio files as Wwise .wem files")
    sound_commands = sound.add_subparsers(dest="sound_command", required=True)
    wem = sound_commands.add_parser("wem", help="WAV, Ogg, MP3 or FLAC files as Wwise .wem files")
    wem.add_argument("inputs", nargs="+", type=Path, help="audio files, or folders searched recursively")
    wem.add_argument("-o", "--out", type=Path, required=True, help="the output folder")
    wem.add_argument("--codec", choices=("pcm", "adpcm", "vorbis"), default="pcm", help="the codec (vorbis: ffmpeg)")
    wem.add_argument("--version", type=int, default=NEWEST_BANK, help="the version of the bank the sound is for")
    wem.add_argument("--force", action="store_true", help="overwrite files that exist")
    wem.set_defaults(func=cmd_wem)


def files_of(inputs: list[Path], suffixes: tuple[str, ...]) -> list[tuple[Path, Path]]:
    """(file, its path relative to the output folder) of every input file with one of ``suffixes``: a file by its
    name, a folder's files by their path inside it."""
    out = []
    for given in inputs:
        if given.is_dir():
            out += [
                (path, path.relative_to(given)) for path in sorted(given.rglob("*")) if path.suffix.lower() in suffixes
            ]
        elif given.is_file():
            out.append((given, Path(given.name)))
        else:
            raise FileNotFoundError(given)
    if not out:
        raise ValueError(f"no input file found ({', '.join(suffixes)})")
    return out


def _each(args: argparse.Namespace, suffixes: tuple[str, ...], suffix: str, make) -> int:
    """``make(source, target)`` for every input whose target is free (or ``--force``); one line per file."""
    failed = 0
    for source, relative_path in files_of(args.inputs, suffixes):
        target = args.out / relative_path.with_suffix(suffix)
        if target.exists() and not args.force:
            print(f"skipped {source} (exists: {target})")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            make(source, target)
        except (OSError, ValueError, RuntimeError) as error:
            failed += 1
            print(f"FAILED {source}: {str(error).splitlines()[0] if str(error) else type(error).__name__}")
            continue
        print(f"wrote {target}")
    return 1 if failed else 0


def cmd_bc7(args: argparse.Namespace) -> int:
    from ..models import textures

    def make(source: Path, target: Path) -> None:
        if source.suffix.lower() == ".dds":
            textures.convert_dds(source, target, is_srgb=args.srgb)
        else:
            textures.png_to_bc7(
                source, target, is_srgb=args.srgb, is_srgb_input=args.srgb
            )  # the picture's values are sRGB

    return _each(args, (*PICTURES, ".dds"), ".dds", make)


def cmd_wem(args: argparse.Namespace) -> int:
    from ..soundedit import clipcodec

    def make(source: Path, target: Path) -> None:
        clip = clipcodec.read_clip(source.read_bytes(), source.name)
        data = clipcodec.encode(clip, args.codec, args.version)
        part_path = target.with_name(target.name + ".part")
        part_path.write_bytes(data)
        part_path.replace(target)  # whole or not at all

    return _each(args, AUDIO, ".wem", make)
