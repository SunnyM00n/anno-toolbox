"""Looking for a newer release of the program, and fetching it: the part that needs no window.

``check(current)`` asks the release page of ``RELEASES`` (a GitHub repository, ``owner/name``) for its latest release
and returns a ``Release`` when it is newer than ``current``; ``download(release, folder)`` fetches the installer and its
``SHA256SUMS`` and checks the one against the other; ``start_installer`` hands the file to Windows. ``RELEASES`` is empty
in a build that has no release page of its own: nothing then asks the network (``enabled``). The window part is
``updatenotice``; what is sent and where files go: ``docs/installer.md`` "Updates".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

from .. import __version__
from ..infra.tools import NO_WINDOW
from ..paths import local_data_dir

RELEASES = "SunnyM00n/anno-toolbox"  # the release page, "owner/name"; set by the edition's build, empty means no update check
TIMEOUT = 10.0  # seconds, for every request
SUMS = "SHA256SUMS"
MAX_NOTES = 20000  # characters of the release notes shown
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$")
log = logging.getLogger(__name__)


class UpdateError(Exception):
    """A check or download that did not work: offline, a refused request, an answer that does not read, a bad file."""


class Net(NamedTuple):
    """The two network calls, so a test can stand in for them: ``get(url) -> bytes``, ``save(url, path)``."""

    get: Callable[[str], bytes]
    save: Callable[[str, Path], None]


@dataclass(frozen=True)
class Release:
    version: str  # "0.1.1"
    notes: str  # the release's text, plain
    msi_url: str
    sums_url: str

    @property
    def msi_name(self) -> str:
        return f"AnnoToolbox-{self.version}.msi"


def enabled() -> bool:
    """Whether this build has a release page to look at."""
    return bool(RELEASES)


def frozen() -> bool:
    """True in the installed program (it can run the installer), False in a Python environment."""
    return bool(getattr(sys, "frozen", False))


def parse_version(text: str) -> tuple[tuple[int, int, int], int]:
    """``("0.1.1")`` -> ``((0, 1, 1), 1)``; a suffix (``rc1``) gives 0 for the second part, so it sorts before the
    plain version. Raises ``ValueError`` for text that is not a version."""
    match = _VERSION.match(text.strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    a, b, c, rest = match.groups()
    return (int(a), int(b), int(c)), 0 if rest.strip() else 1


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def updates_dir() -> Path:
    return local_data_dir() / "updates"


def due(last: str, today: date) -> bool:
    """The once-a-day gate: ``last`` is the ISO date of the last successful check ('' for never)."""
    try:
        return date.fromisoformat(last) != today
    except ValueError:
        return True


def pip_command(version: str) -> str:
    return f'pip install --upgrade "anno-toolbox[gui] @ git+https://github.com/{RELEASES}@v{version}"'


def _request(url: str) -> urllib.request.Request:
    if urlparse(url).scheme != "https":
        raise UpdateError(f"refused a request that is not https: {url}")
    return urllib.request.Request(url, headers={"User-Agent": f"anno-toolbox/{__version__}"})  # nothing else is sent


def _opened(answer):
    """``answer`` unless a redirect left https (urllib follows one to http): its body is then not read."""
    if urlparse(answer.geturl()).scheme != "https":
        answer.close()
        raise UpdateError(f"refused a redirect that is not https: {answer.geturl()}")
    return answer


def http_get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as answer:
            return _opened(answer).read()
    except OSError as exc:  # URLError, timeouts and HTTP errors (rate limit) are all OSErrors
        raise UpdateError(str(exc)) from exc


def http_save(url: str, path: Path) -> None:
    part = path.with_name(path.name + ".part")
    try:
        with _opened(urllib.request.urlopen(_request(url), timeout=TIMEOUT)) as answer, part.open("wb") as out:
            while chunk := answer.read(1 << 20):
                out.write(chunk)
        part.replace(path)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc


NET = Net(http_get, http_save)


def parse_release(raw: bytes) -> Release:
    """The release record from the release page's answer; ``UpdateError`` when it is not one we can use."""
    try:
        data = json.loads(raw)
        tag = data["tag_name"]
        version = parse_version(tag)[0]
        text = ".".join(map(str, version))
        assets = {a["name"]: a["browser_download_url"] for a in data["assets"]}
        notes = data.get("body") or ""
        msi, sums = assets[f"AnnoToolbox-{text}.msi"], assets[SUMS]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"the release page's answer is not usable: {exc!r}") from exc
    if not (isinstance(notes, str) and isinstance(msi, str) and isinstance(sums, str)):
        raise UpdateError("the release page's answer is not usable")
    return Release(text, notes.replace("\r\n", "\n").strip()[:MAX_NOTES], msi, sums)


def check(current: str = __version__, net: Net = NET) -> Release | None:
    """The latest release when it is newer than ``current``, else None. ``UpdateError`` when the page cannot be read."""
    if not enabled():
        return None
    release = parse_release(net.get(f"https://api.github.com/repos/{RELEASES}/releases/latest"))
    return release if is_newer(release.version, current) else None


def checksum_for(sums_text: str, name: str) -> str:
    """The hash of ``name`` in a ``sha256sum`` listing; ``UpdateError`` when the file is not in it."""
    for line in sums_text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip().lstrip("*") == name:
            return parts[0].lower()
    raise UpdateError(f"{name} is not in {SUMS}")


def verify(path: Path, sums_text: str) -> None:
    """``UpdateError`` unless the file's SHA256 is the one ``sums_text`` lists for its name."""
    want = checksum_for(sums_text, path.name)
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != want:
        raise UpdateError(f"{path.name} does not match its checksum in {SUMS}")


def download(release: Release, folder: Path | None = None, net: Net = NET) -> Path:
    """Fetch the installer into ``folder`` (``updates_dir()``) and check it; older downloads are deleted. Returns its
    path. A file that fails the check is deleted."""
    folder = folder or updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    sums = net.get(release.sums_url).decode("utf-8", "replace")
    msi = folder / release.msi_name
    net.save(release.msi_url, msi)
    try:
        verify(msi, sums)
    except (UpdateError, OSError) as exc:
        msi.unlink(missing_ok=True)
        raise UpdateError(str(exc)) from exc
    for old in folder.iterdir():
        if old != msi and old.is_file():
            old.unlink(missing_ok=True)
    return msi


def start_installer(msi: Path) -> None:
    """Run the installer (a major upgrade) apart from this program, which is about to quit."""
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"  # not a msiexec.exe in the working folder
    subprocess.Popen([str(system / "msiexec.exe"), "/i", str(msi)], close_fds=True, **NO_WINDOW)
