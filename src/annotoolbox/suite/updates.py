"""Looking for a newer release of the program, and fetching it: the part that needs no window.

``check(current)`` asks the release page of ``RELEASES`` (a GitHub repository, ``owner/name``) for its latest release.
In an installed program the installer's own updater does the work (``stage`` downloads the changed parts, ``apply``
restarts into the new version, ``verify_feed`` ties its checksums to ``SHA256SUMS``); elsewhere the person goes to the
release page. ``helper_check`` compares the release's link helper with the installed one. ``RELEASES`` is empty in a
build with no release page: nothing then asks the network. ``docs/installer.md`` "Updates" has the rest.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

from .. import __version__

RELEASES = "SunnyM00n/anno-toolbox"  # the release page, "owner/name"; set by the edition's build, empty means no update check
TIMEOUT = 10.0  # seconds, for every request
SUMS = "SHA256SUMS"
SUMS_LINUX = (
    "SHA256SUMS-linux"  # the Linux build's own list: the Windows and Linux jobs each attach one (docs/installer.md)
)
HELPER_SUMS = "live-link-SHA256SUMS"  # the link helper's checksum on a release page (docs/live-link.md)
HELPER_FILE = "version.dll"
HELPER_MARK = b"library inert"  # text of our helper's log, in its file: tells it from another library of that name
MAX_NOTES = 20000  # characters of the release notes shown
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$")
# URLError, timeouts and HTTP errors (rate limit) are OSErrors; an answer cut short while it is read is not
NET_ERRORS = (OSError, http.client.HTTPException)
log = logging.getLogger(__name__)


class CUpdateError(Exception):
    """A check or download that did not work: offline, a refused request, an answer that does not read, a bad file."""


class SNet(NamedTuple):
    """The network call, so a test can stand in for it: ``get(url) -> bytes`` (the updater downloads the rest)."""

    get: Callable[[str], bytes]


@dataclass(frozen=True)
class SRelease:
    version: str  # "0.1.1"
    notes: str  # the release's text, plain
    sums_url: str
    page_url: str = ""  # the release's own page (https), for the notices that send the person there
    helper_sums_url: str = ""  # the link helper's checksum file, when the release carries a helper


def enabled() -> bool:
    """Whether this build has a release page to look at."""
    return bool(RELEASES)


def frozen() -> bool:
    """True in the packaged program, False in a Python environment."""
    return bool(getattr(sys, "frozen", False))


def source_url() -> str:
    """The repository the installer's updater reads releases from."""
    return f"https://github.com/{RELEASES}"


