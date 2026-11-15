"""A map template's editor level header (``.a7te``): its dimensions and the list of its 64-tile chunks, read and written
in the layout the games ship (CRLF, two-space indent). docs/map-editor.md "The files per game".
"""

from __future__ import annotations

import re

CHUNK = 64  # the chunk edge every shipped header names
_DIMENSIONS = re.compile(rb"(<Dimensions>\s*<X>)(\d+)(</X>\s*<Z>)(\d+)(</Z>)")
_CHUNKS = re.compile(rb"<Chunks>.*?</Chunks>|<Chunks\s*/>", re.S)


def _chunks(width: int, height: int, chunk: int = CHUNK) -> bytes:
    cols = "".join(
        "    <Column>\r\n"
        + "".join(f"      <Chunk>{x}x{z}</Chunk>\r\n" for z in range(height // chunk))
        + "    </Column>\r\n"
        for x in range(width // chunk)
    )
    return f"<Chunks>\r\n{cols}  </Chunks>".encode()


def editor_level(width: int, height: int | None = None, chunk: int = CHUNK) -> bytes:
    """The header of a map of ``width`` x ``height`` tiles (square when ``height`` is None), one column per chunk in x."""
    height = width if height is None else height
    return (
        b"<AnnoEditorLevel>\r\n  <FileVersion>4</FileVersion>\r\n  <ScenarioGuid>0</ScenarioGuid>\r\n"
        + f"  <Dimensions>\r\n    <X>{width}</X>\r\n    <Z>{height}</Z>\r\n  </Dimensions>\r\n".encode()
        + f"  <ChunkSize>\r\n    <X>{chunk}</X>\r\n    <Z>{chunk}</Z>\r\n  </ChunkSize>\r\n  ".encode()
        + _chunks(width, height, chunk)
        + b"\r\n</AnnoEditorLevel>"
    )


def dimensions(data: bytes) -> tuple[int, int] | None:
    """The (X, Z) a header names, None when it names none."""
    m = _DIMENSIONS.search(data)
    return (int(m.group(2)), int(m.group(4))) if m else None


def regrown(data: bytes, width: int, height: int) -> bytes:
    """A header at ``width`` x ``height``: its dimensions and chunk list rewritten, everything else as it was (the
    version, scenario and region lines differ between the games)."""
    if not _DIMENSIONS.search(data) or not _CHUNKS.search(data):
        return editor_level(width, height)
    out = _DIMENSIONS.sub(
        lambda m: m.group(1) + b"%d" % width + m.group(3) + b"%d" % height + m.group(5), data, count=1
    )
    return _CHUNKS.sub(lambda _m: _chunks(width, height), out, count=1)
