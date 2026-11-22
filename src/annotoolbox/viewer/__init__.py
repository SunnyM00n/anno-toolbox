"""The asset viewer, the suite's Viewer app: any installed game's files (117, 1800, 2205, 2070, 1404, 1701) browsed
and shown - models in 3D with their configs' parts, materials, levels of detail, sequences and animations, textures by
channel and level, particle effects, sound banks and clips, data files. Plan and user's guide: docs/tool-suite.md
("The viewer") and docs/viewer.md.

``viewerwindow`` is the window (``CViewerWindow``, ``run``); ``browser`` the file browser, ``panels`` the parts tree,
the properties, who uses a file and the sequence timeline. The 3D view: ``modelview`` (the widget), ``modelrender``
(the renderer, no Qt, with its light in ``modellights``, its places and poses in ``modelposes``), ``modelscene``
(what a config draws: parts, effects, lights, decals), ``motion`` (spins and parts on joints), ``ownpass`` with ``gamepass``, ``fxcpass``,
``fxcpass1404``, ``fxcpass1701`` and ``forwardpass`` (the games' own shaders), ``plainshading`` (our plain look, the fallback, in
the frame's light; the skin posed on the GPU), ``modelfiles`` (files read once; meshes as arrays by ``models.rdmmesh``, posed by ``models.skinning``),
``clothplay`` (cloths simulated each frame by ``render.clothsim``), ``figures`` (a feedback file's walking figures).
The other views: ``textureview``, ``soundview`` (banks, events, music and clips: ``exporting.banks``), ``dataview``
(text, FileDB, bytes).
"""
