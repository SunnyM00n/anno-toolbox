"""Every model config of an installed game looked up the way the viewer draws it (``modelscene.build``; the island
editor shares its rules through ``models.fileresolve``): the names that find no file counted by kind and reason, and
every texture found checked for decoding (``fileresolve.check_decode``).

``check(tree, options)`` is the entry and ``CReport.lines`` the text the ``viewer-files`` command prints; a sample
of islands adds the configs their objects place (``placed``). docs/viewer.md "How files are found".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..models import configtree, fileresolve
from ..sources.overlay import normalize
from . import modelscene


@dataclass(frozen=True)
class SCheckOptions:
    sample: int = 0  # configs checked, picked evenly over the sorted list (0: all)
    decode: bool = True  # check every texture found for decoding
    examples: int = 4  # names listed per kind and reason
    placed: tuple[str, ...] = ()  # configs an island sample places (checked as well, counted apart)


@dataclass
class CReport:
    """What a files check found: configs checked and unreadable, references by kind, the failures by (kind, reason) with
    examples, the placeholders and switched-off slots, the textures checked and the decode failures by reason."""

    m_game: str
    m_configs: int = 0
    m_replaced: int = 0  # data/ configs the game reads through their shared/ copy (checked as themselves too)
    m_placed: int = 0
    m_unreadable: list[tuple[str, str]] = field(default_factory=list)
    m_refs: Counter = field(default_factory=Counter)
    m_failed: Counter = field(default_factory=Counter)
    m_placeholders: Counter = field(default_factory=Counter)
    m_off: int = 0
    m_examples: dict = field(default_factory=dict)
    m_textures: int = 0
    m_decode_failed: Counter = field(default_factory=Counter)
    m_decode_examples: dict = field(default_factory=dict)
    m_formats: Counter = field(default_factory=Counter)

    @property
    def unresolved(self) -> int:
        return sum(self.m_failed.values())

    def add(self, ref: fileresolve.SRef, keep: int, elsewhere: str = "") -> None:
        self.m_refs[ref.kind] += 1
        if ref.is_off:
            self.m_off += 1
        elif ref.reason in fileresolve.EMPTY_REASONS and not ref.path:
            self.m_placeholders[ref.kind] += 1
        elif ref.failed:
            self.m_failed[ref.kind, ref.reason] += 1
            shown = self.m_examples.setdefault((ref.kind, ref.reason), [])
            if len(shown) < keep:
                shown.append(f"{ref.owner}: {ref.name}" + (f" (ships as {elsewhere})" if elsewhere else ""))

    def lines(self) -> list[str]:
        """The report as text: totals, then each failure with its examples, then the decode failures."""
        out = [
            f"{self.m_game}: {self.m_configs} configs checked ({self.m_replaced} data/ configs the game reads through their "
            f"shared/ copy; {self.m_placed} placed on the island sample), {len(self.m_unreadable)} unreadable",
            "references: " + ", ".join(f"{k} {count}" for k, count in sorted(self.m_refs.items())),
            f"unresolved: {self.unresolved}; placeholders (drawn without a map, as the game draws its default): "
            f"{sum(self.m_placeholders.values())}; slots switched off: {self.m_off}",
        ]
        for (kind, reason), count in self.m_failed.most_common():
            out.append(f"  {kind}, {reason}: {count}")
            out += [f"    {example}" for example in self.m_examples.get((kind, reason), [])]
        out += [f"  unreadable: {cfg}: {why}" for cfg, why in self.m_unreadable[:8]]
        if self.m_textures:
            out.append(f"textures checked: {self.m_textures}; decode failures: {sum(self.m_decode_failed.values())}")
            out.append("  formats: " + ", ".join(f"{k} {count}" for k, count in self.m_formats.most_common()))
            for reason, count in self.m_decode_failed.most_common():
                out.append(f"  {reason}: {count}")
                out += [f"    {example}" for example in self.m_decode_examples.get(reason, [])]
        return out


def model_configs(tree) -> list[str]:
    """The model configs a game ships, sorted."""
    return configtree.model_configs(tree.m_paths, tree.m_game)


def _picked(paths: list[str], sample: int) -> list[str]:
    if not sample or sample >= len(paths):
        return paths
    step = len(paths) / sample
    return [paths[int(k * step)] for k in range(sample)]


def check(tree, options: SCheckOptions | None = None, progress: Callable[[int, int], None] | None = None) -> CReport:
    """Look up every name the configs of ``tree`` (a ``sources.gametree.CGameTree``) write, as the viewer does."""
    options = options or SCheckOptions()
    report = CReport(tree.m_game)
    resolve = fileresolve.CResolver(tree, tree.m_game, m_keep=False)
    configs = _picked(model_configs(tree), options.sample)
    suffixes, listed = configtree.config_suffixes(tree.m_game), set(configs)
    placed = [
        placed_path
        for placed_path in dict.fromkeys(normalize(config) for config in options.placed)
        if placed_path.endswith(suffixes)
    ]
    placed = [
        placed_path for placed_path in placed if placed_path not in listed
    ]  # props (.prp) are the island editor's, checked there
    found: set[str] = set()
    effects: set[str] = set()
    todo = [*configs, *placed]
    for k, cfg in enumerate(todo):
        if progress is not None and k % 500 == 0:
            progress(k, len(todo))
        _check_one(tree, cfg, report, options, (found, effects))
    for path in sorted(effects):
        _check_effect(tree, path, report, resolve, options, found)
    report.m_configs, report.m_placed = len(configs), len(placed)
    report.m_replaced = sum(
        1 for config in configs if configtree.shared_config(config) and tree.exists(configtree.shared_config(config))
    )
    if options.decode:
        check_textures(tree, sorted(found), report, options.examples)
    return report


def _check_one(tree, cfg: str, report: CReport, options: SCheckOptions, seen: tuple[set, set]) -> None:
    """One config as the viewer reads it (``shared`` off: each file as itself), its lookups counted; the textures
    and effects found go into ``seen``."""
    if not tree.exists(cfg):
        resolve = fileresolve.CResolver(tree, tree.m_game)
        report.add(fileresolve.SRef("config", cfg, reason=resolve.why([(cfg, "")]), owner="(placed on an island)"), 99)
        return
    try:
        scene = modelscene.build(tree, cfg, tree.m_game, modelscene.SOptions(shared=False, follow=False))
    except Exception as exc:  # a config the readers do not take: counted, the files check goes on
        report.m_unreadable.append((cfg, f"{type(exc).__name__}: {exc}"))
        return
    for ref in scene.m_resolve.m_refs:
        elsewhere = scene.m_resolve.elsewhere(ref) if ref.reason == fileresolve.ELSEWHERE else ""
        report.add(ref, options.examples, elsewhere)
        if ref.path and ref.kind in ("texture", "effect"):
            seen[0 if ref.kind == "texture" else 1].add(ref.path)


def effect_textures(data: bytes, path: str) -> list[str]:
    """The texture names a particle file writes: a 117 ``.rdp``'s header and materials, a 2070 ``.efc``'s emitters."""
    if path.endswith(".rdp"):
        from ..formats.rdp117 import parse_rdp

        particle_file = parse_rdp(data)
        return [
            texture
            for texture in (particle_file.texture, *(material.movie_texture for material in particle_file.materials))
            if texture
        ]
    from ..models.particles2070 import parse_efc

    return [effect.texture for effect in parse_efc(data.decode("utf-8", "replace")) if effect.texture]


def _check_effect(tree, path: str, report: CReport, resolve, options: SCheckOptions, found: set[str]) -> None:
    """The textures of one effect file, looked up as the viewer's effects find them (``ModelFiles.effect``)."""
    try:
        names = effect_textures(tree.read(path), path)
    except Exception as exc:  # an effect the readers do not take: counted, the files check goes on
        report.m_unreadable.append((path, f"{type(exc).__name__}: {exc}"))
        return
    for name in dict.fromkeys(names):
        ref = resolve.texture_reference(name, "effect", path, kind="effect texture")
        report.add(ref, options.examples, resolve.elsewhere(ref) if ref.reason == fileresolve.ELSEWHERE else "")
        if ref.path:
            found.add(ref.path)


def check_textures(tree, paths: Iterable[str], report: CReport, keep: int = 4) -> None:
    """Every texture file found, checked for decoding (``fileresolve.check_decode``), failures by reason."""
    from ..models.ddsdecode import CDdsFile, CUnsupportedDds

    for path in paths:
        data = tree.read(path)
        report.m_textures += 1
        try:
            dds_info = CDdsFile(data).m_info if data[:4] == b"DDS " else None
            report.m_formats[(dds_info.format + (" sRGB" if dds_info.srgb else "") + (" cube" if dds_info.faces == 6 else ""))
                           if dds_info else path.rsplit(".", 1)[-1]] += 1  # fmt: skip
        except CUnsupportedDds:
            pass
        why = fileresolve.check_decode(data)
        if why:
            report.m_decode_failed[why.split(" (")[0]] += 1
            shown = report.m_decode_examples.setdefault(why.split(" (")[0], [])
            if len(shown) < keep:
                shown.append(path)
