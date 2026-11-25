"""The game look's shadow on the CPU's side (``editor.desktop.desktopgameshadow``): the switch that culls the native
objects' casters on the GPU, and the CPU's culling again after the GPU's failed."""

from __future__ import annotations

from types import SimpleNamespace

from annotoolbox.editor.desktop.desktopgameshadow import CGameShadow
from annotoolbox.render import shadowcullnative


class FakeCull:
    def __init__(self, device, natives) -> None:
        self.m_error, self.m_released = "", False

    def release(self) -> None:
        self.m_released = True


class FakeIsland(CGameShadow):
    def __init__(self) -> None:
        self.m_native_objects = SimpleNamespace(m_error="", m_cull=None)
        self.m_said: list[str] = []
        self.m_renderer = SimpleNamespace(_message=self.m_said.append)
        self._m_device = self._m_natives = None
        self._m_cull_failed = False


def test_the_switch_culls_the_casters_on_the_gpu_and_a_failure_sends_them_back_to_the_cpu(monkeypatch):
    monkeypatch.setattr(shadowcullnative, "CShadowCull", FakeCull)
    island = FakeIsland()
    assert island._cull_on_gpu(SimpleNamespace(gpu_culling=True))
    made = island.m_native_objects.m_cull
    assert island._cull_on_gpu(SimpleNamespace(gpu_culling=True)) and island.m_native_objects.m_cull is made  # kept
    assert not island._cull_on_gpu(SimpleNamespace(gpu_culling=False)) and made.m_released
    assert island._cull_on_gpu(SimpleNamespace(gpu_culling=True))
    island.m_native_objects.m_cull.m_error = "RuntimeError: the culling did not bind"
    assert not island._cull_on_gpu(SimpleNamespace(gpu_culling=True)) and len(island.m_said) == 1  # said once
    assert not island._cull_on_gpu(SimpleNamespace(gpu_culling=True)) and len(island.m_said) == 1
    island.m_native_objects = None  # no native objects: nothing to cull on the GPU
    assert not island._cull_on_gpu(SimpleNamespace(gpu_culling=True))
