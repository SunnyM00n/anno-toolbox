"""What a profile makes of the installed mods, and what is wrong with it before the game starts.

``plan_profile`` is the quick part and runs at every change: the mods that are on, with the ones that come with them,
the order the loader applies them in (``modloader.resolve``) and the problems the dependencies give: a required mod
that is missing, a ``LoadAfter`` circle, an incompatible pair that is on together, a mod another one replaces, a
``modinfo`` the loader complains about. ``deep_check`` is the slow part, run on request: the mod lint of every mod on
and the operations applied to the game's files, each one that matches nothing listed. ``docs/mod-manager.md``
"Problems".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, tr
from ..mod.modlint import lint_mod, lint_modinfo
from ..mod.modlintdeps import resolution_findings
from ..modloader.engine import WARNING, CSession
from ..modloader.mods import CMod, CVariables, read_options
from ..modloader.profilefile import CProfileText
from ..modloader.resolve import SResolution, resolve_profile
from .catalog import NEW, SCatalog, state_of

PROBLEM, NOTE = "problem", "note"


@dataclass(frozen=True)
class SIssue:
    """One thing a person should know before starting: ``level`` is ``PROBLEM`` (the loader logs it or does something
    the mod's author did not mean) or ``NOTE``; ``mod`` the ModID it is about ("" for the profile as a whole)."""

    level: str
    mod: str
    code: str
    text: str

    def line(self) -> str:
        return f"{self.mod}: {self.text}" if self.mod else self.text


@dataclass
class SOrderRow:
    position: int
    mod: CMod
    why: str  # how it came in and what places it


@dataclass
class SPlan:
    profile: str
    resolution: SResolution
    rows: list[SOrderRow] = field(default_factory=list)
    issues: list[SIssue] = field(default_factory=list)

    @property
    def order(self) -> list[CMod]:
        return self.resolution.order

    def issues_of(self, mod_id: str) -> list[SIssue]:
        return [i for i in self.issues if i.mod == mod_id]

    @property
    def problems(self) -> list[SIssue]:
        return [i for i in self.issues if i.level == PROBLEM]


def _why(mod: CMod, on_ids: set[str], how: dict[str, str]) -> str:
    bits = []
    if mod.m_parent is not None:
        bits.append(tr("mods.nested_in_id", id=mod.top.m_mod_id))
    else:
        bits.append(tr(how.get(mod.m_mod_id, N_("mods.listed"))))
    if mod.late:
        bits.append(tr("mods.late_phase"))
    after = [other_id for other_id in mod.m_load_after if other_id in on_ids and other_id != "*"]
    if after:
        bits.append(tr("mods.after_mods", mods=", ".join(after)))
    return "; ".join(bits)


def plan_profile(
    catalog: SCatalog, text: CProfileText | None, name: str = "", provided: frozenset[str] = frozenset()
) -> SPlan:
    """The load order of ``text`` over ``catalog`` and its dependency problems. ``text`` None: no profile, nothing on.
    ``provided``: ids a ``Require`` may name that are no mod (a game's DLCs)."""
    new_roots = tuple(root.path for root in catalog.roots if root.new_mods)
    profile = (CProfileText("") if text is None else text).to_profile(name)
    resolution = resolve_profile(catalog.mods, profile, new_roots, provided)
    how: dict[str, str] = {}
    for card in catalog.cards:
        if card.usable:
            _, why = state_of(card, text)
            how[card.mod_id] = N_("mods.enablenewmods") if why == NEW else N_("mods.listed")
    on_ids = {ordered_mod.m_mod_id for ordered_mod in resolution.order}
    plan = SPlan(name, resolution)
    plan.rows = [
        SOrderRow(position, ordered_mod, _why(ordered_mod, on_ids, how))
        for position, ordered_mod in enumerate(resolution.order, 1)
    ]
    plan.issues = _resolution_issues(resolution) + _catalog_issues(catalog, text) + _info_issues(resolution.order)
    return plan


def _level(finding) -> str:
    """A lint finding's level as an issue's: a problem unless the lint calls it information."""
    return NOTE if finding.level == "info" else PROBLEM


def _lint_issue(finding, mod_id: str) -> SIssue:
    """A finding of the mod lint about a file of the mod ``mod_id``."""
    return SIssue(_level(finding), mod_id, finding.code, f"{finding.where}: {finding.message}")


def _resolution_issues(resolution: SResolution) -> list[SIssue]:
    """The resolution's M01-M09 as issues: a problem for the ones the loader logs or gets wrong, else a note."""
    return [
        SIssue(_level(finding), finding.where, finding.code, finding.message)
        for finding in resolution_findings(resolution, notes=True)
    ]


def _catalog_issues(catalog: SCatalog, text: CProfileText | None) -> list[SIssue]:
    """What the folders say: one the loader drops, a second copy of a ModID, a listed mod that is not installed."""
    out: list[SIssue] = []
    for card in catalog.cards:
        if card.problem and not card.parked:
            out.append(SIssue(PROBLEM, "", "C01", f"{card.folder.name}: {card.problem}"))
        elif card.duplicate_of:
            out.append(
                SIssue(
                    NOTE,
                    card.mod_id,
                    "C02",
                    tr(
                        "mods.modid_copy_loader_keeps_instead",
                        folder=card.folder.name,
                        other=Path(card.duplicate_of).name,
                    ),
                )
            )
    if text is not None:
        have = catalog.installed_ids()
        out += [
            SIssue(NOTE, i, "C03", tr("mods.profile_lists_no_installed_mod")) for i in text.ids(True) if i not in have
        ]
    return out


def _info_issues(order: list[CMod]) -> list[SIssue]:
    """The ``modinfo`` findings of every mod that is on (117's rules: ``Anno`` 8, ``Difficulty``)."""
    return [
        _lint_issue(finding, mod.m_mod_id)
        for mod in order
        if mod.m_anno is not None
        for finding in lint_modinfo(mod.m_folder)
    ]


def deep_check(
    plan: SPlan,
    mods_dir: Path,
    read_base: Callable[[str], bytes | None] | None = None,
    has_base: Callable[[str], bool] | None = None,
    progress: Callable[[str], None] | None = None,
    lint: bool = True,
) -> list[SIssue]:
    """The slow checks of the mods ``plan`` has on. ``lint``: ``mod.modlint`` over each mod's files (``has_base``
    tells whether the game has a file: patches of a game file that does not exist). ``read_base``: the game's files;
    with it every operation is applied in the loader's order and each one that matches nothing, or that the loader
    drops, is an issue."""
    say = progress or (lambda _t: None)
    out: list[SIssue] = []
    tops = [top for top in plan.order if top.m_parent is None]
    if lint:
        for mod in tops:
            if mod.m_anno is None:
                continue
            say(tr("mods.lint_mod", mod=mod.m_mod_id))
            result = lint_mod(mod.m_folder, has_base=has_base)
            out += [
                _lint_issue(finding, mod.m_mod_id)
                for finding in result.findings
                if not finding.code.startswith(("M", "I0"))  # the plan has these already
            ]
    if read_base is not None:
        session = CSession(read_base)
        variables = CVariables(plan.order, read_options(mods_dir))
        for mod in plan.order:
            say(tr("common.applying_mod", mod=mod.m_mod_id))
            session.apply_mod(mod, variables)
        out += [
            SIssue(PROBLEM, event.mod, f"op:{event.code}", f"{event.file}: {event.message}")
            for event in session.m_events
            if event.level == WARNING
        ]
    return out
