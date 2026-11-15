"""Wwise ADPCM ``.wem`` files (IMA ADPCM, 16-bit samples) both ways: ``read`` decodes, ``write`` encodes.

A block is one 36-byte sub-block a channel: a 4-byte header (the start value and the step index), then 64 samples of 4
bits, low nibble first. ``read`` needs numpy (imported where used); ``write`` is stdlib.
"""

from __future__ import annotations

import array
import struct

from . import riff

# fmt: off
STEPS = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107,
    118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894,
    6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
)
# fmt: on
INDEX = (-1, -1, -1, -1, 2, 4, 6, 8)  # the step index's move per nibble (its low three bits)
BLOCK_SAMPLES = 64  # the samples of a sub-block


def read(data: bytes):
    """A Wwise ADPCM ``.wem`` as ``(samples, rate, channels)``: interleaved int16 samples (numpy). ``ValueError``
    for a file that is no RIFF/WAVE or has no usable format chunk."""
    import numpy as np

    chunks = riff.chunks(data)
    fmt = chunks.get(b"fmt ", b"")
    if len(fmt) < 16:
        raise ValueError("no format chunk")
    _tag, channels, rate, _per_second, align = struct.unpack_from("<HHIIH", fmt)
    sub = align // max(channels, 1)
    if not channels or sub < 5:
        raise ValueError(f"ADPCM block of {align} bytes for {channels} channels")
    body = chunks.get(b"data", b"")
    blocks = np.frombuffer(body, np.uint8, len(body) // align * align).reshape(-1, channels, sub)
    hist = blocks[..., 0].astype(np.int64) | (blocks[..., 1].astype(np.int64) << 8)
    hist = np.where(hist >= 32768, hist - 65536, hist)
    index = np.clip(blocks[..., 2].astype(np.int64), 0, 88)
    steps, moves = np.asarray(STEPS), np.asarray(INDEX)
    count = 2 * (sub - 4)
    out = np.empty((len(blocks), count, channels), np.int16)
    for i in range(count):
        nibble = (blocks[..., 4 + i // 2] >> (4 * (i & 1))).astype(np.int64) & 15
        step = steps[index]
        delta = (
            (step >> 3) + (nibble & 1) * (step >> 2) + ((nibble >> 1) & 1) * (step >> 1) + ((nibble >> 2) & 1) * step
        )
        hist = np.clip(hist + np.where(nibble & 8, -delta, delta), -32768, 32767)
        index = np.clip(index + moves[nibble & 7], 0, 88)
        out[:, i] = hist
    return out.reshape(-1), rate, channels


def _sub_block(samples: array.array, start: int, step: int, state: list[int]) -> bytes:
    """One sub-block: the state the decoder starts from, then 64 nibbles (the samples from ``start``, every
    ``step``-th); ``state`` (predictor, index) moves on to the next block."""
    pred, index = state
    out = bytearray(struct.pack("<hBB", pred, index, 0))
    nibbles = []
    for i in range(BLOCK_SAMPLES):
        at = start + i * step
        target = samples[at] if at < len(samples) else pred  # a partial last block holds on
        diff, nib, size = target - pred, 0, STEPS[index]
        if diff < 0:
            nib, diff = 8, -diff
        for bit in (4, 2, 1):
            if diff >= size:
                nib |= bit
                diff -= size
            size >>= 1
        full = STEPS[index]  # the decoder's own reconstruction, so the error does not run away
        delta = (full >> 3) + (nib & 1) * (full >> 2) + ((nib >> 1) & 1) * (full >> 1) + ((nib >> 2) & 1) * full
        pred = max(-32768, min(32767, pred + (-delta if nib & 8 else delta)))
        index = max(0, min(88, index + INDEX[nib & 7]))
        nibbles.append(nib)
    for i in range(0, BLOCK_SAMPLES, 2):
        out.append(nibbles[i] | nibbles[i + 1] << 4)
    state[:] = [pred, index]
    return bytes(out)


def write(samples: array.array, rate: int, channels: int) -> bytes:
    """A Wwise ADPCM ``.wem`` of interleaved int16 ``samples`` (mono or stereo); the inverse of ``read``."""
    frames = len(samples) // channels
    states = [[0, 0] for _ in range(channels)]
    blocks = []
    for first in range(0, max(frames, 1), BLOCK_SAMPLES):
        for c in range(channels):
            blocks.append(_sub_block(samples, first * channels + c, channels, states[c]))
    data = b"".join(blocks)
    align = 36 * channels
    avg = round(rate * align / BLOCK_SAMPLES)
    mask = {1: 4, 2: 3}[channels]
    fmt = struct.pack("<HHIIHHHHI", 2, channels, rate, avg, align, 4, 6, 0, mask)[:24]
    return riff.build([(b"fmt ", fmt), (b"data", data)])
