"""The Flash UI editor: Anno 2205's UI movies (``data/ui/*.feu``) opened, looked into, edited and built into a mod.

``uiwindow`` is the PC application (Qt, the ``desktop`` extra; ``run`` starts it), ``uicommands`` the same work on the
command line (``python -m`` on this package, or a host program's command group). ``uidoc`` is a movie open in the
editor (its element tree, properties, edits and undo), ``uiproject`` the edit file and what a build writes from it,
``uirender`` the preview of a movie's first frame, ``uiseam`` the extension points. The file formats are read and
written in ``formats/feuread``, ``feuwrite``, ``feuedit``, ``feushape``, ``feuabc``, ``feuassets``; the install and
the way a changed file reaches the game in ``othergames/ui2205``. Start at docs/ui/2205/flash-ui-editor.md and
docs/formats/anno2205-ui-format.md.
"""
