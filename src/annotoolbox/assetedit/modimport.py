"""A mod (or a profile of mods) opened in the asset editor: its ModOps applied to the game's files by the mod
loader's rules, the result compared with the game, the difference turned into a change set, and a summary of what
was imported and what the change set cannot hold.

``import_folder(game, folder)`` imports one mod, ``import_profile(game, profile, folders)`` the mods a profile
switches on; both return an ``SImported`` (its ``changes`` the change set, ``lines()`` the summary). The patching is
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
from ..modloader.engine import WARNING, CSession, SEvent
from ..modloader.mods import GAME_NUMBER, CMod, CVariables
from ..modloader.patchdoc import CPatchDoc, canonical
from ..modloader.profilefile import CProfileText
from ..modloader.resolve import SResolution, resolve, resolve_profile
from . import assetdiff
from .assetgame import TEXT_KEYS, CGame
from .changeset import CChangeSet, SCarried, SNewAsset
from .gamefiles import SLayout
from .xmltree import to_text

SHOWN = 12  # lines of each kind the summary shows


@dataclass
class SImported:
    changes: CChangeSet
    mods: list[CMod]
    events: list[SEvent] = field(default_factory=list)
    forms: Counter[str] = field(default_factory=Counter)  # changed assets by the form they took (``assetdiff``)
    added: list[int] = field(default_factory=list)
    changed: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    values: int = 0  # values set, reset and blocks replaced
    texts: dict[str, int] = field(default_factory=dict)
    lost: list[str] = field(default_factory=list)  # what the change set cannot hold
    overridden: dict[int, list[str]] = field(default_factory=dict)  # asset -> the mods that changed it, in order
    carried: list[str] = field(default_factory=list)
    session: CSession | None = None
    resolution: SResolution | None = None  # which mods came in how (nested, dependencies), what is missing
    archive: str = ""  # the archive an import read (``archiveimport``) instead of mods

    @property
    def dropped(self) -> list[SEvent]:
        """The operations the loader would drop or log as a problem."""
        return [event for event in self.events if event.level == WARNING]

    @property
    def conditional(self) -> list[SEvent]:
        return [event for event in self.events if event.code == "modcondition"]

    def lines(self) -> list[str]:
        out = [
            tr("assets.archive_name", name=self.archive)
            if self.archive
            else tr(
                "assets.n_mods_list",
                n=len(self.mods),
                list=", ".join(f"{mod.m_mod_id} {mod.m_version}".strip() for mod in self.mods),
            ),
            *(self.resolution.lines() if self.resolution else []),
            tr(
                "assets.assets_added_changed_removed_values",
                added=len(self.added),
                changed=len(self.changed),
                removed=len(self.removed),
                values=self.values,
                forms=", ".join(f"{count} {k}" for k, count in sorted(self.forms.items())),
            ),
            tr(
                "assets.texts_list",
                list=", ".join(f"{lang} {count}" for lang, count in sorted(self.texts.items())) or tr("common.none"),
            ),
            tr("assets.carried_files_written_mod_they", n=len(self.carried)),
        ]
        sections = (
            (N_("assets.operations_matched_nothing_loader_drops"), [event.line() for event in self.dropped]),
            (N_("assets.conditions_other_mods_import_took"), [event.line() for event in self.conditional]),
            (N_("assets.changed_more_one_mod_change"), self._overrides()),
            (N_("assets.what_change_set_cannot_hold"), self.lost),
        )
        for title, rows in sections:
            if rows:
                out.append(f"{tr(title)}: {len(rows)}")
                out += [f"  {row}" for row in rows[:SHOWN]] + (
                    ["  " + tr("assets.and_n_more", n=len(rows) - SHOWN)] if len(rows) > SHOWN else []
                )
        return out

    def _overrides(self) -> list[str]:
        return [
            f"{guid}: {f' {tr("assets.then")} '.join(mod_ids)}"
            for guid, mod_ids in sorted(self.overridden.items())
            if len(mod_ids) > 1
        ]


def import_folder(
    game: CGame, folder: Path, progress: Callable[[str], None] | None = None, search: list[Path] | None = None
) -> SImported:
    """One mod folder with the mods inside it (nested) and the mods it requires that sit in ``search`` (default: the
    folder's neighbours), in the loader's order; its options as ``active-options.jsonc`` beside it sets them. No
    profile names them, so nothing else is on. The change set is the folder's mod's."""
    mod, why = modlist.read_mod(Path(folder), GAME_NUMBER.get(game.m_layout.key))
    if mod is None:
        raise ValueError(why)
    here = Path(folder).resolve()
    roots = search if search is not None else [Path(folder).parent]
    others = [
        candidate for candidate in modlist.find_mods(roots, mod.m_anno)[0] if candidate.m_folder.resolve() != here
    ]
    resolution = resolve([mod], others, allow_pull=True)
    chosen = modlist.read_options(Path(folder).parent)
    return import_mods(
        game, resolution.order, CVariables(resolution.order, chosen), progress, resolution=resolution, primary=mod
    )


def _own_mods(primary: CMod) -> list[CMod]:
    """The mod and every mod inside its folder, at any depth: what the mod itself ships."""
    out, stack = [], [primary]
    while stack:
        one = stack.pop(0)
        out.append(one)
        stack += one.nested
    return out


def import_profile(
    game: CGame, profile: Path, folders: list[Path], progress: Callable[[str], None] | None = None
) -> SImported:
    """The mods ``profile`` switches on, found in ``folders`` (the loader's), with their nested mods, in the loader's
    order."""
    found, notes = modlist.find_mods(folders, GAME_NUMBER.get(game.m_layout.key))
    prof = CProfileText.from_bytes(Path(profile).read_bytes()).to_profile(Path(profile).stem)
    new_roots = tuple(folder for folder in folders if Path(folder).name != modlist.UBI)
    resolution = resolve_profile(found, prof, new_roots)
    if not resolution.order:
        raise ValueError(
            tr(
                "assets.profile_switches_no_mod_found",
                profile=profile,
                folders=", ".join(map(str, folders)),
            )
        )
    chosen = modlist.read_options(Path(profile).parent.parent)
    result = import_mods(game, resolution.order, CVariables(resolution.order, chosen), progress, resolution=resolution)
    result.lost += notes
    return result


def import_mods(
    game: CGame,
    mods: list[CMod],
    variables: CVariables,
    progress: Callable[[str], None] | None = None,
    resolution: SResolution | None = None,
    primary: CMod | None = None,
) -> SImported:
    """``mods`` applied in this order to the game's files, and the result as a change set (``primary``: the mod whose
    identity it takes, else the last)."""
    say = progress or (lambda _t: None)
    asset_path = game.m_layout.patch_target.lower()
    run = _CRun(asset_path)
    session = run.m_session = CSession(
        game.read_base, game.lazy_ranges, run.observe, m_keep=_added, m_game=game.m_layout.key
    )  # added assets are kept as bytes (``CPatchDoc.freeze``) and read with ``peek`` (docs/mod-diff.md "Speed and memory")
    for mod in mods:
        say(f"applying {mod.m_mod_id}")
        session.apply_mod(mod, variables)
    last = primary or mods[-1]
    own = _own_mods(primary) if primary is not None else list(mods)
    changes = CChangeSet(game.m_layout.key, last.m_mod_id, last.m_name or last.m_mod_id, last.m_version or "0.1.0")
    changes.m_requires = list(
        dict.fromkeys(own_mod.top.m_mod_id for own_mod in mods if own_mod not in own)
    )  # a library, not its inner mods
    result = SImported(changes, list(mods), list(session.m_events), session=session, resolution=resolution)
    say("comparing with the game")
    doc = session.m_docs.get(asset_path)
    if doc is not None:
        assets_against(game, doc, result)
    _texts(game, session, result)
    _carry(game, own, result)
    _leave_out_required(run, {own_mod.m_mod_id for own_mod in own}, result)
    result.overridden = {guid: mod_ids for guid, mod_ids in run.m_touched.items() if guid in changes.m_assets}
    if len(mods) > 1:
        result.lost.append(
            tr("assets.mods_one_change_set_takes", n=len(mods), id=last.m_mod_id)
            if primary
            else tr("assets.mods_one_change_set_takes_identity", n=len(mods), id=last.m_mod_id)
        )
    return result


class _CRun:
    """What the session is told: per asset, the mods whose operations touched it (the files it reads are the game's:
    ``CGame.read_base``, ``CGame.lazy_ranges``)."""

    def __init__(self, asset_path: str) -> None:
        self.m_asset_path = asset_path
        self.m_touched: dict[int, list[str]] = {}
        self.m_session: CSession | None = None

    def observe(self, mod_id: str, path: str, kind: str, nodes: list) -> None:
        doc = self.m_session.m_docs.get(path) if self.m_session else None
        if doc is None or path != self.m_asset_path or kind == "remove":
            return
        for node in nodes:
            guid = _num(doc, doc.asset_of(node)) if isinstance(node, ET.Element) else None
            if guid is not None:
                users = self.m_touched.setdefault(guid, [])
                if not users or users[-1] != mod_id:
                    users.append(mod_id)


def _leave_out_required(run: _CRun, own: set[str], result: SImported) -> None:
    """The assets only a required mod (not the imported one or its nested mods) touched are not the change set's: that
    mod stays a dependency (``CChangeSet.m_requires``), so the built mod does not ship its content twice."""
    changes = result.changes
    foreign = {touched_guid for touched_guid, users in run.m_touched.items() if not own.intersection(users)}
    for guid in sorted(foreign & {*changes.m_assets, *changes.m_new}):
        changes.m_assets.pop(guid, None)
        changes.m_new.pop(guid, None)
        for found in (result.changed, result.added):
            if guid in found:
                found.remove(guid)
    if foreign:
        result.lost.append(
            tr(
                "assets.assets_only_required_mod_changes",
                n=len(foreign),
                mods=", ".join(changes.m_requires),
            )
        )


def assets_against(game: CGame, doc: CPatchDoc, result: SImported, path: str = "") -> None:
    """The game's assets of the asset file ``path`` (the layout's ModOps target when empty) against the patched
    ones in ``doc``: removed, changed, added."""
    index, changes = game.m_index, result.changes
    file = next(
        (
            k
            for k, file_path in enumerate(index.m_files)
            if file_path.lower() == (path or game.m_layout.patch_target).lower()
        ),
        None,
    )
    vanilla = [entry for entry in index.m_entries if entry.file == file]
    known = {entry.guid for entry in vanilla}
    for entry in vanilla:
        found = doc.m_by_guid.get(str(entry.guid))
        if not found:
            if (
                index.m_by_guid.get(entry.guid) is entry
            ):  # a definition that another file overrides is not the game's asset
                changes.m_removed.append(entry.guid)
                result.removed.append(entry.guid)
            continue
        element, before = (
            found[-1],
            index.element_of(entry),
        )  # a GUID defined twice in the patched file: the later one wins
        if doc.is_lazy(element) and element not in doc.m_notes:  # the game's own bytes, untouched
            continue
        element = doc.peek(element)  # an asset a mod added again with the game's GUID is kept as bytes
        if canonical(element) == canonical(before):
            continue
        change, form = assetdiff.asset_change(entry.guid, before, element)
        changes.m_assets[entry.guid] = change
        result.changed.append(entry.guid)
        result.forms[form] += 1
        result.values += len(change.set) + len(change.reset) + len(change.blocks) + (1 if change.whole else 0)
    _new_assets(doc, known, result)


def _new_assets(doc: CPatchDoc, known: set[int], result: SImported) -> None:
    """The assets the mods added, in file order, each anchored to its neighbour in the patched file."""
    seen: set[int] = set()
    for holder in doc.m_top.iter("Assets"):
        row = [child for child in holder if child.tag == "Asset"]
        guids = [_num(doc, child) for child in row]
        after_known: list[int | None] = [None] * (len(row) + 1)  # the next game asset from each place on
        for k in range(len(row) - 1, -1, -1):
            after_known[k] = guids[k] if guids[k] in known else after_known[k + 1]
        previous = None
        for k, (asset, guid) in enumerate(zip(row, guids, strict=True)):
            if guid is not None and guid not in known:
                if guid in seen:
                    result.lost.append(tr("assets.asset_added_twice_change_set", guid=guid))
                else:
                    seen.add(guid)
                    anchor, place = (previous, "after") if previous is not None else (after_known[k + 1], "before")
                    _add_new(result, guid, doc.peek(asset), anchor, place)
            previous = guid if guid is not None else previous


def _add_new(result: SImported, guid: int, asset: ET.Element, anchor: int | None, place: str) -> None:
    if anchor is None:
        result.lost.append(tr("assets.asset_stands_alone_list_written", guid=guid))
    base = (asset.findtext("BaseAssetGUID") or "").strip()
    template = (asset.findtext("Template") or "").strip()
    name = (asset.findtext("Values/Standard/Name") or "").strip()
    record = SNewAsset(template, name, int(base) if base.isdigit() else None, None, anchor, place, to_text(asset))
    result.changes.m_new[guid] = record
    result.added.append(guid)


def _added(_doc: CPatchDoc, _asset: ET.Element) -> bool:
    """The note a kept added asset carries (``CPatchDoc.notes``): it marks the asset as a mod's, not the game's."""
    return True


def _num(doc: CPatchDoc, asset: ET.Element | None) -> int | None:
    text = doc.m_guid_text.get(asset, "") if asset is not None else ""
    return int(text) if text.strip().isdigit() else None


def load_or_report(data: bytes, path: str, result: SImported) -> CPatchDoc | None:
    """The file ``path`` as a document; one that is not valid XML is one finding of ``result`` and None, so the rest
    of the import goes on."""
    try:
        return CPatchDoc.load(data)
    except ET.ParseError as error:
        result.lost.append(tr("assets.file_not_valid_xml_left_out", path=path, error=str(error)))
        return None


def _texts(game: CGame, session: CSession, result: SImported) -> None:
    pattern = game.m_layout.texts
    if not pattern:
        return
    head, tail = pattern.split("{language}")
    key = TEXT_KEYS[0] if game.m_layout.key == "anno117" else TEXT_KEYS[1]
    for path, doc in session.m_docs.items():
        if not (path.startswith(head.lower()) and path.endswith(tail.lower())):
            continue
        language = path[len(head) : len(path) - len(tail)]
        if language == "metadata":
            continue
        base = session.m_read_base(path)
        vanilla = load_or_report(base, path, result) if base else None
        if base and vanilla is None:
            continue  # against no game file every text would count as changed
        changed, lost = assetdiff.text_changes(vanilla.roots[0] if vanilla else None, doc.roots[0], key)
        if changed:
            result.changes.m_texts[language] = changed
            result.texts[language] = len(changed)
        result.lost += [f"{language}: {line}" for line in lost]


def _carry(game: CGame, mods: list[CMod], result: SImported) -> None:
    """Every file of the mods the change set does not write itself is carried; a path two mods have, reported."""
    layout = game.m_layout
    owned = {layout.patch_target.lower()} | {lang_path.lower() for lang_path in _text_paths(layout, result)}
    seen: dict[str, str] = {}
    for mod in mods:
        mine = []
        for relative_path in mod.files():
            low = relative_path.lower()
            if low in owned or low in ("modinfo.json", "modinfo.jsonc"):
                continue
            if low in seen:
                result.lost.append(
                    tr(
                        "assets.both_build_keeps_s",
                        path=relative_path,
                        first=seen[low],
                        second=mod.m_mod_id,
                    )
                )
            seen[low] = mod.m_mod_id
            mine.append(relative_path)
        result.carried += mine
        result.changes.m_carry.append(SCarried(str(mod.m_folder), sorted(owned)))


def _text_paths(layout: SLayout, result: SImported) -> list[str]:
    """The language files the change set writes: those it holds texts for, and those the mods patch."""
    if not layout.texts:
        return []
    head, tail = layout.texts.split("{language}")
    langs = set(result.changes.m_texts)
    for mod in result.mods:
        for relative_path, _path in mod.patch_files():
            low = relative_path.lower()
            if low.startswith(head.lower()) and low.endswith(tail.lower()):
                lang = low[len(head) : len(low) - len(tail)]
                if lang != "metadata":
                    langs.add(lang)
    return [layout.texts.format(language=lang) for lang in sorted(langs)]
