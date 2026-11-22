"""The old interface of Anno 2070, 1404 and 1701: dialogs, templates and backgrounds read, placed, drawn and inspected.

Start at ``view.COldGuiPage`` (a page of the UI editor's window); the parts, each one job:

- ``gui2070``: the readers of the old interface's files (dialogs, templates, backgrounds, sequences, support files)
- ``files``: the game's old interface files (dialogs, templates, backgrounds, texts, icon atlases) parsed on demand
- ``scene``: a dialog placed as rectangles with its effective properties, texts and backgrounds; a background cut into tiles
- ``view``: the page (Qt): the file list, the dialog drawn with its pictures, the inspector

Read only: the game loads compiled pools that this package does not write (docs/ui/old-gui.md, "Writing").
"""
