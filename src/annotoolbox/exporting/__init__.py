"""Export any game file as stored and in the forms other tools read (docs/exporting.md).

``run.export_path`` / ``run.export_many`` do the work and write an index (``index``); ``kinds`` sorts a path into a
kind and lists its forms. ``textures``, ``pictures``, ``sounds``, ``movies``, ``models`` and ``data`` are the
per-kind exporters, ``banks`` reads sound banks and clips (no Qt anywhere; numpy and the optional tools are
needed only by the forms that use them).
"""
