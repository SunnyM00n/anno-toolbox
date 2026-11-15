"""XPath 1.0 evaluated over a ``patchdoc.PatchDoc``: node sets of elements, attributes and texts, numbers, strings
and booleans, the core functions. Stdlib only.

``evaluate(doc, expr, node)`` is the entry (``select`` when a node set is wanted); the tree comes from
``xpathparse.parse``. Two shortcuts keep a large asset file fast without changing a result: a key test such as
``Text[LineId='5']`` is looked up in ``PatchDoc.keyed``, and ``//Values[Standard/GUID='5']`` reads only the lazy
assets whose bytes hold the ``5`` (``docs/tool-suite.md`` section 17).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

from .patchdoc import DOCUMENT, PatchDoc
from .xpathparse import DESCEND, XPathError, parse

PLAIN = re.compile(r"[\w .:;,+\-]+")  # a literal whose bytes in the file are the literal itself
ARITHMETIC = frozenset({"+", "-", "*", "div", "mod"})
NUMERIC = frozenset({"number", "sum", "count", "round", "floor", "ceiling", "string-length", "position", "last"})


@dataclass(frozen=True)
class Attr:
    owner: ET.Element
    name: str
    value: str


@dataclass(frozen=True)
class TextNode:
    owner: ET.Element
    value: str


def evaluate(doc: PatchDoc, expr, node=None):
    """The value of ``expr`` (text or a parsed tree) with ``node`` as the context (the document node when None)."""
    tree = parse(expr) if isinstance(expr, str) else expr
    return _Eval(doc).value(tree, doc.top if node is None else node, 1, 1)


def select(doc: PatchDoc, expr, node=None) -> list:
    found = evaluate(doc, expr, node)
    if not isinstance(found, list):
        raise XPathError(f"{expr!r} is not a path (it gives {format_value(found)!r})")
    return found


def boolean(value) -> bool:
    if isinstance(value, list):
        return bool(value)
    if isinstance(value, float):
        return value != 0 and not math.isnan(value)
    return bool(value)


def to_number(text: str) -> float:
    try:
        return float(text.strip()) if re.fullmatch(r"\s*-?(\d+\.?\d*|\.\d+)\s*", text) else math.nan
    except ValueError:
        return math.nan


def format_number(x: float) -> str:
    """A number as XPath's ``string()`` writes it (15 significant digits, no exponent: not checked in game)."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e18:
        return str(int(x))
    text = f"{x:.15g}"
    if "e" in text:
        text = f"{x:.15f}".rstrip("0").rstrip(".")
    return text


def string_of(doc: PatchDoc, node) -> str:
    if isinstance(node, (Attr, TextNode)):
        return node.value
    return doc.string(node)


def format_value(value, doc: PatchDoc | None = None) -> str:
    if isinstance(value, list):
        return string_of(doc, value[0]) if value and doc is not None else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_number(value)
    return value


def _needles(step) -> tuple[bytes, ...]:
    """Texts every element the step selects must hold below it (from ``a/b = 'x'`` tests joined by ``and``)."""
    out = []
    for pred in step[2]:
        for part in _conjuncts(pred):
            test = _key_test(part)
            if test and isinstance(test[1], str) and PLAIN.fullmatch(test[1]):
                out.append(test[1].encode())
            elif test and isinstance(test[1], float) and test[1] == int(test[1]):
                out.append(str(int(test[1])).encode())
    return tuple(out)


def _conjuncts(pred):
    if pred[0] == "bin" and pred[1] == "and":
        return [*_conjuncts(pred[2]), *_conjuncts(pred[3])]
    return [pred]


@lru_cache(maxsize=4096)
def _key_test(pred) -> tuple[str, str | float] | None:
    """``(key path, literal)`` of a test ``a/b = 'x'`` or ``a/@c = 5`` (child steps only, no predicates)."""
    if pred[0] != "bin" or pred[1] != "=":
        return None
    path, lit = (pred[2], pred[3]) if pred[3][0] in ("lit", "num") else (pred[3], pred[2])
    if lit[0] not in ("lit", "num") or path[0] != "path" or path[1] or not path[2]:
        return None
    names = []
    for k, (axis, test, preds) in enumerate(path[2]):
        last = k == len(path[2]) - 1
        if preds or test[0] != "name" or test[1] == "*" or not (axis == "child" or axis == "attribute" and last):
            return None
        names.append(("@" if axis == "attribute" else "") + test[1])
    key = "/".join(names).replace("/@", "@")
    return key, lit[1]


