"""Movies exported as MP4 or WebM through ffmpeg, which is optional and never shipped.

The stored file is always exported. ``mp4`` and ``webm`` run ffmpeg (found as ``infra.tools`` finds it: the
``ANNO_FFMPEG`` variable, the tools folder, the path); when it is missing the status is "tool-missing" and the note
says where to get it. ffmpeg reads Bink 1 (``.bik``) itself; Bink 2 (``.bk2``) it does not: those need RAD Video
Tools (``RAD_VIDEO``) to make a plain video first, which is a manual step this export does not drive, and the note
says so, unless a game's Bink DLL is found (``infra.binkdll``): then it decodes the movie and ffmpeg only encodes.
docs/exporting.md.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..infra import binkdll, tools
from .run import SJob, SMade

ARGS = {
    "mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx-vp9", "-c:a", "libopus"],
}
FFMPEG_HELP = (
    "ffmpeg is not installed: "
    + tools.WHERE_TO_GET["ffmpeg"]
    + " (optional; put it in tools/, on the path or in ANNO_FFMPEG)"
)


def missing() -> list[str]:
    """The lines to show when ffmpeg is absent (none when it is there)."""
    return [] if tools.find_tool("ffmpeg") else [f"mp4 and webm need {FFMPEG_HELP}"]


def export(job: SJob) -> SMade:
    """The video forms of a movie file; the stored file is exported by the caller."""
    made = SMade()
    wanted = [k for k in ("mp4", "webm") if k in job.formats]
    ffmpeg = tools.find_tool("ffmpeg")
    if not wanted:
        return made
    if ffmpeg is None:
        return SMade(notes=[FFMPEG_HELP], status="tool-missing")
    suffix = Path(job.path).suffix.lower()
    for key in wanted:
        if suffix == "." + key:
            made.notes.append(f"already {key}: the stored file is the {key}")
            continue
        try:
            made.files[job.name("." + key)] = _run(ffmpeg, job.data, suffix, key, _roots(job))
        except RuntimeError as exc:
            made.notes.append(str(exc) + (_rad_hint() if suffix == ".bk2" else ""))
            made.status = "failed"
    return made


def _rad_hint() -> str:
    where = "found" if tools.find_rad() else "not found"
    return (
        f" Bink 2 is not read by ffmpeg: make a plain video (or a frame series) with RAD Video Tools first ({where}: "
        + tools.WHERE_TO_GET["rad"]
        + ")."
    )


def _roots(job: SJob) -> list[Path]:
    """The install a Bink DLL may come from: the folder of an opened game (``ANNO_BINK_DLLS`` besides). A folder of
    loose files is data, not an install: native code is never loaded from it."""
    folder = getattr(job.source, "m_folder", None)
    return [Path(folder)] if folder else []


def _run_dll(ffmpeg: Path, data: bytes, key: str, roots: Sequence[Path], target: Path) -> bytes:
    """A Bink movie decoded by the game's DLL and encoded by ffmpeg (the sound goes along when a Bink 2 DLL is found)."""
    try:
        with binkdll.open_movie(data, roots) as movie:
            binkdll.feed_ffmpeg(movie, ffmpeg, ARGS[key], target)
    except binkdll.CBinkError as exc:
        raise RuntimeError(str(exc)) from exc
    return target.read_bytes()


def _run(ffmpeg: Path, data: bytes, suffix: str, key: str, roots: Sequence[Path] = ()) -> bytes:
    """ffmpeg over a temporary copy of the movie; the output's bytes, or a RuntimeError with ffmpeg's last line.
    A Bink 2 movie goes through the game's Bink DLL when one is found (ffmpeg cannot read it)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="export-movie-") as temp_dir:
        source, target = Path(temp_dir) / f"in{suffix}", Path(temp_dir) / f"out.{key}"
        if suffix == ".bk2" and binkdll.available(data, roots):
            return _run_dll(ffmpeg, data, key, roots, target)
        source.write_bytes(data)
        result = tools.run_tool(
            ffmpeg, ["-y", "-v", "error", "-i", str(source), *ARGS[key], str(target)], timeout_s=3600
        )
        if not result.ok or not target.is_file():
            last = (result.stderr.strip().splitlines() or ["no output"])[-1]
            raise RuntimeError(f"ffmpeg could not make the {key}: {last}")
        return target.read_bytes()
