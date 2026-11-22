"""XPath 1.0 evaluated over a ``patchdoc.CPatchDoc``: node sets of elements, attributes and texts, numbers, strings
and booleans, the core functions. Stdlib only.

``evaluate(document, expression, node)`` is the entry (``select`` when a node set is wanted); the tree comes from
``xpathparse.parse``. Two shortcuts keep a large asset file fast without changing a result: a key test such as
``Text[LineId='5']`` is looked up in ``CPatchDoc.keyed``, and ``//Values[Standard/GUID='5']`` reads only the lazy
assets whose bytes hold the ``5`` (``docs/tool-suite.md`` section 17).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

from .patchdoc import DOCUMENT, CPatchDoc
from .xpathparse import DESCEND, CXPathError, parse

PLAIN = re.compile(r"[\w .:;,+\-]+")  # a literal whose bytes in the file are the literal itself
ARITHMETIC = frozenset({"+", "-", "*", "div", "mod"})
NODE_SET_ARGUMENT = frozenset({"count", "sum"})
NUMERIC = frozenset({"number", "sum", "count", "round", "floor", "ceiling", "string-length", "position", "last"})


@dataclass(frozen=True)
class SAttr:
    owner: ET.Element
    name: str
    value: str


@dataclass(frozen=True)
class STextNode:
    owner: ET.Element
    value: str


def evaluate(document: CPatchDoc, expression, node=None):
    """The value of ``expression`` (text or a parsed tree) with ``node`` as the context (the document node when None)."""
    tree = parse(expression) if isinstance(expression, str) else expression
    return _CEval(document).value(tree, document.m_top if node is None else node, 1, 1)


def select(document: CPatchDoc, expression, node=None) -> list:
    found = evaluate(document, expression, node)
    if not isinstance(found, list):
        raise CXPathError(f"{expression!r} is not a path (it gives {format_value(found)!r})")
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


def string_of(document: CPatchDoc, node) -> str:
    if isinstance(node, (SAttr, STextNode)):
        return node.value
    return document.string(node)


def format_value(value, document: CPatchDoc | None = None) -> str:
    if isinstance(value, list):
        return string_of(document, value[0]) if value and document is not None else ""
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


def _ends_at_guid(key: str) -> bool:
    """Whether a key path ends with the elements ``Standard/GUID`` (what ``CPatchDoc.guid_holders`` has indexed)."""
    return key == "Standard/GUID" or key.endswith("/Standard/GUID")


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
    return any(
        _uses_position(predicate) for predicate in parts.get(node[0], tuple)()
    )  # a path's predicates are its own


class _CEval:
    def __init__(self, document: CPatchDoc) -> None:
        self.document = document

    def value(self, tree, node, position: int, context_size: int):
        kind = tree[0]
        if kind == "lit":
            return tree[1]
        if kind == "num":
            return tree[1]
        if kind == "path":
            start = [self.document.m_top] if tree[1] else [node]
            return self.steps(start, tree[2])
        if kind == "bin":
            return self.binary(tree, node, position, context_size)
        if kind == "neg":
            return -self.number(self.value(tree[1], node, position, context_size))
        if kind == "union":
            left, right = (
                self.value(tree[1], node, position, context_size),
                self.value(tree[2], node, position, context_size),
            )
            if not isinstance(left, list) or not isinstance(right, list):
                raise CXPathError("| joins node sets only")
            return _unique(left + right)
        if kind == "filter":
            found = self.value(tree[1], node, position, context_size)
            if not isinstance(found, list):
                raise CXPathError("a predicate or a path after a value that is not a node set")
            return self.steps(self.predicates(found, tree[2]), tree[3])
        if kind == "fn":
            return self.function(tree[1], tree[2], node, position, context_size)
        raise CXPathError(f"unknown expression {kind}")

    # paths
    def steps(self, nodes: list, steps: tuple) -> list:
        k = 0
        while k < len(steps):
            step = steps[k]
            next_step = steps[k + 1] if k + 1 < len(steps) else None
            if (
                step == DESCEND
                and next_step
                and next_step[0] == "child"
                and not any(_positional(predicate) for predicate in next_step[2])
            ):
                nodes = self.descend(nodes, next_step)
                k += 2
                continue
            nodes = _unique([match for node in nodes for match in self.step(node, step)])
            k += 1
        return nodes

    def descend(self, nodes: list, step) -> list:
        """``//name[...]``: the descendants by name, the lazy assets that cannot match left unread."""
        test = step[1]
        tag = test[1] if test[0] == "name" and test[1] != "*" else None
        key = _key_test(step[2][0]) if step[2] and tag else None
        by_guid = key and isinstance(key[1], str) and PLAIN.fullmatch(key[1]) and _ends_at_guid(key[0])
        hold = _needles(step)
        out = []
        for node in nodes:
            if not isinstance(node, ET.Element):
                continue
            if by_guid:  # ``//Values[Standard/GUID='5']``: the GUID index instead of every element
                found = [
                    element
                    for element in self.document.guid_holders(key[1], (tag, *key[0].split("/")))
                    if self._under(element, node)
                ]
                out += self.predicates(found, step[2][1:])
                continue
            found = [
                element
                for element in self.document.descendants(node, tag, hold)
                if self.matches(element, test, "child")
            ]
            out += self.predicates(found, step[2])
        return _unique(out)

    def _under(self, element: ET.Element, ancestor: ET.Element) -> bool:
        node = self.document.parent(element)
        while node is not None and node is not ancestor:
            node = self.document.parent(node)
        return node is ancestor

    def step(self, node, step) -> list:
        axis, test, preds = step
        if axis == "child" and preds and test[0] == "name" and test[1] != "*" and isinstance(node, ET.Element):
            key = _key_test(preds[0])
            if key and isinstance(key[1], str):
                found = list(self.document.keyed(node, test[1], key[0]).get(key[1], ()))
                return self.predicates(found, preds[1:])
        found = [n for n in self.axis(node, axis, test) if self.matches(n, test, axis)]
        return self.predicates(found, preds)

    def predicates(self, nodes: list, preds) -> list:
        for pred in preds:
            if not nodes:
                break
            context_size = len(nodes)
            key = _key_test(pred)
            if key is not None:
                nodes = [node for node in nodes if self.key_holds(node, key)]
                continue
            kept = []
            for i, node in enumerate(nodes, 1):
                value = self.value(pred, node, i, context_size)
                if (value == i) if isinstance(value, float) else boolean(value):
                    kept.append(node)
            nodes = kept
        return nodes

    def key_holds(self, node, key: tuple[str, str | float]) -> bool:
        """``a/b = 'x'`` on one node, read with the element tree's own path search (the same result, faster)."""
        if not isinstance(node, ET.Element):
            return False
        found = self.document.key_values(node, key[0])
        if isinstance(key[1], str):
            return key[1] in found
        return any(to_number(value) == key[1] for value in found)

    def matches(self, node, test, axis: str) -> bool:
        kind, name = test
        if kind == "type":
            if name == "node":
                return True
            return name == "text" and isinstance(node, STextNode)
        if axis == "attribute":
            return isinstance(node, SAttr) and (name == "*" or node.name == name)
        return isinstance(node, ET.Element) and node.tag != DOCUMENT and (name == "*" or node.tag == name)

    def axis(self, node, axis: str, test) -> list:
        """The nodes of an XPath axis from ``node``, in document order (reverse axes nearest first)."""
        document = self.document
        if not isinstance(node, ET.Element):
            return [node.owner] if axis in ("parent", "ancestor") else [node] if axis.startswith("self") else []
        if axis == "child":
            kids = document.children(node)
            if test == ("type", "text"):
                return [STextNode(node, tree) for tree in [node.text, *(child.tail for child in kids)] if tree]
            return kids
        if axis == "attribute":
            if node in document.m_lazy:
                document.materialize(node)
            return [SAttr(node, k, value) for k, value in node.attrib.items()]
        if axis == "self":
            return [node]
        if axis in ("descendant", "descendant-or-self"):
            tag = test[1] if test[0] == "name" and test[1] != "*" else None
            below = document.descendants(node, tag)
            return [node, *below] if axis == "descendant-or-self" else below
        if axis in ("parent", "ancestor", "ancestor-or-self"):
            out = [node] if axis == "ancestor-or-self" else []
            parent = document.parent(node)
            while parent is not None:
                out.append(parent)
                if axis == "parent":
                    break
                parent = document.parent(parent)
            return out
        if axis in ("following-sibling", "preceding-sibling"):
            parent = document.parent(node)
            if parent is None:
                return []
            kids = list(parent)
            index = next(k for k, child in enumerate(kids) if child is node)
            return kids[index + 1 :] if axis == "following-sibling" else kids[:index][::-1]
        raise CXPathError(f"the axis {axis} is not supported")

    # operators
    def binary(self, tree, node, position: int, context_size: int):
        operator = tree[1]
        if operator == "or":
            return boolean(self.value(tree[2], node, position, context_size)) or boolean(
                self.value(tree[3], node, position, context_size)
            )
        if operator == "and":
            return boolean(self.value(tree[2], node, position, context_size)) and boolean(
                self.value(tree[3], node, position, context_size)
            )
        left, right = (
            self.value(tree[2], node, position, context_size),
            self.value(tree[3], node, position, context_size),
        )
        if operator in ("=", "!=", "<", "<=", ">", ">="):
            return self.compare(operator, left, right)
        x, y = self.number(left), self.number(right)
        if operator == "+":
            return x + y
        if operator == "-":
            return x - y
        if operator == "*":
            return x * y
        if operator == "div":
            if y:
                return x / y
            return math.nan if x == 0 or math.isnan(x) else math.copysign(math.inf, x) * math.copysign(1, y)
        if operator == "mod":
            return math.fmod(x, y) if y else math.nan
        raise CXPathError(f"unknown operator {operator}")

    def compare(self, operator: str, left, right) -> bool:
        if isinstance(left, list) or isinstance(right, list):
            if isinstance(left, list) and isinstance(right, list):
                left_texts = [string_of(self.document, node) for node in left]
                return any(
                    _cmp(operator, x, y) for x in left_texts for y in (string_of(self.document, node) for node in right)
                )
            nodes, other, flip = (left, right, False) if isinstance(left, list) else (right, left, True)
            if isinstance(other, bool):
                return _cmp(operator, boolean(nodes), other, flip)
            for node in nodes:
                text = string_of(self.document, node)
                if _cmp(operator, to_number(text) if isinstance(other, float) else text, other, flip):
                    return True
            return False
        if operator in ("=", "!="):
            if isinstance(left, bool) or isinstance(right, bool):
                return _cmp(operator, boolean(left), boolean(right))
            if isinstance(left, float) or isinstance(right, float):
                return _cmp(operator, self.number(left), self.number(right))
            return _cmp(operator, left, right)
        return _cmp(operator, self.number(left), self.number(right))

    def number(self, value) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, float):
            return value
        if isinstance(value, list):
            return to_number(string_of(self.document, value[0])) if value else math.nan
        return to_number(value)

    def string(self, value) -> str:
        return format_value(value, self.document)

    def function(self, name: str, arguments, node, position: int, context_size: int):
        if name == "last":
            return float(context_size)
        if name == "position":
            return float(position)
        vals = [self.value(left, node, position, context_size) for left in arguments]
        if name in NODE_SET_ARGUMENT and not (vals and isinstance(vals[0], list)):
            raise CXPathError(f"{name}() takes a node set")
        try:
            return _call(self, name, vals, node)
        except IndexError as exc:  # an argument the function reads is not there
            raise CXPathError(f"{name}() is given {len(vals)} argument(s), too few") from exc


