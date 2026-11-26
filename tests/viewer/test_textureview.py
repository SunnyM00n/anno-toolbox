"""The texture view decodes off the window's thread: a small level first, and only the newest request lands."""

from __future__ import annotations

import threading

import numpy as np
from tests.ddsfiles import make_bc7_dds
from tests.viewer.viewerfakes import settle_texture_view, wait_for_pixels

from annotoolbox.models.ddsdecode import CDdsFile
from annotoolbox.viewer import textureview


def _gated(monkeypatch, gates: dict[int, threading.Event], calls: list[tuple[int, int]]) -> None:
    """``level_pixels`` as a stand-in: a flat picture of the file's width, waiting on the gate of that width."""

    def fake(request, mip: int) -> np.ndarray:
        width, height = request.dds.size_of(mip)
        calls.append((request.dds.m_info.width_px, mip))
        gate = gates.get(request.dds.m_info.width_px * 100 + mip)
        if gate is not None:
            assert gate.wait(10)
        return np.full((height, width, 4), request.dds.m_info.width_px % 251, np.uint8)

    monkeypatch.setattr(textureview, "level_pixels", fake)


def test_a_newer_texture_is_never_replaced_by_an_older_decode_that_ends_later(qt, monkeypatch):
    slow = threading.Event()
    calls: list[tuple[int, int]] = []
    _gated(monkeypatch, {400: slow}, calls)  # the 4-pixel file's level 0 waits
    view = textureview.CTextureView()
    view.show_texture("old.dds", make_bc7_dds(4, 4))
    view.show_texture("new.dds", make_bc7_dds(8, 8))
    wait_for_pixels(view)
    assert view.m_pixels.shape == (8, 8, 4)
    slow.set()  # the older decode ends now
    view.m_background.join()
    settle_texture_view(view)
    assert view.m_pixels.shape == (8, 8, 4) and view.m_pixels[0, 0, 0] == 8


def test_a_changed_level_supersedes_the_decode_of_the_level_before(qt, monkeypatch):
    slow = threading.Event()
    _gated(monkeypatch, {400: slow}, [])
    view = textureview.CTextureView()
    view.show_texture("a.dds", make_bc7_dds(4, 4, mip_count=3))
    view.m_mip.setValue(1)  # a second request for the same file
    wait_for_pixels(view)
    assert view.m_pixels.shape == (2, 2, 4)
    slow.set()
    view.m_background.join()
    settle_texture_view(view)
    assert view.m_pixels.shape == (2, 2, 4)


def test_a_large_texture_shows_a_small_level_first_and_the_full_one_after(qt, monkeypatch):
    full = threading.Event()
    calls: list[tuple[int, int]] = []
    _gated(monkeypatch, {102400: full}, calls)  # level 0 of the 1024-pixel file waits
    data = make_bc7_dds(1024, 1024, mip_count=11)
    assert textureview.preview_level(CDdsFile(data), 0) == 1  # 512 pixels
    view = textureview.CTextureView()
    view.show_texture("big.dds", data)
    wait_for_pixels(view)
    assert view.m_pixels.shape[:2] == (512, 512)  # the small level is in, the full one still waits
    full.set()
    settle_texture_view(view)
    assert view.m_pixels.shape[:2] == (1024, 1024) and calls == [(1024, 1), (1024, 0)]


def test_a_level_that_is_small_already_has_no_preview():
    dds = CDdsFile(make_bc7_dds(1024, 1024, mip_count=11))
    assert textureview.preview_level(dds, 1) is None and textureview.preview_level(dds, 10) is None
    assert textureview.preview_level(CDdsFile(make_bc7_dds(1024, 1024)), 0) is None  # no smaller level in the file


def test_the_checkerboard_is_the_same_everywhere_and_an_opaque_picture_skips_it():
    pixels = np.zeros((40, 50, 4), np.uint8)
    pixels[..., 3] = 0
    board = textureview.channel_image(pixels, "RGBA")[..., 0]
    y, x = np.mgrid[0:40, 0:50]
    assert (board == np.where(((x // 8) + (y // 8)) % 2 == 0, 200, 150)).all()
    pixels[..., :3], pixels[..., 3] = 77, 255
    assert (textureview.channel_image(pixels, "RGBA") == 77).all()
