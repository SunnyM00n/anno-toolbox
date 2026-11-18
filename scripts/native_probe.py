"""Step 0 of the native backend (docs/rendering/native-backend.md): one full-screen triangle drawn by the game's
shipped tone-map shaders (``posteffect_tonemap.fx``, the DXIL of the install's cache as it is) through the compiled
module, on both routes, with the numbers the choice is made on.

Route A records inside QRhi's frame (NVRHI on QRhi's device and queue, the draw on QRhi's command list between
``beginExternal`` and ``endExternal``, into the view's bound target, an overlay of QRhi's drawn after it). Route B
draws on a device of the module's own and copies the picture through the CPU into a QRhi texture each frame.
Usage and the output: ``--help`` and scripts/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STEM, VS, PS = "posteffect_tonemap", "vsTonemap", "psTonemap"
SIZES = ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160))
EPILOG = """\
  python scripts/native_probe.py --anno117 "<117 install>" --out out/native-probe
  python scripts/native_probe.py --anno117 "<117 install>" --frames 60 --sizes 1920x1080,3840x2160 --no-window

Writes route-a.png, route-b.png (the proof pictures) and numbers.json into --out: per frame size the median frame of
each route against a view that draws nothing native, the recording's CPU time, route B's copy through the CPU, and
the on-screen pacing with two resizes (a small window for a few seconds; --no-window skips it). Needs the module
(scripts/build_native.py), PySide6 and a Direct3D 12 device. ANNO_D3D12_DEBUG=1 turns the debug layer on for route B
where Windows' Graphics Tools are installed.
"""
COPY_FS = """
#version 330
uniform sampler2D frame;
in vec2 vNdc; out vec4 color;
void main() { color = vec4(texture(frame, vec2(vNdc.x * 0.5 + 0.5, 0.5 - vNdc.y * 0.5)).rgb, 1.0); }
"""


def shipped_tonemap(install: str):
    """The tone map's vertex and pixel shader from the shipped cache (the first variant of the file), with their
    reflections by the game's compiler."""
    from annotoolbox.infra import dxc
    from annotoolbox.models import shadercache
    from annotoolbox.sources.anno117 import Anno117Files

    files = Anno117Files(install)
    game = shadercache.GameShaders(files)
    path = sorted(game.by_file[game.info(STEM).vanilla_hash])[0]
    shaders = {s.name: s.bytecode for s in shadercache.read_cached_shaders(files.read(path))}
    compiler = dxc.Dxc.find(anno117=install)
    return path, shaders[VS], shaders[PS], compiler.reflect(shaders[VS]), compiler.reflect(shaders[PS])


