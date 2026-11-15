"""A compiled shader of a game that compiles with FXC (DXBC, Direct3D 10 and 11) as the viewer draws it, and what an
effect file states about a pass: no Qt, no GPU.

``Stage`` is one compiled shader (the bytecode, its reflection, the vertex inputs under the semantics QRhi feeds, the
texture kinds and sampler states its text declares); ``samplers_of`` reads a shader's sampler states, ``States`` an
effect's render states (the blocks ``render.fxcdraw`` turns into a pipeline). ``FxcSource`` (a game's record:
``FxcGame``) is the base of ``shader2070.Source2070`` and ``shaderforward.ForwardSource``.
docs/rendering/shader-compile.md sections 10 to 12.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import fxeffect, fxlink
from ..infra import buildcache
from ..render import gameshader as gs


@dataclass(frozen=True)
class Sampler:
    """A sampler state the shader's text declares: filter (``ANISOTROPIC``, ``MIN_MAG_MIP_LINEAR``, ...), the
    address modes, the anisotropy, whether it compares (a shadow map's)."""

    filter: str = "MIN_MAG_MIP_LINEAR"
    address_u: str = "WRAP"
    address_v: str = "WRAP"
    address_w: str = "WRAP"
    anisotropy: int = 1
    compare: bool = False


@dataclass
class Stage:
    """One compiled shader (``reflection``: ``infra.dxc.Reflection``; ``inputs``: a vertex shader's inputs; ``types``:
    a texture's declared type by name; ``samplers``: the sampler states by name)."""

    blob: bytes
    entry: str
    reflection: object
    inputs: list = field(default_factory=list)
    types: dict = field(default_factory=dict)
    samplers: dict = field(default_factory=dict)


_SAMPLER_RE = re.compile(r"\bSampler(Comparison)?State\s+(\w+)\s*(?:\{([^}]*)\})?\s*(?::\s*register\([^)]*\))?\s*;")
_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*(\w+)\s*;")


def samplers_of(hlsl: str) -> dict[str, Sampler]:
    """Each ``SamplerState name { Filter = ...; AddressU = ...; }`` of a shader's text by name (a state without a
    block has the defaults)."""
    out = {}
    for m in _SAMPLER_RE.finditer(hlsl):
        values = {k.lower(): v.upper() for k, v in _ASSIGN_RE.findall(m.group(3) or "")}
        aniso = values.get("maxanisotropy", "1")
        out[m.group(2)] = Sampler(
            values.get("filter", "MIN_MAG_MIP_LINEAR"),
            values.get("addressu", "WRAP"),
            values.get("addressv", values.get("addressu", "WRAP")),
            values.get("addressw", values.get("addressu", "WRAP")),
            int(aniso) if aniso.isdigit() else 16,  # an engine macro that did not expand: the highest
            bool(m.group(1)) or "COMPARISON" in values.get("filter", ""),
        )
    return out


def plain_samplers(hlsl: str) -> str:
    """The text with each sampler state's block taken off (``SamplerState s;``): a plain compile needs none, the
    states are ``samplers_of``'s."""
    return _SAMPLER_RE.sub(lambda m: f"Sampler{'Comparison' if m.group(1) else ''}State {m.group(2)};", hlsl)


@dataclass(frozen=True)
class State:
    """The render states one pass sets: back-face culling (``back`` / ``front`` / ``none``), the depth test and
    write, the blend of the first target."""

    cull: str = "back"
    depth_test: bool = True
    depth_write: bool = True
    depth_func: str = "less"
    blend: bool = False
    source: str = "one"
    dest: str = "zero"
    stencil: bool = False
    source_alpha: str = ""  # the alpha's blend factors ("": the colour's)
    dest_alpha: str = ""
    depth_bias: int = 0  # the rasterizer's constant and slope-scaled depth bias (a shadow caster's)
    slope_bias: float = 0.0
    writes: tuple = ()  # per colour target the channels written (D3D's bits: 1 red .. 8 alpha; None or missing: all)


_BLOCK_RE = re.compile(r"\b(RasterizerState|DepthStencilState|BlendState)\s+(\w+)\s*\{([^}]*)\}\s*;")


def _flag(value: str) -> bool:
    return value.strip().upper() in ("TRUE", "1")


def read_states(hlsl: str) -> dict[str, dict[str, str]]:
    """The effect's state blocks by name: lower-cased property -> value (``blendenable[0]`` keeps its index, also
    where the preprocessor spaced it out: ``BLENDENABLE [ 0 ]``)."""
    out: dict[str, dict[str, str]] = {}
    for m in _BLOCK_RE.finditer(hlsl):
        out[m.group(2)] = fxeffect.assignments(re.sub(r"\s*\[\s*(\d+)\s*\]", r"[\1]", m.group(3)))
    return out


_PARAM_RE = re.compile(r"^\s*(?:in\s+)?(\w+)\s+(\w+)\s*:\s*(\w+)\s*$")


def parameter_inputs(hlsl: str, entry: str) -> tuple[str, list]:
    """``gameshader.vertex_inputs`` for an entry that takes its vertex inputs as parameters (``in float3 p :
    SV_Position``, the older effects' way) instead of a structure: renamed to ``TEXCOORD<n>`` in its declaration and
    its definition alike, with the inputs."""
    found_inputs: list[list] = []
    out, at = [], 0
    for m in re.finditer(rf"\b{re.escape(entry)}\s*\(", hlsl):
        close = fxeffect.matching(hlsl, m.end() - 1, "()")
        params, inputs = [], []
        for p in fxeffect.split_args(hlsl[m.end() : close]):
            found = _PARAM_RE.match(p)
            if found and found.group(3) not in gs.SYSTEM_INPUTS:
                inputs.append(gs.Input(found.group(2), found.group(1), found.group(3), len(inputs)))
                p = f"in {found.group(1)} {found.group(2)} : TEXCOORD{len(inputs) - 1}"
            params.append(p)
        found_inputs.append(inputs)
        out += [hlsl[at : m.end()], ", ".join(params)]
        at = close
    return "".join(out) + hlsl[at:], found_inputs[0] if found_inputs else []


def pass_state(blocks: dict[str, dict[str, str]], rasterizer: str, depth: str, blend: str) -> State:
    """The ``State`` of a pass from the named blocks (an absent block has the defaults)."""
    r, d, b = blocks.get(rasterizer, {}), blocks.get(depth, {}), blocks.get(blend, {})
    return State(
        cull=r.get("cullmode", "back").lower(),
        depth_test=_flag(d.get("depthenable", "true")),
        depth_write=d.get("depthwritemask", "all").upper() != "ZERO",
        depth_func=d.get("depthfunc", "less").lower(),
        blend=_flag(b.get("blendenable[0]", b.get("blendenable", "false"))),
        source=b.get("srcblend", "one").lower(),
        dest=b.get("destblend", "zero").lower(),
        stencil=_flag(d.get("stencilenable", "false")),
        writes=write_masks(b),
    )


def write_masks(blend: dict[str, str]) -> tuple:
    """Per colour target the channels a blend block writes (``RENDERTARGETWRITEMASK[n]``, D3D's bits; None: all), up
    to the last target it names (2070's choppy waves keep their foam channel so)."""
    found = {}
    for key, value in blend.items():
        m = re.fullmatch(r"rendertargetwritemask(?:\[(\d+)\])?", key)
        if m and re.fullmatch(r"\s*(0x[0-9a-fA-F]+|\d+)\s*", value):
            found[int(m.group(1) or 0)] = int(value.strip(), 0)
    return tuple(found.get(i) for i in range(max(found) + 1)) if found else ()


_SAMPLER_NAMES = {"Wrap": "WRAP", "Clamp": "CLAMP", "Mirror": "MIRROR", "Border": "BORDER"}


def sampler_named(name: str, default: str = "WRAP") -> Sampler:
    """117's fixed sampler from the words its name carries (``Point``, ``Wrap``, ``Clamp``, ``Mirror``, ``Border``,
    ``Cmp``) as the record an effect block gives; ``default`` is the address mode of a name with none of them. A point
    filter keeps a linear mip filter, as the engine's do."""
    mode = next((m for k, m in _SAMPLER_NAMES.items() if k in name), default)
    filter_ = "MIN_MAG_POINT_MIP_LINEAR" if "Point" in name else "MIN_MAG_MIP_LINEAR"
    return Sampler(filter_, mode, mode, mode, 1, "Cmp" in name)


@dataclass(frozen=True)
class FxcGame:
    """What differs between the games' effect compilers: the build cache's ``namespace``, the compiler ``flags``, the
    ``profile`` of a call, whether a stage's text ``uses`` a sampler and whether a vertex shader may take its inputs
    as parameters (``parameters``, ``parameter_inputs``: 2070 and 1404)."""

    namespace: str
    flags: int
    profile: Callable[[fxeffect.ShaderCall], str]
    uses: Callable[[str, str], bool]
    parameters: bool = False


@dataclass
class Built:
    """An effect preprocessed for one set of defines: its techniques (``effect``), the text the compiler's
    preprocessor made, the program the entries are cut from and the sampler states by name."""

    effect: object
    text: str
    program: fxlink.Program
    samplers: dict


@dataclass(frozen=True)
class Entry:
    """One shader as it goes to the compiler (its text after every rewrite, entry, profile and flags: its whole cache
    key) and what its stage records of that text (vertex inputs, texture types, the sampler states it uses)."""

    hlsl: str
    entry: str
    profile: str
    flags: int
    inputs: tuple = ()
    types: dict = field(default_factory=dict)
    samplers: dict = field(default_factory=dict)


Picked = tuple[fxeffect.ShaderCall, fxeffect.ShaderCall, State | None]


class FxcSource:
    """A game's effects compiled one shader at a time and kept in the build cache; thread-safe. A game's source gives
    ``_preprocess(fx, defines) -> Built`` and ``_pick(built, technique, fx)``, and ``_entry(built, call, fx)`` where
    its text needs more than ``prepare``."""

    def __init__(self, files, compiler, game: FxcGame) -> None:
        self.files, self.compiler, self.record = files, compiler, game
        self._lock = threading.Lock()
        self._effects: dict[tuple, Built] = {}

    def _preprocess(self, fx: str, defines: tuple) -> Built:
        raise NotImplementedError

    def _entry(self, built: Built, call: fxeffect.ShaderCall, fx: str) -> Entry:
        """The shader of ``call`` as it goes to the compiler (a game's source adds its own steps)."""
        return self.prepare(built, call)

    def _pick(self, built: Built, technique: str, fx: str) -> Picked:
        """The vertex and pixel calls of ``technique`` and its states; the game's ``NoPass`` when it has none."""
        raise NotImplementedError

    def _geometry(self, built: Built, technique: str) -> fxeffect.ShaderCall | None:
        """The geometry shader call of ``technique``, compiled after the pixel shader; None (the default) without one."""
        return None

    def effect(self, fx: str, defines: tuple) -> Built:
        """The preprocessed effect, made once per file and defines."""
        with self._lock:
            if (fx, defines) not in self._effects:
                self._effects[fx, defines] = self._preprocess(fx, defines)
            return self._effects[fx, defines]

    def prepare(
        self,
        built: Built,
        call: fxeffect.ShaderCall,
        *,
        profile: str | None = None,
        text: tuple[str, str] | None = None,
        samplers: dict | None = None,
    ) -> Entry:
        """One entry as the compiler gets it: cut from the program (``text``: the source and entry when the game's
        source makes its own), the vertex inputs renamed, ``profile`` the game's for the call unless given;
        ``samplers``: the states to pick the used ones from (the effect's by default)."""
        hlsl, entry = text or built.program.entry_source(call)
        inputs = []
        if call.stage == "vs":
            hlsl, inputs = gs.vertex_inputs(hlsl, entry)
            if not inputs and self.record.parameters:
                hlsl, inputs = parameter_inputs(hlsl, entry)
        states = built.samplers if samplers is None else samplers
        used = {k: v for k, v in states.items() if self.record.uses(hlsl, k)}
        return Entry(hlsl, entry, profile or self.record.profile(call), self.record.flags, tuple(inputs),
                     gs.declared_types(hlsl), used)  # fmt: skip

    def compiled(self, entry: Entry, fx: str) -> Stage:
        """``entry`` compiled, its bytecode kept in the build cache under exactly what the compiler is handed: a
        change to any rewrite of the text compiles it again by itself, with no version to bump."""
        key = buildcache.digest(self.compiler.version(), entry.profile, entry.flags, entry.entry, entry.hlsl)
        blob = buildcache.get(self.record.namespace, key)
        if blob is None:
            with self._lock:
                done = self.compiler.compile(entry.hlsl, entry.entry, entry.profile, flags=entry.flags, name=fx)
            if not done.ok:
                raise RuntimeError(f"{entry.entry} of {fx}: {done.messages[:2000]}")
            blob = done.object
            buildcache.put(self.record.namespace, key, blob)
        with self._lock:
            reflection = self.compiler.reflect(blob)
        return Stage(blob, entry.entry, reflection, list(entry.inputs), dict(entry.types), dict(entry.samplers))

    def effect_pass(self, fx: str, defines: tuple, technique: str) -> tuple[list[Stage], State | None]:
        """The vertex and pixel ``Stage`` (and the geometry stage where ``_geometry`` gives one) and the states (None
        where the game reads none) of ``technique`` of ``fx``, each shader compiled once and kept in the build cache."""
        built = self.effect(fx, defines)
        vs, ps, state = self._pick(built, technique, fx)
        geometry = self._geometry(built, technique)
        calls = (vs, ps) if geometry is None else (vs, ps, geometry)
        return [self.compiled(self._entry(built, call, fx), fx) for call in calls], state
