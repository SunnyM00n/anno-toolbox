"""The mod loader's patching, run outside the game: each mod's patch files applied to the game's files in load order,
every operation as the loader does it, and every case the loader would skip or log, reported. Stdlib only.

``CSession(read_base)`` holds the patched documents (``patchdoc.CPatchDoc``, one per game file); ``apply_mod(mod,
variables)`` runs one mod (``mods.CMod``); ``events`` lists what the loader would log or silently drop, by mod, file
and line. The rules are ``docs/engine-modloader.md`` ("ModOps", "Everything that fails without a word"); what this
reproduces and what it cannot: ``docs/tool-suite.md`` section 17.
"""

from __future__ import annotations

import copy
import posixpath
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat

from . import content
from .mods import CMod, CVariables, patch_target
from .patchdoc import CPatchDoc
from .xpath import CXPathError, boolean, evaluate, select

OPS_CHILDREN = frozenset({"ModOp", "Asset", "Assets", "Group", "Include"})
TYPES = frozenset(
    {"add", "assets", "addNextSibling", "addAfter", "addPrevSibling", "addBefore", "remove", "replace", "merge"}
)
ALIASES = {"addAfter": "addNextSibling", "addBefore": "addPrevSibling"}
SHORTHANDS = {
    "Add": "add", "Append": "addNextSibling", "Prepend": "addPrevSibling", "Remove": "remove", "Replace": "replace",
    "Merge": "merge",
}  # fmt: skip
MODOP_ATTRS = frozenset(
    {"Type", "Path", "GUID", "Property", "Template", "Condition", "AllowNoMatch", "MaxRepeat", "Content", "Skip",
     *SHORTHANDS}
)  # fmt: skip
MAX_INCLUDE_DEPTH = 16
MAX_REPEAT = 1000
WARNING, INFO = "warning", "info"
GAME_117, GAME_1800 = "anno117", "anno1800"  # the loader whose rules a session follows
ONLY_117 = frozenset({"Property", "MaxRepeat", *SHORTHANDS})  # ModOp attributes 1800's loader does not read
TYPE_NAMES = {name.lower(): name for name in TYPES}  # 1800's loader (the community source) reads ``Type`` in any case
BINARY = ("export.bin",)  # patch targets the loader converts to XML first: not read here


@dataclass(frozen=True)
class SEvent:
    """One line the loader would log, or a case it skips without a word."""

    mod: str
    file: str  # the mod-relative file, with the line
    # nomatch, condition, skip, type, notype, child, include, parse, root, nobase, badbase, binary, variable, xpath
    code: str
    message: str
    level: str = WARNING
    others: tuple[str, ...] = ()  # the other mods a condition asks about

    def line(self) -> str:
        tag = "" if self.level == WARNING else "info "
        return f"{tag}{self.mod}: {self.file}: {self.message}"


@dataclass
class _SFile:
    """The running state of one patch file (or include) of one mod."""

    mod: CMod
    game_path: str
    document: CPatchDoc
    rel: str
    lines: dict
    variables: CVariables

    def where(self, element: ET.Element) -> str:
        return f"{self.rel}:{self.lines.get(element, 0)}"


