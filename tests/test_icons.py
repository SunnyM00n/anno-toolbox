from pathlib import Path

import pytest

from annotoolbox.sources.icons2070 import IconTable
from annotoolbox.sources.overlay import SourceTree

PIL = pytest.importorskip("PIL.Image")

ICON_FILE_MAP = """<IconFileMap>
  <IconFile><IsGameIcon>1</IsGameIcon><IconFilename>data/graphics/ui/icons/atlas.png</IconFilename>
    <IconWidth>4</IconWidth><IconHeight>4</IconHeight><IconFileID>30</IconFileID></IconFile>
  <IconFile><IsGameIcon>0</IsGameIcon><IconFilename>Tools/x.png</IconFilename>
    <IconWidth>4</IconWidth><IconHeight>4</IconHeight><IconFileID>1000</IconFileID></IconFile>
</IconFileMap>"""

ICONS = """<Icons>
  <i><GUID>10275</GUID><Icons><i><IconFileID>30</IconFileID><IconIndex>5</IconIndex></i></Icons></i>
  <i><GUID>10276</GUID><Icons><i><IconFileID>1000</IconFileID><IconIndex>0</IconIndex></i></Icons></i>
  <i><GUID>10277</GUID><Icons><i><IconFileID>30</IconFileID><IconIndex>99</IconIndex></i></Icons></i>
  <i><GUID>10037</GUID><Icons><i><IconFileID>30</IconFileID></i></Icons></i>
</Icons>"""


@pytest.fixture
def icon_root(tmp_path: Path) -> Path:
    root = tmp_path / "src" / "Data1"
    (root / "data/config/game").mkdir(parents=True)
    (root / "data/config/gui").mkdir(parents=True)
    (root / "data/graphics/ui/icons").mkdir(parents=True)
    (root / "data/config/game/icons.xml").write_text(ICONS)
    (root / "data/config/gui/iconfilemap.xml").write_text(ICON_FILE_MAP)
    # 3 tiles per row (12 px wide), 2 rows: tile 5 is row 1, column 2 -> painted red.
    atlas = PIL.new("RGBA", (12, 8), (0, 0, 0, 0))
    for x in range(8, 12):
        for y in range(4, 8):
            atlas.putpixel((x, y), (255, 0, 0, 255))
    atlas.save(root / "data/graphics/ui/icons/atlas.png")
    return tmp_path / "src"


def test_icon_export(icon_root: Path, tmp_path: Path):
    tree = SourceTree(icon_root)
    table = IconTable(tree, tmp_path / "work")
    assert table.lookup(10275) is not None
    assert table.lookup(10276) is None  # tool-only atlas, not a game icon
    assert table.lookup(99999) is None
    out = table.export(10275, tmp_path / "out" / "10275.png", scale=1)
    assert out is not None
    img = PIL.open(out)
    assert img.size == (4, 4) and img.getpixel((1, 1)) == (255, 0, 0, 255)
    assert table.export(10277, tmp_path / "out" / "x.png") is None  # index beyond the atlas
    scaled = table.export(10275, tmp_path / "out" / "big.png", scale=2)
    assert PIL.open(scaled).size == (8, 8)


def test_icon_table_without_files(tmp_path: Path):
    (tmp_path / "Data0" / "data").mkdir(parents=True)
    table = IconTable(SourceTree(tmp_path), tmp_path / "work")
    assert table.lookup(1) is None and table.export(1, tmp_path / "x.png") is None


def test_missing_icon_index_is_tile_zero(icon_root: Path, tmp_path: Path):
    """The sawmill's and the Wood good's entries name an atlas and no index: 2070 reads tile 0."""
    table = IconTable(SourceTree(icon_root), tmp_path / "work")
    ref = table.lookup(10037)
    assert ref is not None and ref.index == 0
    assert table.export(10037, tmp_path / "out" / "10037.png", scale=1) is not None


def test_an_atlas_without_a_tile_size_is_left_out_not_a_division_by_zero():
    import xml.etree.ElementTree as ET

    from annotoolbox.sources.icons2070 import parse_atlases

    root = ET.fromstring(
        "<M><IconFile><IconFileID>1</IconFileID><IconFilename>a.png</IconFilename><IconWidth>0</IconWidth>"
        "<IconHeight>0</IconHeight></IconFile><IconFile><IconFileID>2</IconFileID><IconFilename>b.png</IconFilename>"
        "<IconWidth>8</IconWidth><IconHeight>8</IconHeight></IconFile></M>"
    )
    assert list(parse_atlases(root)) == [2]
