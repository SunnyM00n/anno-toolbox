"""The check of a written mod folder for what the mod loader drops without a word."""

import zlib

from annotoolbox.formats.rda import write_v22
from annotoolbox.suite import modcheck


def mod(tmp_path, assets: str, a7m: bytes | None = None, info: str = '{"ModID": "m"}'):
    root = tmp_path / "m"
    (root / "data/base/config/export").mkdir(parents=True)
    (root / "modinfo.json").write_text(info, encoding="utf-8")
    (root / "data/base/config/export/assets.xml").write_text(assets, encoding="utf-8")
    if a7m is not None:
        isl = root / "data/i/new/new.a7m"
        isl.parent.mkdir(parents=True)
        isl.write_bytes(a7m)
    return root


GOOD = '<ModOps><ModOp Type="addNextSibling" GUID="5"><Asset><FilePath>data/i/new/new.a7m</FilePath></Asset></ModOp></ModOps>'


def test_a_mod_the_editor_writes_passes(tmp_path):
    root = mod(tmp_path, GOOD, write_v22({"gamedata.data": bytes(300), "rd3d.data": bytes(300)}))
    (root / "data/i/new/new.a7minfo").write_bytes(b"")
    assert modcheck.check(root) == []


def test_what_the_loader_would_drop_is_named(tmp_path):
    bad = '<Assets><ModOp Type="add"/></Assets>'
    root = mod(tmp_path, bad, info="{not json")
    problems = modcheck.check(root)
    assert any("modinfo.json does not read" in p for p in problems)
    assert any("not <ModOps>" in p for p in problems)
    root2 = mod(
        tmp_path / "2",
        '<ModOps><ModOp Type="add"/><ModOp GUID="1"><Asset><FilePath>data/gone.a7m</FilePath></Asset></ModOp></ModOps>',
    )
    problems = modcheck.check(root2)
    assert any("operation 1 (add) names no GUID" in p for p in problems)
    assert any("data/gone.a7m" in p for p in problems)
    assert not modcheck.check(root2, in_game=lambda p: p == "data/gone.a7m")[1:]
    for nameless in ("[]", "7", "{}"):  # JSON that is no record (it stopped the check), or one without the id
        assert any("no ModID" in p for p in modcheck.check(mod(tmp_path / nameless, GOOD, info=nameless)))


def test_an_island_packed_at_another_level_and_without_its_info(tmp_path):
    archive = bytearray(write_v22({"gamedata.data": bytes(300)}))
    at = archive.find(b"\x78\x01")
    archive[at : at + 2] = zlib.compress(b"x", 9)[:2]
    root = mod(tmp_path, GOOD, bytes(archive))
    problems = modcheck.check(root)
    assert any("no .a7minfo" in p for p in problems)
    assert any("another zlib level" in p for p in problems)
