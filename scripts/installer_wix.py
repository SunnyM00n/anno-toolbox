"""The .msi of the Toolbox: the WiX source written from a program folder, the licence page, signing, and the WiX runs.

``wxs`` is the pure part (a folder's file list in, XML out); ``build_msi`` runs WiX; ``sign`` is the optional signing
step. The rules (per-user, upgrades, uninstall) are in ``docs/installer.md``.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import quoteattr

import installer_bundle as bundle
from installer_bundle import Edition

WIX_VERSION = "6.0.2"
UI_EXTENSION = "WixToolset.UI.wixext"
MANUFACTURER = "Sunny_Moon and the Anno Toolbox contributors"
SIGNABLE = (".exe", ".dll", ".pyd")
SHORTCUT_ARGUMENTS = "--app islands"  # the suite's window; without --app the program is the island editor alone


def short_id(prefix: str, text: str) -> str:
    """A stable identifier for WiX from a path: ``prefix`` and 12 hex digits of its hash."""
    return prefix + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def component_guid(edition: Edition, directory: str) -> str:
    """The component's GUID: fixed by the edition and the folder, so an upgrade finds the same components."""
    return "{" + str(uuid.uuid5(uuid.UUID(edition.upgrade_code), "dir:" + directory)).upper() + "}"


def directories(files: list[str]) -> list[str]:
    """Every folder that holds a file or a folder ("" the root), parents first; "" is the program folder."""
    found = {""}
    for file in files:
        parent = PurePosixPath(file).parent
        while str(parent) != ".":
            found.add(parent.as_posix())
            parent = parent.parent
    return sorted(found, key=lambda d: (d.count("/") if d else -1, d))


def _attr(value: str) -> str:
    return quoteattr(value)


def _component(edition: Edition, directory: str, files: list[str], program_id: str, hive: str) -> list[str]:
    """One component: the folder's files, a registry value as the key path (a per-user install needs one), and
    the folder's removal on uninstall (the root is the install folder, removed too)."""
    cid = short_id("c", directory or ".")
    sub = f" Subdirectory={_attr(directory.replace('/', chr(92)))}" if directory else ""
    out = [f'      <Component Id="{cid}" Guid="{component_guid(edition, directory)}"{sub}>']
    for name in files:
        fid = program_id if name == bundle.PROGRAM + ".exe" and not directory else short_id("f", f"{directory}/{name}")
        source = f"{directory}/{name}" if directory else name
        out.append(f'        <File Id="{fid}" Source="$(var.AppDir)\\{source.replace("/", chr(92))}" />')
    out.append(f'        <RemoveFolder Id="{short_id("r", directory or ".")}" On="uninstall" />')
    out.append(
        f'        <RegistryValue Root="{hive}" Key={_attr(edition.registry_key + chr(92) + "Components")} '
        f'Name="{cid}" Type="integer" Value="1" KeyPath="yes" />'
    )
    out.append("      </Component>")
    return out


