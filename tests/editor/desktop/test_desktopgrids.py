"""The cell grids' overlays (``desktopgrids``) and the slot links dialog's rows, on synthetic data."""

import os
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from annotoolbox.editor.desktop import desktopgrids, desktopslotlinks
from annotoolbox.editor.slotkinds import SSlotAsset

SESSION = SimpleNamespace(m_size_tiles=16.0, height_at=lambda x, z: 0.5 * x)


def outline_by_cells(session, cells, per, rgba, lift=0.3):
    """The outline as the first version found it, cell by cell."""
    n = len(cells)
    pad = np.pad(cells, 1, constant_values=False)
    rows = []
    for z in range(n + 1):
        for x in range(n):
            if pad[z, x + 1] != pad[z + 1, x + 1]:
                rows += desktopgrids._edge(session, (x * per, z * per), ((x + 1) * per, z * per), rgba, lift)
    for z in range(n):
        for x in range(n + 1):
            if pad[z + 1, x] != pad[z + 1, x + 1]:
                rows += desktopgrids._edge(session, (x * per, z * per), (x * per, (z + 1) * per), rgba, lift)
    return np.asarray(rows, "f4").reshape(-1, 7)


def test_the_outline_is_the_same_as_the_cell_by_cell_one():
    cells = np.random.default_rng(5).random((12, 12)) > 0.6
    got = desktopgrids.outline(SESSION, cells, 2, (1, 0, 0, 1))
    assert got.shape[0] > 0 and np.array_equal(got, outline_by_cells(SESSION, cells, 2, (1, 0, 0, 1)))


def test_no_cells_have_no_outline():
    assert desktopgrids.outline(SESSION, np.zeros((4, 4), bool), 2, (1, 0, 0, 1)).shape == (0, 7)


def test_the_slot_links_list_survives_an_object_without_a_guid():
    app = QApplication.instance() or QApplication([])
    asset = SSlotAsset(7, "Slot", "WorkAreaSlot", "iron")
    used = Counter({None: 2, 7: 1})
    dialog = desktopslotlinks.CSlotLinksDialog(None, "anno117", used, {7: asset}, {}, {}, {}, "links.json")
    assert dialog.m_guids == [7] and app is not None


class FakeShapes:
    def shapes(self, kind):
        return [{"points": []}, {"points": [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]}] if kind == "build" else []

    def coast(self, k):
        return {"points": []}


def _overlay_view():
    from annotoolbox.editor.desktop.desktopoverlays import COverlayDraw

    class FakeView(COverlayDraw):
        m_water_sel = None

        def __init__(self):
            self.m_session = SimpleNamespace(
                m_water_net={"blockers": True, "rivers": [], "lakes": [], "coasts": []},
                m_path=FakeShapes(),
                height_at=lambda x, z: 0.0,
                m_anno2070_editing=True,
            )

        def to_view(self, p):
            return np.asarray(p, float)

        def _rgba(self, name):
            return (1, 1, 1, 1)

    return FakeView()


def test_a_blocker_shape_without_points_is_skipped_not_raised():
    (_lines, rows), (_points, pts) = _overlay_view()._water_overlay()
    assert len(rows) == 6 and len(pts) == 3  # the triangle's three sides and corners


def test_go_to_takes_numbers_and_leaves_nan_and_inf_alone():
    from annotoolbox.editor.desktop.desktopeditor import CIslandDoc

    went = []
    doc = SimpleNamespace(m_view=SimpleNamespace(go_to=lambda *a: went.append(a)))
    for text in ("nan 3", "1 inf", "x y", "5"):
        CIslandDoc.goto(doc, text)
    assert went == []
    CIslandDoc.goto(doc, "10, 20")
    assert went == [(10.0, 20.0, 25.0, 0.0)]


def _panel_doc():
    """What ``CObjectsPanel`` reaches of its doc: the view's hidden set and the doc's own layer switch."""
    calls = []
    hidden = SimpleNamespace(clear=lambda: calls.append("clear"), m_foliage=False)
    view = SimpleNamespace(m_hidden=hidden, apply_hidden=lambda: calls.append("apply"))
    doc = SimpleNamespace(
        m_view=view,
        set_layer=lambda key, on: calls.append((key, on)),
        m_shell=SimpleNamespace(sync=lambda: calls.append("sync")),
    )
    return SimpleNamespace(m_doc=doc, fill=lambda: calls.append("fill")), calls


def test_show_hidden_and_hide_foliage_switch_the_layers_through_the_doc():
    from annotoolbox.editor.desktop.desktoppanels import CObjectsPanel

    panel, calls = _panel_doc()
    CObjectsPanel.show_hidden(panel)
    assert ("forest", True) in calls and ("grass", True) in calls
    panel, calls = _panel_doc()
    CObjectsPanel.set_foliage(panel, True)
    assert ("forest", False) in calls and ("grass", False) in calls and panel.m_doc.m_view.m_hidden.m_foliage


def test_a_river_point_an_undo_took_away_shows_as_no_selection():
    from annotoolbox.editor.desktop.desktoppanels import CPropertiesPanel

    river = {"points": [[0, 1.0, 0], [1, 1.0, 1]], "widths": [2.0, 2.0]}
    session = SimpleNamespace(m_water_net={"rivers": [river], "lakes": []}, river=lambda k: river)
    titles = []
    panel = SimpleNamespace(
        m_doc=SimpleNamespace(m_session=session),
        m_title=SimpleNamespace(setText=titles.append),
        _visible=lambda keys: None,
        fields={"width": SimpleNamespace(setValue=lambda v: None)},
    )
    panel.show_water = lambda w: CPropertiesPanel.show_water(panel, w)
    panel.show_water({"river": 0, "point": 2})  # was the third point
    panel.show_water({"river": 3, "point": 0})
    assert len(titles) == 2 and "River" not in titles[0] and titles[0] == titles[1]


def test_box_rows_are_twelve_edges():
    from annotoolbox.editor.desktop.desktopoverlays import box_corners, box_rows

    assert len(box_rows(box_corners((0, 0, 0), (1, 2, 3)), (1, 0, 0, 1))) == 24
