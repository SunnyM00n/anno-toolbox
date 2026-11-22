"""What one shader of an effect file uses, in an order a DirectX compiler takes: only what the entry reaches,
nothing used before it is declared (docs/rendering/shader-compile.md, "The effect file").

``CProgram(hlsl)`` splits the HLSL ``fxeffect.split_effect`` leaves into its top-level items once;
``CProgram.entry_source(call, passthrough, Program.bindings(technique.functions))`` returns the text and the entry
name for one pass shader: the reached declarations, the resources at the engine's registers in its order
(``fxbind``), a declaration of every reached function, the functions, and an entry under the function's name when
the pass gives literal arguments to ``uniform`` parameters.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from . import fxbind
from .fxeffect import MARKER, CEffectError, SShaderCall, define_lines, split_arguments

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
class SItem:
    """One top-level piece: a declaration, a function definition or a leftover directive."""

    where: str  # the #line that puts it back at its file and line
    text: str
    head: str | None  # a function's head (up to the parameters and the semantic); None for anything else
    names: frozenset[str]  # what it declares
    uses: frozenset[str]  # every identifier in it
    resource: fxbind.SResource | None = None  # a resource declaration's binding facts


class CProgram:
    """The top-level items of an effect's HLSL, indexed by the names they declare."""

    def __init__(self, hlsl: str) -> None:
        markers = _line_markers(hlsl)
        self.m_items: list[SItem] = []
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
            idents = frozenset(_IDENT_RE.findall(text)) - names - _fields(text)
            where = _line_marker(hlsl, markers, start)
            resource = _resource(text, words, where) if head is None else None
            self.m_items.append(SItem(where, text, head, names, idents, resource))
        self.m_by_name: dict[str, list[int]] = {}
        for i, item in enumerate(self.m_items):
            for name in item.names:
                self.m_by_name.setdefault(name, []).append(i)
        unbounded = [item.resource.name for item in self.m_items if item.resource and item.resource.count == 0]
        self._m_own_space = {name: 100 + k for k, name in enumerate(unbounded)}  # unbound: a space each

    def resources(self, roots: set[str]) -> list[fxbind.SResource]:
        """The resources the names in ``roots`` reach, in file order: one pass's are its tables
        (``fxbind.bind``)."""
        return [resource for i in self.reached(roots) if (resource := self.m_items[i].resource) is not None]

    def declared(self) -> list[fxbind.SResource]:
        """Every resource of the effect, in file order."""
        return [item.resource for item in self.m_items if item.resource is not None]

    def bindings(self, functions: set[str]) -> dict[str, str]:
        """The registers of the pass whose shader functions are ``functions``, as the engine binds them."""
        return fxbind.bind(self.resources(functions), self.declared())

    def _declaration(self, item: SItem, bindings: dict[str, str] | None) -> str:
        """A declaration as emitted: a resource at its binding; without one the compiler places it (an unbounded
        array in a space of its own, since it takes every register after it)."""
        resource = item.resource
        if resource is None:
            return item.text
        match = _RESOURCE_RE.match(item.text)
        clause = (bindings or {}).get(resource.name)
        if clause is None and resource.count == 0:
            clause = f"register({fxbind.LETTERS.get(resource.kind, 't')}0, space{self._m_own_space[resource.name]})"
        if clause is None:
            if bindings is not None and resource.space is not None or not match.group(5) or match.group(6) == "b":
                return item.text  # a space the source names itself, a constant buffer's slot: as written
            return item.text[: match.start(5)] + item.text[match.end(5) :]
        if match.group(5):
            return item.text[: match.start(5)] + ": " + clause + item.text[match.end(5) :]
        end = match.end(4) if match.group(4) else match.end(3)
        return item.text[:end] + " : " + clause + item.text[end:]

    def reached(self, roots: set[str]) -> list[int]:
        """The items the names in ``roots`` lead to, through every identifier they use, in file order; the
        directives left at the top level always count."""
        seen: set[int] = set()
        todo = [i for name in roots for i in self.m_by_name.get(name, ())]
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            todo += [j for name in self.m_items[i].uses for j in self.m_by_name.get(name, ()) if j not in seen]
        return sorted(seen | {i for i, item in enumerate(self.m_items) if item.text.startswith("#")})

    def source(
        self, roots: set[str], bindings: dict[str, str] | None = None, rename: tuple[str, str] | None = None
    ) -> str:
        """The reached declarations, then a declaration of each reached function, then the functions
        (``bindings``: each resource's register, from ``fxbind.bind``; ``rename``: a function emitted under
        another name)."""
        chosen = [self.m_items[i] for i in self.reached(roots)]
        decls = [item.where + item.text for item in chosen if item.head is None and _bound_name(item) is None]

        # the engine declares its resources root constants first, then by table and register, the samplers by name:
        # the compiler numbers them in that order
        def order(item: SItem) -> tuple[int, int, int, str]:
            name = _bound_name(item) or ""
            match = _CLAUSE_RE.search((bindings or {}).get(name) or item.text)
            if item.resource is None or match is None:  # the engine's samplers by name
                return 2, 0, 0, name
            space = int(match.group(2) or 0)
            return int(space != fxbind.ROOT_SPACE), space, int(match.group(1)), name  # root constants first

        bound = sorted((item for item in chosen if item.head is None and _bound_name(item) is not None), key=order)
        decls += [item.where + self._declaration(item, bindings) for item in bound]
        # no declaration of a geometry or compute shader: FXC wants its maxvertexcount or numthreads there too, and
        # nothing calls an entry
        protos = [
            item.where + _named(_strip_attributes(item.head), item, rename) + ";\n"
            for item in chosen
            if item.head and not re.search(r"\b(?:maxvertexcount|numthreads)\b", item.head)
        ]
        bodies = [
            item.where + _named(_strip_defaults(item.text, item.head), item, rename) for item in chosen if item.head
        ]
        return "".join(decls) + "\n" + "".join(protos) + "".join(bodies)

    def entry_source(
        self, call: SShaderCall, passthrough: tuple[str, ...] = (), bindings: dict[str, str] | None = None
    ) -> tuple[str, str]:
        """The HLSL and the entry name for one pass shader (``passthrough``: the technique's PassthroughDefines,
        which the compiler's own preprocessor sees; ``bindings``: its pass's registers, else the compiler
        places)."""
        head = define_lines([(name, "") for name in passthrough])
        wrapper = self._wrapper(call) if call.args else ""
        if not wrapper:
            return head + self.source({call.function}, bindings), call.function
        body = self.source({call.function}, bindings, (call.function, _inner_name(call)))
        return head + body + '\n#line 1 "<entry>"\n' + wrapper, call.function

    def _wrapper(self, call: SShaderCall) -> str:
        """An entry under the function's name that passes the pass's literal arguments to its ``uniform``
        parameters (the function itself renamed, ``_inner_name``); empty when it has none."""
        overloads = [self.m_items[i] for i in self.m_by_name.get(call.function, ()) if self.m_items[i].head]
        if not overloads:
            raise CEffectError(f"shader function {call.function} not found")
        item = next(
            (overload for overload in overloads if _uniform_count(overload.head) == len(call.args)), overloads[0]
        )
        head, bare = item.head, _strip_attributes(item.head)
        attributes = re.findall(r"\[[^\]]*\]", head[: len(head) - len(bare)])
        parameters = split_arguments(
            bare[bare.index("(") + 1 : _close_paren(bare, bare.index("("))], is_parameter_list=True
        )
        semantic = re.search(r"\)\s*:\s*(\w+)\s*$", bare)
        returns = bare[: bare.index(call.function)].split()[-1]
        uniform = [parameter for parameter in parameters if re.match(r"\s*uniform\b", parameter)]
        if not uniform:  # arguments for a function without uniform parameters: the game compiles it as it is
            return ""
        if len(uniform) != len(call.args):
            raise CEffectError(f"{call.function}: {len(call.args)} arguments for {len(uniform)} uniform parameters")
        values, kept, arguments = iter(call.args), [], []
        for parameter in parameters:
            if re.match(r"\s*uniform\b", parameter):
                arguments.append(next(values))
            else:
                kept.append(re.sub(r"\s*=[^,]*$", "", parameter))
                arguments.append(_parameter_name(parameter))
        tail = f" : {semantic.group(1)}" if semantic else ""
        return_keyword = "" if returns == "void" else "return "
        return (
            f"{' '.join(attributes)}\n{returns} {call.function}({', '.join(kept)}){tail}\n"
            f"{{\n\t{return_keyword}{_inner_name(call)}({', '.join(arguments)});\n}}\n"
        )


