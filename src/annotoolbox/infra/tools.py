"""Locate and run the external tools (texconv, ffmpeg and the Vorbis codebooks, all optional: tools/README.md),
and the number of parallel jobs of a run.

Search order for each tool: its environment variable, the checkout's ``tools/`` folder (recursively), then PATH.
``candidates`` is that search for any program or library (the shader compilers and the Bink DLLs use it too).
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..paths import REPO_ROOT
from .buildcache import file_digest

REPO_TOOLS_DIR = REPO_ROOT / "tools"

# name -> (environment variable, file names to look for)
_TOOL_SPECS = {
    "texconv": ("ANNO_TEXCONV", ("texconv.exe", "texconv")),
    "ffmpeg": ("ANNO_FFMPEG", ("ffmpeg.exe", "ffmpeg")),  # optional: 2070's video screens (videos2070), sound effects
    "wwcodebooks": ("ANNO_WW_CODEBOOKS", ("packed_codebooks_aoTuV_603.bin",)),  # optional: Vorbis sound effects
}

WHERE_TO_GET = {
    "texconv": "texconv.exe from https://github.com/microsoft/DirectXTex/releases (tools/README.md; optional, the "
    "built-in texture path does without)",
    "ffmpeg": "an ffmpeg build with libvorbis, https://ffmpeg.org/download.html (tools/README.md)",
    "wwcodebooks": "packed_codebooks_aoTuV_603.bin, the codebook file of the ww2ogg tool (tools/README.md)",
    "rad": "RAD Video Tools, https://www.radgametools.com/bnkdown.htm (set RAD_VIDEO to radvideo64.exe if elsewhere)",
}
RAD_ENV = "RAD_VIDEO"
RAD_DEFAULT = r"C:\Program Files (x86)\RADVideo\radvideo64.exe"

JOBS_ENV = "ANNO2070_JOBS"
MAX_DEFAULT_JOBS = 12  # a worker holds an island's grids and runs tools of its own: more cores than this thrash
GB_PER_JOB = 5  # free memory one worker needs: 17 GB free ran 3 island workers, 4 ran out of memory (2026-11-04)
# a windowed program (the packaged editor) would open a console window for every tool it starts
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class ToolError(RuntimeError):
    pass


def free_memory_gb() -> float | None:
    """The memory free right now in GB: the less of the physical memory and the commit limit left (None: unknown)."""
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("total", "avail", "ptotal", "pavail", "vtotal", "vavail", "ext")
            ]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return min(status.avail, status.pavail) / 2**30  # the commit limit ends a worker too
    except (AttributeError, OSError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except (OSError, ValueError):
        pass
    return None


def jobs() -> int:
    """How many jobs run at once: ``--jobs`` / ``ANNO2070_JOBS``, else the cores (at most 12) held back to what
    the free memory carries (``GB_PER_JOB`` each, at least 1)."""
    try:
        n = int(os.environ.get(JOBS_ENV, ""))
    except ValueError:
        n = 0
    if n > 0:
        return n
    n = min(MAX_DEFAULT_JOBS, os.cpu_count() or 1)
    free = free_memory_gb()
    return n if free is None else max(1, min(n, int(free // GB_PER_JOB)))


def set_jobs(n: int) -> None:
    """For this process and the ones it starts."""
    os.environ[JOBS_ENV] = str(max(1, n))


def process_names() -> set[str]:
    """The image names of the running processes, in lower case; empty when the system cannot say."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15, **NO_WINDOW
            ).stdout
            return {row[0].lower() for row in csv.reader(io.StringIO(out)) if row}
        out = subprocess.run(
            ["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=15, **NO_WINDOW
        ).stdout
        return {line.strip().rsplit("/", 1)[-1].lower() for line in out.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()


def running(exe: str) -> bool:
    """Whether a process of that program runs now (``exe``: its name or a path to it, any case)."""
    return Path(exe).name.lower() in process_names()


def tool_id(name: str) -> str:
    """The content hash of a tool, which cached results are keyed by (``buildcache``)."""
    return file_digest(require_tool(name))


@dataclass
class ToolResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def candidates(
    names: Sequence[str],
    *,
    env: str = "",
    roots: Iterable[Path] = (),
    subdirs: Sequence[str | Path] = ("",),
    extra: Iterable[Path] = (),
    search_repo_tools: bool = False,
    on_path: bool = False,
    accept: Callable[[Path], bool] = Path.is_file,
    explicit_files: bool = False,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    """The files that can be ``names`` (best first, once each, ``accept`` true): the places ``env`` (folders or files,
    separated like PATH), ``roots`` x ``subdirs`` and ``extra`` (a file counts by its name, or any name from ``env``
    with ``explicit_files``; a folder gives ``folder/name``), the first name's files before the second's; then
    ``tools/`` below the checkout (``search_repo_tools``), then PATH (``on_path``). ``environ`` replaces the process's."""
    values = os.environ if environ is None else environ
    listed = [Path(p) for p in values.get(env, "").split(os.pathsep) if p] if env else []
    places = [(p, explicit_files) for p in listed]
    places += [(Path(r) / sub, False) for r in roots for sub in subdirs] + [(Path(p), False) for p in extra]
    found: list[Path] = []

    def add(path: Path) -> None:
        if path not in found and accept(path):
            found.append(path)

    for name in names:
        for place, any_name in places:
            if place.is_file():
                if any_name or place.name.lower() == name.lower():
                    add(place)
            else:
                add(place / name)
    if search_repo_tools and REPO_TOOLS_DIR.is_dir():
        for name in names:
            add(REPO_TOOLS_DIR / name)
            for hit in sorted(REPO_TOOLS_DIR.rglob(name)):
                add(hit)
    if on_path:
        for name in names:
            if hit := shutil.which(name):
                add(Path(hit))
    return found


_FOUND: dict[tuple, Path] = {}  # a search's places -> the tool found there


def find_tool(name: str) -> Path | None:
    """The program ``name`` (``_TOOL_SPECS``), None when absent. A hit is remembered while the places searched stay
    and the file stands: a PATH search costs 40 ms, and every texture asks (build-cache "Where the time went")."""
    env_var, names = _TOOL_SPECS[name]
    if os.name != "nt":  # a Windows program in tools/ does not run here (the variable may still name any file)
        names = tuple(n for n in names if not n.lower().endswith(".exe"))
    key = (name, os.environ.get(env_var, ""), os.environ.get("PATH", ""), str(REPO_TOOLS_DIR))
    hit = _FOUND.get(key)
    if hit is not None and hit.is_file():
        return hit
    found = next(iter(candidates(names, env=env_var, search_repo_tools=True, on_path=True, explicit_files=True)), None)
    if found is not None:
        _FOUND[key] = found
    return found


def require_tool(name: str) -> Path:
    path = find_tool(name)
    if path is None:
        env_var, names = _TOOL_SPECS[name]
        raise ToolError(
            f"{name} not found. Put {names[0]} into {REPO_TOOLS_DIR}, add it to PATH, or set {env_var}. "
            f"Where to get it: {WHERE_TO_GET[name]}"
        )
    return path


def find_rad() -> Path | None:
    """RAD Video Tools' ``radvideo64.exe`` (``RAD_VIDEO``, else its usual install folder), None when absent."""
    names = [Path(RAD_DEFAULT).name]
    return next(iter(candidates(names, env=RAD_ENV, extra=[Path(RAD_DEFAULT)], explicit_files=True)), None)


def missing_tools(needs: dict[str, bool]) -> list[str]:
    """One line per tool in ``needs`` (name -> required) that is absent, with where to get it; required ones first."""
    lines = []
    for required in (True, False):
        for name, req in needs.items():
            if req != required:
                continue
            absent = find_rad() is None if name == "rad" else find_tool(name) is None
            if absent:
                lines.append(f"{'missing' if req else 'optional, missing'}: {name}: {WHERE_TO_GET[name]}")
    return lines


def run_tool(exe: Path, args: list[str], cwd: Path | None = None, timeout: float | None = None) -> ToolResult:
    """Run a tool; after ``timeout`` seconds it is killed and the result says so (``timed_out``)."""
    try:
        proc = subprocess.run(
            [str(exe), *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(-1, "", f"{exe.name} timed out after {timeout} s", timed_out=True)
    return ToolResult(proc.returncode, proc.stdout, proc.stderr)


def _last_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[-1]


def ffmpeg_bytes(
    data: bytes, args: list[str], *, in_suffix: str = "", out_suffix: str = ".wav", timeout: float = 600
) -> bytes:
    """``data`` through ffmpeg with the output options ``args``, as ``out_suffix`` data. With ``in_suffix`` the input
    is a temporary ``in<suffix>`` file (ffmpeg tells a format by its name), without it a pipe. ``ToolError`` with
    ffmpeg's last line when it fails or is missing."""
    ffmpeg = require_tool("ffmpeg")
    if in_suffix:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="ffmpeg-") as tmp:
            source, target = Path(tmp) / f"in{in_suffix}", Path(tmp) / f"out{out_suffix}"
            source.write_bytes(data)
            result = run_tool(ffmpeg, ["-y", "-v", "error", "-i", str(source), *args, str(target)], timeout=timeout)
            if not result.ok or not target.is_file():
                raise ToolError(f"ffmpeg: {_last_line(result.stderr)}")
            return target.read_bytes()
    command = [str(ffmpeg), "-loglevel", "error", "-i", "pipe:0", *args, "-f", out_suffix.lstrip("."), "pipe:1"]
    try:
        done = subprocess.run(command, input=data, capture_output=True, check=False, timeout=timeout, **NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ToolError(f"ffmpeg: timed out after {timeout} s") from None
    if done.returncode or not done.stdout:
        raise ToolError(f"ffmpeg: {_last_line(done.stderr.decode('utf-8', 'replace'))}")
    return done.stdout


def require_converters() -> None:
    """Fail before a build writes anything when Pillow or numpy is missing. texconv is no longer required: the
    built-in path converts textures without it (``models.texbackend``); meshes need no external tool."""
    if os.environ.get("ANNO_TEXTURE_BACKEND", "").strip().lower() == "texconv":  # texbackend.BACKEND_ENV: forced
        require_tool("texconv")
    for module, package in (("PIL", "pillow"), ("numpy", "numpy")):
        try:
            __import__(module)
        except ImportError:
            name = "Pillow" if module == "PIL" else module
            raise ToolError(f"{name} is not installed (pip install {package}, or pip install -e '.[dev]')") from None
