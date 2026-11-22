"""A mod kept in a ``.zip``: members are read in place, and a member that inflates past what the directory says is
refused (modloader/zipmods.py)."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from annotoolbox.modloader import zipmods


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _claim_size(path: Path, size: int) -> None:
    """Rewrite the uncompressed size the central directory states for the first member."""
    raw = bytearray(path.read_bytes())
    at = raw.rindex(b"PK\x01\x02")
    raw[at + 24 : at + 28] = struct.pack("<I", size)
    path.write_bytes(bytes(raw))


def test_a_member_is_read_in_place(tmp_path: Path):
    listing = zipmods.CListing(_zip(tmp_path / "m.zip", {"a/b.txt": b"hello " * 50}))
    assert listing.read("a/b.txt") == b"hello " * 50


def test_a_deflate_bomb_that_claims_a_small_size_is_refused_with_a_clear_error(tmp_path: Path):
    path = _zip(tmp_path / "bomb.zip", {"big.bin": bytes(5_000_000)})
    _claim_size(path, 10)
    with pytest.raises(zipfile.BadZipFile, match="inflates past its stated size"):
        zipmods.CListing(path).read("big.bin")


def test_a_member_that_claims_more_than_the_limit_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(zipmods, "MAX_MEMBER_BYTES", 100)
    listing = zipmods.CListing(_zip(tmp_path / "m.zip", {"a.bin": bytes(1000)}))
    with pytest.raises(zipfile.BadZipFile, match="over the limit"):
        listing.read("a.bin")
