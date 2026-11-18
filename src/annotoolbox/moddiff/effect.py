"""What mods do to the game's files, one mod at a time: the mod loader's run (``modloader.engine.Session``) with every
mod's **step** kept, the changes it made on top of the mods before it, and the whole against the game.

``run_mods(game, mods, variables)`` applies the mods in this order and returns an ``Effect``: ``steps`` (a
``ModEffect`` per mod: its targets, its files, what the loader would drop) and ``targets`` (all steps against the
game). Targets are assets by GUID, texts by id and other files whole (``docs/mod-diff.md`` "What a change is").
Only what an operation opened is compared: an untouched asset of a large file is never parsed.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..assetedit.assetgame import Game
from ..infra.i18n import tr
from ..modloader.engine import INFO, Event, Session
from ..modloader.mods import Mod, Variables, patch_target
from ..modloader.patchdoc import PatchDoc
from .base import GameBase
from .elementdata import asset_facts, data_changes, element_data
from .filechanges import mod_files
from .model import ADDED, CHANGED, REMOVED, Change, FileChange, Target
from .writes import Write, guid_of, set_values

BINARY = "export.bin"  # the binary infotips: kept as a file, not read


@dataclass
class ModEffect:
    """One mod's step: what it changed on top of the mods before it."""

    mod_id: str
    label: str  # "id version"
    mod: Mod | None = None
    targets: dict[tuple[str, str], Target] = field(default_factory=dict)
    files: list[FileChange] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    writes: dict[tuple[str, str], dict[str, str]] = field(
        default_factory=dict
    )  # (file, GUID) -> value path -> value set


@dataclass
class Effect:
    """A run of the loader: each mod's step, and every step together against the game."""

    game: str
    steps: list[ModEffect] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)  # a path two mods carry once, the later mod's

    @property
    def events(self) -> list[Event]:
        return [e for s in self.steps for e in s.events]


def guid_key(text: str) -> tuple:
    """A sort key for GUIDs and text ids: the numbers first, in number order."""
    return (0, int(text), "") if text.lstrip("-").isdigit() else (1, 0, text)


def run_mods(
    game: Game,
    mods: list[Mod],
    variables: Variables,
    progress: Callable[[str], None] | None = None,
    compare_bytes: int = 2000,
) -> Effect:
    """``mods`` applied in this order to the game's files. ``compare_bytes``: how many replaced files are compared
    byte for byte with the game's (to tell a file that is the game's own from one that is not)."""
    base = GameBase(game)
    run = Run(base, progress or (lambda _t: None))
    effect = Effect(game.layout.key)
    patched = [{t for rel, _p in m.patch_files() if (t := patch_target(rel))} for m in mods]
    for number, mod in enumerate(mods):
        run.remember = number < len(mods) - 1  # what a later mod starts from is kept only when one follows
        later = set().union(*patched[number + 1 :])  # a file no later mod patches is done with after this mod
        effect.steps.append(run.step(mod, variables, compare_bytes, later))
    effect.targets = run.final(effect.steps)
    carried: dict[str, FileChange] = {}
    for step in effect.steps:
        for change in step.files:
            before = carried.get(change.path)
            carried[change.path] = change if before is None else _later(before, change)
    effect.files = sorted(carried.values(), key=lambda c: c.path)
    return effect


def _later(before: FileChange, change: FileChange) -> FileChange:
    """The file a later mod carries over an earlier one's: its content, both mods named."""
    return FileChange(change.path, change.kind, change.size, [*before.mods, *change.mods], change.digest, change.source)


