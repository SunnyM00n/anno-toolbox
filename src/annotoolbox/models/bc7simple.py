"""A BC7 encoder in numpy, mode 6 only: the fallback where neither etcpak nor texconv is there (docs/texture-pipeline.md
"The built-in encoders").

Mode 6 is one subset, two 7-bit RGBA endpoints with a p-bit each and sixteen index levels: a block is one line through
colour and alpha. The fit is the block's principal axis, then three rounds of "nearest level per texel, least-squares
endpoints", the best round kept. It is exact on flat and on gradient blocks and loses to bc7e on blocks of two
unrelated colours (an edge), where bc7e picks a two-subset mode. No perceptual weights. The bytes depend only on the
input, on every machine (integer decisions on float64 sums in a fixed order).
"""

from __future__ import annotations

import numpy as np

from .bc7encode import dds_header

SETTINGS_VERSION = 1  # bumped when the fit changes what comes out
ROUNDS = 3
CHUNK = 8192  # blocks fitted at once: the working arrays stay a few tens of MB
ALPHA_WEIGHT = 4.0  # the shared p-bit is chosen against alpha too: opaque must stay 255, not 254
WEIGHTS = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], np.float64)  # the 4-bit indices
_MIDS = (WEIGHTS[1:] + WEIGHTS[:-1]) / 2
_CHANNEL_WEIGHT = np.array([1.0, 1.0, 1.0, ALPHA_WEIGHT])


def identity() -> str:
    return f"bc7simple-m6-s{SETTINGS_VERSION}"


