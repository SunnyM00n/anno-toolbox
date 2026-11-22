"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the island
catalogue and the editor's asset lookups: each test drives the reader with the real class, or a stand-in carrying
the real class's names."""

from __future__ import annotations

from types import SimpleNamespace


def test_the_island_catalogue_opens_the_2070_tree_through_its_method(monkeypatch):
    from annotoolbox.editor import islandcatalogue

    tree = object()

    class CCatalogue(islandcatalogue.CIslandCatalogue):
        def tree2070(self):
            return tree

    catalogue = object.__new__(CCatalogue)
    catalogue.m_settings, catalogue._m_list2070 = SimpleNamespace(anno2070="G:/2070"), None
    seen = []
    monkeypatch.setattr(islandcatalogue.isd_islandlist, "island_paths", lambda got: seen.append(got) or ["a/b.isd"])
    monkeypatch.setattr(islandcatalogue.isd_islandlist, "island_groups", lambda got, paths: dict.fromkeys(paths, "g"))
    names, groups = catalogue.islands2070()
    assert seen == [tree] and names == {"a/b.isd": "a/b.isd"} and groups == {"a/b.isd": "g"}


def test_a_texture_that_did_not_load_reaches_the_checks_tabs_list():
    from annotoolbox.editor.islandeditor import CIslandEditor

    editor = object.__new__(CIslandEditor)
    editor._m_resolver = SimpleNamespace(missing=lambda: [])
    editor.texture_failure("data/graphics/a.psd", OSError("gone"))
    assert editor.file_problems() == [("data/graphics/a.psd", "OSError: gone", "")]
