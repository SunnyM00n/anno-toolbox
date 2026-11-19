"""What the own-shader frames record on the QRhi device besides their draws: one render pass of the game's draws into a
framebuffer of ours (``draw_pass``), and the composite of a frame's offscreen picture into the view's target
(``Composite``), which writes the frame's depth so that what the view draws itself meets the models.
``draw_pass(ctx, fbo, prepare, draw)`` records a pass, ``draw_models`` a pass of models each prepared on its own
(``prepared``: new parts are made within ``MAKE_BUDGET`` of a frame; one that raises is left out and kept in
``failed_models``, so that its owner draws it by our look from the next frame); ``Composite(ctx, fragment)`` is one
composite program. Used by ``frame2070``, ``frameforward``, ``gameframe``, ``gameshadow``, ``shadowforward``;
docs/viewer.md "When a pass fails".
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtGui import QColor, QRhiCommandBuffer, QRhiDepthStencilClearValue

from .shaders import FINAL_VS

MAKE_BUDGET = 0.025  # seconds of one frame the first draws of new parts may take (``prepared``)

# (the target's render pass descriptor, (colour targets, samples), the batch the pass begins with)
Prepare = Callable[[object, tuple[int, int], object], None]


def draw_pass(ctx, fbo, prepare: Prepare, draw: Callable[[object], None], *, targets: int = 1, depth: float = 1.0,
              clear: bool = True, color: tuple = (0.0, 0.0, 0.0, 0.0),
              external: Callable[[object], None] | None = None) -> None:  # fmt: skip
    """One render pass into ``fbo`` as a native pass of ``ctx``: ``prepare`` makes the draws' resources and writes
    their constants before it begins, ``draw(cb)`` records them; the colour cleared to ``color`` (rgba 0..1,
    transparent black) and the depth to ``depth`` (``clear`` False: both kept). ``external(cb)``: draws of the native
    backend recorded first, on QRhi's list (``beginExternal``; the terrain: ``terrainnative``)."""
    rt, rp, signature = ctx.render_target(fbo, clear=clear)
    flags = QRhiCommandBuffer.BeginPassFlag.ExternalContent if external is not None else None

    def run(cb, batch) -> None:
        prepare(rp, (targets, signature[2]), batch)
        clears = (QColor.fromRgbF(*color), QRhiDepthStencilClearValue(depth, 0), batch)
        cb.beginPass(rt, *clears, flags) if flags is not None else cb.beginPass(rt, *clears)
        if external is not None:
            cb.beginExternal()
            try:
                external(cb)
            finally:
                cb.endExternal()
        draw(cb)
        cb.endPass()

    ctx.native_pass(run)


def prepared(ctx, models: list, prepare: Callable[[object], None]) -> list:
    """The models whose ``prepare(model)`` went through; one that raised is left out and goes, with its error, into
    ``ctx.failed_models`` (an exception inside the frame's end would otherwise end the whole frame)."""
    out = []
    for m in models:
        fresh = getattr(m, "fresh", None)
        new = fresh is not None and fresh()
        if new and ctx.make_spent > MAKE_BUDGET:
            ctx.make_left += 1  # a continental island's parts took seconds in one frame: it is drawn from a later one
            continue
        started = time.perf_counter()
        try:
            prepare(m)
        except Exception as exc:
            ctx.failed_models.append((m, f"{type(exc).__name__}: {exc}"))
        else:
            out.append(m)
        if new:
            ctx.make_spent += time.perf_counter() - started
    return out


def draw_models(ctx, fbo, models: list, prepare: Callable[[object, object, tuple, object], None],
                draw: Callable[[object, object], None], *, before: Callable[[object], None] | None = None,
                **pass_args) -> None:  # fmt: skip
    """``draw_pass`` of ``models``: ``before(batch)``, then ``prepare(model, rp, signature, batch)`` of each, then
    ``draw(model, cb)`` of those that prepared (``prepared``); ``pass_args`` go to ``draw_pass``."""
    ok: list = []

    def prepare_all(rp, signature, batch) -> None:
        if before is not None:
            before(batch)
        ok[:] = prepared(ctx, models, lambda m: prepare(m, rp, signature, batch))

    def record(cb) -> None:
        for m in ok:
            draw(m, cb)

    draw_pass(ctx, fbo, prepare_all, record, **pass_args)


class Composite:
    """A full-screen triangle of ``fragment`` (over ``shaders.FINAL_VS``) drawn into the view's target."""

    def __init__(self, ctx, fragment: str) -> None:
        self.ctx = ctx
        self.program = ctx.program(vertex_shader=FINAL_VS, fragment_shader=fragment)
        self.full = ctx.vertex_array(self.program, [])

    def draw(self, target, textures: dict, uniforms: dict, *, depth_test: bool = True, blend=None,
             depth_func: str | None = None) -> None:  # fmt: skip
        """``textures`` (uniform name -> texture, one unit each) and ``uniforms`` (bytes are written as they are) into
        ``target``; ``blend``: (source, destination) factors, None for none. The view's wireframe, depth function and
        blend are put back after it: a composite drawn as lines would show the triangle's edges, not the frame."""
        ctx, prog = self.ctx, self.program
        target.use()
        for unit, (name, tex) in enumerate(textures.items()):
            tex.use(unit)
            prog[name].value = unit
        for name, value in uniforms.items():
            if isinstance(value, bytes):
                prog[name].write(value)
            else:
                prog[name].value = value
        wireframe, func = ctx.wireframe, ctx.depth_func
        ctx.wireframe = False
        if depth_func is not None:
            ctx.depth_func = depth_func
        (ctx.enable if depth_test else ctx.disable)(ctx.DEPTH_TEST)
        if blend is not None:
            ctx.enable(ctx.BLEND)
            ctx.blend_func = blend
        self.full.render(vertices=3)
        if blend is not None:
            ctx.disable(ctx.BLEND)
        ctx.wireframe, ctx.depth_func = wireframe, func

    def release(self) -> None:
        self.program.release()
