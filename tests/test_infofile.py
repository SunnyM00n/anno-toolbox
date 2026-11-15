"""``models/infofile``: an info file's points in both number styles (floats, the older games' fixed point)."""

from __future__ import annotations

from annotoolbox.models import cfgdoc, infofile

FLOATS = """<Info><BuildBlocker><Position><xf>-2</xf><zf>-1.5</zf></Position><Position><xf>-2</xf><zf>1</zf></Position>
<Position><xf>2</xf><zf>1</zf></Position></BuildBlocker><Other><Position><xf>0</xf><zf>0</zf></Position></Other></Info>"""
FIXED = """<Info><BuildBlocker><Position><x>-8192</x><z>-4096</z></Position><Position><x>-8192</x><z>4096</z></Position>
<Position><x>8192</x><z>4096</z></Position></BuildBlocker></Info>"""
MIXED = """<Info><BuildBlocker><Position><xf>1.5</xf><z>4096</z></Position><Position><zf>2</zf><xf>3</xf></Position>
<Position><x>-4096</x><z>-8192</z></Position></BuildBlocker></Info>"""


def test_a_footprint_reads_in_tiles_in_both_styles_and_keeps_polygons_of_three_points():
    assert infofile.footprint(FLOATS) == [[(-2.0, -1.5), (-2.0, 1.0), (2.0, 1.0)]]
    assert infofile.footprint(FIXED) == [[(-2.0, -1.0), (-2.0, 1.0), (2.0, 1.0)]]
    assert (
        infofile.footprint("<Info><BuildBlocker><Position><xf>0</xf><zf>0</zf></Position></BuildBlocker></Info>") == []
    )
    assert infofile.footprint("no tags at all") == []


def test_a_point_mixing_styles_reads_each_leaf_by_its_name_and_any_order():
    assert infofile.footprint(MIXED) == [[(1.5, 1.0), (3.0, 2.0), (-1.0, -2.0)]]  # zf before xf reads too


def test_the_style_comes_from_the_points_else_the_game():
    assert infofile.fixed_style(cfgdoc.Doc.read(FLOATS.encode()), older_game=True) is False
    assert infofile.fixed_style(cfgdoc.Doc.read(FIXED.encode()), older_game=False) is True
    assert infofile.fixed_style(cfgdoc.Doc.read(b"<Info><A>1</A></Info>"), older_game=True) is True
    assert infofile.fixed_style(cfgdoc.Doc.read(b"<Info><A>1</A></Info>"), older_game=False) is False


def test_values_and_texts_round_trip_in_each_style():
    doc = cfgdoc.Doc.read(FIXED.encode())
    area = next(n for n in doc.root.iter() if n.name == infofile.FOOTPRINT)
    position = area.children[0]
    leaf = infofile.coordinate(position, "x")
    assert leaf is not None and infofile.value(leaf, True) == -2.0 and infofile.value(leaf, False) == -8192.0
    assert infofile.value(None, True, 7.0) == 7.0
    assert infofile.points(area, True)[1] == (-2.0, 1.0)
    assert infofile.text(-2.5, True) == "-10240" and infofile.text(1.25, False) == "1.25"
    assert infofile.text(-0.00001, False) == "0" and infofile.text(3.0, False) == "3"
    assert infofile.FIXED == 4096.0
