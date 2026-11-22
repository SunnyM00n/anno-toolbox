"""XPath 1.0 expressions as the mod loader reads them in ``Path``, ``Condition``, ``Content`` and ``ModValue Insert``:
tokens, then a tree of tuples that ``xpath.evaluate`` walks. Stdlib only.

``parse(text)`` returns the tree or raises ``CXPathError``. The tree's nodes: ``("lit", str)``, ``("num", float)``,
``("fn", name, args)``, ``("bin", op, a, b)``, ``("neg", a)``, ``("union", a, b)``, ``("path", absolute, steps)``,
``("filter", primary, predicates, steps)``; a step is ``(axis, test, predicates)``. The grammar is the W3C's
(``docs/tool-suite.md`` section 17).
"""

from __future__ import annotations

import re
from functools import lru_cache

AXES = frozenset(
    {
        "ancestor", "ancestor-or-self", "attribute", "child", "descendant", "descendant-or-self", "following",
        "following-sibling", "parent", "preceding", "preceding-sibling", "self",
    }
)  # fmt: skip
NODE_TYPES = frozenset({"node", "text", "comment", "processing-instruction"})
OPERATOR_NAMES = frozenset({"and", "or", "div", "mod"})
TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d*)?|\.\d+)|(?P<str>\"[^\"]*\"|'[^']*')|(?P<op>//|::|!=|<=|>=|\.\.|[/\[\]()@,|=<>+\-*.$])"
    r"|(?P<name>[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?))"
)
DESCEND = ("descendant-or-self", ("type", "node"), ())  # what "//" stands for
# after these a "*" is a name test and "and" a name (the spec's lexical rule)
AFTER_THESE = frozenset({"@", "::", "(", "[", ",", "/", "//", "|", "+", "-", "=", "!=", "<", "<=", ">", ">=", "$"})


class CXPathError(ValueError):
    """An expression the evaluator cannot read."""


def tokens(text: str) -> list[tuple[str, str]]:
    """(kind, text) pairs: num, str, op, name; ``*`` and the operator names marked as operators where the grammar says."""
    out: list[tuple[str, str]] = []
    position, end = 0, len(text.rstrip())
    while position < end:
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise CXPathError(f"cannot read {text[position : position + 20]!r} in {text!r}")
        position = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        previous = out[-1] if out else None
        operand_before = (
            previous is not None and previous[0] != "oper" and not (previous[0] == "op" and previous[1] in AFTER_THESE)
        )
        if kind == "op" and value == "*" and operand_before:
            kind = "oper"
        elif kind == "name" and value in OPERATOR_NAMES and operand_before:
            kind = "oper"
        out.append((kind, value))
    return out


