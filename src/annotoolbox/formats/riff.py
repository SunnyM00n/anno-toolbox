"""RIFF/WAVE files: the chunks of a file, and a file from chunks (a plain WAV, Wwise's ``.wem``).

``chunks(data)`` is the one reader (a ``.wem``, a streamed WAV, a game's loose sound); ``build`` and ``wav_bytes``
write. Nothing here knows a codec.
"""

from __future__ import annotations

import struct


def spans(data: bytes, form: bytes = b"WAVE") -> dict[bytes, tuple[int, int]]:
    """Where each chunk's body is: ``tag -> (offset, stated size)``, the first of each tag winning. ``ValueError`` when
    ``data`` is no RIFF of that form."""
    if data[:4] != b"RIFF" or data[8:12] != form:
        raise ValueError(f"not a RIFF/{form.decode('ascii', 'replace')} file")
    pos, out = 12, {}
    while pos + 8 <= len(data):
        tag, size = data[pos : pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        out.setdefault(tag, (pos + 8, size))
        pos += 8 + size + (size & 1)
    return out


def chunks(data: bytes, form: bytes = b"WAVE") -> dict[bytes, bytes]:
    """The chunks of a RIFF file by tag, the first of each tag winning; a chunk whose size runs past the end (a WAV
    streamed through a pipe states none) is cut there."""
    return {tag: data[at : at + size] for tag, (at, size) in spans(data, form).items()}


def build(parts: list[tuple[bytes, bytes]], form: bytes = b"WAVE") -> bytes:
    """A RIFF file of ``form`` from ``(tag, body)`` chunks, an odd body padded as the format asks."""
    body = form + b"".join(tag + struct.pack("<I", len(part)) + part + b"\0" * (len(part) & 1) for tag, part in parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_bytes(samples: bytes, rate: int, channels: int, bits: int = 16) -> bytes:
    """Integer PCM samples (interleaved, little endian, ``bits`` wide) as a plain WAV file."""
    width = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * width, width, bits)
    return build([(b"fmt ", fmt), (b"data", samples)])
