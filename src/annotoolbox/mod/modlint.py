"""Static lint of a built mod: what the 117 mod loader would silently ignore or drop (engine-modloader "Everything that fails").

``lint_mod(folder)`` reads ``modinfo.json`` and every ``.xml`` below it as the loader does and returns findings; a
finding is a warning, never an error. Rules marked ``unverified`` come from the reference's static reading and were
not seen in the game. Start at ``lint_mod``; ``mod lint`` and the end of ``mod build`` run it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from xml.parsers import expat

from ..modloader.engine import MODOP_ATTRS, OPS_CHILDREN, SHORTHANDS, TYPES
from ..modloader.mods import DEPENDENCY_KINDS, read_mod, strip_jsonc

TARGET_ATTRS = ("Path", "GUID", "Property", "Template")
FALSE_LOOKING = frozenset({"", "0", "false", "no", "off"})
# Extensions the loader only replaces whole: there is no `.<ext>.xml` rule for them (engine-modloader "What a mod can change").
WHOLE_ONLY = frozenset(
    {"ifo", "rdm", "dds", "a7m", "a7t", "fx", "fxh", "fxs", "lua", "bnk", "bk2", "ctt", "tmc", "ani"}
)
PATCH_SUFFIXES = (".cfg.xml", ".fc.xml", "export.bin.xml")  # the target is the name without `.xml`
MODID_FORMAT = re.compile(r"^[a-z0-9-]+$")
VERSION_FORMAT = re.compile(r"^\d+\.\d+\.\d+$")
DIFFICULTIES = ("cheat", "easier", "unchanged", "harder")  # modinfo "Difficulty"; the loader logs an error otherwise
SETUP_FLAGS = ("RequiresNewGame", "SafeToRemove", "Multiplayer", "Campaign")  # booleans of "GameSetup"
GROUP_PATH = "/AssetList/Groups"
TARGET_KEYS = ("Path", "Add", "Append", "Prepend", "Replace", "Merge", "Remove")
# The fixed lists of the game (engine-notes "Fixed lists and data switches"): a name outside them keeps the default, silently.
SLOT_TYPES = frozenset({"None", "Coast", "River", "ClaimingBuilding", "Mountain", "WorkArea", "Marsh"})
CREDIT_CHOICES = frozenset({"Plenty", "Medium", "Spare"})
FLEET_CHOICES = frozenset({"WarFleet", "TradeFleet", "Escort", "OneShip", "None"})
TERRAIN_WORDS = frozenset({"Water", "Coast", "Embankment", "NormalLand", "Marsh", "MarshStatic"})
TUTORIAL_LEVELS = frozenset({"NewToAnno", "NewToLimestone", "NoTutorial"})
FIXED_VALUES = {
    "SlotType": (SLOT_TYPES, "slot type"),
    "SetupStartCredits": (CREDIT_CHOICES, "start credits choice"),
    "SetupStartShips": (FLEET_CHOICES, "start fleet choice"),
    "TutorialLevel": (TUTORIAL_LEVELS, "tutorial level"),
}
FIXED_CHILDREN = {  # a container whose child element names are a fixed list
    "SlotTypes": (SLOT_TYPES, "slot type"),
    "StartCredits": (CREDIT_CHOICES, "start credits choice"),
    "StartShips": (FLEET_CHOICES, "start fleet choice"),
}
LIST_CONTAINERS = frozenset({"BuildingList", "ShipsForFirstProvince"})  # children must be <Item>
PLACEMENT_GATES = ("AllowWaterPlacement", "AllowCoastPlacement")
VANILLA_REGION_TECHS = frozenset({"81491"})  # Latium's research opens open water to every Water;Coast building
SLOT_MENU_TEMPLATE = "objectmenuslotbuilding"
QUEST_POOL = "QuestPool"


@dataclass(frozen=True)
class Finding:
    code: str
    where: str  # a path inside the mod, with ``:line`` for an operation
    message: str
    unverified: bool = False  # the reference read it in the code but never saw it in the game
    level: str = "warning"  # "info": harmless by the reading of the loader, listed for completeness

    def line(self) -> str:
        note = " (not verified in game)" if self.unverified else ""
        tag = "info " if self.level == "info" else ""
        return f"{tag}{self.code} {self.where}: {self.message}{note}"


@dataclass
class LintResult:
    files: int = 0  # ModOps documents read
    ops: int = 0
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # checks that could not run

    @property
    def ok(self) -> bool:
        return all(f.level == "info" for f in self.findings)

    def lines(self, per_code: int = 5) -> list[str]:
        """A summary: the count per rule, then the first ``per_code`` findings of each."""
        out = [f"{self.files} ModOps files, {self.ops} operations: {len(self.findings)} finding(s)", *self.notes]
        counts = Counter(f.code for f in self.findings)
        for code, n in sorted(counts.items()):
            shown = [f for f in self.findings if f.code == code][:per_code]
            out += [f"  {f.line()}" for f in shown]
            if n > per_code:
                out.append(f"  {code}: ... and {n - per_code} more")
        return out


def _add_hint(attr: str, known: frozenset[str] | tuple[str, ...]) -> str:
    near = [k for k in known if k.lower() == attr.lower()]
    return f" (names are case-sensitive: {near[0]})" if near else ""


def lint_modinfo(mod: Path) -> list[Finding]:
    """The folder name, the file, ``Anno``, ``ModID``, ``Version`` and where the load-order keys sit."""
    out: list[Finding] = []
    if mod.name.startswith(("-", ".")):
        out.append(
            Finding("I04", mod.name, "a folder name starting with '-' or '.' is skipped by the loader without a line")
        )
    path = next((p for p in (mod / "modinfo.json", mod / "modinfo.jsonc") if p.is_file()), None)
    if path is None:
        return [*out, Finding("I01", mod.name, "no modinfo.json: the mod is disabled")]
    try:
        info = json.loads(strip_jsonc(path.read_text(encoding="utf-8-sig")))
    except (ValueError, OSError) as exc:
        return [*out, Finding("I01", path.name, f"not valid JSON ({exc}): the mod is disabled")]
    if not isinstance(info, dict):
        return [*out, Finding("I01", path.name, "the top level is not an object: the mod is disabled")]
    anno = info.get("Anno")
    if isinstance(anno, bool) or anno != 8:
        out.append(Finding("I02", path.name, f'"Anno" is {anno!r}, must be the number 8: the mod is disabled'))
    for key in DEPENDENCY_KINDS:  # only valid inside "Dependencies" (a top-level "Optional" is no order key)
        if key != "Optional" and key in info:
            out.append(
                Finding("I03", path.name, f'"{key}" belongs inside "Dependencies": at the top level it is ignored')
            )
    deps = info.get("Dependencies")
    if deps is not None and not isinstance(deps, dict):
        out.append(Finding("I03", path.name, '"Dependencies" must be an object'))
    mod_id = info.get("ModID")
    if not isinstance(mod_id, str) or not MODID_FORMAT.match(mod_id):
        out.append(
            Finding("I05", path.name, f'"ModID" {mod_id!r} is missing or not lower-case letters, digits and dashes')
        )
    if not isinstance(info.get("Version"), str) or not VERSION_FORMAT.match(info["Version"]):
        out.append(Finding("I05", path.name, '"Version" is missing or not a.b.c (the mod loads, shown as unversioned)'))
    return out + _lint_setup(info, path.name)


def _lint_setup(info: dict, name: str) -> list[Finding]:
    """``Difficulty`` (mandatory for 117) and the ``GameSetup`` booleans."""
    out: list[Finding] = []
    difficulty = info.get("Difficulty")
    if difficulty not in DIFFICULTIES:
        what = "missing" if difficulty is None else f"{difficulty!r}"
        out.append(
            Finding(
                "I06",
                name,
                f'"Difficulty" is {what}, must be one of {", ".join(DIFFICULTIES)}: the loader logs an error',
            )
        )
    setup = info.get("GameSetup")
    if setup is not None and not isinstance(setup, dict):
        return [*out, Finding("I07", name, '"GameSetup" must be an object')]
    for flag in SETUP_FLAGS:
        if flag in (setup or {}) and not isinstance(setup[flag], bool):
            out.append(Finding("I07", name, f'"GameSetup.{flag}" is {setup[flag]!r}, must be true or false', True))
    return out


@dataclass
class _Op:
    line: int
    attrs: dict[str, str]
    depth: int  # len(stack) at its start tag
    content: list[str] = field(default_factory=list)  # names of its direct child elements


class _PatchLint:
    """Streams one patch file through expat and collects the findings of the operation rules."""

    def __init__(
        self, rel: str, mod: Path, parser: expat.XMLParserType, slot_types: frozenset[str] = SLOT_TYPES
    ) -> None:
        self.rel, self.mod, self.parser = rel, mod, parser
        self.stack: list[str] = []
        self.findings: list[Finding] = []
        self.ops = 0
        self.op: _Op | None = None
        self.group_ready = False
        self.dead = False  # the root is wrong: nothing below counts
        self.data = _DataLint(self, slot_types)

    def at(self) -> str:
        return f"{self.rel}:{self.parser.CurrentLineNumber}"

    def add(self, code: str, message: str, unverified: bool = False) -> None:
        self.findings.append(Finding(code, self.at(), message, unverified))

    def ops_level(self) -> bool:
        """The current element is a direct operation slot: under ModOps, or under Groups under it."""
        return bool(self.stack) and self.stack[0] == "ModOps" and all(n == "Group" for n in self.stack[1:])

    def start(self, name: str, attrs: dict[str, str]) -> None:
        if not self.stack:
            if name != "ModOps":
                self.add("X02", f"the root is <{name}>, not <ModOps>: the whole file is skipped")
                self.dead = True
        elif self.dead:
            pass
        elif self.ops_level():
            self.operation(name, attrs)
        elif self.op and len(self.stack) == self.op.depth + 1:
            self.op.content.append(name)
        if self.stack and not self.dead:
            self.data.start(name, attrs)
        self.stack.append(name)

    def end(self, name: str) -> None:
        self.stack.pop()
        if self.stack and not self.dead:
            self.data.end(name)
        if self.op and len(self.stack) == self.op.depth:
            self.finish_op(self.op)
            self.op = None

    def text(self, data: str) -> None:
        self.data.text(data)
        if not self.dead and data.strip() and self.ops_level():
            self.add("X04", "text between operations is ignored")

    def operation(self, name: str, attrs: dict[str, str]) -> None:
        if name == "ModOp":
            self.modop(attrs)
        elif name in ("Asset", "Assets"):
            self.ops += 1
            self.default_route(f"<{name}>")
        elif name == "Group":
            if "Skip" in attrs:
                self.add("O07", f'Skip="{attrs["Skip"]}" on a Group: any value skips it, only the attribute counts')
        elif name == "Include":
            self.include(attrs.get("File"))
        else:
            self.add(
                "X04",
                f"<{name}> is not a ModOp, Asset, Assets, Group or Include: ignored"
                + _add_hint(name, tuple(OPS_CHILDREN)),
            )

    def include(self, file: str | None) -> None:
        if not file:
            self.add("X05", "<Include> without File: nothing is included", unverified=True)
            return
        base = self.mod if file.startswith(("/", "\\")) else (self.mod / self.rel).parent
        if not (base / file.lstrip("/\\")).is_file():
            self.add("X05", f"Include {file!r} is not a file here (the path rules were not tried)", unverified=True)

    def modop(self, attrs: dict[str, str]) -> None:
        self.ops += 1
        self.op = _Op(self.parser.CurrentLineNumber, attrs, len(self.stack))
        for name in attrs:
            if name not in MODOP_ATTRS:
                self.add("O06", f"unknown attribute {name}: ignored" + _add_hint(name, MODOP_ATTRS))
        shorthand = [s for s in SHORTHANDS if s in attrs]
        kind = attrs.get("Type")
        if kind is None and not shorthand:
            self.add("O05", "ModOp with no Type and no Add/Append/Prepend/Remove/Replace/Merge: it does nothing")
        elif kind is not None and kind not in TYPES:
            self.add(
                "O08",
                f'Type="{kind}" is unknown (lower case except addNextSibling/addPrevSibling): the operation is dead',
            )
        if "Skip" in attrs:
            self.add("O07", f'Skip="{attrs["Skip"]}": any value skips the operation, only the attribute counts')
        if attrs.get("AllowNoMatch", "x").strip().lower() in FALSE_LOOKING:
            self.add("O07", f'AllowNoMatch="{attrs["AllowNoMatch"]}" still allows a miss: only the attribute counts')
        self.check_base(attrs, shorthand)
        if self.rel.lower().endswith("camera.xml") and attrs.get("Path") == "/":
            self.add("O09", 'Path="/" selects both roots of camera.xml (Normal and Simple)', unverified=True)

    def check_base(self, attrs: dict[str, str], shorthand: list[str]) -> None:
        """A shorthand path starts at <Values>; a Type= path with a GUID starts at the <Asset> (F10)."""
        if "GUID" not in attrs:
            return
        for s in shorthand:
            if attrs[s].lstrip("/").startswith("Values"):
                self.add("O10", f'{s}="{attrs[s]}" with GUID starts at <Values>, so "Values/..." selects nothing')

    def finish_op(self, op: _Op) -> None:
        attrs = op.attrs
        kind = attrs.get("Type")
        target = attrs.get("Path", "") or attrs.get("Add", "")
        if "Group" in op.content and GROUP_PATH in target and (kind == "add" or "Add" in attrs):
            self.group_ready = True
        elif kind == "assets" or (kind == "add" and not any(k in attrs for k in TARGET_ATTRS)):
            self.default_route("an add with no path", op.line)

    def default_route(self, what: str, line: int | None = None) -> None:
        """Assets go to the last <Assets> of the vanilla file, which the game drops until a new group exists (G01)."""
        if self.rel.lower().endswith("assets.xml") and not self.group_ready:
            where = f"{self.rel}:{line or self.parser.CurrentLineNumber}"
            msg = f"{what} adds assets to the vanilla quest-object group before any new empty group was added (harmless by the loader's reading of 2026-11-06)"
            self.findings.append(Finding("G01", where, msg, level="info"))


class _DataLint:
    """The silent data mistakes inside operations: list entries, fixed lists, retired fields (D01-D06).

    Keeps its own stack of ``[name, target path, text]`` frames, so a direct child of a ``ModOp`` knows its container
    from the op's path. ``slot_types`` are the slot types this mod may name: the engine's, and the ones the caller
    widens the list by (``lint_mod``'s ``slot_types``).
    """

    def __init__(self, lint: _PatchLint, slot_types: frozenset[str] = SLOT_TYPES) -> None:
        self.lint = lint
        self.slot_types = slot_types
        self.op_type = ""
        self.frames: list[list] = []
        self.asset_templates: list[str | None] = []
        self.asset_flags: list[list[str]] = []

    def fixed(self, table: dict[str, tuple[frozenset[str], str]], key: str) -> tuple[frozenset[str], str]:
        """A fixed list and what it is called; the slot types as this mod widens them."""
        known, what = table[key]
        return (self.slot_types if known is SLOT_TYPES else known), what

    def container(self, child: str = "") -> str:
        if not self.frames:
            return ""
        name, target, _ = self.frames[-1]
        if name != "ModOp":
            return name
        path = target.rstrip("/").rsplit("/", 1)
        # a replace whose path ends at the node itself: the child is that node, its container is the path's parent
        if self.op_type == "replace" and path[-1] == child:
            return path[0].rsplit("/", 1)[-1] if len(path) > 1 else ""
        return path[-1]

    def in_slot_menu(self) -> bool:
        return any(f[0] == "SlotTypes" or "SlotTypes" in f[1] for f in self.frames)

    def start(self, name: str, attrs: dict[str, str]) -> None:
        target = next((attrs[k] for k in TARGET_KEYS if k in attrs), "") if name == "ModOp" else ""
        if name == "ModOp":
            self.check_path(target, attrs)
            self.op_type = attrs.get("Type", "").lower()
        parent = self.container(name)
        if (parent in LIST_CONTAINERS or (parent == "Buildings" and self.in_slot_menu())) and name not in (
            "Item",
            "ModItem",
        ):  # ModItem becomes an Item
            self.lint.add("D01", f"<{name}> in the list {parent} is skipped by the game: entries must be <Item>")
        if parent in FIXED_CHILDREN:
            self.check_value(name, *self.fixed(FIXED_CHILDREN, parent), where=f"a child of {parent}")
        if name == "Asset":
            self.asset_templates.append(None)
            self.asset_flags.append([])
        self.frames.append([name, target, []])

    def text(self, data: str) -> None:
        if self.frames:
            self.frames[-1][2].append(data)

    def end(self, name: str) -> None:
        if not self.frames:
            return
        _, _, parts = self.frames.pop()
        text = "".join(parts).strip()
        parent = self.container()
        if name in FIXED_VALUES:
            self.check_value(text, *self.fixed(FIXED_VALUES, name), where=name)
        elif name == "TerrainType2":
            for word in re.split(r"[;,\s]+", text):
                self.check_value(word, TERRAIN_WORDS, "terrain flag", where=name)
        elif name == "TerrainType" and parent == "Building":
            self.old_terrain()
        elif name in PLACEMENT_GATES:
            self.check_gate(name, text)
        elif name == "Template" and parent == "Asset" and self.asset_templates:
            self.asset_templates[-1] = text
            if text.lower() == SLOT_MENU_TEMPLATE:
                self.lint.add("D04", "a second ObjectmenuSlotBuilding asset is never read: add to the slot menu 40495")
        elif name == "DisabledByDefault" and self.asset_flags:
            self.asset_flags[-1].append(self.lint.at())
        elif name == "Asset" and self.asset_templates:
            template, flags = self.asset_templates.pop(), self.asset_flags.pop()
            if template != QUEST_POOL:
                for where in flags:
                    msg = f"DisabledByDefault on a {template or 'template-less'} asset: only a QuestPool reads it"
                    self.lint.findings.append(Finding("D06", where, msg))

    def old_terrain(self) -> None:
        self.lint.add(
            "D03", "Building/TerrainType was renamed TerrainType2 (flags) on update 3.0: the old field is not read"
        )

    def check_value(self, value: str, known: frozenset[str], what: str, *, where: str) -> None:
        if value and value not in known:
            msg = f"{where}: {value!r} is not a {what} ({', '.join(sorted(known))}): the game keeps the default"
            self.lint.add("D02", msg)

    def check_gate(self, name: str, text: str) -> None:
        if text in VANILLA_REGION_TECHS:
            self.lint.add("D05", f"{name} {text} is a vanilla region tech (Latium's research opens open water to all)")
        elif not text.isdigit():
            self.lint.add("D05", f"{name} {text!r} names no tech unlock (a GUID, or 0 for none)")

    def check_path(self, target: str, attrs: dict[str, str]) -> None:
        """The same rules for a name inside a path attribute: ``StartCredits/Plenty``, ``SlotTypes/Coast``."""
        for key in FIXED_CHILDREN:
            known, what = self.fixed(FIXED_CHILDREN, key)
            for m in re.finditer(rf"\b{key}/(\w+)", target):
                self.check_value(m.group(1), known, what, where=f"path {key}/")
        if re.search(r"\bBuilding/TerrainType\b", target) or attrs.get("Property") == "Building/TerrainType":
            self.old_terrain()


def lint_patch(path: Path, rel: str, mod: Path, slot_types: frozenset[str] = SLOT_TYPES) -> tuple[list[Finding], int]:
    """The operation rules of one ``.xml`` of the mod: the findings and the number of operations."""
    data = path.read_bytes()
    if not data.strip():
        return [Finding("X01", rel, "an empty file: 'Failed to get root element', the file is skipped")], 0
    parser = expat.ParserCreate()
    lint = _PatchLint(rel, mod, parser, slot_types)
    parser.StartElementHandler = lint.start
    parser.EndElementHandler = lint.end
    parser.CharacterDataHandler = lint.text
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        lint.findings.append(
            Finding("X01", f"{rel}:{exc.lineno}", f"does not parse ({exc}): the whole file is skipped")
        )
    return lint.findings, lint.ops


def lint_patch_name(rel: str, mod: Path, has_base: Callable[[str], bool] | None) -> list[Finding]:
    """Whether the loader has a game file to apply this patch file to."""
    low = rel.lower()
    if low.endswith(".include.xml"):
        return []
    stem = low[:-4]
    ext = stem.rsplit(".", 1)[-1] if "." in stem.rsplit("/", 1)[-1] else ""
    if ext in WHOLE_ONLY:
        return [
            Finding("P02", rel, f".{ext} can only be replaced whole: there is no .{ext}.xml rule, the file is skipped")
        ]
    if has_base is None:
        return []
    target = rel[:-4] if low.endswith(PATCH_SUFFIXES) else rel
    if has_base(target) or (target != rel and (mod / target).is_file()):  # a plain .xml is its own file
        return []
    return [Finding("P01", rel, f"no game file {target} to patch: 'Can't find file to patch', the file is skipped")]


def lint_mod(
    mod: Path,
    *,
    has_base: Callable[[str], bool] | None = None,
    slot_types: frozenset[str] = frozenset(),
    folders: list[Path] | None = None,
    profile: Path | None = None,
) -> LintResult:
    """Lint a built mod folder. ``has_base(path)`` says whether the game has a file at that mod-relative path (117's
    files); ``slot_types`` are slot types the game takes beyond its own list for this mod (a caller that widens it).
    The mods nested inside are linted as mods of their own (their paths are relative to their folder). ``folders``
    (the mods folders) and ``profile`` switch on the dependency checks (``modlintdeps``)."""
    from .modlintdeps import lint_dependencies

    res = _lint_tree(mod, has_base, slot_types)
    found, notes = lint_dependencies(mod, folders, profile)
    res.findings += found
    res.notes += notes
    return res


def _lint_tree(
    mod: Path, has_base: Callable[[str], bool] | None, slot_types: frozenset[str], where: str = ""
) -> LintResult:
    res = LintResult()
    res.findings += [replace(f, where=where + f.where) for f in lint_modinfo(mod)]
    info, _ = read_mod(mod)
    nested = info.nested if info else []
    if has_base is None and not where:
        res.notes.append("patch targets not checked (give the 117 install)")
    if has_base is not None and not where:
        from .phxrefs import missing_refs

        res.findings += [Finding("U01", where, message) for where, message in missing_refs(mod, has_base)]
    if slot_types and not where:
        res.notes.append(f"slot types this mod adds, accepted: {', '.join(sorted(slot_types))}")
    inside = {child.folder.resolve() for child in nested}
    for path in sorted(p for p in mod.rglob("*") if p.suffix.lower() == ".xml" and p.is_file()):
        if any(parent in inside for parent in path.resolve().parents):
            continue
        rel = path.relative_to(mod).as_posix()
        res.findings += [replace(f, where=where + f.where) for f in lint_patch_name(rel, mod, has_base)]
        found, ops = lint_patch(path, rel, mod, SLOT_TYPES | slot_types)
        res.findings += [replace(f, where=where + f.where) for f in found]
        res.files += 1
        res.ops += ops
    for child in nested:
        sub = _lint_tree(child.folder, has_base, slot_types, f"{where}{child.folder.relative_to(mod).as_posix()}/")
        res.findings += sub.findings
        res.files += sub.files
        res.ops += sub.ops
    return res