def wxs(edition: Edition, version: str, files: list[str], scope: str = "perUser") -> str:
    """The WiX source for a program folder (``files``: relative paths with "/"); ``$(var.AppDir)`` is the folder.

    A component per folder (stable GUIDs), the Start-menu shortcut, the licence page, a ticked "start it" box on the
    last page, a major upgrade through the edition's upgrade code. ``scope`` is "perUser" (no administrator) or
    "perMachine"."""
    by_dir: dict[str, list[str]] = {d: [] for d in directories(files)}
    for file in files:
        path = PurePosixPath(file)
        by_dir["" if str(path.parent) == "." else path.parent.as_posix()].append(path.name)
    program_id = "AnnoToolboxExe"
    hive = "HKCU" if scope == "perUser" else "HKLM"
    root = "LocalAppDataFolder" if scope == "perUser" else "ProgramFiles64Folder"
    programs = '<Directory Id="ProgramsDir" Name="Programs">' if scope == "perUser" else ""
    close = "</Directory>" if scope == "perUser" else ""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">',
        f'  <Package Name={_attr(edition.product)} Manufacturer={_attr(MANUFACTURER)} Version="{version}" '
        f'UpgradeCode="{edition.upgrade_code}" Scope="{scope}" Language="1033">',
        f"    <SummaryInformation Description={_attr(edition.product + ' - an unofficial fan project')} />",
        f'    <MajorUpgrade DowngradeErrorMessage="A newer version of {edition.product} is already installed." />',
        '    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />',
        # the recorded folder is found again at an upgrade and an uninstall, whichever folder the install chose
        f'    <Property Id="INSTALLFOLDER"><RegistrySearch Id="FindInstall" Root="{hive}" '
        f'Key={_attr(edition.registry_key)} Name="InstallFolder" Type="directory" /></Property>',
        '    <WixVariable Id="WixUILicenseRtf" Value="$(var.LicenseRtf)" />',
        # Welcome, licence, what to install (the shortcuts are features of their own), progress, finish
        '    <ui:WixUI Id="WixUI_FeatureTree" />',
        # the last page's "start it now" box, ticked; it starts the program as the shortcut does
        f'    <Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value={_attr("Start " + edition.product)} />',
        '    <Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOX" Value="1" />',
        f'    <CustomAction Id="StartProgram" FileRef="{program_id}" ExeCommand="{SHORTCUT_ARGUMENTS}" '
        'Return="asyncNoWait" />',
        '    <UI><Publish Dialog="ExitDialog" Control="Finish" Event="DoAction" Value="StartProgram" '
        'Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 and NOT Installed" /></UI>',
        f'    <StandardDirectory Id="{root}">',
        f"      {programs}",
        f'      <Directory Id="INSTALLFOLDER" Name={_attr(edition.install_folder)} />',
        f"      {close}",
        "    </StandardDirectory>",
        '    <StandardDirectory Id="ProgramMenuFolder">',
        f'      <Directory Id="StartMenuDir" Name={_attr(edition.product)} />',
        "    </StandardDirectory>",
        '    <ComponentGroup Id="AppFiles" Directory="INSTALLFOLDER">',
    ]
    for directory, names in by_dir.items():
        lines += _component(edition, directory, names, program_id, hive)
    lines += [
        "    </ComponentGroup>",
        *_shortcuts(edition, hive),
        f'    <Feature Id="Main" Title={_attr(edition.product)} Level="1" AllowAbsent="no" AllowAdvertise="no" '
        'Display="expand" Description="The program">',
        '      <ComponentGroupRef Id="AppFiles" />',
        '      <ComponentRef Id="InstallRecord" />',
        '      <Feature Id="StartMenuEntry" Title="Start menu entry" Level="1" AllowAdvertise="no" '
        'Description="A shortcut in the Start menu">',
        '        <ComponentRef Id="StartMenu" />',
        "      </Feature>",
        '      <Feature Id="DesktopShortcut" Title="Desktop shortcut" Level="1" AllowAdvertise="no" '
        'Description="A shortcut on the desktop">',
        '        <ComponentRef Id="DesktopShortcut" />',
        "      </Feature>",
        "    </Feature>",
        "  </Package>",
        "</Wix>",
    ]
    return "\n".join(line for line in lines if line.strip()) + "\n"


