"""The mod loader's patching, run outside the game: each mod's patch files applied to the game's files in load order,
every operation as the loader does it, and every case the loader would skip or log, reported. Stdlib only.

``Session(read_base)`` holds the patched documents (``patchdoc.PatchDoc``, one per game file); ``apply_mod(mod,
variables)`` runs one mod (``mods.Mod``); ``events`` lists what the loader would log or silently drop, by mod, file
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
from .mods import Mod, Variables, patch_target
from .patchdoc import PatchDoc
from .xpath import XPathError, boolean, evaluate, select

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
class Event:
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
class _File:
    """The running state of one patch file (or include) of one mod."""

    mod: Mod
    game_path: str
    doc: PatchDoc
    rel: str
    lines: dict
    variables: Variables

    def where(self, el: ET.Element) -> str:
        return f"{self.rel}:{self.lines.get(el, 0)}"


@dataclass
class Session:
    """One run of the loader over several mods: ``read_base(game path)`` gives a game file's bytes (None: no such
    file); ``lazy(game path)`` the asset ranges of a large one (``PatchDoc.load``); ``observe`` is told of each
    applied operation (mod id, game path, kind, touched nodes); ``written`` also of its targets and the content it
    put in, before the operation runs (to see what a mod set even where the value was the same)."""

    read_base: Callable[[str], bytes | None]
    lazy: Callable[[str], list | None] | None = None
    observe: Callable[[str, str, str, list], None] | None = None
    written: Callable[[str, str, str, list, list], None] | None = None  # (mod id, game path, kind, targets, content)
    keep: Callable[[PatchDoc, ET.Element], object] | None = None  # asset the loader adds -> a note; it is kept as bytes
    game: str = GAME_117  # ``GAME_1800``: what 1800's loader does not read is left out and said ("Anno 1800's loader")
    docs: dict[str, PatchDoc] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    applied: Counter = field(default_factory=Counter)  # mod id -> operations applied
    missing: set[str] = field(default_factory=set)
    unreadable: dict[str, str] = field(default_factory=dict)  # game file that is not XML -> the parser's message

    def document(self, game_path: str) -> PatchDoc | None:
        """The patched document of a game file, read on first use; None for a file the game lacks (``missing``) or
        one that is not XML (``unreadable``), so the other files of the run are still patched."""
        key = game_path.lower()
        if key not in self.docs and key not in self.missing and key not in self.unreadable:
            data = self.read_base(key)
            if data is None:
                self.missing.add(key)
                return None
            try:
                self.docs[key] = PatchDoc.load(data, self.lazy(key) if self.lazy else None)
            except ET.ParseError as exc:
                self.unreadable[key] = str(exc)
        return self.docs.get(key)

    def apply_mod(self, mod: Mod, variables: Variables) -> None:
        for rel, _path in mod.patch_files():
            self.apply_file(mod, rel, mod.read(rel), variables)

    def apply_file(self, mod: Mod, rel: str, data: bytes, variables: Variables) -> None:
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
        doc = self.document(target)
        if doc is None and target.lower() in self.unreadable:
            return say("badbase", f"Can't read the file to patch: {target} ({self.unreadable[target.lower()]})")
        if doc is None:
            return say("nobase", f"Can't find file to patch: {target}")
        self._run(root, _File(mod, target, doc, rel, lines, variables), 0)

    def _say(self, mod: Mod, where: str):
        def say(code: str, message: str, level: str = WARNING, others: tuple = ()) -> None:
            self.events.append(Event(mod.mod_id, where, code, message, level, others))

        return say

    # the elements of a ModOps document
    def _run(self, parent: ET.Element, f: _File, depth: int) -> None:
        for el in parent:
            if not isinstance(el.tag, str):
                continue
            say = self._say(f.mod, f.where(el))
            if el.tag == "ModOp":
                self._modop(el, f)
            elif el.tag == "Asset" and self.game == GAME_1800:
                say("feature", "<Asset> outside a ModOp is read by 117's loader only: 1800 ignores it")
            elif el.tag == "Asset":
                self._new_assets(f, [el], el)
            elif el.tag == "Assets":
                self._new_assets(f, [a for a in el if a.tag == "Asset"], el)
            elif el.tag == "Group":
                if "Skip" in el.attrib:
                    say("skip", "Group skipped (Skip)", INFO)
                else:
                    self._group(el, f, depth, say)
            elif el.tag == "Include":
                self._include(el, f, depth, say)
            else:
                say("child", f"<{el.tag}> is not ModOp, Asset, Assets, Group or Include: ignored")

    def _group(self, el: ET.Element, f: _File, depth: int, say) -> None:
        """A ``Group``: its operations while its ``Condition`` holds, again up to ``MaxRepeat`` times (117; 1800 does
        not read the attribute)."""
        repeat = 1
        if self.game != GAME_1800 and "MaxRepeat" in el.attrib:
            repeat = max(1, min(MAX_REPEAT, _int(el.get("MaxRepeat"), 1)))
        for turn in range(repeat):
            if not self._holds(el, f, f.doc.top, say if turn == 0 else _quiet):
                return
            self._run(el, f, depth)

    def _include(self, el: ET.Element, f: _File, depth: int, say) -> None:
        if "Skip" in el.attrib:
            return say("skip", "Include skipped (Skip)", INFO)
        if not self._holds(el, f, f.doc.top, say):
            return
        ref = el.get("File", "")
        rel = ref.lstrip("/") if ref.startswith("/") else posixpath.join(posixpath.dirname(f.rel), ref)
        rel = posixpath.normpath(rel)
        usable = ref and not rel.startswith("..") and f.mod.is_file(rel) and depth < MAX_INCLUDE_DEPTH
        data = f.mod.read(rel) if usable else b""
        root, lines = parse_lines(data) if data.strip() else (None, "")
        if root is None or root.tag != "ModOps":
            return say("include", f"Include file missing or empty: {ref}")
        self._run(root, _File(f.mod, f.game_path, f.doc, rel, lines, f.variables), depth + 1)

    def _holds(self, el: ET.Element, f: _File, node, say) -> bool:
        """Whether ``el``'s ``Condition`` holds at ``node`` (true without one); a false one is said."""
        cond = el.get("Condition")
        if cond is None:
            return True
        text, negate = cond.strip(), False
        if text.startswith("!") and not text.startswith("!="):
            text, negate = text[1:].strip(), True
        found = self._calc(text, node, f, say)
        holds = (not boolean(found)) if negate else boolean(found)
        expanded = f.variables.expand(text, f.mod.mod_id)
        if expanded.mods:
            others = tuple(sorted(expanded.mods))
            say("modcondition", f"Condition {cond!r} asks about {', '.join(others)}: {holds}", INFO, others)
        if not holds:
            say("condition", f"Condition {cond!r} doesn't match", INFO)
        return holds

    def _calc(self, expr: str, node, f: _File, say=None):
        """An expression's value at ``node`` with the mod's variables; an unreadable one is said and is empty."""
        expanded = f.variables.expand(expr, f.mod.mod_id)
        for name in expanded.unknown:
            if say:
                say("variable", f"Variable '{name}' not found")
        try:
            return evaluate(f.doc, expanded.text, node)
        except (XPathError, ZeroDivisionError, RecursionError) as exc:
            if say:
                say("xpath", f"cannot evaluate {expr!r}: {exc}")
            return []

    # one ModOp
    def _modop(self, op: ET.Element, f: _File) -> None:
        say = self._say(f.mod, f.where(op))
        if "Skip" in op.attrib:
            return say("skip", "ModOp skipped (Skip)", INFO)
        if self.game == GAME_1800:
            op = self._as_1800(op, say)
        kind, path, short = op.get("Type"), op.get("Path", ""), False
        for attr, meaning in SHORTHANDS.items():
            if attr in op.attrib:
                kind, path, short = kind or meaning, op.get(attr, ""), True
                break
        if kind is None:
            return say("notype", "ModOp has no Type and no Add=, Merge=, ...: it does nothing")
        if self.game == GAME_1800:
            kind = TYPE_NAMES.get(kind.lower(), kind)
        if kind not in TYPES:
            return say("type", f"Unknown ModOp {kind}")
        kind = ALIASES.get(kind, kind)
        guids = [g.strip() for g in op.get("GUID", "").split(",") if g.strip()] or [None]
        for guid in guids:
            self._one(op, f, say, _Op(kind, path, short, guid))

    @staticmethod
    def _as_1800(op: ET.Element, say) -> ET.Element:
        """``op`` as 1800's loader reads it: without the attributes only 117 reads (said), the content as it is."""
        found = sorted(a for a in op.attrib if a in ONLY_117)
        if not found:
            return op
        say("feature", f"{', '.join(found)}: read by 117's loader only, 1800 ignores it")
        plain = ET.Element(op.tag, {k: v for k, v in op.attrib.items() if k not in ONLY_117})
        plain.extend(op)
        return plain

    def _one(self, op: ET.Element, f: _File, say, o: _Op) -> None:
        body = [c for c in op if isinstance(c.tag, str)]
        if o.kind == "assets" or o.kind == "add" and not o.path and o.guid is None and not _picks(op):
            return self._new_assets(f, [c for c in body if c.tag == "Asset"], op)
        repeat = max(1, min(MAX_REPEAT, _int(op.get("MaxRepeat"), 1))) if op.get("Condition") else 1
        for turn in range(repeat):
            bases = self._bases(op, f, o)
            node = bases[0] if bases else f.doc.top
            if not self._holds(op, f, node, say if turn == 0 else _quiet):
                return
            targets = self._targets(op, f, o, bases, say)
            if not targets:
                if turn == 0:
                    level = INFO if "AllowNoMatch" in op.attrib else WARNING
                    say("nomatch", f'No matching node for Path "{_described(op, o)}"', level)
                return
            payload = body
            if "Content" in op.attrib:
                payload = content.fill(copy.deepcopy(body), self._calc(op.get("Content"), node, f, say))
            if self.written:
                self.written(f.mod.mod_id, f.game_path, o.kind, targets, payload)
            touched = self._apply(f, o.kind, targets, payload)
            self.applied[f.mod.mod_id] += 1
            if self.observe:
                self.observe(f.mod.mod_id, f.game_path, o.kind, touched)

    def _bases(self, op: ET.Element, f: _File, o: _Op) -> list:
        """The nodes a path starts from: the asset (``Type=``) or its ``Values`` (a shorthand) by GUID, a property
        block in every asset, a template; none for a path from the document."""
        doc = f.doc
        if o.guid is not None:
            found = doc.assets(o.guid)
        elif op.get("Property"):
            found = [a for users in list(doc.by_guid.values()) for a in users]
            found = [v for a in found for v in doc.children(a) if v.tag == "Values"]
            return [p for v in found for p in doc.children(v) if p.tag == op.get("Property")]
        elif op.get("Template"):
            name = op.get("Template")
            found = [t for t in doc.descendants(doc.top, "Template") if t.findtext("Name") == name]
            return [c for t in found for c in doc.children(t) if c.tag == "Properties"] if o.short else found
        else:
            return []
        return [v for a in found for v in doc.children(a) if v.tag == "Values"] if o.short else found

    def _targets(self, op: ET.Element, f: _File, o: _Op, bases: list, say) -> list:
        path = o.path.replace("&gt;", ">").replace("&lt;", "<").strip()
        if len(path) > 1:
            path = path.rstrip("/")
        if path == "/":
            return f.doc.roots
        from_doc = path.startswith(("//", "@")) or not _picks(op)
        try:
            if from_doc:
                return select(f.doc, self._path(path, f, say), f.doc.top) if path else []
            if not path:
                return bases
            rel = self._path(path.lstrip("/"), f, say)
            return [n for b in bases for n in select(f.doc, rel, b)]
        except (XPathError, RecursionError) as exc:
            say("xpath", f"cannot evaluate {path!r}: {exc}")
            return []

    def _path(self, path: str, f: _File, say) -> str:
        expanded = f.variables.expand(path, f.mod.mod_id)
        for name in expanded.unknown:
            say("variable", f"Variable '{name}' not found")
        return expanded.text

    def _apply(self, f: _File, kind: str, targets: list, body: list[ET.Element]) -> list:
        """The operation on each target, with its own copy of the content; returns the nodes it touched."""
        doc, touched = f.doc, []

        def calc(expr: str, node):
            return self._calc(expr, node, f)

        for target in targets:
            if not isinstance(target, ET.Element):
                continue
            new = copy.deepcopy(body)
            if kind == "merge":
                content.merge(doc, target, new, calc)
                touched.append(target)
                continue
            if kind == "remove":
                doc.remove(target)
                touched.append(target)
                continue
            for el in new:
                content.resolve(doc, el, target, calc)
            if kind == "add":
                doc.insert(target, len(doc.children(target)), new)
                self._keep(doc, new)
                touched.append(target)
            elif doc.parent(target) is not None:
                at = doc.index_of(target)
                doc.insert(doc.parent(target), at if kind == "addPrevSibling" else at + 1, new)
                self._keep(doc, new)
                if kind == "replace":
                    doc.remove(target)
                touched += new
        return touched

    def _keep(self, doc: PatchDoc, added: list[ET.Element]) -> None:
        """With ``keep``: every asset just added to an ``<Assets>`` is frozen, so a mod that adds a hundred thousand
        holds their bytes, not their trees."""
        if self.keep is None:
            return
        for el in added:
            up = doc.parent(el)
            if el.tag == "Asset" and up is not None and up.tag == "Assets":
                doc.freeze(el, self.keep(doc, el))

    def _new_assets(self, f: _File, assets: list[ET.Element], el: ET.Element) -> None:
        say = self._say(f.mod, f.where(el))
        target = assets_target(f.doc)
        if target is None:
            return say("nomatch", "No matching node for the asset list (no group holds <Assets>)")
        new = copy.deepcopy(assets)
        f.doc.insert(target, len(f.doc.children(target)), new)
        self._keep(f.doc, new)
        self.applied[f.mod.mod_id] += 1
        if self.observe:
            self.observe(f.mod.mod_id, f.game_path, "assets", new)


