"""Patches of model configs (``models.cfgpatch``): each one applied with the suite's reproduction of the mod loader
must give the edited file; what a patch cannot say is reported; the ways to save follow 117's ``shared/`` rule."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.test_cfgdoc import NEW, OLD

from annotoolbox.models import cfgdoc, cfgparts, cfgpatch
from annotoolbox.models.cfgdoc import Doc
from annotoolbox.modloader.engine import run_text
from annotoolbox.modloader.patchdoc import PatchDoc

# a 117 config without headings or tag suffixes: every edit can be a patch
PLAIN = b"\r\n".join([
    b"<Config><ConfigType>MAIN</ConfigType>", b"<Files><Config><ConfigType>FILE</ConfigType>",
    b"<FileName>data/a.cfg</FileName>", b"</Config>", b"<Config><ConfigType>FILE</ConfigType>",
    b"<FileName>data/b.cfg</FileName>", b"</Config>", b"</Files>", b"<Models><Config><ConfigType>MODEL</ConfigType>",
    b"<FileName>data/m.fbx</FileName>", b"<Materials><Config><ConfigType>MATERIAL</ConfigType>",
    b"<cModelDiffTex>data/w.psd</cModelDiffTex>", b"</Config>", b"</Materials>", b"</Config>", b"</Models>",
    b"<Lights></Lights>", b"</Config>", b"",
])  # fmt: skip


def _applied(base: bytes, patch: str) -> bytes:
    """The game's file with the patch applied by the mod loader as the suite reproduces it."""
    doc = PatchDoc.of(*(cfgpatch.as_element(c) for c in Doc.read(base).root.children))
    events = run_text(doc, patch)
    assert not [e for e in events if "No matching" in e.line() or "skip" in e.line().lower()], events
    return b"".join(ET.tostring(r) for r in doc.roots)


def _want(doc: Doc) -> bytes:
    return b"".join(ET.tostring(cfgpatch.as_element(c)) for c in doc.root.children)


def test_a_patch_turns_the_game_file_into_the_edited_one():
    doc = Doc.read(PLAIN, "data/x.cfg")
    files = doc.find("Files")
    texture = next(f for f in cfgdoc.fields(doc.find("Models/0/Materials/0").node) if f.name == "cModelDiffTex")
    lights, _made = cfgparts.list_node(doc, doc.parts().node, "Lights")
    for make in (
        lambda: cfgdoc.set_value(texture.node, "data/stone.psd"),
        lambda: cfgdoc.remove(files.node, doc.find("Files/0").node),
        lambda: cfgdoc.duplicate(files.node, doc.find("Files/0").node),
        lambda: cfgdoc.insert(lights, 0, [cfgparts.new_part(doc, "LIGHT")]),
    ):
        make().do()
    patch = cfgpatch.render_patch(PLAIN, doc)
    assert not patch.problems and patch.count == 4
    assert 'Type="replace" Path="/Config/Models/Config/Materials/Config/cModelDiffTex"' in patch.text
    assert 'Type="add" Path="/Config/Lights"' in patch.text
    assert _applied(PLAIN, patch.text) == _want(doc)


def test_an_entity_in_an_edited_value_is_not_escaped_twice():
    doc = Doc.read(PLAIN, "data/x.cfg")
    name = next(f for f in cfgdoc.fields(doc.find("Models/0").node) if f.name == "FileName")
    cfgdoc.set_value(name.node, "a &amp; b &lt;c&gt; &").do()
    patch = cfgpatch.render_patch(PLAIN, doc)
    assert not patch.problems
    op = ET.fromstring(patch.text if patch.text.lstrip().startswith("<ModOps") else f"<ModOps>{patch.text}</ModOps>")
    assert op.find(".//FileName").text == "a & b <c> &"


def test_moved_parts_and_a_new_first_part_line_up():
    doc = Doc.read(PLAIN, "data/x.cfg")
    files = doc.find("Files").node
    cfgdoc.move(files, files.children[1], -1).do()
    cfgdoc.insert(files, 0, [cfgparts.new_part(doc, "FILE")]).do()
    patch = cfgpatch.render_patch(PLAIN, doc)
    assert not patch.problems
    assert _applied(PLAIN, patch.text) == _want(doc)


