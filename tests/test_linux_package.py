"""The Linux download: the packer's command, the release's files, the tarball, the desktop entry, the AppImage's place,
the Linux release's checksum file (scripts/installer_linux.py, suite/install.py, suite/shortcuts.py, suite/updates.py)."""

import json
import sys
import tarfile

import pytest

from annotoolbox.paths import REPO_ROOT
from annotoolbox.suite import install, installevents, shortcuts, updates

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import installer_bundle as bundle
import installer_linux as linux

EDITION = bundle.EDITIONS["public"]


def test_pack_command_is_an_appimage_without_windows_options(tmp_path):
    cmd = linux.pack_command("vpk", EDITION, "0.2.1rc1", tmp_path / "app", tmp_path, tmp_path / "i.png")
    assert cmd[:2] == ["vpk", "pack"]
    assert cmd[cmd.index("--runtime") + 1] == "linux-x64"
    assert cmd[cmd.index("--mainExe") + 1] == "AnnoToolbox"  # the window program, not the console command
    assert cmd[cmd.index("--packVersion") + 1] == "0.2.1-rc1"
    assert "--shortcuts" not in cmd and "--noPortable" not in cmd and "--signTemplate" not in cmd
    assert cmd[cmd.index("--icon") + 1].endswith("i.png")


def test_release_file_names_carry_the_system(tmp_path):
    assert linux.release_appimage_name(EDITION) == "AnnoToolbox-linux-x86_64.AppImage"
    assert linux.tarball_name(EDITION, "0.2.1") == "AnnoToolbox-0.2.1-linux-x86_64.tar.gz"
    for name in ("AnnoToolbox-linux-x86_64.AppImage", "AnnoToolbox-0.2.1-linux-full.nupkg", "releases.linux.json"):
        (tmp_path / name).write_bytes(b"x")
    found = [p.name for p in linux.release_files(tmp_path, EDITION, "0.2.1")]
    assert found == ["AnnoToolbox-linux-x86_64.AppImage", "AnnoToolbox-0.2.1-linux-full.nupkg", "releases.linux.json"]


def test_tarball_keeps_one_top_folder_and_the_modes(tmp_path):
    app = tmp_path / "AnnoToolbox"
    (app / "_internal").mkdir(parents=True)
    (app / "AnnoToolbox").write_bytes(b"elf")
    (app / "AnnoToolbox").chmod(0o755)
    (app / "_internal" / "x.txt").write_text("x")
    path = linux.make_tarball(app, tmp_path, EDITION, "0.2.1")
    with tarfile.open(path) as tar:
        names = tar.getnames()
        assert names[0] == "AnnoToolbox" and all(n.startswith("AnnoToolbox") for n in names)
        assert "AnnoToolbox/_internal/x.txt" in names
        assert tar.getmember("AnnoToolbox/AnnoToolbox").mode & 0o111 == 0o111 or sys.platform == "win32"


def test_sums_file_is_written_and_read_back(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"1")
    sums = linux.write_sums(tmp_path, [tmp_path / "a.bin"])
    assert sums.name == "SHA256SUMS-linux"
    assert bundle.verify_sums(tmp_path, sums.read_text(encoding="utf-8")) == []


def test_add_files_writes_icon_template_and_script(tmp_path):
    pytest.importorskip("PIL")
    linux.add_files(tmp_path)
    assert (tmp_path / "anno-toolbox.png").stat().st_size > 100
    assert "@DIR@/AnnoToolbox" in (tmp_path / "anno-toolbox.desktop.in").read_text(encoding="utf-8")
    assert (tmp_path / "install-desktop-entry.sh").read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_appimage_is_found_only_beside_the_updater(tmp_path):
    image = tmp_path / "AnnoToolbox.AppImage"
    image.write_bytes(b"x")
    mount = tmp_path / "mount" / "usr" / "bin"
    mount.mkdir(parents=True)
    exe = str(mount / "AnnoToolbox")
    env = {"APPIMAGE": str(image)}
    if sys.platform == "win32":
        assert install.appimage(exe, env) is None
        return
    assert install.appimage(exe, env) is None  # a program folder started by hand has no updater beside it
    (mount / "UpdateNix").write_bytes(b"x")
    assert install.appimage(exe, env) == image
    assert install.appimage(exe, {}) is None  # started without the AppImage runtime
    assert install.appimage(None, env) is None  # a source run (this interpreter is not a frozen program)


def test_desktop_entry_quotes_the_path_and_reads_back(tmp_path, monkeypatch):
    target = tmp_path / "My Apps" / 'Tool "x" $y.AppImage'
    text = shortcuts.desktop_entry(target, tmp_path / "icon.png")
    assert "Exec=" in text and "Icon=" in text and text.startswith("[Desktop Entry]")
    assert '\\\\"x\\\\"' in text and "\\\\$y" in text
    entry = tmp_path / "anno-toolbox.desktop"
    entry.write_text(text, encoding="utf-8")
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    assert shortcuts.link_target(entry) == target


def test_remove_ours_takes_the_entry_that_points_at_the_appimage(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    image = tmp_path / "AnnoToolbox.AppImage"
    menu, desk = tmp_path / "applications", tmp_path / "Desktop"
    menu.mkdir()
    desk.mkdir()
    (menu / "anno-toolbox.desktop").write_text(shortcuts.desktop_entry(image, None), encoding="utf-8")
    (desk / "anno-toolbox.desktop").write_text(shortcuts.desktop_entry(tmp_path / "other", None), encoding="utf-8")
    gone = shortcuts.remove_ours(shortcuts.Places(menu, desk), image)
    assert [p.parent.name for p in gone] == ["applications"]
    assert (desk / "anno-toolbox.desktop").is_file()  # somebody else's entry stays


def test_before_uninstall_on_linux_uses_the_appimage_as_root(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    image = tmp_path / "AnnoToolbox.AppImage"
    menu = tmp_path / "applications"
    menu.mkdir()
    (menu / "anno-toolbox.desktop").write_text(shortcuts.desktop_entry(image, None), encoding="utf-8")
    done = installevents.before_uninstall(tmp_path / "data", shortcuts.Places(menu, tmp_path / "d"), image)
    assert len(done["shortcuts"]) == 1 and not (menu / "anno-toolbox.desktop").exists()


def test_linux_release_needs_its_own_checksum_file(monkeypatch):
    monkeypatch.setattr(updates, "windows", lambda: False)

    def raw(names):
        assets = [{"name": n, "browser_download_url": f"https://example.test/{n}"} for n in names]
        return json.dumps({"tag_name": "v0.2.1", "assets": assets, "html_url": "https://example.test/r"}).encode()

    release = updates.parse_release(raw(["SHA256SUMS", "SHA256SUMS-linux", updates.HELPER_SUMS]))
    assert release.sums_url.endswith("/SHA256SUMS-linux") and release.helper_sums_url == ""
    with pytest.raises(updates.UpdateError):  # a release with Windows files only has no Linux build
        updates.parse_release(raw(["SHA256SUMS"]))
