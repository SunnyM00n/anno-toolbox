"""The decals of the island's objects laid on the drawn terrain: each placed object's DECALs (``CModelLibrary.decals``)
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


class CDecalLayer:
    """The decal meshes of the scene on the GPU (module docstring); ``update`` makes them, ``draw`` draws a pass."""

    def __init__(self, ctx) -> None:
        self.m_ctx = ctx
        self.m_key: object = None
        self.m_groups: list[tuple[int, dict, object, object, object]] = []  # (pass, material, vao, vbo, ibo)
        self.m_inst = ctx.buffer(np.identity(4, "f4").tobytes())  # one instance: the grids are in the view's space
        self._m_work: tuple | None = None  # (key, the laying as a generator, what it found) while it goes on

    def update(self, key, placed, height_at, step_tiles: float, program) -> bool:
        """Make the meshes anew when ``key`` changed: ``placed`` yields (instance matrices [n, 4, 4], the config's
        decals) of every shown model, ``height_at(x, z)`` the drawn ground. The laying takes ``DECAL_BUDGET`` of a
        frame at most (a continental island's took 12 seconds at once), the old meshes stay until the new are made;
        True while it still goes on (the next frame calls again)."""
        if key == self.m_key:
            return False
        if self._m_work is None or self._m_work[0] != key:
            found: dict[tuple, list] = {}
            self._m_work = (key, self._lay(placed, height_at, step_tiles, found), found)
        start = time.perf_counter()
        for _ in self._m_work[1]:
            if time.perf_counter() - start > DECAL_BUDGET:
                return True
        found = self._m_work[2]
        self._m_work = None
        self.release_meshes()
        self.m_key = key
        self._upload(found, program)
        return False

    def is_laying(self) -> bool:
        """Whether the decals are still being laid (``update`` goes on in the next frames)."""
        return self._m_work is not None

    @staticmethod
    def _lay(placed, height_at, step_tiles: float, found: dict):
        """The decals' grids into ``found`` by their look, handing control back after each."""
        for matrices, decals in placed:
            for decal in decals:
                look = (decalmesh.pass_of(decal["kind"], decal["on_streets"]), decal["diffuse"], decal["normal"],
                        tuple(decal["color"]), float(decal["opacity"]))  # fmt: skip
                for matrix in matrices:
                    if np.abs(matrix[:3, :3]).sum() > 0:  # a scale of 0: a hidden object
                        vertices, indices = decalmesh.conform(
                            decal["extents"], matrix @ decal["matrix"], height_at, step_tiles
                        )
                        if "retexture" in decal:  # a street cell: its place on the sheet, not the flat rectangle's
                            vertices[:, 6:8] = decal["retexture"](vertices[:, 6:8])
                        found.setdefault(look, []).append((vertices, indices))
                        yield

    def _upload(self, found: dict, program) -> None:
        for (kind, diffuse, normal, color, opacity), meshes in found.items():
            vertices, indices, base = [], [], 0
            for mesh_vertices, i in meshes:
                vertices.append(mesh_vertices)
                indices.append(i + base)
                base += len(mesh_vertices)
            vbo = self.m_ctx.buffer(np.concatenate(vertices).tobytes())
            ibo = self.m_ctx.buffer(np.concatenate(indices).astype("u4").tobytes())
            vao = self.m_ctx.vertex_array(program, [(vbo, "3f 3f 2f", "pos", "normal", "uv"), (self.m_inst, "16f/i", "inst")],
                                        index_buffer=ibo, index_element_size=4, skip_errors=True)  # fmt: skip
            material = {"diffuse": diffuse, "normal": normal, "color": color, "opacity": opacity}
            self.m_groups.append((kind, material, vao, vbo, ibo))

    def draw(self, renderer, frame, *kinds: int) -> None:
        """The passes ``kinds`` blended over what is drawn, in the order of the passes, without writing depth
        (``renderer``: the view's ``Renderer``, whose light, textures and model program it uses)."""
        groups = sorted((group for group in self.m_groups if group[0] in kinds), key=lambda group: group[0])
        if not groups:
            return
        ctx, program = self.m_ctx, renderer.m_prog["model"]
        target = renderer.m_targets["msaa"]
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        target.depth_mask = False
        program["viewProj"].write(gl(frame.view_proj))
        if "view" in program:
            program["view"].write(gl(frame.view))
        program["part"].write(gl(np.identity(4)))
        renderer._set_light(program, frame.lt, frame.eye, frame.shadow)
        for _kind, material, vao, _vbo, _ibo in groups:
            texture = renderer._gpu_texture("d:" + material["diffuse"], True) if material["diffuse"] else None
            (texture or renderer._m_dummy).use(0)
            program["diffuseTex"].value = 0
            program["textured"].value = texture is not None
            if "hasNormal" in program:
                program["hasNormal"].value = False
            program["tint"].value = tuple(
                float(min(channel, 1.5)) for channel in (material["color"] if texture is not None else (0.6, 0.6, 0.6))
            )
            program["emissive"].value, program["kind"].value, program["opacity"].value = (
                0.0,
                DECAL_KIND,
                material["opacity"],
            )
            if "flip" in program:
                program["flip"].value = (0, 0, 0, 0)
            if "uvOffset" in program:
                program["uvOffset"].value = (0.0, 0.0)
            renderer.m_stats["calls"] = renderer.m_stats.get("calls", 0) + 1
            vao.render()
        target.depth_mask = True
        ctx.disable(ctx.BLEND)

    def release_meshes(self) -> None:
        self._m_work = None
        for _kind, _material, vao, vbo, ibo in self.m_groups:
            for resource in (vao, vbo, ibo):
                resource.release()
        self.m_groups, self.m_key = [], None

    def release(self) -> None:
        self.release_meshes()
        self.m_inst.release()
