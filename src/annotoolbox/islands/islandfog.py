"""2070's fog banks of an island, read and mapped to the island's grid.

2070 puts a rectangle of tiles and a setting name (``fogbanksettings.xml``: colour, density, height, lifetime,
velocity, size, deflection) on each fog bank. 117's placed ``SFogBank`` object draws nothing; what it draws is the island's fog map (``rd3d`` ``Fog``), so the banks are read, moved into the grid, kept as
records (``builder.fog_banks``) and painted into that map (``paint_fog_map``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FOGBANK_SETTINGS = "data/config/engine/fogbanksettings.xml"
# the banks are painted into the island's fog map; False keeps them as records only
FOG_MAP = True
TEXEL = 4  # tiles per fog map texel, both ways
# the scales of 2070's numbers in the map's bytes are not settled (the shader constants were not found): the largest
# ordinary 2070 Density (3) is a full byte, a bank's height (HeightMin + mean Size, tiles) of 16 a full factor;
# MIN_FACTOR is the shader's floor (0.05 of 255, rounded up)
DENSITY_FULL = 3.0
HEIGHT_FULL = 16.0
MIN_FACTOR = 13
FIELDS = (
    "Density",
    "HeightMin",
    "LifetimeMin",
    "LifetimeMax",
    "VelocityMin",
    "VelocityMax",
    "SizeMin",
    "SizeMax",
    "DeflectionMin",
    "DeflectionMax",
)


@dataclass(frozen=True)
class SFogBank:
    setting: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom: tiles of the converted grid
    values: dict[str, float]  # the setting's numbers, and ``Color`` as (r, g, b, a)


def read_settings(tree) -> dict[str, dict]:
    """Setting name -> its values from 2070's ``fogbanksettings.xml`` (``tree``: the ``SourceTree``)."""
    text = tree.read_bytes(FOGBANK_SETTINGS).decode("utf-8", "ignore")
    out: dict[str, dict] = {}
    for block in re.findall(r"<FogbankSetting>(.*?)</FogbankSetting>", text, re.S):
        name = re.search(r"<Name>([^<]*)</Name>", block)
        if not name:
            continue
        values: dict = {}
        for field in FIELDS:
            match = re.search(rf"<{field}>([^<]*)</{field}>", block)
            if match:
                values[field] = float(match.group(1))
        colour = re.search(r"<Color>(.*?)</Color>", block, re.S)
        if colour:
            values["Color"] = tuple(
                float(re.search(rf"<{channel}>([^<]*)</{channel}>", colour.group(1)).group(1)) for channel in "rgba"
            )
        out[name.group(1)] = values
    return out


def fog_banks(island, offset: tuple[int, int], settings: dict[str, dict] | None = None) -> list[SFogBank]:
    """The island's fog banks, their rectangles moved by ``offset`` (``pad_x``, ``pad_z``) into the grid."""
    node = island.m_root.path("Fogbanks")
    px, pz = offset
    out = []
    for item in node.children if node is not None else []:
        rect = item.find("Rect")
        if rect is None:
            continue
        x0, z0, x1, z1 = (rect.int(k) for k in ("left", "top", "right", "bottom"))
        name = item.value("Setting", "")
        out.append(SFogBank(name, (x0 + px, z0 + pz, x1 + px, z1 + pz), dict((settings or {}).get(name, {}))))
    return out


def texel_bytes(bank: SFogBank) -> tuple[int, int, tuple[int, int, int]]:
    """A bank's fog texel: ``(density, height factor, rgb)``, bytes. Density = 2070's ``Density`` over ``DENSITY_FULL``;
    height factor = the bank's ``HeightMin`` plus its mean ``Size`` over ``HEIGHT_FULL`` (at least ``MIN_FACTOR``, the
    shader's floor); the colour is the setting's rgb (its alpha has no field in the map). The two scales are open
    (``DENSITY_FULL``, ``HEIGHT_FULL``)."""
    values = bank.values
    density = round(255 * min(1.0, max(0.0, values.get("Density", 0.0)) / DENSITY_FULL))
    height = values.get("HeightMin", 0.0) + (values.get("SizeMin", 0.0) + values.get("SizeMax", 0.0)) / 2
    factor = max(MIN_FACTOR, round(255 * min(1.0, max(0.0, height) / HEIGHT_FULL)))
    rgb = tuple(round(255 * min(1.0, max(0.0, channel))) for channel in values.get("Color", (1.0, 1.0, 1.0, 1.0))[:3])
    return density, factor, rgb  # type: ignore[return-value]