def parse_version(text: str) -> tuple[tuple[int, int, int], int]:
    """``("0.1.1")`` -> ``((0, 1, 1), 1)``; a suffix (``rc1``) gives 0 for the second part, so it sorts before the
    plain version. Raises ``ValueError`` for text that is not a version."""
    match = _VERSION.match(text.strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch, rest = match.groups()
    return (int(major), int(minor), int(patch)), 0 if rest.strip() else 1


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def due(last: str, today: date) -> bool:
    """The once-a-day gate: ``last`` is the ISO date of the last successful check ('' for never)."""
    try:
        return date.fromisoformat(last) != today
    except ValueError:
        return True


def _request(url: str) -> urllib.request.Request:
    if urlparse(url).scheme != "https":
        raise CUpdateError(f"refused a request that is not https: {url}")
    return urllib.request.Request(url, headers={"User-Agent": f"anno-toolbox/{__version__}"})  # nothing else is sent


def _opened(answer):
    """``answer`` unless a redirect left https (urllib follows one to http): its body is then not read."""
    if urlparse(answer.geturl()).scheme != "https":
        answer.close()
        raise CUpdateError(f"refused a redirect that is not https: {answer.geturl()}")
    return answer


def http_get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(_request(url), timeout=TIMEOUT) as answer:
            return _opened(answer).read()
    except NET_ERRORS as exc:
        raise CUpdateError(str(exc) or type(exc).__name__) from exc


NET = SNet(http_get)


def windows() -> bool:
    """Whether the Windows build's release files are the ones to look for (a test stands in for it)."""
    return sys.platform == "win32"


def sums_name() -> str:
    """The checksum file of the release for this system."""
    return SUMS if windows() else SUMS_LINUX


def parse_release(raw: bytes) -> SRelease:
    """The release record from the release page's answer; ``CUpdateError`` when it is not one we can use (on Linux also
    a release that has no Linux build: the Linux files come from the first release after 0.2.0)."""
    try:
        data = json.loads(raw)
        version = parse_version(data["tag_name"])[0]
        text = ".".join(map(str, version))
        assets = {asset["name"]: asset["browser_download_url"] for asset in data["assets"]}
        notes = data.get("body") or ""
        sums = assets[sums_name()]
        page = data.get("html_url") or ""
    except (ValueError, KeyError, TypeError) as exc:
        raise CUpdateError(f"the release page's answer is not usable: {exc!r}") from exc
    if not (isinstance(notes, str) and isinstance(sums, str) and isinstance(page, str)):
        raise CUpdateError("the release page's answer is not usable")
    helper = assets.get(HELPER_SUMS, "") if windows() else ""  # the link helper is a Windows library
    return SRelease(text, notes.replace("\r\n", "\n").strip()[:MAX_NOTES], sums, page, helper)


def latest(net: SNet = NET) -> SRelease | None:
    """The latest release, newer or not (None without a release page). ``CUpdateError`` when it cannot be read."""
    if not enabled():
        return None
    return parse_release(net.get(f"https://api.github.com/repos/{RELEASES}/releases/latest"))


def check(current: str = __version__, net: SNet = NET) -> SRelease | None:
    """The latest release when it is newer than ``current``, else None. ``CUpdateError`` when the page cannot be read."""
    release = latest(net)
    return release if release is not None and is_newer(release.version, current) else None


def checksum_for(sums_text: str, name: str) -> str:
    """The hash of ``name`` in a ``sha256sum`` listing; ``CUpdateError`` when the file is not in it."""
    for line in sums_text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip().lstrip("*") == name:
            return parts[0].lower()
    raise CUpdateError(f"{name} is not in {sums_name()}")


def verify_feed(update_info, sums_text: str, version: str) -> None:
    """``CUpdateError`` unless the installer's update (``update_info``, from its ``check_for_updates``) is the release's
    ``version`` and every package it will download (the full one, the deltas) has the SHA256 that ``SHA256SUMS`` lists.
    The installer then checks each download against its own hashes; the two lists must agree."""
    target = update_info.TargetFullRelease
    if parse_version(target.Version)[0] != parse_version(version)[0]:
        raise CUpdateError(f"the update feed offers {target.Version}, the release is {version}")
    for asset in (target, *update_info.DeltasToTarget):
        have = (asset.SHA256 or "").lower()
        if not have:
            raise CUpdateError(f"{asset.FileName}: the update feed carries no SHA256")
        if have != checksum_for(sums_text, asset.FileName):
            raise CUpdateError(f"{asset.FileName} does not match its checksum in {sums_name()}")


def manager():
    """The installer's update manager on this program's GitHub releases; ``CUpdateError`` when this program has none
    (the module is absent, or the program was not put there by the installer)."""
    try:
        import velopack

        return velopack.UpdateManager(velopack.GithubSource(source_url()))
    except Exception as exc:  # ImportError, or the manager's refusal outside an install
        raise CUpdateError(f"no installer to update with: {exc}") from exc


def can_self_update(make: Callable[[], object] = manager) -> bool:
    """Whether this is an installed program the installer's updater can replace (else: the release page)."""
    if not (frozen() and enabled()):
        return False
    try:
        make()
    except CUpdateError:
        return False
    return True


def stage(release: SRelease, update_manager=None, net: SNet = NET, progress: Callable[[int], None] | None = None):
    """Check the feed against ``SHA256SUMS``, then download the changed parts (a delta when one fits). Nothing is
    fetched before this call, which the person starts. Returns the update to ``apply``."""
    update_manager = update_manager or manager()
    try:
        update_info = update_manager.check_for_updates()
        if update_info is None:
            raise CUpdateError("the installer's update feed has no newer version")
        verify_feed(update_info, net.get(release.sums_url).decode("utf-8", "replace"), release.version)
        update_manager.download_updates(update_info, progress)
    except CUpdateError:
        raise
    except Exception as exc:  # the updater's own errors (network, a package that fails its hash)
        raise CUpdateError(str(exc)) from exc
    return update_info


def apply(update_info, update_manager=None) -> None:
    """Quit and restart into the downloaded version; the installer's updater swaps the files in between.
    ``CUpdateError`` when the updater refuses (its own errors are ours, as in ``stage``)."""
    update_manager = update_manager or manager()
    try:
        update_manager.apply_updates_and_restart(update_info)
    except Exception as exc:  # the updater's own errors: the window says them
        raise CUpdateError(str(exc) or type(exc).__name__) from exc


@dataclass(frozen=True)
class SHelperCheck:
    sha256: str  # the release's helper
    changed: bool  # differs from the reference: the installed helper of ours, else the last release's
    installed: bool  # an installed helper of ours was the reference


def helper_path(game_dir: str) -> Path:
    return Path(game_dir) / "Bin" / "Win64" / HELPER_FILE


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def is_our_helper(path: Path, seen: str = "") -> bool:
    """Whether ``path`` is a link helper of ours: the hash of the last release's, or our marker text in the file
    (another program's library of that name must not be reported on)."""
    try:
        if seen and file_sha256(path) == seen:
            return True
        return HELPER_MARK in path.read_bytes()
    except OSError:
        return False


def helper_check(release: SRelease, game_dir: str, seen: str = "", net: SNet = NET) -> SHelperCheck | None:
    """Whether the release brings a new link helper: its hash against ``seen``, the one the last checked release had,
    so a helper uploaded again unchanged is no news; an installed helper (``<game_dir>/Bin/Win64``) that already is
    the new one is none either. The first look has nothing to compare and only records. None: no helper on the
    release. Nothing but the checksum file is fetched; the helper itself is never downloaded."""
    if not release.helper_sums_url:
        return None
    new = checksum_for(net.get(release.helper_sums_url).decode("utf-8", "replace"), HELPER_FILE)
    path = helper_path(game_dir) if game_dir else None
    ours = path is not None and path.is_file() and is_our_helper(path, seen)
    already = ours and file_sha256(path) == new
    return SHelperCheck(new, bool(seen) and seen != new and not already, ours)
