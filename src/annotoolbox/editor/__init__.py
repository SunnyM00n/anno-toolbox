"""The island editor (docs/island-editor.md): a PC application that shows an island in 3D and edits it by hand.

``desktop/`` is the Qt application over ``editorapp`` (settings, island lists, jobs). ``islandeditor`` opens an
island, ``editsession`` is the edit document the tools work on. ``formats/`` holds each game's own part, by
island format family: ``formats/a7m`` (117, 1800, 2205), ``formats/isd`` (2070, 1404), ``formats/isc`` (1701);
everything else here is shared. ``a7mviewer`` and ``gameviewers`` draw the other games' islands
(``..othergames``), ``modelviewer`` draws their models from the games' files.
``features`` says which optional parts this edition has.
"""
