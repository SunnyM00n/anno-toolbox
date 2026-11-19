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
    h, w = rgba.shape[:2]
    ph, pw = -(-h // 4) * 4, -(-w // 4) * 4
    if (ph, pw) != (h, w):
        rgba = np.pad(rgba, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    tiled = rgba.reshape(ph // 4, 4, pw // 4, 4, 4).transpose(0, 2, 1, 3, 4)
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
    for p in (0, 1):
        q = np.clip(np.rint((target - p) / 2), 0, 127)
        err = (((q * 2 + p) - target) ** 2 * _CHANNEL_WEIGHT).sum(axis=1)
        if best_err is None:
            best_err, best_q, best_p = err, q, np.zeros(len(target), np.int64)
        else:
            use = err < best_err
            best_err = np.where(use, err, best_err)
            best_q = np.where(use[:, None], q, best_q)
            best_p = np.where(use, p, best_p)
    return best_q.astype(np.int64), best_p, best_q * 2 + best_p[:, None]


def _assign(texels: np.ndarray, e0: np.ndarray, e1: np.ndarray) -> np.ndarray:
    """The nearest of the sixteen levels for each texel, ``(n, 16)`` (the levels lie on one line: project, then pick)."""
    span = e1 - e0
    length = (span * span).sum(axis=1)
    t = np.einsum("nti,ni->nt", texels - e0[:, None, :], span) / np.maximum(length, 1e-9)[:, None]
    return np.searchsorted(_MIDS, np.clip(t, 0.0, 1.0) * 64.0).astype(np.int64)


def _decoded(e0: np.ndarray, e1: np.ndarray, index: np.ndarray) -> np.ndarray:
    w = WEIGHTS[index][:, :, None]
    return np.floor(((64 - w) * e0[:, None, :] + w * e1[:, None, :] + 32) / 64)


def _refit(texels: np.ndarray, index: np.ndarray, e0: np.ndarray, e1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares endpoints for the given levels (a block that uses one level keeps its endpoints)."""
    w = WEIGHTS[index] / 64.0
    a, b, c = ((1 - w) ** 2).sum(1), ((1 - w) * w).sum(1), (w * w).sum(1)
    det = a * c - b * b
    ok = det > 1e-6
    safe = np.where(ok, det, 1.0)[:, None]
    p0 = np.einsum("nt,nti->ni", 1 - w, texels)
    p1 = np.einsum("nt,nti->ni", w, texels)
    n0 = (c[:, None] * p0 - b[:, None] * p1) / safe
    n1 = (a[:, None] * p1 - b[:, None] * p0) / safe
    return np.where(ok[:, None], np.clip(n0, 0, 255), e0), np.where(ok[:, None], np.clip(n1, 0, 255), e1)


def _fit(texels: np.ndarray) -> tuple[np.ndarray, ...]:
    """(q0, p0, q1, p1, index) of the best round for each block."""
    mean = texels.mean(axis=1, keepdims=True)
    axis = _axis(texels, mean)
    t = np.einsum("nti,ni->nt", texels - mean, axis)
    e0 = np.clip(mean[:, 0] + axis * t.min(axis=1)[:, None], 0, 255)
    e1 = np.clip(mean[:, 0] + axis * t.max(axis=1)[:, None], 0, 255)
    best = None
    for _ in range(ROUNDS):
        q0, p0, v0 = _quantise(e0)
        q1, p1, v1 = _quantise(e1)
        index = _assign(texels, v0.astype(np.float64), v1.astype(np.float64))
        err = ((_decoded(v0, v1, index) - texels) ** 2 * _CHANNEL_WEIGHT).sum(axis=(1, 2))
        if best is None:
            best = [err, q0, p0, q1, p1, index]
        else:
            use = err < best[0]
            best = [
                np.where(use, err, best[0]),
                *(
                    np.where(use.reshape((-1,) + (1,) * (new.ndim - 1)), new, old)
                    for new, old in zip((q0, p0, q1, p1, index), best[1:], strict=True)
                ),
            ]
        e0, e1 = _refit(texels, index, e0, e1)
    return tuple(best[1:])


def _pack(q0, p0, q1, p1, index) -> np.ndarray:
    """``(n, 16)`` bytes: mode 6, R0 R1 G0 G1 B0 B1 A0 A1 (7 bits), the two p-bits, a 3-bit anchor index, 15 x 4 bits."""
    flip = index[:, 0] >= 8  # the anchor texel's top index bit is implicit 0: swap the ends instead
    q0, q1 = np.where(flip[:, None], q1, q0), np.where(flip[:, None], q0, q1)
    p0, p1 = np.where(flip, p1, p0), np.where(flip, p0, p1)
    index = np.where(flip[:, None], 15 - index, index)
    lo = np.full(len(q0), 64, np.uint64)
    for c in range(4):
        lo |= q0[:, c].astype(np.uint64) << np.uint64(7 + 14 * c)
        lo |= q1[:, c].astype(np.uint64) << np.uint64(14 + 14 * c)
    lo |= p0.astype(np.uint64) << np.uint64(63)
    hi = p1.astype(np.uint64) | (index[:, 0].astype(np.uint64) << np.uint64(1))
    shifts = (np.uint64(4) + np.uint64(4) * np.arange(15, dtype=np.uint64))[None, :]
    hi |= (index[:, 1:].astype(np.uint64) << shifts).sum(axis=1, dtype=np.uint64)
    out = np.empty((len(lo), 2), "<u8")
    out[:, 0], out[:, 1] = lo, hi
    return out.view(np.uint8).reshape(-1, 16)


def encode_level(rgba: np.ndarray) -> bytes:
    """One mip level (``(h, w, 4)`` uint8) as BC7 blocks, row by row."""
    texels = _blocks(np.asarray(rgba, np.uint8))
    return b"".join(_pack(*_fit(texels[i : i + CHUNK])).tobytes() for i in range(0, len(texels), CHUNK))


def encode_dds(levels: list[np.ndarray], srgb: bool) -> bytes:
    """A BC7 DDS of the mip levels (level 0 first), the header of ``bc7encode``."""
    h, w = levels[0].shape[:2]
    return dds_header(w, h, len(levels), srgb) + b"".join(encode_level(level) for level in levels)
