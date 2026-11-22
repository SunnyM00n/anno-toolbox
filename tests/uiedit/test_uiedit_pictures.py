"""``uiedit/pictures``: a picture is decoded at the stored level its drawn size asks for, and kept per level. Synthetic
trees and a stand-in DDS reader only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")

import numpy as np

from annotoolbox.uiedit import pictures
from annotoolbox.uiedit.pictures import CPictures, size_level


class FakeGameTree:
    def exists(self, path: str) -> bool:
        return path == "data/ui/x_0.dds"

    def read(self, path: str) -> bytes:
        return b""


class FakeDds:
    """Three levels: 256, 128 and 64 pixels wide, each filled with its own level number."""

    def __init__(self, _data) -> None:
        self.m_info = SimpleNamespace(mips=3)

    def size_of(self, mip: int):
        return (256 >> mip, 256 >> mip)

    def level(self, mip: int):
        return np.full((256 >> mip, 256 >> mip, 4), mip, dtype=np.uint8)


@pytest.fixture
def pics(monkeypatch) -> CPictures:
    monkeypatch.setattr(pictures.ddsdecode, "CDdsFile", FakeDds)
    return CPictures(SimpleNamespace(m_tree=FakeGameTree(), root=lambda _a: None))


def test_a_picture_first_drawn_small_is_decoded_again_when_drawn_larger(pics):
    small = pics.get(image_id="data/ui/x", size_px=(60.0, 60.0))
    assert small is not None and small.width == 64
    large = pics.get(image_id="data/ui/x", size_px=(200.0, 200.0))
    assert large is not None and large.width == 256  # not the 64-pixel level kept from the first draw
    assert large.key != small.key  # a drawing backend's own copies are told apart too
    assert pics.get(image_id="data/ui/x", size_px=(250.0, 100.0)) is large  # the same level: kept
    assert pics.get(image_id="data/ui/x", size_px=(60.0, 60.0)) is small


def test_sizes_of_one_power_of_two_step_share_a_level():
    assert size_level((0.0, 0.0)) == size_level((1.0, 1.0)) == 0
    assert size_level((65.0, 3.0)) == size_level((128.0, 100.0)) == 7
    assert size_level((129.0, 1.0)) == 8


def test_a_name_the_game_lacks_is_listed_once_per_name(pics):
    assert pics.get(image_id="data/ui/none", size_px=(10.0, 10.0)) is None
    assert pics.get(image_id="data/ui/none", size_px=(500.0, 500.0)) is None
    assert pics.m_missing == {"data/ui/none"}