def _four_edits(doc: Doc) -> None:
    files = doc.find("Files")
    texture = next(f for f in cfgdoc.fields(doc.find("Models/0/Materials/0").node) if f.name == "cModelDiffTex")
    lights, made = cfgparts.list_node(doc, doc.parts().node, "Lights")
    for edit in (
        cfgdoc.set_value(texture.node, "data/stone.psd"),
        cfgdoc.remove(files.node, doc.find("Files/0").node),
        cfgdoc.duplicate(files.node, doc.find("Files/0").node),
        cfgdoc.insert(lights, 0, [cfgparts.new_part(doc, "LIGHT")]),
    ):
        edit.do()


def test_a_saved_patch_applied_to_the_game_file_gives_the_edited_file_byte_for_byte():
    doc = Doc.read(PLAIN, "data/x.cfg")
    _four_edits(doc)
    patch = cfgpatch.render_patch(PLAIN, doc)
    base = Doc.read(PLAIN, "data/x.cfg")
    again, warnings = cfgpatch.apply_patch(base, patch.text)
    assert not warnings and again.to_bytes() == doc.to_bytes()
    assert base.to_bytes() == PLAIN and again.data == PLAIN  # the game's document is untouched, its bytes the base
    assert cfgpatch.render_patch(PLAIN, again).text == patch.text  # a second save writes the same patch


def test_a_patch_the_game_file_no_longer_fits_is_applied_as_far_as_it_goes():
    doc = Doc.read(PLAIN, "data/x.cfg")
    _four_edits(doc)
    patch = cfgpatch.render_patch(PLAIN, doc).text
    updated = PLAIN.replace(b"<Lights></Lights>\r\n", b"")  # a game update that dropped the list the patch fills
    again, warnings = cfgpatch.apply_patch(Doc.read(updated, "data/x.cfg"), patch)
    assert len(warnings) == 1 and "Lights" in warnings[0]
    assert b"data/stone.psd" in again.to_bytes() and b"<Lights>" not in again.to_bytes()


def test_nothing_changed_is_no_patch():
    assert cfgpatch.render_patch(PLAIN, Doc.read(PLAIN, "x.cfg")).text == ""


def test_what_a_patch_cannot_say_is_reported():
    doc = Doc.read(NEW, "data/x.cfg")
    material = doc.find("Models/0/Materials/0")
    cfgdoc.duplicate(material.parent, material.node).do()  # a material with a heading: no XML for it
    assert any("cannot be written as XML" in p for p in cfgpatch.render_patch(NEW, doc).problems)
    old = Doc.read(OLD, "data/a.cfg")
    old.find("Models/0").node.children[0].text = "data/graphics/b.rdm"
    assert cfgpatch.render_patch(OLD, old).count == 1  # 2070's files compare too (they are saved whole)


def test_the_ways_follow_the_shared_rule():
    game = {"shared/base/x.cfg", "data/base/x.cfg", "data/base/y.cfg"}
    exists = game.__contains__
    hidden = {w.way: w for w in cfgpatch.ways("anno117", "data/base/x.cfg", "data/base/x.cfg", exists)}
    assert hidden["new"].allowed and not hidden["replace"].allowed and "shared/base/x.cfg" in hidden["patch"].why
    shared = {w.way: w for w in cfgpatch.ways("anno117", "data/base/x.cfg", "shared/base/x.cfg", exists)}
    assert shared["patch"].allowed and shared["patch"].path == "shared/base/x.cfg.xml" and ".bfg" in shared["patch"].why
    plain = {w.way: w for w in cfgpatch.ways("anno117", "data/base/y.cfg", "data/base/y.cfg", exists)}
    assert plain["replace"].allowed and plain["patch"].allowed and ".bfg" not in plain["patch"].why
    info = {w.way: w for w in cfgpatch.ways("anno117", "data/base/y.ifo", "data/base/y.ifo", exists)}
    assert not info["patch"].allowed and "replaced whole" in info["patch"].why
    old = {w.way: w for w in cfgpatch.ways("anno2070", "data/a.cfg", "data/a.cfg", exists)}
    assert old["replace"].allowed and not old["patch"].allowed
    assert "not checked" in {w.way: w for w in cfgpatch.ways("anno1800", "d/y.cfg", "d/y.cfg", exists)}["patch"].why