def _cmp(operator: str, x, y, is_flipped: bool = False) -> bool:
    if is_flipped:
        x, y = y, x
    if isinstance(x, str) and isinstance(y, str) and operator in ("=", "!="):
        return (x == y) == (operator == "=")
    if isinstance(x, bool) or isinstance(y, bool):
        x, y = bool(x), bool(y)
        return (x == y) == (operator == "=") if operator in ("=", "!=") else _cmp(operator, float(x), float(y))
    if isinstance(x, str):
        x = to_number(x)
    if isinstance(y, str):
        y = to_number(y)
    return {"=": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[operator]


def _unique(nodes: list) -> list:
    seen, out = set(), []
    for node in nodes:
        key = id(node) if isinstance(node, ET.Element) else node
        if key not in seen:
            seen.add(key)
            out.append(node)
    return out


def _call(evaluator: _CEval, name: str, vals: list, node):
    text = evaluator.string
    if name == "count":
        return float(len(vals[0]))
    if name == "not":
        return not boolean(vals[0])
    if name in ("true", "false"):
        return name == "true"
    if name == "boolean":
        return boolean(vals[0])
    if name == "number":
        return evaluator.number(vals[0] if vals else [node])
    if name == "string":
        return text(vals[0] if vals else [node])
    if name == "concat":
        return "".join(text(value) for value in vals)
    if name == "contains":
        return text(vals[1]) in text(vals[0])
    if name == "starts-with":
        return text(vals[0]).startswith(text(vals[1]))
    if name == "ends-with":
        return text(vals[0]).endswith(text(vals[1]))
    if name == "string-length":
        return float(len(text(vals[0] if vals else [node])))
    if name == "normalize-space":
        return " ".join(text(vals[0] if vals else [node]).split())
    if name == "substring-before":
        left, right = text(vals[0]), text(vals[1])
        return left.split(right, 1)[0] if right in left else ""
    if name == "substring-after":
        left, right = text(vals[0]), text(vals[1])
        return left.split(right, 1)[1] if right in left else ""
    if name == "substring":
        return _substring(
            text(vals[0]), evaluator.number(vals[1]), evaluator.number(vals[2]) if len(vals) > 2 else math.inf
        )
    if name == "translate":
        left, source_chars, target_chars = text(vals[0]), text(vals[1]), text(vals[2])
        table = {
            ord(child): (target_chars[k] if k < len(target_chars) else None)
            for k, child in reversed(list(enumerate(source_chars)))
        }
        return left.translate(table)
    if name == "sum":
        return float(sum(to_number(string_of(evaluator.doc, n)) for n in vals[0]))
    if name in ("floor", "ceiling", "round"):
        x = evaluator.number(vals[0])
        if math.isnan(x) or math.isinf(x):
            return x
        return float(
            {"floor": math.floor, "ceiling": math.ceil, "round": lambda value: math.floor(value + 0.5)}[name](x)
        )
    if name in ("name", "local-name"):
        nodes = vals[0] if vals else [node]
        n = nodes[0] if nodes else None
        return n.name if isinstance(n, SAttr) else n.tag if isinstance(n, ET.Element) and n.tag != DOCUMENT else ""
    raise CXPathError(f"the function {name}() is not supported")


def _substring(text: str, start: float, char_count: float) -> str:
    if math.isnan(start) or math.isnan(char_count):
        return ""
    first = math.floor(start + 0.5)
    last = first + (math.floor(char_count + 0.5) if not math.isinf(char_count) else math.inf)
    return "".join(char for k, char in enumerate(text, 1) if first <= k < last)