def _blocks(rgba: np.ndarray) -> np.ndarray:
    """``(h, w, 4)`` as ``(n, 16, 4)`` float64 texels in block order; sides that are no multiple of 4 repeat the edge."""
    height, width = rgba.shape[:2]
    padded_height, padded_width = -(-height // 4) * 4, -(-width // 4) * 4
    if (padded_height, padded_width) != (height, width):
        rgba = np.pad(rgba, ((0, padded_height - height), (0, padded_width - width), (0, 0)), mode="edge")
    tiled = rgba.reshape(padded_height // 4, 4, padded_width // 4, 4, 4).transpose(0, 2, 1, 3, 4)
    return tiled.reshape(-1, 16, 4).astype(np.float64)


def _axis(texels: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """Each block's main axis in RGBA: power iteration on the covariance, started on the bounding diagonal."""
    centred = texels - mean
    cov = np.einsum("nti,ntj->nij", centred, centred)
    axis = texels.max(axis=1) - texels.min(axis=1) + 1e-3
    for _ in range(4):
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-12
    return axis


def _quantise(target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Endpoints ``(n, 4)`` in 0..255 as (7-bit values, p-bit, the 8-bit values the decoder reads): the p-bit that
    leaves the smaller weighted error, one for all four channels."""
    best_err = best_q = best_p = None
    for pbit in (0, 1):
        quantised = np.clip(np.rint((target - pbit) / 2), 0, 127)
        err = (((quantised * 2 + pbit) - target) ** 2 * _CHANNEL_WEIGHT).sum(axis=1)
        if best_err is None:
            best_err, best_q, best_p = err, quantised, np.zeros(len(target), np.int64)
        else:
            use = err < best_err
            best_err = np.where(use, err, best_err)
            best_q = np.where(use[:, None], quantised, best_q)
            best_p = np.where(use, pbit, best_p)
    return best_q.astype(np.int64), best_p, best_q * 2 + best_p[:, None]


def _assign(texels: np.ndarray, first_endpoint: np.ndarray, second_endpoint: np.ndarray) -> np.ndarray:
    """The nearest of the sixteen levels for each texel, ``(n, 16)`` (the levels lie on one line: project, then pick)."""
    span = second_endpoint - first_endpoint
    length = (span * span).sum(axis=1)
    positions = np.einsum("nti,ni->nt", texels - first_endpoint[:, None, :], span) / np.maximum(length, 1e-9)[:, None]
    return np.searchsorted(_MIDS, np.clip(positions, 0.0, 1.0) * 64.0).astype(np.int64)


def _decoded(first_endpoint: np.ndarray, second_endpoint: np.ndarray, index: np.ndarray) -> np.ndarray:
    weights = WEIGHTS[index][:, :, None]
    return np.floor(((64 - weights) * first_endpoint[:, None, :] + weights * second_endpoint[:, None, :] + 32) / 64)


def _refit(
    texels: np.ndarray, index: np.ndarray, first_endpoint: np.ndarray, second_endpoint: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares endpoints for the given levels (a block that uses one level keeps its endpoints)."""
    weights = WEIGHTS[index] / 64.0
    sum_a, sum_b, sum_c = ((1 - weights) ** 2).sum(1), ((1 - weights) * weights).sum(1), (weights * weights).sum(1)
    det = sum_a * sum_c - sum_b * sum_b
    is_solvable = det > 1e-6
    safe = np.where(is_solvable, det, 1.0)[:, None]
    first_sums = np.einsum("nt,nti->ni", 1 - weights, texels)
    second_sums = np.einsum("nt,nti->ni", weights, texels)
    first_refit = (sum_c[:, None] * first_sums - sum_b[:, None] * second_sums) / safe
    second_refit = (sum_a[:, None] * second_sums - sum_b[:, None] * first_sums) / safe
    return np.where(is_solvable[:, None], np.clip(first_refit, 0, 255), first_endpoint), np.where(
        is_solvable[:, None], np.clip(second_refit, 0, 255), second_endpoint
    )


def _fit(texels: np.ndarray) -> tuple[np.ndarray, ...]:
    """(q0, p0, q1, p1, index) of the best round for each block."""
    mean = texels.mean(axis=1, keepdims=True)
    axis = _axis(texels, mean)
    positions = np.einsum("nti,ni->nt", texels - mean, axis)
    first_endpoint = np.clip(mean[:, 0] + axis * positions.min(axis=1)[:, None], 0, 255)
    second_endpoint = np.clip(mean[:, 0] + axis * positions.max(axis=1)[:, None], 0, 255)
    best = None
    for _ in range(ROUNDS):
        first_quantised, first_pbit, first_values = _quantise(first_endpoint)
        second_quantised, second_pbit, second_values = _quantise(second_endpoint)
        index = _assign(texels, first_values.astype(np.float64), second_values.astype(np.float64))
        err = ((_decoded(first_values, second_values, index) - texels) ** 2 * _CHANNEL_WEIGHT).sum(axis=(1, 2))
        if best is None:
            best = [err, first_quantised, first_pbit, second_quantised, second_pbit, index]
        else:
            use = err < best[0]
            best = [
                np.where(use, err, best[0]),
                *(
                    np.where(use.reshape((-1,) + (1,) * (new.ndim - 1)), new, old)
                    for new, old in zip(
                        (first_quantised, first_pbit, second_quantised, second_pbit, index), best[1:], strict=True
                    )
                ),
            ]
        first_endpoint, second_endpoint = _refit(texels, index, first_endpoint, second_endpoint)
    return tuple(best[1:])


def _pack(first_quantised, first_pbit, second_quantised, second_pbit, index) -> np.ndarray:
    """``(n, 16)`` bytes: mode 6, R0 R1 G0 G1 B0 B1 A0 A1 (7 bits), the two p-bits, a 3-bit anchor index, 15 x 4 bits."""
    flip = index[:, 0] >= 8  # the anchor texel's top index bit is implicit 0: swap the ends instead
    first_quantised, second_quantised = (
        np.where(flip[:, None], second_quantised, first_quantised),
        np.where(flip[:, None], first_quantised, second_quantised),
    )
    first_pbit, second_pbit = np.where(flip, second_pbit, first_pbit), np.where(flip, first_pbit, second_pbit)
    index = np.where(flip[:, None], 15 - index, index)
    low_bits = np.full(len(first_quantised), 64, np.uint64)
    for channel in range(4):
        low_bits |= first_quantised[:, channel].astype(np.uint64) << np.uint64(7 + 14 * channel)
        low_bits |= second_quantised[:, channel].astype(np.uint64) << np.uint64(14 + 14 * channel)
    low_bits |= first_pbit.astype(np.uint64) << np.uint64(63)
    high_bits = second_pbit.astype(np.uint64) | (index[:, 0].astype(np.uint64) << np.uint64(1))
    shifts = (np.uint64(4) + np.uint64(4) * np.arange(15, dtype=np.uint64))[None, :]
    high_bits |= (index[:, 1:].astype(np.uint64) << shifts).sum(axis=1, dtype=np.uint64)
    out = np.empty((len(low_bits), 2), "<u8")
    out[:, 0], out[:, 1] = low_bits, high_bits
    return out.view(np.uint8).reshape(-1, 16)


def encode_level(rgba: np.ndarray) -> bytes:
    """One mip level (``(h, w, 4)`` uint8) as BC7 blocks, row by row."""
    texels = _blocks(np.asarray(rgba, np.uint8))
    return b"".join(_pack(*_fit(texels[i : i + CHUNK])).tobytes() for i in range(0, len(texels), CHUNK))


def encode_dds(levels: list[np.ndarray], is_srgb: bool) -> bytes:
    """A BC7 DDS of the mip levels (level 0 first), the header of ``bc7encode``."""
    height, width = levels[0].shape[:2]
    return dds_header(width, height, len(levels), is_srgb) + b"".join(encode_level(level) for level in levels)
