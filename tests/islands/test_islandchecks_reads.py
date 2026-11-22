"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the island
checks: each test drives the reader with the real class, or a stand-in carrying the real class's names."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from annotoolbox.formats import filedb


def test_the_island_checks_read_the_material_weights_through_the_builders_method():
    from annotoolbox.islands.islandchecks import CIslandChecks

    class CBuilder(CIslandChecks):
        material_set = "data/m.xml"
        files = SimpleNamespace(m_game="", where=lambda path: "game")

        def materials(self):
            return {3: {"file": "data/m3.xml", "maps": {}}}

        def weights(self):
            return np.array([3, 5]), np.array([1.0, 0.0])

    builder = CBuilder()
    bits = filedb.SAttrib("UsedMaterialBits", (1 << 3).to_bytes(4, "little"))
    builder.rd3d = filedb.STag("", [filedb.STag("Terrain", [bits])])
    report = builder.check_materials()
    assert any(line.startswith("OK material maps use [3]") for line in report), report
