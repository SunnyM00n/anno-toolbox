"""The player logo of 117's cloths (``render.clothlogo``, ``render.clothmesh``) and the view's frame skipping for many
cloths (``viewer.clothplay``), on synthetic data and, with ``ANNO117_DIR``, on the install's logos."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.models import ddsdecode, textures
from annotoolbox.render import clothlogo, clothmesh, clothsim
from annotoolbox.sources.gametree import open_game

ASSETS = """<Assets>
<Asset><Template>PlayerLogo</Template><Values><Standard><GUID>11</GUID><Name>None</Name></Standard>
<PlayerLogo /></Values></Asset>
<Asset><Template>PlayerLogo</Template><Values><Standard><GUID>12</GUID><Name>Wine</Name></Standard>
<PlayerLogo><DefaultLogo>data/ui/fhd/logos/wine_512.png</DefaultLogo><MiniLogo>x_128.png</MiniLogo></PlayerLogo>
</Values></Asset>
<Asset><Template>PlayerLogo</Template><Values><Standard><GUID>13</GUID><Name>Bull</Name></Standard>
<PlayerLogo><DefaultLogo>data/ui/fhd/logos/bull_512.png</DefaultLogo></PlayerLogo></Values></Asset>
<Asset><Template>Other</Template><Values><PlayerLogo><DefaultLogo>no.png</DefaultLogo></PlayerLogo></Values></Asset>
</Assets>"""


class _Files:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def exists(self, path: str) -> bool:
        return path in self.files

    def read(self, path: str) -> bytes:
        return self.files[path]


def _dds(tmp_path: Path, alpha: int = 255) -> bytes:
    return textures.write_solid_dds(tmp_path / "logo.dds", (255, 255, 255, alpha), 4).read_bytes()


def test_the_logo_assets_are_read_from_the_asset_text():
    found = clothlogo.logos(ASSETS)
    assert [(logo.guid, logo.name) for logo in found] == [(12, "Wine"), (13, "Bull")]  # none without a DefaultLogo
    assert found[0].path == "data/ui/fhd/logos/wine_512.png"
    assert clothlogo.texture_candidates(found[0].path) == [
        "data/ui/fhd/logos/wine_512_0.dds",
        "data/ui/4k/logos/wine_512_0.dds",
    ]


def test_the_default_logo_is_the_first_whose_file_is_there(tmp_path):
    dds = _dds(tmp_path)
    files = _Files({clothlogo.ASSETS: ASSETS.encode(), "data/ui/4k/logos/bull_512_0.dds": dds})
    assert clothlogo.has_logos(files)
    logo = clothlogo.default_logo(files)
    assert isinstance(logo, ddsdecode.DdsFile) and logo.info.width == 4
    assert clothlogo.default_logo(files, 12) is None  # that asset's file is missing
    assert clothlogo.default_logo(files, 13) is not None
    assert clothlogo.default_logo(_Files({})) is None and not clothlogo.has_logos(_Files({}))


def test_an_atlas_entry_is_the_bindless_index_with_the_srgb_bit(tmp_path):
    dds = ddsdecode.DdsFile(_dds(tmp_path))
    assert not dds.info.srgb and clothlogo.atlas_word(5, dds) == 5
    srgb = SimpleNamespace(info=SimpleNamespace(srgb=True))
    assert clothlogo.atlas_word(5, srgb) == 5 | (1 << 31)


def test_the_cloth_constant_carries_the_logos_atlas_index():
    x, y, z, w = clothmesh.cloth_constant(logo=clothlogo.LOGO_INDEX)
    assert (z >> 14) & 0xFF == clothlogo.LOGO_INDEX and z & 3 == 0  # the atlas index, not inverted or metallic
    assert (z >> 2) & 0xFFF == 0xFFF and (y, w) == (0, 0)  # the second colour stays white
    assert (clothmesh.cloth_constant()[2] >> 14) & 0xFF == 0


def _model(buffer: bool = True, keep: bool = False):
    pytest.importorskip("PySide6")
    from annotoolbox.render import gamedraw

    cbuffers = {clothlogo.ATLAS_BUFFER: object()} if buffer else {}
    return SimpleNamespace(table=gamedraw.TextureTable({}), program=SimpleNamespace(cbuffers=cbuffers),
                           keep=[object()] if keep else [], values={})  # fmt: skip


def test_a_cloth_draw_gets_the_logo_in_its_table_and_atlas(tmp_path):
    logo = ddsdecode.DdsFile(_dds(tmp_path))
    model = _model()
    assert clothmesh.attach_logo(model, logo) == clothlogo.LOGO_INDEX
    slot = model.table.slots[clothlogo.LOGO_MAP]
    assert model.values[f"{clothlogo.ATLAS}[{clothlogo.LOGO_INDEX}]"] == slot  # an unsigned format: no sRGB bit
    before = len(model.table.slots)
    assert clothmesh.attach_logo(model, logo) == clothlogo.LOGO_INDEX and len(model.table.slots) == before  # once
    assert model.table.words[slot] & 0xFFFF == 0  # the first layer of its array


def test_no_logo_without_a_texture_an_atlas_buffer_or_a_table_still_open(tmp_path):
    logo = ddsdecode.DdsFile(_dds(tmp_path))
    assert clothmesh.attach_logo(_model(), None) == 0 and clothmesh.attach_logo(_model(), False) == 0
    no_atlas = _model(buffer=False)
    assert clothmesh.attach_logo(no_atlas, logo) == 0 and clothlogo.LOGO_MAP not in no_atlas.table.slots
    made = _model(keep=True)  # its resources exist: the table cannot take another map
    assert clothmesh.attach_logo(made, logo) == 0 and made.values == {}


@pytest.mark.skipif(not os.environ.get(INSTALL_VARS["anno117"]), reason="needs ANNO117_DIR")
def test_117s_player_logos_are_in_its_files():
    tree = open_game("anno117", Path(os.environ[INSTALL_VARS["anno117"]]))
    assert clothlogo.has_logos(tree)
    found = clothlogo.logos(tree.read(clothlogo.ASSETS).decode("utf-8", "replace"))
    assert len(found) > 100
    logo = clothlogo.default_logo(tree)
    assert logo is not None and logo.info.width >= 512
    alpha = logo.level(3)[..., 3]  # the shape is the alpha: it holds both 0 and 255
    assert alpha.min() == 0 and alpha.max() > 200


def _runs(count: int, level: int = 0):
    from annotoolbox.viewer.clothplay import ClothPart, _Running

    pos, tris = np.array([(0, 0, 0), (1, 0, 0), (0, -1, 0), (1, -1, 0.0)]), [0, 1, 2, 1, 3, 2]
    data = SimpleNamespace(vertices=np.zeros((4, 8), np.float32), stored=None, groups={0: np.array(tris)})
    data.vertices[:, :3] = pos
    out = []
    for _ in range(count):
        run = _Running(data, ClothPart(clothsim.ClothParams()), np.identity(4))
        run.sim.set_level(level)
        out.append(run)
    return out


def test_every_cloth_steps_while_their_work_fits_the_budget():
    from annotoolbox.viewer.clothplay import ClothPlayer

    runs = _runs(3)
    assert ClothPlayer.schedule(runs) is None and all(r.idle == 0 for r in runs)


def test_over_the_budget_the_near_cloths_step_first_and_none_waits_for_ever(monkeypatch):
    from annotoolbox.viewer import clothplay
    from annotoolbox.viewer.clothplay import ClothPlayer

    runs = _runs(6)
    cost = clothsim.work(runs[0].sim.fine, 0)
    monkeypatch.setattr(clothplay, "VIEW_BUDGET", cost * 2 + cost // 2)  # two fit a frame
    for k, run in enumerate(runs):
        run.distance = 10.0 + 5.0 * k
    first = ClothPlayer.schedule(runs)
    assert first == {id(runs[0]), id(runs[1])}  # the two nearest
    assert [r.idle for r in runs] == [0, 0, 1, 1, 1, 1]
    stepped = set(first)
    for _ in range(40):
        stepped |= ClothPlayer.schedule(runs)
    assert stepped == {id(r) for r in runs}  # the waiting ones' turn comes: no cloth starves
    assert all(r.idle < 40 for r in runs)


def test_one_cloth_always_steps_even_when_it_alone_passes_the_budget(monkeypatch):
    from annotoolbox.viewer import clothplay
    from annotoolbox.viewer.clothplay import ClothPlayer

    monkeypatch.setattr(clothplay, "VIEW_BUDGET", 1)
    runs = _runs(3)
    assert len(ClothPlayer.schedule(runs)) == 1


def _player_item(node: str):
    from annotoolbox.viewer.clothplay import ClothPart

    pos = np.array([(c * 0.1, -r * 0.1, 0.0) for r in range(5) for c in range(3)])
    tris = np.array([r * 3 + c + d for r in range(4) for c in range(2) for t in ((0, 1, 3), (1, 4, 3)) for d in t])
    colours = np.array([(0x80, 0, 0, 0) if k < 3 else (0, 0, 0, 0) for k in range(15)], np.uint8)
    verts = np.concatenate([pos, np.zeros((15, 3)), np.zeros((15, 2))], axis=1).astype(np.float32)
    stored = SimpleNamespace(indices=tris.tolist(), elements=[SimpleNamespace(usage=5, type=5)],
                             element=lambda k: [tuple(c) for c in colours])  # fmt: skip
    part = SimpleNamespace(node=node, matrix=np.identity(4),
                           cloth=ClothPart(clothsim.ClothParams(gravity=0.5, wind_strength=0.0)))  # fmt: skip
    vbo = SimpleNamespace(writes=0, write=lambda data: setattr(vbo, "writes", vbo.writes + 1))
    data = SimpleNamespace(vertices=verts, stored=stored, groups={0: tris})
    return part, {"data": data, "mesh": 0, "verts": verts, "vbo": vbo}


def test_a_skipped_cloth_keeps_its_pose_and_is_not_written_again(monkeypatch):
    from annotoolbox.viewer import clothplay
    from annotoolbox.viewer.clothplay import ClothPlayer

    cloths = [_player_item(f"Clothes/{k}") for k in range(4)]
    player, state = ClothPlayer(), SimpleNamespace(cloth=True, time=0.0, torn=False)

    def frame(k: int) -> None:
        state.time = k / 30
        player.next_frame((0.0, 0.0, 5.0), 0.75)
        for part, item in cloths:
            player.pose(part, item, state, np.identity(4), None)

    frame(0)
    frame(1)  # every cloth fits: stepped and written each frame
    assert [item["vbo"].writes for _p, item in cloths] == [2, 2, 2, 2]
    cost = clothsim.work(player.running[("Clothes/0", 0)].sim.fine, 0)
    monkeypatch.setattr(clothplay, "VIEW_BUDGET", cost * 2)  # two of the four fit
    for k in range(2, 14):
        frame(k)
    writes = [item["vbo"].writes for _p, item in cloths]
    assert max(writes) < 2 + 12 and min(writes) > 2  # each waits some frames, none for ever
    versions = [item["cloth_version"] for _p, item in cloths]
    assert versions == writes  # only a new pose is a new version: the mesh is not uploaded again for nothing
    steps = [r.poses for r in player.running.values()]
    assert sum(steps) < 4 * 14 and min(steps) > 2
