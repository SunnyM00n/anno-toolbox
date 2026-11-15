"""The UI editor: the Phoenix UI files of 117, 1800 and the History Editions browsed, previewed, edited and built into a mod.

Start at ``uiwindow.UiWindow`` (the suite's UI app, or ``uiwindow.run`` alone); docs/tool-suite.md section 20 is the
design, docs/ui-editor.md the user's guide. The parts, each one job:

- ``uifiles``: the game's UI files by kind and name, decoded on demand, who names a file; ``library``: the Widget
  Library's descriptions and the shipped templates' property names; ``fields``: an object's fields as typed values
- ``elements``: a template's element tree; ``operators``: the binding operators; ``bindings``: a template's bindings as a
  graph, edited with the index space renumbered
- ``animations``: a template's animations, keys eased; ``tweens``: the tween components the preview plays;
  ``viewmodels``: the UI data model from the Lua type stubs, script readouts, field names, sample values
- ``scope``: one placed file's bindings evaluated; ``placeholders`` made-up data; ``layout``: every widget placed as
  draw items; ``labels`` a label's text and size; ``listlayout`` a list's items; ``memo`` what placing keeps
- ``effects``: widget effects and soft masks; ``fonts``: the game's fonts and the text layout; ``worldscene``: the 3D
  scene behind the preview, ``worldrender`` a model or an island drawn as it, ``worldfx`` the effects that read it, ``worldmenu`` its
  View menu
- ``canvas``: the preview's drawing interface; ``pictures``: an image asset's picture; ``uidoc``: the open file, its
  edits on the undo stack, its bytes; ``projectpart``: the project's share of a mod
- ``preview``, ``graphview``, ``panels``, ``timeline`` (the sequence editor), ``pickers``, ``uiwindow``: the window (Qt);
  a game's old interface is a page of it (``guiold``)
"""
