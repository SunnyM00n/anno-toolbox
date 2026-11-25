"""These tests check the distance clusters' device side (render/clustersnative) without a device. A region's draw is
made from its mesh's pool pieces, as the native objects make theirs, and the pieces go back to the objects' pool."""

from __future__ import annotations

from types import SimpleNamespace

from annotoolbox.render import clustersnative, nativegpu
from annotoolbox.render.distanceclusters import SClusterMesh


class FakeClusterDevice:
    """It stands in for the objects' device as their pool uses it and counts each ``release``."""

    frame, keep_frames = 0, 3

    def __init__(self) -> None:
        self.released = 0

    def buffer(self, size, usage, stride=0, name=""):
        return SimpleNamespace(size=size, release=self._count)

    def _count(self) -> None:
        self.released += 1


def test_a_regions_draw_binds_its_pieces_of_the_objects_pool_and_gives_them_back():
    """``CNativeClusters.item`` is ``CNativePart.item``, which binds pool pieces (a vertex buffer and its offset, the
    index buffer and its offset): a region's mesh made as a buffer of its own raised at its first draw."""
    drawn = []
    device = FakeClusterDevice()
    pool = nativegpu.CBufferPool(device, "vertex,index", "meshes")
    module = SimpleNamespace(draw_item=lambda *args, **options: drawn.append((args, options)) or "draw")
    objects = SimpleNamespace(m_pool=pool, m_list=SimpleNamespace(write_buffer=lambda *args: None), m_module=module,
                              pipeline=lambda technique, mesh: ("pipeline", "fill"),
                              static=lambda data, usage, name: device.buffer(len(data), usage))  # fmt: skip
    clusters = object.__new__(clustersnative.CNativeClusters)
    clusters.m_objects, clusters.m_techniques, clusters.m_items = objects, {"main": "technique"}, {}
    clusters._m_meshes, clusters._m_free, clusters._m_written = {}, [0], {}
    mesh = clusters.put(SClusterMesh(bytes(64), bytes(24), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 2))
    assert clusters.item("main", mesh, ["sets"]) == "draw"
    (pipeline, sets, count, vertex_buffers, index_buffer, *_rest), options = drawn[0]
    assert count == 6 and vertex_buffers[0] == (mesh.m_vertices.buffer, mesh.m_vertices.offset)
    assert index_buffer is mesh.m_index_buffer.buffer and options["index_offset"] == mesh.m_index_buffer.offset
    clusters.drop(mesh)
    assert len(pool.m_freed) == 2 and clusters._m_free == [0]
