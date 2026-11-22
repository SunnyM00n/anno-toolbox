import pytest
from tests.systems.quest.questfakes import FILES

from annotoolbox.sources.overlay import CSourceTree


@pytest.fixture
def tree(source_root):
    base = "patch2/data/config/"
    for rel, text in FILES:
        path = source_root / (base + rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return CSourceTree(source_root)
