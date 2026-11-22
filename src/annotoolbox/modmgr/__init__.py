"""The mod manager: the installed mods of Anno 117 and 1800, their profiles, the order the mod loader gives them, what
is wrong before the game starts and what the last start said. Start at ``manager.CModManager``; the window is
``window.CModManagerWindow`` (the suite's Mods app). The user's guide is docs/mod-manager.md.

Qt-free parts, each one job (the profile file text, ``modloader.profilefile``, and the log reader,
``modloader.loadlog``, are the loader's own and shared with the lint and the scripts):

- ``profiles``: the profiles folder: list, create, copy, rename, delete, the active one
- ``catalog``: the folders the loader searches and every mod found there, as cards with their ``modinfo``
- ``checks``: the load order and its problems, the mod lint, the operations that match nothing
- ``install``: a mod folder or zip put into the mods folder, a mod taken out (to the recycle bin)
- ``gameguard``: whether the game runs, what a profile change then means, the reload in the running game
- ``archivegames``: what the editor installed into 2205/2070/1404/1701 (restore) and loose-file mods (``PreferLocalFiles``)
- ``details``: a mod's card as text; ``manager``: all of it behind one object the window talks to

Qt parts: ``window``, ``installspage`` (the Installs tab), ``modtree``, ``orderview``.
"""
