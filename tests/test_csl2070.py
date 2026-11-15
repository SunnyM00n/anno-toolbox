"""The .csl reader and the work-script numbers, on scripts written for the test; every shipped script of 1404, 1701
and 2070 when ``ANNO1404_DIR`` / ``ANNO1701_DIR`` / ``ANNO2070_DIR`` name the installs."""

from __future__ import annotations

from pathlib import Path

import pytest

from annotoolbox.formats import csl2070 as csl
from annotoolbox.sources import workscripts2070
from annotoolbox.sources.overlay import SourceTree
from fakes import installed

API = """<api><folder><name>X</name><functions>
<APIFunction><name>_sleep</name><id>1</id><sleep>1</sleep><value>1</value><parameter><ms>0</ms></parameter>
<doc>sleep for time ms\\r\\nmore</doc></APIFunction>
<APIFunction><name>_getWaterPos</name><id>2</id><value>2</value><parameter><&pos>442655</&pos><radius>0</radius>
</parameter></APIFunction>
</functions></folder></api>
<globals><folder><name>G</name><globalvars>
<globalvar><name>_SEQ_WALK01</name><id>-5</id><value>1000</value></globalvar>
<globalvar><name>_SEQ_WORK01</name><id>-6</id><value>6000</value></globalvar>
<globalvar><name>_REALINT_ONE</name><id>7</id><value>4096</value></globalvar>
<globalvar><name>_droneGUID</name><id>8</id><value>20132</value></globalvar>
<globalvar><name>_NOVALUE</name><id>9</id></globalvar>
</globalvars></folder></globals>"""

SCRIPT = """#Transient
module Demo(unitHID)
{
	// a comment with an umlaut \xe4
	var hid = _setScriptHID(unitHID)
	var rest = 10 * 1000
	Pos2D pos
	var cut = 1200

	function work()
	{
		var seq = _SEQ_WORK01
		if (_random(100) > 50)
		{
			seq = 6001
			cut = 3000
		}
		_setIdleSequence(seq)
		_sleep(cut + 2000)
		pos.x += (pos.x - 5) * 2048 / _REALINT_ONE
		for (i = 0 to 3) { _sleep(250) }
		foreach (t in list) { _sleep(1) }
	}

	interruptable function main()
	{
		while (_isAlive(hid))
		{
			work()
			if (_getWaterPos(hid, pos, 30)) { _sleep(rest) } else { _sleep(30000) }
			swapscript($Revision: #4 $)
		}
	}

	#Other(hid, "name", -1)
	&Third(hid)
	main()
}
"""


def test_tokens_and_revision_stamp():
    toks = csl.tokens("swapscript($Revision: #3 $) /* skip */ x // end\n y")
    assert [t[1] for t in toks] == ["swapscript", "(", "3", ")", "x", "y"]
    assert csl.tokens("a\nb")[1][2] == 2
    with pytest.raises(csl.CslError):
        csl.tokens("a ` b")


def test_parse_shapes():
    mod = csl.parse(SCRIPT)
    assert mod.name == "Demo" and mod.params == ["unitHID"] and mod.directives == ["Transient"]
    assert set(mod.functions) == {"work", "main"} and mod.functions["main"].interruptable
    assert [v.name for v in mod.variables] == ["hid", "rest", "pos", "cut"]
    calls = [s.expr for s in mod.body if isinstance(s, csl.ExprStmt)]
    assert [(c.name, c.kind) for c in calls] == [("Other", "start"), ("Third", "start_ref"), ("main", "call")]
    assert calls[0].args[1] == csl.Str("name") and calls[0].args[2] == csl.Num(-1)


def test_parse_errors_name_the_line():
    with pytest.raises(csl.CslError, match="line 4"):
        csl.parse("module A()\n{\n if (\n}")
    with pytest.raises(csl.CslError):
        csl.parse("module A() { function f() { ")


def test_api_reader_handles_non_xml_and_constants():
    api = csl.read_api(API)
    assert api.functions["_sleep"].blocks and api.functions["_sleep"].doc == "sleep for time ms\nmore"
    assert api.functions["_getWaterPos"].params == ("pos", "radius") and not api.functions["_getWaterPos"].blocks
    assert api.constants["_SEQ_WALK01"] == 1000 and api.constants["_NOVALUE"] == 0


