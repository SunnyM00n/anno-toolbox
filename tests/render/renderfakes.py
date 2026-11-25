"""Stand-ins for the renderer: shader variables and buffers, the native module, a child process that draws and a
far-view model's baked data file."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np
import pytest
from tests import childguard, hangguard

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.models import shadernative
from annotoolbox.paths import REPO_ROOT
from annotoolbox.render import distanceclusters, nativegpu, shaderbake


@dataclass
class FakeBinding:
    """A shader's resource binding as ``dxc.SBinding`` holds it, with the count and the space defaulted."""

    name: str
    kind: str
    register: int
    count: int = 1
    space: int = 0


@dataclass
class FakeReflection:
    """The part of ``dxc.SReflection`` the binding plan reads: bindings and constant buffers."""

    bindings: list
    cbuffers: list = field(default_factory=list)


def native_module():
    monkey = pytest.MonkeyPatch()
    monkey.delenv(nativegpu.ENV, raising=False)
    nativegpu._g_state.pop("module", None)
    module = nativegpu.load()
    monkey.undo()
    if module is None:
        pytest.skip(f"the native module: {nativegpu.reason()}")
    return module


class FakeHandover:
    """The handover without QRhi: the module's textures as they are."""

    def give(self, tex, fmt, size, layers=0, flags=None):
        return tex

    def native(self, q):
        return q

    def take_back(self, lst, textures) -> None:
        pass

    def handed_back(self) -> None:
        pass

    def let_go(self, ctx, q) -> None:
        q.release()

    def drop(self, q) -> None:
        if q is not None:
            q.release()


ROOT = REPO_ROOT


def run_child(code: str, timeout: float = 300) -> dict:
    """Run ``code`` in a child and return its last JSON line. The child ends itself when this process is gone or
    its time is up (tests/childguard.py); the time is cut to stay under the hang guard's limit, so a hung child
    fails the test with its output before the guard ends the whole run."""
    timeout = min(timeout, max(30.0, hangguard.limit() - childguard.MARGIN))
    paths = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "windows",
        "PYTHONPATH": os.pathsep.join(paths),
        **childguard.child_env(timeout + 5),
    }
    done = subprocess.run(
        [sys.executable, "-c", childguard.child_prelude() + code],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=timeout,
    )
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    if done.returncode != 0 or not lines:
        raise AssertionError(f"the child failed ({done.returncode}):\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    return json.loads(lines[-1])


def render_backends() -> list[str]:
    if sys.platform != "win32":
        return []
    pytest.importorskip("PySide6")
    if shaderbake.find_qsb() is None:
        pytest.skip("Qt's shader baker is not installed")
    found = run_child(
        "import json; from PySide6.QtWidgets import QApplication; app = QApplication([]);"
        "from annotoolbox.render import rhiview;"
        "print(json.dumps({b: rhiview.available(b) for b in ('d3d12', 'd3d11', 'opengl')}))"
    )
    return [b for b, ok in found.items() if ok]


def native_modules():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from annotoolbox.infra import dxc
    from annotoolbox.sources.anno117 import CAnno117Files

    try:
        compiler = dxc.CDxc.find(anno117=root)
    except Exception as exc:
        pytest.skip(f"the game's compiler: {exc}")
    return shadernative.CNativeShaders(CAnno117Files(root), compiler)


FAR_TILE = bytes(range(256)) * 5 + bytes(80)  # 1360 bytes: a BC3 tile of 85 blocks
FAR_GLOSS = bytes(distanceclusters.TILE_BLOCKS * distanceclusters.BC1_BYTES)


def far_view_bfg(flags: int = distanceclusters.HAS_MODELS, materials: list | None = None, vertex_material: int = 0,
                 tile: bytes = FAR_TILE, extra_index: int = 0) -> bytes:  # fmt: skip
    """A ``.bfg`` of one triangle's far-view model: positions (0, 0, 0), (1, 0, 0), (0, 2, 1), one texture set."""
    rows = np.zeros(3, distanceclusters.VERTEX)
    rows["position"] = [(0, 0, 0), (1, 0, 0), (0, 2, 1)]
    rows["uv"] = [(0, 0), (65535, 0), (0, 65535)]
    rows["material"] = vertex_material
    indices = np.array([0, 1, 2 + extra_index], "<u2")
    texture_set = STag("#1", [SAttrib("MaxUV", struct.pack("<2f", 1, 1)), SAttrib("DiffAlphaData", tile),
                              SAttrib("GlossMetalData", FAR_GLOSS)])  # fmt: skip
    if materials is None:
        materials = [STag("#1", [SAttrib("Type", bytes([1])), SAttrib("Color", struct.pack("<4f", 1, 0.5, 2, 1)),
                                 SAttrib("TexSetIdx", struct.pack("<I", 0))])]  # fmt: skip
    low = STag("LowPolyData", [SAttrib("Vertices", rows.tobytes()), SAttrib("Indices", indices.tobytes()),
                               STag("TextureSets", [texture_set]), STag("Materials", materials),
                               SAttrib("Flags", struct.pack("<I", flags))])  # fmt: skip
    return filedb.write(STag("", [STag("Config", [SAttrib("ConfigType", struct.pack("<I", 9)), low])]))
