"""The particle editor, the suite's Particles app (docs/particle-editor.md): any game's particle effect opened from a
file or a model, its emitters as a tree of forms, curves as graphs, colours as pickers, a live preview in the viewer's
3D view, undo, saved into the project as a mod.

Start at ``particlewindow.CParticleWindow``. The document is ``effectdoc`` (an ``.rdp``, a 2070 or 1404 ``.efc``, a
1701 ``.efc``, byte for byte; the model config it was opened from), the tree ``outline`` with the fields' kinds and
meanings ``fieldinfo``, the curves ``curves``, the tick tools of an ``.rdp`` ``rdptools``, a new emitter ``emitters``, the ways to save ``saving``
and the project's share ``projectpart``. The Qt pieces: ``fieldform`` (the form), ``curveview`` (graphs and colour
bands), ``playbar`` (play, pause, scrub, loop), ``savedialog``.
"""
