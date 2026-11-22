"""The static scan of the sources for the interface's string ids: every ``tr`` / ``trn`` / ``N_`` call with a literal
id, where it is and the places it passes, and the Qt text calls that still take a literal. ``scan_sources()`` is the one
pass; ``scripts/check_i18n.py`` judges it, the translation window (``langedit``) shows each id's places of use.
Only a checkout has the sources to scan.
"""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import i18n

PKG = Path(__file__).resolve().parents[1]

MARKERS = {"tr", "trn", "N_"}
OK_COMMENT = re.compile(r"#\s*i18n:\s*ok\b")

# Qt calls whose literal arguments are text a person reads: attribute name -> positional indices (None: all).
METHODS: dict[str, tuple[int, ...] | None] = {
    "setWindowTitle": (0,), "setText": None, "setToolTip": (0,), "setStatusTip": (0,), "setWhatsThis": (0,),
    "setPlaceholderText": (0,), "setTitle": (0,), "setPrefix": (0,), "setSuffix": (0,), "setLabelText": (0,),
    "setInformativeText": (0,), "setDetailedText": (0,), "setHtml": (0,), "setPlainText": (0,),
    "addAction": None, "addMenu": None, "addTab": (1,), "insertTab": (2,), "setTabText": (1,), "setTabToolTip": (1,),
    "addItem": None, "insertItem": None, "setItemText": (1,), "showMessage": (0,), "addRow": (0,),
    "setHeaderLabels": None, "setHorizontalHeaderLabels": None, "setVerticalHeaderLabels": None, "addItems": None,
    "setSpecialValueText": (0,), "setButtonText": (1,), "setOkButtonText": (0,), "setCancelButtonText": (0,),
    "information": None, "warning": None, "critical": None, "question": None, "about": None,
    "getText": None, "getInt": None, "getDouble": None, "getItem": None, "getMultiLineText": None,
    "getOpenFileName": None, "getSaveFileName": None, "getOpenFileNames": None, "getExistingDirectory": None,
    "addToolBar": None, "addDockWidget": (),
}  # fmt: skip
# Qt classes made with their text as an argument.
CONSTRUCTORS: dict[str, tuple[int, ...] | None] = {
    "QLabel": (0,), "QPushButton": (0,), "QCheckBox": (0,), "QRadioButton": (0,), "QGroupBox": (0,),
    "QToolButton": (0,), "QAction": None, "QDockWidget": (0,), "QMenu": (0,), "QToolBar": (0,),
    "QListWidgetItem": (0,), "QTableWidgetItem": (0,), "QStandardItem": (0,), "QTreeWidgetItem": None,
    "QProgressDialog": (0, 1), "QMessageBox": None, "QShortcut": (),
}  # fmt: skip
KEYWORDS = {
    "text", "toolTip", "windowTitle", "title", "placeholderText", "statusTip", "whatsThis",
    "specialValueText", "labelText",
}  # fmt: skip


@dataclass
class SCall:
    """One ``tr`` or ``trn`` call with a literal id: where it is and the places it passes (None: ``**fields``)."""

    where: str
    name: str
    ident: str
    passed: set[str] | None


@dataclass
class SScan:
    """What one pass over the sources found."""

    marked: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # id -> modules that name it
    sites: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )  # id -> 'module:line' of every marker
    plurals: set[str] = field(default_factory=set)  # ids named by ``trn``
    calls: list[SCall] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unmarked: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))  # module -> "line: text"
    counts: Counter = field(default_factory=Counter)  # module -> marks


def module_name(path: Path, package_dir: Path = PKG) -> str:
    rel = path.relative_to(package_dir).with_suffix("")
    return rel.parent.as_posix() + "/" if rel.name == "__init__" else rel.as_posix()


def literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _has_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)


