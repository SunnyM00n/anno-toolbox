"""The live link's companion mod: a small mod with one key binding that sends the selected object to the suite.

``write_companion(mods_dir)`` writes it (a ``ShortcutConfig`` binding whose command calls the helper library's
``HelperLink`` script table, ``EDIT_COMMAND``); ``is_installed(mods_dir)`` says whether it is there. The library
also sends the object on Ctrl+Shift+E by itself, so the mod is the game's own, rebindable route to the same request.
The on-screen label is not built (docs/live-link.md, section 9.4): the game window's title says "[live link]" while
the suite is connected. What the game has to confirm for the mod is in docs/live-link.md, section 9.6.
"""

from __future__ import annotations

from pathlib import Path

from ..mod import modxml

MOD_ID = "livelink-companion"
EDIT_COMMAND = "HelperLink:EditSelected()"  # the library's script table; without the library it is a script error
EDIT_KEY = "Control;Alt;E"  # not Ctrl+Shift+E: the library reads that one itself
ACTIVE = "Session"  # an object is only selected in a session


def write_companion(mods_dir: Path, key: str = EDIT_KEY, mod_id: str = MOD_ID) -> Path:
    """The companion mod in ``mods_dir/<mod_id>``; returns its folder."""
    root = Path(mods_dir) / mod_id
    assets = root / "data/base/config/export/assets.xml"
    assets.parent.mkdir(parents=True, exist_ok=True)
    binding = modxml.shortcut_binding(EDIT_COMMAND, key, ACTIVE)
    mod_operation = f'<ModOp GUID="{modxml.SHORTCUT_CONFIG}" Add="ShortcutConfig/InputBindings">{binding}</ModOp>'
    assets.write_text(
        modxml.modops([mod_operation], "game link companion: edit the selected object"), encoding="utf-8", newline="\n"
    )
    (root / "modinfo.json").write_text(
        modxml.modinfo(
            mod_id,
            "Tools",
            "Game link companion",
            f"A key ({key.replace(';', '+')}) that sends the selected object to the tool suite (needs the helper library).",
        ),
        encoding="utf-8",
        newline="\n",
    )
    return root


def is_installed(mods_dir: Path, mod_id: str = MOD_ID) -> bool:
    return (Path(mods_dir) / mod_id / "modinfo.json").is_file()
