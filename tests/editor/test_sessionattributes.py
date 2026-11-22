"""Every attribute the editor's modules and scripts read on an edit session exists on one: a rename that
missed a user (a receiver no type checker follows) shows here, not in a view on the GPU."""

import ast
from pathlib import Path

import pytest
from tests.editor.editorfakes import edit_island

from annotoolbox.editor.editsession import CEditSession

ROOT = Path(__file__).resolve().parents[2]
READERS = [
    *sorted((ROOT / "src/annotoolbox/editor").glob("*.py")),
    *sorted((ROOT / "src/annotoolbox/editor/desktop").glob("*.py")),
    ROOT / "scripts/native_compare.py",
    ROOT / "scripts/editor_shots.py",
]
READERS = [path for path in READERS if path.is_file()]  # the public edition carries no scripts/
RECEIVERS = {"session", "m_session"}


def _reads(path: Path) -> set[tuple[str, int]]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute) or isinstance(node.ctx, ast.Store):
            continue
        base = node.value
        if (isinstance(base, ast.Name) and base.id in RECEIVERS) or (
            isinstance(base, ast.Attribute) and base.attr in RECEIVERS
        ):
            found.add((node.attr, node.lineno))
    return found


@pytest.mark.parametrize("path", READERS, ids=lambda path: path.name)
def test_session_reads_name_attributes_the_session_has(path):
    session = CEditSession(edit_island())
    missing = sorted(f"{attr} (line {line})" for attr, line in _reads(path) if not hasattr(session, attr))
    assert not missing, f"{path.name} reads {', '.join(missing)} on an edit session"