def _texty(node: ast.AST) -> str | None:
    """The literal text a call argument is when it is a sentence-ish string built in place, else None."""
    if (text := literal(node)) is not None:
        return text if _has_letters(re.sub(r"\{[^}]*\}", "", text)) else None
    if isinstance(node, ast.JoinedStr):
        parts = [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        return "".join(parts) if _has_letters("".join(parts)) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        for side in (node.left, node.right):
            if (found := _texty(side)) is not None:
                return found
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _texty(node.func.value)
    return None


def _call_name(call: ast.Call) -> tuple[str, bool]:
    """(the called name, whether it is an attribute call) of a call."""
    if isinstance(call.func, ast.Name):
        return call.func.id, False
    if isinstance(call.func, ast.Attribute):
        return call.func.attr, True
    return "", False


def _literals_in(node: ast.AST) -> list[ast.AST]:
    """The arguments to test: a list or tuple argument stands for its items."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return [literal_text for item in node.elts for literal_text in _literals_in(item)]
    return [node]


class CVisitor(ast.NodeVisitor):
    def __init__(self, module: str, lines: list[str], scan: SScan, qt: bool) -> None:
        self.m_module, self.m_lines, self.m_scan, self.m_qt = module, lines, scan, qt
        self.m_depth = 0  # function nesting: a mark at depth 0 runs at import

    def _function(self, node: ast.AST) -> None:
        self.m_depth += 1
        self.generic_visit(node)
        self.m_depth -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def _ok(self, node: ast.AST) -> bool:
        first, last = node.lineno, getattr(node, "end_lineno", node.lineno)
        return any(OK_COMMENT.search(self.m_lines[i - 1]) for i in range(first, last + 1))

    def visit_Call(self, node: ast.Call) -> None:
        name, is_attr = _call_name(node)
        if name in MARKERS and not is_attr:
            self._mark(node, name)
        elif self.m_qt:  # only a module that uses Qt has Qt text calls
            self._qt_text(node, name, is_attr)
        self.generic_visit(node)

    def _mark(self, node: ast.Call, name: str) -> None:
        where = f"{self.m_module}:{node.lineno}"
        if self.m_depth == 0 and name != "N_":
            self.m_scan.problems.append(
                f"{where}: {name}() runs when the module is imported (before the language is chosen): use N_ there and tr at the use"
            )
        ident = self._ident(node, name, where)
        if ident is None:
            return
        self.m_scan.marked[ident].add(self.m_module)
        self.m_scan.sites[ident].append(where)
        self.m_scan.counts[self.m_module] += 1
        if name == "trn":
            self.m_scan.plurals.add(ident)
        if name != "N_":
            star = any(keyword.arg is None for keyword in node.keywords)
            passed = {keyword.arg for keyword in node.keywords if keyword.arg} | ({"n"} if name == "trn" else set())
            self.m_scan.calls.append(SCall(where, name, ident, None if star else passed))

    def _ident(self, node: ast.Call, name: str, where: str) -> str | None:
        """The literal string id a marker names, None (with the problem said) when it names none. A variable in
        ``tr`` is allowed: it holds an id that ``N_`` marked."""
        first = node.args[0] if node.args else None
        if isinstance(first, (ast.JoinedStr, ast.BinOp)):
            self.m_scan.problems.append(f"{where}: {name}() takes a string id, not a built string")
            return None
        text = literal(first) if first is not None else None
        if text is None:
            if name != "tr":
                self.m_scan.problems.append(f"{where}: {name}() takes its string id as a literal")
            return None
        if not i18n.is_id(text):
            self.m_scan.problems.append(
                f"{where}: {name}({text[:40]!r}) takes a string id (section.name): the text itself goes into en.json"
            )
            return None
        if name == "trn" and (len(node.args) < 2 or literal(node.args[1]) is not None):
            self.m_scan.problems.append(f'{where}: trn() takes the id and the count: trn("section.name", n)')
            return None
        return text

    def _qt_text(self, node: ast.Call, name: str, is_attr: bool) -> None:
        table = METHODS if is_attr else CONSTRUCTORS
        if is_attr and name not in METHODS and name in CONSTRUCTORS:  # QtWidgets.QLabel(...)
            table = CONSTRUCTORS
        if name not in table and not any(k.arg in KEYWORDS for k in node.keywords):
            return
        picks: list[ast.AST] = []
        indices = table.get(name, ())
        for i, argument in enumerate(node.args):
            if indices is None or i in indices:
                picks += _literals_in(argument)
        picks += [
            literal_text
            for keyword in node.keywords
            if keyword.arg in KEYWORDS
            for literal_text in _literals_in(keyword.value)
        ]
        if any(isinstance(pick, ast.Call) and _call_name(pick) == ("N_", False) for pick in picks):
            self.m_scan.problems.append(
                f"{self.m_module}:{node.lineno}: N_() only marks an id, {name}() shows it: use tr()"
            )
        found = [picked_text for pick in picks if (picked_text := _texty(pick)) is not None]
        if found and not self._ok(node):
            self.m_scan.unmarked[self.m_module].append(f"{node.lineno}: {name}({found[0][:50]!r})")


def scan_sources(package_dir: Path = PKG) -> SScan:
    scan = SScan()
    for path in sorted(package_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            scan.problems.append(
                f"{module_name(path, package_dir)}: does not parse ({error.msg} at line {error.lineno})"
            )
            continue
        CVisitor(module_name(path, package_dir), text.splitlines(), scan, "PySide6" in text).visit(tree)
    return scan
