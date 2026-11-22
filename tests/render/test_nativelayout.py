"""The native backend's binding plan and shader signature (``render.nativelayout``) on synthetic containers."""

from __future__ import annotations

import struct

import pytest

from annotoolbox.render import nativelayout


def _container(part: bytes, name: bytes = b"ISG1") -> bytes:
    """A DXBC container of one part (the header's hash and size are not read)."""
    start = 32 + 4
    return (
        b"DXBC"
        + bytes(24)
        + struct.pack("<I", 1)
        + struct.pack("<I", start)
        + name
        + struct.pack("<I", len(part))
        + part
    )


def _signature(names: list[bytes]) -> bytes:
    """An input signature of float3 elements named ``names``, one semantic index 0 each."""
    names_at = 8 + 32 * len(names)
    body, strings = b"", b""
    for k, name in enumerate(names):
        body += struct.pack("<6IB", 0, names_at + len(strings), 0, 0, 3, k, 7).ljust(32, b"\0")
        strings += name + b"\0"
    return struct.pack("<II", len(names), 8) + body + strings


def test_a_signature_is_read_with_its_semantics_registers_and_width():
    found = nativelayout.signature(_container(_signature([b"POSITION", b"TEXCOORD"])))
    assert [(e.semantic, e.register, e.component, e.width) for e in found] == [("POSITION", 0, "float", 3),
                                                                              ("TEXCOORD", 1, "float", 3)]  # fmt: skip
    assert nativelayout.signature(_container(b"", b"XXXX")) == []  # no such part: no inputs


def test_a_cut_signature_raises_valueerror_not_a_struct_error():
    whole = _signature([b"POSITION", b"TEXCOORD"])
    for cut in (whole[:4], whole[:20], whole[:60], whole[: 8 + 32 * 2 + 3]):
        with pytest.raises(ValueError):
            nativelayout.signature(_container(cut))
    with pytest.raises(ValueError):
        nativelayout.signature(b"DXBC" + bytes(10))  # the container itself cut
