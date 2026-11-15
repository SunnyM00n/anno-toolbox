"""The editor's ground look reads the material maps the way the island build's encoder writes them."""

import numpy as np
import pytest

from annotoolbox.islands.islandground import GroundMixin
from annotoolbox.islands.islandview import IslandView
from annotoolbox.islands.materialorder import ranks
from fakes import FakeFiles

SET = "data/base/graphics/material_sets/test.xml"
LISTED = [7, 3, 11, 5]  # the set's draw order: not the IDs' and not any slot order


class _Encoder(GroundMixin):
    """The island build's encoder over weights given, as ``groundedit`` uses it."""

    def __init__(self, mats, weights, rank) -> None:
        self._mats, self._weights, self.material_rank = mats, weights, rank

    def material_weights(self, texels_per_tile, material_of):
        return self._mats, self._weights


class _Maps(IslandView):
    """An island view over material maps already in hand."""

    def __init__(self, ids, alphas, listed) -> None:
        body = "".join(f"<Material><ID>{m}</>\n<FileName>data/m/{m}.xml</>\n</>\n" for m in listed)
        self.files, self.material_set = FakeFiles({SET: f"<MaterialSet>{body}</MaterialSet>".encode()}), SET
        self._maps = ids, alphas

    def material_maps(self):
        return self._maps


def _ground(seed: int) -> np.ndarray:
    """Four grounds per texel, some of them zero, in smooth-ish patches (a block of 2 x 2 texels keeps its mix)."""
    rng = np.random.default_rng(seed)
    raw = rng.random((4, 8, 8)) ** 3
    raw[rng.random((4, 8, 8)) < 0.3] = 0
    raw[0] += 0.01  # something stands everywhere
    raw = np.repeat(np.repeat(raw, 2, axis=1), 2, axis=2)
    return (raw / raw.sum(axis=0)).astype(np.float32)


@pytest.mark.parametrize("listed", [LISTED, sorted(LISTED)])
def test_the_editor_reads_the_ground_in_the_order_the_encoder_wrote_it(listed):
    mats = sorted(LISTED)
    weights = _ground(1)
    ids, alphas, _ = _Encoder(mats, weights, ranks(listed)).encode_materials(8, {}, 2)

    mids, wts = _Maps(ids, alphas, listed).weights()

    read = {m: np.where(mids == m, wts, 0).sum(axis=0) for m in mats}
    for k, m in enumerate(mats):
        assert np.abs(read[m] - weights[k]).max() < 0.1, m  # 5-bit opacities, stacked
    assert np.allclose(sum(read.values()), 1.0, atol=1e-5)
    assert (np.argmax([read[m] for m in mats], axis=0) == weights.argmax(axis=0)).mean() > 0.97
