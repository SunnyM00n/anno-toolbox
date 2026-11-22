"""The asset a position of an asset file stands in, and an asset's use of a file.

``asset_at(text, starts, position)`` gives the (GUID, name) of the ``<Asset>`` a position stands in (``ASSET_START``
matches where each begins); ``SAssetUse`` is one asset's use of a file, as the viewer's "Used by" dock lists it. Who
names a file is answered by the reference graph (``refgraph.graph.CRefGraph.users``; docs/references.md).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

ASSET_START = re.compile(r"<Asset>")
_GUID = re.compile(r"<GUID>(\d+)</GUID>")
_NAME = re.compile(r"<Name>([^<>]*)</Name>")


@dataclass(frozen=True)
class SAssetUse:
    """An asset that names a file: its GUID, its name, the field and the asset file it stands in."""

    guid: int
    name: str
    field: str
    file: str


def asset_at(text: str, starts: list[int], position: int) -> tuple[int, str] | None:
    """(GUID, name) of the ``<Asset>`` a position in an asset file stands in (``starts``: where each ``<Asset>``
    begins), None outside one or in one without a GUID."""
    start_index = bisect.bisect_right(starts, position) - 1
    if start_index < 0:
        return None
    end = starts[start_index + 1] if start_index + 1 < len(starts) else len(text)
    guid = _GUID.search(text, starts[start_index], end)
    if guid is None:
        return None
    name = _NAME.search(text, starts[start_index], end)
    return int(guid.group(1)), name.group(1) if name else ""
