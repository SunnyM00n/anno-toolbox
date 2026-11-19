"""The decals of the island's objects laid on the drawn terrain: each placed object's DECALs (``ModelLibrary.decals``)
as grids that follow the terrain under them (``render.decalmesh.conform``), one mesh per pass and material, made
again when the objects or the ground change, drawn blended in the pass their data picks (``decalmesh.pass_of``:
before the streets, after them, the vehicle tracks after the models). island-editor-view "Decals on the terrain".
"""

from __future__ import annotations

import time

import numpy as np

from ...render import decalmesh
from ...render.camera import gl

DECAL_KIND = 3  # the model shader's decal: blended, in the sun's shadow (shaders.MODEL_FS)
DECAL_BUDGET = 0.025  # seconds of a frame the laying of the decals may take; it goes on in the next frames


class DecalLayer:
    """The decal meshes of the scene on the GPU (module docstring); ``update`` makes them, ``draw`` draws a pass."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.key: object = None
        self.groups: list[tuple[int, dict, object, object, object]] = []  # (pass, material, vao, vbo, ibo)
        self.inst = ctx.buffer(np.identity(4, "f4").tobytes())  # one instance: the grids are in the view's space
        self._work: tuple | None = None  # (key, the laying as a generator, what it found) while it goes on

    def update(self, key, placed, height_at, step: float, program) -> bool:
        """Make the meshes anew when ``key`` changed: ``placed`` yields (instance matrices [n, 4, 4], the config's
        decals) of every shown model, ``height_at(x, z)`` the drawn ground. The laying takes ``DECAL_BUDGET`` of a
        frame at most (a continental island's took 12 seconds at once), the old meshes stay until the new are made;
        True while it still goes on (the next frame calls again)."""
        if key == self.key:
            return False
        if self._work is None or self._work[0] != key:
            found: dict[tuple, list] = {}
            self._work = (key, self._lay(placed, height_at, step, found), found)
        start = time.perf_counter()
        for _ in self._work[1]:
            if time.perf_counter() - start > DECAL_BUDGET:
                return True
        found = self._work[2]
        self._work = None
        self.release_meshes()
        self.key = key
        self._upload(found, program)
        return False

    @staticmethod
    def _lay(placed, height_at, step: float, found: dict):
        """The decals' grids into ``found`` by their look, handing control back after each."""
        for mats, decals in placed:
            for d in decals:
                look = (decalmesh.pass_of(d["kind"], d["on_streets"]), d["diffuse"], d["normal"],
                        tuple(d["color"]), float(d["opacity"]))  # fmt: skip
                for m in mats:
                    if np.abs(m[:3, :3]).sum() > 0:  # a scale of 0: a hidden object
                        found.setdefault(look, []).append(
                            decalmesh.conform(d["extents"], m @ d["matrix"], height_at, step)
                        )
                        yield

    def _upload(self, found: dict, program) -> None:
        for (kind, diffuse, normal, color, opacity), meshes in found.items():
            verts, idx, base = [], [], 0
            for v, i in meshes:
                verts.append(v)
                idx.append(i + base)
                base += len(v)
            vbo = self.ctx.buffer(np.concatenate(verts).tobytes())
            ibo = self.ctx.buffer(np.concatenate(idx).astype("u4").tobytes())
            vao = self.ctx.vertex_array(program, [(vbo, "3f 3f 2f", "pos", "normal", "uv"), (self.inst, "16f/i", "inst")],
                                        index_buffer=ibo, index_element_size=4, skip_errors=True)  # fmt: skip
            material = {"diffuse": diffuse, "normal": normal, "color": color, "opacity": opacity}
            self.groups.append((kind, material, vao, vbo, ibo))

    def draw(self, renderer, f, kind: int) -> None:
        """The pass ``kind`` blended over what is drawn, without writing depth (``renderer``: the view's
        ``Renderer``, whose light, textures and model program it uses)."""
        groups = [g for g in self.groups if g[0] == kind]
        if not groups:
            return
        ctx, prog = self.ctx, renderer.prog["model"]
        target = renderer.targets["msaa"]
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        target.depth_mask = False
        prog["viewProj"].write(gl(f.view_proj))
        if "view" in prog:
            prog["view"].write(gl(f.view))
        prog["part"].write(gl(np.identity(4)))
        renderer._set_light(prog, f.lt, f.eye, f.shadow)
        for _kind, m, vao, _vbo, _ibo in groups:
            tex = renderer._gpu_texture("d:" + m["diffuse"], True) if m["diffuse"] else None
            (tex or renderer._dummy).use(0)
            prog["diffuseTex"].value = 0
            prog["textured"].value = tex is not None
            if "hasNormal" in prog:
                prog["hasNormal"].value = False
            prog["tint"].value = tuple(float(min(c, 1.5)) for c in (m["color"] if tex is not None else (0.6, 0.6, 0.6)))
            prog["emissive"].value, prog["kind"].value, prog["opacity"].value = 0.0, DECAL_KIND, m["opacity"]
            if "flip" in prog:
                prog["flip"].value = (0, 0, 0, 0)
            if "uvOffset" in prog:
                prog["uvOffset"].value = (0.0, 0.0)
            renderer.stats["calls"] = renderer.stats.get("calls", 0) + 1
            vao.render()
        target.depth_mask = True
        ctx.disable(ctx.BLEND)

    def release_meshes(self) -> None:
        self._work = None
        for _kind, _m, vao, vbo, ibo in self.groups:
            for r in (vao, vbo, ibo):
                r.release()
        self.groups, self.key = [], None

    def release(self) -> None:
        self.release_meshes()
        self.inst.release()
