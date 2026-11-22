"""Stand-ins for the mod manager: mods, patches and a mods folder."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from annotoolbox.modloader import modswap

MOD_ASSETS = (
    "<AssetList><Groups><Group><Assets>"
    "<Asset><Values><Standard><GUID>1</GUID></Standard></Values></Asset>"
    "</Assets></Group></Groups></AssetList>"
)


ASSETS_PATH = "data/base/config/export/assets.xml"


PROFILE = """## Download mods at: https://mod.io/g/anno-117-pax-romana
##
## Disable a mod:
## - Add a single `#` before it.
# EnableNewMods
DisplayName Mod Profile

# DisplayName Mod Profile # not installed
# alpha
beta
# gamma # not installed
"""


ACTIVE = (
    '## Change the value after "ActiveProfile " to configure which mod profile will be loaded by the game on startup.\n'
    "## Not specifying the active profile or leaving the value empty will start the game without any mods.\n"
    "ActiveProfile default"
)


def make_mod(root: Path, folder: str, mod_id: str | None = None, version: str = "1.0.0", **info) -> Path:
    """A mod folder with a ``modinfo.json`` for 117 (``Anno`` 8); ``info`` adds or overrides fields."""
    path = Path(root) / folder
    path.mkdir(parents=True, exist_ok=True)
    data = {
        "Anno": 8,
        "ModID": mod_id or folder.strip("-."),
        "Version": version,
        "Category": {"English": "Tools"},
        "ModName": {"English": (mod_id or folder).title()},
        "Description": {"English": f"the {folder} mod"},
        "Difficulty": "unchanged",
        **info,
    }
    (path / "modinfo.json").write_text(json.dumps(data), encoding="utf-8")
    return path


def make_patch(mod: Path, ops: str, rel: str = ASSETS_PATH) -> None:
    file = mod / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"<ModOps>{ops}</ModOps>", encoding="utf-8")


def make_mods_folder(root: Path) -> Path:
    """alpha, beta, gamma (with a mod nested in beta) and a profile naming them; returns the mods folder."""
    mods = Path(root) / "mods"
    make_mod(mods, "alpha")
    beta = make_mod(mods, "beta", Dependencies={"LoadAfter": ["alpha"], "Require": ["alpha"]})
    make_mod(beta, "inner", "beta-inner")
    make_mod(mods, "gamma", Dependencies={"Incompatible": ["alpha"]})
    (mods / "profiles").mkdir()
    (mods / "profiles" / "default.txt").write_text(
        "# EnableNewMods\nDisplayName default\n\nalpha\nbeta\n", encoding="utf-8"
    )
    (mods / "profile.txt").write_text("ActiveProfile default", encoding="utf-8")
    return mods


class FakeRecycleBin:
    """A recycle bin that only remembers what it was given (the real one is never touched)."""

    def __init__(self, check: Callable[[], None] | None = None) -> None:
        self.got: list[Path] = []
        self.check = check

    def __call__(self, path: Path) -> str:
        if self.check:
            self.check()
        self.got.append(Path(path))
        os.rmdir(path) if modswap.is_link(path) else shutil.rmtree(path)  # what the real bin does to the place
        return "the test bin"
