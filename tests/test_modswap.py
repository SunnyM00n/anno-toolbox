"""Putting a mod into the mods folder and taking it out: links, folder names, the order of the swap, the bin's flags,
a zip of several mods (modloader/modswap.py, modmgr/install.py). Temp folders and fakes only: never the real bin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from tests.modmgrfakes import make_mod

from annotoolbox.modloader import modswap
from annotoolbox.modmgr import install

pytestmark = pytest.mark.skipif(os.name != "nt", reason="junctions and the shell's bin are Windows")


class Bin:
    """A recycle bin that only remembers what it was given."""

    def __init__(self, check=None) -> None:
        self.got: list[Path] = []
        self.check = check

    def __call__(self, path: Path) -> str:
        if self.check:
            self.check()
        self.got.append(Path(path))
        os.rmdir(path) if modswap.is_link(path) else shutil.rmtree(path)
        return "the test bin"


def _junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_a_junction_is_removed_as_the_link_and_its_target_stays(tmp_path):
    src, mods = make_mod(tmp_path / "devsrc", "dev"), tmp_path / "mods"
    mods.mkdir()
    _junction(mods / "dev", src)
    bin_ = Bin()
    install.remove_mod(mods / "dev", [mods], bin_)
    assert bin_.got == [mods / "dev"]  # the junction itself, not devsrc/dev
    _junction(mods / "dev", src)
    assert modswap.recycle(mods / "dev") and not os.path.lexists(mods / "dev")
    assert (src / "modinfo.json").is_file()  # the real recycle on the link left the source alone


def test_replacing_a_junctioned_mod_unlinks_it_and_keeps_the_source(tmp_path):
    src, mods = make_mod(tmp_path / "devsrc", "dev"), tmp_path / "mods"
    mods.mkdir()
    _junction(mods / "dev", src)
    new = make_mod(tmp_path / "new", "dev", version="2.0.0")
    [done] = install.install_path(new, mods, 8, modswap.recycle)
    assert done.replaced and (mods / "dev" / "modinfo.json").is_file() and not modswap.is_link(mods / "dev")
    assert (src / "modinfo.json").is_file()


def _zip(path: Path, mod_id: str, anno: int = 8) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modinfo.json", json.dumps({"Anno": anno, "ModID": mod_id, "Version": "1.0.0"}))
    return path


@pytest.mark.parametrize("mod_id", ["../escaped", "a/b", "C:/x", "con", "..\\escaped", "name."])
def test_a_modid_that_is_no_folder_name_installs_nothing_outside_the_mods_folder(tmp_path, mod_id):
    mods = tmp_path / "docs" / "mods"
    with pytest.raises(install.InstallError, match="not a folder name|not a mod the loader"):
        install.install_path(_zip(tmp_path / "bad.zip", mod_id), mods, 8, Bin())
    assert sorted(p.name for p in (tmp_path / "docs").iterdir()) == ["mods"]
    assert [p.name for p in mods.iterdir()] == []


def test_the_old_copy_stays_when_the_new_one_cannot_be_placed(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    old = make_mod(mods, "keep", version="1.0.0")
    new = make_mod(tmp_path / "src", "keep", version="2.0.0")
    real, bin_ = os.replace, Bin()

    def refuse(src, dst):
        if Path(dst) == old and Path(src).name == "keep":
            raise PermissionError(5, "a scanner holds the folder")
        return real(src, dst)

    monkeypatch.setattr(modswap.os, "replace", refuse)
    with pytest.raises(install.InstallError, match="could not be installed"):
        install.install_path(new, mods, 8, bin_)
    assert json.loads((old / "modinfo.json").read_text())["Version"] == "1.0.0" and bin_.got == []


def test_the_old_copy_goes_to_the_bin_only_after_the_new_one_is_in_place(tmp_path):
    mods = tmp_path / "mods"
    make_mod(mods, "keep", version="1.0.0")
    new = make_mod(tmp_path / "src", "keep", version="2.0.0")

    def new_is_there():
        assert json.loads((mods / "keep" / "modinfo.json").read_text())["Version"] == "2.0.0"

    bin_ = Bin(new_is_there)
    install.install_path(new, mods, 8, bin_)
    assert len(bin_.got) == 1 and bin_.got[0].name == "old"


def test_a_bin_that_refuses_leaves_the_old_copy_parked_with_a_note(tmp_path):
    mods = tmp_path / "mods"
    make_mod(mods, "keep", version="1.0.0")
    new = make_mod(tmp_path / "src", "keep", version="2.0.0")

    def refuse(_path):
        raise OSError("no bin here")

    [done] = install.install_path(new, mods, 8, refuse)
    parked = [p for p in mods.iterdir() if p.name.startswith(modswap.STAGING + "old-keep")]
    assert len(parked) == 1 and (parked[0] / "modinfo.json").is_file() and "kept as" in done.notes[-1]


def test_the_shell_is_told_to_warn_when_the_bin_would_destroy_for_good(tmp_path, monkeypatch):
    import ctypes

    seen = []

    def fake(op):
        seen.append(op._obj.flags)
        return 0

    monkeypatch.setattr(ctypes.windll.shell32, "SHFileOperationW", fake)
    modswap.recycle(make_mod(tmp_path, "x"))
    assert seen and seen[0] & 0x4000 and seen[0] & 0x40  # FOF_WANTNUKEWARNING with FOF_ALLOWUNDO


def test_a_zip_with_a_bad_mod_installs_none_of_its_mods(tmp_path):
    mods = tmp_path / "mods"
    pack = tmp_path / "pack"
    make_mod(pack, "good")
    make_mod(pack, "wrong", Anno=7)
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in pack.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(tmp_path).as_posix())
    with pytest.raises(install.InstallError, match="wrong.*nothing was installed"):
        install.install_path(zpath, mods, 8, Bin())
    assert not list(mods.iterdir())


def test_two_folders_of_one_install_with_one_name_are_refused(tmp_path):
    pack = tmp_path / "pack"
    make_mod(pack / "A", "mod", mod_id="same")
    make_mod(pack / "B", "mod", mod_id="same")
    with pytest.raises(install.InstallError, match="both go to"):
        install.install_path(pack, tmp_path / "mods", 8, Bin())