def _positional(pred) -> bool:
    """Whether a predicate may depend on the position: a number value, ``position()`` or ``last()`` in it."""
    if pred[0] in ("num", "neg", "filter") or pred[0] == "bin" and pred[1] in ARITHMETIC:
        return True
    return pred[0] == "fn" and pred[1] in NUMERIC or _uses_position(pred)


def _uses_position(node) -> bool:
    if node[0] == "fn" and node[1] in ("position", "last"):
        return True
    parts = {"fn": lambda: node[2], "bin": lambda: node[2:], "neg": lambda: node[1:], "union": lambda: node[1:]}
    return any(_uses_position(p) for p in parts.get(node[0], tuple)())  # a path's predicates are its own


class _Eval:
    def __init__(self, doc: PatchDoc) -> None:
        self.doc = doc

    def value(self, t, node, pos: int, size: int):
        kind = t[0]
        if kind == "lit":
            return t[1]
        if kind == "num":
            return t[1]
        if kind == "path":
            start = [self.doc.top] if t[1] else [node]
            return self.steps(start, t[2])
        if kind == "bin":
            return self.binary(t, node, pos, size)
        if kind == "neg":
            return -self.number(self.value(t[1], node, pos, size))
        if kind == "union":
            a, b = self.value(t[1], node, pos, size), self.value(t[2], node, pos, size)
            if not isinstance(a, list) or not isinstance(b, list):
                raise XPathError("| joins node sets only")
            return _unique(a + b)
        if kind == "filter":
            found = self.value(t[1], node, pos, size)
            if not isinstance(found, list):
                raise XPathError("a predicate or a path after a value that is not a node set")
            return self.steps(self.predicates(found, t[2]), t[3])
        if kind == "fn":
            return self.function(t[1], t[2], node, pos, size)
        raise XPathError(f"unknown expression {kind}")

    # paths
    def steps(self, nodes: list, steps: tuple) -> list:
        k = 0
        while k < len(steps):
            step = steps[k]
            nxt = steps[k + 1] if k + 1 < len(steps) else None
            if step == DESCEND and nxt and nxt[0] == "child" and not any(_positional(p) for p in nxt[2]):
                nodes = self.descend(nodes, nxt)
                k += 2
                continue
            nodes = _unique([m for n in nodes for m in self.step(n, step)])
            k += 1
        return nodes

    def descend(self, nodes: list, step) -> list:
        """``//name[...]``: the descendants by name, the lazy assets that cannot match left unread."""
        test = step[1]
        tag = test[1] if test[0] == "name" and test[1] != "*" else None
        key = _key_test(step[2][0]) if step[2] and tag else None
        by_guid = key and isinstance(key[1], str) and PLAIN.fullmatch(key[1]) and key[0].endswith("Standard/GUID")
        hold = _needles(step)
        out = []
        for n in nodes:
            if not isinstance(n, ET.Element):
                continue
            if by_guid:  # ``//Values[Standard/GUID='5']``: the GUID index instead of every element
                found = [e for e in self.doc.guid_holders(key[1], (tag, *key[0].split("/"))) if self._under(e, n)]
                out += self.predicates(found, step[2][1:])
                continue
            found = [e for e in self.doc.descendants(n, tag, hold) if self.matches(e, test, "child")]
            out += self.predicates(found, step[2])
        return _unique(out)

    def _under(self, el: ET.Element, ancestor: ET.Element) -> bool:
        node = self.doc.parent(el)
        while node is not None and node is not ancestor:
            node = self.doc.parent(node)
        return node is ancestor

    def step(self, node, step) -> list:
        axis, test, preds = step
        if axis == "child" and preds and test[0] == "name" and test[1] != "*" and isinstance(node, ET.Element):
            key = _key_test(preds[0])
            if key and isinstance(key[1], str):
                found = list(self.doc.keyed(node, test[1], key[0]).get(key[1], ()))
                return self.predicates(found, preds[1:])
        found = [n for n in self.axis(node, axis, test) if self.matches(n, test, axis)]
        return self.predicates(found, preds)

    def predicates(self, nodes: list, preds) -> list:
        for pred in preds:
            if not nodes:
                break
            size = len(nodes)
            key = _key_test(pred)
            if key is not None:
                nodes = [n for n in nodes if self.key_holds(n, key)]
                continue
            kept = []
            for i, n in enumerate(nodes, 1):
                v = self.value(pred, n, i, size)
                if (v == i) if isinstance(v, float) else boolean(v):
                    kept.append(n)
            nodes = kept
        return nodes

    def key_holds(self, node, key: tuple[str, str | float]) -> bool:
        """``a/b = 'x'`` on one node, read with the element tree's own path search (the same result, faster)."""
        if not isinstance(node, ET.Element):
            return False
        found = self.doc.key_values(node, key[0])
        if isinstance(key[1], str):
            return key[1] in found
        return any(to_number(v) == key[1] for v in found)

    def matches(self, node, test, axis: str) -> bool:
        kind, name = test
        if kind == "type":
            if name == "node":
                return True
            return name == "text" and isinstance(node, TextNode)
        if axis == "attribute":
            return isinstance(node, Attr) and (name == "*" or node.name == name)
        return isinstance(node, ET.Element) and node.tag != DOCUMENT and (name == "*" or node.tag == name)

    def axis(self, node, axis: str, test) -> list:
        """The nodes of an XPath axis from ``node``, in document order (reverse axes nearest first)."""
        doc = self.doc
        if not isinstance(node, ET.Element):
            return [node.owner] if axis in ("parent", "ancestor") else [node] if axis.startswith("self") else []
        if axis == "child":
            kids = doc.children(node)
            if test == ("type", "text"):
                return [TextNode(node, t) for t in [node.text, *(c.tail for c in kids)] if t]
            return kids
        if axis == "attribute":
            if node in doc.lazy:
                doc.materialize(node)
            return [Attr(node, k, v) for k, v in node.attrib.items()]
        if axis == "self":
            return [node]
        if axis in ("descendant", "descendant-or-self"):
            tag = test[1] if test[0] == "name" and test[1] != "*" else None
            below = doc.descendants(node, tag)
            return [node, *below] if axis == "descendant-or-self" else below
        if axis in ("parent", "ancestor", "ancestor-or-self"):
            out = [node] if axis == "ancestor-or-self" else []
            up = doc.parent(node)
            while up is not None:
                out.append(up)
                if axis == "parent":
                    break
                up = doc.parent(up)
            return out
        if axis in ("following-sibling", "preceding-sibling"):
            up = doc.parent(node)
            if up is None:
                return []
            kids = list(up)
            at = next(k for k, c in enumerate(kids) if c is node)
            return kids[at + 1 :] if axis == "following-sibling" else kids[:at][::-1]
        raise XPathError(f"the axis {axis} is not supported")

    # operators
    def binary(self, t, node, pos: int, size: int):
        op = t[1]
        if op == "or":
            return boolean(self.value(t[2], node, pos, size)) or boolean(self.value(t[3], node, pos, size))
        if op == "and":
            return boolean(self.value(t[2], node, pos, size)) and boolean(self.value(t[3], node, pos, size))
        a, b = self.value(t[2], node, pos, size), self.value(t[3], node, pos, size)
        if op in ("=", "!=", "<", "<=", ">", ">="):
            return self.compare(op, a, b)
        x, y = self.number(a), self.number(b)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "div":
            if y:
                return x / y
            return math.nan if x == 0 or math.isnan(x) else math.copysign(math.inf, x) * math.copysign(1, y)
        if op == "mod":
            return math.fmod(x, y) if y else math.nan
        raise XPathError(f"unknown operator {op}")

    def compare(self, op: str, a, b) -> bool:
        if isinstance(a, list) or isinstance(b, list):
            if isinstance(a, list) and isinstance(b, list):
                left = [string_of(self.doc, n) for n in a]
                return any(_cmp(op, x, y) for x in left for y in (string_of(self.doc, n) for n in b))
            nodes, other, flip = (a, b, False) if isinstance(a, list) else (b, a, True)
            if isinstance(other, bool):
                return _cmp(op, boolean(nodes), other, flip)
            for n in nodes:
                s = string_of(self.doc, n)
                if _cmp(op, to_number(s) if isinstance(other, float) else s, other, flip):
                    return True
            return False
        if op in ("=", "!="):
            if isinstance(a, bool) or isinstance(b, bool):
                return _cmp(op, boolean(a), boolean(b))
            if isinstance(a, float) or isinstance(b, float):
                return _cmp(op, self.number(a), self.number(b))
            return _cmp(op, a, b)
        return _cmp(op, self.number(a), self.number(b))

    def number(self, v) -> float:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, float):
            return v
        if isinstance(v, list):
            return to_number(string_of(self.doc, v[0])) if v else math.nan
        return to_number(v)

    def string(self, v) -> str:
        return format_value(v, self.doc)

    def function(self, name: str, args, node, pos: int, size: int):
        if name == "last":
            return float(size)
        if name == "position":
            return float(pos)
        vals = [self.value(a, node, pos, size) for a in args]
        return _call(self, name, vals, node)


