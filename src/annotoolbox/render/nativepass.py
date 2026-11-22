"""What the own-shader frames record on the QRhi device besides their draws: one render pass of the game's draws into a
framebuffer of ours (``draw_pass``), and the composite of a frame's offscreen picture into the view's target
(``CComposite``), which writes the frame's depth so that what the view draws itself meets the models.
``draw_pass(ctx, fbo, prepare, draw)`` records a pass, ``draw_models`` a pass of models each prepared on its own
(``prepared``: new parts within ``MAKE_BUDGET`` of a frame, a one-shot pass's all, ``defer``; one that raises is
left out and kept in ``failed_models``, so its owner draws it by our look from the next frame; ``native`` runs),
``compute_pass`` a compute pass the native backend records; ``CComposite(ctx, fragment)`` is one composite program.
Used by the own-shader frames and ``gameshadow``; docs/viewer.md "When a pass fails".
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtGui import QColor, QRhiCommandBuffer, QRhiDepthStencilClearValue

from .shaders import FINAL_VS

MAKE_BUDGET = 0.025  # seconds of one frame the first draws of new parts may take (``prepared``)

# (the target's render pass descriptor, (colour targets, samples), the batch the pass begins with)
Prepare = Callable[[object, tuple[int, int], object], None]


def draw_pass(ctx, framebuffer, prepare: Prepare, draw: Callable[[object], None], *, targets: int = 1, depth: float = 1.0,
              clear: bool = True, color: tuple = (0.0, 0.0, 0.0, 0.0),
              external: Callable[[object], None] | None = None,
              after: Callable[[object], None] | None = None, external_content: bool = False) -> None:  # fmt: skip
    """One render pass into ``framebuffer`` as a native pass of ``ctx``: ``prepare`` makes the draws' resources and writes
    their constants before it begins, ``draw(command_buffer)`` records them; the colour cleared to ``color`` (rgba 0..1,
    transparent black) and the depth to ``depth`` (``clear`` False: both kept). ``external(command_buffer)``: draws of the native
    backend recorded first, on QRhi's list (``beginExternal``; the terrain: ``terrainnative``); ``after(command_buffer)``: those
    recorded after QRhi's (the objects, ``objectsnative``: over the cut-outs and the decals); ``external_content``:
    ``draw`` itself records native draws between QRhi's (the transparent pass, ``draw_models``' ``native``)."""
    render_target, render_pass, signature = ctx.render_target(framebuffer, is_cleared=clear)
    native = external is not None or after is not None or external_content
    flags = QRhiCommandBuffer.BeginPassFlag.ExternalContent if native else None

    def run(command_buffer, batch) -> None:
        prepare(render_pass, (targets, signature[2]), batch)
        clears = (QColor.fromRgbF(*color), QRhiDepthStencilClearValue(depth, 0), batch)
        if flags is None:
            command_buffer.beginPass(render_target, *clears)
        else:
            command_buffer.beginPass(render_target, *clears, flags)
        _external(command_buffer, external)
        draw(command_buffer)
        _external(command_buffer, after)
        command_buffer.endPass()

    ctx.native_pass(run)


def _external(command_buffer, record: Callable[[object], None] | None) -> None:
    """``record(command_buffer)`` between ``beginExternal`` and ``endExternal`` (nothing without one)."""
    if record is None:
        return
    command_buffer.beginExternal()
    try:
        record(command_buffer)
    finally:
        command_buffer.endExternal()


def prepared(ctx, models: list, prepare: Callable[[object], None], defer: bool = True) -> list:
    """The models whose ``prepare(model)`` went through; one that raised is left out and goes, with its error, into
    ``ctx.failed_models`` (an exception inside the frame's end would otherwise end the whole frame). ``defer``: a
    new model past the frame's ``MAKE_BUDGET`` waits for a later frame; a one-shot pass passes False, as nothing
    draws it again (a bake left out so stayed black: native-backend.md 16.9)."""
    out = []
    for model in models:
        fresh = getattr(model, "fresh", None)
        new = fresh is not None and fresh()
        if new and defer and ctx.m_make_spent > MAKE_BUDGET:
            ctx.m_make_left += 1  # a continental island's parts took seconds in one frame: it is drawn from a later one
            continue
        started = time.perf_counter()
        try:
            prepare(model)
        except Exception as exc:  # pattern: ok (a boundary: the frame goes on without the model, which is named)
            ctx.m_failed_models.append((model, f"{type(exc).__name__}: {exc}"))
        else:
            out.append(model)
        if new:
            ctx.m_make_spent += time.perf_counter() - started
    return out


def draw_models(ctx, framebuffer, models: list, prepare: Callable[[object, object, tuple, object], None],
                draw: Callable[[object, object], None], *, before: Callable[[object], None] | None = None,
                native: Callable[[object, list], None] | None = None, defer: bool = True,
                **pass_args) -> None:  # fmt: skip
    """``draw_pass`` of ``models``: ``before(batch)``, then ``prepare(model, render_pass, signature, batch)`` of each, then
    ``draw(model, command_buffer)`` of those that prepared (``prepared``); ``pass_args`` go to ``draw_pass``. ``native(command_buffer,
    run)``: each run of consecutive models whose ``native`` is true, recorded by the native backend in their place
    of the order (between ``beginExternal`` and ``endExternal``), never prepared here. ``defer`` False: a one-shot
    pass, none of its models left to a later frame (``prepared``)."""
    prepared_models: list = []
    own = [model for model in models if not getattr(model, "m_native", False)] if native is not None else models

    def prepare_all(render_pass, signature, batch) -> None:
        if before is not None:
            before(batch)
        prepared_models[:] = prepared(ctx, own, lambda model: prepare(model, render_pass, signature, batch), defer)

    def record(command_buffer) -> None:
        if native is None:
            for model in prepared_models:
                draw(model, command_buffer)
            return
        kept, run = {id(model) for model in prepared_models}, []
        for model in [*models, None]:  # None ends the last run
            if model is not None and getattr(model, "m_native", False):
                run.append(model)
                continue
            if run:
                _external(command_buffer, lambda external_buffer, run=run: native(external_buffer, run))
                run = []
            if model is not None and id(model) in kept:
                draw(model, command_buffer)

    draw_pass(ctx, framebuffer, prepare_all, record, external_content=native is not None, **pass_args)


def compute_pass(ctx, record: Callable[[object], None], before: Callable[[object], None] | None = None,
                 after: Callable[[], object] | None = None) -> None:  # fmt: skip
    """One compute pass of QRhi's whose work the native backend records (``record(command_buffer)`` between ``beginExternal``
    and ``endExternal``): ``before(batch)`` adds uploads to the batch it begins with, ``after()`` makes the one it
    ends with (a copy of the result)."""

    def run(command_buffer, batch) -> None:
        if before is not None:
            before(batch)
        command_buffer.beginComputePass(batch, QRhiCommandBuffer.BeginPassFlag.ExternalContent)
        _external(
            command_buffer, record
        )  # a raise: the recorder ends the pass it left open (``rhipass.CRecorder._end_begun``)
        command_buffer.endComputePass(after() if after is not None else None)

    ctx.native_pass(run)


class CComposite:
    """A full-screen triangle of ``fragment`` (over ``shaders.FINAL_VS``) drawn into the view's target."""

    def __init__(self, ctx, fragment: str) -> None:
        self.m_ctx = ctx
        self.m_program = ctx.program(vertex_shader=FINAL_VS, fragment_shader=fragment)
        self.m_full = ctx.vertex_array(self.m_program, [])

    def draw(self, target, textures: dict, uniforms: dict, *, depth_test: bool = True, blend=None,
             depth_func: str | None = None) -> None:  # fmt: skip
        """``textures`` (uniform name -> texture, one unit each) and ``uniforms`` (bytes are written as they are) into
        ``target``; ``blend``: (source, destination) factors, None for none. The view's wireframe, depth function and
        blend are put back after it: a composite drawn as lines would show the triangle's edges, not the frame."""
        ctx, program = self.m_ctx, self.m_program
        target.use()
        for unit, (name, texture) in enumerate(textures.items()):
            texture.use(unit)
            program[name].value = unit
        for name, value in uniforms.items():
            if isinstance(value, bytes):
                program[name].write(value)
            else:
                program[name].value = value
        wireframe, previous_depth_func = ctx.wireframe, ctx.depth_func
        ctx.wireframe = False
        if depth_func is not None:
            ctx.depth_func = depth_func
        (ctx.enable if depth_test else ctx.disable)(ctx.DEPTH_TEST)
        if blend is not None:
            ctx.enable(ctx.BLEND)
            ctx.blend_func = blend
        self.m_full.render(vertices=3)
        if blend is not None:
            ctx.disable(ctx.BLEND)
        ctx.wireframe, ctx.depth_func = wireframe, previous_depth_func

    def release(self) -> None:
        self.m_full.release()
        self.m_program.release()