def test_folding_and_values():
    script = csl.load(SCRIPT, csl.read_api(API))
    assert script.values("rest") == [10000]
    assert script.values("seq") == [6000, 6001]  # two assignments, both constant
    assert script.values("cut") == [1200, 3000]
    assert script.values("pos") is None and script.values("nothing") is None
    assert script.fold(csl.Binary("/", csl.Num(7), csl.Num(2))) == 3
    assert script.fold(csl.Binary("/", csl.Num(7), csl.Num(0))) is None
    assert script.fold(csl.Name("_droneGUID")) == 20132
    assert script.assignments("seq", "work") == [6000, 6001]


def test_events_follow_calls_and_keep_conditions():
    script = csl.load(SCRIPT, csl.read_api(API))
    names = [e.name for e in script.events("main")]
    assert names[:4] == ["_isAlive", "_random", "_setIdleSequence", "_sleep"]  # work() is inlined where it is called
    sleeps = script.sleeps("main")
    assert [ms for _, ms in sleeps] == [None, 250, 1, 10000, 30000]
    assert sleeps[3][0].when == ("_getWaterPos(hid, pos, 30)",)
    assert sleeps[4][0].when == ("not _getWaterPos(hid, pos, 30)",)
    assert all(e.loop for e in script.events("main"))
    assert [e.function for e in script.calls("swapscript")] == ["main"] and script.folded(
        script.calls("swapscript")[0]
    ) == [4]
    assert script.strings("Other") == ["name"]


OLD_DIALECT = """interruptable module Farmer(unitHID)
{
	var x, y, z
	var chance[3]
	function work()
	{
		if (!_getPoint(unitHID, &x, &z)) { return }
		switch (_random(3))
		{
			case 0:
			{
				_sleep(1000)
			}
			break
			case 1:
				_sleep(2000)
				break
			default:
				_sleep(3000)
		}
	}
	function dead()
	{
		return
		#Event_MassMob(unitHID, 6, 60000)
	}
}

patchSwap(8, 9)
{
	x = 4000
}
"""


def test_the_1404_and_1701_dialect():
    mod = csl.parse(OLD_DIALECT)
    assert mod.interruptable and mod.kind == "module"
    assert [(v.name, v.size) for v in mod.variables] == [("x", None), ("y", None), ("z", None), ("chance", csl.Num(3))]
    cond = mod.functions["work"].body[0].cond
    assert cond.operand.args[1:] == (csl.Unary("&", csl.Name("x")), csl.Unary("&", csl.Name("z")))
    sleeps = csl.load(OLD_DIALECT).sleeps("work")
    assert [ms for _, ms in sleeps] == [1000, 2000, 3000]  # the switch reads as the if / else chain it decides
    assert sleeps[1][0].when == ("not (_random(3) == 0)", "(_random(3) == 1)")
    assert mod.functions["dead"].body[0] == csl.Return()
    assert [(c.args, len(body)) for c, body in mod.patches] == [((csl.Num(8), csl.Num(9)), 1)]
    assert csl.parse("framemodule Music() { }").kind == "framemodule"


@pytest.mark.parametrize("game", ["anno1404", "anno1701", "anno2070"])
def test_every_shipped_script_parses(game: str):
    tree = installed(game)
    if tree is None:
        pytest.skip(f"needs the installed {game}")
    paths = [p for p in tree.iter_paths("") if p.lower().endswith(".csl")]
    assert paths
    for path in paths:
        script = csl.load(tree.read_text(path, "latin-1"))
        script.all_events()


def test_unparse_round_trips_the_readable_form():
    expr = csl.parse("module A() { function f() { x = (a + 1) * -2 } }").functions["f"].body[0].value
    assert csl.unparse(expr) == "((a + 1) * -2)"