@dataclass
class CSession:
    """One run of the loader over several mods: ``read_base(game path)`` gives a game file's bytes (None: no such
    file); ``lazy(game path)`` the asset ranges of a large one (``CPatchDoc.load``); ``observe`` is told of each
    applied operation (mod id, game path, kind, touched nodes); ``written`` also of its targets and the content it
    put in, before the operation runs (to see what a mod set even where the value was the same)."""

    m_read_base: Callable[[str], bytes | None]
    m_lazy: Callable[[str], list | None] | None = None
    m_observe: Callable[[str, str, str, list], None] | None = None
    m_written: Callable[[str, str, str, list, list], None] | None = None  # (mod id, game path, kind, targets, content)
    m_keep: Callable[[CPatchDoc, ET.Element], object] | None = (
        None  # asset the loader adds -> a note; it is kept as bytes
    )
    m_game: str = (
        GAME_117  # ``GAME_1800``: what 1800's loader does not read is left out and said ("Anno 1800's loader")
    )
    m_docs: dict[str, CPatchDoc] = field(default_factory=dict)
    m_events: list[SEvent] = field(default_factory=list)
    m_applied: Counter = field(default_factory=Counter)  # mod id -> operations applied
    m_missing: set[str] = field(default_factory=set)
    m_unreadable: dict[str, str] = field(default_factory=dict)  # game file that is not XML -> the parser's message

    def document(self, game_path: str) -> CPatchDoc | None:
        """The patched document of a game file, read on first use; None for a file the game lacks (``missing``) or
        one that is not XML (``unreadable``), so the other files of the run are still patched."""
        key = game_path.lower()
        if key not in self.m_docs and key not in self.m_missing and key not in self.m_unreadable:
            data = self.m_read_base(key)
            if data is None:
                self.m_missing.add(key)
                return None
            try:
                self.m_docs[key] = CPatchDoc.load(data, self.m_lazy(key) if self.m_lazy else None)
            except ET.ParseError as exc:
                self.m_unreadable[key] = str(exc)
        return self.m_docs.get(key)

    def apply_mod(self, mod: CMod, variables: CVariables) -> None:
        for rel, _path in mod.patch_files():
            self.apply_file(mod, rel, mod.read(rel), variables)

    def apply_file(self, mod: CMod, rel: str, data: bytes, variables: CVariables) -> None:
        """One patch file of ``mod`` (``rel``: its path in the mod) on the game file of the same path."""
        target = patch_target(rel)
        say = self._say(mod, rel)
        if target.endswith(BINARY):
            return say("binary", f"{target}: the binary infotips are not read here (kept as a file)", INFO)
        root, lines = parse_lines(data)
        if root is None:
            return say("parse", f"Failed to parse: {lines}")
        if root.tag != "ModOps":
            return say("root", "Doesn't contain ModOps root node")
        document = self.document(target)
        if document is None and target.lower() in self.m_unreadable:
            return say("badbase", f"Can't read the file to patch: {target} ({self.m_unreadable[target.lower()]})")
        if document is None:
            return say("nobase", f"Can't find file to patch: {target}")
        self._run(root, _SFile(mod, target, document, rel, lines, variables), 0)

    def _say(self, mod: CMod, where: str):
        def say(code: str, message: str, level: str = WARNING, others: tuple = ()) -> None:
            self.m_events.append(SEvent(mod.m_mod_id, where, code, message, level, others))

        return say

    # the elements of a ModOps document
    def _run(self, parent: ET.Element, file: _SFile, depth: int) -> None:
        for element in parent:
            if not isinstance(element.tag, str):
                continue
            say = self._say(file.mod, file.where(element))
            if element.tag == "ModOp":
                self._modop(element, file)
            elif element.tag == "Asset" and self.m_game == GAME_1800:
                say("feature", "<Asset> outside a ModOp is read by 117's loader only: 1800 ignores it")
            elif element.tag == "Asset":
                self._new_assets(file, [element], element)
            elif element.tag == "Assets":
                self._new_assets(file, [asset for asset in element if asset.tag == "Asset"], element)
            elif element.tag == "Group":
                if "Skip" in element.attrib:
                    say("skip", "Group skipped (Skip)", INFO)
                else:
                    self._group(element, file, depth, say)
            elif element.tag == "Include":
                self._include(element, file, depth, say)
            else:
                say("child", f"<{element.tag}> is not ModOp, Asset, Assets, Group or Include: ignored")

    def _group(self, element: ET.Element, file: _SFile, depth: int, say) -> None:
        """A ``Group``: its operations while its ``Condition`` holds, again up to ``MaxRepeat`` times (117; 1800 does
        not read the attribute)."""
        repeat = 1
        if self.m_game != GAME_1800 and "MaxRepeat" in element.attrib:
            repeat = max(1, min(MAX_REPEAT, _int(element.get("MaxRepeat"), 1)))
        for turn in range(repeat):
            if not self._holds(element, file, file.document.m_top, say if turn == 0 else _quiet):
                return
            self._run(element, file, depth)

    def _include(self, element: ET.Element, file: _SFile, depth: int, say) -> None:
        if "Skip" in element.attrib:
            return say("skip", "Include skipped (Skip)", INFO)
        if not self._holds(element, file, file.document.m_top, say):
            return
        ref = element.get("File", "")
        rel = ref.lstrip("/") if ref.startswith("/") else posixpath.join(posixpath.dirname(file.rel), ref)
        rel = posixpath.normpath(rel)
        usable = ref and not rel.startswith("..") and file.mod.is_file(rel) and depth < MAX_INCLUDE_DEPTH
        data = file.mod.read(rel) if usable else b""
        root, lines = parse_lines(data) if data.strip() else (None, "")
        if root is None or root.tag != "ModOps":
            return say("include", f"Include file missing or empty: {ref}")
        self._run(root, _SFile(file.mod, file.game_path, file.document, rel, lines, file.variables), depth + 1)

    def _holds(self, element: ET.Element, file: _SFile, node, say) -> bool:
        """Whether ``element``'s ``Condition`` holds at ``node`` (true without one); a false one is said."""
        condition = element.get("Condition")
        if condition is None:
            return True
        text, negate = condition.strip(), False
        if text.startswith("!") and not text.startswith("!="):
            text, negate = text[1:].strip(), True
        found = self._calc(text, node, file, say)
        holds = (not boolean(found)) if negate else boolean(found)
        expanded = file.variables.expand(text, file.mod.m_mod_id)
        if expanded.mods:
            others = tuple(sorted(expanded.mods))
            say("modcondition", f"Condition {condition!r} asks about {', '.join(others)}: {holds}", INFO, others)
        if not holds:
            say("condition", f"Condition {condition!r} doesn't match", INFO)
        return holds

    def _calc(self, expression: str, node, file: _SFile, say=None):
        """An expression's value at ``node`` with the mod's variables; an unreadable one is said and is empty."""
        expanded = file.variables.expand(expression, file.mod.m_mod_id)
        for name in expanded.unknown:
            if say:
                say("variable", f"Variable '{name}' not found")
        try:
            return evaluate(file.document, expanded.text, node)
        except (CXPathError, ZeroDivisionError, RecursionError) as exc:
            if say:
                say("xpath", f"cannot evaluate {expression!r}: {exc}")
            return []

    # one ModOp
    def _modop(self, operation: ET.Element, file: _SFile) -> None:
        say = self._say(file.mod, file.where(operation))
        if "Skip" in operation.attrib:
            return say("skip", "ModOp skipped (Skip)", INFO)
        if self.m_game == GAME_1800:
            operation = self._as_1800(operation, say)
        kind, path, short = operation.get("Type"), operation.get("Path", ""), False
        for attribute, meaning in SHORTHANDS.items():
            if attribute in operation.attrib:
                kind, path, short = kind or meaning, operation.get(attribute, ""), True
                break
        if kind is None:
            return say("notype", "ModOp has no Type and no Add=, Merge=, ...: it does nothing")
        if self.m_game == GAME_1800:
            kind = TYPE_NAMES.get(kind.lower(), kind)
        if kind not in TYPES:
            return say("type", f"Unknown ModOp {kind}")
        kind = ALIASES.get(kind, kind)
        guids = [part.strip() for part in operation.get("GUID", "").split(",") if part.strip()] or [None]
        for guid in guids:
            self._one(operation, file, say, _SOp(kind, path, short, guid))

    @staticmethod
    def _as_1800(operation: ET.Element, say) -> ET.Element:
        """``op`` as 1800's loader reads it: without the attributes only 117 reads (said), the content as it is."""
        found = sorted(asset for asset in operation.attrib if asset in ONLY_117)
        if not found:
            return operation
        say("feature", f"{', '.join(found)}: read by 117's loader only, 1800 ignores it")
        plain = ET.Element(operation.tag, {k: value for k, value in operation.attrib.items() if k not in ONLY_117})
        plain.extend(operation)
        return plain

    def _one(self, operation: ET.Element, file: _SFile, say, flags: _SOp) -> None:
        body = [child for child in operation if isinstance(child.tag, str)]
        if (
            flags.kind == "assets"
            or flags.kind == "add"
            and not flags.path
            and flags.guid is None
            and not _picks(operation)
        ):
            return self._new_assets(file, [child for child in body if child.tag == "Asset"], operation)
        repeat = max(1, min(MAX_REPEAT, _int(operation.get("MaxRepeat"), 1))) if operation.get("Condition") else 1
        for turn in range(repeat):
            bases = self._bases(operation, file, flags)
            node = bases[0] if bases else file.document.m_top
            if not self._holds(operation, file, node, say if turn == 0 else _quiet):
                return
            targets = self._targets(operation, file, flags, bases, say)
            if not targets:
                if turn == 0:
                    level = INFO if "AllowNoMatch" in operation.attrib else WARNING
                    say("nomatch", f'No matching node for Path "{_described(operation, flags)}"', level)
                return
            payload = body
            if "Content" in operation.attrib:
                payload = content.fill(copy.deepcopy(body), self._calc(operation.get("Content"), node, file, say))
            if self.m_written:
                self.m_written(file.mod.m_mod_id, file.game_path, flags.kind, targets, payload)
            touched = self._apply(file, flags.kind, targets, payload)
            self.m_applied[file.mod.m_mod_id] += 1
            if self.m_observe:
                self.m_observe(file.mod.m_mod_id, file.game_path, flags.kind, touched)

    def _bases(self, operation: ET.Element, file: _SFile, flags: _SOp) -> list:
        """The nodes a path starts from: the asset (``Type=``) or its ``Values`` (a shorthand) by GUID, a property
        block in every asset, a template; none for a path from the document."""
        document = file.document
        if flags.guid is not None:
            found = document.assets(flags.guid)
        elif operation.get("Property"):
            found = [asset for users in list(document.m_by_guid.values()) for asset in users]
            found = [value for asset in found for value in document.children(asset) if value.tag == "Values"]
            return [
                property_node
                for value in found
                for property_node in document.children(value)
                if property_node.tag == operation.get("Property")
            ]
        elif operation.get("Template"):
            name = operation.get("Template")
            found = [
                template
                for template in document.descendants(document.m_top, "Template")
                if template.findtext("Name") == name
            ]
            return (
                [child for template in found for child in document.children(template) if child.tag == "Properties"]
                if flags.short
                else found
            )
        else:
            return []
        return (
            [value for asset in found for value in document.children(asset) if value.tag == "Values"]
            if flags.short
            else found
        )

    def _targets(self, operation: ET.Element, file: _SFile, flags: _SOp, bases: list, say) -> list:
        path = flags.path.replace("&gt;", ">").replace("&lt;", "<").strip()
        if len(path) > 1:
            path = path.rstrip("/")
        if path == "/":
            return file.document.roots
        from_doc = path.startswith(("//", "@")) or not _picks(operation)
        try:
            if from_doc:
                return select(file.document, self._path(path, file, say), file.document.m_top) if path else []
            if not path:
                return bases
            rel = self._path(path.lstrip("/"), file, say)
            return [node for base in bases for node in select(file.document, rel, base)]
        except (CXPathError, RecursionError) as exc:
            say("xpath", f"cannot evaluate {path!r}: {exc}")
            return []

    def _path(self, path: str, file: _SFile, say) -> str:
        expanded = file.variables.expand(path, file.mod.m_mod_id)
        for name in expanded.unknown:
            say("variable", f"Variable '{name}' not found")
        return expanded.text

    def _apply(self, file: _SFile, kind: str, targets: list, body: list[ET.Element]) -> list:
        """The operation on each target, with its own copy of the content; returns the nodes it touched."""
        document, touched = file.document, []

        def calc(expression: str, node):
            return self._calc(expression, node, file)

        for target in targets:
            if not isinstance(target, ET.Element):
                continue
            new = copy.deepcopy(body)
            if kind == "merge":
                content.merge(document, target, new, calc)
                touched.append(target)
                continue
            if kind == "remove":
                document.remove(target)
                touched.append(target)
                continue
            for element in new:
                content.resolve(document, element, target, calc)
            if kind == "add":
                document.insert(target, len(document.children(target)), new)
                self._keep(document, new)
                touched.append(target)
            elif document.parent(target) is not None:
                index = document.index_of(target)
                document.insert(document.parent(target), index if kind == "addPrevSibling" else index + 1, new)
                self._keep(document, new)
                if kind == "replace":
                    document.remove(target)
                touched += new
        return touched

    def _keep(self, document: CPatchDoc, added: list[ET.Element]) -> None:
        """With ``keep``: every asset just added to an ``<Assets>`` is frozen, so a mod that adds a hundred thousand
        holds their bytes, not their trees."""
        if self.m_keep is None:
            return
        for element in added:
            parent = document.parent(element)
            if element.tag == "Asset" and parent is not None and parent.tag == "Assets":
                document.freeze(element, self.m_keep(document, element))

    def _new_assets(self, file: _SFile, assets: list[ET.Element], element: ET.Element) -> None:
        say = self._say(file.mod, file.where(element))
        target = assets_target(file.document)
        if target is None:
            return say("nomatch", "No matching node for the asset list (no group holds <Assets>)")
        new = copy.deepcopy(assets)
        file.document.insert(target, len(file.document.children(target)), new)
        self._keep(file.document, new)
        self.m_applied[file.mod.m_mod_id] += 1
        if self.m_observe:
            self.m_observe(file.mod.m_mod_id, file.game_path, "assets", new)


