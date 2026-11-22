"""The map editor (the suite's Maps app): a game's sessions as maps, edited from above and written as a mod.

Start at ``mapwindow.CMapWindow`` (the window) or ``mapdoc.open_map`` (the open map with its undo stack). The modules:
``template`` (117's and 1800's ``.a7tinfo`` as a record, byte for byte), ``levelfile`` (the ``.a7te``),
``sessionfile`` (the ``.a7t`` session and its growth), ``grow`` (a map grown on any side, with ``references``, its
report), ``worlds`` (2070's, 1404's and 1701's ``.www`` and ``.tww``), ``pool`` (the island pool, size classes,
``MapTemplate`` and ``Slot`` assets, island grids, pictures and slot objects), ``rules`` (the checks), ``placing``
(quarter turns, complexes, mirrored copies), ``variants`` (easy, medium, hard), ``mapmod`` (the files of a mod),
``projectpart`` (the share of a project); the window's ``mapview`` (the map from above), ``mappanel`` (the
selection's and the map's fields) and ``mapdialogs``. Only the window modules import Qt. docs/map-editor.md.
"""
