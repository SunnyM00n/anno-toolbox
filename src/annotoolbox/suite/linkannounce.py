"""What an app calls when it changed something the running game could reload: one call, one sentence back.

``announce(kinds, mod_id, paths)`` sends ``files.changed`` and one ``reload`` per kind to the suite's link
(``livelink.current()``) and returns a ``SNotice`` whose ``text`` goes on the app's status line, "press Shift+F3" and
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
from .livelink import ASSETS, SReply

TEXTURES = linkprotocol.TEXTURE  # not a message of its own: asset reload, then the texture note (livelink.TEXTURE_NOTE)


@dataclass(frozen=True)
class SNotice:
    """What an announcement came to: ``sent`` (a game got a request), the sentence for the status line, the replies."""

    sent: bool
    text: str
    replies: tuple[SReply, ...] = ()


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
    return {file.relative_to(folder).as_posix(): _digest(file) for file in sorted(folder.rglob("*")) if file.is_file()}


def changed_since(before: dict[str, str], folder: Path) -> list[str]:
    """The files of ``folder`` that are new or differ from ``before`` (a ``snapshot``), and the ones gone."""
    if not before:  # everything is new: no need to read (and hash) every byte of a big mod
        folder = Path(folder)
        return (
            sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())
            if folder.is_dir()
            else []
        )
    now = snapshot(folder)
    return sorted(
        [path for path, digest in now.items() if before.get(path) != digest]
        + [path for path in before if path not in now]
    )


def announce(
    kinds: Iterable[str], mod_id: str = "", paths: Iterable[str] = (), is_deployed: bool = True, game: str = ""
) -> SNotice:
    """Tell the game that ``kinds`` of reload are due for the mod ``mod_id`` (``paths``: its changed files, relative to
    its folder). ``is_deployed`` False: the change is saved in the project only, so nothing is sent and the text says
    to build and install first. ``game``: the game the mod is for, so the manual route names its way."""
    if game:
        livelink.set_target_game(game)
    groups = {kind: [path for path in paths if classify(path) == kind] for kind in kinds}
    if not is_deployed:
        return SNotice(False, tr("common.saved_project_build_install", how=_manual(groups)))
    return _push(groups, mod_id, list(paths))


def announce_files(mod_id: str, paths: Iterable[str], new: Iterable[str] = (), game: str = "") -> SNotice:
    """The same for a list of changed files: they are sorted into kinds with ``classify``. ``new``: the files the
    game has not seen (a scene file the mod pass has not mapped yet makes the pass run first)."""
    paths = list(paths)
    if game:
        livelink.set_target_game(game)
    groups: dict[str, list[str]] = {}
    for path in paths:
        kind = classify(path)
        if kind:
            groups.setdefault(kind, []).append(path)
    if not groups:
        return SNotice(False, tr("common.no_file_game_reloads_changed"))
    return _push(groups, mod_id, paths, new=set(new))


def announce_install(mod_id: str, folder: Path, before: dict[str, str], game: str = "") -> SNotice:
    """After a mod was written into ``folder`` (``before``: its ``snapshot`` from before): announce what changed."""
    changed = changed_since(before, folder)
    return announce_files(mod_id, changed, new=[path for path in changed if path not in before], game=game)


def announce_in_background(background, say: Callable[[str], None], call: Callable[[], SNotice]) -> SNotice | None:
    """Run ``call`` (an ``announce*``) so a window's thread never waits for the game: with no game connected it is
    instant and runs here (the notice is returned); with one, ``say`` first gets "asking the game ..." and the
    notice's text when the game has answered, on the window's thread (``background``: ``suite.background``)."""
    if not livelink.current().connected:
        notice = call()
        say(notice.text)
        return notice
    say(tr("common.game_link_reloading"))
    background.run(call, lambda result: say(_text_of(result)))
    return None


def _text_of(result: object) -> str:
    if isinstance(result, Exception):
        return tr("common.game_link_failed", reason=result)
    return result.text if isinstance(result, SNotice) else str(result)


def _push(groups: dict[str, list[str]], mod_id: str, paths: list[str], new: set[str] = frozenset()) -> SNotice:
    """Send what the game can take. A file whose name the link refuses (a space, a non-ASCII letter) is left out on
    its own: the rest goes, and the notice names it."""
    link = livelink.current()
    left_out = [path for path in paths if livelink.check_paths([path])]
    kept = {k: [path for path in found if path not in left_out] for k, found in groups.items()}
    groups = {k: kept[k] for k, found in groups.items() if kept[k] or not found}  # a kind of left-out files is dropped
    replies = []
    safe = [path for path in paths if path not in left_out]
    if safe:
        replies.append(link.files_changed(safe, mod_id))
    notes = []
    reloaded: list[str] = []
    if livelink.SCENES in groups and ASSETS not in groups and new.intersection(groups[livelink.SCENES]):
        groups = {ASSETS: [], **groups}  # a new scene file is not in the mod's file map until the mod pass ran
    for kind, found in groups.items():
        if kind == TEXTURES:  # the texture follows the asset reload and a new request for it
            notes.append(tr(livelink.TEXTURE_NOTE))
            if ASSETS not in groups:
                replies.append(link.reload(ASSETS, (), mod_id))
        else:
            reply = link.reload(kind, found, mod_id)
            replies.append(reply)
            if reply.is_ok:
                reloaded.append(kind)
    if any(link_reply.data.get("staleObjects") for link_reply in replies):
        notes.append(tr(livelink.STALE_NOTE))  # the engine's asset swap leaves placed objects as they were
    if not link.connected:
        return SNotice(False, _manual(groups) + "".join(f" ({note})" for note in notes), tuple(replies))
    sent = any(link_reply.sent for link_reply in replies)
    bad = [link_reply.message for link_reply in replies if not link_reply.is_ok and link_reply.message]
    if left_out:
        bad.append(tr("common.not_sent_game_link_takes", names=", ".join(left_out)))
    done = ", ".join(reloaded)
    text = "; ".join([tr("common.game_reloaded_kinds", kinds=done)] * bool(done) + bad) or tr(
        "common.game_link_refused_request"
    )
    return SNotice(sent, text + "".join(f" ({note})" for note in notes), tuple(replies))


def _manual(groups: dict[str, list[str]]) -> str:
    """The manual route for the kinds in ``groups`` (the same sentence once)."""
    game = livelink.target_game()
    steps = list(dict.fromkeys(livelink.manual(k, game) for k in groups))
    return " / ".join(steps) if steps else tr("common.nothing_game_reloads")


def _digest(path: Path) -> str:
    hasher = hashlib.blake2b(digest_size=16)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