class Run:
    """The loader's session, and the last known data of every asset a mod has touched (its step's "before")."""

    def __init__(self, base: GameBase, say: Callable[[str], None], remember: bool = True) -> None:
        self.base, self.say, self.remember = base, say, remember
        self.language = base.language_of()
        self.session = Session(
            base.read_base, base.lazy, self.observe, self.record, keep=self.keep, game=base.layout.key
        )
        self.last: dict[tuple[str, str], object] = {}  # an asset's data (or its packed form) after the last step
        self.touched: dict[str, set[str]] = {}  # file -> the GUIDs an operation of the running mod reached
        self.operated: set[str] = set()  # the files an operation of the running mod reached at all
        self.shots: dict[str, dict] = {}  # a text or data file as data after the last step: the next one's start
        self.writes: dict[tuple[str, str], dict[str, str]] = {}  # the running mod's single values set, even if equal
        self.again: list[tuple[str, str]] = []  # (file, GUID) the running mod adds though the file has it
        self.pending: list[Write] = []  # the values the operation about to run will calculate
        self.steps: list[ModEffect] = []  # the steps so far, the running one last
        self.nets: dict[tuple[str, str], Target] = {}  # a target against the game, made when its file was let go

    @staticmethod
    def keep(_doc: PatchDoc, el) -> tuple:
        """What is read from an asset the loader adds before it is packed: its name, template and values (packed)."""
        values = Target("", "", ADDED)
        values.set_values(element_data(el))
        return asset_facts(el), values.packed

    def observe(self, _mod: str, path: str, _kind: str, nodes: list) -> None:
        self.operated.add(path)
        doc = self.session.docs.get(path)
        pending, self.pending = self.pending, []
        if doc is None:
            return
        for write in pending:  # the values a ``ModValue`` gave are in the document now
            self.writes.setdefault((path, write.guid), {})[write.path] = write.settle(doc)
        for node in nodes:
            if guid := guid_of(doc, node):
                self.touched.setdefault(path, set()).add(guid)

    def record(self, _mod: str, path: str, kind: str, targets: list, content: list) -> None:
        doc = self.session.docs.get(path)
        if doc is not None:
            for write in set_values(doc, kind, targets, content):
                if write.value is None:
                    self.pending.append(write)
                else:
                    self.writes.setdefault((path, write.guid), {})[write.path] = write.value

    # one mod
    def step(self, mod: Mod, variables: Variables, compare_bytes: int, later: set[str] = frozenset()) -> ModEffect:
        """One mod, a patch file at a time: each file's document is opened, patched and compared, and let go once no
        later mod patches it (``later``: the files they patch), so only one large file is held at once."""
        self.say(tr("common.applying_mod", mod=mod.mod_id))
        step = ModEffect(mod.mod_id, f"{mod.mod_id} {mod.version}".strip(), mod)
        self.steps.append(step)
        files = [(rel, patch_target(rel)) for rel, _path in mod.patch_files()]
        last = {target: rel for rel, target in files if target}
        self.touched.clear()
        self.operated = set()
        self.writes, self.again = {}, []
        seen = len(self.session.events)
        before: dict[str, object] = {}
        for rel, target in files:
            if (
                target
                and not target.endswith(BINARY)
                and target not in before
                and (doc := self.session.document(target))
            ):
                before[target] = self._snapshot(target, doc)
            self.session.apply_file(mod, rel, mod.read(rel), variables)
            if target in before and last[target] == rel:
                self._finish(step, target, before.pop(target), target not in later)
        step.events = self.session.events[seen:]
        step.writes = self.writes
        step.events += [
            Event(mod.mod_id, path, "duplicate", f"GUID {guid} is added again: the file already has it", INFO)
            for path, guid in self.again
        ]
        step.files = mod_files(self.base, mod, compare_bytes)
        for change in step.files:
            change.mods = [mod.mod_id]
        return step

    def _finish(self, step: ModEffect, path: str, before, done: bool) -> None:
        """The targets the running mod made of ``path``; ``done``: no later mod patches it, so its net result against
        the game is made now and the document let go."""
        for target in self._targets(path, self.session.docs[path], before):
            target.mods = [step.mod_id]
            step.targets[target.ident] = target
        if not done:
            return
        for ident in {i for s in self.steps for i in s.targets if i[0] == path}:
            net = self._net(ident, [s.targets[ident] for s in self.steps if ident in s.targets])
            if net is not None:
                self.nets[ident] = net
        del self.session.docs[path]
        self.shots.pop(path, None)
        for key in [k for k in self.last if k[0] == path]:
            del self.last[key]

    def _kind(self, path: str, doc: PatchDoc) -> str:
        if self.language(path) is not None and self.base.layout.text_format == "xml":
            return "texts"
        return "assets" if doc.by_guid or doc.lazy or path in self.base.files else "data"

    def _snapshot(self, path: str, doc: PatchDoc):
        kind = self._kind(path, doc)
        if kind == "assets":
            return _counts(doc)
        if path not in self.shots:  # the first look: the file is still the game's own (or what earlier mods left)
            self.shots[path] = self.base.texts_of(doc) if kind == "texts" else data_of(doc)
        return self.shots[path]

    def _targets(self, path: str, doc: PatchDoc, before) -> list[Target]:
        kind = self._kind(path, doc)
        if kind == "assets":
            return self.asset_targets(path, doc, before)
        if path not in self.operated:  # no operation reached it: nothing to read again
            return []
        self.shots[path] = after = self.base.texts_of(doc) if kind == "texts" else data_of(doc)
        if kind == "texts":
            return text_targets(path, before, after)
        changes = data_changes(before, after)
        return [Target(path, "", CHANGED, changes=changes)] if changes else []

    def asset_targets(self, path: str, doc: PatchDoc, before: dict[str, int]) -> list[Target]:
        after, out = _counts(doc), []
        for guid in sorted((g for g in after if after[g] > before.get(g, 0)), key=guid_key):
            if before.get(guid, 0):
                self.again.append((path, guid))
            target = self._added(path, doc, guid)
            if self.remember:
                self.last[(path, guid)] = target.packed
            out.append(target)
        for guid in sorted((g for g in before if g not in after), key=guid_key):
            name, template = self.base.vanilla_info(path, guid)
            self.last.pop((path, guid), None)
            out.append(Target(path, guid, REMOVED, name, template))
        for guid in sorted((g for g in self.touched.get(path, set()) if g in before and g in after), key=guid_key):
            el = doc.by_guid[guid][-1]
            if doc.is_lazy(el):
                if doc.notes.get(el) is None:
                    continue  # an asset of the game's file no operation looked into
                el = doc.peek(el)
            now, was = element_data(el), self._state(path, guid)
            self.last[(path, guid)] = now
            changes = data_changes(was, now) if was is not None else []
            if changes:
                name, template = asset_facts(el)
                out.append(Target(path, guid, CHANGED, name, template, changes))
        return out

    @staticmethod
    def _added(path: str, doc: PatchDoc, guid: str) -> Target:
        """The target of the asset ``guid`` the document now holds (its last definition) as an added one."""
        el = doc.by_guid[guid][-1]
        note = doc.notes.get(el)
        (name, template), packed = note if note is not None else (asset_facts(el), b"")
        target = Target(path, guid, ADDED, name, template, packed=packed)
        if not packed:
            target.set_values(element_data(el))
        return target

    def _state(self, path: str, guid: str):
        """An asset's data before the running mod: what an earlier mod left, else the game's own."""
        if (path, guid) not in self.last:
            vanilla = self.base.vanilla_asset(path, guid)
            self.last[(path, guid)] = None if vanilla is None else element_data(vanilla)
        data = self.last[(path, guid)]
        return json.loads(zlib.decompress(data)) if isinstance(data, bytes) else data

    # all steps against the game
    def final(self, steps: list[ModEffect]) -> list[Target]:
        for target in self.nets.values():
            self._add_writers(target, steps)
        return sorted(self.nets.values(), key=lambda t: (t.file, guid_key(t.key)))

    @staticmethod
    def _add_writers(target: Target, steps: list[ModEffect]) -> None:
        """The mods that only set a value of ``target`` to the one it had count as touching it, in load order."""
        writers = {s.mod_id for s in steps if target.ident in s.writes}
        if writers - set(target.mods):
            target.mods = [s.mod_id for s in steps if s.mod_id in writers | set(target.mods)]

    def _net(self, ident: tuple[str, str], found: list[Target]) -> Target | None:
        path, key = ident
        if len(found) == 1:  # one mod touched it: its step is the whole against the game
            return found[0]
        first, last = found[0], found[-1]
        mods = list(dict.fromkeys(m for t in found for m in t.mods))
        name, template = next(((t.name, t.template) for t in reversed(found) if t.name), ("", ""))
        if last.kind == REMOVED:
            return None if first.kind == ADDED else Target(path, key, REMOVED, name, template, mods=mods)
        if not key:  # a file's data: against the game's own
            return self._net_data(path, found, mods)
        if self.language(path) is not None:
            before, after = found[0].changes[0].before, last.changes[0].after
            change = [Change("Text", before, after)]
            kind = ADDED if before is None else CHANGED
            return None if before == after else Target(path, key, kind, changes=change, mods=mods)
        doc = self.session.docs[path]
        if first.kind == ADDED:
            target = self._added(path, doc, key)
            target.name, target.template, target.mods = name or target.name, template or target.template, mods
            return target
        el = doc.by_guid[key][-1]
        was = self.base.vanilla_asset(path, key)
        changes = data_changes(element_data(was), element_data(el)) if was is not None else []
        return Target(path, key, CHANGED, name, template, changes, mods) if changes else None

    def _net_data(self, path: str, found: list[Target], mods: list[str]) -> Target | None:
        vanilla = self.base.vanilla_doc(path)
        changes = data_changes(data_of(vanilla) if vanilla else {}, data_of(self.session.docs[path]))
        return Target(path, "", CHANGED, changes=changes, mods=mods) if changes else None


def _counts(doc: PatchDoc) -> dict[str, int]:
    """How many assets of each GUID a document holds (a GUID added twice counts two)."""
    return {guid: len(found) for guid, found in doc.by_guid.items()}


def data_of(doc: PatchDoc) -> dict:
    return {root.tag: element_data(root) for root in doc.roots}


def text_targets(path: str, before: dict[str, str], after: dict[str, str]) -> list[Target]:
    out = []
    for tid in sorted(before.keys() | after.keys(), key=guid_key):
        was, now = before.get(tid), after.get(tid)
        if was != now:
            kind = ADDED if was is None else REMOVED if now is None else CHANGED
            out.append(Target(path, tid, kind, changes=[Change("Text", was, now)]))
    return out
