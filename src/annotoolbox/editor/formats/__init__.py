"""The island editor's game-specific code, one package per island format family; the shared editor code is ``..``.

``a7m/`` is the new engine's FileDB islands and maps: Anno 117 and Anno 1800 (``.a7m``) and Anno 2205 (``.a6m``).
``isd/`` is the ``.isd`` island of Anno 2070 and Anno 1404. ``isc/`` is Anno 1701, whose ``.isd`` keeps its rivers,
coast lines and properties in an ``.isc`` header beside it. The file readers themselves are in ``...formats`` and
``...othergames``; these packages hold what the editor reads from an island and writes back into it.
"""
