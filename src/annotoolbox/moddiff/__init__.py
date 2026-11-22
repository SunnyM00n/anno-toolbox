"""The Mod diff app: what a mod changes in the game, how two versions of a mod differ, where mods collide, what an
archive mod replaces. Start at ``jobs.run_job`` (a question in, a ``model.SReport`` out); the window is
``window.CModDiffWindow`` (the suite's Mod diff app). The guide and the design are ``docs/mod-diff.md``.

Qt-free parts, each one job:

- ``model``: the records (``Target``, ``Change``, ``SFileChange``, ``Conflict``, ``Report``)
- ``elementdata``: an XML element as data, the value changes between two (``sources.valuediff``)
- ``base``: the opened game as the loader's session reads it
- ``effect``: the loader's run, one mod at a time, every mod's step and the whole against the game
- ``filechanges``: the files a mod carries whole
- ``writes``: the single values an operation sets, even to the value that was there
- ``conflicts``: where the steps of several mods meet
- ``archivediff``: an archive or a folder of loose files against the game's files
- ``compare``: the six questions as reports (a mod, a profile, two versions, two mods, an archive, two archives)
- ``jobs``: a question as a record, found in the mods folders and run
- ``export``: a report as a Markdown summary, Markdown in full or CSV, written line by line

Qt parts: ``window`` (the app), ``difftree`` (the tree of changes, a model made on demand), ``tables`` (conflicts and dropped operations).
"""
