"""Our GLSL 330 shaders in the form Qt's shader baker takes (Vulkan-style GLSL 440): one source for both devices.

The suite's shaders are written once, in GLSL 330 with loose uniforms (what the OpenGL device compiles as it is);
``port(vertex, fragment)`` rewrites a pair for the QRhi device: the loose uniforms of both stages become one std140
block at binding 0, each sampler a binding of its own, every stage input and output a location, the built-ins their
Vulkan names, and the vertex shader ends by correcting its clip position for the backend (``rhiClip``: y and depth
range, set per draw by ``rhidevice``). docs/tool-suite.md section 21, "Our own shaders".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BLOCK = "Params"
CLIP = "rhiClip"  # the clip correction the device writes into every draw's block
_SAMPLER = re.compile(r"^[iu]?sampler\w+$")
_QUALIFIERS = ("flat", "noperspective", "smooth", "centroid")
_LOCATIONS = {"mat2": 2, "mat3": 3, "mat4": 4, "mat3x4": 3, "mat4x3": 4}
_NAME = re.compile(r"\s*(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*$")


@dataclass(frozen=True)
class Variable:
    """A declared name: its type, array length (0: not an array), and where the port put it."""

    name: str
    type: str
    count: int = 0
    slot: int = 0  # a sampler's binding, an input's or output's location


@dataclass
class Ported:
    """A program as the QRhi device takes it: the two stage sources, the block's members in order, the samplers
    (with the stages that read them: "v", "f"), the vertex inputs and the fragment outputs."""

    vertex: str
    fragment: str
    members: list[Variable] = field(default_factory=list)
    samplers: dict[str, Variable] = field(default_factory=dict)
    sampler_stages: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Variable] = field(default_factory=dict)
    outputs: list[Variable] = field(default_factory=list)


def strip_comments(source: str) -> str:
    """The source without ``//`` and ``/* */`` comments (the port reads declarations, not prose)."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def global_statements(source: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of every statement at file scope ending in ``;`` (declarations); function bodies,
    structure bodies and preprocessor lines are skipped."""
    out: list[tuple[int, int, str]] = []
    depth, start, i = 0, 0, 0
    while i < len(source):
        ch = source[i]
        if ch == "#" and depth == 0 and source[start:i].strip() == "":
            end = source.find("\n", i)
            i = start = len(source) if end < 0 else end + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                start = i + 1
        elif ch == ";" and depth == 0:
            out.append((start, i + 1, source[start:i]))
            start = i + 1
        i += 1
    return out


def _names(text: str) -> list[tuple[str, int]]:
    found = []
    for part in text.split(","):
        m = _NAME.match(part)
        if not m:
            raise ValueError(f"cannot read the declaration {text.strip()!r}")
        found.append((m.group(1), int(m.group(2) or 0)))
    return found


@dataclass
class _Stage:
    text: str
    uniforms: list[Variable] = field(default_factory=list)
    samplers: list[Variable] = field(default_factory=list)
    ins: list[tuple[str, Variable]] = field(default_factory=list)  # (qualifier, variable)
    outs: list[tuple[str, Variable]] = field(default_factory=list)
    cuts: list[tuple[int, int, str, list[str]]] = field(default_factory=list)  # (start, end, kind, names)


def _read(source: str) -> _Stage:
    text = strip_comments(source)
    text = re.sub(r"(?m)^\s*#version[^\n]*\n", "", text)
    stage = _Stage(text)
    for start, end, statement in global_statements(text):
        words = statement.split()
        if not words:
            continue
        qualifier = words[0] if words[0] in _QUALIFIERS else ""
        rest = words[1:] if qualifier else words
        if len(rest) < 3 or rest[0] not in ("uniform", "in", "out"):
            continue
        kind, gtype, names = rest[0], rest[1], " ".join(rest[2:])
        variables = [Variable(n, gtype, c) for n, c in _names(names)]
        if kind == "uniform":
            (stage.samplers if _SAMPLER.match(gtype) else stage.uniforms).extend(variables)
        else:
            (stage.ins if kind == "in" else stage.outs).extend((qualifier, v) for v in variables)
        stage.cuts.append((start, end, kind, [v.name for v in variables]))
    return stage


def _union(*lists: list[Variable]) -> list[Variable]:
    seen: dict[str, Variable] = {}
    for variables in lists:
        for v in variables:
            have = seen.get(v.name)
            if have is not None and (have.type, have.count) != (v.type, v.count):
                raise ValueError(f"uniform {v.name} declared as {have.type} and {v.type}")
            seen.setdefault(v.name, v)
    return list(seen.values())


def _slots(variables: list[tuple[str, Variable]], first: int = 0) -> list[tuple[str, Variable]]:
    out, slot = [], first
    for qualifier, v in variables:
        out.append((qualifier, Variable(v.name, v.type, v.count, slot)))
        slot += _LOCATIONS.get(v.type, 1) * max(v.count, 1)
    return out


def _decl(qualifier: str, kind: str, v: Variable) -> str:
    array = f"[{v.count}]" if v.count else ""
    q = f"{qualifier} " if qualifier else ""
    return f"layout(location = {v.slot}) {q}{kind} {v.type} {v.name}{array};"


def _emit(stage: _Stage, header: str, decls: dict[str, str]) -> str:
    """The stage's text with its uniform and stage-variable declarations replaced, ``header`` in front."""
    text, out, pos = stage.text, [], 0
    for start, end, kind, names in stage.cuts:
        out.append(text[pos:start])
        out.append(" ".join(decls[f"{kind}:{n}"] for n in names if f"{kind}:{n}" in decls))
        pos = end
    out.append(text[pos:])
    body = "".join(out)
    body = re.sub(r"\bgl_VertexID\b", "gl_VertexIndex", body)
    return header + re.sub(r"\bgl_InstanceID\b", "gl_InstanceIndex", body)


