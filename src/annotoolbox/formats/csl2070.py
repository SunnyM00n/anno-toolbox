"""Anno 2070's ``.csl`` scripts (``data/ai/**``; 1404's, 1701's too): the language as a tree, and questions on it.

The scripts drive what moves on the map: a fishing boat's trips, a mine's drone, the felling worker, residents,
carriers. ``parse`` reads one file into a ``SModule``; ``SApi`` reads ``data/ai/cslapi.xml`` (the built-in functions,
which of them block, and the global constants such as ``_SEQ_WALK01`` or ``_feedback_transportation_droneGUID``);
``CScript`` answers the questions a reader of the scripts has: every call of a function with its arguments folded to numbers
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


class CCslError(ValueError):
    """A script the parser cannot read; the message names the line."""


# syntax tree


@dataclass(frozen=True)
class SNum:
    value: int


@dataclass(frozen=True)
class SStr:
    text: str


@dataclass(frozen=True)
class SName:
    name: str


@dataclass(frozen=True)
class SMember:
    base: object
    field: str


@dataclass(frozen=True)
class SIndex:
    base: object
    index: object


@dataclass(frozen=True)
class SUnary:
    operator: str
    operand: object


@dataclass(frozen=True)
class SBinary:
    operator: str
    left: object
    right: object


@dataclass(frozen=True)
class SCall:
    """A call. ``kind``: "call" ``f(x)``, "start" ``#Module(x)`` (starts a script of its own), "start_ref"
    ``&Module(x)`` (the same, keeping the handle). ``swapscript($Revision: #1 $)`` is ``SCall("swapscript", (Num(1),))``."""

    name: str
    args: tuple = ()
    kind: str = "call"
    line: int = 0


@dataclass
class SVar:
    name: str
    type: str = "var"
    init: object | None = None
    size: object | None = None  # ``var chance[3]``: an array (1404, 1701)


@dataclass
class SAssign:
    target: object
    operator: str  # "=", "+=", "-=", "*=", "/=", "%=", "++", "--"
    value: object | None = None


@dataclass
class SExprStmt:
    expr: object


@dataclass
class SIf:
    condition: object
    then: list
    other: list = field(default_factory=list)


@dataclass
class SWhile:
    condition: object
    body: list


@dataclass
class SFor:
    var: str
    start: object
    end: object  # ``for (i = a to b)``: b inclusive
    body: list


@dataclass
class SForeach:
    var: str
    source: object
    body: list


@dataclass
class SReturn:
    value: object | None = None


@dataclass
class SJump:
    kind: str  # "break" or "continue"


@dataclass
class SFunction:
    name: str
    params: list[str]
    body: list
    interruptable: bool = False


@dataclass
class SModule:
    name: str
    params: list[str]
    directives: list[str]  # the ``#Transient`` lines before ``module``
    variables: list[SVar]
    functions: dict[str, SFunction]
    body: list  # the module's own statements: what runs when the script starts
    kind: str = "module"  # or "framemodule" (1404, 1701)
    interruptable: bool = False  # ``interruptable module`` (1404)
    patches: list = field(default_factory=list)  # ``patchSwap(8, 9) { ... }`` after the module: (call, statements)


# reading


def _add(out: list, statement) -> None:
    """A statement into a body: ``var a, b`` reads as several."""
    if isinstance(statement, list):
        out.extend(statement)
    elif statement is not None:
        out.append(statement)


def tokens(text: str) -> list[tuple[str, str, int]]:
    """``(kind, text, line)`` for each token: kinds num, str, name, op, rev (a ``$Revision: #n $`` stamp)."""
    out: list[tuple[str, str, int]] = []
    cursor, line = 0, 1
    while cursor < len(text):
        match = _TOKEN.match(text, cursor)
        if match is None:
            raise CCslError(f"line {line}: cannot read {text[cursor : cursor + 20]!r}")
        kind = match.lastgroup
        piece = match.group(0)
        if kind == "revnum":
            kind = "rev"
        if kind in ("num", "str", "name", "op", "rev"):
            out.append((kind, (match.group("revnum") or "0") if kind == "rev" else piece, line))
        line += piece.count("\n")
        cursor = match.end()
    return out


class _CParser:
    def __init__(self, text: str):
        self.token_list = tokens(text)
        self.index = 0

    def peek(self, ahead: int = 0) -> tuple[str, str, int]:
        j = self.index + ahead
        return (
            self.token_list[j]
            if j < len(self.token_list)
            else ("eof", "", self.token_list[-1][2] if self.token_list else 0)
        )

    def next(self) -> tuple[str, str, int]:
        tok = self.peek()
        self.index += 1
        return tok

    def at(self, text: str, ahead: int = 0) -> bool:
        kind, value, _ = self.peek(ahead)
        return value == text and kind in ("op", "name")

    def eat(self, text: str) -> bool:
        if self.at(text):
            self.index += 1
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.eat(text):
            kind, value, line = self.peek()
            raise CCslError(f"line {line}: expected {text!r}, found {value!r}")

    def ident(self) -> str:
        kind, value, line = self.next()
        if kind != "name":
            raise CCslError(f"line {line}: expected a name, found {value!r}")
        return value

    # module
    def module(self) -> SModule:
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
        functions: dict[str, SFunction] = {}
        body: list = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CCslError(f"module {name} is not closed")
            if self.at("function") or (self.at("interruptable") and self.at("function", 1)):
                function = self.function()
                functions[function.name] = function
            else:
                _add(body, self.statement())
        self.expect("}")
        variables = [statement for statement in body if isinstance(statement, SVar)]
        return SModule(name, params, directives, variables, functions, body, kind, interruptable)

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

    def function(self) -> SFunction:
        interruptable = self.eat("interruptable")
        self.expect("function")
        name = self.ident()
        params = self.params()
        return SFunction(name, params, self.block(), interruptable)

    # statements
    def block(self) -> list:
        self.expect("{")
        out: list = []
        while not self.at("}"):
            if self.peek()[0] == "eof":
                raise CCslError("a block is not closed")
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
                return SExprStmt(self.call(self.ident(), "start", line))
            self.next()  # a directive inside a body: ``#Transient``
            self.ident()
            return None
        if kind == "op" and value == "&" and self.peek(1)[0] == "name" and self.at("(", 2):
            self.next()
            return SExprStmt(self.call(self.ident(), "start_ref", line))
        if kind == "name":
            if value == "if":
                return self.if_statement()
            if value == "while":
                self.next()
                self.expect("(")
                condition = self.expr()
                self.expect(")")
                return SWhile(condition, self.body())
            if value == "for":
                return self.for_statement()
            if value == "foreach":
                self.next()
                self.expect("(")
                var = self.ident()
                self.expect("in")
                source = self.expr()
                self.expect(")")
                return SForeach(var, source, self.body())
            if value == "switch":
                return self.switch_statement()
            if value == "return":
                return self.return_statement()
            if value in ("break", "continue"):
                self.next()
                return SJump(value)
            if value in TYPES and self.peek(1)[0] == "name":
                return self.declaration()
        return self.simple_statement()

    def return_statement(self) -> SReturn:
        self.next()
        if self.at("}") or self.at(";") or self.at("#"):  # 1701: a bare ``return`` before dead code
            return SReturn()
        return SReturn(self.expr())

    def declaration(self) -> SVar | list[SVar]:
        """``var x = 1``, ``var x, y, z`` (several), ``var chance[3]`` (an array)."""
        kind = self.ident()
        out: list[SVar] = []
        while True:
            name = self.ident()
            size = None
            if self.eat("["):
                size = self.expr()
                self.expect("]")
            out.append(SVar(name, kind, self.expr() if self.eat("=") else None, size))
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
                raise CCslError("a switch is not closed")
            value = None if self.eat("default") else (self.expect("case"), self.expr())[1]
            self.expect(":")
            body: list = []
            while not (self.at("case") or self.at("default") or self.at("}")):
                _add(body, self.statement())
            if body and body[-1] == SJump("break"):
                body.pop()
            cases.append((value, body))
        chain: list = next((case_body for case_value, case_body in cases if case_value is None), [])
        for value, body in reversed([case_entry for case_entry in cases if case_entry[0] is not None]):
            chain = [SIf(SBinary("==", subject, value), body, chain)]
        return chain

    def simple_statement(self):
        expr = self.expr()
        kind, value, line = self.peek()
        if kind == "op" and value in ("=", "+=", "-=", "*=", "/=", "%="):
            self.next()
            statement = SAssign(expr, value, self.expr())
        elif kind == "op" and value in ("++", "--"):
            self.next()
            statement = SAssign(expr, value)
        else:
            statement = SExprStmt(expr)
        self.eat(";")
        return statement

    def if_statement(self) -> SIf:
        self.next()
        self.expect("(")
        condition = self.expr()
        self.expect(")")
        then = self.body()
        other: list = []
        if self.eat("else"):
            other = self.body()
        return SIf(condition, then, other)

    def for_statement(self) -> SFor:
        self.next()
        self.expect("(")
        var = self.ident()
        self.expect("=")
        start = self.expr()
        self.expect("to")
        end = self.expr()
        self.expect(")")
        return SFor(var, start, end, self.body())

    # expressions
    def expr(self, level: int = 0):
        if level == len(_BINARY):
            return self.unary()
        left = self.expr(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in _BINARY[level]:
            operator = self.next()[1]
            left = SBinary(operator, left, self.expr(level + 1))
        return left

    def unary(self):
        kind, value, line = self.peek()
        if kind == "op" and value == "&" and self.peek(1)[0] == "name":  # ``&x``: an out-parameter (1404, 1701)
            self.next()
            if self.at("(", 1):
                return self.call(self.ident(), "start_ref", line)
            return SUnary("&", self.postfix())
        if kind == "op" and value in ("!", "-", "~"):
            self.next()
            operand = self.unary()
            if value == "-" and isinstance(operand, SNum):
                return SNum(-operand.value)
            return SUnary(value, operand)
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while True:
            if self.eat("."):
                node = SMember(node, self.ident())
            elif self.eat("["):
                node = SIndex(node, self.expr())
                self.expect("]")
            else:
                return node

    def primary(self):
        kind, value, line = self.next()
        if kind == "num":
            return SNum(int(value, 16) if value[:2] in ("0x", "0X") else int(value))  # "010" is ten, not an octal
        if kind == "str":
            return SStr(value[1:-1])
        if kind == "rev":
            return SNum(int(value))
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "name":
            if self.at("("):
                return self.call(value, "call", line)
            return SName(value)
        raise CCslError(f"line {line}: unexpected {value!r}")

    def call(self, name: str, kind: str, line: int) -> SCall:
        self.expect("(")
        args: list = []
        while not self.at(")"):
            args.append(self.expr())
            if not self.eat(","):
                break
        self.expect(")")
        return SCall(name, tuple(args), kind, line)


def parse(text: str) -> SModule:
    """One script (its text decoded as latin-1: the comments hold German umlauts in the old code page)."""
    parser = _CParser(text)
    try:
        module = parser.module()
        while parser.at("patchSwap"):  # 1404: what a hot swap from one revision to the next sets
            swap = parser.call(parser.ident(), "call", parser.peek()[2])
            module.patches.append((swap, parser.block()))
    except RecursionError:
        raise CCslError(f"line {parser.peek()[2]}: nested too deeply") from None
    if parser.peek()[0] != "eof":
        raise CCslError(f"line {parser.peek()[2]}: text after the module")
    return module


# cslapi.xml


@dataclass(frozen=True)
class SApiFunction:
    name: str
    is_blocking: bool  # ``<sleep>1</sleep>``: the call suspends the script until the engine event arrives
    params: tuple[str, ...]
    doc: str = ""


@dataclass
class SApi:
    """``data/ai/cslapi.xml``: the built-in functions (``functions``) and the global constants (``constants``)."""

    functions: dict[str, SApiFunction] = field(default_factory=dict)
    constants: dict[str, int] = field(default_factory=dict)


_API_FUNCTION = re.compile(r"<APIFunction>(.*?)</APIFunction>", re.DOTALL)
_API_GLOBAL = re.compile(r"<globalvar>\s*<name>(\w+)</name>\s*<id>-?\d+</id>(?:\s*<value>(-?\d+)</value>)?", re.DOTALL)
_API_PARAM = re.compile(r"<(&?\w+)>-?\d+</\1>")


def read_api(text: str) -> SApi:
    """The file is not well-formed XML (``<&pos>`` is a tag), so it is read by pattern."""
    api = SApi()
    for body in _API_FUNCTION.findall(text):
        name = re.search(r"<name>(\w+)</name>", body)
        if name is None:
            continue
        params = re.search(r"<parameter>(.*?)</parameter>", body, re.DOTALL)
        doc = re.search(r"<doc>(.*?)</doc>", body, re.DOTALL)
        api.functions[name.group(1)] = SApiFunction(
            name.group(1),
            "<sleep>1</sleep>" in body,
            tuple(parameter.lstrip("&") for parameter in _API_PARAM.findall(params.group(1))) if params else (),
            doc.group(1).replace("\\r\\n", "\n").strip() if doc else "",
        )
    for name, value in _API_GLOBAL.findall(text):
        api.constants[name] = int(value) if value else 0
    return api


# questions

REALINT_ONE = 4096  # ``_REALINT_ONE``: 2070's fixed point, 4096 = one tile / one degree step of the angle unit


def _divide(a: int, b: int) -> int:
    """The quotient the script's integers give: cut towards zero, exact for any size."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


_FOLD = {
    "+": lambda left, right: left + right,
    "-": lambda left, right: left - right,
    "*": lambda left, right: left * right,
    "/": lambda left, right: _divide(left, right) if right else None,
    "%": lambda left, right: left - right * _divide(left, right) if right else None,
    "==": lambda left, right: int(left == right),
    "!=": lambda left, right: int(left != right),
    "<": lambda left, right: int(left < right),
    ">": lambda left, right: int(left > right),
    "<=": lambda left, right: int(left <= right),
    ">=": lambda left, right: int(left >= right),
    "&&": lambda left, right: int(bool(left and right)),
    "||": lambda left, right: int(bool(left or right)),
    "&": lambda left, right: left & right,
    "|": lambda left, right: left | right,
}


def unparse(node) -> str:
    """An expression as text, for messages and for the condition of an event."""
    if isinstance(node, SNum):
        return str(node.value)
    if isinstance(node, SStr):
        return f'"{node.text}"'
    if isinstance(node, SName):
        return node.name
    if isinstance(node, SMember):
        return f"{unparse(node.base)}.{node.field}"
    if isinstance(node, SIndex):
        return f"{unparse(node.base)}[{unparse(node.index)}]"
    if isinstance(node, SUnary):
        return f"{node.operator}{unparse(node.operand)}"
    if isinstance(node, SBinary):
        return f"({unparse(node.left)} {node.operator} {unparse(node.right)})"
    if isinstance(node, SCall):
        prefix = {"start": "#", "start_ref": "&"}.get(node.kind, "")
        return f"{prefix}{node.name}({', '.join(unparse(argument) for argument in node.args)})"
    return repr(node)


@dataclass(frozen=True)
class SEvent:
    """One call in the order the script runs it. ``when``: the conditions (as text) it sits under."""

    call: SCall
    function: str
    when: tuple[str, ...] = ()
    in_loop: bool = False  # inside a ``while`` / ``for`` / ``foreach``

    @property
    def name(self) -> str:
        return self.call.name


class CScript:
    """A parsed module with the questions on it. ``constants`` are the API's globals (``SApi.constants``)."""

    def __init__(self, module: SModule, constants: dict[str, int] | None = None, api: SApi | None = None):
        self.m_module = module
        self.m_api = api
        self.m_constants = dict(api.constants) if api is not None else {}
        self.m_constants.update(constants or {})
        self.m_constants.setdefault("true", 1)
        self.m_constants.setdefault("false", 0)
        self._m_assigned = self._collect_assignments()

    # assignments
    def _collect_assignments(self) -> dict[str, list]:
        found: dict[str, list] = {}

        def visit(stmts):
            for statement in stmts:
                if isinstance(statement, SVar):
                    if statement.init is not None:
                        found.setdefault(statement.name, []).append(statement.init)
                elif isinstance(statement, SAssign) and isinstance(statement.target, SName):
                    found.setdefault(statement.target.name, []).append(
                        statement.value if statement.operator == "=" else None
                    )
                elif isinstance(statement, SIf):
                    visit(statement.then)
                    visit(statement.other)
                elif isinstance(statement, (SWhile, SForeach)):
                    visit(statement.body)
                elif isinstance(statement, SFor):
                    found.setdefault(statement.var, []).append(None)
                    visit(statement.body)

        visit(self.m_module.body)
        for function in self.m_module.functions.values():
            visit(function.body)
        return found

    def values(self, name: str) -> list[int] | None:
        """Every number the variable is given (a literal, or an expression of constants); ``None`` when one of its
        assignments is not a constant (a call's result, ``+=``). ``cutTime`` in ``lumberjack.csl`` is [2270, 3150]."""
        return self.values_depth(name, 1)

    def fold(self, node, _depth: int = 0) -> int | None:
        """The number an expression stands for, ``None`` if it depends on something only the game knows. A
        variable folds when every assignment gives it the same number."""
        if isinstance(node, SNum):
            return node.value
        if isinstance(node, SName):
            if node.name in self.m_constants:
                return self.m_constants[node.name]
            if _depth > 6:
                return None
            numbers = self.values_depth(node.name, _depth + 1)
            return numbers[0] if numbers is not None and len(numbers) == 1 else None
        if isinstance(node, SUnary):
            inner = self.fold(node.operand, _depth)
            if inner is None:
                return None
            return {"-": -inner, "!": int(not inner), "~": ~inner}.get(
                node.operator
            )  # "&x" is a reference, not a number
        if isinstance(node, SBinary):
            left, right = self.fold(node.left, _depth), self.fold(node.right, _depth)
            if left is None or right is None:
                return None
            return _FOLD[node.operator](left, right)
        return None

    def values_depth(self, name: str, depth: int) -> list[int] | None:
        exprs = self._m_assigned.get(name)
        if not exprs or depth > 6:
            return None
        out: list[int] = []
        for expr in exprs:
            value = self.fold(expr, depth) if expr is not None else None
            if value is None:
                return None
            if value not in out:
                out.append(value)
        return out

    # calls
    def events(self, function: str | None = None) -> list[SEvent]:
        """The calls of a function (the module body when ``None``) in running order. A call of another function of the
        module is replaced by that function's events (one level of recursion is cut)."""
        stmts = self.m_module.body if function is None else self.m_module.functions[function].body
        out: list[SEvent] = []
        self._walk(stmts, function or "", (), False, out, {function} if function else set())
        return out

    def _walk(self, stmts, owner, when, in_loop, out, stack) -> None:
        for statement in stmts:
            if isinstance(statement, SVar):
                self._calls(statement.init, owner, when, in_loop, out, stack)
            elif isinstance(statement, SAssign):
                self._calls(statement.value, owner, when, in_loop, out, stack)
            elif isinstance(statement, SExprStmt):
                self._calls(statement.expr, owner, when, in_loop, out, stack)
            elif isinstance(statement, SIf):
                self._calls(statement.condition, owner, when, in_loop, out, stack)
                text = unparse(statement.condition)
                self._walk(statement.then, owner, (*when, text), in_loop, out, stack)
                self._walk(statement.other, owner, (*when, f"not {text}"), in_loop, out, stack)
            elif isinstance(statement, SWhile):
                self._calls(statement.condition, owner, when, True, out, stack)
                self._walk(statement.body, owner, when, True, out, stack)
            elif isinstance(statement, SFor):
                for bound in (statement.start, statement.end):
                    self._calls(bound, owner, when, in_loop, out, stack)
                self._walk(statement.body, owner, when, True, out, stack)
            elif isinstance(statement, SForeach):
                self._calls(statement.source, owner, when, in_loop, out, stack)
                self._walk(statement.body, owner, when, True, out, stack)
            elif isinstance(statement, SReturn) and statement.value is not None:
                self._calls(statement.value, owner, when, in_loop, out, stack)

    def _calls(self, node, owner, when, in_loop, out, stack) -> None:
        if isinstance(node, SCall):
            for arg in node.args:
                self._calls(arg, owner, when, in_loop, out, stack)
            function = self.m_module.functions.get(node.name)
            if function is not None:
                if node.name not in stack:
                    self._walk(function.body, node.name, when, in_loop, out, stack | {node.name})
            else:
                out.append(SEvent(node, owner, when, in_loop))
        elif isinstance(node, SBinary):
            self._calls(node.left, owner, when, in_loop, out, stack)
            self._calls(node.right, owner, when, in_loop, out, stack)
        elif isinstance(node, SUnary):
            self._calls(node.operand, owner, when, in_loop, out, stack)
        elif isinstance(node, (SMember, SIndex)):
            self._calls(node.base, owner, when, in_loop, out, stack)
            if isinstance(node, SIndex):
                self._calls(node.index, owner, when, in_loop, out, stack)

    def all_events(self) -> list[SEvent]:
        """Every call of the file, each function once in source order (no inlining)."""
        out: list[SEvent] = []
        for function in self.m_module.functions.values():
            self._walk(function.body, function.name, (), False, out, set(self.m_module.functions))
        self._walk(self.m_module.body, "", (), False, out, set(self.m_module.functions))
        return out

    def assignments(self, name: str, function: str) -> list[int | None]:
        """What the variable is given in a function's own statements, in source order (``None``: not a constant). With
        ``values`` for the pairs: ``workSequence`` and ``cutTime`` in ``lumberjack.csl`` are set side by side."""
        out: list[int | None] = []

        def visit(stmts):
            for statement in stmts:
                if isinstance(statement, SVar) and statement.name == name and statement.init is not None:
                    out.append(self.fold(statement.init))
                elif isinstance(statement, SAssign) and statement.target == SName(name):
                    out.append(self.fold(statement.value) if statement.operator == "=" else None)
                elif isinstance(statement, SIf):
                    visit(statement.then)
                    visit(statement.other)
                elif isinstance(statement, (SWhile, SFor, SForeach)):
                    visit(statement.body)

        visit(self.m_module.functions[function].body)
        return out

    def calls(self, name: str) -> list[SEvent]:
        """Every call of ``name`` in the file."""
        return [event for event in self.all_events() if event.name == name]

    def folded(self, event: SEvent) -> list[int | str | None]:
        """The arguments of an event as numbers (``None`` where unknown) and strings."""
        return [argument.text if isinstance(argument, SStr) else self.fold(argument) for argument in event.call.args]

    def sleeps(self, function: str | None = None) -> list[tuple[SEvent, int | None]]:
        """The ``_sleep`` calls in running order with their milliseconds (``None``: a computed time)."""
        events = self.events(function) if function is not None else self.all_events()
        return [
            (event, self.fold(event.call.args[0])) for event in events if event.name == "_sleep" and event.call.args
        ]

    def strings(self, call: str) -> list[str]:
        """The string arguments of every call of ``call``: ``_getDummyPosition(h, "ship", ...)`` gives "ship"."""
        return [
            argument.text for event in self.calls(call) for argument in event.call.args if isinstance(argument, SStr)
        ]


def load(text: str, api: SApi | None = None) -> CScript:
    return CScript(parse(text), api=api)
