"""The object builder, the suite's Objects app: a model config of any game edited as the studio's object builder
edited it - its parts as a tree, each part's fields, the viewer's 3D view following every edit, the sequences on a
timeline, the info file's boxes and areas and the feedback's dummies over the view - and saved into the project as a
new file, a replacement or a ModOps patch. Plan: docs/tool-suite.md section 23; user's guide docs/object-builder.md.

``objwindow`` is the window (``ObjectWindow``, ``run``); ``objdoc`` the open object (its files as
``models.cfgdoc`` documents, one undo stack, the scene drawn with the edits laid over the game's files); ``objtree``
the tree, ``objprops`` the property editor, ``timeline`` the sequence editor, ``shapes`` and ``overlay`` what is
drawn over the view; ``writeback`` the three ways to save and the save dialog, ``projectpart`` the project's share;
``figuremenu`` the walking figures of the feedback file in the view, ``feedbackline`` its loops on a timeline.
"""
