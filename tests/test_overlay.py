from pathlib import Path

import pytest

from annotoolbox.sources.overlay import SourceTree, classify_archive, normalize


def test_classify_archive_ranks():
    # InitFileSystem's order: data, language, patch1..8, exedata, add-on, add-on language, patch9
    order = ["Data0", "data5", "eng3", "patch1", "patch8", "exedata", "addon1", "addon_ger1", "patch9"]
    ranks = [classify_archive(n)[0] for n in order]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert classify_archive("eng3") == (203, "eng")
    assert classify_archive("addon_ger1") == (601, "ger")
    assert classify_archive("notes") is None


def test_normalize():
    assert normalize("data\\Graphics\\X.CFG") == "data/graphics/x.cfg"
    assert normalize("./data/a") == "data/a"
    assert normalize("/data/a") == "data/a"


def test_normalize_doubled_slashes_and_case():
    assert normalize("data/maps//X.psd") == "data/maps/x.psd"
    assert normalize("//Data/A.cfg", lower=False) == "Data/A.cfg"


def test_normalize_studio_paths():
    studio = "D:\\Projects\\anno8\\game\\data\\Graphics\\X.cfg"
    assert normalize(studio, studio=True) == "data/graphics/x.cfg"
    assert normalize(studio, studio=True, lower=False) == "data/Graphics/X.cfg"
    assert normalize(studio) == "d:/projects/anno8/game/data/graphics/x.cfg"


def test_the_tools_folders_follow_localappdata_or_the_xdg_folders_and_the_cache_lives_in_them(
    tmp_path: Path, monkeypatch
):
    from annotoolbox import paths
    from annotoolbox.sources.overlay import cache_base

    monkeypatch.delenv("ANNO2070_CACHE", raising=False)
    app = "anno-toolbox"
    if paths.os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert paths.local_data_dir() == paths.config_dir() == tmp_path / app
        assert cache_base() == paths.cache_dir() == tmp_path / app / "cache"
        monkeypatch.delenv("LOCALAPPDATA")
        monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
        assert paths.local_data_dir() == tmp_path / "AppData/Local" / app
        return
    for variable, folder in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    assert paths.local_data_dir() == tmp_path / "data" / app
    assert paths.config_dir() == tmp_path / "config" / app
    assert cache_base() == paths.cache_dir() == tmp_path / "cache" / app
    for variable in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, "relative/path")  # the spec ignores a relative one
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.local_data_dir() == tmp_path / ".local/share" / app
    assert paths.config_dir() == tmp_path / ".config" / app
    assert paths.cache_dir() == tmp_path / ".cache" / app


def test_the_documents_folder_on_linux_comes_from_the_user_dirs_file(tmp_path: Path, monkeypatch):
    from annotoolbox import paths

    if paths.os.name == "nt":
        pytest.skip("Windows asks the shell for its Documents folder")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert paths.documents_dir() == tmp_path / "home" / "Documents"
    (tmp_path / "user-dirs.dirs").write_text('# x\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n', encoding="utf-8")
    assert paths.documents_dir() == tmp_path / "home" / "Dokumente"


def test_patch_overrides_base(source_root: Path):
    tree = SourceTree(source_root)
    assert tree.read_text("data/graphics/marker.txt") == "patch1"
    assert tree.archive_of("data/graphics/marker.txt") == "patch1"
    assert [n for n, _ in tree.candidates("data/graphics/marker.txt")] == ["Data2", "patch1"]


def test_language_filter(source_root: Path):
    eng = SourceTree(source_root, language="eng")
    assert "ger0" not in [a.name for a in eng.archives]
    assert "eng0" in [a.name for a in eng.archives]
    ger = SourceTree(source_root, language="ger")
    assert "ger0" in [a.name for a in ger.archives]
    assert "eng0" not in [a.name for a in ger.archives]


def test_ignored_folders(source_root: Path):
    tree = SourceTree(source_root)
    assert tree.ignored_folders == ["notes"]
    assert not tree.exists("readme.txt")


def test_resolve_texture_variants(source_root: Path):
    tree = SourceTree(source_root)
    hit = tree.resolve_texture("data\\graphics\\buildings\\bakery\\maps\\bakery_diff.png")
    assert hit is not None and hit.name == "bakery_diff_0.dds"
    assert tree.resolve_texture("data/graphics/buildings/bakery/maps/missing.png") is None


def test_glob_and_iter(source_root: Path):
    tree = SourceTree(source_root)
    assert tree.glob("data/config/dlc_*/assets.xml") == []
    assert tree.glob("addondata/config/balancing/*_assets.xml") == ["addondata/config/balancing/addon_01_assets.xml"]
    assert list(tree.iter_paths("data/graphics/buildings", suffix=".rdm")) == [
        "data/graphics/buildings/bakery/rdm/bakery_lod0.rdm"
    ]
