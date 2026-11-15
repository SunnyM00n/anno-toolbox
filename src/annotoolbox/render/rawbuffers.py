"""The engine's structured buffers rewritten as raw buffers, the one kind of storage buffer QRhi binds: a structure's
layout worked out from its declaration (structured-buffer packing), each element read with ``Load<T>``, a member's
assignment a load, change and store of its element, an ``Interlocked*`` on a member that call on the buffer at the
member's byte offset.

``rewrite(hlsl, names)`` is the entry (text in, text out; ``gameshader.adapt`` calls it); ``layout(hlsl, struct)``
gives a structure's size and member offsets. docs/rendering/frame-117-viewer.md section 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..formats import fxeffect

SCALARS = {"float": 4, "int": 4, "uint": 4, "bool": 4, "dword": 4, "half": 4, "min16float": 4}
_TYPE = re.compile(r"^(float|int|uint|bool|dword|half|min16float)(\d)?(?:x(\d))?$")


@dataclass(frozen=True)
class Member:
    offset: int
    type: str
    size: int  # one element's
    count: int  # an array's length, 1 for one value


def _body(hlsl: str, struct: str) -> str | None:
    m = re.search(rf"\bstruct\s+{re.escape(struct)}\s*\{{", hlsl)
    return hlsl[m.end() : fxeffect.matching(hlsl, m.end() - 1, "{}")] if m else None


def structured_size(hlsl: str, type_name: str) -> int:
    """A type's bytes in a structured buffer: scalars, vectors and matrices of 4-byte components, a structure the sum
    of its members (no 16-byte rows in this packing)."""
    m = _TYPE.match(type_name)
    if m:
        return SCALARS[m.group(1)] * int(m.group(2) or 1) * int(m.group(3) or 1)
    return layout(hlsl, type_name)[0]


def layout(hlsl: str, struct: str) -> tuple[int, dict[str, Member]]:
    """(size, members by name) of ``struct`` as a structured buffer packs it."""
    body = _body(hlsl, struct)
    if body is None:
        raise ValueError(f"no struct {struct}")
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)
    members, at = {}, 0
    for kind, name, count in re.findall(r"(?:\b(?:row_major|column_major|precise)\s+)*(\w+)\s+(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*;", body):  # fmt: skip
        one = structured_size(hlsl, kind)
        members[name] = Member(at, kind, one, int(count or 1))
        at += one * int(count or 1)
    return at, members


def _chain(hlsl: str, text: str, at: int, element: str) -> tuple[int, str, str]:
    """Reads ``.Member[expr].Sub`` from ``at``: (its end, the byte offset as an expression, the last member's type)."""
    offset, kind = "0", element
    while at < len(text):
        m = re.match(r"\s*\.\s*(\w+)", text[at:])
        if not m or _TYPE.match(kind):
            break
        member = layout(hlsl, kind)[1][m.group(1)]
        offset, kind, at = f"{offset} + {member.offset}", member.type, at + m.end()
        if text[at : at + 1] == "[":
            close = fxeffect.matching(text, at)
            offset = f"{offset} + ({text[at + 1 : close]}) * {member.size}"
            at = close + 1
    return at, offset, kind


def _uses(text: str, name: str):
    """The places ``name[`` is indexed, from the last (so earlier rewrites keep their offsets)."""
    return [m.start() for m in re.finditer(rf"\b{name}\s*\[", text)][::-1]


def matrix_loader(kind: str) -> str:
    """The function that reads a matrix of ``kind`` (``float4x3``) from a raw buffer as a structured buffer stores it,
    column by column (the shaders are compiled ``-Zpc``), whatever the compiler's templated load makes of a matrix."""
    m = _TYPE.match(kind)
    rows, cols = int(m.group(2)), int(m.group(3))
    loads = ", ".join(f"b.Load<{m.group(1)}{rows}>(a + {4 * rows * k})" for k in range(cols))
    return (
        f"{kind} rb_{kind}(RWByteAddressBuffer b, uint a) {{ return transpose({m.group(1)}{cols}x{rows}({loads})); }}\n"
    )


def _is_matrix(kind: str) -> bool:
    m = _TYPE.match(kind)
    return bool(m and m.group(3))


def _rewrite_one(hlsl: str, name: str, element: str) -> str:
    stride = structured_size(hlsl, element)
    text = hlsl
    for at in _uses(text, name):
        bracket = text.index("[", at)
        close = fxeffect.matching(text, bracket)
        base = f"(uint)({text[bracket + 1 : close]}) * {stride}"
        end, offset, _kind = _chain(hlsl, text, close + 1, element)
        call = re.search(r"\b(Interlocked\w+)\s*\(\s*$", text[:at])
        assign = re.match(r"\s*=(?!=)", text[end:])
        if call:  # InterlockedMin(name[i].Member, value...) -> name.InterlockedMin(address, value...)
            start = call.start()
            comma = text.index(",", end)
            text = text[:start] + f"{name}.{call.group(1)}({base} + {offset}, " + text[comma + 1 :].lstrip()
        elif assign:  # name[i].path = value; -> load the element, change it, store it back
            stop = text.index(";", end)
            value = text[end + assign.end() : stop].strip()
            path = text[close + 1 : end]
            text = (text[:at] + f"{{ {element} rw_ = {name}.Load<{element}>({base}); rw_{path} = {value}; "
                    f"{name}.Store<{element}>({base}, rw_); }}" + text[stop + 1 :])  # fmt: skip
        elif _is_matrix(element):
            text = text[:at] + f"rb_{element}({name}, {base})" + text[close + 1 :]
        else:
            text = text[:at] + f"{name}.Load<{element}>({base})" + text[close + 1 :]
    return text


def _parameters(hlsl: str, found: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """A function parameter of a structured-buffer type (``StructuredBuffer<float4x3> boneMatrixBuffer``) as a raw
    buffer when every structured buffer of its element type is one (the arguments are), with its reads."""
    raw = {element for element, _name in found}
    kept = set(re.findall(r"(?m)^\s*(?:RW)?StructuredBuffer\s*<\s*(\w+)\s*>\s*\w+\s*;", hlsl))
    params = []

    def param(m: re.Match) -> str:
        if m.group(1) not in raw or m.group(1) in kept:
            return m.group(0)
        params.append((m.group(1), m.group(2)))
        return f"RWByteAddressBuffer {m.group(2)}"

    hlsl = re.sub(r"\bStructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)(?=\s*[,)])", param, hlsl)
    return hlsl, params


def rewrite(hlsl: str, names: tuple[str, ...] = ()) -> str:
    """Every ``RWStructuredBuffer<T>`` and the read-only ``StructuredBuffer<T>`` of ``names`` as a
    ``RWByteAddressBuffer`` (QRhi's Direct3D 12 backend binds a storage buffer as an unordered-access view: a
    read-only raw buffer made the pipeline fail), their uses rewritten (module docstring)."""
    found = re.findall(r"(?m)^\s*(?:globallycoherent\s+)?RWStructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)\s*;", hlsl)
    for name in names:
        found += [(t, name) for t in re.findall(rf"(?m)^\s*StructuredBuffer\s*<\s*(\w+)\s*>\s*{name}\s*;", hlsl)]
    for _element, name in found:
        hlsl = re.sub(rf"(?m)^(\s*(?:globallycoherent\s+)?)(?:RW)?StructuredBuffer\s*<\s*\w+\s*>\s*{name}\s*;",
                      rf"\1RWByteAddressBuffer {name};", hlsl)  # fmt: skip
    hlsl, params = _parameters(hlsl, found)
    for element, name in found + params:
        hlsl = _rewrite_one(hlsl, name, element)
    loaders = sorted({element for element, _name in found + params if _is_matrix(element)})
    return "".join(matrix_loader(kind) for kind in loaders) + hlsl
