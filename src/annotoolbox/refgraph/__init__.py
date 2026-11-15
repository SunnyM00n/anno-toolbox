"""Which files of a game nothing references, and what references a file: the reference graph of an installed game.

``graph`` is the entry (``build(tree, cache)`` -> ``RefGraph``: ``referenced_by``, ``references``, ``users``,
``verdicts``, ``unreferenced_csv``), ``scan`` reads the names one file writes (and a sound bank's media by id),
``wwise`` follows a sound event or bank named by its Wwise id into the banks and ``rules`` turns "no file names it"
into a verdict (entry points, the whole chain from them, parts of a live file, generated files). "Unreferenced" means
no reference was found in the data: the program may still load the file. docs/references.md.
"""
