"""What an app calls when it changed something the running game could reload: one call, one sentence back.

``announce(kinds, mod, paths)`` sends ``files.changed`` and one ``reload`` per kind to the suite's link
(``livelink.current()``) and returns a ``Notice`` whose ``text`` goes on the app's status line, "press Shift+F3" and
the like when no game is connected. ``announce_install`` does it for a mod folder that was just written (it finds
the changed files by ``snapshot`` before and ``changed_since`` after, and sorts them with ``classify``).
docs/live-link.md, "Toolbox side".
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..infra.i18n import tr
from . import linkprotocol, livelink
from .livelink import ASSETS, Reply

TEXTURES = linkprotocol.TEXTURE  # not a message of its own: asset reload, then the texture note (livelink.TEXTURE_NOTE)


@dataclass(frozen=True)
class Notice:
    """What an announcement came to: ``sent`` (a game got a request), the sentence for the status line, the replies."""

    sent: bool
    text: str
    replies: tuple[Reply, ...] = ()


def classify(path: str) -> str:
    """The kind of reload a mod file at ``path`` (relative, forward slashes) needs; "" when the game never reads it
    again (docs/live-link.md section 1)."""
    kind = linkprotocol.needs_of(path)
    return "" if kind == linkprotocol.RESTART else kind


def snapshot(folder: Path) -> dict[str, str]:
    """Relative path -> content hash of every file under ``folder`` ({} when it is not there yet)."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).as_posix(): _digest(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def changed_since(before: dict[str, str], folder: Path) -> list[str]:
    """The files of ``folder`` that are new or differ from ``before`` (a ``snapshot``), and the ones gone."""
    if not before:  # everything is new: no need to read (and hash) every byte of a big mod
        folder = Path(folder)
        return (
            sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
            if folder.is_dir()
            else []
        )
    now = snapshot(folder)
    return sorted([p for p, h in now.items() if before.get(p) != h] + [p for p in before if p not in now])


def announce(kinds: Iterable[str], mod: str = "", paths: Iterable[str] = (), deployed: bool = True) -> Notice:
    """Tell the game that ``kinds`` of reload are due for the mod ``mod`` (``paths``: its changed files, relative to
    its folder). ``deployed`` False: the change is saved in the project only, so nothing is sent and the text says
    to build and install first."""
    groups = {kind: [p for p in paths if classify(p) == kind] for kind in kinds}
    if not deployed:
        return Notice(False, tr("saved in the project: build and install, then {how}", how=_manual(groups)))
    return _push(groups, mod, list(paths))


def announce_files(mod: str, paths: Iterable[str], new: Iterable[str] = ()) -> Notice:
    """The same for a list of changed files: they are sorted into kinds with ``classify``. ``new``: the files the
    game has not seen (a scene file the mod pass has not mapped yet makes the pass run first)."""
    paths = list(paths)
    groups: dict[str, list[str]] = {}
    for path in paths:
        kind = classify(path)
        if kind:
            groups.setdefault(kind, []).append(path)
    if not groups:
        return Notice(False, tr("no file the game reloads changed"))
    return _push(groups, mod, paths, new=set(new))


def announce_install(mod: str, folder: Path, before: dict[str, str]) -> Notice:
    """After a mod was written into ``folder`` (``before``: its ``snapshot`` from before): announce what changed."""
    changed = changed_since(before, folder)
    return announce_files(mod, changed, new=[p for p in changed if p not in before])


def announce_in_background(background, say: Callable[[str], None], call: Callable[[], Notice]) -> Notice | None:
    """Run ``call`` (an ``announce*``) so a window's thread never waits for the game: with no game connected it is
    instant and runs here (the notice is returned); with one, ``say`` first gets "asking the game ..." and the
    notice's text when the game has answered, on the window's thread (``background``: ``suite.background``)."""
    if not livelink.current().connected:
        notice = call()
        say(notice.text)
        return notice
    say(tr("game link: reloading ..."))
    background.run(call, lambda result: say(_text_of(result)))
    return None


def _text_of(result: object) -> str:
    if isinstance(result, Exception):
        return tr("the game link failed: {reason}", reason=result)
    return result.text if isinstance(result, Notice) else str(result)


def _push(groups: dict[str, list[str]], mod: str, paths: list[str], new: set[str] = frozenset()) -> Notice:
    """Send what the game can take. A file whose name the link refuses (a space, a non-ASCII letter) is left out on
    its own: the rest goes, and the notice names it."""
    link = livelink.current()
    left_out = [p for p in paths if livelink.check_paths([p])]
    kept = {k: [p for p in found if p not in left_out] for k, found in groups.items()}
    groups = {k: kept[k] for k, found in groups.items() if kept[k] or not found}  # a kind of left-out files is dropped
    replies = []
    safe = [p for p in paths if p not in left_out]
    if safe:
        replies.append(link.files_changed(safe, mod))
    notes = []
    reloaded: list[str] = []
    if livelink.SCENES in groups and ASSETS not in groups and new.intersection(groups[livelink.SCENES]):
        groups = {ASSETS: [], **groups}  # a new scene file is not in the mod's file map until the mod pass ran
    for kind, found in groups.items():
        if kind == TEXTURES:  # the texture follows the asset reload and a new request for it
            notes.append(tr(livelink.TEXTURE_NOTE))
            if ASSETS not in groups:
                replies.append(link.reload(ASSETS, (), mod))
        else:
            reply = link.reload(kind, found, mod)
            replies.append(reply)
            if reply.ok:
                reloaded.append(kind)
    if any(r.data.get("staleObjects") for r in replies):
        notes.append(tr(livelink.STALE_NOTE))  # the engine's asset swap leaves placed objects as they were
    if not link.connected:
        return Notice(False, _manual(groups) + "".join(f" ({n})" for n in notes), tuple(replies))
    sent = any(r.sent for r in replies)
    bad = [r.message for r in replies if not r.ok and r.message]
    if left_out:
        bad.append(tr("not sent, the game link takes no such file name: {names}", names=", ".join(left_out)))
    done = ", ".join(reloaded)
    text = "; ".join([tr("game reloaded: {kinds}", kinds=done)] * bool(done) + bad) or tr(
        "the game link refused the request"
    )
    return Notice(sent, text + "".join(f" ({n})" for n in notes), tuple(replies))


def _manual(groups: dict[str, list[str]]) -> str:
    """The manual route for the kinds in ``groups`` (the same sentence once)."""
    steps = list(dict.fromkeys(tr(livelink.MANUAL.get(k, "restart the game")) for k in groups))
    return " / ".join(steps) if steps else tr("nothing the game reloads")


def _digest(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