def _cmp(op: str, x, y, flip: bool = False) -> bool:
    if flip:
        x, y = y, x
    if isinstance(x, str) and isinstance(y, str) and op in ("=", "!="):
        return (x == y) == (op == "=")
    if isinstance(x, bool) or isinstance(y, bool):
        x, y = bool(x), bool(y)
        return (x == y) == (op == "=") if op in ("=", "!=") else _cmp(op, float(x), float(y))
    if isinstance(x, str):
        x = to_number(x)
    if isinstance(y, str):
        y = to_number(y)
    return {"=": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def _unique(nodes: list) -> list:
    seen, out = set(), []
    for n in nodes:
        key = id(n) if isinstance(n, ET.Element) else n
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _call(ev: _Eval, name: str, vals: list, node):
    s = ev.string
    if name == "count":
        return float(len(vals[0]))
    if name == "not":
        return not boolean(vals[0])
    if name in ("true", "false"):
        return name == "true"
    if name == "boolean":
        return boolean(vals[0])
    if name == "number":
        return ev.number(vals[0] if vals else [node])
    if name == "string":
        return s(vals[0] if vals else [node])
    if name == "concat":
        return "".join(s(v) for v in vals)
    if name == "contains":
        return s(vals[1]) in s(vals[0])
    if name == "starts-with":
        return s(vals[0]).startswith(s(vals[1]))
    if name == "ends-with":
        return s(vals[0]).endswith(s(vals[1]))
    if name == "string-length":
        return float(len(s(vals[0] if vals else [node])))
    if name == "normalize-space":
        return " ".join(s(vals[0] if vals else [node]).split())
    if name == "substring-before":
        a, b = s(vals[0]), s(vals[1])
        return a.split(b, 1)[0] if b in a else ""
    if name == "substring-after":
        a, b = s(vals[0]), s(vals[1])
        return a.split(b, 1)[1] if b in a else ""
    if name == "substring":
        return _substring(s(vals[0]), ev.number(vals[1]), ev.number(vals[2]) if len(vals) > 2 else math.inf)
    if name == "translate":
        a, src, dst = s(vals[0]), s(vals[1]), s(vals[2])
        table = {ord(c): (dst[k] if k < len(dst) else None) for k, c in reversed(list(enumerate(src)))}
        return a.translate(table)
    if name == "sum":
        return float(sum(to_number(string_of(ev.doc, n)) for n in vals[0]))
    if name in ("floor", "ceiling", "round"):
        x = ev.number(vals[0])
        if math.isnan(x) or math.isinf(x):
            return x
        return float({"floor": math.floor, "ceiling": math.ceil, "round": lambda v: math.floor(v + 0.5)}[name](x))
    if name in ("name", "local-name"):
        nodes = vals[0] if vals else [node]
        n = nodes[0] if nodes else None
        return n.name if isinstance(n, Attr) else n.tag if isinstance(n, ET.Element) and n.tag != DOCUMENT else ""
    raise XPathError(f"the function {name}() is not supported")


def _substring(text: str, start: float, length: float) -> str:
    if math.isnan(start) or math.isnan(length):
        return ""
    first = math.floor(start + 0.5)
    last = first + (math.floor(length + 0.5) if not math.isinf(length) else math.inf)
    return "".join(c for k, c in enumerate(text, 1) if first <= k < last)
