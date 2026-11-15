"""The mip levels of an sRGB texture built by our compute pass (``render.srgbmips``) on Direct3D 12, read back
against the CPU's chain (``models.mipchain``): the same levels within rounding."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from annotoolbox.models import mipchain

MIPS_FRAME = """
import json
import numpy as np
from PySide6.QtGui import QRhiReadbackDescription, QRhiReadbackResult
from PySide6.QtWidgets import QApplication
from annotoolbox.render import rhidevice
from annotoolbox.render.rhiview import RhiView
app = QApplication([])
rng = np.random.default_rng(7)
base = rng.integers(0, 256, (32, 64, 4), dtype=np.uint8)
reads = []
class V(RhiView):
    def draw(self, ctx, screen, size):
        if not hasattr(self, "tex"):
            self.tex = ctx.texture((64, 32), 4, base.tobytes(), internal_format=rhidevice.SRGB8_ALPHA8)
            self.tex.build_mipmaps()
            def read(cb, batch, native=self.tex.native):
                for level in (1, 3, 5):
                    desc = QRhiReadbackDescription(native)
                    desc.setLevel(level)
                    result = QRhiReadbackResult()
                    batch.readBackTexture(desc, result)
                    reads.append((level, result))
                cb.beginComputePass(batch)
                cb.endComputePass()
            ctx.native_pass(read)
        screen.use()
        screen.clear(0, 0, 0, 1)
v = V("d3d12")
for _ in range(4):
    v.picture(16, 16)
out = {str(level): np.frombuffer(bytes(r.data), np.uint8).tolist() for level, r in reads}
print(json.dumps({"failed": v.failed, "errors": v.ctx.errors, "gpu": v.ctx._srgb_mips[0] is not None, "levels": out}))
"""


def test_the_compute_pass_builds_the_levels_the_cpu_would():
    if sys.platform != "win32":
        pytest.skip("Direct3D 12 only")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(MIPS_FRAME)
    assert not got["failed"] and not got["errors"] and got["gpu"], got
    rng = np.random.default_rng(7)
    base = rng.integers(0, 256, (32, 64, 4), dtype=np.uint8)
    cpu = list(mipchain.mip_chain(base, gamma=True))
    for level, values in got["levels"].items():
        gpu = np.asarray(values, int).reshape(cpu[int(level)].shape)
        assert np.abs(gpu - cpu[int(level)].astype(int)).max() <= 2, level
