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
class SVariable:
    """A declared name: its type, array length (0: not an array), and where the port put it."""

    name: str
    type: str
    count: int = 0
    slot: int = 0  # a sampler's binding, an input's or output's location


@dataclass
class SPorted:
    """A program as the QRhi device takes it: the two stage sources, the block's members in order, the samplers
    (with the stages that read them: "v", "f"), the vertex inputs and the fragment outputs."""

    vertex: str
    fragment: str
    members: list[SVariable] = field(default_factory=list)
    samplers: dict[str, SVariable] = field(default_factory=dict)
    sampler_stages: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, SVariable] = field(default_factory=dict)
    outputs: list[SVariable] = field(default_factory=list)


def strip_comments(source: str) -> str:
    """The source without ``//`` and ``/* */`` comments (the port reads declarations, not prose)."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def global_statements(source: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of every statement at file scope ending in ``;`` (declarations); function bodies,
    structure bodies and preprocessor lines are skipped. ``ValueError`` on unbalanced braces."""
    out: list[tuple[int, int, str]] = []
    depth, start, i = 0, 0, 0
    while i < len(source):
        character = source[i]
        if character == "#" and depth == 0 and source[start:i].strip() == "":
            end = source.find("\n", i)
            i = start = len(source) if end < 0 else end + 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"the source closes a brace it did not open (at character {i})")
            if depth == 0:
                start = i + 1
        elif character == ";" and depth == 0:
            out.append((start, i + 1, source[start:i]))
            start = i + 1
        i += 1
    if depth:
        raise ValueError("the source opens a brace it does not close")
    return out


def _names(text: str) -> list[tuple[str, int]]:
    found = []
    for part in text.split(","):
        match = _NAME.match(part)
        if not match:
            raise ValueError(f"cannot read the declaration {text.strip()!r}")
        found.append((match.group(1), int(match.group(2) or 0)))
    return found


@dataclass
class _SStage:
    text: str
    uniforms: list[SVariable] = field(default_factory=list)
    samplers: list[SVariable] = field(default_factory=list)
    ins: list[tuple[str, SVariable]] = field(default_factory=list)  # (qualifier, variable)
    outs: list[tuple[str, SVariable]] = field(default_factory=list)
    cuts: list[tuple[int, int, str, list[str]]] = field(default_factory=list)  # (start, end, kind, names)


def _read(source: str) -> _SStage:
    text = strip_comments(source)
    text = re.sub(r"(?m)^\s*#version[^\n]*\n", "", text)
    stage = _SStage(text)
    for start, end, statement in global_statements(text):
        words = statement.split()
        if not words:
            continue
        qualifier = words[0] if words[0] in _QUALIFIERS else ""
        rest = words[1:] if qualifier else words
        if len(rest) < 3 or rest[0] not in ("uniform", "in", "out"):
            continue
        kind, gtype, names = rest[0], rest[1], " ".join(rest[2:])
        variables = [SVariable(name, gtype, count) for name, count in _names(names)]
        if kind == "uniform":
            (stage.samplers if _SAMPLER.match(gtype) else stage.uniforms).extend(variables)
        else:
            (stage.ins if kind == "in" else stage.outs).extend((qualifier, variable) for variable in variables)
        stage.cuts.append((start, end, kind, [variable.name for variable in variables]))
    return stage


def _union(*lists: list[SVariable]) -> list[SVariable]:
    seen: dict[str, SVariable] = {}
    for variables in lists:
        for variable in variables:
            have = seen.get(variable.name)
            if have is not None and (have.type, have.count) != (variable.type, variable.count):
                raise ValueError(f"uniform {variable.name} declared as {have.type} and {variable.type}")
            seen.setdefault(variable.name, variable)
    return list(seen.values())


def _slots(variables: list[tuple[str, SVariable]], first: int = 0) -> list[tuple[str, SVariable]]:
    out, slot = [], first
    for qualifier, variable in variables:
        out.append((qualifier, SVariable(variable.name, variable.type, variable.count, slot)))
        slot += _LOCATIONS.get(variable.type, 1) * max(variable.count, 1)
    return out


def _decl(qualifier: str, kind: str, variable: SVariable) -> str:
    array = f"[{variable.count}]" if variable.count else ""
    prefix = f"{qualifier} " if qualifier else ""
    return f"layout(location = {variable.slot}) {prefix}{kind} {variable.type} {variable.name}{array};"


