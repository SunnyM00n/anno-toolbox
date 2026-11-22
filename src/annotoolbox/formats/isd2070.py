"""Anno 2070 island files (``.isd``, also the level files ``.www``): tree reader.

XML-like text with binary blocks: ``<Name>`` opens a node, ``</Name>`` or the generic ``</>`` closes the innermost
one, text between tags is the node's value, ``CDATA[`` + ``uint32 length`` + bytes + ``]`` a binary value. Anno 1701
and 1404 write the same tree with spaces and dots in names and with or without a line break after a close; ``parse``
keeps the white space around tags, so ``serialize`` gives the file back (formats/anno1701-world-format.md "The tag
tree"). This module only builds the tree; ``CIsland`` interprets the parts the editor uses.
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
class SNode:
    """One tag of the tree: its name, its text or binary value, its children, and the bytes around it that keep the file whole."""

    name: str
    text: str | None = None
    data: bytes | None = None
    children: list[SNode] = field(default_factory=list)
    short: bool = False  # closed by the generic </> (as the Terrain section is)
    tail: bytes = CRLF  # what follows the close: 2070 always writes CRLF, 1701 often nothing
    head: bytes = b""  # what follows the open tag (the indentation of hand-made 1701 files)
    closed: bool = True  # False: the file ends inside it (old 1701 worlds never close their root)
    empty: bool = False  # written ``<Name/>``, as 1404's hand-made benchmark world does
    trail: bytes = b""  # white space ending the text, kept ("... pioneer = </>" in a 1701 save)

    def find(self, name: str) -> SNode | None:
        """The first child with that name, None where there is none."""
        return next((child for child in self.children if child.name == name), None)

    def findall(self, name: str) -> list[SNode]:
        """Every child with that name."""
        return [child for child in self.children if child.name == name]

    def path(self, path: str) -> SNode | None:
        """The node at a ``/`` separated path of child names, None where a step is missing."""
        node = self
        for part in path.split("/"):
            node = node.find(part) if node else None
        return node

    def value(self, path: str, default=None):
        """The text at ``path``, ``default`` where the node or its text is missing."""
        node = self.path(path)
        return node.text if node is not None and node.text is not None else default

    def int(self, path: str, default: int = 0) -> int:
        text = self.value(path)
        return int(text) if text is not None else default

    def float(self, path: str, default: float = 0.0) -> float:
        text = self.value(path)
        return float(text) if text is not None else default

    def iter(self):
        """This node and every node below it, depth first."""
        yield self
        for child in self.children:
            yield from child.iter()


def parse(data: bytes) -> SNode:
    root = SNode("")
    stack = [root]
    i, data_length = 0, len(data)
    while i < data_length:
        byte = data[i]
        if byte == 0x3C:  # '<'
            tag = _TAG.match(data, i)
            if tag:
                i = tag.end()
                if tag.group(1):
                    if len(stack) > 1:
                        node = stack.pop()
                        node.short = not tag.group(2)
                        k = _SPACE.match(data, i).end()
                        if data[i:k] != CRLF:
                            node.tail = data[i:k]
                        i = k
                else:
                    node = SNode(tag.group(2).decode("ascii"), empty=bool(tag.group(3)))
                    stack[-1].children.append(node)
                    if node.empty:
                        k = _SPACE.match(data, i).end()
                        node.tail = data[i:k]
                        i = k
                        continue
                    stack.append(node)
                    k = _SPACE.match(data, i).end()
                    if k > i:
                        node.head = data[i:k]
                        i = k
                continue
        if data.startswith(b"CDATA[", i):
            if i + 10 > data_length:
                raise ValueError(f"the binary value at {i} is cut short")
            (length,) = struct.unpack_from("<I", data, i + 6)
            stack[-1].data = data[i + 10 : i + 10 + length]
            i += 10 + length + 1  # the closing ']'
            continue
        j = data.find(b"<", i + 1)
        if j < 0:
            j = data_length
        raw = data[i:j]
        text = raw.strip()
        if text:
            stack[-1].text = text.decode("latin1")
            stack[-1].trail = raw[len(raw.rstrip()) :]
        i = j
    for node in stack[1:]:
        node.closed = False
    return root


def serialize(root: SNode) -> bytes:
    """The bytes of a tree from ``parse``: a vanilla file comes back byte for byte."""
    out = bytearray()

    def write(node: SNode) -> None:
        if node.empty:
            out.extend(b"<%s/>" % node.name.encode("ascii") + node.tail)
            return
        out.extend(b"<%s>" % node.name.encode("ascii") + node.head)
        if node.data is not None:
            out.extend(b"CDATA[" + struct.pack("<I", len(node.data)) + node.data + b"]")
        elif node.text is not None:
            out.extend(node.text.encode("latin1") + node.trail)
        for child in node.children:
            write(child)
        if node.closed:
            out.extend(b"</>" if node.short else b"</%s>" % node.name.encode("ascii"))
            out.extend(node.tail)

    if root.text is not None:  # a byte order mark before the first tag (2205's .fc files)
        out.extend(root.text.encode("latin1") + root.trail)
    for child in root.children:
        write(child)
    return bytes(out)


FIXED = 4096.0  # fixed-point positions (int64 x, z, height): units per tile
# PropertyMesh/m_RenderParameters (props, slots, effects, lights): a 30-byte header, then the
# object as drawn - position, orientation (x, y, z, w, tilt included) and scale - and a tint
RENDER_AT = 30
RENDER_TRANSFORM = struct.Struct("<3f4ff")


@dataclass
class SIslandObject:
    """One placed object of a 2070 island: its group, GUID, position in tiles and how it is drawn."""

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


class CIsland:
    """The parts of a 2070 island the editor uses."""

    # texture GUID -> SplatID (terrain2070.splat_ids): the order 2070 paints a chunk's layers in;
    # without it, the file's order (texture GUID order, rendering-2070 §2.1)
    m_order: dict[int, int] | None = None
    # which sides (west, north, east, south) of the island's grid meet another island's grid at a seam: the window
    # lies flush to that side, whose ground is not laid to the floor (islands.islandvariant)
    m_seam: tuple[bool, bool, bool, bool] = (False, False, False, False)
    # the sides whose neighbour's grid would overlap this one's: the window lies flush to the side, which stays laid to
    # the floor as any grid edge (islands.islandvariant)
    m_flush: tuple[bool, bool, bool, bool] = (False, False, False, False)
    # what a crop or a merge made of the island (islands.islandframe, islandvariant): its window in the file's own
    # tiles, the file's size and the islands merged in; None for an island as the file has it
    m_frame: dict | None = None

    def __init__(self, data: bytes) -> None:
        self._init(parse(data))

    @classmethod
    def from_tree(cls, root: SNode) -> CIsland:
        """An island over a tree built in memory (``blank_island``, tests), not read from a file."""
        island = cls.__new__(cls)
        island._init(root)
        return island

    def _init(self, root: SNode) -> None:
        self.m_root = root
        self.m_width = root.int("Width")
        self.m_height = root.int("Height")
        self.m_clime = root.int("Clime")  # 0 above water, 1 underwater
        self.m_difficulty = root.int("Difficulty")  # 0 on 132 of the 133 normal islands
        # 0 on the 133 normal islands (absent: 0, or -28 underwater); other values on the pirate towers (-3),
        # the third parties (-1, -2) and the underwater islands (-28..-33)
        self.m_sea_level = root.int("SeaLevel")

    def _ordered(self, layers: list) -> list:
        """A chunk's ``(texture GUID, ...)`` layers in 2070's paint order (``order``)."""
        if not self.m_order:
            return layers
        return sorted(layers, key=lambda layer: self.m_order.get(layer[0], 1 << 30))

    def heightmap(self, missing: float | None = None) -> list[list[float]]:
        """(width+1) x (height+1) float heights from the terrain chunks, rows = z. Chunks without a
        heightmap (open sea) get ``missing`` (default: the lowest height found). A chunk may hold
        finer samples than its corners (33 x 33: one per half tile); only those on corners are read
        (placing every sample at its rounded tile let half-tile samples overwrite the corners)."""
        terrain = self.m_root.path("Terrain")
        chunk_map = terrain.path("ChunkMap")
        chunk_width, chunk_height = chunk_map.int("Width"), chunk_map.int("Height")
        tiles_x, tiles_z = terrain.int("TileCountX"), terrain.int("TileCountZ")
        per_x, per_z = tiles_x // chunk_width, tiles_z // chunk_height
        grid: list[list[float | None]] = [[None] * (tiles_x + 1) for _ in range(tiles_z + 1)]
        low = None
        for k, element in enumerate(chunk_map.findall("Element")):
            height_map = element.find("HeightMap")
            if height_map is None or height_map.path("Data") is None:
                continue
            sample_width = height_map.int("Width")
            values = struct.unpack(f"<{sample_width * sample_width}f", height_map.path("Data").data)
            chunk_x, chunk_z = k % chunk_width, k // chunk_width
            if (sample_width - 1) % per_x == 0:  # as fine as the corners or finer: the samples on corners
                samples_per_tile = (sample_width - 1) // per_x
                samples = [
                    (i, j, i * samples_per_tile, j * samples_per_tile)
                    for j in range(per_z + 1)
                    for i in range(per_x + 1)
                ]
            else:  # coarser: each sample at its nearest corner
                step = per_x / (sample_width - 1)
                samples = [
                    (round(i * step), round(j * step), i, j) for j in range(sample_width) for i in range(sample_width)
                ]
            for corner_x, corner_z, i, j in samples:
                sample = values[j * sample_width + i]
                grid[chunk_z * per_z + corner_z][chunk_x * per_x + corner_x] = sample
                low = sample if low is None else min(low, sample)
        fill = missing if missing is not None else (low or 0.0)
        return [[fill if sample is None else sample for sample in row] for row in grid]

    def texture_layers(self) -> dict[int, list[list[int]]]:
        """Texture index -> (width+1) x (height+1) alpha (0-255), assembled from the chunks. Layers
        are painted in order over a full-coverage base layer; dict order is the paint order.

        Every chunk lists its own layers in its own order, so the order across the island is the
        topological order of all "a is painted before b" pairs (first appearance breaks ties and
        cycles). Merging the chunks in first-seen order put, for instance, n_l03's river bed sand
        (83) before its grass (52) although all 81 chunks holding both paint the sand over the
        grass, and the river bed came out grassy (2026-09-22)."""
        terrain = self.m_root.path("Terrain")
        chunk_map = terrain.path("ChunkMap")
        chunk_width = chunk_map.int("Width")
        tiles_x, tiles_z = terrain.int("TileCountX"), terrain.int("TileCountZ")
        tiles_per_chunk = tiles_x // chunk_width
        layers: dict[int, list[list[int]]] = {}
        after: dict[int, set[int]] = {}  # layer -> layers painted before it
        seen: list[int] = []
        for element in chunk_map.findall("Element"):
            order = [
                i
                for i, _ in self._ordered(
                    [(texture.int("TextureIndex"), None) for texture in element.findall("TexIndexData")]
                )
            ]
            for position, texture_index in enumerate(order):
                if texture_index not in after:
                    after[texture_index] = set()
                    seen.append(texture_index)
                after[texture_index].update(order[:position])
        for k, element in enumerate(chunk_map.findall("Element")):
            chunk_x, chunk_z = k % chunk_width, k // chunk_width
            for texture in element.findall("TexIndexData"):
                texture_index = texture.int("TextureIndex")
                alpha_map = texture.find("AlphaMap")
                alpha_width = alpha_map.int("Width")
                alphas = alpha_map.path("Data").data
                grid = layers.setdefault(texture_index, [[0] * (tiles_x + 1) for _ in range(tiles_z + 1)])
                step = tiles_per_chunk / (alpha_width - 1)
                for j in range(alpha_width):
                    for i in range(alpha_width):
                        grid[chunk_z * tiles_per_chunk + round(j * step)][
                            chunk_x * tiles_per_chunk + round(i * step)
                        ] = alphas[j * alpha_width + i]
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
        terrain = self.m_root.path("Terrain")
        chunk_map = terrain.path("ChunkMap")
        chunk_width = chunk_map.int("Width")
        tiles_per_chunk = terrain.int("TileCountX") // chunk_width
        for k, element in enumerate(chunk_map.findall("Element")):
            layers = []
            for texture in element.findall("TexIndexData"):
                alpha_map = texture.find("AlphaMap")
                alpha_width = alpha_map.int("Width")
                alphas = alpha_map.path("Data").data
                grid = [[alphas[j * alpha_width + i] for i in range(alpha_width)] for j in range(alpha_width)]
                layers.append((texture.int("TextureIndex"), grid))
            if layers:
                yield k % chunk_width, k // chunk_width, tiles_per_chunk, self._ordered(layers)

    def objects(self) -> list[SIslandObject]:
        """Every placed object of ``m_GOPManager/Objects``, by group (Nature, Simple, Handle ...)."""
        groups = self.m_root.path("m_GOPManager/Objects")
        out = []
        for group in groups.children if groups else []:
            objects = group.find("Objects")
            for game_object in objects.findall("Object") if objects else []:
                out.append(_island_object(group.name, game_object))
        return out

    def animal_paths(self) -> list[list[tuple[tuple[float, float], list[int]]]]:
        """``AnimalLayer``: per populated layer the anchor points the animals walk between, ``((x, z) in tiles, indices
        of the anchors it connects to)``. The points are 24-byte records (int64 x, z, height at 4096 per tile), the
        connections pairs of uint32 indices into them."""
        layers = self.m_root.path("AnimalLayer")
        out = []
        for layer in layers.children if layers else []:
            points = layer.find("m_AnchorPoints")
            if points is None or not points.children:
                continue
            positions = [
                (struct.unpack_from("<q", point.data, 0)[0] / FIXED, struct.unpack_from("<q", point.data, 8)[0] / FIXED)
                for point in points.children
                if point.data and len(point.data) >= 16
            ]
            links = layer.find("m_Connections")
            raw = links.data if links is not None and links.data else b""
            pairs = struct.unpack_from(f"<{len(raw) // 4}I", raw) if raw else ()
            near: list[list[int]] = [[] for _ in positions]
            for first, second in zip(pairs[0::2], pairs[1::2], strict=False):
                if first < len(positions) and second < len(positions):
                    near[first].append(second)
                    near[second].append(first)
            out.append(list(zip(positions, near, strict=True)))
        return out

    def object_groups(self) -> list[list[int]]:
        """``m_GOPManager/ObjectGroups``: lists of ``m_ID`` that 2070 treats as one (a mine slot and its pickup, a dam
        slot and its walls, a rubble slot and its ruins; island-slots)."""
        groups = self.m_root.path("m_GOPManager/ObjectGroups")
        return [
            list(
                struct.unpack_from(f"<{len(group.data) // 4}I", group.data)
            )  # a stray tail byte is ignored, as in animal_paths
            for group in (groups.children if groups else [])
            if group.data and len(group.data) >= 8
        ]

    def heightmap_v2(self) -> list[int]:
        """The logic grid heights (``m_HeightMap_v2``, width x height int16: a tile's mean corner height x 1024)."""
        data = self.m_root.path("m_GOPManager/m_GRIDManager/m_HeightMap_v2").data
        return list(struct.unpack(f"<{len(data) // 2}h", data))


def position_node(game_object: SNode) -> SNode | None:
    """The node holding an object's position (three int64: x, z, height): ``m_Position`` itself, or in the 35 older
    1404 islands its child ``data``; None where the object has none."""
    position = game_object.find("m_Position")
    if position is not None and position.data is None:
        position = position.find("data")
    return position if position is not None and position.data is not None and len(position.data) == 24 else None


def _island_object(kind: str, game_object: SNode) -> SIslandObject:
    position = position_node(game_object)
    x, z, y = struct.unpack_from("<qqq", position.data) if position is not None else (0, 0, 0)
    variation_node = game_object.find("m_Variation")
    nature = game_object.find("Nature")
    orientation_node = nature.find("m_Orientation") if nature else None
    scale_node = nature.find("m_Scale") if nature else None
    quaternion = (
        struct.unpack("<4f", orientation_node.data) if orientation_node is not None and orientation_node.data else None
    )
    scale = float(scale_node.text) if scale_node is not None and scale_node.text else None
    render_parameters = game_object.path("PropertyMesh/m_RenderParameters")
    if (
        nature is None
        and render_parameters is not None
        and render_parameters.data
        and len(render_parameters.data) >= RENDER_AT + RENDER_TRANSFORM.size
    ):
        # outside the Nature group the tilt and scale are only in the drawn transform
        *_, orientation_x, orientation_y, orientation_z, orientation_w, drawn_scale = RENDER_TRANSFORM.unpack_from(
            render_parameters.data, RENDER_AT
        )
        quaternion, scale = (orientation_x, orientation_y, orientation_z, orientation_w), drawn_scale
    return SIslandObject(
        kind=kind,
        guid=game_object.int("m_GUID"),
        x=x / FIXED,
        y=y / FIXED,
        z=z / FIXED,
        direction=game_object.int("m_Direction"),
        variation=int(variation_node.text) if variation_node is not None and variation_node.text else None,
        scale=scale,
        orientation=quaternion,
        id=game_object.int("m_ID"),
    )


def outline(node: SNode, depth: int = 0, out: list | None = None) -> list[str]:
    """Each distinct child path once, with counts (repeated siblings folded)."""
    out = [] if out is None else out
    groups: dict[str, list[SNode]] = {}
    for child in node.children:
        groups.setdefault(child.name, []).append(child)
    for name, nodes in groups.items():
        first = nodes[0]
        description = f"{'  ' * depth}{name} x{len(nodes)}"
        if first.text is not None:
            description += f" = {first.text[:40]}"
        if first.data is not None:
            description += f" [{len(first.data)} bytes] {first.data[:16].hex(' ')}"
        out.append(description)
        merged = SNode(name, children=[child for group_node in nodes for child in group_node.children])
        outline(merged, depth + 1, out)
    return out


BLANK_TEXTURES = {"water": 0, "beach": 78, "grass": 52}  # GroundTextures GUIDs: the sea bed, beach_01, gras_01
BLANK_LAND = 0.7  # 2070's building height: the plateau most of an island stands on
BLANK_SEA_BED = -40.0
BLANK_SHELF = 14.0  # tiles: the sea floor falls to within a third of the bed at this distance out
CHUNK_TILES = 16
BLANK_COAST_LINES, BLANK_COAST_POINTS = 6, 72  # harbor coast all the way round, as six lines


def blank_island(size_tiles: int, seed: int = 1) -> CIsland:
    """A new island in 2070's own form, for ``island_test.py --new``: a rounded, gently lobed
    landmass of grass at building height with a beach, sloping into the sea, no objects. It goes
    through the reader like any 2070 island, so the island editor starts from it."""
    import math
    import random

    import numpy as np

    if size_tiles % CHUNK_TILES or not 64 <= size_tiles <= 256:
        raise ValueError(f"island size {size_tiles}: a multiple of {CHUNK_TILES} from 64 to 256")
    random_source = random.Random(seed)
    lobes = [
        (k, random_source.uniform(0.04, 0.12) / k**0.5, random_source.uniform(0, 2 * math.pi)) for k in (2, 3, 5, 7)
    ]
    corner_count = size_tiles + 1
    z_grid, x_grid = np.mgrid[0:corner_count, 0:corner_count].astype(np.float64)
    center = size_tiles / 2
    angle_rad = np.arctan2(z_grid - center, x_grid - center)
    reach = 0.34 * size_tiles * (1 + sum(amplitude * np.cos(k * angle_rad + phase) for k, amplitude, phase in lobes))
    inside_tiles = reach - np.hypot(x_grid - center, z_grid - center)  # tiles inside the coast (negative: out at sea)
    heights = np.where(
        inside_tiles >= 4,
        BLANK_LAND,
        np.where(
            inside_tiles >= 0, BLANK_LAND * inside_tiles / 4, BLANK_SEA_BED * (1 - np.exp(inside_tiles / BLANK_SHELF))
        ),
    ).astype(np.float32)
    beach = np.clip((6 - inside_tiles) / 3, 0, 1) * (
        inside_tiles > -3
    )  # a sand rim along the coast, under the waterline too
    grass = np.clip((inside_tiles - 3) / 3, 0, 1)
    alphas = {
        BLANK_TEXTURES["water"]: np.full((corner_count, corner_count), 255.0),
        BLANK_TEXTURES["beach"]: beach * 255,
        BLANK_TEXTURES["grass"]: grass * 255,
    }
    terrain = terrain_node(size_tiles, size_tiles, heights, alphas)

    def shore(theta_rad: float) -> tuple[float, float]:  # a tile inside the coast, on the beach
        radius_tiles = (
            0.34 * size_tiles * (1 + sum(amplitude * math.cos(k * theta_rad + phase) for k, amplitude, phase in lobes))
            - 1
        )
        return center + radius_tiles * math.cos(theta_rad), center + radius_tiles * math.sin(theta_rad)

    ring = [shore(2 * math.pi * k / BLANK_COAST_POINTS) for k in range(BLANK_COAST_POINTS + 1)]
    points_per_line = BLANK_COAST_POINTS // BLANK_COAST_LINES
    lines = [ring[k * points_per_line : (k + 1) * points_per_line + 1] for k in range(BLANK_COAST_LINES)]
    coast = SNode("CoastBuildingLines", children=[_coast_line(line_points) for line_points in lines])
    return CIsland.from_tree(
        SNode(
            "",
            children=[
                SNode("Width", str(size_tiles)),
                SNode("Height", str(size_tiles)),
                SNode("Clime", "0"),
                coast,
                terrain,
            ],
        )
    )


def flat_island(size_tiles: int, height: float) -> CIsland:
    """An island in 2070's form with no land: every corner at ``height`` under the sea bed ground, no coast, no
    objects (a floor island between the islands of a fixed map, island-maps "The sea bed")."""
    import numpy as np

    if size_tiles % CHUNK_TILES or not 64 <= size_tiles <= 256:
        raise ValueError(f"island size {size_tiles}: a multiple of {CHUNK_TILES} from 64 to 256")
    corner_count = size_tiles + 1
    heights = np.full((corner_count, corner_count), height, np.float32)
    terrain = terrain_node(
        size_tiles, size_tiles, heights, {BLANK_TEXTURES["water"]: np.full((corner_count, corner_count), 255.0)}
    )
    return CIsland.from_tree(
        SNode(
            "",
            children=[SNode("Width", str(size_tiles)), SNode("Height", str(size_tiles)), SNode("Clime", "0"), terrain],
        )
    )


def terrain_node(width_tiles: int, height_tiles: int, heights, alphas: dict, missing: float = BLANK_SEA_BED) -> SNode:
    """2070's ``Terrain`` of chunks from corner ``heights`` (rows z, NaN: no terrain there) and texture ``alphas``
    (GUID -> corner alpha 0-255, the sea bed's laid in every chunk of a blank island). A chunk with no height has no
    ``HeightMap``; the NaN corners of one with heights read ``missing``; a texture is listed where it has any alpha."""
    import numpy as np

    cx_n, cz_n = width_tiles // CHUNK_TILES, height_tiles // CHUNK_TILES
    elements = []
    for cz in range(cz_n):
        for cx in range(cx_n):
            window = np.s_[cz * CHUNK_TILES : cz * CHUNK_TILES + 17, cx * CHUNK_TILES : cx * CHUNK_TILES + 17]
            block = np.asarray(heights[window], np.float32)
            element = SNode("Element")
            if not np.isnan(block).all():
                element.children.append(_grid_node("HeightMap", np.where(np.isnan(block), missing, block).tobytes()))
            for guid, alpha in alphas.items():
                alpha_bytes = np.asarray(alpha[window]).round().astype(np.uint8)
                if alpha_bytes.any():
                    texture_node = SNode(
                        "TexIndexData",
                        children=[SNode("TextureIndex", str(guid)), _grid_node("AlphaMap", alpha_bytes.tobytes())],
                    )
                    element.children.append(texture_node)
            elements.append(element)
    chunk_map = SNode("ChunkMap", children=[SNode("Width", str(cx_n)), SNode("Height", str(cz_n)), *elements])
    return SNode(
        "Terrain", children=[SNode("TileCountX", str(width_tiles)), SNode("TileCountZ", str(height_tiles)), chunk_map]
    )


def _coast_line(points) -> SNode:
    """A 2070 coast building line: fixed-point (x, z) points; the directions are not read."""
    point_nodes = [SNode("i", data=struct.pack("<qq", round(x * FIXED), round(z * FIXED))) for x, z in points]
    return SNode("i", children=[SNode("Points", children=point_nodes), SNode("Directions", children=[])])


def _grid_node(name: str, data: bytes) -> SNode:
    """A chunk's grid node: its width and its binary data."""
    return SNode(name, children=[SNode("Width", str(CHUNK_TILES + 1)), SNode("Data", data=data)])
