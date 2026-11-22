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


class CGrannyCompressionError(ValueError):
    """A compressed section that does not decode to its stated size."""


class _CDecoder:
    """The arithmetic decoder: ``decode`` peeks a value below ``top``, ``commit`` takes a range of them."""

    __slots__ = ("data", "pos", "numer", "denom", "step")

    def __init__(self, data: bytes, position: int) -> None:
        self.data, self.pos = data, position
        self.numer, self.denom, self.step = data[position] >> 1, 0x80, 1

    def decode(self, top: int) -> int:
        data = self.data
        while self.denom <= 0x800000:
            position = self.pos
            self.numer = (
                (self.numer << 8) | ((data[position] << 7) & 0x80) | ((data[position + 1] >> 1) & 0x7F)
            ) & _MASK
            self.pos = position + 1
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


class _CWindow:
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
        for weight in self.weights:
            ranges.append(start & 0xFFFF)
            start += (weight * unit) // 8
        ranges.append(0x4000)
        self.ranges = ranges
        if self.grow > self.grow_cap // 2:
            self.next_ranges = self.total + self.grow_cap
        else:
            self.grow *= 2
            self.next_ranges = self.total + self.grow

    def _rebuild_weights(self) -> None:
        weights, values = [weight // 2 for weight in self.weights], self.values
        i = 1
        while i < len(weights):
            while i < len(weights) and weights[i] == 0:  # the last one takes the empty slot
                weights[i], values[i] = weights[-1], values[-1]
                weights.pop()
                values.pop()
            i += 1
        if len(weights) > 1:  # the heaviest moves to the end (the first of equals)
            heaviest = max(range(1, len(weights)), key=weights.__getitem__)
            weights[heaviest], weights[-1] = weights[-1], weights[heaviest]
            values[heaviest], values[-1] = values[-1], values[heaviest]
        self.weights, self.total = weights, sum(weights)
        if len(weights) < self.cap and weights[0] == 0:
            weights[0] = 1
            self.total += 1

    def symbol(self, decoder: _CDecoder, top: int) -> int:
        """The next value; a new one is read flat below ``top`` and remembered."""
        if self.total >= self.next_ranges:
            if self.next_ranges >= self.next_weights:
                self._rebuild_weights()
            self._rebuild_ranges()
        ranges, weights = self.ranges, self.weights
        k = bisect_right(ranges, decoder.decode(0x4000)) - 1
        decoder.commit(0x4000, ranges[k], ranges[k + 1] - ranges[k])
        weights[k] += 1
        self.total += 1
        if k > 0:
            return self.values[k]
        if len(weights) >= len(ranges) and decoder.take(2) == 1:  # one added since the last rebuild
            k = len(ranges) + decoder.take(len(weights) - len(ranges) + 1) - 1
            weights[k] += 2
            self.total += 2
            return self.values[k]
        weights.append(2)
        self.total += 2
        if len(weights) == self.cap:  # full: no more escapes
            self.total -= weights[0]
            weights[0] = 0
        value = decoder.take(top)
        self.values.append(value)
        return value


def _parameters(raw: bytes) -> tuple[int, int, int, int, bytes]:
    """One run's parameters: literal values, match offset range, literal and high-offset counts, size counts."""
    first_word, second_word = struct.unpack_from("<II", raw)
    return first_word & 0x1FF, first_word >> 9, second_word & 0x1FF, second_word >> 19, raw[8:12]


def _run(decoder: _CDecoder, parameter_bytes: bytes, out: bytearray, stop: int) -> None:
    """Decode into ``out`` until it holds ``stop`` bytes, with a fresh model (one per run)."""
    literal_max, offset_max, literal_count, high_count, size_counts = _parameters(parameter_bytes)
    low_max, mid_max, high_max = min(offset_max + 1, 4), min(offset_max // 4 + 1, 256), offset_max // 1024 + 1
    low = _CWindow(low_max - 1, low_max)
    high = _CWindow(high_max - 1, high_count + 1)
    mids = [_CWindow(mid_max - 1, mid_max) for _ in range(high_max)]
    literals = [_CWindow(literal_max - 1, literal_count) for _ in range(4)]
    sizes = [_CWindow(64, size_counts[3 - i]) for i in range(4) for _ in range(16)] + [_CWindow(64, size_counts[0])]
    last, written = 0, 0
    while len(out) < stop:
        last = sizes[last].symbol(decoder, 65)
        if last == 0:  # a literal, modelled by its position modulo 4
            out.append(literals[len(out) & 3].symbol(decoder, literal_max) & 0xFF)
            written += 1
            continue
        length = last + 1 if last < 61 else _LONG_MATCHES[last - 61]
        reach = min(offset_max, written)
        low_part = low.symbol(decoder, low_max)
        high_part = high.symbol(decoder, reach // 1024 + 1)
        mid = mids[high_part].symbol(decoder, min(reach // 4 + 1, 256))
        offset = (high_part << 10) + (mid << 2) + low_part + 1
        if offset > len(out):
            raise CGrannyCompressionError(f"a match reaches {offset} bytes back from byte {len(out)}")
        written += length
        chunk = out[-offset:]
        out += (chunk * (length // offset + 1))[:length]


def decompress(data: bytes, size_bytes: int, stop0: int, stop1: int) -> bytes:
    """A section compressed with Oodle1 (``data``, as stored) to its ``size_bytes`` bytes; the stops are the
    section's 4-byte / 2-byte boundaries from the section table."""
    if not data:
        return bytes(size_bytes)
    if len(data) < 36:
        raise CGrannyCompressionError("an Oodle1 stream shorter than its parameters")
    padded = bytes(data) + bytes(4)  # the decoder reads one byte ahead
    decoder, out = _CDecoder(padded, 36), bytearray()
    try:
        for k, stop in enumerate((stop0, stop1, size_bytes)):
            _run(decoder, padded[12 * k : 12 * k + 12], out, stop)
    except (
        IndexError,
        struct.error,
    ) as exc:  # a cut or damaged stream reads past its end or names a model slot it lacks
        raise CGrannyCompressionError(f"the stream is cut short or damaged ({exc})") from exc
    if len(out) < size_bytes:
        raise CGrannyCompressionError(f"decoded {len(out)} of {size_bytes} bytes")
    return bytes(out[:size_bytes])
