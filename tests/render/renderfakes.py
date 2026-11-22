"""Stand-ins for the renderer: shader variables and buffers, the native module and a child process that draws."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

import pytest

from annotoolbox.models import shadernative
from annotoolbox.paths import REPO_ROOT
from annotoolbox.render import nativegpu, shaderbake


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


def run_child(code: str, timeout: int = 300) -> dict:
    paths = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, timeout=timeout
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
