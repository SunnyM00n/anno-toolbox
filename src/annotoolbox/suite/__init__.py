"""The suite's shared core: what every app (the island editor, the asset editor, later the UI editor and the viewer)
takes from one place. Plan: docs/tool-suite.md (sections 7 and 10).

``shell`` is the one window with the app switcher (``CSuiteWindow``, ``run``), ``apps`` lists the apps. ``settings``
holds the game folders and their detection, the workspace, the theme and each app's remembered state; ``theme`` and
``icons`` the look (a dark and a light theme, icons drawn as vectors); ``undo`` the one undo stack every document
uses; ``background`` runs work off the window's thread. ``project`` is the project (one folder per mod: every
app's work, one GUID range, one build and install), ``projectpanel`` its window.
"""
