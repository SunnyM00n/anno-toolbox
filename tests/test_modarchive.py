"""An island packed in a mod's archive: found and read by game path, listed by the app, opened by the editor."""

from annotoolbox.editor import editorapp
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands.islandview import Files
from annotoolbox.suite.settings import Settings
from test_islandinfo import SOURCE, island_files

INFO = SOURCE[:-4] + ".a7minfo"


class NoGame:
    def read(self, key):
        raise FileNotFoundError(key)

    def iter_paths(self, prefix=""):
        return iter(())


def mod_with_archive(tmp_path):
    files = island_files()
    mod = tmp_path / "mymod"
    (mod / "data/loose").mkdir(parents=True)
    (mod / "data/loose/free.a7m").write_bytes(b"loose")
    (mod / "pack.rda").write_bytes(write_v22({SOURCE: files.read(SOURCE), INFO: files.read(INFO)}))
    return mod, files


def test_an_archive_of_the_mod_is_indexed_only_when_asked_for(tmp_path):
    mod, files = mod_with_archive(tmp_path)
    plain = Files(NoGame(), mod)
    assert plain.where(SOURCE) is None and plain.list("data/") == ["data/loose/free.a7m"]
    packed = Files(NoGame(), mod, archives=True)
    assert packed.where(SOURCE) == "mod" and packed.read(SOURCE) == files.read(SOURCE)
    assert packed.read(SOURCE.upper().replace("\\", "/")) == files.read(SOURCE)  # found as the game finds a path
    assert packed.mod_paths(".a7m") == ["data/base/provinces/roman/islands/pool/isle/isle.a7m", "data/loose/free.a7m"]
    assert packed.list("data/base/provinces/") == sorted([SOURCE, INFO])
    assert not packed.unreadable


def test_a_loose_file_wins_over_the_same_path_in_an_archive(tmp_path):
    mod, files = mod_with_archive(tmp_path)
    (mod / SOURCE).parent.mkdir(parents=True)
    (mod / SOURCE).write_bytes(b"the loose one")
    assert Files(NoGame(), mod, archives=True).read(SOURCE) == b"the loose one"


def test_a_file_that_is_no_archive_is_listed_not_fatal(tmp_path):
    mod, _ = mod_with_archive(tmp_path)
    (mod / "broken.rda").write_bytes(b"this is not an archive at all, not even a header")
    packed = Files(NoGame(), mod, archives=True)
    assert packed.unreadable == ["broken.rda"] and packed.where(SOURCE) == "mod"


def test_the_app_lists_a_mods_islands_loose_and_packed_without_the_builds_base_copies(tmp_path, monkeypatch):
    mod, _ = mod_with_archive(tmp_path)
    (mod / "data/loose/free_base").mkdir()
    (mod / "data/loose/free_base/free_base.a7m").write_bytes(b"base")
    settings = Settings(anno117=str(tmp_path), workspace=str(tmp_path / "ws"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json")
    monkeypatch.setattr(editorapp.App, "game117", lambda self: NoGame())
    assert app.mod_islands(mod) == [SOURCE, "data/loose/free.a7m"]
    assert app.files().where(SOURCE) is None  # the workspace's own mod is read as it always was: loose files only


def test_an_island_read_out_of_the_archive_opens_as_any_island_file(tmp_path):
    mod, files = mod_with_archive(tmp_path)
    packed = Files(NoGame(), mod, archives=True)
    isl = a7m_edit117.IslandFile(packed.read(SOURCE), packed.read(INFO))
    assert isl.size == 32 and isl.info.find("ActiveMapRect") is not None
