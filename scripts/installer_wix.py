"""The migration package: a small ``.msi`` for the one job of moving a 0.1.x install to the installer of today.

Every release carries one under the name the old updater looks for (``AnnoToolbox-<version>.msi``). It replaces the old
install (same upgrade code, a higher version: a major upgrade), carries the release's ``Setup.exe`` and starts it as the
user once the install has finished, and records what the program needs to finish the move (its own product code, the
shortcuts the old install had). ``docs/installer.md``, "The move from v0.1.x". ``migration_wxs`` is the pure part;
``build_migration`` runs WiX; ``sign`` is the optional signing step.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from xml.sax.saxutils import quoteattr

import installer_bundle as bundle
from installer_bundle import Edition

WIX_VERSION = "6.0.2"
MANUFACTURER = "Sunny_Moon and the Anno Toolbox contributors"
SIGNABLE = (".exe", ".dll", ".pyd")
MIGRATION_KEY = r"Software\AnnoToolboxMigration"  # read by suite/migrate.py
LEGACY_KEY = r"Software\AnnoToolbox\Components"  # the old install's records, read before it is removed
FOLDER = "Anno Toolbox Migration"  # where the package leaves Setup.exe, under %LOCALAPPDATA%
SETUP_ID = "SetupExe"


def component_guid(edition: Edition, name: str) -> str:
    """The component's GUID: fixed by the edition and the name."""
    return "{" + str(uuid.uuid5(uuid.UUID(edition.upgrade_code), "migration:" + name)).upper() + "}"


def _attr(value: str) -> str:
    return quoteattr(value)


def migration_wxs(edition: Edition, version: str) -> str:
    """The WiX source of the migration package; ``$(var.SetupExe)`` is the release's ``Setup.exe``.

    Per user, no administrator. ``HAD_START_MENU`` and ``HAD_DESKTOP`` are read from the old install's records before
    the major upgrade removes it (the search runs first), and written to the migration key."""
    legacy = _attr(LEGACY_KEY)
    key = _attr(MIGRATION_KEY)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
        f'  <Package Name={_attr(edition.product)} Manufacturer={_attr(MANUFACTURER)} Version="{version}" '
        f'UpgradeCode="{edition.upgrade_code}" Scope="perUser" Language="1033">',
        f"    <SummaryInformation Description={_attr(edition.product + ' - moves an older installation to the new installer')} />",
        f'    <MajorUpgrade DowngradeErrorMessage="A newer version of {edition.product} is already installed." />',
        '    <MediaTemplate EmbedCab="yes" CompressionLevel="none" />',
        f'    <Property Id="HAD_START_MENU"><RegistrySearch Id="FindStartMenu" Root="HKCU" Key={legacy} '
        'Name="StartMenu" Type="raw" /></Property>',
        f'    <Property Id="HAD_DESKTOP"><RegistrySearch Id="FindDesktop" Root="HKCU" Key={legacy} '
        'Name="DesktopShortcut" Type="raw" /></Property>',
        '    <StandardDirectory Id="LocalAppDataFolder">',
        f'      <Directory Id="MigrationDir" Name={_attr(FOLDER)}>',
        f'        <Component Id="MigrationFiles" Guid="{component_guid(edition, "files")}">',
        f'          <File Id="{SETUP_ID}" Source="$(var.SetupExe)" Name="Setup.exe" />',
        '          <RemoveFolder Id="RemoveMigrationDir" On="uninstall" />',
        f'          <RegistryValue Root="HKCU" Key={key} Name="ProductCode" Type="string" Value="[ProductCode]" KeyPath="yes" />',
        f'          <RegistryValue Root="HKCU" Key={key} Name="StartMenu" Type="string" Value="[HAD_START_MENU]" />',
        f'          <RegistryValue Root="HKCU" Key={key} Name="Desktop" Type="string" Value="[HAD_DESKTOP]" />',
        "        </Component>",
        "      </Directory>",
        "    </StandardDirectory>",
        # the installer's own setup, as the user (a per-user package is not elevated), once this install has finished
        f'    <CustomAction Id="RunSetup" FileRef="{SETUP_ID}" ExeCommand="" Execute="immediate" Return="asyncNoWait" />',
        '    <InstallExecuteSequence><Custom Action="RunSetup" After="InstallFinalize" Condition="NOT Installed" />'
        "</InstallExecuteSequence>",
        f'    <Feature Id="Main" Title={_attr(edition.product)} Level="1" AllowAbsent="no">',
        '      <ComponentRef Id="MigrationFiles" />',
        "    </Feature>",
        "  </Package>",
        "</Wix>",
    ]
    return "\n".join(lines) + "\n"


def migration_name(edition: Edition, version: str) -> str:
    """The file name the old updater looks for in a release: ``AnnoToolbox-<version>.msi`` (``suite/updates.py`` of
    0.1.x)."""
    return f"{bundle.PROGRAM}-{version}{edition.suffix}.msi"


def migration_command(wix: str, wxs_path: str, out: str, setup_exe: str) -> list[str]:
    """``wix build`` for the source: x64, the one variable the source reads."""
    return [wix, "build", wxs_path, "-arch", "x64", "-d", f"SetupExe={setup_exe}", "-o", out]


def build_migration(wix: Path, edition: Edition, version: str, setup_exe: Path, out_dir: Path) -> Path:
    """Write the migration package for ``version`` into ``out_dir``; its source is kept beside it in ``out_dir/msi``."""
    out_dir = out_dir.resolve()  # the run's working folder is WiX's own
    work = out_dir / "msi"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "migration.wxs"
    source.write_text(migration_wxs(edition, version), encoding="utf-8", newline="\n")
    out = out_dir / migration_name(edition, version)
    subprocess.run(
        migration_command(str(wix), str(source), str(out), str(setup_exe.resolve())), check=True, cwd=wix.parent
    )
    return out


# Signing (optional)


def signable(files: list[str], package: str) -> list[str]:
    """What the signing step signs: the program's own exes and the libraries and extension modules of the package itself
    (not the libraries of others, which carry their own signatures or none)."""
    ours = [f for f in files if f.lower().endswith((".exe", ".com")) and "/" not in f]  # AnnoToolbox.exe and .com
    ours += [f for f in files if f.lower().endswith(SIGNABLE) and f.startswith(f"_internal/{package}/")]
    return sorted(ours)


def sign_commands(template: str, paths: list[str]) -> list[list[str]]:
    """One argv per file from ``template`` (a command with ``{file}``); an error when the template has no place."""
    if "{file}" not in template:
        raise SystemExit("--sign needs a command with {file} in it, e.g. 'signtool sign /fd SHA256 /a {file}'")
    return [shlex.split(template.replace("{file}", p), posix=False) for p in paths]


def sign(template: str, paths: list[str]) -> None:
    for argv in sign_commands(template, paths):
        subprocess.run(argv, check=True)


def find_wix() -> Path | None:
    """WiX: ``ANNO_WIX``, ``tools/wix/wix.exe`` (``dotnet tool install wix --tool-path tools/wix``), then PATH."""
    options = [Path(os.environ["ANNO_WIX"])] if os.environ.get("ANNO_WIX") else []
    options.append(bundle.ROOT / "tools" / "wix" / "wix.exe")
    if on_path := shutil.which("wix"):
        options.append(Path(on_path))
    return next((p for p in options if p.is_file()), None)
