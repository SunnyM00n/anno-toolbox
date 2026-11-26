"""The view's model library (``render.modellibrary``): what it keeps of the models' readiness."""

from __future__ import annotations

import threading

from annotoolbox.render.modellibrary import CModelLibrary


def _library() -> CModelLibrary:
    """A library with one resolved config whose one part's mesh has loaded, without a pool or an editor."""
    library = object.__new__(CModelLibrary)
    library._m_lock, library._m_ready, library._m_full = threading.Lock(), set(), None
    library._m_parts, library._m_meshes = {"a.cfg": [{"mesh_key": "m"}]}, {"m": [("vertices", "indices")]}
    library._m_boxes, library._m_users, library._m_decal_maps, library._m_textures = {}, {}, set(), {}
    library.m_changed = threading.Event()
    return library


def test_a_ready_model_stays_ready_until_what_loads_in_full_changes():
    """``ready`` was worked out for every batch at each look of the status bar: 0.3 s of a continental island's window
    each time (performance.md 6, item 2). Once ready, a model stays so until ``set_full`` changes what loads in full."""
    library = _library()
    assert library.ready("a.cfg") and not library.ready("b.cfg")
    library._m_parts["a.cfg"] = None  # what ``ready`` reads is not asked again
    assert library.ready("a.cfg")
    library._m_parts["a.cfg"] = [{"mesh_key": "m"}]
    library.set_full(set())  # the model now loads its boxes alone: it waits for them
    assert not library.ready("a.cfg")
    library._m_boxes["m"] = ["box"]
    assert library.ready("a.cfg")