def paint_fog_map(data: bytes, banks: list[SFogBank], land) -> bytes:
    """The island's ``Fog`` block (``(grid / 4)²`` texels of 8 bytes, row-major, row = z, column = x, texel
    ``(i, j)`` over tiles ``4i..4i+3`` x ``4j..4j+3``) with the banks painted in.

    Everything the block held is cleared first (it described the donor island): no fog, white, type 0. A bank covers the
    texels its rectangle touches; over land (``land``, the grid's tile mask, at least half of a texel's tiles) it sets
    the ground fog bytes 0-1, over the sea the sea-level fog bytes 2-3. Where banks overlap, the denser one sets
    density, factor and colour. Returns the new block; an unusable size returns ``data`` unchanged."""
    import numpy as np

    side = int(round((len(data) / 8) ** 0.5))
    if side * side * 8 != len(data):
        return data
    out = np.zeros((side, side, 8), np.uint8)
    out[..., 4:7] = 255
    tiles = np.asarray(land, float)
    if tiles.shape[0] % side == 0 and tiles.shape[1] % side == 0:
        k = tiles.shape[0] // side
        ground = tiles.reshape(side, k, side, k).mean((1, 3)) >= 0.5
    else:
        ground = np.ones((side, side), bool)
    best = np.zeros((side, side), np.int32)
    for bank in sorted(banks, key=lambda fog_bank: texel_bytes(fog_bank)[0]):
        density, factor, rgb = texel_bytes(bank)
        x0, z0, x1, z1 = bank.rect
        cols = slice(max(0, x0 // TEXEL), min(side, -(-x1 // TEXEL)))
        rows = slice(max(0, z0 // TEXEL), min(side, -(-z1 // TEXEL)))
        if cols.start >= cols.stop or rows.start >= rows.stop or density == 0:
            continue
        win = out[rows, cols]
        sel = density >= best[rows, cols]
        on_land = ground[rows, cols]
        for layer, mask in ((0, sel & on_land), (2, sel & ~on_land)):
            win[..., layer][mask] = density
            win[..., layer + 1][mask] = factor
        win[..., 4:7][sel] = rgb
        win[..., 7][sel] = 0
        best[rows, cols] = np.where(sel, density, best[rows, cols])
    return out.tobytes()


def apply(builder) -> dict:
    """Keep the island's fog banks on ``builder.fog_banks``, count them by setting in its report and, with
    ``FOG_MAP`` on, paint them into the island's ``rd3d`` ``Fog`` map (``paint_fog_map``)."""
    island = getattr(builder, "island", None)
    if (
        getattr(island, "m_root", None) is None
    ):  # a vanilla island of the editor has no banks (a wrapped 2070 island has)
        return {}
    banks = fog_banks(island, (builder.pad_x, builder.pad_z), getattr(builder, "fog_settings", None))
    builder.fog_banks = banks
    counts: dict[str, int] = {}
    for bank in banks:
        counts[bank.setting] = counts.get(bank.setting, 0) + 1
    if counts:
        builder.report["fog banks"] = counts
    fog = builder.rd3d.find("Fog") if FOG_MAP and banks else None
    if fog is not None and fog.data:
        import numpy as np

        fog.data = paint_fog_map(fog.data, banks, builder.land_mask())
        raw = np.frombuffer(fog.data, np.uint8)
        builder.report["fog map texels"] = int(((raw[0::8] > 0) | (raw[2::8] > 0)).sum())
    return counts
