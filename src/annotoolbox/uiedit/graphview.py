"""The bindings as a node graph (Qt): per binding its sources on the left, its operator in the middle, its targets
on the right, joined by curves, as Phoenix Studio shows them; the operator's menu changes or removes it.

``BindingGraph`` is the widget; ``show_graph(graph, describe)`` fills it (``describe(end)`` gives an end's words).
docs/tool-suite.md section 5, point 2; docs/ui-editor.md, "Bindings".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QMenu,
)

from ..infra.i18n import tr
from ..suite import theme
from . import bindings, operators

COL_W, OP_W, GAP_X, ROW_H, GAP_Y = 300.0, 150.0, 70.0, 24.0, 14.0
COLORS = {"path": (70, 110, 160), "own": (110, 80, 150), "constant": (90, 120, 80), "op": (150, 110, 50)}
FAMILY_COLORS = {"Assign": (150, 110, 50), "Numerical": (60, 130, 130), "Maths": (60, 130, 130),
                 "Comparison": (140, 80, 90), "Logical": (140, 80, 90), "Events": (160, 120, 40),
                 "Casts and text": (100, 100, 140), "Enum": (100, 120, 70), "Arrays": (100, 120, 70)}  # fmt: skip


class _Node(QGraphicsRectItem):
    def __init__(self, text: str, rect: QRectF, rgb: tuple[int, int, int], tip: str = "") -> None:
        super().__init__(rect)
        self.setBrush(QBrush(QColor(*rgb)))
        self.setPen(QPen(QColor(*rgb).lighter(140), 1))
        label = QGraphicsSimpleTextItem(text, self)
        label.setBrush(QBrush(QColor(240, 240, 240)))
        font = QFont()
        font.setPointSizeF(8.5)
        label.setFont(font)
        label.setPos(rect.x() + 6, rect.y() + (rect.height() - label.boundingRect().height()) / 2)
        self.setToolTip(tip or text)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)


class BindingGraph(QGraphicsView):
    """``picked(binding object)``: an operator clicked; ``change(binding, operator hash)``, ``remove(binding)``:
    asked for in the operator's menu."""

    picked = Signal(object)
    change = Signal(object, str)
    remove = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._ops: dict[int, bindings.Binding] = {}
        self.empty_text = tr("ui.no_bindings")

    def show_graph(self, shown: list[bindings.Binding], describe: Callable[[bindings.End], str]) -> None:
        """Draw the bindings as source, operator and target boxes in three columns, joined by lines."""
        scene = self.scene()
        scene.clear()
        self.setBackgroundBrush(QColor(theme.colors()["field"]))  # the theme's, not the view's default white
        text = QColor(theme.colors()["text"])
        self._ops = {}
        if not shown:
            scene.addSimpleText(self.empty_text).setBrush(QBrush(text))
            return
        y = 0.0
        x_op, x_dst = COL_W + GAP_X, COL_W + GAP_X + OP_W + GAP_X
        for b in shown:
            n = max(len(b.sources), len(b.targets), 1)
            height = n * ROW_H + (n - 1) * 4
            op = operators.OPERATORS.get(b.operator)
            rgb = FAMILY_COLORS.get(op.family, COLORS["op"]) if op else COLORS["op"]
            tip = f"{b.op_name}: {tr(op.doc)}" if op else b.op_name
            op_rect = QRectF(x_op, y + (height - ROW_H) / 2, OP_W, ROW_H)
            node = _Node(b.op_name + ("" if op is None or op.tested else " *"), op_rect, rgb, tip)
            scene.addItem(node)
            self._ops[id(node)] = b
            for i, e in enumerate(b.sources):
                r = QRectF(0, y + i * (ROW_H + 4), COL_W, ROW_H)
                scene.addItem(_Node(describe(e), r, COLORS[e.kind]))
                self._curve(QPointF(r.right(), r.center().y()), QPointF(op_rect.left(), op_rect.center().y()))
            for i, e in enumerate(b.targets):
                r = QRectF(x_dst, y + i * (ROW_H + 4), COL_W, ROW_H)
                scene.addItem(_Node(describe(e), r, COLORS[e.kind]))
                self._curve(QPointF(op_rect.right(), op_rect.center().y()), QPointF(r.left(), r.center().y()))
            y += height + GAP_Y
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def _curve(self, a: QPointF, b: QPointF) -> None:
        path = QPainterPath(a)
        dx = (b.x() - a.x()) / 2
        path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
        item = QGraphicsPathItem(path)
        item.setPen(QPen(QColor(theme.colors()["dim"]), 1.2))
        item.setZValue(-1)
        self.scene().addItem(item)

    def _binding_at(self, pos) -> bindings.Binding | None:
        item = self.itemAt(pos)
        while item is not None and id(item) not in self._ops:
            item = item.parentItem()
        return self._ops.get(id(item)) if item is not None else None

    def mousePressEvent(self, event) -> None:
        b = self._binding_at(event.position().toPoint())
        if b is not None and event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(b)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        b = self._binding_at(event.pos())
        if b is None:
            return
        menu = QMenu(self)
        ops = menu.addMenu(tr("ui.menu.change_operator"))
        for family, members in operators.families().items():
            sub = ops.addMenu(tr(operators.FAMILY_LABELS[family]))
            for op in members:
                act = sub.addAction(op.name, lambda h=op.hash, b=b: self.change.emit(b.node, h))
                act.setToolTip(tr(op.doc))
                act.setEnabled(op.inputs[0] <= len(b.sources) <= op.inputs[1])
        menu.addAction(tr("ui.remove_binding"), lambda: self.remove.emit(b.node))
        menu.exec(event.globalPos())
