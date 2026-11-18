"""The Flash UI preview's drawing rules on small synthetic movies: colour transforms, masks, blend modes, pictures
the code colours or the game renders, missing pictures and clipped bitmap fills."""

import os
import struct

import pytest

from annotoolbox.formats import feuread, feuwrite
from annotoolbox.formats.feuread import ExternalImage, Matrix, Tag
from annotoolbox.formats.feuwrite import BitWriter, encode_matrix, encode_rect

SWATCH = "ExtractedFireFiles\\pngs\\ComponentLibraryAssets\\Textures\\ColorScheme\\color_base_light.png"
ICON = "ExtractedFireFiles\\pngs\\UIAssets\\icon_white.png"
LIVE = "ExtractedFireFiles\\pngs\\rtt_PortraitHome.png"


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])
    from annotoolbox.flashui import uirender

    return uirender, app


def _rect_shape(cid: int, rgb=None, bitmap=None, kind=0x43, size=(80, 40)) -> bytes:
    """A rectangle at the origin (twips), solid ``rgb`` or filled with a picture (one pixel = one unit of 20)."""
    body = struct.pack("<H", cid) + encode_rect((0, size[0], 0, size[1])) + bytes([1])
    if bitmap is None:
        body += bytes([0]) + bytes(rgb)
    else:
        body += bytes([kind]) + struct.pack("<H", bitmap) + encode_matrix(Matrix(20, 0, 0, 20))
    w = BitWriter()
    w.u(0, 8)
    w.u(1, 4)
    w.u(0, 4)
    w.u(0, 1)
    w.u(0b00101, 5)
    w.u(1, 5)
    w.u(0, 1)
    w.u(0, 1)
    w.u(1, 1)
    for vertical, delta in ((0, size[0]), (1, size[1]), (0, -size[0]), (1, -size[1])):
        w.u(1, 1)
        w.u(1, 1)
        w.u(7, 4)
        w.u(0, 1)
        w.u(vertical, 1)
        w.u(delta & 0x1FF, 9)
    w.u(0, 6)
    return body + w.bytes()


def _cxform(mult=None, add=None, bits=10) -> bytes:
    w = BitWriter()
    w.u(1 if add else 0, 1)
    w.u(1 if mult else 0, 1)
    w.u(bits, 4)
    for group in (mult, add):
        for v in group or ():
            w.u(v & ((1 << bits) - 1), bits)
    return w.bytes()


def _place(char, depth, matrix=None, cxform=b"", clip=None) -> bytes:
    flags = 0x02 | (0x04 if matrix else 0) | (0x08 if cxform else 0) | (0x40 if clip else 0)
    body = bytes([flags]) + struct.pack("<H", depth) + struct.pack("<H", char)
    body += (encode_matrix(matrix) if matrix else b"") + cxform
    return body + (struct.pack("<H", clip) if clip else b"")


def _movie(*tags: Tag) -> feuread.Movie:
    frame = encode_rect((0, 400, 0, 200)) + struct.pack("<HH", 30 << 8, 1)
    end = [Tag(feuread.SHOW_FRAME, b""), Tag(feuread.END, b"")]
    return feuread.read(feuwrite.write(feuread.Movie(feuread.SIGNATURE, 12, frame, [*tags, *end])))


def _image_tag(cid: int, path: str, w: int, h: int) -> Tag:
    return Tag(feuread.IMAGE, feuwrite.encode_image(ExternalImage(cid, w, h, path, 0, 0), feuread.IMAGE))


def _picture(w, h, rgba):
    from PySide6.QtGui import QColor, QImage

    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(*rgba))
    return img


def test_a_placement_reads_its_clip_depth_and_blend_mode():
    body = bytes([0x46, 0x02]) + struct.pack("<HH", 3, 9) + encode_matrix(Matrix()) + struct.pack("<H", 5) + bytes([3])
    p = feuread.read_placement(Tag(feuread.PLACE_OBJECT3, body))
    assert (p.depth, p.char_id, p.clip_depth, p.blend) == (3, 9, 5, 3)


def test_a_colour_transform_colours_solid_fills_and_composes_through_sprites(qt):
    uirender, _ = qt
    cx = feuread.ColorTransform((0.0, 0.0, 0.0, 1.0), (0, 0, 255, 0))
    movie = _movie(
        Tag(feuread.DEFINE_SHAPE, _rect_shape(1, (255, 0, 0))),
        Tag(feuread.PLACE_OBJECT2, _place(1, 1, cxform=_cxform((0, 0, 0, 256), (0, 0, 255, 0)))),
    )
    image, _ = uirender.render(movie)
    assert image.pixelColor(1, 1).getRgb() == (0, 0, 255, 255)  # mult 0 and add 255 turn red into blue
    half = feuread.ColorTransform((0.5, 0.5, 0.5, 1.0), (10, 10, 10, 0))
    both = uirender.compose(half, cx)  # the sprite's transform seen through its parent's
    assert both.mult[:3] == (0.0, 0.0, 0.0) and both.add[:3] == (10, 10, 10 + 128 - 0)  # 0.5 * 255 + 10 = 138
    assert uirender.tint(half, (200, 100, 0, 255)) == (110, 60, 10, 255)


def test_a_colour_transform_colours_pictures_and_keeps_their_alpha(qt):
    uirender, _ = qt
    from PySide6.QtGui import QColor

    img = _picture(2, 1, (200, 100, 50, 128))
    out = uirender.tinted(img, feuread.ColorTransform((0.5, 1.0, 0.0, 1.0), (0, 0, 40, 0)))
    c = QColor(out.pixelColor(0, 0))
    assert (c.red(), c.green(), c.blue(), c.alpha()) == (100, 100, 40, 128)
    assert uirender.tinted(img, uirender.SAME) is img


