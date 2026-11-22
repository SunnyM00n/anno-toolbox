"""The mod loader of Anno 117 (and 1800) reproduced outside the game: which mods run in which order, and their ModOps
applied to the game's files, with every operation the loader would drop reported. Stdlib only. The rules are
``docs/engine-modloader.md``; what is reproduced and what is not, ``docs/tool-suite.md`` section 17.

``engine.CSession`` is the entry: ``apply_mod`` runs one mod's patch files on the documents it holds, ``events`` says
what the loader would log or skip. ``mods`` finds the mods, reads ``modinfo.json``, the profile and the options, and
orders them, nested mods included; ``resolve`` turns a set of mods into the loader's run with dependencies checked;
``patchdoc`` is a patched file (assets kept as bytes until an operation reads them); ``content`` merges,
``ModItem`` and ``ModValue``; ``xpathparse`` and ``xpath`` read and evaluate the paths and conditions.
``activations`` is Anno 1800's way of switching mods on (``activations.json``, no profile).
``profilefile`` reads and writes a profile file without losing a line, ``loadlog`` reads ``mod-loader.log`` per mod.
``modswap`` puts a mod folder into a mods folder through a staging folder and takes one out (``place_folder``).
"""
