"""The island view's objects in the game look (editor/desktop/desktopgameobjects): a native part is drawn only once
every pipeline it asked for is in, as the engine draws a model only once its pipelines are made."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_a_native_part_waits_for_its_pipelines_and_is_drawn_once_they_are_in():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop.desktopgameobjects import CGameObjects
    from annotoolbox.render.objectsnative import CNativePart

    pipeline = SimpleNamespace(ready=False, error="")
    part = object.__new__(CNativePart)
    part.m_is_ready = False
    part._pipelines = lambda: [(pipeline, "constant vertex")]
    view_part = {"mesh": "a.rdm"}
    objects = object.__new__(CGameObjects)
    objects.m_keys, objects.m_parts = {id(view_part): (view_part, "a part")}, {"a part": part}
    assert objects._part(view_part, None) is None  # still made by a worker: left out, the frame does not wait
    pipeline.ready = True
    assert objects._part(view_part, None) is part
    pipeline.ready = False  # once in, it stays in
    assert objects._part(view_part, None) is part
    failed = object.__new__(CNativePart)
    failed.m_is_ready = False
    failed._pipelines = lambda: [(SimpleNamespace(ready=False, error="not made"), None)]
    objects.m_parts["a part"] = failed
    assert objects._part(view_part, None) is failed  # its draw raises and sends it to the current path