@dataclass(frozen=True)
class _SOp:
    kind: str
    path: str
    short: bool  # a shorthand: the path starts at ``Values``
    guid: str | None


def _picks(operation: ET.Element) -> bool:
    return any(operation.get(asset) for asset in ("GUID", "Property", "Template"))


def _int(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default


def _described(operation: ET.Element, flags: _SOp) -> str:
    base = f"GUID {flags.guid}: " if flags.guid else ""
    return base + (flags.path or "")


def _quiet(*_arguments, **_keywords) -> None:
    return None


def run_text(
    document: CPatchDoc, data: bytes | str, mod: CMod | None = None, variables: CVariables | None = None
) -> list[SEvent]:
    """One ModOps document applied to ``document`` (as the patch file ``patch.xml`` of ``mod``); returns the events."""
    mod = mod or CMod(Path("."), {}, "patch")
    path = "patch.xml"
    session = CSession(lambda _message: None, m_docs={path: document})
    session.apply_file(
        mod, path, data.encode("utf-8") if isinstance(data, str) else data, variables or CVariables([mod])
    )
    return session.m_events


def assets_target(document: CPatchDoc) -> ET.Element | None:
    """Where new assets go: the last ``<Assets>`` of the last top-level group that holds one directly."""
    last = None
    for root in document.roots:
        for groups in document.children(root):
            if groups.tag != "Groups":
                continue
            for group in document.children(groups):
                held = (
                    [child for child in document.children(group) if child.tag == "Assets"]
                    if group.tag == "Group"
                    else []
                )
                last = held[-1] if held else last
    return last


def parse_lines(data: bytes) -> tuple[ET.Element | None, dict | str]:
    """An element tree with the line of each element (as the loader's log counts them); on a parse error None and
    the message."""
    parser = expat.ParserCreate()
    builder = ET.TreeBuilder()
    lines: dict = {}

    def start(tag, attrs):
        lines[builder.start(tag, attrs)] = parser.CurrentLineNumber

    parser.StartElementHandler = start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.buffer_text = True
    try:
        parser.Parse(data, True)
        return builder.close(), lines
    except expat.ExpatError as exc:
        return None, str(exc)
