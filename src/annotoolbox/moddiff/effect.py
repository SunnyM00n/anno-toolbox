"""What mods do to the game's files, one mod at a time: the mod loader's run (``modloader.engine.CSession``) with every
mod's **step** kept, the changes it made on top of the mods before it, and the whole against the game.

``run_mods(game, mods, variables)`` applies the mods in this order and returns an ``SEffect``: ``steps`` (a
``SModEffect`` per mod: its targets, its files, what the loader would drop) and ``targets`` (all steps against the
game). Targets are assets by GUID, texts by id and other files whole (``docs/mod-diff.md`` "What a change is").
Only what an operation opened is compared: an untouched asset of a large file is never parsed.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..assetedit.assetgame import CGame
from ..infra.i18n import tr
from ..modloader.engine import INFO, CSession, SEvent
from ..modloader.mods import CMod, CVariables, patch_target
from ..modloader.patchdoc import CPatchDoc
from .base import CGameBase
from .elementdata import asset_facts, data_changes, element_data
from .filechanges import mod_files
from .model import ADDED, CHANGED, REMOVED, CTarget, SChange, SFileChange
from .writes import SWrite, guid_of, set_values

BINARY = "export.bin"  # the binary infotips: kept as a file, not read


@dataclass
class SModEffect:
    """One mod's step: what it changed on top of the mods before it."""

    mod_id: str
    label: str  # "id version"
    mod: CMod | None = None
    targets: dict[tuple[str, str], CTarget] = field(default_factory=dict)
    files: list[SFileChange] = field(default_factory=list)
    events: list[SEvent] = field(default_factory=list)
    writes: dict[tuple[str, str], dict[str, str]] = field(
        default_factory=dict
    )  # (file, GUID) -> value path -> value set


@dataclass
class SEffect:
    """A run of the loader: each mod's step, and every step together against the game."""

    game: str
    steps: list[SModEffect] = field(default_factory=list)
    targets: list[CTarget] = field(default_factory=list)
    files: list[SFileChange] = field(default_factory=list)  # a path two mods carry once, the later mod's

    @property
    def events(self) -> list[SEvent]:
        return [event for step in self.steps for event in step.events]


def guid_key(text: str) -> tuple:
    """A sort key for GUIDs and text ids: the numbers first, in number order."""
    return (0, int(text), "") if text.lstrip("-").isdigit() else (1, 0, text)


def run_mods(
    game: CGame,
    mods: list[CMod],
    variables: CVariables,
    progress: Callable[[str], None] | None = None,
    compare_bytes: int = 2000,
) -> SEffect:
    """``mods`` applied in this order to the game's files. ``compare_bytes``: how many replaced files are compared
    byte for byte with the game's (to tell a file that is the game's own from one that is not)."""
    base = CGameBase(game)
    run = CRun(base, progress or (lambda _message: None))
    effect = SEffect(game.m_layout.key)
    patched = [{target for rel, _patch in one_mod.patch_files() if (target := patch_target(rel))} for one_mod in mods]
    for number, mod in enumerate(mods):
        run.m_remember = number < len(mods) - 1  # what a later mod starts from is kept only when one follows
        later = set().union(*patched[number + 1 :])  # a file no later mod patches is done with after this mod
        effect.steps.append(run.step(mod, variables, compare_bytes, later))
    effect.targets = run.final(effect.steps)
    carried: dict[str, SFileChange] = {}
    for step in effect.steps:
        for change in step.files:
            before = carried.get(change.path)
            carried[change.path] = change if before is None else _later(before, change)
    effect.files = sorted(carried.values(), key=lambda change: change.path)
    return effect


def _later(before: SFileChange, change: SFileChange) -> SFileChange:
    """The file a later mod carries over an earlier one's: its content, both mods named."""
    return SFileChange(
        change.path, change.kind, change.size_bytes, [*before.mods, *change.mods], change.digest, change.source
    )


