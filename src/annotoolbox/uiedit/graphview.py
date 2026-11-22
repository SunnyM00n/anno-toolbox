"""The bindings as a node graph (Qt): per binding its sources on the left, its operator in the middle, its targets
on the right, joined by curves, as Phoenix Studio shows them; the operator's menu changes or removes it.

``CBindingGraph`` is the widget; ``show_graph(graph, describe)`` fills it (``describe(end)`` gives an end's words).
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

BINDING = 0  # the data role of an operator's box: its binding
COL_W, OP_W, GAP_X, ROW_H, GAP_Y = 300.0, 150.0, 70.0, 24.0, 14.0
COLORS = {"path": (70, 110, 160), "own": (110, 80, 150), "constant": (90, 120, 80), "op": (150, 110, 50)}
FAMILY_COLORS = {"Assign": (150, 110, 50), "Numerical": (60, 130, 130), "Maths": (60, 130, 130),
                 "Comparison": (140, 80, 90), "Logical": (140, 80, 90), "Events": (160, 120, 40),
                 "Casts and text": (100, 100, 140), "Enum": (100, 120, 70), "Arrays": (100, 120, 70)}  # fmt: skip


class _CNode(QGraphicsRectItem):
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


class CBindingGraph(QGraphicsView):
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
        self.m_empty_text = tr("ui.no_bindings")

    def show_graph(self, shown: list[bindings.SBinding], describe: Callable[[bindings.SEnd], str]) -> None:
        """Draw the bindings as source, operator and target boxes in three columns, joined by lines."""
        scene = self.scene()
        scene.clear()
        self.setBackgroundBrush(QColor(theme.colors()["field"]))  # the theme's, not the view's default white
        text = QColor(theme.colors()["text"])
        if not shown:
            scene.addSimpleText(self.m_empty_text).setBrush(QBrush(text))
            return
        y = 0.0
        x_op, x_dst = COL_W + GAP_X, COL_W + GAP_X + OP_W + GAP_X
        for binding in shown:
            row_count = max(len(binding.sources), len(binding.targets), 1)
            height = row_count * ROW_H + (row_count - 1) * 4
            operator_entry = operators.OPERATORS.get(binding.operator)
            rgb = FAMILY_COLORS.get(operator_entry.family, COLORS["op"]) if operator_entry else COLORS["op"]
            tip = f"{binding.op_name}: {tr(operator_entry.description)}" if operator_entry else binding.op_name
            op_rect = QRectF(x_op, y + (height - ROW_H) / 2, OP_W, ROW_H)
            node = _CNode(
                binding.op_name + ("" if operator_entry is None or operator_entry.tested else " *"), op_rect, rgb, tip
            )
            scene.addItem(node)
            node.setData(
                BINDING, binding
            )  # the item says which binding it is (an ``id`` could be taken by a later item)
            for i, target in enumerate(binding.sources):
                rect = QRectF(0, y + i * (ROW_H + 4), COL_W, ROW_H)
                scene.addItem(_CNode(describe(target), rect, COLORS[target.kind]))
                self._curve(QPointF(rect.right(), rect.center().y()), QPointF(op_rect.left(), op_rect.center().y()))
            for i, target in enumerate(binding.targets):
                rect = QRectF(x_dst, y + i * (ROW_H + 4), COL_W, ROW_H)
                scene.addItem(_CNode(describe(target), rect, COLORS[target.kind]))
                self._curve(QPointF(op_rect.right(), op_rect.center().y()), QPointF(rect.left(), rect.center().y()))
            y += height + GAP_Y
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def _curve(self, start: QPointF, end: QPointF) -> None:
        path = QPainterPath(start)
        dx = (end.x() - start.x()) / 2
        path.cubicTo(QPointF(start.x() + dx, start.y()), QPointF(end.x() - dx, end.y()), end)
        item = QGraphicsPathItem(path)
        item.setPen(QPen(QColor(theme.colors()["dim"]), 1.2))
        item.setZValue(-1)
        self.scene().addItem(item)

    def _binding_at(self, position) -> bindings.SBinding | None:
        item = self.itemAt(position)
        while item is not None and item.data(BINDING) is None:
            item = item.parentItem()
        return item.data(BINDING) if item is not None else None

    def mousePressEvent(self, event) -> None:
        binding = self._binding_at(event.position().toPoint())
        if binding is not None and event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(binding)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        binding = self._binding_at(event.pos())
        if binding is None:
            return
        menu = QMenu(self)
        ops = menu.addMenu(tr("ui.menu.change_operator"))
        for family, members in operators.families().items():
            sub = ops.addMenu(tr(operators.FAMILY_LABELS[family]))
            for operator_entry in members:
                act = sub.addAction(
                    operator_entry.name,
                    lambda operator_hash=operator_entry.hash_hex, binding=binding: self.change.emit(
                        binding.node, operator_hash
                    ),
                )
                act.setToolTip(tr(operator_entry.description))
                act.setEnabled(operator_entry.inputs[0] <= len(binding.sources) <= operator_entry.inputs[1])
        menu.addAction(tr("ui.remove_binding"), lambda: self.remove.emit(binding.node))
        menu.exec(event.globalPos())
