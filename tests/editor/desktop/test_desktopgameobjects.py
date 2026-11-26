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


def test_a_batch_keeps_its_parts_between_frames_until_one_is_let_go(monkeypatch):
    """A batch whose parts are all on the device is looked up once and kept; a part let go or replaced, new view
    parts, or a part still compiling make the frame look its parts up again."""
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgameobjects
    from annotoolbox.editor.desktop.desktopgameobjects import CGameObjects

    part = object()
    monkeypatch.setattr(desktopgameobjects, "_on_device", lambda candidate: candidate is part)
    objects = object.__new__(CGameObjects)
    objects.m_keys, objects.m_users, objects.m_fallback, objects.m_waiting = {}, {}, set(), 0
    objects.m_parts_changed, objects._m_batch_parts = 0, {}
    asked = []
    found = {"a.rdm": part, "b.rdm": part}
    objects._key = lambda view_part: view_part["mesh"]
    objects._part = lambda view_part, source: asked.append(view_part["mesh"]) or found[view_part["mesh"]]
    view_a, view_b = {"mesh": "a.rdm"}, {"mesh": "b.rdm"}
    batch = SimpleNamespace(m_key=("model", "house"), m_vaos=[(view_a, {}), (view_b, {})])

    def frame() -> tuple:
        return objects._batch_parts(batch, None, False, set())

    first = frame()
    assert [view for view, _part in first[0]] == [view_a, view_b] and asked == ["a.rdm", "b.rdm"]
    assert frame() is first and len(asked) == 2  # kept
    objects._parts_changed()  # a part let go
    frame()
    assert len(asked) == 4
    batch.m_vaos = [(view_a, {})]  # the batch made again
    assert [view for view, _part in frame()[0]] == [view_a] and len(asked) == 5
    found["a.rdm"], batch.m_vaos = None, [(view_a, {})]  # made again with a part still compiling: asked every frame
    frame(), frame()
    assert len(asked) == 7 and objects.m_waiting == 2