def _inner_name(call: SShaderCall) -> str:
    """The name a shader function with literal arguments is emitted under, its entry taking its own."""
    return f"{call.function}__{'_'.join(re.sub(r'\W', '', argument) or 'x' for argument in call.args)}"


def _named(text: str, item: SItem, rename: tuple[str, str] | None) -> str:
    """A function's head or definition, under the new name when ``rename`` names it."""
    if rename is None or rename[0] not in item.names:
        return text
    return re.sub(rf"\b{re.escape(rename[0])}(\s*\()", rename[1] + r"\1", text, count=1)


def _resource(text: str, words: frozenset[str], where: str) -> fxbind.SResource | None:
    """A top-level resource declaration's binding facts, None for anything else."""
    match = _RESOURCE_RE.match(text)
    kind = fxbind.kind_of(match.group(2)) if match else None
    if kind is None:
        return None
    array_size = (match.group(4) or "").strip("[] \t")
    count = 1 if match.group(4) is None else 0 if not array_size else _array_size(array_size)
    slot = int(match.group(7)) if match.group(7) else None
    space = int(match.group(8)) if match.group(8) else None
    file = _FILE_RE.search(where)
    return fxbind.SResource(match.group(3), kind, words, slot, space, count, file.group(1) if file else "")


_CLAUSE_RE = re.compile(r"register\s*\(\s*[a-z](\d+)\s*(?:,\s*space(\d+)\s*)?\)")
_SAMPLER_RE = re.compile(r"^\s*(?:static\s+)?Sampler(?:Comparison)?State\s+(\w+)\s*(?::|;|\[)")


def _bound_name(item: SItem) -> str | None:
    """The name of a resource or sampler declaration, None for anything else."""
    if item.resource is not None:
        return item.resource.name
    match = _SAMPLER_RE.match(item.text)
    return match.group(1) if match else None


def _array_size(text: str) -> int:
    """An array's length as the preprocessor leaves it: a number or a product of numbers (else 1)."""
    factors = [factor.strip().strip("()") for factor in text.split("*")]
    if not all(factor.isdigit() for factor in factors):
        return 1
    element_count = 1
    for factor in factors:
        element_count *= int(factor)
    return element_count


def _uniform_count(head: str) -> int:
    bare = _strip_attributes(head)
    parameters = split_arguments(
        bare[bare.index("(") + 1 : _close_paren(bare, bare.index("("))], is_parameter_list=True
    )
    return sum(1 for parameter in parameters if re.match(r"\s*uniform\b", parameter))


def _top_items(text: str) -> list[tuple[int, str]]:
    """The top-level items (declarations, definitions, other directives) with their start, ``#line`` left out."""
    items, start, depth, i, length = [], 0, 0, 0, len(text)
    while i < length:
        character = text[i]
        if character == "#" and depth == 0 and not text[start:i].strip():
            end = text.find("\n", i)
            end = length if end < 0 else end + 1
            if not _LINE_RE.match(text, i):
                items.append((i, text[i:end]))
            start = i = end
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:  # a block ends the item, with the ';' after it when there is one
                j = i + 1
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] != ";" and text[start:i].lstrip().startswith("struct"):
                    j = _declarators_end(text, j)  # struct S { ... } s; (1701's constants)
                end = j if j < length and text[j] == ";" else i
                items.append((start, text[start : end + 1] + "\n"))
                start = i = end + 1
                continue
        elif character == ";" and depth == 0:
            items.append((start, text[start : i + 1] + "\n"))
            start = i + 1
        i += 1
    if text[start:].strip():
        items.append((start, text[start:]))
    return [
        (offset + len(chunk) - len(chunk.lstrip()), chunk.strip() + "\n") for offset, chunk in items if chunk.strip()
    ]


_DECLARATORS_RE = re.compile(r"[\w\s,\[\]]*;")


def _declarators_end(text: str, start: int) -> int:
    """The ``;`` closing the variables a structure's definition declares (``} vcLightingDir;``), else ``at``."""
    match = _DECLARATORS_RE.match(text, start)
    return match.end() - 1 if match and "#" not in match.group(0) else start


