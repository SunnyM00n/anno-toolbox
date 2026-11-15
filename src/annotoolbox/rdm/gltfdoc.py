"""What the glTF import's steps share: its options, its error, the document with its node tree, and the extras.

``gltfrdm.import_gltf`` builds a ``Doc`` and hands it to ``gltfmesh`` (the meshes) and ``gltfskin`` (skeletons and
animations).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats.gltf import Gltf
from ..formats.rdmread import RdmFile
from . import rigmath

EXTRAS = "rdm"  # the key of every extras block the export writes


class GltfImportError(ValueError):
    """A document the import refuses; the message says what to change."""


@dataclass(frozen=True)
class Options:
    """``axes`` None: the document's own (its scene's extras, else "gltf"). ``layout``: the vertex layout of every
    mesh (else the one the extras or ``like`` name, else the one the attributes ask for). ``mesh``: only the glTF
    mesh of this index or name. ``transform``: bake a static mesh's node transforms into it. ``joint_names``: "name"
    (the nodes' names) or "index". ``like``: an RDM file whose order, names, layouts and stored values to follow."""

    axes: str | None = None
    layout: str | None = None
    mesh: str | None = None
    transform: bool = True
    joint_names: str = "name"
    material_order: tuple[str, ...] = ()
    frame_rate: int | None = None
    like: RdmFile | None = None


def tags(item: dict) -> dict:
    """The extras the export wrote on an item ({} when there are none, as after most modelling tools)."""
    extras = item.get("extras")
    if isinstance(extras, dict) and isinstance(extras.get(EXTRAS), dict):
        return extras[EXTRAS]
    return {}


@dataclass
class Doc:
    """The document with what every step needs: node parents and world matrices, the mirror, the notes."""

    g: Gltf
    opts: Options
    mirror: bool
    scene: dict
    parents: dict[int, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for p, node in enumerate(self.g.items("nodes")):
            for c in node.get("children", []):
                self.parents[c] = p

    def node(self, i: int) -> dict:
        return self.g.items("nodes")[i]

    def name(self, i: int) -> str:
        return self.node(i).get("name", f"node {i}")

    def world(self, i: int) -> rigmath.Matrix:
        """The node's matrix in the scene: its own after its parents'."""
        m = rigmath.from_node(self.node(i))
        while i in self.parents:
            i = self.parents[i]
            m = rigmath.multiply(rigmath.from_node(self.node(i)), m)
        return m

    def walk(self, roots: list[int]) -> list[int]:
        """The nodes under ``roots``, depth first, in the document's order."""
        out, stack = [], list(reversed(roots))
        while stack:
            i = stack.pop()
            out.append(i)
            stack += list(reversed(self.node(i).get("children", [])))
        return out
