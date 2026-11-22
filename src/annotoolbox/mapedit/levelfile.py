"""A map template's editor level header (``.a7te``): its dimensions and the list of its 64-tile chunks, read and written
in the layout the games ship (CRLF, two-space indent). docs/map-editor.md "The files per game".
"""

from __future__ import annotations

import re

CHUNK = 64  # the chunk edge every shipped header names
_DIMENSIONS = re.compile(rb"(<Dimensions>\s*<X>)(\d+)(</X>\s*<Z>)(\d+)(</Z>)")
_CHUNKS = re.compile(rb"<Chunks>.*?</Chunks>|<Chunks\s*/>", re.S)


def _chunks(width_tiles: int, height_tiles: int, chunk: int = CHUNK) -> bytes:
    cols = "".join(
        "    <Column>\r\n"
        + "".join(f"      <Chunk>{x}x{z}</Chunk>\r\n" for z in range(height_tiles // chunk))
        + "    </Column>\r\n"
        for x in range(width_tiles // chunk)
    )
    return f"<Chunks>\r\n{cols}  </Chunks>".encode()


def editor_level(width_tiles: int, height_tiles: int | None = None, chunk: int = CHUNK) -> bytes:
    """The header of a map of ``width`` x ``height`` tiles (square when ``height`` is None), one column per chunk in x."""
    height_tiles = width_tiles if height_tiles is None else height_tiles
    return (
        b"<AnnoEditorLevel>\r\n  <FileVersion>4</FileVersion>\r\n  <ScenarioGuid>0</ScenarioGuid>\r\n"
        + f"  <Dimensions>\r\n    <X>{width_tiles}</X>\r\n    <Z>{height_tiles}</Z>\r\n  </Dimensions>\r\n".encode()
        + f"  <ChunkSize>\r\n    <X>{chunk}</X>\r\n    <Z>{chunk}</Z>\r\n  </ChunkSize>\r\n  ".encode()
        + _chunks(width_tiles, height_tiles, chunk)
        + b"\r\n</AnnoEditorLevel>"
    )


def dimensions(data: bytes) -> tuple[int, int] | None:
    """The (X, Z) a header names, None when it names none."""
    match = _DIMENSIONS.search(data)
    return (int(match.group(2)), int(match.group(4))) if match else None


def regrown(data: bytes, width_tiles: int, height_tiles: int) -> bytes:
    """A header at ``width`` x ``height``: its dimensions and chunk list rewritten, everything else as it was (the
    version, scenario and region lines differ between the games)."""
    if not _DIMENSIONS.search(data) or not _CHUNKS.search(data):
        return editor_level(width_tiles, height_tiles)
    out = _DIMENSIONS.sub(
        lambda match: match.group(1) + b"%d" % width_tiles + match.group(3) + b"%d" % height_tiles + match.group(5),
        data,
        count=1,
    )
    return _CHUNKS.sub(lambda _m: _chunks(width_tiles, height_tiles), out, count=1)
