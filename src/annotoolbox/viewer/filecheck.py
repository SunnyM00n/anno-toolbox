"""Every model config of an installed game looked up the way the viewer draws it (``modelscene.build``; the island
editor shares its rules through ``models.fileresolve``): the names that find no file counted by kind and reason, and
every texture found checked for decoding (``fileresolve.check_decode``).

``check(tree, options)`` is the entry and ``Report.lines`` the text the ``viewer-files`` command prints; a sample
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
class CheckOptions:
    sample: int = 0  # configs checked, picked evenly over the sorted list (0: all)
    decode: bool = True  # check every texture found for decoding
    examples: int = 4  # names listed per kind and reason
    placed: tuple[str, ...] = ()  # configs an island sample places (checked as well, counted apart)


@dataclass
class Report:
    """What a files check found: configs checked and unreadable, references by kind, the failures by (kind, reason) with
    examples, the placeholders and switched-off slots, the textures checked and the decode failures by reason."""

    game: str
    configs: int = 0
    replaced: int = 0  # data/ configs the game reads through their shared/ copy (checked as themselves too)
    placed: int = 0
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    refs: Counter = field(default_factory=Counter)
    failed: Counter = field(default_factory=Counter)
    placeholders: Counter = field(default_factory=Counter)
    off: int = 0
    examples: dict = field(default_factory=dict)
    textures: int = 0
    decode_failed: Counter = field(default_factory=Counter)
    decode_examples: dict = field(default_factory=dict)
    formats: Counter = field(default_factory=Counter)

    @property
    def unresolved(self) -> int:
        return sum(self.failed.values())

    def add(self, ref: fileresolve.Ref, keep: int, elsewhere: str = "") -> None:
        self.refs[ref.kind] += 1
        if ref.off:
            self.off += 1
        elif ref.reason in fileresolve.EMPTY_REASONS and not ref.path:
            self.placeholders[ref.kind] += 1
        elif ref.failed:
            self.failed[ref.kind, ref.reason] += 1
            shown = self.examples.setdefault((ref.kind, ref.reason), [])
            if len(shown) < keep:
                shown.append(f"{ref.owner}: {ref.name}" + (f" (ships as {elsewhere})" if elsewhere else ""))

    def lines(self) -> list[str]:
        """The report as text: totals, then each failure with its examples, then the decode failures."""
        out = [
            f"{self.game}: {self.configs} configs checked ({self.replaced} data/ configs the game reads through their "
            f"shared/ copy; {self.placed} placed on the island sample), {len(self.unreadable)} unreadable",
            "references: " + ", ".join(f"{k} {n}" for k, n in sorted(self.refs.items())),
            f"unresolved: {self.unresolved}; placeholders (drawn without a map, as the game draws its default): "
            f"{sum(self.placeholders.values())}; slots switched off: {self.off}",
        ]
        for (kind, reason), n in self.failed.most_common():
            out.append(f"  {kind}, {reason}: {n}")
            out += [f"    {e}" for e in self.examples.get((kind, reason), [])]
        out += [f"  unreadable: {cfg}: {why}" for cfg, why in self.unreadable[:8]]
        if self.textures:
            out.append(f"textures checked: {self.textures}; decode failures: {sum(self.decode_failed.values())}")
            out.append("  formats: " + ", ".join(f"{k} {n}" for k, n in self.formats.most_common()))
            for reason, n in self.decode_failed.most_common():
                out.append(f"  {reason}: {n}")
                out += [f"    {e}" for e in self.decode_examples.get(reason, [])]
        return out


def model_configs(tree) -> list[str]:
    """The model configs a game ships, sorted."""
    return configtree.model_configs(tree.paths, tree.game)


def _picked(paths: list[str], sample: int) -> list[str]:
    if not sample or sample >= len(paths):
        return paths
    step = len(paths) / sample
    return [paths[int(k * step)] for k in range(sample)]


def check(tree, options: CheckOptions | None = None, progress: Callable[[int, int], None] | None = None) -> Report:
    """Look up every name the configs of ``tree`` (a ``sources.gametree.GameTree``) write, as the viewer does."""
    options = options or CheckOptions()
    report = Report(tree.game)
    resolve = fileresolve.Resolver(tree, tree.game, keep=False)
    configs = _picked(model_configs(tree), options.sample)
    suffixes, listed = configtree.config_suffixes(tree.game), set(configs)
    placed = [p for p in dict.fromkeys(normalize(c) for c in options.placed) if p.endswith(suffixes)]
    placed = [p for p in placed if p not in listed]  # props (.prp) are the island editor's, checked there
    found: set[str] = set()
    effects: set[str] = set()
    todo = [*configs, *placed]
    for k, cfg in enumerate(todo):
        if progress is not None and k % 500 == 0:
            progress(k, len(todo))
        _check_one(tree, cfg, report, options, (found, effects))
    for path in sorted(effects):
        _check_effect(tree, path, report, resolve, options, found)
    report.configs, report.placed = len(configs), len(placed)
    report.replaced = sum(
        1 for c in configs if configtree.shared_config(c) and tree.exists(configtree.shared_config(c))
    )
    if options.decode:
        check_textures(tree, sorted(found), report, options.examples)
    return report


def _check_one(tree, cfg: str, report: Report, options: CheckOptions, seen: tuple[set, set]) -> None:
    """One config as the viewer reads it (``shared`` off: each file as itself), its lookups counted; the textures
    and effects found go into ``seen``."""
    if not tree.exists(cfg):
        resolve = fileresolve.Resolver(tree, tree.game)
        report.add(fileresolve.Ref("config", cfg, reason=resolve.why([(cfg, "")]), owner="(placed on an island)"), 99)
        return
    try:
        scene = modelscene.build(tree, cfg, tree.game, modelscene.Options(shared=False, follow=False))
    except Exception as exc:  # a config the readers do not take: counted, the files check goes on
        report.unreadable.append((cfg, f"{type(exc).__name__}: {exc}"))
        return
    for ref in scene.resolve.refs:
        elsewhere = scene.resolve.elsewhere(ref) if ref.reason == fileresolve.ELSEWHERE else ""
        report.add(ref, options.examples, elsewhere)
        if ref.path and ref.kind in ("texture", "effect"):
            seen[0 if ref.kind == "texture" else 1].add(ref.path)


def effect_textures(data: bytes, path: str) -> list[str]:
    """The texture names a particle file writes: a 117 ``.rdp``'s header and materials, a 2070 ``.efc``'s emitters."""
    if path.endswith(".rdp"):
        from ..formats.rdp117 import parse_rdp

        r = parse_rdp(data)
        return [t for t in (r.texture, *(m.movie_texture for m in r.materials)) if t]
    from ..models.particles2070 import parse_efc

    return [e.texture for e in parse_efc(data.decode("utf-8", "replace")) if e.texture]


def _check_effect(tree, path: str, report: Report, resolve, options: CheckOptions, found: set[str]) -> None:
    """The textures of one effect file, looked up as the viewer's effects find them (``ModelFiles.effect``)."""
    try:
        names = effect_textures(tree.read(path), path)
    except Exception as exc:  # an effect the readers do not take: counted, the files check goes on
        report.unreadable.append((path, f"{type(exc).__name__}: {exc}"))
        return
    for name in dict.fromkeys(names):
        ref = resolve.texture_ref(name, "effect", path, kind="effect texture")
        report.add(ref, options.examples, resolve.elsewhere(ref) if ref.reason == fileresolve.ELSEWHERE else "")
        if ref.path:
            found.add(ref.path)


def check_textures(tree, paths: Iterable[str], report: Report, keep: int = 4) -> None:
    """Every texture file found, checked for decoding (``fileresolve.check_decode``), failures by reason."""
    from ..models.ddsdecode import DdsFile, UnsupportedDds

    for path in paths:
        data = tree.read(path)
        report.textures += 1
        try:
            info = DdsFile(data).info if data[:4] == b"DDS " else None
            report.formats[(info.format + (" sRGB" if info.srgb else "") + (" cube" if info.faces == 6 else ""))
                           if info else path.rsplit(".", 1)[-1]] += 1  # fmt: skip
        except UnsupportedDds:
            pass
        why = fileresolve.check_decode(data)
        if why:
            report.decode_failed[why.split(" (")[0]] += 1
            shown = report.decode_examples.setdefault(why.split(" (")[0], [])
            if len(shown) < keep:
                shown.append(path)