def _function_head(item: str) -> str | None:
    """The head of a function definition (up to its parameter list and semantic), or None for anything else."""
    brace = item.find("{")
    if brace < 0 or _NOT_FUNCTION_RE.match(item):
        return None
    head = item[:brace].rstrip()
    match = re.search(r"\)\s*(?::\s*\w+\s*)?$", head)
    if match is None or "=" in head[: head.find("(")]:
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
    match = _KEYWORD_HEAD_RE.match(text)
    names = {match.group(2)} if match else set()
    if match and match.group(1) in ("cbuffer", "tbuffer", "enum", "namespace") and "{" in text:
        inner = text[text.index("{") + 1 : text.rindex("}")]
        names |= {x for x in _flat(inner) if x}
    elif match and match.group(1) == "struct" and "}" in text:
        names |= {x for x in _flat(text[text.rindex("}") + 1 :]) if x}  # the variables after the definition
    elif not match:
        names |= set(_flat(text))
    return frozenset(names)


def _fields(text: str) -> frozenset[str]:
    """A structure's field names: what it declares, not what it uses (a field named like a constant buffer's member
    does not reach that buffer, as the engine's effect compiler does not)."""
    match = _KEYWORD_HEAD_RE.match(text)
    if not match or match.group(1) != "struct" or "{" not in text:
        return frozenset()
    return frozenset(x for x in _flat(text[text.index("{") + 1 : text.rindex("}")]) if x)


def _flat(text: str) -> list[str]:
    """The declared identifiers of declarations at nesting depth 0 of ``text`` (brackets and blocks blanked)."""
    flat, depth = [], 0
    for character in text:  # angle brackets are not counted: a shift or a comparison would unbalance them
        if character in "([{":
            depth += 1
            flat.append(character if depth == 1 else " ")
            continue
        if character in ")]}":
            depth -= 1
            flat.append(character if depth == 0 else " ")
            continue
        flat.append(character if depth == 0 else " ")
    joined = "".join(flat)
    joined = re.sub(r"=[^;,]*", "=", joined)  # initializers name nothing new
    joined = re.sub(r":\s*\w+(\s*\([^)]*\))?", ":", joined)  # semantics and registers
    return _DECLARED_RE.findall(joined)


def _strip_attributes(head: str) -> str:
    return re.sub(r"^\s*(?:\[[^\]]*\]\s*)+", "", head)


def _strip_defaults(text: str, head: str) -> str:
    """The definition without its parameters' default values (they stay on the declaration)."""
    skip = len(head) - len(_strip_attributes(head))  # an attribute's own brackets are not the parameters
    open_ = head.index("(", skip)
    close = _close_paren(head, open_)
    parameters = [
        re.sub(r"\s*=[^,]*$", "", parameter)
        for parameter in split_arguments(head[open_ + 1 : close], is_parameter_list=True)
    ]
    return head[: open_ + 1] + ", ".join(parameters) + head[close:] + text[len(head) :]


def _close_paren(text: str, i: int) -> int:
    depth = 0
    for j in range(i, len(text)):
        depth += {"(": 1, ")": -1}.get(text[j], 0)
        if depth == 0:
            return j
    raise CEffectError("an unclosed parameter list")


def _parameter_name(parameter: str) -> str:
    left = parameter.split(":", 1)[0].split("=", 1)[0].strip()
    return re.sub(r"\[[^\]]*\]$", "", left).split()[-1]


def _line_markers(text: str) -> list[tuple[int, int, str]]:
    """Every ``#line``: (the position after it, its line, the file it names or the one before)."""
    out, name = [], ""
    for match in _LINE_RE.finditer(text):
        name = match.group(2) if match.group(2) is not None else name
        out.append((match.end(), int(match.group(1)), name))
    return out


def _line_marker(text: str, markers: list[tuple[int, int, str]], position: int) -> str:
    """The ``#line`` that puts the text at ``pos`` back at its file and line."""
    k = bisect.bisect_right(markers, (position, 1 << 62, "")) - 1
    if k < 0:
        return ""
    offset, line, name = markers[k]
    return f'#line {line + text.count(chr(10), offset, position)} "{name}"\n'
