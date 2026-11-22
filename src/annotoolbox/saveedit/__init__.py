"""The Saves app: every game's savegames found, read as a summary, a tree and forms, the values whose layout is fully
known changed and written as a new file next to the original, never over it. Start at ``container.open_file`` (a save
in, its parts out) and ``summary.summarize``; the window is ``savewindow.CSavesWindow``. The guide and the formats are
``docs/save-editor.md``.

Qt-free parts, each one job:

- ``locations``: where each game keeps its saves, the saves found there
- ``container``: any save's archive, its parts unpacked and their documents recognised
- ``records``: the summary's records and the small value readings
- ``summary``, ``summary117``, ``summary2070``, ``summaryold`` (1404, 1701), ``summaryfiledb`` (1800, 2205): what a save
  holds, per game
- ``names``: the names of the GUIDs, options and text ids a summary shows, from the installed game
- ``edits``: the editable values and the write beside the original
- ``needed``: the mods a save needs against the installed ones
- ``nodes``: a save as a tree made a level at a time, a value's readings

Qt parts: ``savewindow`` (the app), ``savetree`` (the Tree tab's model).
"""