def _shortcuts(edition: Edition, hive: str) -> list[str]:
    """The Start-menu entry and the desktop shortcut (each its own feature, so the installer asks for it), and the
    install folder's record, which an upgrade and an uninstall read back."""
    components = hive, _attr(edition.registry_key + chr(92) + "Components")
    # by folder, not [#file]: the exe's component is in the parent feature (a file reference across features is ICE69)
    shortcut = (
        f'Name={_attr(edition.product)} Target="[INSTALLFOLDER]{bundle.PROGRAM}.exe" Arguments="{SHORTCUT_ARGUMENTS}" '
        'WorkingDirectory="INSTALLFOLDER"'
    )
    return [
        '    <StandardDirectory Id="DesktopFolder">',
        f'      <Component Id="DesktopShortcut" Guid="{component_guid(edition, "<desktop>")}">',
        f'        <Shortcut Id="DesktopAppShortcut" {shortcut} />',
        f'        <RegistryValue Root="{components[0]}" Key={components[1]} Name="DesktopShortcut" Type="integer" '
        'Value="1" KeyPath="yes" />',
        "      </Component>",
        "    </StandardDirectory>",
        '    <DirectoryRef Id="StartMenuDir">',
        f'      <Component Id="StartMenu" Guid="{component_guid(edition, "<start menu>")}">',
        f'        <Shortcut Id="AppShortcut" {shortcut} />',
        '        <RemoveFolder Id="RemoveStartMenuDir" On="uninstall" />',
        f'        <RegistryValue Root="{components[0]}" Key={components[1]} Name="StartMenu" Type="integer" '
        'Value="1" KeyPath="yes" />',
        "      </Component>",
        "    </DirectoryRef>",
        '    <DirectoryRef Id="INSTALLFOLDER">',
        f'      <Component Id="InstallRecord" Guid="{component_guid(edition, "<install record>")}">',
        f'        <RegistryValue Root="{hive}" Key={_attr(edition.registry_key)} Name="InstallFolder" Type="string" '
        'Value="[INSTALLFOLDER]" KeyPath="yes" />',
        "      </Component>",
        "    </DirectoryRef>",
    ]


def rtf(text: str) -> str:
    """Plain text as an RTF document (the licence page of the installer): non-ASCII as \\uN, newlines as \\par."""
    out = []
    for ch in text.replace("\r\n", "\n"):
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\par\n")
        elif ord(ch) > 126:
            code = ord(ch) if ord(ch) < 32768 else ord(ch) - 65536
            out.append(f"\\u{code}?")
        else:
            out.append(ch)
    return "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\fs18\n" + "".join(out) + "}"


def licence_page(tree) -> str:
    """The text the installer shows before it installs: the unofficial notice, the licence, the third-party list."""
    return "\n".join([bundle.NOTICE, "-" * 60, bundle.licence_text(tree), "-" * 60,
                      bundle.third_party_text(bundle.third_party())])  # fmt: skip


# Signing (optional)


def signable(files: list[str], package: str) -> list[str]:
    """What the signing step signs: the program's own exes and the libraries and extension modules of the package itself
    (not the libraries of others, which carry their own signatures or none)."""
    ours = [f for f in files if f.lower().endswith(".exe") and "/" not in f]
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


# WiX


def wix_command(wix: str, wxs_path: str, out: str, app_dir: str, rtf_path: str, scope: str) -> list[str]:
    """``wix build`` for the source: x64, the UI extension, the two variables the source reads."""
    return [
        wix, "build", wxs_path, "-arch", "x64", "-ext", UI_EXTENSION, "-d", f"AppDir={app_dir}",
        "-d", f"LicenseRtf={rtf_path}", "-o", out,
    ]  # fmt: skip


def find_wix() -> Path | None:
    """WiX: ``ANNO_WIX``, ``tools/wix/wix.exe`` (``dotnet tool install wix --tool-path tools/wix``), then PATH."""
    options = [Path(os.environ["ANNO_WIX"])] if os.environ.get("ANNO_WIX") else []
    options.append(bundle.ROOT / "tools" / "wix" / "wix.exe")
    if on_path := shutil.which("wix"):
        options.append(Path(on_path))
    return next((p for p in options if p.is_file()), None)


def build_msi(wix: Path, wxs_path: Path, app_dir: Path, rtf_path: Path, out: Path, scope: str) -> None:
    """Run ``wix build``; its folder is the working directory, where the UI extension was added to the tool."""
    cmd = wix_command(str(wix), str(wxs_path), str(out), str(app_dir), str(rtf_path), scope)
    subprocess.run(cmd, check=True, cwd=wix.parent)
