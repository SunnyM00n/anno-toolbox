"""The mods the loader would find: the folders it searches and one ``ModCard`` per folder, with the ``modinfo`` fields a
person wants to read and the mods nested inside. Stdlib only.

``scan`` reads the folders of a game (``mods.loader_roots``: the install's ``mods/.ubi`` and ``mods``, the Documents
``mods``, mod.io's) (a mod the loader would drop is a card too, with the reason), ``state_of`` says what a profile makes
of a card. The loader's rules are ``docs/engine-modloader.md`` ("Where mods live"); ``modloader.mods`` does the reading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, tr
from ..modloader.mods import (
    DEPENDENCY_KINDS,
    MODINFO_NAMES,
    ROOT_DOCUMENTS,
    ROOT_EXAMPLES,
    ROOT_EXTRA,
    ROOT_GAME,
    Mod,
    Root,
    find_mods,
    localized,
    parked,
    read_mod,
)
from ..modloader.profilefile import ProfileText

NOT_MODS = frozenset({"profiles"})  # folders of a mods folder that are no mods
SETUP_DEFAULTS = {"RequiresNewGame": False, "SafeToRemove": False, "Multiplayer": True, "Campaign": True}

ON, OFF = "on", "off"  # ``state_of``; with how the profile decides
LISTED, NEW, UNLISTED = "listed", "new", "unlisted"
ROOT_LABELS = {
    ROOT_EXAMPLES: N_("mods.root_examples"),
    ROOT_GAME: N_("mods.root_game"),
    ROOT_DOCUMENTS: N_("mods.root_documents"),
    ROOT_EXTRA: N_("mods.root_extra"),
}


def root_label(root: Root) -> str:
    """Which of the loader's folders ``root`` is, as the interface says it (mod.io's is a name and stays)."""
    return tr(ROOT_LABELS[root.label]) if root.label in ROOT_LABELS else root.label


def public_folder() -> Path | None:
    """Windows' public folder, where mod.io keeps its mods; None where there is none."""
    found = os.environ.get("PUBLIC")
    return Path(found) if found else None


@dataclass
class ModCard:
    """A folder of a mods folder: a mod (``mod``), or why the loader would not take it (``problem``, ``parked``,
    ``duplicate_of``); ``children`` are the mods nested inside."""

    folder: Path
    root: Root
    mod: Mod | None = None
    problem: str = ""  # the loader's reason for skipping the folder
    parked: bool = False  # the folder's name starts with ``-`` or ``.``
    duplicate_of: str = ""  # the folder of the same ModID the loader keeps instead
    children: list[ModCard] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Whether the loader can take this mod (a profile can name it)."""
        return self.mod is not None and not self.parked and not self.duplicate_of

    @property
    def mod_id(self) -> str:
        return self.mod.mod_id if self.mod else ""

    @property
    def name(self) -> str:
        return (localized(self.mod.info.get("ModName")) if self.mod else "") or self.folder.name

    @property
    def info(self) -> dict:
        return self.mod.info if self.mod else {}

    @property
    def version(self) -> str:
        return self.mod.version if self.mod else ""

    @property
    def category(self) -> str:
        return self.mod.category if self.mod else ""

    @property
    def description(self) -> str:
        return localized(self.info.get("Description"))

    @property
    def difficulty(self) -> str:
        return str(self.info.get("Difficulty") or "")

    @property
    def setup(self) -> dict[str, bool]:
        """The four ``GameSetup`` flags with the loader's defaults for the missing ones."""
        given = self.info.get("GameSetup") if isinstance(self.info.get("GameSetup"), dict) else {}
        return {k: bool(given.get(k, default)) for k, default in SETUP_DEFAULTS.items()}

    @property
    def dependencies(self) -> dict[str, list[str]]:
        """Kind (``DEPENDENCY_KINDS``) -> ids, only the kinds the mod uses."""
        if self.mod is None:
            return {}
        have = {
            "Require": self.mod.require,
            "Optional": self.mod.optional,
            "LoadAfter": self.mod.load_after,
            "Incompatible": self.mod.incompatible,
            "Deprecate": self.mod.deprecate,
        }
        return {k: have[k] for k in DEPENDENCY_KINDS if have[k]}

    def walk(self):
        """This card and the cards nested below it."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Catalog:
    roots: list[Root]
    cards: list[ModCard] = field(default_factory=list)

    @property
    def mods(self) -> list[Mod]:
        """The mods a profile can name (top mods the loader keeps), in the loader's search order."""
        return [c.mod for c in self.cards if c.usable and c.mod is not None]

    def by_id(self, mod_id: str) -> ModCard | None:
        return next((c for c in self.cards if c.usable and c.mod_id == mod_id), None)

    def installed_ids(self) -> set[str]:
        return {c.mod_id for c in self.cards if c.usable}

    def card_of(self, mod: Mod) -> ModCard | None:
        """The card of a mod (a nested one too)."""
        for top in self.cards:
            for card in top.walk():
                if card.mod is mod:
                    return card
        return None


def _nest(card: ModCard) -> list[ModCard]:
    out = []
    for m in card.mod.nested if card.mod else []:
        child = ModCard(m.folder, card.root, m)
        child.children = _nest(child)
        out.append(child)
    return out


def scan(roots: list[Root], anno: int | None = None) -> Catalog:
    """Every folder of ``roots`` as a card. A ModID found twice stays with its higher version (``find_mods``); the
    other folder is a card with ``duplicate_of``."""
    kept, _ = find_mods([r.path for r in roots], anno)
    keep = {m.folder.resolve(): m for m in kept}
    holder = {m.mod_id: m.folder for m in kept}
    catalog = Catalog(list(roots))
    for root in roots:
        folders = sorted(p for p in root.path.iterdir() if p.is_dir()) if root.path.is_dir() else []
        for folder in folders:
            card = _card(folder, root, anno, keep, holder)
            if card is not None:
                catalog.cards.append(card)
    return catalog


def _card(folder: Path, root: Root, anno: int | None, keep: dict, holder: dict) -> ModCard | None:
    is_parked = parked(folder.name)
    if folder.name in NOT_MODS:
        return None
    mod = keep.get(folder.resolve())
    if mod is not None:
        card = ModCard(folder, root, mod)
    else:
        has_info = any((folder / n).is_file() for n in MODINFO_NAMES)
        if is_parked and not has_info:
            return None  # a staging folder or any folder the loader never looks at
        mod, why = read_mod(folder, anno)
        if mod is None:
            return ModCard(folder, root, problem=why, parked=is_parked)
        kept = holder.get(mod.mod_id)
        card = ModCard(folder, root, mod, parked=is_parked, duplicate_of=str(kept) if kept and not is_parked else "")
    card.children = _nest(card)
    return card


def state_of(card: ModCard, text: ProfileText | None) -> tuple[str, str]:
    """(``ON`` or ``OFF``, how the profile decides it: ``LISTED``, ``NEW`` for an unlisted mod ``EnableNewMods``
    switched on, ``UNLISTED``). ``text`` None: no profile, nothing on. Unusable cards are off."""
    if not card.usable or text is None:
        return OFF, UNLISTED
    listed = text.state(card.mod_id)
    if listed is not None:
        return (ON if listed else OFF), LISTED
    if text.enable_new and card.root.new_mods:
        return ON, NEW
    return OFF, UNLISTED
