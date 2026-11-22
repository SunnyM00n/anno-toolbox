"""The text editor: a game's texts in two languages side by side, a translation made and checked, written into a project
as the game reads it (ModOps for 117 and 1800, whole text files for the others).

Start at ``textwindow.CTextWindow`` (the suite's Texts app). The parts, each one job (no Qt unless said):

- ``textfiles``: a text file read as entries with their places and written back, byte for byte when nothing changed
- ``texttable``: a game's languages and one language as a table; ``textdoc``: the open game, the edits, their status, the filters
- ``checks``: placeholders, markup, line breaks and length of a translation; ``glossary``: the game's terms and the person's
- ``exchange``: CSV, XLIFF 1.2 and PO read and written, and applied to the project; ``editfile``: the project's work on disk
- ``render``: what the edits become as files; ``projectpart``: the project's share of a mod
- ``rowsmodel``, ``detailpanel``, ``textwindow``: the window (Qt)

docs/text-editor.md is the user's guide and the format notes, docs/tool-suite.md section 27 the design.
"""
