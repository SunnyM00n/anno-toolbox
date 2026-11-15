"""A new Anno 2070 island from scratch, for Anno 2070 editing: a rounded landmass in 2070's own island form
(``isd2070.blank_island``) that the editor opens like any island of the game and the build writes into the patch
archive next to the game's islands, as ``data/levels/islands/normal/new<size>.isd``.

The edit file names it (``source``, and ``new``: ``size`` and ``seed``) instead of a file of the game, so
``with_new_islands`` lays the blank islands of an edit folder over the game's files for the editor and the build.
A new island has no far-view picture and no entry in any list the game keeps; whether 2070 offers it is not known
(island-editor "Full spec").
"""

from __future__ import annotations

from pathlib import Path

from ....formats import isd2070
from ....islands import islandedits

NORMAL_DIR = "data/levels/islands/normal/"
PREFIX = "new"
SIZES = tuple(range(64, 257, isd2070.CHUNK_TILES))  # the sizes ``blank_island`` makes


def name(size: int) -> str:
    return f"{PREFIX}{size}"


def source(size: int) -> str:
    """The game path of the new island's ``.isd``."""
    return f"{NORMAL_DIR}{name(size)}.isd"


def size_of(island: str) -> int | None:
    """The size of a new island's name (``new128`` -> 128), None for any other name or a size ``blank_island`` refuses."""
    digits = island.removeprefix(PREFIX)
    return int(digits) if island.startswith(PREFIX) and digits.isdigit() and int(digits) in SIZES else None


def new_edits(size: int, seed: int = 1) -> dict:
    """The edit file of a new island of ``size`` tiles: the empty edits and what names the island."""
    if size not in SIZES:
        raise ValueError(
            f"no island of {size} tiles: a multiple of {isd2070.CHUNK_TILES} from {SIZES[0]} to {SIZES[-1]}"
        )
    return {**islandedits.empty(), "source": source(size), "new": {"size": size, "seed": seed}}


def island_bytes(spec: dict) -> bytes:
    """The ``.isd`` file of a new island (``new``: ``size``, ``seed``)."""
    return isd2070.serialize(isd2070.blank_island(int(spec["size"]), int(spec.get("seed", 1))).root)


class NewIslandTree:
    """A tree of the game's files with new islands added: ``paths`` maps a game path to the spec of a new island.
    Every other lookup is the game's own."""

    def __init__(self, tree, paths: dict[str, dict]) -> None:
        self._tree, self._paths = tree, {p.lower(): spec for p, spec in paths.items()}

    def exists(self, rel) -> bool:
        return str(rel).lower() in self._paths or self._tree.exists(rel)

    def read_bytes(self, rel) -> bytes:
        spec = self._paths.get(str(rel).lower())
        return island_bytes(spec) if spec is not None else self._tree.read_bytes(rel)

    def __getattr__(self, attr):
        return getattr(self._tree, attr)


def with_new_islands(tree, edits_dir: Path, prefix: str):
    """``tree`` with the new islands of the edit files ``<prefix>*.json`` in ``edits_dir`` added; ``tree`` itself when
    there is none."""
    paths = {}
    for path in sorted(edits_dir.glob(f"{prefix}*.json")):
        edits = islandedits.load(path)
        if edits.get("new") and edits.get("source"):
            paths[edits["source"]] = edits["new"]
    return NewIslandTree(tree, paths) if paths else tree
