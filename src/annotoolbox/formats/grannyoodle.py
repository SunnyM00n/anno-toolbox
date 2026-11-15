"""Granny 2's Oodle1 section compression, decoded: an LZ77 stream whose literals, match lengths and match
offsets are coded by an adaptive arithmetic coder, in three runs (the section's 4-byte, 2-byte and 1-byte
parts, split at its two stop offsets).

``decompress`` is the entry (``grannyread`` calls it per section). The algorithm follows the decoder
published in xoreos-tools (Boost Software License 1.0) by way of nwn2mdk (Apache 2.0); docs/formats/granny.md
"Oodle1" describes it and names the sources.
"""

from __future__ import annotations

import struct
from bisect import bisect_right

_MASK = 0xFFFFFFFF
_LONG_MATCHES = (128, 192, 256, 512)  # match lengths of the size symbols 61..64


class GrannyCompressionError(ValueError):
    """A compressed section that does not decode to its stated size."""


class _Decoder:
    """The arithmetic decoder: ``decode`` peeks a value below ``top``, ``commit`` takes a range of them."""

    __slots__ = ("data", "pos", "numer", "denom", "step")

    def __init__(self, data: bytes, pos: int) -> None:
        self.data, self.pos = data, pos
        self.numer, self.denom, self.step = data[pos] >> 1, 0x80, 1

    def decode(self, top: int) -> int:
        data = self.data
        while self.denom <= 0x800000:
            p = self.pos
            self.numer = ((self.numer << 8) | ((data[p] << 7) & 0x80) | ((data[p + 1] >> 1) & 0x7F)) & _MASK
            self.pos = p + 1
            self.denom = (self.denom << 8) & _MASK
        self.step = self.denom // top
        return min(self.numer // self.step, top - 1)

    def commit(self, top: int, value: int, width: int) -> int:
        self.numer = (self.numer - self.step * value) & _MASK
        if value + width < top:
            self.denom = self.step * width
        else:
            self.denom = (self.denom - self.step * value) & _MASK
        return value

    def take(self, top: int) -> int:
        """One value below ``top``, all equally likely."""
        return self.commit(top, self.decode(top), 1)


class _Window:
    """An adaptive model of one symbol: the values seen so far with their weights; a value not seen
    yet is escaped and read flat by the caller (``symbol``)."""

    __slots__ = ("cap", "ranges", "values", "weights", "total", "grow", "grow_cap", "next_ranges", "next_weights")

    def __init__(self, max_value: int, count_cap: int) -> None:
        self.cap = count_cap + 1
        self.ranges, self.values, self.weights, self.total = [0, 0x4000], [0], [4], 4
        self.grow, self.next_ranges = 4, 8
        self.next_weights = max(256, min(32 * max_value, 15160))
        self.grow_cap = min(2 * max_value, self.next_weights // 2 - 32) if max_value > 64 else 128

    def _rebuild_ranges(self) -> None:
        unit = 8 * 0x4000 // self.total
        ranges, start = [], 0
        for w in self.weights:
            ranges.append(start & 0xFFFF)
            start += (w * unit) // 8
        ranges.append(0x4000)
        self.ranges = ranges
        if self.grow > self.grow_cap // 2:
            self.next_ranges = self.total + self.grow_cap
        else:
            self.grow *= 2
            self.next_ranges = self.total + self.grow

    def _rebuild_weights(self) -> None:
        weights, values = [w // 2 for w in self.weights], self.values
        i = 1
        while i < len(weights):
            while i < len(weights) and weights[i] == 0:  # the last one takes the empty slot
                weights[i], values[i] = weights[-1], values[-1]
                weights.pop()
                values.pop()
            i += 1
        if len(weights) > 1:  # the heaviest moves to the end (the first of equals)
            k = max(range(1, len(weights)), key=weights.__getitem__)
            weights[k], weights[-1] = weights[-1], weights[k]
            values[k], values[-1] = values[-1], values[k]
        self.weights, self.total = weights, sum(weights)
        if len(weights) < self.cap and weights[0] == 0:
            weights[0] = 1
            self.total += 1

    def symbol(self, dec: _Decoder, top: int) -> int:
        """The next value; a new one is read flat below ``top`` and remembered."""
        if self.total >= self.next_ranges:
            if self.next_ranges >= self.next_weights:
                self._rebuild_weights()
            self._rebuild_ranges()
        ranges, weights = self.ranges, self.weights
        k = bisect_right(ranges, dec.decode(0x4000)) - 1
        dec.commit(0x4000, ranges[k], ranges[k + 1] - ranges[k])
        weights[k] += 1
        self.total += 1
        if k > 0:
            return self.values[k]
        if len(weights) >= len(ranges) and dec.take(2) == 1:  # one added since the last rebuild
            k = len(ranges) + dec.take(len(weights) - len(ranges) + 1) - 1
            weights[k] += 2
            self.total += 2
            return self.values[k]
        weights.append(2)
        self.total += 2
        if len(weights) == self.cap:  # full: no more escapes
            self.total -= weights[0]
            weights[0] = 0
        value = dec.take(top)
        self.values.append(value)
        return value


def _parameters(raw: bytes) -> tuple[int, int, int, int, bytes]:
    """One run's parameters: literal values, match offset range, literal and high-offset counts, size counts."""
    a, b = struct.unpack_from("<II", raw)
    return a & 0x1FF, a >> 9, b & 0x1FF, b >> 19, raw[8:12]


def _run(dec: _Decoder, params: bytes, out: bytearray, stop: int) -> None:
    """Decode into ``out`` until it holds ``stop`` bytes, with a fresh model (one per run)."""
    literal_max, offset_max, literal_count, high_count, size_counts = _parameters(params)
    low_max, mid_max, high_max = min(offset_max + 1, 4), min(offset_max // 4 + 1, 256), offset_max // 1024 + 1
    low = _Window(low_max - 1, low_max)
    high = _Window(high_max - 1, high_count + 1)
    mids = [_Window(mid_max - 1, mid_max) for _ in range(high_max)]
    literals = [_Window(literal_max - 1, literal_count) for _ in range(4)]
    sizes = [_Window(64, size_counts[3 - i]) for i in range(4) for _ in range(16)] + [_Window(64, size_counts[0])]
    last, written = 0, 0
    while len(out) < stop:
        last = sizes[last].symbol(dec, 65)
        if last == 0:  # a literal, modelled by its position modulo 4
            out.append(literals[len(out) & 3].symbol(dec, literal_max) & 0xFF)
            written += 1
            continue
        length = last + 1 if last < 61 else _LONG_MATCHES[last - 61]
        reach = min(offset_max, written)
        lo = low.symbol(dec, low_max)
        hi = high.symbol(dec, reach // 1024 + 1)
        mid = mids[hi].symbol(dec, min(reach // 4 + 1, 256))
        offset = (hi << 10) + (mid << 2) + lo + 1
        if offset > len(out):
            raise GrannyCompressionError(f"a match reaches {offset} bytes back from byte {len(out)}")
        written += length
        chunk = out[-offset:]
        out += (chunk * (length // offset + 1))[:length]


def decompress(data: bytes, size: int, stop0: int, stop1: int) -> bytes:
    """A section compressed with Oodle1 (``data``, as stored) to its ``size`` bytes; the stops are the
    section's 4-byte / 2-byte boundaries from the section table."""
    if not data:
        return bytes(size)
    if len(data) < 36:
        raise GrannyCompressionError("an Oodle1 stream shorter than its parameters")
    padded = bytes(data) + bytes(4)  # the decoder reads one byte ahead
    dec, out = _Decoder(padded, 36), bytearray()
    for k, stop in enumerate((stop0, stop1, size)):
        _run(dec, padded[12 * k : 12 * k + 12], out, stop)
    if len(out) < size:
        raise GrannyCompressionError(f"decoded {len(out)} of {size} bytes")
    return bytes(out[:size])
