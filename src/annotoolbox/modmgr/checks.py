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
from ..modloader.engine import WARNING, Session
from ..modloader.mods import Mod, Variables, read_options
from ..modloader.profilefile import ProfileText
from ..modloader.resolve import Resolution, resolve_profile
from .catalog import NEW, Catalog, state_of

PROBLEM, NOTE = "problem", "note"


@dataclass(frozen=True)
class Issue:
    """One thing a person should know before starting: ``level`` is ``PROBLEM`` (the loader logs it or does something
    the mod's author did not mean) or ``NOTE``; ``mod`` the ModID it is about ("" for the profile as a whole)."""

    level: str
    mod: str
    code: str
    text: str

    def line(self) -> str:
        return f"{self.mod}: {self.text}" if self.mod else self.text


@dataclass
class OrderRow:
    position: int
    mod: Mod
    why: str  # how it came in and what places it


@dataclass
class Plan:
    profile: str
    resolution: Resolution
    rows: list[OrderRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def order(self) -> list[Mod]:
        return self.resolution.order

    def issues_of(self, mod_id: str) -> list[Issue]:
        return [i for i in self.issues if i.mod == mod_id]

    @property
    def problems(self) -> list[Issue]:
        return [i for i in self.issues if i.level == PROBLEM]


def _why(mod: Mod, on_ids: set[str], how: dict[str, str]) -> str:
    bits = []
    if mod.parent is not None:
        bits.append(tr("nested in {id}", id=mod.top.mod_id))
    else:
        bits.append(tr(how.get(mod.mod_id, N_("listed"))))
    if mod.late:
        bits.append(tr("late phase"))
    after = [d for d in mod.load_after if d in on_ids and d != "*"]
    if after:
        bits.append(tr("after {mods}", mods=", ".join(after)))
    return "; ".join(bits)


def plan_profile(
    catalog: Catalog, text: ProfileText | None, name: str = "", provided: frozenset[str] = frozenset()
) -> Plan:
    """The load order of ``text`` over ``catalog`` and its dependency problems. ``text`` None: no profile, nothing on.
    ``provided``: ids a ``Require`` may name that are no mod (a game's DLCs)."""
    new_roots = tuple(r.path for r in catalog.roots if r.new_mods)
    if text is None:
        res = resolve_profile(catalog.mods, ProfileText("").to_profile(name), new_roots, provided)
    else:
        res = resolve_profile(catalog.mods, text.to_profile(name), new_roots, provided)
    how = {}
    for card in catalog.cards:
        if card.usable:
            _, why = state_of(card, text)
            how[card.mod_id] = N_("EnableNewMods") if why == NEW else N_("listed")
    on_ids = {m.mod_id for m in res.order}
    plan = Plan(name, res)
    plan.rows = [OrderRow(n, m, _why(m, on_ids, how)) for n, m in enumerate(res.order, 1)]
    plan.issues = _resolution_issues(res) + _catalog_issues(catalog, text) + _info_issues(res.order)
    return plan


def _resolution_issues(res: Resolution) -> list[Issue]:
    """The resolution's M01-M09 as issues: a problem for the ones the loader logs or gets wrong, else a note."""
    return [
        Issue(NOTE if f.level == "info" else PROBLEM, f.where, f.code, f.message)
        for f in resolution_findings(res, notes=True)
    ]


def _catalog_issues(catalog: Catalog, text: ProfileText | None) -> list[Issue]:
    out = []
    for card in catalog.cards:
        if card.problem and not card.parked:
            out.append(Issue(PROBLEM, "", "C01", f"{card.folder.name}: {card.problem}"))
        elif card.duplicate_of:
            out.append(
                Issue(
                    NOTE,
                    card.mod_id,
                    "C02",
                    tr(
                        "{folder} has the ModID of {other}, a copy the loader keeps instead",
                        folder=card.folder.name,
                        other=Path(card.duplicate_of).name,
                    ),
                )
            )
    if text is not None:
        have = catalog.installed_ids()
        out += [
            Issue(NOTE, i, "C03", tr("the profile lists it, but no installed mod has this ModID"))
            for i in text.ids(True)
            if i not in have
        ]
    return out


def _info_issues(order: list[Mod]) -> list[Issue]:
    """The ``modinfo`` findings of every mod that is on (117's rules: ``Anno`` 8, ``Difficulty``)."""
    out = []
    for mod in order:
        if mod.anno is None:
            continue
        for f in lint_modinfo(mod.folder):
            out.append(Issue(NOTE if f.level == "info" else PROBLEM, mod.mod_id, f.code, f"{f.where}: {f.message}"))
    return out


def deep_check(
    plan: Plan,
    mods_dir: Path,
    read_base: Callable[[str], bytes | None] | None = None,
    has_base: Callable[[str], bool] | None = None,
    progress: Callable[[str], None] | None = None,
    lint: bool = True,
) -> list[Issue]:
    """The slow checks of the mods ``plan`` has on. ``lint``: ``mod.modlint`` over each mod's files (``has_base``
    tells whether the game has a file: patches of a game file that does not exist). ``read_base``: the game's files;
    with it every operation is applied in the loader's order and each one that matches nothing, or that the loader
    drops, is an issue."""
    say = progress or (lambda _t: None)
    out: list[Issue] = []
    tops = [m for m in plan.order if m.parent is None]
    if lint:
        for mod in tops:
            if mod.anno is None:
                continue
            say(f"lint {mod.mod_id}")
            result = lint_mod(mod.folder, has_base=has_base)
            out += [
                Issue(NOTE if f.level == "info" else PROBLEM, mod.mod_id, f.code, f"{f.where}: {f.message}")
                for f in result.findings
                if not f.code.startswith(("M", "I0"))  # the plan has these already
            ]
    if read_base is not None:
        session = Session(read_base)
        variables = Variables(plan.order, read_options(mods_dir))
        for mod in plan.order:
            say(f"applying {mod.mod_id}")
            session.apply_mod(mod, variables)
        out += [
            Issue(PROBLEM, e.mod, f"op:{e.code}", f"{e.file}: {e.message}")
            for e in session.events
            if e.level == WARNING
        ]
    return out
