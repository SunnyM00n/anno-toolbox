"""Bink video decoded by a game's own runtime DLL through ctypes: frames as BGRA, the sound as 16-bit PCM.

``find_dll`` picks the Bink library for a file's Bink version from the installed games (never copied, never shipped): Bink 1
(``BIK``) from 2070's ``binkw64.dll``, Bink 2 (``KB2``) from ``bink2w64.dll`` of 117 or another game; 2070's loop also
plays on ``bink2w64.dll``, which is the fallback and the only one that returns the sound. ``CMovie`` opens a file and
yields ``SFrame`` objects; ``write_png_frames`` and ``feed_ffmpeg`` are the two ways out of it callers use.
Windows only (the DLLs are Windows DLLs). What the API gives and its limits: docs/ui2070-loading.md, "Decoding through
the games' own Bink DLLs".
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
import threading
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..formats import riff
from ..formats.bink import ALPHA_FLAG, HEAD_BYTES, CBinkError, SAudioTrack, header_of, read_header
from .tools import NO_WINDOW, candidates

ENV = "ANNO_BINK_DLLS"  # folders (or DLL files) separated like PATH: searched before the install folders given
BINK1_DLL, BINK2_DLL = "binkw64.dll", "bink2w64.dll"
SUBFOLDERS = ("", "Bin/Win64", "Bin")  # under an install root: 2070 keeps its DLL in the root, 117 in Bin/Win64
PATH_ENCODING = "mbcs" if os.name == "nt" else "utf-8"  # the DLLs take a narrow path in the system's code page
SURFACE_BGRX = 3  # BINKSURFACE32: bytes B, G, R, then a fourth byte (255 from Bink 2's DLL, 0 from Bink 1's)
SURFACE_BGRA = 5  # BINKSURFACE32A: the same with the alpha plane, which needs ALPHA_FLAG at open
COPY_ALL = 0x80000000  # BINKCOPYALL: the whole frame, not only the changed rectangles


class CBinkMissing(CBinkError):
    """No Bink DLL of an installed game is available for this movie."""


@dataclass(frozen=True)
class SFrame:
    """One decoded frame: ``pixels`` is ``width * height * 4`` bytes (BGRA, top row first), None when the frame was
    only decoded (a skipped frame); ``audio`` is the sound that frame released, 16-bit little-endian interleaved."""

    number: int  # from 0
    pixels: bytes | None
    audio: bytes = b""


def candidate_dlls(version: int, roots: Iterable[Path] = (), environ: dict[str, str] | None = None) -> list[Path]:
    """The DLLs that can decode Bink ``version``, best first, that exist: the ``ANNO_BINK_DLLS`` places, then the
    install ``roots`` (each with its ``Bin/Win64`` and ``Bin`` folder; a ``maindata`` folder counts as its install
    too). Bink 1 prefers ``binkw64.dll`` and falls back to ``bink2w64.dll``; Bink 2 only has the latter. No other
    folder is searched: a DLL's start-up code runs in this process, so a folder that is only read (a mod, an export
    source) never supplies one."""
    names = (BINK1_DLL, BINK2_DLL) if version == 1 else (BINK2_DLL,)
    installs = [
        place
        for root in map(Path, roots)
        for place in (root, *([root.parent] if root.name.lower() == "maindata" else []))
    ]
    return candidates(names, env=ENV, roots=installs, subdirs=SUBFOLDERS, environ=environ)


def find_dll(version: int, roots: Iterable[Path] = (), environ: dict[str, str] | None = None) -> Path:
    """The best DLL for Bink ``version`` (``candidate_dlls``' first); ``CBinkMissing`` says where one comes from."""
    found = candidate_dlls(version, roots, environ)
    if not found:
        wanted = BINK1_DLL if version == 1 else BINK2_DLL
        raise CBinkMissing(
            f"no {wanted} found for Bink {version}: it ships with an installed Anno game (2070 root, 117 Bin/Win64); "
            f"pass the install folder or set {ENV}"
        )
    return found[0]


_g_loaded: dict[Path, ctypes.WinDLL] = {}


def load(path: Path):
    """The Bink library at ``path`` with its calls declared (loaded once per path)."""
    path = Path(path).resolve()
    if path in _g_loaded:
        return _g_loaded[path]
    if os.name != "nt":
        raise CBinkMissing("the Bink DLLs are Windows DLLs")
    try:
        dll = ctypes.WinDLL(str(path))
    except OSError as error:
        raise CBinkError(f"{path} did not load: {error}") from error
    pointer_type, uint32_type = ctypes.c_void_p, ctypes.c_uint32
    for name, argument_types, result_type in (
        ("BinkOpen", [ctypes.c_char_p, uint32_type], pointer_type),
        ("BinkClose", [pointer_type], None),
        ("BinkDoFrame", [pointer_type], ctypes.c_int32),
        ("BinkNextFrame", [pointer_type], None),
        (
            "BinkCopyToBuffer",
            [pointer_type, pointer_type, ctypes.c_int32, uint32_type, uint32_type, uint32_type, uint32_type],
            ctypes.c_int32,
        ),
        ("BinkOpenTrack", [pointer_type, uint32_type], pointer_type),
        ("BinkCloseTrack", [pointer_type], None),
        ("BinkGetTrackData", [pointer_type, pointer_type], uint32_type),
        ("BinkGetTrackMaxSize", [pointer_type, uint32_type], uint32_type),
        ("BinkGetError", [], ctypes.c_char_p),
    ):
        function = getattr(dll, name)
        function.argtypes, function.restype = argument_types, result_type
    _g_loaded[path] = dll
    return dll


class CMovie:
    """An open Bink file: ``info``, ``frames()``, ``close()`` (or ``with``). ``dll`` is the library to use; without
    it ``find_dll`` chooses by the file's version from ``roots``, taking the Bink 2 library when ``audio`` is set
    (the Bink 1 library did not return the sound; ``docs/ui2070-loading.md``)."""

    def __init__(
        self,
        path: Path,
        roots: Iterable[Path] = (),
        dll: Path | None = None,
        audio: bool = False,
        _delete: Path | None = None,
    ) -> None:
        self.m_path, self._m_delete, self._m_h, self._m_tracks = Path(path), _delete, None, []
        self.m_roots = [Path(root) for root in roots]
        self.m_info = header_of(self.m_path)
        if dll is None:
            if audio and self.m_info.tracks:
                dll = find_dll(2, roots)
            else:
                dll = find_dll(self.m_info.version, roots)
        self.m_dll_path = Path(dll)
        self._m_dll = load(self.m_dll_path)
        try:
            name = str(self.m_path).encode(PATH_ENCODING)
        except UnicodeEncodeError:
            raise CBinkError(f"{self.m_path}: the Bink library takes a path in the system's code page only") from None
        self._m_h = self._m_dll.BinkOpen(name, ALPHA_FLAG if self.m_info.alpha else 0)
        if not self._m_h:
            raise CBinkError(f"{self.m_dll_path.name} cannot open {self.m_path.name}: {self._last_error()}")
        self._m_tracks = (
            [self._m_dll.BinkOpenTrack(self._m_h, i) for i in range(len(self.m_info.tracks))] if audio else []
        )

    @classmethod
    def from_bytes(cls, data: bytes, **keyword) -> CMovie:
        """A movie held in memory: written to a temporary file (the Bink library opens paths) that ``close`` removes."""
        header = read_header(data[:HEAD_BYTES])
        suffix = ".bik" if header.version == 1 else ".bk2"
        fd, name = tempfile.mkstemp(prefix="bink-", suffix=suffix)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        try:
            return cls(Path(name), _delete=Path(name), **keyword)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def _last_error(self) -> str:
        text = self._m_dll.BinkGetError()
        return text.decode("ascii", "replace") if text else "no reason given"

    def close(self) -> None:
        if self._m_h:
            for track in self._m_tracks:
                if track:
                    self._m_dll.BinkCloseTrack(track)
            self._m_dll.BinkClose(self._m_h)
            self._m_h, self._m_tracks = None, []
        if self._m_delete is not None:
            self._m_delete.unlink(missing_ok=True)
            self._m_delete = None

    def __enter__(self) -> CMovie:
        return self

    def __exit__(self, *error) -> None:
        self.close()

    def frames(self, every: int = 1, pixels: bool = True, only: Collection[int] | None = None) -> Iterator[SFrame]:
        """Every frame in order (each one is decoded, as the codec needs them all); with ``every`` > 1 only each
        ``every``-th frame gets ``pixels`` (the others carry None), ``pixels=False`` copies none: the sound alone;
        ``only`` names the frames that get pixels, and the run ends after the last of them."""
        if not self._m_h:
            raise CBinkError("the movie is closed")
        movie_info, track = self.m_info, (self._m_tracks[0] if self._m_tracks else None)
        size_bytes = movie_info.width * movie_info.height * 4
        buffer = (ctypes.c_ubyte * size_bytes)()
        audio = (ctypes.c_ubyte * (self._m_dll.BinkGetTrackMaxSize(self._m_h, 0) + 4096))() if track else None
        last = (max(only) if only else -1) if only is not None else movie_info.frames - 1  # an empty set wants none
        for frame_number in range(min(movie_info.frames, last + 1)):
            self._m_dll.BinkDoFrame(self._m_h)
            sound = bytes(audio[: self._m_dll.BinkGetTrackData(track, audio)]) if track else b""
            data = None
            if pixels and (frame_number in only if only is not None else frame_number % max(every, 1) == 0):
                surface = SURFACE_BGRA if movie_info.alpha else SURFACE_BGRX
                self._m_dll.BinkCopyToBuffer(
                    self._m_h, buffer, movie_info.width * 4, movie_info.height, 0, 0, surface | COPY_ALL
                )
                data = bytes(buffer)
                if not movie_info.alpha:
                    data = _opaque(data)
            yield SFrame(frame_number, data, sound)
            if frame_number + 1 < movie_info.frames:
                self._m_dll.BinkNextFrame(self._m_h)

    def pcm(self) -> bytes:
        """The whole sound of the first track, 16-bit PCM; empty for a movie with none."""
        if not self._m_tracks:
            return b""
        return b"".join(frame.audio for frame in self.frames(pixels=False))


def _opaque(bgra: bytes) -> bytes:
    """The same pixels with the fourth byte 255 (Bink 1's DLL leaves it 0 where the file has no alpha)."""
    out = bytearray(bgra)
    out[3::4] = b"\xff" * (len(out) // 4)
    return bytes(out)


def open_movie(source: Path | bytes, roots: Iterable[Path] = (), audio: bool = False) -> CMovie:
    """A ``CMovie`` of a file or of its bytes."""
    if isinstance(source, bytes):
        return CMovie.from_bytes(source, roots=roots, audio=audio)
    return CMovie(source, roots=roots, audio=audio)


def available(path: Path | bytes, roots: Iterable[Path] = ()) -> bool:
    """True when a DLL for this movie is found (nothing is opened)."""
    try:
        if isinstance(path, bytes):
            head = path[:HEAD_BYTES]
        else:
            with open(path, "rb") as file:
                head = file.read(HEAD_BYTES)
        find_dll(read_header(head).version, roots)
    except (CBinkError, OSError):
        return False
    return os.name == "nt"


def write_png_frames(
    movie: CMovie, folder: Path, stem: str = "f", size_px: tuple[int, int] | None = None, every: int = 1
) -> list[Path]:
    """The frames of ``movie`` as ``<stem><number>.png`` in ``folder`` (numbers of 5 digits from 0; ``size`` scales
    them, ``every`` thins them); returns the files in order. Needs Pillow."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    movie_info, out = movie.m_info, []
    for frame in movie.frames(every):
        if frame.pixels is None:
            continue
        image = Image.frombuffer("RGBA", (movie_info.width, movie_info.height), frame.pixels, "raw", "BGRA", 0, 1)
        if size_px and size_px != (movie_info.width, movie_info.height):
            image = image.resize(size_px, Image.LANCZOS)
        target = folder / f"{stem}{frame.number:05d}.png"
        image.convert("RGB" if not movie_info.alpha else "RGBA").save(target, compress_level=1)
        out.append(target)
    return out


def _audio_dll(movie: CMovie) -> Path | None:
    """The library that returns the sound: the movie's own when it is a Bink 2 one, else a ``bink2w64.dll`` of the
    movie's roots or the environment; None when there is none (the sound is then left out)."""
    if movie.m_dll_path.name.lower() == BINK2_DLL:
        return movie.m_dll_path
    found = candidate_dlls(2, movie.m_roots)
    return found[0] if found else None


def frame_images(movie: CMovie, numbers: Collection[int]):
    """The frames ``numbers`` of ``movie`` as Pillow RGBA images, in frame order (a number past the end is left out)."""
    from PIL import Image

    movie_info = movie.m_info
    wanted = {frame_number for frame_number in numbers if 0 <= frame_number < movie_info.frames}
    return [
        Image.frombuffer("RGBA", (movie_info.width, movie_info.height), frame.pixels, "raw", "BGRA", 0, 1)
        for frame in movie.frames(only=wanted)
        if frame.pixels is not None
    ]


def write_wav(path: Path, pcm: bytes, track: SAudioTrack) -> None:
    """``pcm`` (16-bit interleaved) as a WAV file at ``path``, with ``track``'s rate and channels."""
    path.write_bytes(riff.wav_bytes(pcm, track.sample_rate, track.channels))


def feed_ffmpeg(movie: CMovie, ffmpeg: Path, ffmpeg_args: Sequence[str], target: Path, timeout_s: float = 3600) -> None:
    """Encode ``movie`` through ffmpeg: the sound is decoded first into a temporary file, then the frames are written
    as raw BGRA to ffmpeg's input; ``args`` are the output options (codecs). Raises ``CBinkError`` with ffmpeg's last
    line when it fails. The sound (first track) is read by a second open of the same file, with a Bink 2 library;
    without one the video has no sound."""
    movie_info = movie.m_info
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="bink-ffmpeg-") as temp_folder:
        command = [str(ffmpeg), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgra"]
        command += ["-s", f"{movie_info.width}x{movie_info.height}", "-r", f"{movie_info.fps:.6f}", "-i", "-"]
        sound_dll = _audio_dll(movie) if movie_info.tracks else None
        if sound_dll:
            with CMovie(movie.m_path, audio=True, dll=sound_dll) as sound:
                pcm = sound.pcm()
            if pcm:
                wav = Path(temp_folder) / "sound.wav"
                write_wav(wav, pcm, movie_info.tracks[0])
                command += ["-i", str(wav)]
        with open(Path(temp_folder) / "ffmpeg.log", "wb") as log:  # a file, not a pipe that fills while we write frames
            process = subprocess.Popen(
                [*command, *ffmpeg_args, str(target)], stdin=subprocess.PIPE, stderr=log, **NO_WINDOW
            )
            # Frames go from a thread so the timeout covers the whole run, also an encoder that stops reading.
            writer = _CFrameWriter(process, movie)
            writer.start()
            try:
                code = process.wait(timeout_s)
                writer.join()
                if writer.error:
                    raise writer.error
            except BaseException as failure:  # a failed decode or a hung encoder must not leave ffmpeg or a half file
                process.kill()
                process.wait()
                writer.join()
                target.unlink(missing_ok=True)
                if isinstance(failure, subprocess.TimeoutExpired):
                    raise CBinkError(f"ffmpeg did not finish within {timeout_s:.0f} s") from None
                raise
        if code != 0 or not target.is_file():
            target.unlink(missing_ok=True)
            err = (Path(temp_folder) / "ffmpeg.log").read_text("utf-8", "replace")
            raise CBinkError(
                f"ffmpeg could not encode the decoded frames: {(err.strip().splitlines() or ['no output'])[-1]}"
            )


class _CFrameWriter(threading.Thread):
    """Writes the movie's frames into ffmpeg's input. ffmpeg closing it early (it failed) ends the writing quietly;
    a decode error is kept in ``error`` and ends ffmpeg, for the caller to raise."""

    def __init__(self, process: subprocess.Popen, movie: CMovie) -> None:
        super().__init__(daemon=True)
        self.process, self.movie, self.error = process, movie, None

    def run(self) -> None:
        try:
            for frame in self.movie.frames():
                self.process.stdin.write(frame.pixels)  # type: ignore[union-attr]
        except OSError:  # a broken pipe, on Windows also a plain invalid-argument error
            pass
        except BaseException as failure:
            self.error = failure
            self.process.kill()
        finally:  # the input is closed on every path: ffmpeg ends its read, and no handle is left open
            try:
                self.process.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass
