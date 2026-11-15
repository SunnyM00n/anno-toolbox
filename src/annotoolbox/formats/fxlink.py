"""What one shader of an effect file uses, in an order a DirectX compiler takes: only what the entry reaches,
nothing used before it is declared (docs/rendering/shader-compile.md, "The effect file").

``Program(hlsl)`` splits the HLSL ``fxeffect.split_effect`` leaves into its top-level items once;
``Program.entry_source(call, passthrough, Program.bindings(technique.functions))`` returns the text and the entry
name for one pass shader: the reached declarations, the resources at the engine's registers in its order
(``fxbind``), a declaration of every reached function, the functions, and an entry under the function's name when
the pass gives literal arguments to ``uniform`` parameters.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from . import fxbind
from .fxeffect import MARKER, EffectError, ShaderCall, define_lines, split_args

_LINE_RE = re.compile(r'(?m)^[ \t]*#[ \t]*line[ \t]+(\d+)(?:[ \t]+"([^"]*)")?[^\n]*\n?')
_NOT_FUNCTION_RE = re.compile(r"^\s*(?:struct|cbuffer|tbuffer|class|enum|namespace|typedef|interface)\b")
_KEYWORD_HEAD_RE = re.compile(r"^\s*(struct|cbuffer|tbuffer|class|enum|namespace|interface)\s+(\w+)")
# a function declared without its body: "<type> name(...) [: semantic];"
_PROTOTYPE_RE = re.compile(r"^[^=(;{:]*?\b(\w+)\s*\([^;{]*\)\s*(?::\s*\w+\s*)?;\s*$", re.S)
_IDENT_RE = re.compile(r"\b[A-Za-z_]\w*\b")
# an identifier a declaration introduces: followed by its end, an initializer, a semantic or register, an array size
_DECLARED_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(?=[;=:,\[\)]|$)")
_MARKER_RE = re.compile(rf"\b{MARKER}(\w+)\s*")
# a resource declaration: the table words, the type, the name, an array's size, the register the source names
_RESOURCE_RE = re.compile(
    rf"^\s*((?:(?:{MARKER}\w+|static|uniform|globallycoherent)\s+)*)"
    r"(cbuffer|tbuffer|RaytracingAccelerationStructure|(?:RW|Append|Consume|RasterizerOrdered)?"
    r"(?:Texture\w*|StructuredBuffer|ByteAddressBuffer|Buffer|ConstantBuffer)(?:\s*<[^;{]*?>)?)\s+(\w+)\s*"
    r"(\[[^\]]*\])?\s*(:\s*register\s*\(\s*([btu])(\d+)\s*(?:,\s*space(\d+)\s*)?\))?"
)
_FILE_RE = re.compile(r'"([^"]*)"')


@dataclass
class Item:
    """One top-level piece: a declaration, a function definition or a leftover directive."""

    where: str  # the #line that puts it back at its file and line
    text: str
    head: str | None  # a function's head (up to the parameters and the semantic); None for anything else
    names: frozenset[str]  # what it declares
    uses: frozenset[str]  # every identifier in it
    resource: fxbind.Resource | None = None  # a resource declaration's binding facts


class Program:
    """The top-level items of an effect's HLSL, indexed by the names they declare."""

    def __init__(self, hlsl: str) -> None:
        markers = _line_markers(hlsl)
        self.items: list[Item] = []
        heads: set[str] = set()
        declared: set[str] = set()
        for start, raw in _top_items(hlsl):
            words, text = frozenset(_MARKER_RE.findall(raw)), _MARKER_RE.sub("", raw)
            head = _function_head(text)
            if head is not None:
                key = " ".join(_strip_attributes(head).split())
                if key in heads:  # the sources define a few functions twice: the first counts
                    continue
                heads.add(key)
            names = _declared(text, head)
            if head is None and names and names <= declared:  # a resource declared twice: the first counts
                continue
            declared |= names if head is None else set()
            struct = _KEYWORD_HEAD_RE.match(text)
            if head is None and struct and struct.group(1) == "struct" and "{" in text:
                if f"struct {struct.group(2)}" in heads:  # and a few structures: the first counts
                    continue
                heads.add(f"struct {struct.group(2)}")
            idents = frozenset(_IDENT_RE.findall(text)) - names
            where = _line_marker(hlsl, markers, start)
            resource = _resource(text, words, where) if head is None else None
            self.items.append(Item(where, text, head, names, idents, resource))
        self.by_name: dict[str, list[int]] = {}
        for i, item in enumerate(self.items):
            for n in item.names:
                self.by_name.setdefault(n, []).append(i)
        unbounded = [it.resource.name for it in self.items if it.resource and it.resource.count == 0]
        self._own_space = {name: 100 + k for k, name in enumerate(unbounded)}  # unbound: a space each

    def resources(self, roots: set[str]) -> list[fxbind.Resource]:
        """The resources the names in ``roots`` reach, in file order: one pass's are its tables
        (``fxbind.bind``)."""
        return [r for i in self.reached(roots) if (r := self.items[i].resource) is not None]

    def declared(self) -> list[fxbind.Resource]:
        """Every resource of the effect, in file order."""
        return [it.resource for it in self.items if it.resource is not None]

    def bindings(self, functions: set[str]) -> dict[str, str]:
        """The registers of the pass whose shader functions are ``functions``, as the engine binds them."""
        return fxbind.bind(self.resources(functions), self.declared())

    def _declaration(self, item: Item, bindings: dict[str, str] | None) -> str:
        """A declaration as emitted: a resource at its binding; without one the compiler places it (an unbounded
        array in a space of its own, since it takes every register after it)."""
        r = item.resource
        if r is None:
            return item.text
        m = _RESOURCE_RE.match(item.text)
        clause = (bindings or {}).get(r.name)
        if clause is None and r.count == 0:
            clause = f"register({fxbind.LETTERS.get(r.kind, 't')}0, space{self._own_space[r.name]})"
        if clause is None:
            if bindings is not None and r.space is not None or not m.group(5) or m.group(6) == "b":
                return item.text  # a space the source names itself, a constant buffer's slot: as written
            return item.text[: m.start(5)] + item.text[m.end(5) :]
        if m.group(5):
            return item.text[: m.start(5)] + ": " + clause + item.text[m.end(5) :]
        at = m.end(4) if m.group(4) else m.end(3)
        return item.text[:at] + " : " + clause + item.text[at:]

    def reached(self, roots: set[str]) -> list[int]:
        """The items the names in ``roots`` lead to, through every identifier they use, in file order; the
        directives left at the top level always count."""
        seen: set[int] = set()
        todo = [i for n in roots for i in self.by_name.get(n, ())]
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            todo += [j for n in self.items[i].uses for j in self.by_name.get(n, ()) if j not in seen]
        return sorted(seen | {i for i, item in enumerate(self.items) if item.text.startswith("#")})

    def source(
        self, roots: set[str], bindings: dict[str, str] | None = None, rename: tuple[str, str] | None = None
    ) -> str:
        """The reached declarations, then a declaration of each reached function, then the functions
        (``bindings``: each resource's register, from ``fxbind.bind``; ``rename``: a function emitted under
        another name)."""
        chosen = [self.items[i] for i in self.reached(roots)]
        decls = [it.where + it.text for it in chosen if it.head is None and _bound_name(it) is None]

        # the engine declares its resources root constants first, then by table and register, the samplers by name:
        # the compiler numbers them in that order
        def order(it: Item) -> tuple[int, int, int, str]:
            name = _bound_name(it) or ""
            m = _CLAUSE_RE.search((bindings or {}).get(name) or it.text)
            if it.resource is None or m is None:  # the engine's samplers by name
                return 2, 0, 0, name
            space = int(m.group(2) or 0)
            return int(space != fxbind.ROOT_SPACE), space, int(m.group(1)), name  # root constants first

        bound = sorted((it for it in chosen if it.head is None and _bound_name(it) is not None), key=order)
        decls += [it.where + self._declaration(it, bindings) for it in bound]
        # no declaration of a geometry or compute shader: FXC wants its maxvertexcount or numthreads there too, and
        # nothing calls an entry
        protos = [
            it.where + _named(_strip_attributes(it.head), it, rename) + ";\n"
            for it in chosen
            if it.head and not re.search(r"\b(?:maxvertexcount|numthreads)\b", it.head)
        ]
        bodies = [it.where + _named(_strip_defaults(it.text, it.head), it, rename) for it in chosen if it.head]
        return "".join(decls) + "\n" + "".join(protos) + "".join(bodies)

    def entry_source(
        self, call: ShaderCall, passthrough: tuple[str, ...] = (), bindings: dict[str, str] | None = None
    ) -> tuple[str, str]:
        """The HLSL and the entry name for one pass shader (``passthrough``: the technique's PassthroughDefines,
        which the compiler's own preprocessor sees; ``bindings``: its pass's registers, else the compiler
        places)."""
        head = define_lines([(d, "") for d in passthrough])
        wrapper = self._wrapper(call) if call.args else ""
        if not wrapper:
            return head + self.source({call.function}, bindings), call.function
        body = self.source({call.function}, bindings, (call.function, _inner_name(call)))
        return head + body + '\n#line 1 "<entry>"\n' + wrapper, call.function

    def _wrapper(self, call: ShaderCall) -> str:
        """An entry under the function's name that passes the pass's literal arguments to its ``uniform``
        parameters (the function itself renamed, ``_inner_name``); empty when it has none."""
        overloads = [self.items[i] for i in self.by_name.get(call.function, ()) if self.items[i].head]
        if not overloads:
            raise EffectError(f"shader function {call.function} not found")
        item = next((o for o in overloads if _uniform_count(o.head) == len(call.args)), overloads[0])
        head, bare = item.head, _strip_attributes(item.head)
        attrs = re.findall(r"\[[^\]]*\]", head[: len(head) - len(bare)])
        params = split_args(bare[bare.index("(") + 1 : _close_paren(bare, bare.index("("))], angles=True)
        semantic = re.search(r"\)\s*:\s*(\w+)\s*$", bare)
        returns = bare[: bare.index(call.function)].split()[-1]
        uniform = [p for p in params if re.match(r"\s*uniform\b", p)]
        if not uniform:  # arguments for a function without uniform parameters: the game compiles it as it is
            return ""
        if len(uniform) != len(call.args):
            raise EffectError(f"{call.function}: {len(call.args)} arguments for {len(uniform)} uniform parameters")
        values, kept, args = iter(call.args), [], []
        for p in params:
            if re.match(r"\s*uniform\b", p):
                args.append(next(values))
            else:
                kept.append(re.sub(r"\s*=[^,]*$", "", p))
                args.append(_param_name(p))
        tail = f" : {semantic.group(1)}" if semantic else ""
        ret = "" if returns == "void" else "return "
        return (
            f"{' '.join(attrs)}\n{returns} {call.function}({', '.join(kept)}){tail}\n"
            f"{{\n\t{ret}{_inner_name(call)}({', '.join(args)});\n}}\n"
        )


def _inner_name(call: ShaderCall) -> str:
    """The name a shader function with literal arguments is emitted under, its entry taking its own."""
    return f"{call.function}__{'_'.join(re.sub(r'\W', '', a) or 'x' for a in call.args)}"


def _named(text: str, item: Item, rename: tuple[str, str] | None) -> str:
    """A function's head or definition, under the new name when ``rename`` names it."""
    if rename is None or rename[0] not in item.names:
        return text
    return re.sub(rf"\b{re.escape(rename[0])}(\s*\()", rename[1] + r"\1", text, count=1)


def _resource(text: str, words: frozenset[str], where: str) -> fxbind.Resource | None:
    """A top-level resource declaration's binding facts, None for anything else."""
    m = _RESOURCE_RE.match(text)
    kind = fxbind.kind_of(m.group(2)) if m else None
    if kind is None:
        return None
    size = (m.group(4) or "").strip("[] \t")
    count = 1 if m.group(4) is None else 0 if not size else _array_size(size)
    slot = int(m.group(7)) if m.group(7) else None
    space = int(m.group(8)) if m.group(8) else None
    file = _FILE_RE.search(where)
    return fxbind.Resource(m.group(3), kind, words, slot, space, count, file.group(1) if file else "")


_CLAUSE_RE = re.compile(r"register\s*\(\s*[a-z](\d+)\s*(?:,\s*space(\d+)\s*)?\)")
_SAMPLER_RE = re.compile(r"^\s*(?:static\s+)?Sampler(?:Comparison)?State\s+(\w+)\s*(?::|;|\[)")


def _bound_name(item: Item) -> str | None:
    """The name of a resource or sampler declaration, None for anything else."""
    if item.resource is not None:
        return item.resource.name
    m = _SAMPLER_RE.match(item.text)
    return m.group(1) if m else None


def _array_size(text: str) -> int:
    """An array's length as the preprocessor leaves it: a number or a product of numbers (else 1)."""
    factors = [f.strip().strip("()") for f in text.split("*")]
    if not all(f.isdigit() for f in factors):
        return 1
    size = 1
    for f in factors:
        size *= int(f)
    return size


def _uniform_count(head: str) -> int:
    bare = _strip_attributes(head)
    params = split_args(bare[bare.index("(") + 1 : _close_paren(bare, bare.index("("))], angles=True)
    return sum(1 for p in params if re.match(r"\s*uniform\b", p))


def _top_items(text: str) -> list[tuple[int, str]]:
    """The top-level items (declarations, definitions, other directives) with their start, ``#line`` left out."""
    items, start, depth, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == "#" and depth == 0 and not text[start:i].strip():
            end = text.find("\n", i)
            end = n if end < 0 else end + 1
            if not _LINE_RE.match(text, i):
                items.append((i, text[i:end]))
            start = i = end
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:  # a block ends the item, with the ';' after it when there is one
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] != ";" and text[start:i].lstrip().startswith("struct"):
                    j = _declarators_end(text, j)  # struct S { ... } s; (1701's constants)
                end = j if j < n and text[j] == ";" else i
                items.append((start, text[start : end + 1] + "\n"))
                start = i = end + 1
                continue
        elif c == ";" and depth == 0:
            items.append((start, text[start : i + 1] + "\n"))
            start = i + 1
        i += 1
    if text[start:].strip():
        items.append((start, text[start:]))
    return [(s + len(t) - len(t.lstrip()), t.strip() + "\n") for s, t in items if t.strip()]


_DECLARATORS_RE = re.compile(r"[\w\s,\[\]]*;")


def _declarators_end(text: str, at: int) -> int:
    """The ``;`` closing the variables a structure's definition declares (``} vcLightingDir;``), else ``at``."""
    m = _DECLARATORS_RE.match(text, at)
    return m.end() - 1 if m and "#" not in m.group(0) else at


def _function_head(item: str) -> str | None:
    """The head of a function definition (up to its parameter list and semantic), or None for anything else."""
    brace = item.find("{")
    if brace < 0 or _NOT_FUNCTION_RE.match(item):
        return None
    head = item[:brace].rstrip()
    m = re.search(r"\)\s*(?::\s*\w+\s*)?$", head)
    if m is None or "=" in head[: head.find("(")]:
        return None
    return head


def _declared(text: str, head: str | None) -> frozenset[str]:
    """The names a top-level item declares: a function's name, a type's name, a variable's, a block's members."""
    if head is not None:
        bare = _strip_attributes(head)
        return frozenset({bare[: bare.index("(")].split()[-1]})
    if text.lstrip().startswith("#"):
        return frozenset()
    proto = _PROTOTYPE_RE.match(_strip_attributes(text))
    if proto and not _NOT_FUNCTION_RE.match(text):
        return frozenset({proto.group(1)})
    m = _KEYWORD_HEAD_RE.match(text)
    names = {m.group(2)} if m else set()
    if m and m.group(1) in ("cbuffer", "tbuffer", "enum", "namespace") and "{" in text:
        inner = text[text.index("{") + 1 : text.rindex("}")]
        names |= {x for x in _flat(inner) if x}
    elif m and m.group(1) == "struct" and "}" in text:
        names |= {x for x in _flat(text[text.rindex("}") + 1 :]) if x}  # the variables after the definition
    elif not m:
        names |= set(_flat(text))
    return frozenset(names)


def _flat(text: str) -> list[str]:
    """The declared identifiers of declarations at nesting depth 0 of ``text`` (brackets and blocks blanked)."""
    flat, depth = [], 0
    for c in text:  # angle brackets are not counted: a shift or a comparison would unbalance them
        if c in "([{":
            depth += 1
            flat.append(c if depth == 1 else " ")
            continue
        if c in ")]}":
            depth -= 1
            flat.append(c if depth == 0 else " ")
            continue
        flat.append(c if depth == 0 else " ")
    s = "".join(flat)
    s = re.sub(r"=[^;,]*", "=", s)  # initializers name nothing new
    s = re.sub(r":\s*\w+(\s*\([^)]*\))?", ":", s)  # semantics and registers
    return _DECLARED_RE.findall(s)


def _strip_attributes(head: str) -> str:
    return re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", head)


def _strip_defaults(text: str, head: str) -> str:
    """The definition without its parameters' default values (they stay on the declaration)."""
    skip = len(head) - len(_strip_attributes(head))  # an attribute's own brackets are not the parameters
    open_ = head.index("(", skip)
    close = _close_paren(head, open_)
    params = [re.sub(r"\s*=[^,]*$", "", p) for p in split_args(head[open_ + 1 : close], angles=True)]
    return head[: open_ + 1] + ", ".join(params) + head[close:] + text[len(head) :]


def _close_paren(text: str, i: int) -> int:
    depth = 0
    for j in range(i, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            return j
    raise EffectError("an unclosed parameter list")


def _param_name(param: str) -> str:
    left = param.split(":", 1)[0].split("=", 1)[0].strip()
    return re.sub(r"\[[^\]]*\]$", "", left).split()[-1]


def _line_markers(text: str) -> list[tuple[int, int, str]]:
    """Every ``#line``: (the position after it, its line, the file it names or the one before)."""
    out, name = [], ""
    for m in _LINE_RE.finditer(text):
        name = m.group(2) if m.group(2) is not None else name
        out.append((m.end(), int(m.group(1)), name))
    return out


def _line_marker(text: str, markers: list[tuple[int, int, str]], pos: int) -> str:
    """The ``#line`` that puts the text at ``pos`` back at its file and line."""
    k = bisect.bisect_right(markers, (pos, 1 << 62, "")) - 1
    if k < 0:
        return ""
    at, line, name = markers[k]
    return f'#line {line + text.count(chr(10), at, pos)} "{name}"\n'