@dataclass(frozen=True)
class _Op:
    kind: str
    path: str
    short: bool  # a shorthand: the path starts at ``Values``
    guid: str | None


def _picks(op: ET.Element) -> bool:
    return any(op.get(a) for a in ("GUID", "Property", "Template"))


def _int(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default


def _described(op: ET.Element, o: _Op) -> str:
    base = f"GUID {o.guid}: " if o.guid else ""
    return base + (o.path or "")


def _quiet(*_args, **_kw) -> None:
    return None


def run_text(
    doc: PatchDoc, data: bytes | str, mod: Mod | None = None, variables: Variables | None = None
) -> list[Event]:
    """One ModOps document applied to ``doc`` (as the patch file ``patch.xml`` of ``mod``); returns the events."""
    mod = mod or Mod(Path("."), {}, "patch")
    path = "patch.xml"
    session = Session(lambda _p: None, docs={path: doc})
    session.apply_file(
        mod, path, data.encode("utf-8") if isinstance(data, str) else data, variables or Variables([mod])
    )
    return session.events


def assets_target(doc: PatchDoc) -> ET.Element | None:
    """Where new assets go: the last ``<Assets>`` of the last top-level group that holds one directly."""
    last = None
    for root in doc.roots:
        for groups in doc.children(root):
            if groups.tag != "Groups":
                continue
            for group in doc.children(groups):
                held = [c for c in doc.children(group) if c.tag == "Assets"] if group.tag == "Group" else []
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
