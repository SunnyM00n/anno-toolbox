"""A mod (or a profile of mods) opened in the asset editor: its ModOps applied to the game's files by the mod
loader's rules, the result compared with the game, the difference turned into a change set, and a summary of what
was imported and what the change set cannot hold.

``import_folder(game, folder)`` imports one mod, ``import_profile(game, profile, folders)`` the mods a profile
switches on; both return an ``Imported`` (its ``changes`` the change set, ``lines()`` the summary). The patching is
``modloader.engine``, the per-asset difference ``assetdiff``. ``docs/tool-suite.md`` section 17.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, tr
from ..modloader import mods as modlist
from ..modloader.engine import WARNING, Event, Session
from ..modloader.mods import GAME_NUMBER, Mod, Variables
from ..modloader.patchdoc import PatchDoc, canonical
from ..modloader.profilefile import ProfileText
from ..modloader.resolve import Resolution, resolve, resolve_profile
from . import assetdiff
from .assetgame import TEXT_KEYS, Game
from .changeset import Carried, ChangeSet, NewAsset
from .xmltree import to_text

SHOWN = 12  # lines of each kind the summary shows


@dataclass
class Imported:
    changes: ChangeSet
    mods: list[Mod]
    events: list[Event] = field(default_factory=list)
    forms: Counter = field(default_factory=Counter)  # changed assets by the form they took (``assetdiff``)
    added: list[int] = field(default_factory=list)
    changed: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    values: int = 0  # values set, reset and blocks replaced
    texts: dict[str, int] = field(default_factory=dict)
    lost: list[str] = field(default_factory=list)  # what the change set cannot hold
    overridden: dict[int, list[str]] = field(default_factory=dict)  # asset -> the mods that changed it, in order
    carried: list[str] = field(default_factory=list)
    session: Session | None = None
    resolution: Resolution | None = None  # which mods came in how (nested, dependencies), what is missing
    archive: str = ""  # the archive an import read (``archiveimport``) instead of mods

    @property
    def dropped(self) -> list[Event]:
        """The operations the loader would drop or log as a problem."""
        return [e for e in self.events if e.level == WARNING]

    @property
    def conditional(self) -> list[Event]:
        return [e for e in self.events if e.code == "modcondition"]

    def lines(self) -> list[str]:
        out = [
            tr("archive {name}", name=self.archive)
            if self.archive
            else tr(
                "{n} mod(s): {list}",
                n=len(self.mods),
                list=", ".join(f"{m.mod_id} {m.version}".strip() for m in self.mods),
            ),
            *(self.resolution.lines() if self.resolution else []),
            tr(
                "assets: {added} added, {changed} changed, {removed} removed; {values} values; forms: {forms}",
                added=len(self.added),
                changed=len(self.changed),
                removed=len(self.removed),
                values=self.values,
                forms=", ".join(f"{n} {k}" for k, n in sorted(self.forms.items())),
            ),
            tr(
                "texts: {list}",
                list=", ".join(f"{lang} {n}" for lang, n in sorted(self.texts.items())) or tr("none"),
            ),
            tr("carried files (written with the mod as they are): {n}", n=len(self.carried)),
        ]
        sections = (
            (N_("operations that matched nothing or that the loader drops"), [e.line() for e in self.dropped]),
            (N_("conditions on other mods (the import took this branch)"), [e.line() for e in self.conditional]),
            (N_("changed by more than one mod (the change set keeps the end result)"), self._overrides()),
            (N_("what the change set cannot hold"), self.lost),
        )
        for title, rows in sections:
            if rows:
                out.append(f"{tr(title)}: {len(rows)}")
                out += [f"  {r}" for r in rows[:SHOWN]] + (
                    ["  " + tr("... and {n} more", n=len(rows) - SHOWN)] if len(rows) > SHOWN else []
                )
        return out

    def _overrides(self) -> list[str]:
        return [f"{g}: {f' {tr("then")} '.join(ms)}" for g, ms in sorted(self.overridden.items()) if len(ms) > 1]


def import_folder(
    game: Game, folder: Path, progress: Callable[[str], None] | None = None, search: list[Path] | None = None
) -> Imported:
    """One mod folder with the mods inside it (nested) and the mods it requires that sit in ``search`` (default: the
    folder's neighbours), in the loader's order; its options as ``active-options.jsonc`` beside it sets them. No
    profile names them, so nothing else is on. The change set is the folder's mod's."""
    mod, why = modlist.read_mod(Path(folder), GAME_NUMBER.get(game.layout.key))
    if mod is None:
        raise ValueError(why)
    here = Path(folder).resolve()
    roots = search if search is not None else [Path(folder).parent]
    others = [m for m in modlist.find_mods(roots, mod.anno)[0] if m.folder.resolve() != here]
    res = resolve([mod], others, pull=True)
    chosen = modlist.read_options(Path(folder).parent)
    return import_mods(game, res.order, Variables(res.order, chosen), progress, resolution=res, primary=mod)


def _own_mods(primary: Mod) -> list[Mod]:
    """The mod and every mod inside its folder, at any depth: what the mod itself ships."""
    out, stack = [], [primary]
    while stack:
        one = stack.pop(0)
        out.append(one)
        stack += one.nested
    return out


def import_profile(game: Game, profile: Path, folders: list[Path], progress=None) -> Imported:
    """The mods ``profile`` switches on, found in ``folders`` (the loader's), with their nested mods, in the loader's
    order."""
    found, notes = modlist.find_mods(folders, GAME_NUMBER.get(game.layout.key))
    prof = ProfileText.from_bytes(Path(profile).read_bytes()).to_profile(Path(profile).stem)
    new_roots = tuple(f for f in folders if Path(f).name != modlist.UBI)
    res = resolve_profile(found, prof, new_roots)
    if not res.order:
        raise ValueError(
            tr(
                "the profile {profile} switches on no mod found in {folders}",
                profile=profile,
                folders=", ".join(map(str, folders)),
            )
        )
    chosen = modlist.read_options(Path(profile).parent.parent)
    result = import_mods(game, res.order, Variables(res.order, chosen), progress, resolution=res)
    result.lost += notes
    return result


def import_mods(
    game: Game,
    mods: list[Mod],
    variables: Variables,
    progress=None,
    resolution: Resolution | None = None,
    primary: Mod | None = None,
) -> Imported:
    """``mods`` applied in this order to the game's files, and the result as a change set (``primary``: the mod whose
    identity it takes, else the last)."""
    say = progress or (lambda _t: None)
    asset_path = game.layout.patch_target.lower()
    run = _Run(asset_path)
    session = run.session = Session(
        game.read_base, game.lazy_ranges, run.observe, keep=_added, game=game.layout.key
    )  # added assets are kept as bytes (``PatchDoc.freeze``) and read with ``peek`` (docs/mod-diff.md "Speed and memory")
    for mod in mods:
        say(f"applying {mod.mod_id}")
        session.apply_mod(mod, variables)
    last = primary or mods[-1]
    own = _own_mods(primary) if primary is not None else list(mods)
    changes = ChangeSet(game.layout.key, last.mod_id, last.name or last.mod_id, last.version or "0.1.0")
    changes.requires = list(dict.fromkeys(m.top.mod_id for m in mods if m not in own))  # a library, not its inner mods
    result = Imported(changes, list(mods), list(session.events), session=session, resolution=resolution)
    say("comparing with the game")
    doc = session.docs.get(asset_path)
    if doc is not None:
        assets_against(game, doc, result)
    _texts(game, session, result)
    _carry(game, own, result)
    _leave_out_required(run, {m.mod_id for m in own}, result)
    result.overridden = {g: ms for g, ms in run.touched.items() if g in changes.assets}
    if len(mods) > 1:
        result.lost.append(
            tr("{n} mods in one change set: it takes the identity of {id}", n=len(mods), id=last.mod_id)
            if primary
            else tr("{n} mods in one change set: it takes the identity of the last, {id}", n=len(mods), id=last.mod_id)
        )
    return result


class _Run:
    """What the session is told: per asset, the mods whose operations touched it (the files it reads are the game's:
    ``Game.read_base``, ``Game.lazy_ranges``)."""

    def __init__(self, asset_path: str) -> None:
        self.asset_path = asset_path
        self.touched: dict[int, list[str]] = {}
        self.session: Session | None = None

    def observe(self, mod_id: str, path: str, kind: str, nodes: list) -> None:
        doc = self.session.docs.get(path) if self.session else None
        if doc is None or path != self.asset_path or kind == "remove":
            return
        for node in nodes:
            guid = _num(doc, doc.asset_of(node)) if isinstance(node, ET.Element) else None
            if guid is not None:
                users = self.touched.setdefault(guid, [])
                if not users or users[-1] != mod_id:
                    users.append(mod_id)


def _leave_out_required(run: _Run, own: set[str], result: Imported) -> None:
    """The assets only a required mod (not the imported one or its nested mods) touched are not the change set's: that
    mod stays a dependency (``ChangeSet.requires``), so the built mod does not ship its content twice."""
    changes = result.changes
    foreign = {g for g, users in run.touched.items() if not own.intersection(users)}
    for guid in sorted(foreign & {*changes.assets, *changes.new}):
        changes.assets.pop(guid, None)
        changes.new.pop(guid, None)
        for found in (result.changed, result.added):
            if guid in found:
                found.remove(guid)
    if foreign:
        result.lost.append(
            tr(
                "{n} asset(s) only a required mod ({mods}) changes are left to it: the built mod requires it instead",
                n=len(foreign),
                mods=", ".join(changes.requires),
            )
        )


def assets_against(game: Game, doc: PatchDoc, result: Imported, path: str = "") -> None:
    """The game's assets of the asset file ``path`` (the layout's ModOps target when empty) against the patched
    ones in ``doc``: removed, changed, added."""
    index, changes = game.index, result.changes
    file = next((k for k, p in enumerate(index.files) if p.lower() == (path or game.layout.patch_target).lower()), None)
    vanilla = [e for e in index.entries if e.file == file]
    known = {e.guid for e in vanilla}
    for e in vanilla:
        found = doc.by_guid.get(str(e.guid))
        if not found:
            if index.by_guid.get(e.guid) is e:  # a definition that another file overrides is not the game's asset
                changes.removed.append(e.guid)
                result.removed.append(e.guid)
            continue
        el, before = found[-1], index.element_of(e)  # a GUID defined twice in the patched file: the later one wins
        if doc.is_lazy(el) and el not in doc.notes:  # the game's own bytes, untouched
            continue
        el = doc.peek(el)  # an asset a mod added again with the game's GUID is kept as bytes
        if canonical(el) == canonical(before):
            continue
        change, form = assetdiff.asset_change(e.guid, before, el)
        changes.assets[e.guid] = change
        result.changed.append(e.guid)
        result.forms[form] += 1
        result.values += len(change.set) + len(change.reset) + len(change.blocks) + (1 if change.whole else 0)
    _new_assets(doc, known, result)


def _new_assets(doc: PatchDoc, known: set[int], result: Imported) -> None:
    """The assets the mods added, in file order, each anchored to its neighbour in the patched file."""
    seen: set[int] = set()
    for holder in doc.top.iter("Assets"):
        row = [a for a in holder if a.tag == "Asset"]
        guids = [_num(doc, a) for a in row]
        after_known: list[int | None] = [None] * (len(row) + 1)  # the next game asset from each place on
        for k in range(len(row) - 1, -1, -1):
            after_known[k] = guids[k] if guids[k] in known else after_known[k + 1]
        prev = None
        for k, (asset, guid) in enumerate(zip(row, guids, strict=True)):
            if guid is not None and guid not in known:
                if guid in seen:
                    result.lost.append(tr("asset {guid} was added twice; the change set keeps the first", guid=guid))
                else:
                    seen.add(guid)
                    anchor, place = (prev, "after") if prev is not None else (after_known[k + 1], "before")
                    _add_new(result, guid, doc.peek(asset), anchor, place)
            prev = guid if guid is not None else prev


def _add_new(result: Imported, guid: int, asset: ET.Element, anchor: int | None, place: str) -> None:
    if anchor is None:
        result.lost.append(
            tr("asset {guid} stands alone in its list: written to the asset file's default group", guid=guid)
        )
    base = (asset.findtext("BaseAssetGUID") or "").strip()
    template = (asset.findtext("Template") or "").strip()
    name = (asset.findtext("Values/Standard/Name") or "").strip()
    record = NewAsset(template, name, int(base) if base.isdigit() else None, None, anchor, place, to_text(asset))
    result.changes.new[guid] = record
    result.added.append(guid)


def _added(_doc: PatchDoc, _asset: ET.Element) -> bool:
    """The note a kept added asset carries (``PatchDoc.notes``): it marks the asset as a mod's, not the game's."""
    return True


def _num(doc: PatchDoc, asset: ET.Element | None) -> int | None:
    text = doc.guid_text.get(asset, "") if asset is not None else ""
    return int(text) if text.strip().isdigit() else None


def _texts(game: Game, session: Session, result: Imported) -> None:
    pattern = game.layout.texts
    if not pattern:
        return
    head, tail = pattern.split("{language}")
    key = TEXT_KEYS[0] if game.layout.key == "anno117" else TEXT_KEYS[1]
    for path, doc in session.docs.items():
        if not (path.startswith(head.lower()) and path.endswith(tail.lower())):
            continue
        language = path[len(head) : len(path) - len(tail)]
        if language == "metadata":
            continue
        base = session.read_base(path)
        vanilla = PatchDoc.load(base).roots[0] if base else None
        changed, lost = assetdiff.text_changes(vanilla, doc.roots[0], key)
        if changed:
            result.changes.texts[language] = changed
            result.texts[language] = len(changed)
        result.lost += [f"{language}: {line}" for line in lost]


def _carry(game: Game, mods: list[Mod], result: Imported) -> None:
    """Every file of the mods the change set does not write itself is carried; a path two mods have, reported."""
    layout = game.layout
    owned = {layout.patch_target.lower()} | {lang_path.lower() for lang_path in _text_paths(layout, result)}
    seen: dict[str, str] = {}
    for mod in mods:
        mine = []
        for rel in mod.files():
            low = rel.lower()
            if low in owned or low in ("modinfo.json", "modinfo.jsonc"):
                continue
            if low in seen:
                result.lost.append(
                    tr(
                        "{path}: both {first} and {second} have it; the build keeps {second}'s",
                        path=rel,
                        first=seen[low],
                        second=mod.mod_id,
                    )
                )
            seen[low] = mod.mod_id
            mine.append(rel)
        result.carried += mine
        result.changes.carry.append(Carried(str(mod.folder), sorted(owned)))


def _text_paths(layout, result: Imported) -> list[str]:
    """The language files the change set writes: those it holds texts for, and those the mods patch."""
    if not layout.texts:
        return []
    head, tail = layout.texts.split("{language}")
    langs = set(result.changes.texts)
    for mod in result.mods:
        for rel, _path in mod.patch_files():
            low = rel.lower()
            if low.startswith(head.lower()) and low.endswith(tail.lower()):
                lang = low[len(head) : len(low) - len(tail)]
                if lang != "metadata":
                    langs.add(lang)
    return [layout.texts.format(language=lang) for lang in sorted(langs)]
