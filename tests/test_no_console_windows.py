"""Every tool the package starts with its output captured or sent to a file starts without a console window: the
installed program is windowed, and a console tool would flash a window of its own (Qt's shader baker did, once per
shader)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "annotoolbox"
CALLS = {"run", "Popen", "check_output", "call", "check_call"}
QUIET = {"capture_output", "stdout", "stderr"}


def _unhidden(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CALLS):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            continue
        names = {k.arg for k in node.keywords if k.arg}
        spread = [ast.unparse(k.value) for k in node.keywords if k.arg is None]
        hidden = "creationflags" in names or "startupinfo" in names or any("NO_WINDOW" in s for s in spread)
        if names & QUIET and not hidden:  # a call that prints into its caller's terminal keeps that console
            lines.append(node.lineno)
    return lines


def test_every_captured_tool_call_starts_without_a_console_window():
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if lines := _unhidden(ast.parse(path.read_text(encoding="utf-8"))):
            found[path.relative_to(PACKAGE).as_posix()] = lines
    assert found == {}, "pass **tools.NO_WINDOW (infra/tools.py) to these calls"


def test_the_check_sees_a_call_without_the_flag():
    code = "import subprocess\nsubprocess.run(['qsb'], capture_output=True)\nsubprocess.run(['a'], **NO_WINDOW, text=True, capture_output=True)\n"
    assert _unhidden(ast.parse(code)) == [2]
