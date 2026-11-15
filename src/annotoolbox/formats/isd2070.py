"""Anno 2070 island files (``.isd``, also the level files ``.www``): tree reader.

XML-like text with binary blocks: ``<Name>`` opens a node, ``</Name>`` or the generic ``</>`` closes the innermost
one, text between tags is the node's value, ``CDATA[`` + ``uint32 length`` + bytes + ``]`` a binary value. Anno 1701
and 1404 write the same tree with spaces and dots in names and with or without a line break after a close; ``parse``
keeps the white space around tags, so ``serialize`` gives the file back (formats/anno1701-world-format.md "The tag
tree"). This module only builds the tree; ``Island`` interprets the parts the editor uses.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

# 1701: "Config 0000", "m_DynGridPosition.x"; 1404: <Areas/>; 117: <Common [PhysicallyBasedSubstance]>, <Distance;>
_TAG = re.compile(rb"<(/?)([A-Za-z0-9_ .;\[\]]*)(/?)>")
_SPACE = re.compile(rb"[ \t\r\n]*")
CRLF = b"\r\n"


@dataclass
class Node:
    name: str
    text: str | None = None
    data: bytes | None = None
    children: list[Node] = field(default_factory=list)
    short: bool = False  # closed by the generic </> (as the Terrain section is)
    tail: bytes = CRLF  # what follows the close: 2070 always writes CRLF, 1701 often nothing
    head: bytes = b""  # what follows the open tag (the indentation of hand-made 1701 files)
    closed: bool = True  # False: the file ends inside it (old 1701 worlds never close their root)
    empty: bool = False  # written ``<Name/>``, as 1404's hand-made benchmark world does
    trail: bytes = b""  # white space ending the text, kept ("... pioneer = </>" in a 1701 save)

    def find(self, name: str) -> Node | None:
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name: str) -> list[Node]:
        return [c for c in self.children if c.name == name]

    def path(self, path: str) -> Node | None:
        node = self
        for part in path.split("/"):
            node = node.find(part) if node else None
        return node

    def value(self, path: str, default=None):
        n = self.path(path)
        return n.text if n is not None and n.text is not None else default

    def int(self, path: str, default: int = 0) -> int:
        v = self.value(path)
        return int(v) if v is not None else default

    def float(self, path: str, default: float = 0.0) -> float:
        v = self.value(path)
        return float(v) if v is not None else default

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


def parse(b: bytes) -> Node:
    root = Node("")
    stack = [root]
    i, n = 0, len(b)
    while i < n:
        c = b[i]
        if c == 0x3C:  # '<'
            m = _TAG.match(b, i)
            if m:
                i = m.end()
                if m.group(1):
                    if len(stack) > 1:
                        node = stack.pop()
                        node.short = not m.group(2)
                        k = _SPACE.match(b, i).end()
                        if b[i:k] != CRLF:
                            node.tail = b[i:k]
                        i = k
                else:
                    node = Node(m.group(2).decode("ascii"), empty=bool(m.group(3)))
                    stack[-1].children.append(node)
                    if node.empty:
                        k = _SPACE.match(b, i).end()
                        node.tail = b[i:k]
                        i = k
                        continue
                    stack.append(node)
                    k = _SPACE.match(b, i).end()
                    if k > i:
                        node.head = b[i:k]
                        i = k
                continue
        if b.startswith(b"CDATA[", i):
            (length,) = struct.unpack_from("<I", b, i + 6)
            stack[-1].data = b[i + 10 : i + 10 + length]
            i += 10 + length + 1  # the closing ']'
            continue
        j = b.find(b"<", i + 1)
        if j < 0:
            j = n
        raw = b[i:j]
        text = raw.strip()
        if text:
            stack[-1].text = text.decode("latin1")
            stack[-1].trail = raw[len(raw.rstrip()) :]
        i = j
    for node in stack[1:]:
        node.closed = False
    return root


def serialize(root: Node) -> bytes:
    """The bytes of a tree from ``parse``: a vanilla file comes back byte for byte."""
    out = bytearray()

    def write(node: Node) -> None:
        if node.empty:
            out.extend(b"<%s/>" % node.name.encode("ascii") + node.tail)
            return
        out.extend(b"<%s>" % node.name.encode("ascii") + node.head)
        if node.data is not None:
            out.extend(b"CDATA[" + struct.pack("<I", len(node.data)) + node.data + b"]")
        elif node.text is not None:
            out.extend(node.text.encode("latin1") + node.trail)
        for c in node.children:
            write(c)
        if node.closed:
            out.extend(b"</>" if node.short else b"</%s>" % node.name.encode("ascii"))
            out.extend(node.tail)

    if root.text is not None:  # a byte order mark before the first tag (2205's .fc files)
        out.extend(root.text.encode("latin1") + root.trail)
    for c in root.children:
        write(c)
    return bytes(out)


FIXED = 4096.0  # fixed-point positions (int64 x, z, height): units per tile
# PropertyMesh/m_RenderParameters (props, slots, effects, lights): a 30-byte header, then the
# object as drawn - position, orientation (x, y, z, w, tilt included) and scale - and a tint
RENDER_AT = 30
RENDER_TRANSFORM = struct.Struct("<3f4ff")


@dataclass
class IslandObject:
    kind: str  # Nature, Simple, Handle, Feedback, Grass
    guid: int
    x: float  # tiles
    y: float  # height
    z: float  # tiles
    direction: int
    variation: int | None
    scale: float | None
    orientation: tuple[float, float, float, float] | None
    id: int = 0  # m_ID, what the island's object groups name


class Island:
    """The parts of a 2070 island the editor uses."""

    # texture GUID -> SplatID (terrain2070.splat_ids): the order 2070 paints a chunk's layers in;
    # without it, the file's order (texture GUID order, rendering-2070 §2.1)
    order: dict[int, int] | None = None
    # which sides (west, north, east, south) of the island's grid meet another island's grid at a seam: the window
    # lies flush to that side, whose ground is not laid to the floor (islands.islandvariant)
    seam: tuple[bool, bool, bool, bool] = (False, False, False, False)
    # the sides whose neighbour's grid would overlap this one's: the window lies flush to the side, which stays laid to
    # the floor as any grid edge (islands.islandvariant)
    flush: tuple[bool, bool, bool, bool] = (False, False, False, False)
    # what a crop or a merge made of the island (islands.islandframe, islandvariant): its window in the file's own
    # tiles, the file's size and the islands merged in; None for an island as the file has it
    frame: dict | None = None

    def __init__(self, data: bytes) -> None:
        self._init(parse(data))

    @classmethod
    def from_tree(cls, root: Node) -> Island:
        """An island over a tree built in memory (``blank_island``, tests), not read from a file."""
        isl = cls.__new__(cls)
        isl._init(root)
        return isl

    def _init(self, root: Node) -> None:
        self.root = root
        self.width = root.int("Width")
        self.height = root.int("Height")
        self.clime = root.int("Clime")  # 0 above water, 1 underwater
        self.difficulty = root.int("Difficulty")  # 0 on 132 of the 133 normal islands
        # 0 on the 133 normal islands (absent: 0, or -28 underwater); other values on the pirate towers (-3),
        # the third parties (-1, -2) and the underwater islands (-28..-33)
        self.sea_level = root.int("SeaLevel")

    def _ordered(self, layers: list) -> list:
        """A chunk's ``(texture GUID, ...)`` layers in 2070's paint order (``order``)."""
        if not self.order:
            return layers
        return sorted(layers, key=lambda layer: self.order.get(layer[0], 1 << 30))

    def heightmap(self, missing: float | None = None) -> list[list[float]]:
        """(width+1) x (height+1) float heights from the terrain chunks, rows = z. Chunks without a
        heightmap (open sea) get ``missing`` (default: the lowest height found). A chunk may hold
        finer samples than its corners (33 x 33: one per half tile); only those on corners are read
        (placing every sample at its rounded tile let half-tile samples overwrite the corners)."""
        t = self.root.path("Terrain")
        cm = t.path("ChunkMap")
        cw, ch = cm.int("Width"), cm.int("Height")
        tiles_x, tiles_z = t.int("TileCountX"), t.int("TileCountZ")
        per_x, per_z = tiles_x // cw, tiles_z // ch
        grid: list[list[float | None]] = [[None] * (tiles_x + 1) for _ in range(tiles_z + 1)]
        low = None
        for k, el in enumerate(cm.findall("Element")):
            hm = el.find("HeightMap")
            if hm is None or hm.path("Data") is None:
                continue
            w = hm.int("Width")
            vals = struct.unpack(f"<{w * w}f", hm.path("Data").data)
            cx, cz = k % cw, k // cw
            if (w - 1) % per_x == 0:  # as fine as the corners or finer: the samples on corners
                s = (w - 1) // per_x
                samples = [(i, j, i * s, j * s) for j in range(per_z + 1) for i in range(per_x + 1)]
            else:  # coarser: each sample at its nearest corner
                step = per_x / (w - 1)
                samples = [(round(i * step), round(j * step), i, j) for j in range(w) for i in range(w)]
            for gi, gj, i, j in samples:
                v = vals[j * w + i]
                grid[cz * per_z + gj][cx * per_x + gi] = v
                low = v if low is None else min(low, v)
        fill = missing if missing is not None else (low or 0.0)
        return [[fill if v is None else v for v in row] for row in grid]

    def texture_layers(self) -> dict[int, list[list[int]]]:
        """Texture index -> (width+1) x (height+1) alpha (0-255), assembled from the chunks. Layers
        are painted in order over a full-coverage base layer; dict order is the paint order.

        Every chunk lists its own layers in its own order, so the order across the island is the
        topological order of all "a is painted before b" pairs (first appearance breaks ties and
        cycles). Merging the chunks in first-seen order put, for instance, n_l03's river bed sand
        (83) before its grass (52) although all 81 chunks holding both paint the sand over the
        grass, and the river bed came out grassy (2026-09-22)."""
        t = self.root.path("Terrain")
        cm = t.path("ChunkMap")
        cw = cm.int("Width")
        tiles_x, tiles_z = t.int("TileCountX"), t.int("TileCountZ")
        per = tiles_x // cw
        layers: dict[int, list[list[int]]] = {}
        after: dict[int, set[int]] = {}  # layer -> layers painted before it
        seen: list[int] = []
        for el in cm.findall("Element"):
            order = [
                i for i, _ in self._ordered([(tex.int("TextureIndex"), None) for tex in el.findall("TexIndexData")])
            ]
            for pos, idx in enumerate(order):
                if idx not in after:
                    after[idx] = set()
                    seen.append(idx)
                after[idx].update(order[:pos])
        for k, el in enumerate(cm.findall("Element")):
            cx, cz = k % cw, k // cw
            for tex in el.findall("TexIndexData"):
                idx = tex.int("TextureIndex")
                am = tex.find("AlphaMap")
                w = am.int("Width")
                vals = am.path("Data").data
                grid = layers.setdefault(idx, [[0] * (tiles_x + 1) for _ in range(tiles_z + 1)])
                step = per / (w - 1)
                for j in range(w):
                    for i in range(w):
                        grid[cz * per + round(j * step)][cx * per + round(i * step)] = vals[j * w + i]
        done: list[int] = []
        rest = list(seen)
        while rest:
            ready = [i for i in rest if not (after[i] - set(done))]
            if not ready:  # a cycle: keep the remaining layers in first-seen order
                ready = rest[:1]
            done.extend(ready)
            rest = [i for i in rest if i not in set(ready)]
        return {i: layers[i] for i in done if i in layers}

    def texture_chunks(self):
        """Per chunk: (chunk x, chunk z, tiles per chunk, [(texture index, alpha grid)]) with the
        chunk's own paint order and its (w x w) alpha grid over the chunk's tiles plus one border
        sample. Chunks disagree about the order (a beach paints grass over sand, a river bed sand
        over grass), so weights must be composited per chunk, not from a merged order."""
        t = self.root.path("Terrain")
        cm = t.path("ChunkMap")
        cw = cm.int("Width")
        per = t.int("TileCountX") // cw
        for k, el in enumerate(cm.findall("Element")):
            layers = []
            for tex in el.findall("TexIndexData"):
                am = tex.find("AlphaMap")
                w = am.int("Width")
                vals = am.path("Data").data
                grid = [[vals[j * w + i] for i in range(w)] for j in range(w)]
                layers.append((tex.int("TextureIndex"), grid))
            if layers:
                yield k % cw, k // cw, per, self._ordered(layers)

    def objects(self) -> list[IslandObject]:
        """Every placed object of ``m_GOPManager/Objects``, by group (Nature, Simple, Handle ...)."""
        groups = self.root.path("m_GOPManager/Objects")
        out = []
        for group in groups.children if groups else []:
            objects = group.find("Objects")
            for obj in objects.findall("Object") if objects else []:
                out.append(_island_object(group.name, obj))
        return out

    def animal_paths(self) -> list[list[tuple[tuple[float, float], list[int]]]]:
        """``AnimalLayer``: per populated layer the anchor points the animals walk between, ``((x, z) in tiles, indices
        of the anchors it connects to)``. The points are 24-byte records (int64 x, z, height at 4096 per tile), the
        connections pairs of uint32 indices into them."""
        layers = self.root.path("AnimalLayer")
        out = []
        for layer in layers.children if layers else []:
            points = layer.find("m_AnchorPoints")
            if points is None or not points.children:
                continue
            pos = [
                (struct.unpack_from("<q", n.data, 0)[0] / FIXED, struct.unpack_from("<q", n.data, 8)[0] / FIXED)
                for n in points.children
                if n.data and len(n.data) >= 16
            ]
            links = layer.find("m_Connections")
            raw = links.data if links is not None and links.data else b""
            pairs = struct.unpack_from(f"<{len(raw) // 4}I", raw) if raw else ()
            near: list[list[int]] = [[] for _ in pos]
            for a, b in zip(pairs[0::2], pairs[1::2], strict=False):
                if a < len(pos) and b < len(pos):
                    near[a].append(b)
                    near[b].append(a)
            out.append(list(zip(pos, near, strict=True)))
        return out

    def object_groups(self) -> list[list[int]]:
        """``m_GOPManager/ObjectGroups``: lists of ``m_ID`` that 2070 treats as one (a mine slot and its pickup, a dam
        slot and its walls, a rubble slot and its ruins; island-slots)."""
        groups = self.root.path("m_GOPManager/ObjectGroups")
        return [
            list(struct.unpack(f"<{len(g.data) // 4}I", g.data))
            for g in (groups.children if groups else [])
            if g.data and len(g.data) >= 8
        ]

    def heightmap_v2(self) -> list[int]:
        """The logic grid heights (``m_HeightMap_v2``, width x height int16: a tile's mean corner height x 1024)."""
        d = self.root.path("m_GOPManager/m_GRIDManager/m_HeightMap_v2").data
        return list(struct.unpack(f"<{len(d) // 2}h", d))


def position_node(obj: Node) -> Node | None:
    """The node holding an object's position (three int64: x, z, height): ``m_Position`` itself, or in the 35 older
    1404 islands its child ``data``; None where the object has none."""
    pos = obj.find("m_Position")
    if pos is not None and pos.data is None:
        pos = pos.find("data")
    return pos if pos is not None and pos.data is not None and len(pos.data) == 24 else None


def _island_object(kind: str, obj: Node) -> IslandObject:
    pos = position_node(obj)
    x, z, y = struct.unpack_from("<qqq", pos.data) if pos is not None else (0, 0, 0)
    var = obj.find("m_Variation")
    nat = obj.find("Nature")
    orient = nat.find("m_Orientation") if nat else None
    scale = nat.find("m_Scale") if nat else None
    quat = struct.unpack("<4f", orient.data) if orient is not None and orient.data else None
    size = float(scale.text) if scale is not None and scale.text else None
    rp = obj.path("PropertyMesh/m_RenderParameters")
    if nat is None and rp is not None and rp.data and len(rp.data) >= RENDER_AT + RENDER_TRANSFORM.size:
        # outside the Nature group the tilt and scale are only in the drawn transform
        *_, qx, qy, qz, qw, s = RENDER_TRANSFORM.unpack_from(rp.data, RENDER_AT)
        quat, size = (qx, qy, qz, qw), s
    return IslandObject(
        kind=kind,
        guid=obj.int("m_GUID"),
        x=x / FIXED,
        y=y / FIXED,
        z=z / FIXED,
        direction=obj.int("m_Direction"),
        variation=int(var.text) if var is not None and var.text else None,
        scale=size,
        orientation=quat,
        id=obj.int("m_ID"),
    )


def outline(node: Node, depth: int = 0, out: list | None = None) -> list[str]:
    """Each distinct child path once, with counts (repeated siblings folded)."""
    out = [] if out is None else out
    groups: dict[str, list[Node]] = {}
    for c in node.children:
        groups.setdefault(c.name, []).append(c)
    for name, nodes in groups.items():
        first = nodes[0]
        desc = f"{'  ' * depth}{name} x{len(nodes)}"
        if first.text is not None:
            desc += f" = {first.text[:40]}"
        if first.data is not None:
            desc += f" [{len(first.data)} bytes] {first.data[:16].hex(' ')}"
        out.append(desc)
        merged = Node(name, children=[ch for nd in nodes for ch in nd.children])
        outline(merged, depth + 1, out)
    return out


BLANK_TEXTURES = {"water": 0, "beach": 78, "grass": 52}  # GroundTextures GUIDs: the sea bed, beach_01, gras_01
BLANK_LAND = 0.7  # 2070's building height: the plateau most of an island stands on
BLANK_SEA_BED = -40.0
BLANK_SHELF = 14.0  # tiles: the sea floor falls to within a third of the bed at this distance out
CHUNK_TILES = 16
BLANK_COAST_LINES, BLANK_COAST_POINTS = 6, 72  # harbour coast all the way round, as six lines


def blank_island(size: int, seed: int = 1) -> Island:
    """A new island in 2070's own form, for ``island_test.py --new``: a rounded, gently lobed
    landmass of grass at building height with a beach, sloping into the sea, no objects. It goes
    through the reader like any 2070 island, so the island editor starts from it."""
    import math
    import random

    import numpy as np

    if size % CHUNK_TILES or not 64 <= size <= 256:
        raise ValueError(f"island size {size}: a multiple of {CHUNK_TILES} from 64 to 256")
    rnd = random.Random(seed)
    lobes = [(k, rnd.uniform(0.04, 0.12) / k**0.5, rnd.uniform(0, 2 * math.pi)) for k in (2, 3, 5, 7)]
    n = size + 1
    zz, xx = np.mgrid[0:n, 0:n].astype(np.float64)
    c = size / 2
    angle = np.arctan2(zz - c, xx - c)
    reach = 0.34 * size * (1 + sum(a * np.cos(k * angle + p) for k, a, p in lobes))
    d = reach - np.hypot(xx - c, zz - c)  # tiles inside the coast (negative: out at sea)
    heights = np.where(
        d >= 4, BLANK_LAND, np.where(d >= 0, BLANK_LAND * d / 4, BLANK_SEA_BED * (1 - np.exp(d / BLANK_SHELF)))
    ).astype(np.float32)
    beach = np.clip((6 - d) / 3, 0, 1) * (d > -3)  # a sand rim along the coast, under the waterline too
    grass = np.clip((d - 3) / 3, 0, 1)
    alphas = {
        BLANK_TEXTURES["water"]: np.full((n, n), 255.0),
        BLANK_TEXTURES["beach"]: beach * 255,
        BLANK_TEXTURES["grass"]: grass * 255,
    }
    terrain = terrain_node(size, size, heights, alphas)

    def shore(theta: float) -> tuple[float, float]:  # a tile inside the coast, on the beach
        r = 0.34 * size * (1 + sum(a * math.cos(k * theta + p) for k, a, p in lobes)) - 1
        return c + r * math.cos(theta), c + r * math.sin(theta)

    ring = [shore(2 * math.pi * k / BLANK_COAST_POINTS) for k in range(BLANK_COAST_POINTS + 1)]
    per = BLANK_COAST_POINTS // BLANK_COAST_LINES
    lines = [ring[k * per : (k + 1) * per + 1] for k in range(BLANK_COAST_LINES)]
    coast = Node("CoastBuildingLines", children=[_coast_line(pts) for pts in lines])
    return Island.from_tree(
        Node("", children=[Node("Width", str(size)), Node("Height", str(size)), Node("Clime", "0"), coast, terrain])
    )


def flat_island(size: int, height: float) -> Island:
    """An island in 2070's form with no land: every corner at ``height`` under the sea bed ground, no coast, no
    objects (a floor island between the islands of a fixed map, island-maps "The sea bed")."""
    import numpy as np

    if size % CHUNK_TILES or not 64 <= size <= 256:
        raise ValueError(f"island size {size}: a multiple of {CHUNK_TILES} from 64 to 256")
    n = size + 1
    heights = np.full((n, n), height, np.float32)
    terrain = terrain_node(size, size, heights, {BLANK_TEXTURES["water"]: np.full((n, n), 255.0)})
    return Island.from_tree(
        Node("", children=[Node("Width", str(size)), Node("Height", str(size)), Node("Clime", "0"), terrain])
    )


def terrain_node(width: int, height: int, heights, alphas: dict, missing: float = BLANK_SEA_BED) -> Node:
    """2070's ``Terrain`` of chunks from corner ``heights`` (rows z, NaN: no terrain there) and texture ``alphas``
    (GUID -> corner alpha 0-255, the sea bed's laid in every chunk of a blank island). A chunk with no height has no
    ``HeightMap``; the NaN corners of one with heights read ``missing``; a texture is listed where it has any alpha."""
    import numpy as np

    cx_n, cz_n = width // CHUNK_TILES, height // CHUNK_TILES
    elements = []
    for cz in range(cz_n):
        for cx in range(cx_n):
            sl = np.s_[cz * CHUNK_TILES : cz * CHUNK_TILES + 17, cx * CHUNK_TILES : cx * CHUNK_TILES + 17]
            block = np.asarray(heights[sl], np.float32)
            el = Node("Element")
            if not np.isnan(block).all():
                el.children.append(_grid_node("HeightMap", np.where(np.isnan(block), missing, block).tobytes()))
            for guid, alpha in alphas.items():
                a = np.asarray(alpha[sl]).round().astype(np.uint8)
                if a.any():
                    tex = Node(
                        "TexIndexData", children=[Node("TextureIndex", str(guid)), _grid_node("AlphaMap", a.tobytes())]
                    )
                    el.children.append(tex)
            elements.append(el)
    chunk_map = Node("ChunkMap", children=[Node("Width", str(cx_n)), Node("Height", str(cz_n)), *elements])
    return Node("Terrain", children=[Node("TileCountX", str(width)), Node("TileCountZ", str(height)), chunk_map])


def _coast_line(points) -> Node:
    """A 2070 coast building line: fixed-point (x, z) points; the directions are not read."""
    pts = [Node("i", data=struct.pack("<qq", round(x * FIXED), round(z * FIXED))) for x, z in points]
    return Node("i", children=[Node("Points", children=pts), Node("Directions", children=[])])


def _grid_node(name: str, data: bytes) -> Node:
    return Node(name, children=[Node("Width", str(CHUNK_TILES + 1)), Node("Data", data=data)])