def _emit(stage: _SStage, header: str, decls: dict[str, str]) -> str:
    """The stage's text with its uniform and stage-variable declarations replaced, ``header`` in front."""
    text, out, position = stage.text, [], 0
    for start, end, kind, names in stage.cuts:
        out.append(text[position:start])
        out.append(" ".join(decls[f"{kind}:{name}"] for name in names if f"{kind}:{name}" in decls))
        position = end
    out.append(text[position:])
    body = "".join(out)
    body = re.sub(r"\bgl_VertexID\b", "gl_VertexIndex", body)
    return header + re.sub(r"\bgl_InstanceID\b", "gl_InstanceIndex", body)


def _block(members: list[SVariable]) -> str:
    lines = [f"    {member.type} {member.name}{f'[{member.count}]' if member.count else ''};" for member in members]
    return f"layout(std140, binding = 0) uniform {BLOCK} {{\n" + "\n".join(lines) + "\n};\n"


def port(vertex: str, fragment: str) -> SPorted:
    """The pair rewritten for the QRhi device (module docstring); raises ``ValueError`` on what it cannot read."""
    vertex_io, fragment_io = _read(vertex), _read(fragment)
    members = [*_union(vertex_io.uniforms, fragment_io.uniforms), SVariable(CLIP, "vec2")]
    samplers, stages = {}, {}
    for variable in _union(vertex_io.samplers, fragment_io.samplers):
        samplers[variable.name] = SVariable(variable.name, variable.type, variable.count, len(samplers) + 1)
        stages[variable.name] = (
            "v" if any(sampler.name == variable.name for sampler in vertex_io.samplers) else ""
        ) + ("f" if any(sampler.name == variable.name for sampler in fragment_io.samplers) else "")
    attributes = _slots(vertex_io.ins)
    # what the fragment shader reads first: Direct3D links a stage only to a prefix of the one before
    read = [variable.name for _, variable in fragment_io.ins]
    order = sorted(vertex_io.outs, key=lambda qv: read.index(qv[1].name) if qv[1].name in read else len(read))
    varyings = {variable.name: (qualifier, variable) for qualifier, variable in _slots(order)}
    fs_ins = []
    for qualifier, variable in fragment_io.ins:
        if variable.name not in varyings:
            raise ValueError(f"the fragment input {variable.name} has no vertex output")
        fs_ins.append((qualifier, varyings[variable.name][1]))
    outputs = _slots(fragment_io.outs)
    head = "#version 440\n" + _block(members)
    sampler_decl = {
        f"uniform:{sampler.name}": f"layout(binding = {sampler.slot}) uniform {sampler.type} {sampler.name}{f'[{sampler.count}]' if sampler.count else ''};"
        for sampler in samplers.values()
    }
    vs_decls = dict(sampler_decl)
    vs_decls |= {f"in:{variable.name}": _decl(qualifier, "in", variable) for qualifier, variable in attributes}
    vs_decls |= {f"out:{variable.name}": _decl(qualifier, "out", variable) for qualifier, variable in varyings.values()}
    fs_decls = dict(sampler_decl)
    fs_decls |= {f"in:{variable.name}": _decl(qualifier, "in", variable) for qualifier, variable in fs_ins}
    fs_decls |= {f"out:{variable.name}": _decl(qualifier, "out", variable) for qualifier, variable in outputs}
    vtext = _emit(vertex_io, head, vs_decls)
    if not re.search(r"\bvoid\s+main\s*\(\s*\)", vtext):
        raise ValueError("the vertex shader has no main()")
    vtext = re.sub(r"\bvoid\s+main\s*\(\s*\)", "void rhi_main()", vtext, count=1)
    # y flipped where the backend's image is upside down against OpenGL's, depth into [0, 1] where clip depth is
    vtext += (
        "\nvoid main() {\n    rhi_main();\n"
        f"    gl_Position.y *= {CLIP}.x;\n"
        f"    gl_Position.z = mix(gl_Position.z, (gl_Position.z + gl_Position.w) * 0.5, {CLIP}.y);\n}}\n"
    )
    return SPorted(
        vertex=vtext,
        fragment=_emit(fragment_io, head, fs_decls),
        members=members,
        samplers=samplers,
        sampler_stages=stages,
        attributes={variable.name: variable for _, variable in attributes},
        outputs=[variable for _, variable in outputs],
    )