class Tonemap:
    """The triangle's resources on one module device: the shipped shaders' pipeline, its binding sets, an HDR
    gradient as the picture to tone-map, the frame's constants (``gamepost.CONSTANTS``)."""

    def __init__(self, gpu, shipped, colors: list[str], depth: str) -> None:
        from annotoolbox.render import gamepost, nativegpu, nativelayout
        from annotoolbox.render import gameshader as gs

        self.gpu = gpu
        _path, vs, ps, rvs, rps = shipped
        self.plan = nativelayout.BindingPlan.of([rvs, rps])
        elements, stride = nativelayout.float_elements(nativelayout.signature(vs))
        self.layouts = nativegpu.layouts(gpu, self.plan)
        self.pipeline = gpu.graphics_pipeline(
            gpu.shader("vs", vs, VS), gpu.shader("ps", ps, PS), gpu.input_layout(elements), self.layouts,
            {"cull": "none", "depth_test": False, "depth_write": False, "colors": colors, "depth": depth},
        )  # fmt: skip
        tri = gamepost.TRIANGLE
        vertices = np.concatenate([np.asarray(tri["float3"], np.float32), np.asarray(tri["float2"], np.float32)], 1)
        assert vertices.shape[1] * 4 == stride
        self.vbuf = gpu.buffer(vertices.nbytes, "vertex", name="triangle")
        cb = next(c for c in rps.cbuffers if c.name == "cbPerFrame")
        self.constants = gs.Constants(cb)
        for key, value in gamepost.CONSTANTS.items():
            self.constants.set(key, value)
        self.constants.set("cGlobalFlags", 0)
        self.cbuf = gpu.buffer((len(self.constants.data) + 255) // 256 * 256, "constant", name="cbPerFrame")
        self.noise = gpu.texture(8, 8, "RGBA8_UNORM", name="noise")
        self.exposure = gpu.buffer(64, "structured", stride=16, name="exposure")
        self.cl = gpu.command_list()
        self.cl.open()
        self.cl.write_buffer(self.vbuf, vertices.tobytes())
        self.cl.write_texture(self.noise, bytes([128] * 8 * 8 * 4), 8 * 4)
        self.cl.write_buffer(self.exposure, np.ones(16, np.float32).tobytes())
        self.size = None
        self.sets: list = []
        self.write_constants()

    def write_constants(self, exposure: float = 0.55) -> None:
        self.constants.set("cExposure", exposure)
        self.cl.write_buffer(self.cbuf, bytes(self.constants.data))

    def resize(self, w: int, h: int) -> None:
        """The HDR picture at the frame's size (the shader fetches it by pixel) and the sets that read it."""
        from annotoolbox.render import nativegpu

        if self.size == (w, h):
            return
        x, y = np.meshgrid(np.linspace(0.0, 1.0, w, dtype=np.float32), np.linspace(0.0, 1.0, h, dtype=np.float32))
        hdr = np.stack([x * 6.0, y * 3.0, 0.4 + 2.0 * (1.0 - x) * y, np.ones_like(x)], -1).astype(np.float16)
        self.hdr = self.gpu.texture(w, h, "RGBA16_FLOAT", name="hdr")
        self.cl.write_texture(self.hdr, hdr.tobytes(), w * 8)
        named = {"cbPerFrame": self.cbuf, "cBlueNoiseTex": self.noise, "cExposureBuffer": self.exposure,
                 "cTexture0": self.hdr}  # fmt: skip
        samplers = nativegpu.samplers(self.gpu, self.plan)
        self.sets = [nativegpu.binding_set(self.gpu, lay, spec, named, samplers)
                     for lay, spec in zip(self.layouts, self.plan.layouts, strict=True)]  # fmt: skip
        self.size = (w, h)

    def submit(self) -> None:
        """What was written goes to the GPU, on the device's queue ahead of what QRhi records next."""
        self.cl.execute()


def _view_class():
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtGui import QColor, QRhiCommandBuffer, QRhiDepthStencilClearValue

    from annotoolbox.render import nativegpu, nativepass, rhiview
    from annotoolbox.render.shaders import FLAT_FS, FLAT_VS

    class ProbeView(rhiview.RhiView):
        """A view that draws route ``mode`` ("a", "b", or "" for none) and a small triangle of QRhi's over it."""

        def __init__(self, shipped, mode: str) -> None:
            super().__init__("d3d12")
            self.shipped, self.mode = shipped, mode
            self.scene = self.own = self.copy = self.frame_tex = None
            self.record_us: list[float] = []
            self.native_ms: list[float] = []
            self.picture_b: bytes | None = None
            self.intervals: list[float] = []
            self.clock = QElapsedTimer()

        def ready(self, ctx) -> None:
            prog = ctx.program(FLAT_VS, FLAT_FS)
            tri = np.array([[0.6, 0.6, 0, 1, 1, 1], [0.9, 0.6, 0, 1, 0.2, 0.2], [0.75, 0.9, 0, 0.2, 1, 0.2]], "f4")
            self.overlay = (prog, ctx.vertex_array(prog, [(ctx.buffer(tri.tobytes()), "3f 3f", "pos", "rgb")]))
            if self.mode == "a":
                gpu = nativegpu.attach(self.rhi())
                self.scene = Tonemap(gpu, self.shipped, ["RGBA8_UNORM"], "D32")
            elif self.mode == "b":
                self.own = nativegpu.own_device()
                self.copy = nativepass.Composite(ctx, COPY_FS)

        def released(self, ctx) -> None:
            if self.scene is not None:
                self.scene.gpu.wait_idle()
                self.scene = None
                nativegpu.detach(self.rhi())

        def draw(self, ctx, screen, size) -> None:
            if self.clock.isValid():
                self.intervals.append(self.clock.nsecsElapsed() / 1e6)
            self.clock.start()
            w, h = size
            if self.mode == "a":
                self._route_a(ctx, screen, w, h)
            elif self.mode == "b":
                self._route_b(ctx, screen, w, h)
            else:
                screen.use()
                screen.clear(0.1, 0.1, 0.12, 1.0)
            prog, vao = self.overlay  # QRhi's own drawing after the native pass: its state must be its own again
            screen.use()
            ctx.disable(ctx.DEPTH_TEST)
            prog["viewProj"].write(np.eye(4, dtype="f4").tobytes())
            prog["pointSize"].value, prog["alpha"].value, prog["gain"].value = 1.0, 1.0, 1.0
            vao.render()

        def _route_a(self, ctx, screen, w, h) -> None:
            scene = self.scene
            scene.resize(w, h)
            scene.write_constants(0.55 + 0.05 * np.sin(len(self.intervals) * 0.2))  # a list of ours every frame
            scene.submit()
            rt, _rp, _sig = ctx.render_target(screen, clear=True)
            module = nativegpu.load()

            def run(cb, batch) -> None:
                cb.beginPass(rt, QColor(20, 20, 30), QRhiDepthStencilClearValue(0.0, 0), batch,
                             QRhiCommandBuffer.BeginPassFlag.ExternalContent)  # fmt: skip
                cb.beginExternal()
                t0 = time.perf_counter()
                module.record_external(scene.gpu, nativegpu.command_list(cb), scene.pipeline, scene.sets, 3,
                                       (0.0, 0.0, float(w), float(h)), [scene.vbuf])  # fmt: skip
                self.record_us.append((time.perf_counter() - t0) * 1e6)
                cb.endExternal()
                cb.endPass()

            ctx.native_pass(run)
            scene.gpu.end_frame()

        def _route_b(self, ctx, screen, w, h) -> None:
            if self.scene is None or self.scene.size != (w, h):
                self.scene = Tonemap(self.own, self.shipped, ["RGBA8_UNORM"], "")
                self.scene.resize(w, h)
                self.target = self.own.texture(w, h, "RGBA8_UNORM", usage="target", name="frame")
                self.fb = self.own.framebuffer([self.target])
                self.staging = self.own.staging(w, h, "RGBA8_UNORM")
                self.frame_tex = ctx.texture((w, h), 4)
            s = self.scene
            t0 = time.perf_counter()
            s.cl.clear_color(self.target, 0.0, 0.0, 0.0, 0.0)  # a new target holds no defined pixels until cleared
            s.cl.draw(self.fb, s.pipeline, s.sets, 3, vertex_buffers=[s.vbuf])
            s.cl.copy_to_staging(self.staging, self.target)
            s.submit()
            data = self.own.read(self.staging)
            self.own.end_frame()
            self.native_ms.append((time.perf_counter() - t0) * 1e3)
            self.picture_b = data
            self.frame_tex.write(data)
            screen.use()
            screen.clear(0.0, 0.0, 0.0, 1.0)  # the view's own target: its first pass must clear it (debug layer)
            self.copy.draw(screen, {"frame": self.frame_tex}, {}, depth_test=False)

    return ProbeView


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 3) if xs else 0.0


