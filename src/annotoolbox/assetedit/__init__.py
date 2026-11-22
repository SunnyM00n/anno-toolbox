"""The asset editor: a game's assets in a tree, each one's values in a form made from the game's schema, edits kept
as a change set and written as a mod for the mod loader or as the game's own files. Plan: ``docs/tool-suite.md``.

``assetgame.open_game`` opens a game: ``gamefiles`` (where its files are), ``schema`` (properties, templates, data
sets), ``assetindex`` (every asset by GUID, parsed on demand). ``assetdoc.CAssetDocument`` is the document the window
edits (``values`` its layers, ``changeset`` its file, one undo stack from ``suite.undo``); ``modops`` and ``native``
write it out, ``checks`` checks it, ``compare`` diffs it and finds an asset's users, ``balancing`` is the table view's
data and the CSV files; ``modimport`` turns a mod's ModOps into a change set (``assetdiff`` the per-asset part);
``projectpart`` is the editor's share of a project's mod (``suite.project``).
The window: ``assetwindow`` (the tree, the tabs), ``assetform`` (the generated form); ``xmltree`` the XML helpers.
"""