WORK_API = (
    "<globalvars>"
    + "".join(
        f"<globalvar><name>{n}</name><id>1</id><value>{v}</value></globalvar>"
        for n, v in [
            ("_SEQ_IDLE01", 5000),
            ("_SEQ_WALK01", 1000),
            ("_SEQ_WALK02", 1001),
            ("_SEQ_WORK01", 6000),
            ("_SEQ_WORK02", 6001),
            ("_SEQ_WORK03", 6002),
            ("_feedback_transportation_droneGUID", 20132),
        ]
    )
    + "</globalvars>"
)
FISHERY = """module Fishery()
{
	function init()
	{
		if (_getDummyPosition(b, "ship", -1, p) && _getDummyPosition(b, "harbour_0", 0, w)) { return 1 }
		return 0
	}
	interruptable function goFishing()
	{
		if (_getWaterPos(b, t, 30))
		{
			_setIdleSequence(_SEQ_WORK01)
			_sleep(_getSequenceDuration(_SEQ_WORK01) * 2)
		}
		else { _sleep(30000) }
	}
	interruptable function main() { while (_isAlive(b)) { goFishing() _sleep(10000) } }
}
"""
MINE = """module Mine()
{
	var drone
	function fly()
	{
		_setWalkSequence(_SEQ_WALK01)
		heightDuration = _changeFlyHeight(5)
		_sleep(heightDuration)
		_sleep(4000)
		_setWalkSequence(_SEQ_WALK02)
		_sleep(3000)
	}
	function init() { _getDummyPosition(m, "drone", 0, t) drone = _createObjectAtPos3D(_feedback_transportation_droneGUID, 1, s) }
	function main() { while (1) { fly() _sleep(10000) } }
}
"""
LUMBER = """module Lumberjack(unitHID)
{
	var fallingTreeHID
	var treeFallingTime = 1200
	function lumber()
	{
		var workSequence
		var cutTime
		if (treeHID == 0) { _sleep(30000) return }
		targetPos.x += (b.x - treePos.x) * 2048 / dist;
		workSequence = _SEQ_WORK01
		cutTime = 2270
		if (_random(100) > 50) { workSequence = _SEQ_WORK02 cutTime = 3150 }
		_shakeTree(fallingTreeHID, unitHID, 1820, 700, difftime - 2000, 0, 0)
		_sleep(difftime - 2000)
		_startTreeDying(fallingTreeHID, unitHID, treeFallingTime + 5000)
		_sleep(2000)
		_sleep(treeFallingTime + 3000)
	}
}
"""
PLANT = """module TreeFarmer(unitHID)
{
	function plant()
	{
		if (!_getPlantPos(b, treePos)) { _sleep(30000) return }
		targetPos.x += (b.x - treePos.x) * 2048 / dist;
		_sleep(_getSequenceDuration(_SEQ_WORK02))
		_sleep(_getSequenceDuration(_SEQ_WORK03))
	}
}
"""


def _tree(root: Path, files: dict[str, str]) -> SourceTree:
    for rel, text in files.items():
        path = root / "Data0" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("latin-1"))
    return SourceTree(root)


def test_work_scripts_numbers(tmp_path: Path):
    tree = _tree(
        tmp_path,
        {
            "data/ai/cslapi.xml": WORK_API,
            "data/ai/feedback/fishery.csl": FISHERY,
            "data/ai/feedback/minefeedback.csl": MINE,
            "data/ai/economy/lumberjack.csl": LUMBER,
            "data/ai/economy/treefarmer.csl": PLANT,
        },
    )
    got = workscripts2070.read(tree)
    assert got.warnings == []
    assert got.fishery == workscripts2070.FisheryScript("ship", "harbour_0", 30, 6000, 2, 10000, 30000)
    assert got.mine == workscripts2070.MineScript(20132, "drone", 4000, 3000, 10000, (1000, 1001))
    assert got.fell.stand_off == 0.5 and got.fell.fall_ms == 1200
    assert got.fell.cuts == ((6000, 2270), (6001, 3150))
    assert (got.fell.no_tree_ms, got.fell.sink_ms, got.fell.fade_ms, got.fell.work_end_ms) == (30000, 4200, 6200, 2000)
    assert got.fell.shake == (1820, 700, None, 0, 0)
    assert got.plant == workscripts2070.PlantScript(0.5, 30000, (6001, 6002))


def test_work_scripts_warn_instead_of_guessing(tmp_path: Path):
    tree = _tree(tmp_path, {"data/ai/cslapi.xml": WORK_API, "data/ai/feedback/fishery.csl": "module Fishery() { }"})
    got = workscripts2070.read(tree)
    assert got.fishery is None and got.mine is None
    assert any("fishery.csl has another shape" in w for w in got.warnings)
    assert any("minefeedback.csl is not in" in w for w in got.warnings)
    assert (
        workscripts2070.read(_tree(tmp_path / "empty", {"data/x.txt": ""}))
        .warnings[0]
        .endswith("is not in 2070's data")
    )
