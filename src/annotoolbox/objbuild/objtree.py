"""The object tree: the config's lists and parts with the studio's titles, then the info file's boxes and areas and
the feedback file's dummies; the lists a part may hold but has not yet are rows too, so a first part can be added.

``ObjectTree.show_object(obj)`` fills it; ``picked`` gives the selected row as a ``Row``; ``select(role, path)``
makes a part current. docs/tool-suite.md section 23, "The window".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..infra.i18n import N_, tr
from ..models import cfgmcf, cfgparts
from ..models.cfgdoc import Part, is_config
from .objdoc import ObjectDoc

ROW = Qt.ItemDataRole.UserRole + 1
ROLE_TITLES = {"cfg": N_("Config"), "ifo": N_("Info"), "fc": N_("Feedback")}
EXPAND = 12  # a list with more parts starts folded


@dataclass(frozen=True)
class Row:
    """What a row stands for: a ``part`` (its path), a ``list`` (the list's path, ``section``) or an ``empty`` list
    the part at ``path`` may hold; ``role`` the file."""

    role: str
    kind: str
    path: str
    section: str = ""


def label(part: Part, mcf: bool = False) -> str:
    """A part's row text; in a 1701 mesh config (``mcf``) by the node's class type (``models.cfgmcf``)."""
    found = cfgmcf.label(part) if mcf else None
    if found:
        return found
    name = part.name
    return f"{part.kind}  {name}".rstrip() if part.kind != name else part.kind


class ObjectTree(QTreeWidget):
    picked = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self.currentItemChanged.connect(lambda cur, _prev: self.picked.emit(cur.data(0, ROW) if cur else None))

    def show_object(self, obj: ObjectDoc) -> None:
        """Fill the tree from the object, keeping the current row and the folded lists where they still exist."""
        current = self.current()
        folded = {key for key, item in self.items.items() if not item.isExpanded()}
        self.blockSignals(True)
        self.clear()
        self.items = {}
        for role, opened in obj.opened.items():
            top = QTreeWidgetItem(
                [f"{tr(ROLE_TITLES[role]) if role in ROLE_TITLES else role}  {opened.read.rsplit('/', 1)[-1]}"]
            )
            top.setToolTip(
                0, opened.read + ("" if opened.doc.exact else tr(" (does not read back exactly: read only)"))
            )
            self.addTopLevelItem(top)
            root = opened.doc.parts()
            top.setData(0, ROW, Row(role, "part", root.path))
            self.items[(role, root.path)] = top
            self._add(top, role, root, cfgparts.family(opened.doc) == "mcf")
            top.setExpanded((role, root.path) not in folded)
        self.blockSignals(False)
        if current is not None:
            self.select(current.role, current.path, current.kind, current.section)

    def _add(self, parent: QTreeWidgetItem, role: str, part: Part, mcf: bool = False) -> None:
        for child in part.children:
            if child.is_list:
                text = tr("{title} ({count})", title=cfgparts.title(child.section), count=len(child.children))
                row = Row(role, "list", child.path, child.section)
            else:
                text, row = label(child, mcf), Row(role, "part", child.path)
            item = QTreeWidgetItem([text])
            item.setData(0, ROW, row)
            parent.addChild(item)
            self.items[(role, row.path)] = item
            self._add(item, role, child, mcf)
            item.setExpanded(len(child.children) <= EXPAND)
        if is_config(part.node):
            have = {c.section for c in part.children if c.is_list}
            for section in cfgparts.lists_of(part.node):
                if section not in have and cfgparts.kinds_for(section):
                    item = QTreeWidgetItem([tr("{title} (empty)", title=cfgparts.title(section))])
                    item.setForeground(0, Qt.GlobalColor.gray)
                    item.setData(0, ROW, Row(role, "empty", part.path, section))
                    parent.addChild(item)

    def current(self) -> Row | None:
        item = self.currentItem()
        return item.data(0, ROW) if item is not None else None

    def select(self, role: str, path: str, kind: str = "part", section: str = "") -> None:
        """Make the row of a part (or the nearest part above it that is listed) current."""
        if kind == "empty":
            for item in self._all():
                if item.data(0, ROW) == Row(role, kind, path, section):
                    self.setCurrentItem(item)
                    return
        parts = path.split("/") if path else []
        for end in range(len(parts), -1, -1):
            found = self.items.get((role, "/".join(parts[:end])))
            if found is not None:
                self.setCurrentItem(found)
                self.scrollToItem(found)
                return

    def _all(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack += [item.child(i) for i in range(item.childCount())]
