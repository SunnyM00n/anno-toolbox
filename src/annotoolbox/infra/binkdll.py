"""Bink video decoded by a game's own runtime DLL through ctypes: frames as BGRA, the sound as 16-bit PCM.

``find_dll`` picks the Bink library for a file's Bink version from the installed games (never copied, never shipped): Bink 1
(``BIK``) from 2070's ``binkw64.dll``, Bink 2 (``KB2``) from ``bink2w64.dll`` of 117 or another game; 2070's loop also
plays on ``bink2w64.dll``, which is the fallback and the only one that returns the sound. ``Movie`` opens a file and
yields ``Frame`` objects; ``write_png_frames`` and ``feed_ffmpeg`` are the two ways out of it callers use.
Windows only (the DLLs are Windows DLLs). What the API gives and its limits: docs/ui2070-loading.md, "Decoding through
the games' own Bink DLLs".
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..formats import riff
from ..formats.bink import ALPHA_FLAG, HEAD_BYTES, AudioTrack, BinkError, header_of, read_header
from .tools import NO_WINDOW, candidates

ENV = "ANNO_BINK_DLLS"  # folders (or DLL files) separated like PATH: searched before the install folders given
BINK1_DLL, BINK2_DLL = "binkw64.dll", "bink2w64.dll"
SUBFOLDERS = ("", "Bin/Win64", "Bin")  # under an install root: 2070 keeps its DLL in the root, 117 in Bin/Win64
PATH_ENCODING = "mbcs" if os.name == "nt" else "utf-8"  # the DLLs take a narrow path in the system's code page
SURFACE_BGRX = 3  # BINKSURFACE32: bytes B, G, R, then a fourth byte (255 from Bink 2's DLL, 0 from Bink 1's)
SURFACE_BGRA = 5  # BINKSURFACE32A: the same with the alpha plane, which needs ALPHA_FLAG at open
COPY_ALL = 0x80000000  # BINKCOPYALL: the whole frame, not only the changed rectangles


class BinkMissing(BinkError):
    """No Bink DLL of an installed game is available for this movie."""


@dataclass(frozen=True)
class Frame:
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
    found = candidate_dlls(version, roots, environ)
    if not found:
        wanted = BINK1_DLL if version == 1 else BINK2_DLL
        raise BinkMissing(
            f"no {wanted} found for Bink {version}: it ships with an installed Anno game (2070 root, 117 Bin/Win64); "
            f"pass the install folder or set {ENV}"
        )
    return found[0]


_LOADED: dict[Path, ctypes.WinDLL] = {}


def load(path: Path):
    """The Bink library at ``path`` with its calls declared (loaded once per path)."""
    path = Path(path).resolve()
    if path in _LOADED:
        return _LOADED[path]
    if os.name != "nt":
        raise BinkMissing("the Bink DLLs are Windows DLLs")
    try:
        dll = ctypes.WinDLL(str(path))
    except OSError as exc:
        raise BinkError(f"{path} did not load: {exc}") from exc
    v, u32 = ctypes.c_void_p, ctypes.c_uint32
    for name, args, res in (
        ("BinkOpen", [ctypes.c_char_p, u32], v),
        ("BinkClose", [v], None),
        ("BinkDoFrame", [v], ctypes.c_int32),
        ("BinkNextFrame", [v], None),
        ("BinkCopyToBuffer", [v, v, ctypes.c_int32, u32, u32, u32, u32], ctypes.c_int32),
        ("BinkOpenTrack", [v, u32], v),
        ("BinkCloseTrack", [v], None),
        ("BinkGetTrackData", [v, v], u32),
        ("BinkGetTrackMaxSize", [v, u32], u32),
        ("BinkGetError", [], ctypes.c_char_p),
    ):
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, res
    _LOADED[path] = dll
    return dll


class Movie:
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
        self.path, self._delete, self._h, self._tracks = Path(path), _delete, None, []
        self.roots = [Path(r) for r in roots]
        self.info = header_of(self.path)
        if dll is None:
            if audio and self.info.tracks:
                dll = find_dll(2, roots)
            else:
                dll = find_dll(self.info.version, roots)
        self.dll_path = Path(dll)
        self._dll = load(self.dll_path)
        try:
            name = str(self.path).encode(PATH_ENCODING)
        except UnicodeEncodeError:
            raise BinkError(f"{self.path}: the Bink library takes a path in the system's code page only") from None
        self._h = self._dll.BinkOpen(name, ALPHA_FLAG if self.info.alpha else 0)
        if not self._h:
            raise BinkError(f"{self.dll_path.name} cannot open {self.path.name}: {self._last_error()}")
        self._tracks = [self._dll.BinkOpenTrack(self._h, i) for i in range(len(self.info.tracks))] if audio else []

    @classmethod
    def from_bytes(cls, data: bytes, **kw) -> Movie:
        """A movie held in memory: written to a temporary file (the Bink library opens paths) that ``close`` removes."""
        header = read_header(data[:HEAD_BYTES])
        suffix = ".bik" if header.version == 1 else ".bk2"
        fd, name = tempfile.mkstemp(prefix="bink-", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            return cls(Path(name), _delete=Path(name), **kw)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def _last_error(self) -> str:
        text = self._dll.BinkGetError()
        return text.decode("ascii", "replace") if text else "no reason given"

    def close(self) -> None:
        if self._h:
            for track in self._tracks:
                if track:
                    self._dll.BinkCloseTrack(track)
            self._dll.BinkClose(self._h)
            self._h, self._tracks = None, []
        if self._delete is not None:
            self._delete.unlink(missing_ok=True)
            self._delete = None

    def __enter__(self) -> Movie:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def frames(self, every: int = 1, pixels: bool = True, only: Collection[int] | None = None) -> Iterator[Frame]:
        """Every frame in order (each one is decoded, as the codec needs them all); with ``every`` > 1 only each
        ``every``-th frame gets ``pixels`` (the others carry None), ``pixels=False`` copies none: the sound alone;
        ``only`` names the frames that get pixels, and the run ends after the last of them."""
        if not self._h:
            raise BinkError("the movie is closed")
        info, track = self.info, (self._tracks[0] if self._tracks else None)
        size = info.width * info.height * 4
        buf = (ctypes.c_ubyte * size)()
        audio = (ctypes.c_ubyte * (self._dll.BinkGetTrackMaxSize(self._h, 0) + 4096))() if track else None
        last = (max(only) if only else -1) if only is not None else info.frames - 1  # an empty set wants none
        for n in range(min(info.frames, last + 1)):
            self._dll.BinkDoFrame(self._h)
            sound = bytes(audio[: self._dll.BinkGetTrackData(track, audio)]) if track else b""
            data = None
            if pixels and (n in only if only is not None else n % max(every, 1) == 0):
                surface = SURFACE_BGRA if info.alpha else SURFACE_BGRX
                self._dll.BinkCopyToBuffer(self._h, buf, info.width * 4, info.height, 0, 0, surface | COPY_ALL)
                data = bytes(buf)
                if not info.alpha:
                    data = _opaque(data)
            yield Frame(n, data, sound)
            if n + 1 < info.frames:
                self._dll.BinkNextFrame(self._h)

    def pcm(self) -> bytes:
        """The whole sound of the first track, 16-bit PCM; empty for a movie with none."""
        if not self._tracks:
            return b""
        return b"".join(f.audio for f in self.frames(pixels=False))


def _opaque(bgra: bytes) -> bytes:
    """The same pixels with the fourth byte 255 (Bink 1's DLL leaves it 0 where the file has no alpha)."""
    out = bytearray(bgra)
    out[3::4] = b"\xff" * (len(out) // 4)
    return bytes(out)


def open_movie(source: Path | bytes, roots: Iterable[Path] = (), audio: bool = False) -> Movie:
    """A ``Movie`` of a file or of its bytes."""
    if isinstance(source, bytes):
        return Movie.from_bytes(source, roots=roots, audio=audio)
    return Movie(source, roots=roots, audio=audio)


def available(path: Path | bytes, roots: Iterable[Path] = ()) -> bool:
    """True when a DLL for this movie is found (nothing is opened)."""
    try:
        if isinstance(path, bytes):
            head = path[:HEAD_BYTES]
        else:
            with open(path, "rb") as f:
                head = f.read(HEAD_BYTES)
        find_dll(read_header(head).version, roots)
    except (BinkError, OSError):
        return False
    return os.name == "nt"


def write_png_frames(
    movie: Movie, folder: Path, stem: str = "f", size: tuple[int, int] | None = None, every: int = 1
) -> list[Path]:
    """The frames of ``movie`` as ``<stem><number>.png`` in ``folder`` (numbers of 5 digits from 0; ``size`` scales
    them, ``every`` thins them); returns the files in order. Needs Pillow."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    info, out = movie.info, []
    for frame in movie.frames(every):
        if frame.pixels is None:
            continue
        im = Image.frombuffer("RGBA", (info.width, info.height), frame.pixels, "raw", "BGRA", 0, 1)
        if size and size != (info.width, info.height):
            im = im.resize(size, Image.LANCZOS)
        target = folder / f"{stem}{frame.number:05d}.png"
        im.convert("RGB" if not info.alpha else "RGBA").save(target, compress_level=1)
        out.append(target)
    return out


def _audio_dll(movie: Movie) -> Path | None:
    """The library that returns the sound: the movie's own when it is a Bink 2 one, else a ``bink2w64.dll`` of the
    movie's roots or the environment; None when there is none (the sound is then left out)."""
    if movie.dll_path.name.lower() == BINK2_DLL:
        return movie.dll_path
    found = candidate_dlls(2, movie.roots)
    return found[0] if found else None


def frame_images(movie: Movie, numbers: Collection[int]):
    """The frames ``numbers`` of ``movie`` as Pillow RGBA images, in frame order (a number past the end is left out)."""
    from PIL import Image

    info = movie.info
    wanted = {n for n in numbers if 0 <= n < info.frames}
    return [
        Image.frombuffer("RGBA", (info.width, info.height), f.pixels, "raw", "BGRA", 0, 1)
        for f in movie.frames(only=wanted)
        if f.pixels is not None
    ]


def write_wav(path: Path, pcm: bytes, track: AudioTrack) -> None:
    path.write_bytes(riff.wav_bytes(pcm, track.sample_rate, track.channels))


def feed_ffmpeg(movie: Movie, ffmpeg: Path, args: Sequence[str], target: Path, timeout: float = 3600) -> None:
    """Encode ``movie`` through ffmpeg: the sound is decoded first into a temporary file, then the frames are written
    as raw BGRA to ffmpeg's input; ``args`` are the output options (codecs). Raises ``BinkError`` with ffmpeg's last
    line when it fails. The sound (first track) is read by a second open of the same file, with a Bink 2 library;
    without one the video has no sound."""
    info = movie.info
    with tempfile.TemporaryDirectory(prefix="bink-ffmpeg-") as tmp:
        cmd = [str(ffmpeg), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgra"]
        cmd += ["-s", f"{info.width}x{info.height}", "-r", f"{info.fps:.6f}", "-i", "-"]
        sound_dll = _audio_dll(movie) if info.tracks else None
        if sound_dll:
            with Movie(movie.path, audio=True, dll=sound_dll) as sound:
                pcm = sound.pcm()
            if pcm:
                wav = Path(tmp) / "sound.wav"
                write_wav(wav, pcm, info.tracks[0])
                cmd += ["-i", str(wav)]
        with open(Path(tmp) / "ffmpeg.log", "wb") as log:  # a file, not a pipe that fills while we write frames
            proc = subprocess.Popen([*cmd, *args, str(target)], stdin=subprocess.PIPE, stderr=log, **NO_WINDOW)
            try:
                _write_frames(proc, movie)
                code = proc.wait(timeout)
            except BaseException as exc:  # a failed decode or a hung encoder must not leave ffmpeg or a half file
                proc.kill()
                proc.wait()
                target.unlink(missing_ok=True)
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise BinkError(f"ffmpeg did not finish within {timeout:.0f} s") from None
                raise
        if code != 0 or not target.is_file():
            target.unlink(missing_ok=True)
            err = (Path(tmp) / "ffmpeg.log").read_text("utf-8", "replace")
            raise BinkError(
                f"ffmpeg could not encode the decoded frames: {(err.strip().splitlines() or ['no output'])[-1]}"
            )


def _write_frames(proc: subprocess.Popen, movie: Movie) -> None:
    """The movie's frames into ffmpeg's input; ffmpeg closing it early (it failed) ends the writing quietly."""
    try:
        for frame in movie.frames():
            proc.stdin.write(frame.pixels)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:
        pass
