"""The island lists and openers the files check and the template donors share (``editor/islandfiles``) follow each game's
record in ``othergames.installs``, and the viewer table resolves its classes."""

from annotoolbox.editor import editorapp, islandfiles
from fakes import FakeTree


class _Listed(FakeTree):
    """What 117's ``Files`` adds to a tree: ``list`` by prefix."""

    def list(self, prefix: str) -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix))


def test_the_island_lists_follow_the_games_records():
    paths = {
        "data/base/provinces/roman/islands/pool/a/a.a7m": b"",
        "data/base/ui/something.a7m": b"",  # an .a7m that is no island: 117 keeps others in data/
        "data/levels/islands/normal/n_a.isd": b"",
        "data/levels/islands/normal/n_a.isc": b"",
        "data/levels/other/x.isd": b"",
    }
    tree = _Listed(paths)
    assert islandfiles.island_paths("anno117", tree) == ["data/base/provinces/roman/islands/pool/a/a.a7m"]
    assert islandfiles.island_paths("anno2070", tree) == ["data/levels/islands/normal/n_a.isd"]
    assert islandfiles.island_paths("anno1701", tree) == ["data/levels/islands/normal/n_a.isd"]


def test_the_viewer_table_names_a_class_for_every_editor_kind_of_the_other_games():
    from annotoolbox.othergames import installs

    kinds = {g.editor for g in installs.GAMES.values()} - {"117", "2070"}
    assert kinds == set(editorapp.VIEWER_CLASSES)
    assert {editorapp.viewer_class(k).__name__ for k in kinds} == {
        "A7mViewer",
        "A6mViewer",
        "IsdViewer",
        "Isd1701Viewer",
    }