class _CParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = tokens(text)
        self.i = 0

    def peek(self, k: int = 0) -> tuple[str, str] | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise CXPathError(f"unexpected end of {self.text!r}")
        self.i += 1
        return tok

    def accept(self, kind: str, value: str | None = None) -> bool:
        tok = self.peek()
        if tok is not None and tok[0] == kind and (value is None or tok[1] == value):
            self.i += 1
            return True
        return False

    def expect(self, kind: str, value: str) -> None:
        if not self.accept(kind, value):
            raise CXPathError(f"expected {value!r} at token {self.i} of {self.text!r}")

    # expressions, lowest precedence first
    def expr(self):
        return self._binary(0)

    LEVELS = (("or",), ("and",), ("=", "!="), ("<", "<=", ">", ">="), ("+", "-"), ("*", "div", "mod"))

    def _binary(self, level: int):
        if level == len(self.LEVELS):
            return self.unary()
        node = self._binary(level + 1)
        while (tok := self.peek()) is not None and tok[1] in self.LEVELS[level] and tok[0] in ("op", "oper"):
            if tok[0] == "op" and tok[1] == "*":
                break  # a name test, not a multiplication
            self.i += 1
            node = ("bin", tok[1], node, self._binary(level + 1))
        return node

    def unary(self):
        if self.accept("op", "-"):
            return ("neg", self.unary())
        node = self.path_expr()
        while self.accept("op", "|"):
            node = ("union", node, self.path_expr())
        return node

    def path_expr(self):
        tok = self.peek()
        if tok is None:
            raise CXPathError(f"unexpected end of {self.text!r}")
        if self._starts_filter(tok):
            primary = self.primary()
            preds = self.predicates()
            steps = self.rest_steps()
            return ("filter", primary, preds, steps) if preds or steps else primary
        if tok == ("op", "/"):
            self.i += 1
            next_token = self.peek()
            starts_step = next_token is not None and (next_token[0] == "name" or next_token[1] in ("*", "@", ".", ".."))
            return ("path", True, self.relative() if starts_step else ())
        if tok == ("op", "//"):
            self.i += 1
            return ("path", True, (DESCEND, *self.relative()))
        return ("path", False, self.relative())

    def _starts_filter(self, tok) -> bool:
        if tok[0] in ("num", "str") or tok == ("op", "(") or tok == ("op", "$"):
            return True
        next_token = self.peek(1)
        return tok[0] == "name" and next_token == ("op", "(") and tok[1] not in NODE_TYPES

    def rest_steps(self) -> tuple:
        steps: list = []
        while (tok := self.peek()) is not None and tok[1] in ("/", "//") and tok[0] == "op":
            self.i += 1
            if tok[1] == "//":
                steps.append(DESCEND)
            steps.append(self.step())
        return tuple(steps)

    def relative(self) -> tuple:
        return (self.step(), *self.rest_steps())

    def step(self):
        if self.accept("op", "."):
            return ("self", ("type", "node"), ())
        if self.accept("op", ".."):
            return ("parent", ("type", "node"), ())
        axis = "child"
        if self.accept("op", "@"):
            axis = "attribute"
        elif (tok := self.peek()) is not None and tok[0] == "name" and self.peek(1) == ("op", "::"):
            if tok[1] not in AXES:
                raise CXPathError(f"unknown axis {tok[1]!r} in {self.text!r}")
            axis = tok[1]
            self.i += 2
        return (axis, self.node_test(), self.predicates())

    def node_test(self):
        tok = self.take()
        if tok == ("op", "*"):
            return ("name", "*")
        if tok[0] not in ("name", "oper") or tok[1] == "*":
            raise CXPathError(f"expected a name in {self.text!r}, found {tok[1]!r}")
        if tok[1] in NODE_TYPES and self.accept("op", "("):
            self.accept("str")
            self.expect("op", ")")
            return ("type", tok[1])
        return ("name", tok[1])

    def predicates(self) -> tuple:
        out = []
        while self.accept("op", "["):
            out.append(self.expr())
            self.expect("op", "]")
        return tuple(out)

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", float(value))
        if kind == "str":
            return ("lit", value[1:-1])
        if (kind, value) == ("op", "("):
            node = self.expr()
            self.expect("op", ")")
            return node
        if (kind, value) == ("op", "$"):
            raise CXPathError(f"variable {self.peek()} left in {self.text!r} (the loader replaces them first)")
        self.expect("op", "(")
        arguments = []
        if not self.accept("op", ")"):
            arguments.append(self.expr())
            while self.accept("op", ","):
                arguments.append(self.expr())
            self.expect("op", ")")
        return ("fn", value, tuple(arguments))


@lru_cache(maxsize=4096)
def parse(text: str):
    """The tree of an expression (cached: a mod repeats its paths)."""
    parser = _CParser(text)
    if not parser.toks:
        raise CXPathError("an empty expression")
    try:
        node = parser.expr()
    except RecursionError:
        raise CXPathError(f"{text[:40]!r}: nested too deeply") from None
    if parser.peek() is not None:
        raise CXPathError(f"unexpected {parser.peek()[1]!r} in {text!r}")
    return node
