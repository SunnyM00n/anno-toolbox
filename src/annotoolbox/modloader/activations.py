"""Anno 1800's way of switching mods on, which differs from 117's profiles: a mod is on unless its folder's
``activations.json`` lists its ModID in ``disabledIds``. There is no profile file and no ``EnableNewMods``. Stdlib only.

``read_disabled(folder)`` reads one folder's list, ``switched_on(found, roots)`` the mods that are on (the ``.zip`` mods of
``zipmods.py`` count by the folder of their archive). The evidence and what is inferred:
``docs/engine-modloader.md`` "Anno 1800's loader".
"""

from __future__ import annotations

from pathlib import Path

from .mods import Mod, read_jsonc

ACTIVATIONS = "activations.json"


def read_disabled(folder: Path) -> set[str]:
    """The ModIDs ``folder``'s ``activations.json`` switches off (``{"disabledIds": [...]}``); none for a missing or
    unreadable file."""
    data = read_jsonc(Path(folder) / ACTIVATIONS)
    ids = data.get("disabledIds") if data else None
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()


def switched_on(found: list[Mod], roots: list[Path]) -> list[Mod]:
    """The mods of ``found`` that are on: those whose own mods folder does not list their ModID."""
    disabled = {Path(r).resolve(): read_disabled(r) for r in roots}
    return [m for m in found if m.mod_id not in disabled.get(m.home.resolve(), set())]
