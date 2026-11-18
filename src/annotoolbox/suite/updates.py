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
HELPER_SUMS = "live-link-SHA256SUMS"  # the link helper's checksum on a release page (docs/live-link.md)
HELPER_FILE = "version.dll"
HELPER_MARK = b"library inert"  # text of our helper's log, in its file: tells it from another library of that name
MAX_NOTES = 20000  # characters of the release notes shown
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$")
# URLError, timeouts and HTTP errors (rate limit) are OSErrors; an answer cut short while it is read is not
NET_ERRORS = (OSError, http.client.HTTPException)
log = logging.getLogger(__name__)


class UpdateError(Exception):
    """A check or download that did not work: offline, a refused request, an answer that does not read, a bad file."""


class Net(NamedTuple):
    """The network call, so a test can stand in for it: ``get(url) -> bytes`` (the updater downloads the rest)."""

    get: Callable[[str], bytes]


@dataclass(frozen=True)
class Release:
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
    a, b, c, rest = match.groups()
    return (int(a), int(b), int(c)), 0 if rest.strip() else 1


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
    except NET_ERRORS as exc:
        raise UpdateError(str(exc) or type(exc).__name__) from exc


NET = Net(http_get)


def parse_release(raw: bytes) -> Release:
    """The release record from the release page's answer; ``UpdateError`` when it is not one we can use."""
    try:
        data = json.loads(raw)
        version = parse_version(data["tag_name"])[0]
        text = ".".join(map(str, version))
        assets = {a["name"]: a["browser_download_url"] for a in data["assets"]}
        notes = data.get("body") or ""
        sums = assets[SUMS]
        page = data.get("html_url") or ""
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError(f"the release page's answer is not usable: {exc!r}") from exc
    if not (isinstance(notes, str) and isinstance(sums, str) and isinstance(page, str)):
        raise UpdateError("the release page's answer is not usable")
    return Release(text, notes.replace("\r\n", "\n").strip()[:MAX_NOTES], sums, page, assets.get(HELPER_SUMS, ""))


def latest(net: Net = NET) -> Release | None:
    """The latest release, newer or not (None without a release page). ``UpdateError`` when it cannot be read."""
    if not enabled():
        return None
    return parse_release(net.get(f"https://api.github.com/repos/{RELEASES}/releases/latest"))


def check(current: str = __version__, net: Net = NET) -> Release | None:
    """The latest release when it is newer than ``current``, else None. ``UpdateError`` when the page cannot be read."""
    release = latest(net)
    return release if release is not None and is_newer(release.version, current) else None


def checksum_for(sums_text: str, name: str) -> str:
    """The hash of ``name`` in a ``sha256sum`` listing; ``UpdateError`` when the file is not in it."""
    for line in sums_text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip().lstrip("*") == name:
            return parts[0].lower()
    raise UpdateError(f"{name} is not in {SUMS}")


def verify_feed(info, sums_text: str, version: str) -> None:
    """``UpdateError`` unless the installer's update (``info``, from its ``check_for_updates``) is the release's
    ``version`` and every package it will download (the full one, the deltas) has the SHA256 that ``SHA256SUMS`` lists.
    The installer then checks each download against its own hashes; the two lists must agree."""
    target = info.TargetFullRelease
    if parse_version(target.Version)[0] != parse_version(version)[0]:
        raise UpdateError(f"the update feed offers {target.Version}, the release is {version}")
    for asset in (target, *info.DeltasToTarget):
        have = (asset.SHA256 or "").lower()
        if not have:
            raise UpdateError(f"{asset.FileName}: the update feed carries no SHA256")
        if have != checksum_for(sums_text, asset.FileName):
            raise UpdateError(f"{asset.FileName} does not match its checksum in {SUMS}")


def manager():
    """The installer's update manager on this program's GitHub releases; ``UpdateError`` when this program has none
    (the module is absent, or the program was not put there by the installer)."""
    try:
        import velopack

        return velopack.UpdateManager(velopack.GithubSource(source_url()))
    except Exception as exc:  # ImportError, or the manager's refusal outside an install
        raise UpdateError(f"no installer to update with: {exc}") from exc


def can_self_update(make: Callable[[], object] = manager) -> bool:
    """Whether this is an installed program the installer's updater can replace (else: the release page)."""
    if not (frozen() and enabled()):
        return False
    try:
        make()
    except UpdateError:
        return False
    return True


def stage(release: Release, mgr=None, net: Net = NET, progress: Callable[[int], None] | None = None):
    """Check the feed against ``SHA256SUMS``, then download the changed parts (a delta when one fits). Nothing is
    fetched before this call, which the person starts. Returns the update to ``apply``."""
    mgr = mgr or manager()
    try:
        info = mgr.check_for_updates()
        if info is None:
            raise UpdateError("the installer's update feed has no newer version")
        verify_feed(info, net.get(release.sums_url).decode("utf-8", "replace"), release.version)
        mgr.download_updates(info, progress)
    except UpdateError:
        raise
    except Exception as exc:  # the updater's own errors (network, a package that fails its hash)
        raise UpdateError(str(exc)) from exc
    return info


def apply(info, mgr=None) -> None:
    """Quit and restart into the downloaded version; the installer's updater swaps the files in between.
    ``UpdateError`` when the updater refuses (its own errors are ours, as in ``stage``)."""
    mgr = mgr or manager()
    try:
        mgr.apply_updates_and_restart(info)
    except Exception as exc:  # the updater's own errors: the window says them
        raise UpdateError(str(exc) or type(exc).__name__) from exc


@dataclass(frozen=True)
class HelperCheck:
    sha256: str  # the release's helper
    changed: bool  # differs from the reference: the installed helper of ours, else the last release's
    installed: bool  # an installed helper of ours was the reference


def helper_path(game_dir: str) -> Path:
    return Path(game_dir) / "Bin" / "Win64" / HELPER_FILE


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
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


def helper_check(release: Release, game_dir: str, seen: str = "", net: Net = NET) -> HelperCheck | None:
    """Compare the release's link helper with the installed one (``<game_dir>/Bin/Win64``) when it is ours, else with
    ``seen``, the hash the last release had. None: no helper on the release. Nothing but the checksum file is fetched;
    the helper itself is never downloaded."""
    if not release.helper_sums_url:
        return None
    new = checksum_for(net.get(release.helper_sums_url).decode("utf-8", "replace"), HELPER_FILE)
    path = helper_path(game_dir) if game_dir else None
    ours = path is not None and path.is_file() and is_our_helper(path, seen)
    reference = file_sha256(path) if ours else seen
    if not reference:
        return HelperCheck(new, False, False)
    return HelperCheck(new, reference != new, ours)
