"""The viewer's data view: a text file (XML, Lua, JSON, a config) as text, a FileDB document (the newer games'
level data, ``.a7m``, ``.ifo``, ``.fc`` ...) as a tree of its tags and attributes, anything else as its first bytes.

``CDataView(parent)`` is the entry: ``show_file(path, data)``; ``display_kind`` decides how a file is shown (no Qt).
FileDB: docs/formats/filedb.md.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QStackedWidget, QTreeWidget, QTreeWidgetItem

from ..formats import filedb
from ..infra.i18n import number, tr

TEXT = (".xml", ".txt", ".json", ".lua", ".cfg", ".ini", ".fx", ".fxh", ".md", ".csv", ".efc", ".mcf", ".ocf", ".isd")
TREE_LIMIT = 50_000  # tags and attributes shown at most
HEX_BYTES = 4096


def display_kind(path: str, data: bytes) -> str:
    """ "text", "filedb" or "bytes"."""
    if path.lower().endswith(TEXT):
        return "text"
    if filedb.version(data) in (2, 3):
        return "filedb"
    head = data[:2048]
    if head and all(byte in (9, 10, 13) or 32 <= byte < 127 or byte >= 128 for byte in head) and b"\0" not in head:
        return "text"
    return "bytes"


def hex_lines(data: bytes, count: int = HEX_BYTES) -> str:
    """The first bytes as a hex dump with their printable characters."""
    out = []
    for offset in range(0, min(len(data), count), 16):
        row = data[offset : offset + 16]
        text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        out.append(f"{offset:08x}  {row.hex(' '):<47}  {text}")
    return "\n".join(out)


def attribute_text(data: bytes) -> str:
    if data and all(32 <= byte < 127 for byte in data):
        return f'"{data.decode()}"'
    return data[:32].hex(" ") + (" ..." if len(data) > 32 else "")


class CDataView(QStackedWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_text = QPlainTextEdit(readOnly=True)
        self.m_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderLabels([tr("viewer.tag_attribute"), tr("common.value_lower")])
        self.addWidget(self.m_text)
        self.addWidget(self.m_tree)

    def show_file(self, path: str, data: bytes) -> str:
        """Show a file; returns how it is shown (``display_kind``)."""
        kind = display_kind(path, data)
        if kind == "filedb":
            try:
                self._tree(filedb.read(data))
                self.setCurrentWidget(self.m_tree)
                return kind
            except Exception as exc:  # a document this reader does not take: its bytes instead
                kind = "bytes"
                self.m_text.setPlainText(tr("viewer.filedb_not_read", error=exc) + "\n\n" + hex_lines(data))
        elif kind == "text":
            self.m_text.setPlainText(data.decode("utf-8", "replace"))
        else:
            self.m_text.setPlainText(tr("viewer.n_bytes", n=number(len(data))) + "\n\n" + hex_lines(data))
        self.setCurrentWidget(self.m_text)
        return kind

    def _tree(self, root) -> None:
        self.m_tree.clear()
        root_item = QTreeWidgetItem([root.name or tr("viewer.document"), ""])
        self.m_tree.addTopLevelItem(root_item)
        stack, shown = [(root_item, root)], 0
        while stack and shown < TREE_LIMIT:
            item, tag = stack.pop()
            for child in tag.children:
                shown += 1
                if isinstance(child, filedb.STag):
                    sub = QTreeWidgetItem([child.name, tr("viewer.n_children", n=len(child.children))])
                    item.addChild(sub)
                    stack.append((sub, child))
                else:
                    item.addChild(QTreeWidgetItem([child.name, attribute_text(child.data)]))
        root_item.setExpanded(True)
