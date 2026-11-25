"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the native
objects: each test drives the reader with the real class, or a stand-in carrying the real class's names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_the_native_instance_buffers_read_the_frames_serial():
    from annotoolbox.render.nativedraws import CInstanceBuffers

    buffers = CInstanceBuffers(device=None)
    buffers.m_serial, buffers.m_buffers = 7, {"instances": object()}
    frame = SimpleNamespace(m_serial=7, gathered=lambda: pytest.fail("the same frame is sent once"))
    buffers.send(None, frame)  # the frame's instances are on the device already: nothing to write
