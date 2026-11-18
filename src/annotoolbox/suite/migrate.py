"""The move from the old Windows-installer versions (0.1.x) to the installed program of today (``docs/installer.md``,
"The move from v0.1.x").

A small migration package that every release carries replaces the old install and starts the new setup. It leaves a
record in the registry: its own product code (the program removes that entry quietly, ``remove_leftover``) and which
shortcuts the old install had (``carried_shortcuts``). Older installs that the migration did not reach are found in the
Apps list's registry (``old_products``) and offered for removal on the setup page. Registry and process are passed in,
so the logic runs in a test without either.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..infra.tools import NO_WINDOW

MIGRATION_KEY = r"Software\AnnoToolboxMigration"  # written by the migration package (scripts/installer_wix.py)
LEGACY_KEY = r"Software\AnnoToolbox\Components"  # the old install's components: a value per optional shortcut
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
PRODUCT_NAME = "Anno Toolbox"
PRODUCT_CODE = re.compile(r"^\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$")
BUSY = 1618  # another installation is running (the migration package is still finishing)
NOT_INSTALLED = 1605
OK_CODES = (0, 3010, NOT_INSTALLED)  # 3010: done, a restart is advised (never forced: /norestart)


class Registry(Protocol):
    """The part of the current user's registry the logic reads."""

    def read(self, key: str, name: str) -> str | None: ...
    def subkeys(self, key: str) -> list[str]: ...
    def delete_tree(self, key: str) -> None: ...


class WinRegistry:
    """``HKEY_CURRENT_USER`` through ``winreg``; every failure is an absent value."""

    def read(self, key: str, name: str) -> str | None:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except (OSError, ImportError):
            return None

    def subkeys(self, key: str) -> list[str]:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
        except (OSError, ImportError):
            return []

    def delete_tree(self, key: str) -> None:
        try:
            import winreg

            for sub in self.subkeys(key):
                self.delete_tree(key + "\\" + sub)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except (OSError, ImportError):
            pass


def leftover_code(reg: Registry) -> str:
    """The migration package's own product code, "" when there is none or the value is not a product code."""
    code = (reg.read(MIGRATION_KEY, "ProductCode") or "").strip()
    return code if PRODUCT_CODE.match(code) else ""


def carried_shortcuts(reg: Registry) -> tuple[bool, bool] | None:
    """(Start menu entry, desktop shortcut) of the old install: the migration package's record, else the old install's
    own components when it is still there. None when neither says anything (a fresh install)."""
    own = [reg.read(MIGRATION_KEY, n) for n in ("StartMenu", "Desktop")]
    if any(v is not None for v in own):
        return bool(own[0]), bool(own[1])
    legacy = [reg.read(LEGACY_KEY, n) for n in ("StartMenu", "DesktopShortcut")]
    if any(v is not None for v in legacy):
        return legacy[0] is not None, legacy[1] is not None
    return None


def old_products(reg: Registry) -> list[str]:
    """Product codes of Windows-installer entries of this program in the user's Apps list: the old installs."""
    found = []
    for name in reg.subkeys(UNINSTALL_KEY):
        key = UNINSTALL_KEY + "\\" + name
        if (
            PRODUCT_CODE.match(name)
            and reg.read(key, "WindowsInstaller") == "1"
            and (reg.read(key, "DisplayName") or "").startswith(PRODUCT_NAME)
        ):
            found.append(name)
    return found


def uninstall_argv(code: str, system_root: str | None = None) -> list[str]:
    """``msiexec /x <code> /qn /norestart`` from the Windows folder's own System32 (not one in the working folder)."""
    if not PRODUCT_CODE.match(code):
        raise ValueError(f"not a product code: {code!r}")
    system = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [str(system / "msiexec.exe"), "/x", code, "/qn", "/norestart"]


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, check=False, timeout=600, **NO_WINDOW).returncode


def remove_product(
    code: str, run: Callable[[list[str]], int] = _run, sleep: Callable[[float], None] = time.sleep, tries: int = 12
) -> str:
    """Uninstall one product quietly. "removed", "busy" (another install kept running through every try: the migration
    package is still finishing, so the caller tries again at the next start) or "failed"."""
    for attempt in range(tries):
        try:
            code_back = run(uninstall_argv(code))
        except (OSError, subprocess.SubprocessError):
            return "failed"
        if code_back in OK_CODES:
            return "removed"
        if code_back != BUSY:
            return "failed"
        if attempt + 1 < tries:
            sleep(5.0)
    return "busy"


def remove_leftover(reg: Registry | None = None, run=_run, sleep=time.sleep) -> str:
    """The migration package's own entry removed quietly: "none" (nothing to do), "removed", "busy" or "failed". The
    record is deleted once it is gone, so the program asks no more."""
    reg = reg or WinRegistry()
    code = leftover_code(reg)
    if not code:
        return "none"
    result = remove_product(code, run, sleep)
    if result == "removed":
        reg.delete_tree(MIGRATION_KEY)
    return result