def test_a_mask_clips_the_depths_it_covers_and_is_not_drawn_itself(qt):
    uirender, _ = qt
    movie = _movie(
        Tag(feuread.DEFINE_SHAPE, _rect_shape(1, (255, 0, 0))),
        Tag(feuread.DEFINE_SHAPE, _rect_shape(2, (0, 255, 0))),
        Tag(feuread.PLACE_OBJECT2, _place(2, 1, Matrix(0.5, 0, 0, 1.0), clip=2)),  # the mask: the left half
        Tag(feuread.PLACE_OBJECT2, _place(1, 2)),
        Tag(feuread.PLACE_OBJECT2, _place(1, 3, Matrix(tx=200))),  # above the mask's range: not clipped
    )
    image, _ = uirender.render(movie)
    assert image.pixelColor(0, 1).getRgb() == (255, 0, 0, 255)
    assert image.pixelColor(3, 1).alpha() == 0  # clipped away
    assert image.pixelColor(11, 1).getRgb() == (255, 0, 0, 255)  # depth 3 is outside the mask


def test_a_blend_mode_composes_and_one_qt_lacks_is_named(qt):
    uirender, _ = qt
    movie = _movie(
        Tag(feuread.DEFINE_SHAPE, _rect_shape(1, (100, 100, 100))),
        Tag(feuread.PLACE_OBJECT2, _place(1, 1)),
        Tag(
            feuread.PLACE_OBJECT3, bytes([0x06, 0x02]) + struct.pack("<HH", 2, 1) + encode_matrix(Matrix()) + bytes([8])
        ),
        Tag(
            feuread.PLACE_OBJECT3,
            bytes([0x06, 0x02]) + struct.pack("<HH", 3, 1) + encode_matrix(Matrix(tx=100)) + bytes([10]),
        ),
    )
    image, r = uirender.render(movie)
    assert image.pixelColor(1, 1).red() == 200  # "add" doubles the grey
    assert r.unsupported == {"blend mode 10"}


def test_a_white_base_the_code_colours_is_tinted_blue_grey_with_its_reason_not_a_white_box(qt):
    uirender, _ = qt
    movie = _movie(_image_tag(1, SWATCH, 4, 4), _image_tag(2, ICON, 4, 4),
                   Tag(feuread.PLACE_OBJECT2, _place(1, 1)),
                   Tag(feuread.PLACE_OBJECT2, _place(2, 2, Matrix(tx=100))))  # fmt: skip
    solid = _picture(4, 4, (229, 229, 229, 255))
    icon = _picture(4, 4, (255, 255, 255, 255))
    icon.setPixelColor(0, 0, icon.pixelColor(0, 0).fromRgb(255, 255, 255, 0))  # an icon has transparent corners
    image, r = uirender.render(movie, images=lambda img: solid if img.path == SWATCH else icon)
    assert uirender.REASONS[uirender.SWATCH] in r.unsupported and len(r.unsupported) == 1
    r_, g_, b_ = image.pixelColor(2, 2).getRgb()[:3]
    assert b_ > r_ and (r_, g_, b_) != (229, 229, 229)  # the placeholder blue-grey, its shade kept
    assert image.pixelColor(7, 2).getRgb() == (255, 255, 255, 255)  # the icon stays as it is
    assert not uirender._flat_white(solid) and uirender._flat_white(solid, 200)


def test_a_picture_the_game_renders_live_is_hatched_and_crossed(qt):
    uirender, _ = qt
    movie = _movie(_image_tag(1, LIVE, 8, 8), Tag(feuread.PLACE_OBJECT2, _place(1, 1)))
    image, r = uirender.render(movie, images=lambda img: _picture(8, 8, (255, 255, 255, 255)))
    assert r.unsupported == {uirender.REASONS[uirender.LIVE]}
    assert image.pixelColor(3, 3).getRgb()[:3] != (255, 255, 255)


def test_a_missing_picture_is_a_crossed_placeholder_and_is_counted(qt):
    uirender, _ = qt
    movie = _movie(_image_tag(1, ICON, 8, 8), Tag(feuread.PLACE_OBJECT2, _place(1, 1)))
    image, r = uirender.render(movie, images=lambda img: None)
    assert r.missing == {ICON}
    assert image.pixelColor(4, 4).red() > image.pixelColor(4, 4).blue()  # the red cross crosses the middle


def test_a_clipped_bitmap_fill_is_drawn_once_and_extends_its_edge_pixels(qt):
    uirender, _ = qt
    movie = _movie(_image_tag(2, ICON, 2, 2), Tag(feuread.DEFINE_SHAPE, _rect_shape(1, bitmap=2, size=(160, 40))),
                   Tag(feuread.PLACE_OBJECT2, _place(1, 1)))  # fmt: skip
    pic = _picture(2, 2, (10, 20, 30, 255))
    pic.setPixelColor(1, 0, pic.pixelColor(1, 0).fromRgb(200, 0, 0, 255))  # the right column of the top row
    image, _ = uirender.render(movie, images=lambda img: pic)
    assert image.pixelColor(0, 0).getRgb() == (10, 20, 30, 255)
    assert image.pixelColor(1, 0).getRgb() == (200, 0, 0, 255)
    assert image.pixelColor(6, 0).getRgb() == (200, 0, 0, 255)  # beyond the picture: its edge, not a second tile
    assert image.pixelColor(6, 1).getRgb() == (10, 20, 30, 255)
