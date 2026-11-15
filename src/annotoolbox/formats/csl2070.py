"""Anno 2070's ``.csl`` scripts (``data/ai/**``; 1404's, 1701's too): the language as a tree, and questions on it.

The scripts drive what moves on the map: a fishing boat's trips, a mine's drone, the felling worker, residents,
carriers. ``parse`` reads one file into a ``Module``; ``Api`` reads ``data/ai/cslapi.xml`` (the built-in functions,
which of them block, and the global constants such as ``_SEQ_WALK01`` or ``_feedback_transportation_droneGUID``);
``Script`` answers the questions a reader of the scripts has: every call of a function with its arguments folded to numbers
(``calls``), the sleeps in the order the script runs them (``events``), the values a variable is ever given
(``values``). Format and grammar: docs/formats/csl.md. The module does not run scripts and knows no game logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TYPES = frozenset({"var", "Pos2D", "Pos3D", "Pos2DDir", "Pos3DDir"})
_TOKEN = re.compile(
    r"""(?P<space>[ \t\r\n\f]+)
      | (?P<line>//[^\n]*)
      | (?P<block>/\*.*?\*/)
      | (?P<rev>\$\w+(?::\s*\#?(?P<revnum>\d+))?\s*\$)
      | (?P<num>0[xX][0-9a-fA-F]+|\d+)
      | (?P<str>"[^"\n]*")
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>\+\+|--|\+=|-=|\*=|/=|%=|==|!=|<=|>=|&&|\|\||[-+*/%<>=!&|~#.,;:(){}\[\]])""",
    re.VERBOSE | re.DOTALL,
)
_BINARY = (  # loosest first
    ("||",),
    ("&&",),
    ("|",),
    ("&",),
    ("==", "!="),
    ("<", ">", "<=", ">="),
    ("+", "-"),
    ("*", "/", "%"),
)


class CslError(ValueError):
    """A script the parser cannot read; the message names the line."""


# syntax tree


@dataclass(frozen=True)
class Num:
    value: int


@dataclass(frozen=True)
class Str:
    text: str


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Member:
    base: object
    field: str


@dataclass(frozen=True)
class Index:
    base: object
    index: object


@dataclass(frozen=True)
class Unary:
    op: str
    operand: object


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Call:
    """A call. ``kind``: "call" ``f(x)``, "start" ``#Module(x)`` (starts a script of its own), "start_ref"
    ``&Module(x)`` (the same, keeping the handle). ``swapscript($Revision: #1 $)`` is ``Call("swapscript", (Num(1),))``."""

    name: str
    args: tuple = ()
    kind: str = "call"
    line: int = 0


@dataclass
class Var:
    name: str
    type: str = "var"
    init: object | None = None
    size: object | None = None  # ``var chance[3]``: an array (1404, 1701)


@dataclass
class Assign:
    target: object
    op: str  # "=", "+=", "-=", "*=", "/=", "%=", "++", "--"
    value: object | None = None


@dataclass
class ExprStmt:
    expr: object


@dataclass
class If:
    cond: object
    then: list
    other: list = field(default_factory=list)


@dataclass
class While:
    cond: object
    body: list


@dataclass
class For:
    var: str
    start: object
    end: object  # ``for (i = a to b)``: b inclusive
    body: list


@dataclass
class Foreach:
    var: str
    source: object
    body: list


@dataclass
class Return:
    value: object | None = None


@dataclass
class Jump:
    kind: str  # "break" or "continue"


@dataclass
class Function:
    name: str
    params: list[str]
    body: list
    interruptable: bool = False


@dataclass
class Module:
    name: str
    params: list[str]
    directives: list[str]  # the ``#Transient`` lines before ``module``
    variables: list[Var]
    functions: dict[str, Function]
    body: list  # the module's own statements: what runs when the script starts
    kind: str = "module"  # or "framemodule" (1404, 1701)
    interruptable: bool = False  # ``interruptable module`` (1404)
    patches: list = field(default_factory=list)  # ``patchSwap(8, 9) { ... }`` after the module: (call, statements)


# reading


def _add(out: list, stmt) -> None:
    """A statement into a body: ``var a, b`` reads as several."""
    if isinstance(stmt, list):
        out.extend(stmt)
    elif stmt is not None:
        out.append(stmt)


def tokens(text: str) -> list[tuple[str, str, int]]:
    """``(kind, text, line)`` for each token: kinds num, str, name, op, rev (a ``$Revision: #n $`` stamp)."""
    out: list[tuple[str, str, int]] = []
    pos, line = 0, 1
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise CslError(f"line {line}: cannot read {text[pos : pos + 20]!r}")
        kind = m.lastgroup
        piece = m.group(0)
        if kind == "revnum":
            kind = "rev"
        if kind in ("num", "str", "name", "op", "rev"):
            out.append((kind, (m.group("revnum") or "0") if kind == "rev" else piece, line))
        line += piece.count("\n")
        pos = m.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.toks = tokens(text)
        self.i = 0

    def peek(self, ahead: int = 0) -> tuple[str, str, int]:
        j = self.i + ahead
        return self.toks[j] if j < len(self.toks) else ("eof", "", self.toks[-1][2] if self.toks else 0)

    def next(self) -> tuple[str, str, int]:
        tok = self.peek()
        self.i += 1
        return tok

    def at(self, text: str, ahead: int = 0) -> bool:
        kind, value, _ = self.peek(ahead)
        return value == text and kind in ("op", "name")

    def eat(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.eat(text):
            kind, value, line = self.peek()
            raise CslError(f"line {line}: expected {text!r}, found {value!r}")

    def ident(self) -> str:
        kind, value, line = self.next()
        if kind != "name":
            raise CslError(f"line {line}: expected a name, found {value!r}")
        return value

    # module
    def module(self) -> Module:
        directives: list[str] = []
        while self.at("#"):
            self.next()
            directives.append(self.ident())
        interruptable = self.eat("interruptable")
        kind = "framemodule" if self.eat("framemodule") else "module"
        if kind == "module":
            self.expect("module")
        name = self.ident()
        params = self.params()
        self.expect("{")
        functions: dict[str, Function] = {}
        body: list = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CslError(f"module {name} is not closed")
            if self.at("function") or (self.at("interruptable") and self.at("function", 1)):
                fn = self.function()
                functions[fn.name] = fn
            else:
                _add(body, self.statement())
        self.expect("}")
        variables = [s for s in body if isinstance(s, Var)]
        return Module(name, params, directives, variables, functions, body, kind, interruptable)

    def params(self) -> list[str]:
        self.expect("(")
        names: list[str] = []
        while not self.at(")"):
            names.append(self.ident())
            if self.peek()[0] == "name":  # a typed parameter: ``Pos2D pos``
                names[-1] = self.ident()
            self.eat(",")
        self.expect(")")
        return names

    def function(self) -> Function:
        interruptable = self.eat("interruptable")
        self.expect("function")
        name = self.ident()
        params = self.params()
        return Function(name, params, self.block(), interruptable)

    # statements
    def block(self) -> list:
        self.expect("{")
        out: list = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CslError("a block is not closed")
            _add(out, self.statement())
        self.expect("}")
        return out

    def body(self) -> list:
        if self.at("{"):
            return self.block()
        out: list = []
        _add(out, self.statement())
        return out

    def statement(self):
        if self.eat(";"):
            return None
        if self.at("{"):  # a bare block (1404's ``case 0: { ... }``)
            return self.block()
        kind, value, line = self.peek()
        if kind == "op" and value == "#":
            if self.peek(1)[0] == "name" and self.at("(", 2):
                self.next()
                return ExprStmt(self.call(self.ident(), "start", line))
            self.next()  # a directive inside a body: ``#Transient``
            self.ident()
            return None
        if kind == "op" and value == "&" and self.peek(1)[0] == "name" and self.at("(", 2):
            self.next()
            return ExprStmt(self.call(self.ident(), "start_ref", line))
        if kind == "name":
            if value == "if":
                return self.if_statement()
            if value == "while":
                self.next()
                self.expect("(")
                cond = self.expr()
                self.expect(")")
                return While(cond, self.body())
            if value == "for":
                return self.for_statement()
            if value == "foreach":
                self.next()
                self.expect("(")
                var = self.ident()
                self.expect("in")
                source = self.expr()
                self.expect(")")
                return Foreach(var, source, self.body())
            if value == "switch":
                return self.switch_statement()
            if value == "return":
                return self.return_statement()
            if value in ("break", "continue"):
                self.next()
                return Jump(value)
            if value in TYPES and self.peek(1)[0] == "name":
                return self.declaration()
        return self.simple_statement()

    def return_statement(self) -> Return:
        self.next()
        if self.at("}") or self.at(";") or self.at("#"):  # 1701: a bare ``return`` before dead code
            return Return()
        return Return(self.expr())

    def declaration(self) -> Var | list[Var]:
        """``var x = 1``, ``var x, y, z`` (several), ``var chance[3]`` (an array)."""
        kind = self.ident()
        out: list[Var] = []
        while True:
            name = self.ident()
            size = None
            if self.eat("["):
                size = self.expr()
                self.expect("]")
            out.append(Var(name, kind, self.expr() if self.eat("=") else None, size))
            if not self.eat(","):
                break
        self.eat(";")
        return out[0] if len(out) == 1 else out

    def switch_statement(self) -> list:
        """``switch (x) { case 0: ... default: ... }`` (1404) as the if / else chain it decides: a case's closing
        ``break`` is dropped, a fall-through into the next case is not followed (docs/formats/csl.md)."""
        self.next()
        self.expect("(")
        subject = self.expr()
        self.expect(")")
        self.expect("{")
        cases: list[tuple[object | None, list]] = []
        while not self.eat("}"):
            if self.peek()[0] == "eof":
                raise CslError("a switch is not closed")
            value = None if self.eat("default") else (self.expect("case"), self.expr())[1]
            self.expect(":")
            body: list = []
            while not (self.at("case") or self.at("default") or self.at("}")):
                _add(body, self.statement())
            if body and body[-1] == Jump("break"):
                body.pop()
            cases.append((value, body))
        chain: list = next((b for v, b in cases if v is None), [])
        for value, body in reversed([c for c in cases if c[0] is not None]):
            chain = [If(Binary("==", subject, value), body, chain)]
        return chain

    def simple_statement(self):
        expr = self.expr()
        kind, value, line = self.peek()
        if kind == "op" and value in ("=", "+=", "-=", "*=", "/=", "%="):
            self.next()
            stmt = Assign(expr, value, self.expr())
        elif kind == "op" and value in ("++", "--"):
            self.next()
            stmt = Assign(expr, value)
        else:
            stmt = ExprStmt(expr)
        self.eat(";")
        return stmt

    def if_statement(self) -> If:
        self.next()
        self.expect("(")
        cond = self.expr()
        self.expect(")")
        then = self.body()
        other: list = []
        if self.eat("else"):
            other = self.body()
        return If(cond, then, other)

    def for_statement(self) -> For:
        self.next()
        self.expect("(")
        var = self.ident()
        self.expect("=")
        start = self.expr()
        self.expect("to")
        end = self.expr()
        self.expect(")")
        return For(var, start, end, self.body())

    # expressions
    def expr(self, level: int = 0):
        if level == len(_BINARY):
            return self.unary()
        left = self.expr(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in _BINARY[level]:
            op = self.next()[1]
            left = Binary(op, left, self.expr(level + 1))
        return left

    def unary(self):
        kind, value, line = self.peek()
        if kind == "op" and value == "&" and self.peek(1)[0] == "name":  # ``&x``: an out-parameter (1404, 1701)
            self.next()
            if self.at("(", 1):
                return self.call(self.ident(), "start_ref", line)
            return Unary("&", self.postfix())
        if kind == "op" and value in ("!", "-", "~"):
            self.next()
            operand = self.unary()
            if value == "-" and isinstance(operand, Num):
                return Num(-operand.value)
            return Unary(value, operand)
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            if self.eat("."):
                node = Member(node, self.ident())
            elif self.eat("["):
                node = Index(node, self.expr())
                self.expect("]")
            else:
                return node

    def primary(self):
        kind, value, line = self.next()
        if kind == "num":
            return Num(int(value, 0))
        if kind == "str":
            return Str(value[1:-1])
        if kind == "rev":
            return Num(int(value))
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "name":
            if self.at("("):
                return self.call(value, "call", line)
            return Name(value)
        raise CslError(f"line {line}: unexpected {value!r}")

    def call(self, name: str, kind: str, line: int) -> Call:
        self.expect("(")
        args: list = []
        while not self.at(")"):
            args.append(self.expr())
            if not self.eat(","):
                break
        self.expect(")")
        return Call(name, tuple(args), kind, line)


def parse(text: str) -> Module:
    """One script (its text decoded as latin-1: the comments hold German umlauts in the old code page)."""
    parser = _Parser(text)
    module = parser.module()
    while parser.at("patchSwap"):  # 1404: what a hot swap from one revision to the next sets
        swap = parser.call(parser.ident(), "call", parser.peek()[2])
        module.patches.append((swap, parser.block()))
    if parser.peek()[0] != "eof":
        raise CslError(f"line {parser.peek()[2]}: text after the module")
    return module


# cslapi.xml


@dataclass(frozen=True)
class ApiFunction:
    name: str
    blocks: bool  # ``<sleep>1</sleep>``: the call suspends the script until the engine event arrives
    params: tuple[str, ...]
    doc: str = ""


@dataclass
class Api:
    """``data/ai/cslapi.xml``: the built-in functions (``functions``) and the global constants (``constants``)."""

    functions: dict[str, ApiFunction] = field(default_factory=dict)
    constants: dict[str, int] = field(default_factory=dict)


_API_FUNCTION = re.compile(r"<APIFunction>(.*?)</APIFunction>", re.DOTALL)
_API_GLOBAL = re.compile(r"<globalvar>\s*<name>(\w+)</name>\s*<id>-?\d+</id>(?:\s*<value>(-?\d+)</value>)?", re.DOTALL)
_API_PARAM = re.compile(r"<(&?\w+)>-?\d+</\1>")


def read_api(text: str) -> Api:
    """The file is not well-formed XML (``<&pos>`` is a tag), so it is read by pattern."""
    api = Api()
    for body in _API_FUNCTION.findall(text):
        name = re.search(r"<name>(\w+)</name>", body)
        if name is None:
            continue
        params = re.search(r"<parameter>(.*?)</parameter>", body, re.DOTALL)
        doc = re.search(r"<doc>(.*?)</doc>", body, re.DOTALL)
        api.functions[name.group(1)] = ApiFunction(
            name.group(1),
            "<sleep>1</sleep>" in body,
            tuple(p.lstrip("&") for p in _API_PARAM.findall(params.group(1))) if params else (),
            doc.group(1).replace("\\r\\n", "\n").strip() if doc else "",
        )
    for name, value in _API_GLOBAL.findall(text):
        api.constants[name] = int(value) if value else 0
    return api


# questions

REALINT_ONE = 4096  # ``_REALINT_ONE``: 2070's fixed point, 4096 = one tile / one degree step of the angle unit
_FOLD = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: int(a / b) if b else None,
    "%": lambda a, b: a - b * int(a / b) if b else None,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b),
    ">=": lambda a, b: int(a >= b),
    "&&": lambda a, b: int(bool(a and b)),
    "||": lambda a, b: int(bool(a or b)),
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
}


def unparse(node) -> str:
    """An expression as text, for messages and for the condition of an event."""
    if isinstance(node, Num):
        return str(node.value)
    if isinstance(node, Str):
        return f'"{node.text}"'
    if isinstance(node, Name):
        return node.name
    if isinstance(node, Member):
        return f"{unparse(node.base)}.{node.field}"
    if isinstance(node, Index):
        return f"{unparse(node.base)}[{unparse(node.index)}]"
    if isinstance(node, Unary):
        return f"{node.op}{unparse(node.operand)}"
    if isinstance(node, Binary):
        return f"({unparse(node.left)} {node.op} {unparse(node.right)})"
    if isinstance(node, Call):
        prefix = {"start": "#", "start_ref": "&"}.get(node.kind, "")
        return f"{prefix}{node.name}({', '.join(unparse(a) for a in node.args)})"
    return repr(node)


@dataclass(frozen=True)
class Event:
    """One call in the order the script runs it. ``when``: the conditions (as text) it sits under."""

    call: Call
    function: str
    when: tuple[str, ...] = ()
    loop: bool = False  # inside a ``while`` / ``for`` / ``foreach``

    @property
    def name(self) -> str:
        return self.call.name


class Script:
    """A parsed module with the questions on it. ``constants`` are the API's globals (``Api.constants``)."""

    def __init__(self, module: Module, constants: dict[str, int] | None = None, api: Api | None = None):
        self.module = module
        self.api = api
        self.constants = dict(api.constants) if api is not None else {}
        self.constants.update(constants or {})
        self.constants.setdefault("true", 1)
        self.constants.setdefault("false", 0)
        self._assigned = self._collect_assignments()

    # assignments
    def _collect_assignments(self) -> dict[str, list]:
        found: dict[str, list] = {}

        def visit(stmts):
            for s in stmts:
                if isinstance(s, Var):
                    if s.init is not None:
                        found.setdefault(s.name, []).append(s.init)
                elif isinstance(s, Assign) and isinstance(s.target, Name):
                    found.setdefault(s.target.name, []).append(s.value if s.op == "=" else None)
                elif isinstance(s, If):
                    visit(s.then)
                    visit(s.other)
                elif isinstance(s, (While, Foreach)):
                    visit(s.body)
                elif isinstance(s, For):
                    found.setdefault(s.var, []).append(None)
                    visit(s.body)

        visit(self.module.body)
        for fn in self.module.functions.values():
            visit(fn.body)
        return found

    def values(self, name: str) -> list[int] | None:
        """Every number the variable is given (a literal, or an expression of constants); ``None`` when one of its
        assignments is not a constant (a call's result, ``+=``). ``cutTime`` in ``lumberjack.csl`` is [2270, 3150]."""
        exprs = self._assigned.get(name)
        if not exprs:
            return None
        out: list[int] = []
        for expr in exprs:
            v = self.fold(expr, _depth=1) if expr is not None else None
            if v is None:
                return None
            if v not in out:
                out.append(v)
        return out

    def fold(self, node, _depth: int = 0) -> int | None:
        """The number an expression stands for, ``None`` if it depends on something only the game knows. A
        variable folds when every assignment gives it the same number."""
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Name):
            if node.name in self.constants:
                return self.constants[node.name]
            if _depth > 6:
                return None
            vals = self.values_depth(node.name, _depth + 1)
            return vals[0] if vals is not None and len(vals) == 1 else None
        if isinstance(node, Unary):
            inner = self.fold(node.operand, _depth)
            if inner is None:
                return None
            return {"-": -inner, "!": int(not inner), "~": ~inner}.get(node.op)  # "&x" is a reference, not a number
        if isinstance(node, Binary):
            a, b = self.fold(node.left, _depth), self.fold(node.right, _depth)
            if a is None or b is None:
                return None
            return _FOLD[node.op](a, b)
        return None

    def values_depth(self, name: str, depth: int) -> list[int] | None:
        exprs = self._assigned.get(name)
        if not exprs or depth > 6:
            return None
        out: list[int] = []
        for expr in exprs:
            v = self.fold(expr, depth) if expr is not None else None
            if v is None:
                return None
            if v not in out:
                out.append(v)
        return out

    # calls
    def events(self, function: str | None = None) -> list[Event]:
        """The calls of a function (the module body when ``None``) in running order. A call of another function of the
        module is replaced by that function's events (one level of recursion is cut)."""
        stmts = self.module.body if function is None else self.module.functions[function].body
        out: list[Event] = []
        self._walk(stmts, function or "", (), False, out, {function} if function else set())
        return out

    def _walk(self, stmts, owner, when, loop, out, stack) -> None:
        for s in stmts:
            if isinstance(s, Var):
                self._calls(s.init, owner, when, loop, out, stack)
            elif isinstance(s, Assign):
                self._calls(s.value, owner, when, loop, out, stack)
            elif isinstance(s, ExprStmt):
                self._calls(s.expr, owner, when, loop, out, stack)
            elif isinstance(s, If):
                self._calls(s.cond, owner, when, loop, out, stack)
                text = unparse(s.cond)
                self._walk(s.then, owner, (*when, text), loop, out, stack)
                self._walk(s.other, owner, (*when, f"not {text}"), loop, out, stack)
            elif isinstance(s, While):
                self._calls(s.cond, owner, when, True, out, stack)
                self._walk(s.body, owner, when, True, out, stack)
            elif isinstance(s, (For, Foreach)):
                self._walk(s.body, owner, when, True, out, stack)
            elif isinstance(s, Return) and s.value is not None:
                self._calls(s.value, owner, when, loop, out, stack)

    def _calls(self, node, owner, when, loop, out, stack) -> None:
        if isinstance(node, Call):
            for arg in node.args:
                self._calls(arg, owner, when, loop, out, stack)
            fn = self.module.functions.get(node.name)
            if fn is not None:
                if node.name not in stack:
                    self._walk(fn.body, node.name, when, loop, out, stack | {node.name})
            else:
                out.append(Event(node, owner, when, loop))
        elif isinstance(node, Binary):
            self._calls(node.left, owner, when, loop, out, stack)
            self._calls(node.right, owner, when, loop, out, stack)
        elif isinstance(node, Unary):
            self._calls(node.operand, owner, when, loop, out, stack)
        elif isinstance(node, (Member, Index)):
            self._calls(node.base, owner, when, loop, out, stack)

    def all_events(self) -> list[Event]:
        """Every call of the file, each function once in source order (no inlining)."""
        out: list[Event] = []
        for fn in self.module.functions.values():
            self._walk(fn.body, fn.name, (), False, out, set(self.module.functions))
        self._walk(self.module.body, "", (), False, out, set(self.module.functions))
        return out

    def assignments(self, name: str, function: str) -> list[int | None]:
        """What the variable is given in a function's own statements, in source order (``None``: not a constant). With
        ``values`` for the pairs: ``workSequence`` and ``cutTime`` in ``lumberjack.csl`` are set side by side."""
        out: list[int | None] = []

        def visit(stmts):
            for s in stmts:
                if isinstance(s, Var) and s.name == name and s.init is not None:
                    out.append(self.fold(s.init))
                elif isinstance(s, Assign) and s.target == Name(name):
                    out.append(self.fold(s.value) if s.op == "=" else None)
                elif isinstance(s, If):
                    visit(s.then)
                    visit(s.other)
                elif isinstance(s, (While, For, Foreach)):
                    visit(s.body)

        visit(self.module.functions[function].body)
        return out

    def calls(self, name: str) -> list[Event]:
        """Every call of ``name`` in the file."""
        return [e for e in self.all_events() if e.name == name]

    def folded(self, event: Event) -> list[int | str | None]:
        """The arguments of an event as numbers (``None`` where unknown) and strings."""
        return [a.text if isinstance(a, Str) else self.fold(a) for a in event.call.args]

    def sleeps(self, function: str | None = None) -> list[tuple[Event, int | None]]:
        """The ``_sleep`` calls in running order with their milliseconds (``None``: a computed time)."""
        events = self.events(function) if function is not None else self.all_events()
        return [(e, self.fold(e.call.args[0])) for e in events if e.name == "_sleep" and e.call.args]

    def strings(self, call: str) -> list[str]:
        """The string arguments of every call of ``call``: ``_getDummyPosition(h, "ship", ...)`` gives "ship"."""
        return [a.text for e in self.calls(call) for a in e.call.args if isinstance(a, Str)]


def load(text: str, api: Api | None = None) -> Script:
    return Script(parse(text), api=api)