def offscreen(shipped, sizes, frames: int, out: Path) -> dict:
    """Each route against the plain view, per frame size: the median of ``frames`` grabbed frames (the grab's
    read-back is in every one of them, so the differences are what a route adds)."""
    ProbeView = _view_class()
    results: dict = {}
    for mode in ("", "a", "b"):
        view = ProbeView(shipped, mode)
        for w, h in sizes:
            times = []
            for i in range(frames + 3):
                t0 = time.perf_counter()
                img = view.picture(w, h)
                if i >= 3:  # the first frames make the resources
                    times.append((time.perf_counter() - t0) * 1e3)
                if view.failed:
                    raise RuntimeError(f"route {mode or 'none'}: {view.failed}")
            row = results.setdefault(f"{w}x{h}", {})
            row[mode or "none"] = {"frame_ms": _median(times)}
            if mode == "a":
                row["a"]["record_us"] = _median(view.record_us[-frames:])
                if (w, h) == sizes[0]:
                    img.save(str(out / "route-a.png"))
            if mode == "b":
                row["b"]["native_render_and_read_ms"] = _median(view.native_ms[-frames:])
                if (w, h) == sizes[0]:
                    px = np.frombuffer(view.picture_b, np.uint8).reshape(h, w, 4)
                    _save_rgba(px, out / "route-b.png")
                    img.save(str(out / "route-b-in-qrhi.png"))
        messages = view.scene.gpu.messages() if view.scene is not None else []
        results.setdefault("messages", {})[mode or "none"] = messages[:20]
        view.release() if hasattr(view, "release") else None
        view.deleteLater()
    return results


