"""The asset a position of an asset file stands in, and an asset's use of a file.

``asset_at(text, starts, position)`` gives the (GUID, name) of the ``<Asset>`` a position stands in (``ASSET_START``
matches where each begins); ``AssetUse`` is one asset's use of a file, as the viewer's "Used by" dock lists it. Who
names a file is answered by the reference graph (``refgraph.graph.RefGraph.users``; docs/references.md).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

ASSET_START = re.compile(r"<Asset>")
_GUID = re.compile(r"<GUID>(\d+)</GUID>")
_NAME = re.compile(r"<Name>([^<>]*)</Name>")


@dataclass(frozen=True)
class AssetUse:
    """An asset that names a file: its GUID, its name, the field and the asset file it stands in."""

    guid: int
    name: str
    field: str
    file: str


def asset_at(text: str, starts: list[int], position: int) -> tuple[int, str] | None:
    """(GUID, name) of the ``<Asset>`` a position in an asset file stands in (``starts``: where each ``<Asset>``
    begins), None outside one or in one without a GUID."""
    at = bisect.bisect_right(starts, position) - 1
    if at < 0:
        return None
    end = starts[at + 1] if at + 1 < len(starts) else len(text)
    guid = _GUID.search(text, starts[at], end)
    if guid is None:
        return None
    name = _NAME.search(text, starts[at], end)
    return int(guid.group(1)), name.group(1) if name else ""