class CRun:
    """The loader's session, and the last known data of every asset a mod has touched (its step's "before")."""

    def __init__(self, base: CGameBase, say: Callable[[str], None], remember: bool = True) -> None:
        self.m_base, self.m_say, self.m_remember = base, say, remember
        self.m_language = base.language_of()
        self.m_session = CSession(
            base.read_base, base.lazy, self.observe, self.record, m_keep=self.keep, m_game=base.m_layout.key
        )
        self.m_last: dict[tuple[str, str], object] = {}  # an asset's data (or its packed form) after the last step
        self.m_touched: dict[str, set[str]] = {}  # file -> the GUIDs an operation of the running mod reached
        self.m_operated: set[str] = set()  # the files an operation of the running mod reached at all
        self.m_shots: dict[str, dict] = {}  # a text or data file as data after the last step: the next one's start
        self.m_writes: dict[tuple[str, str], dict[str, str]] = {}  # the running mod's single values set, even if equal
        self.m_again: list[tuple[str, str]] = []  # (file, GUID) the running mod adds though the file has it
        self.m_pending: list[SWrite] = []  # the values the operation about to run will calculate
        self.m_steps: list[SModEffect] = []  # the steps so far, the running one last
        self.m_nets: dict[tuple[str, str], CTarget] = {}  # a target against the game, made when its file was let go

    @staticmethod
    def keep(_doc: CPatchDoc, element) -> tuple:
        """What is read from an asset the loader adds before it is packed: its name, template and values (packed)."""
        values = CTarget("", "", ADDED)
        values.set_values(element_data(element))
        return asset_facts(element), values.m_packed

    def observe(self, _mod: str, path: str, _kind: str, nodes: list) -> None:
        self.m_operated.add(path)
        doc = self.m_session.m_docs.get(path)
        pending, self.m_pending = self.m_pending, []
        if doc is None:
            return
        for write in pending:  # the values a ``ModValue`` gave are in the document now
            self.m_writes.setdefault((path, write.guid), {})[write.path] = write.settle(doc)
        for node in nodes:
            if guid := guid_of(doc, node):
                self.m_touched.setdefault(path, set()).add(guid)

    def record(self, _mod: str, path: str, kind: str, targets: list, content: list) -> None:
        doc = self.m_session.m_docs.get(path)
        if doc is not None:
            for write in set_values(doc, kind, targets, content):
                if write.value is None:
                    self.m_pending.append(write)
                else:
                    self.m_writes.setdefault((path, write.guid), {})[write.path] = write.value

    # one mod
    def step(self, mod: CMod, variables: CVariables, compare_bytes: int, later: set[str] = frozenset()) -> SModEffect:
        """One mod, a patch file at a time: each file's document is opened, patched and compared, and let go once no
        later mod patches it (``later``: the files they patch), so only one large file is held at once."""
        self.m_say(tr("common.applying_mod", mod=mod.m_mod_id))
        step = SModEffect(mod.m_mod_id, f"{mod.m_mod_id} {mod.m_version}".strip(), mod)
        self.m_steps.append(step)
        files = [(rel, patch_target(rel)) for rel, _path in mod.patch_files()]
        last = {target: rel for rel, target in files if target}
        self.m_touched.clear()
        self.m_operated = set()
        self.m_writes, self.m_again = {}, []
        seen = len(self.m_session.m_events)
        before: dict[str, object] = {}
        for rel, target in files:
            if (
                target
                and not target.endswith(BINARY)
                and target not in before
                and (doc := self.m_session.document(target))
            ):
                before[target] = self._snapshot(target, doc)
            self.m_session.apply_file(mod, rel, mod.read(rel), variables)
            if target in before and last[target] == rel:
                self._finish(step, target, before.pop(target), target not in later)
        step.events = self.m_session.m_events[seen:]
        step.writes = self.m_writes
        step.events += [
            SEvent(mod.m_mod_id, path, "duplicate", f"GUID {guid} is added again: the file already has it", INFO)
            for path, guid in self.m_again
        ]
        step.files = mod_files(self.m_base, mod, compare_bytes)
        for change in step.files:
            change.mods = [mod.m_mod_id]
        return step

    def _finish(self, step: SModEffect, path: str, before, done: bool) -> None:
        """The targets the running mod made of ``path``; ``done``: no later mod patches it, so its net result against
        the game is made now and the document let go."""
        for target in self._targets(path, self.m_session.m_docs[path], before):
            target.m_mods = [step.mod_id]
            step.targets[target.ident] = target
        if not done:
            return
        for ident in {i for step in self.m_steps for i in step.targets if i[0] == path}:
            net = self._net(ident, [step.targets[ident] for step in self.m_steps if ident in step.targets])
            if net is not None:
                self.m_nets[ident] = net
        del self.m_session.m_docs[path]
        self.m_shots.pop(path, None)
        for key in [k for k in self.m_last if k[0] == path]:
            del self.m_last[key]

    def _kind(self, path: str, doc: CPatchDoc) -> str:
        if self.m_language(path) is not None and self.m_base.m_layout.text_format == "xml":
            return "texts"
        return "assets" if doc.m_by_guid or doc.m_lazy or path in self.m_base.m_files else "data"

    def _snapshot(self, path: str, doc: CPatchDoc):
        kind = self._kind(path, doc)
        if kind == "assets":
            return _counts(doc)
        if path not in self.m_shots:  # the first look: the file is still the game's own (or what earlier mods left)
            self.m_shots[path] = self.m_base.texts_of(doc) if kind == "texts" else data_of(doc)
        return self.m_shots[path]

    def _targets(self, path: str, doc: CPatchDoc, before) -> list[CTarget]:
        kind = self._kind(path, doc)
        if kind == "assets":
            return self.asset_targets(path, doc, before)
        if path not in self.m_operated:  # no operation reached it: nothing to read again
            return []
        self.m_shots[path] = after = self.m_base.texts_of(doc) if kind == "texts" else data_of(doc)
        if kind == "texts":
            return text_targets(path, before, after)
        changes = data_changes(before, after)
        return [CTarget(path, "", CHANGED, m_changes=changes)] if changes else []

    def asset_targets(self, path: str, doc: CPatchDoc, before: dict[str, int]) -> list[CTarget]:
        after, out = _counts(doc), []
        for guid in sorted(
            (candidate for candidate in after if after[candidate] > before.get(candidate, 0)), key=guid_key
        ):
            if before.get(guid, 0):
                self.m_again.append((path, guid))
            target = self._added(path, doc, guid)
            if self.m_remember:
                self.m_last[(path, guid)] = target.m_packed
            out.append(target)
        for guid in sorted((candidate for candidate in before if candidate not in after), key=guid_key):
            name, template = self.m_base.vanilla_info(path, guid)
            self.m_last.pop((path, guid), None)
            out.append(CTarget(path, guid, REMOVED, name, template))
        for guid in sorted(
            (candidate for candidate in self.m_touched.get(path, set()) if candidate in before and candidate in after),
            key=guid_key,
        ):
            element = doc.m_by_guid[guid][-1]
            if doc.is_lazy(element):
                if doc.m_notes.get(element) is None:
                    continue  # an asset of the game's file no operation looked into
                element = doc.peek(element)
            now, was = element_data(element), self._state(path, guid)
            self.m_last[(path, guid)] = now
            changes = data_changes(was, now) if was is not None else []
            if changes:
                name, template = asset_facts(element)
                out.append(CTarget(path, guid, CHANGED, name, template, changes))
        return out

    @staticmethod
    def _added(path: str, doc: CPatchDoc, guid: str) -> CTarget:
        """The target of the asset ``guid`` the document now holds (its last definition) as an added one."""
        element = doc.m_by_guid[guid][-1]
        note = doc.m_notes.get(element)
        (name, template), packed = note if note is not None else (asset_facts(element), b"")
        target = CTarget(path, guid, ADDED, name, template, m_packed=packed)
        if not packed:
            target.set_values(element_data(element))
        return target

    def _state(self, path: str, guid: str):
        """An asset's data before the running mod: what an earlier mod left, else the game's own."""
        if (path, guid) not in self.m_last:
            vanilla = self.m_base.vanilla_asset(path, guid)
            self.m_last[(path, guid)] = None if vanilla is None else element_data(vanilla)
        data = self.m_last[(path, guid)]
        return json.loads(zlib.decompress(data)) if isinstance(data, bytes) else data

    # all steps against the game
    def final(self, steps: list[SModEffect]) -> list[CTarget]:
        for target in self.m_nets.values():
            self._add_writers(target, steps)
        return sorted(self.m_nets.values(), key=lambda target: (target.m_file, guid_key(target.m_key)))

    @staticmethod
    def _add_writers(target: CTarget, steps: list[SModEffect]) -> None:
        """The mods that only set a value of ``target`` to the one it had count as touching it, in load order."""
        writers = {step.mod_id for step in steps if target.ident in step.writes}
        if writers - set(target.m_mods):
            target.m_mods = [step.mod_id for step in steps if step.mod_id in writers | set(target.m_mods)]

    def _net(self, ident: tuple[str, str], found: list[CTarget]) -> CTarget | None:
        path, key = ident
        if len(found) == 1:  # one mod touched it: its step is the whole against the game
            return found[0]
        first, last = found[0], found[-1]
        mods = list(dict.fromkeys(mod for found_target in found for mod in found_target.m_mods))
        name, template = next(
            ((found_target.m_name, found_target.m_template) for found_target in reversed(found) if found_target.m_name),
            ("", ""),
        )
        if last.m_kind == REMOVED:
            return None if first.m_kind == ADDED else CTarget(path, key, REMOVED, name, template, m_mods=mods)
        if not key:  # a file's data: against the game's own
            return self._net_data(path, found, mods)
        if self.m_language(path) is not None:
            before, after = found[0].m_changes[0].before, last.m_changes[0].after
            change = [SChange("Text", before, after)]
            kind = ADDED if before is None else CHANGED
            return None if before == after else CTarget(path, key, kind, m_changes=change, m_mods=mods)
        doc = self.m_session.m_docs[path]
        if first.m_kind == ADDED:
            target = self._added(path, doc, key)
            target.m_name, target.m_template, target.m_mods = name or target.m_name, template or target.m_template, mods
            return target
        element = doc.m_by_guid[key][-1]
        was = self.m_base.vanilla_asset(path, key)
        changes = data_changes(element_data(was), element_data(element)) if was is not None else []
        return CTarget(path, key, CHANGED, name, template, changes, mods) if changes else None

    def _net_data(self, path: str, found: list[CTarget], mods: list[str]) -> CTarget | None:
        vanilla = self.m_base.vanilla_doc(path)
        changes = data_changes(data_of(vanilla) if vanilla else {}, data_of(self.m_session.m_docs[path]))
        return CTarget(path, "", CHANGED, m_changes=changes, m_mods=mods) if changes else None


def _counts(doc: CPatchDoc) -> dict[str, int]:
    """How many assets of each GUID a document holds (a GUID added twice counts two)."""
    return {guid: len(found) for guid, found in doc.m_by_guid.items()}


def data_of(doc: CPatchDoc) -> dict:
    return {root.tag: element_data(root) for root in doc.roots}


def text_targets(path: str, before: dict[str, str], after: dict[str, str]) -> list[CTarget]:
    out = []
    for tid in sorted(before.keys() | after.keys(), key=guid_key):
        was, now = before.get(tid), after.get(tid)
        if was != now:
            kind = ADDED if was is None else REMOVED if now is None else CHANGED
            out.append(CTarget(path, tid, kind, m_changes=[SChange("Text", was, now)]))
    return out