def _save_rgba(px: np.ndarray, path: Path) -> None:
    from PySide6.QtGui import QImage

    QImage(px.tobytes(), px.shape[1], px.shape[0], px.shape[1] * 4, QImage.Format.Format_RGBA8888).save(str(path))


def on_screen(shipped, mode: str, seconds: float = 3.0) -> dict:
    """Route ``mode`` in a small shown window for ``seconds``, resized twice: the intervals between frames."""
    from PySide6.QtCore import QEventLoop, QTimer

    ProbeView = _view_class()
    view = ProbeView(shipped, mode)
    view.setWindowTitle("native probe")
    view.resize(640, 360)
    view.move(40, 40)
    view.show()
    timer = QTimer()
    timer.timeout.connect(view.update)
    timer.start(0)
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 333), lambda: view.resize(960, 540))
    QTimer.singleShot(int(seconds * 666), lambda: view.resize(800, 450))
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()
    timer.stop()
    view.close()
    gaps = view.intervals[3:]
    out = {"frames": len(gaps), "median_ms": _median(gaps), "p95_ms": round(float(np.percentile(gaps, 95)), 3) if gaps
           else 0.0, "max_ms": round(max(gaps), 3) if gaps else 0.0, "failed": view.failed}  # fmt: skip
    view.deleteLater()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    ap.add_argument("--anno117", default=os.environ.get("ANNO117_DIR", ""), help="the Anno 117 install")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "native-probe")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SIZES))
    ap.add_argument("--no-window", action="store_true", help="skip the on-screen pacing run")
    args = ap.parse_args(argv)
    if not args.anno117:
        ap.error("--anno117 (or ANNO117_DIR) is needed: the shaders are the install's")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.render import nativegpu

    if not nativegpu.available():
        print(f"no module: {nativegpu.reason()}")
        return 2
    app = QApplication.instance() or QApplication([])
    args.out.mkdir(parents=True, exist_ok=True)
    shipped = shipped_tonemap(args.anno117)
    sizes = [tuple(int(v) for v in s.split("x")) for s in args.sizes.split(",")]
    result = {"shader_file": shipped[0], "sizes": offscreen(shipped, sizes, args.frames, args.out)}
    if not args.no_window:
        result["on_screen"] = {mode or "none": on_screen(shipped, mode) for mode in ("", "a", "b")}
    (args.out / "numbers.json").write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1))
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
