"""The water's leftovers of step 6 on the native backend (render/oceannative, copiesnative, inlandnative): they
follow the whole native look's switch; with the game and the module, the ocean's shipped simulation runs on Windows'
software rasteriser into moving waves, the depth copy's shipped shader binds with its camera records and the rivers'
three compute passes and the flow map's raster draw bind as they ship."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.render.renderfakes import FakeHandover, native_modules

from annotoolbox.render import copiesnative, gameocean, gamesea, inlandnative, nativegpu, oceannative, renderstates


def test_they_follow_the_whole_native_looks_switch(monkeypatch):
    module = SimpleNamespace(record_dispatch=None)  # a loaded module that records on QRhi's list
    monkeypatch.setattr(nativegpu, "_g_state", {"module": module, "reason": ""})
    assert oceannative.is_enabled() and copiesnative.is_enabled()
    monkeypatch.setenv(oceannative.ENV, "0")
    assert not oceannative.is_enabled() and copiesnative.is_enabled()
    assert inlandnative.is_enabled()
    nativegpu.want(False)
    assert not copiesnative.is_enabled() and not inlandnative.is_enabled()


def test_the_oceans_shipped_simulation_makes_waves_on_the_software_rasteriser():
    from tests.render.renderfakes import native_module

    natives = native_modules()
    native_module()
    device = nativegpu.own_device(adapter=-2)
    water = {"simulations": {s: {"Fetch": 16000.0, "WindSpeed": 8.0, "Choppiness": 1.0, "Directionality": 0.5,
                                 "OceanDepth": 100.0} for s in gameocean.SIMULATIONS}, "seed": 1}  # fmt: skip
    ocean = oceannative.CNativeOcean(device, natives, FakeHandover(), water)
    assert set(ocean.inputs()) == {"cWaveDx", "cWaveDy", "cWaveDz", "cWaveGradients"}
    pictures = []
    for seconds in (1.0, 2.0):
        ocean.run(None, seconds)
        staging = device.staging(gameocean.SIZE, gameocean.SIZE, oceannative.HEIGHT)
        lst = device.command_list()
        lst.open()
        lst.copy_to_staging(staging, ocean.m_waves["Dy"])
        lst.execute()
        pictures.append(np.frombuffer(device.read(staging), np.float16).astype(float))
    assert np.abs(pictures[0]).max() > 0 and not np.array_equal(*pictures)  # heights, and they move
    shader = copiesnative.shaders(natives)["depth"]
    copies = copiesnative.CNativeCopies(device, natives)
    assert copies.m_element is not None and natives.reflection(shader).cbuffers  # it reads the camera records
    copies.release()
    inland = inlandnative.CNativeInland(device, natives)  # the rivers' three passes bind as they ship
    assert {i.name for spec in inland.m_kernels["gradients"].m_plan.layouts for i in spec.items} >= {
        "cWavesElevation",
        inlandnative.GRADIENTS_OUT,
    }
    program = SimpleNamespace(
        m_vertex_stage=SimpleNamespace(entry="vsClusterFlow"), m_pixel_stage=SimpleNamespace(entry="psClusterFlow")
    )
    state = replace(renderstates.SPassState(), depth_test=False, depth_write=False)
    ribbon = SimpleNamespace(m_program=program, m_switches=inland.m_flow_switches, m_state=state)
    tech = inland.m_ribbons.technique(ribbon)  # the flow map's raster: its own target, no depth, its variant's define
    assert tech.state["colors"] == inlandnative.RASTER and tech.state["depth"] == ""
    assert inland.m_ribbons.pipeline(tech, gamesea.grid(3))[0] is not None
    inland.release()
    ocean.release()
    assert device.errors == 0, device.messages()