def _block(members: list[Variable]) -> str:
    lines = [f"    {m.type} {m.name}{f'[{m.count}]' if m.count else ''};" for m in members]
    return f"layout(std140, binding = 0) uniform {BLOCK} {{\n" + "\n".join(lines) + "\n};\n"


def port(vertex: str, fragment: str) -> Ported:
    """The pair rewritten for the QRhi device (module docstring); raises ``ValueError`` on what it cannot read."""
    vs, fs = _read(vertex), _read(fragment)
    members = [*_union(vs.uniforms, fs.uniforms), Variable(CLIP, "vec2")]
    samplers, stages = {}, {}
    for v in _union(vs.samplers, fs.samplers):
        samplers[v.name] = Variable(v.name, v.type, v.count, len(samplers) + 1)
        stages[v.name] = ("v" if any(s.name == v.name for s in vs.samplers) else "") + (
            "f" if any(s.name == v.name for s in fs.samplers) else ""
        )
    attributes = _slots(vs.ins)
    # what the fragment shader reads first: Direct3D links a stage only to a prefix of the one before
    read = [n for _, n in ((q, v.name) for q, v in fs.ins)]
    order = sorted(vs.outs, key=lambda qv: read.index(qv[1].name) if qv[1].name in read else len(read))
    varyings = {v.name: (q, v) for q, v in _slots(order)}
    fs_ins = []
    for q, v in fs.ins:
        if v.name not in varyings:
            raise ValueError(f"the fragment input {v.name} has no vertex output")
        fs_ins.append((q, varyings[v.name][1]))
    outputs = _slots(fs.outs)
    head = "#version 440\n" + _block(members)
    sampler_decl = {
        f"uniform:{s.name}": f"layout(binding = {s.slot}) uniform {s.type} {s.name}{f'[{s.count}]' if s.count else ''};"
        for s in samplers.values()
    }
    vs_decls = dict(sampler_decl)
    vs_decls |= {f"in:{v.name}": _decl(q, "in", v) for q, v in attributes}
    vs_decls |= {f"out:{v.name}": _decl(q, "out", v) for q, v in varyings.values()}
    fs_decls = dict(sampler_decl)
    fs_decls |= {f"in:{v.name}": _decl(q, "in", v) for q, v in fs_ins}
    fs_decls |= {f"out:{v.name}": _decl(q, "out", v) for q, v in outputs}
    vtext = _emit(vs, head, vs_decls)
    if not re.search(r"\bvoid\s+main\s*\(\s*\)", vtext):
        raise ValueError("the vertex shader has no main()")
    vtext = re.sub(r"\bvoid\s+main\s*\(\s*\)", "void rhi_main()", vtext, count=1)
    # y flipped where the backend's image is upside down against OpenGL's, depth into [0, 1] where clip depth is
    vtext += (
        "\nvoid main() {\n    rhi_main();\n"
        f"    gl_Position.y *= {CLIP}.x;\n"
        f"    gl_Position.z = mix(gl_Position.z, (gl_Position.z + gl_Position.w) * 0.5, {CLIP}.y);\n}}\n"
    )
    return Ported(
        vertex=vtext,
        fragment=_emit(fs, head, fs_decls),
        members=members,
        samplers=samplers,
        sampler_stages=stages,
        attributes={v.name: v for _, v in attributes},
        outputs=[v for _, v in outputs],
    )
