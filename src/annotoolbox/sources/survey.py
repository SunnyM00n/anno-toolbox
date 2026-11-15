"""Asset survey: follow every asset to its files and report coverage."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..models.cfg2070 import Cfg, parse_cfg_bytes
from .assetdb import Asset, AssetDatabase
from .loca2070 import load_texts
from .overlay import SourceTree, normalize
from .toolone import load_database

COMPANION_EXTENSIONS = (".ifo", ".fc")
MAX_CFG_DEPTH = 8

# Developer leftovers referenced by shipped configs; the engine substitutes them.
PLACEHOLDER_FILES = {
    "tools/bobnet/data/default_diff.png",
}


@dataclass
class FileRef:
    kind: str
    requested: str
    resolved: str | None
    archive: str | None = None
    placeholder: bool = False
    via: str | None = None  # nested cfg this reference came from

    @property
    def found(self) -> bool:
        return self.resolved is not None


@dataclass
class AssetSurvey:
    guid: int | None
    name: str
    display_name: str
    template: str
    group: str
    source: str
    base_guid: int | None = None
    cfgs: list[str] = field(default_factory=list)
    refs: list[FileRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    vertex_formats: list[str] = field(default_factory=list)
    shader_indices: list[int] = field(default_factory=list)

    @property
    def missing(self) -> list[FileRef]:
        return [r for r in self.refs if not r.found and not r.placeholder]

    @property
    def placeholders(self) -> list[FileRef]:
        return [r for r in self.refs if r.placeholder]

    @property
    def status(self) -> str:
        if not self.cfgs:
            return "no-model"
        if self.errors:
            return "error"
        if self.missing:
            return "partial"
        return "complete"


@dataclass
class SurveyResult:
    source_root: str
    language: str
    archives: list[str]
    ignored_folders: list[str]
    indexed_files: int
    asset_sources: list[str]
    assets: list[AssetSurvey]
    template_count: int
    property_count: int
    text_count: int
    problems: list[str] = field(default_factory=list)

    def status_counts(self) -> Counter:
        return Counter(a.status for a in self.assets)

    def per_template(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for a in self.assets:
            out.setdefault(a.template, Counter())[a.status] += 1
        return dict(sorted(out.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])))

    def missing_files(self) -> Counter:
        c: Counter = Counter()
        for a in self.assets:
            for r in a.missing:
                c[f"{r.kind} {r.requested}"] += 1
        return c

    def kind_counts(self) -> Counter:
        c: Counter = Counter()
        for a in self.assets:
            for r in a.refs:
                c[r.kind.split(":")[0]] += 1
        return c

    def vertex_format_counts(self) -> Counter:
        return Counter(vf for a in self.assets for vf in a.vertex_formats)

    def shader_counts(self) -> Counter:
        return Counter(s for a in self.assets for s in a.shader_indices)


class _CfgCache:
    def __init__(self, tree: SourceTree) -> None:
        self.tree = tree
        self._cache: dict[str, Cfg | Exception] = {}

    def get(self, rel: str) -> Cfg | Exception | None:
        key = normalize(rel)
        if key in self._cache:
            return self._cache[key]
        if not self.tree.exists(key):
            return None
        try:
            cfg: Cfg | Exception = parse_cfg_bytes(self.tree.read_bytes(key), key)
        except (ET.ParseError, ValueError) as exc:
            cfg = exc
        self._cache[key] = cfg
        return cfg


def _ref(tree: SourceTree, kind: str, requested: str, texture: bool = False, via: str | None = None) -> FileRef:
    key = normalize(requested)
    found = tree.texture_key(key) if texture else key
    resolved = tree.locate(found) if found else None
    return FileRef(
        kind=kind,
        requested=key,
        resolved=resolved,
        archive=tree.archive_of(found) if resolved and found else None,
        placeholder=key in PLACEHOLDER_FILES,
        via=via,
    )


def survey_asset(tree: SourceTree, asset: Asset, cache: _CfgCache, texts: dict[int, str]) -> AssetSurvey:
    s = AssetSurvey(
        guid=asset.guid,
        name=asset.name,
        display_name=texts.get(asset.guid, "") if asset.guid is not None else "",
        template=asset.template,
        group=asset.group_path,
        source=asset.source,
        base_guid=asset.base_guid,
    )
    visited: set[str] = set()

    def follow(cfg_path: str, depth: int, via: str | None) -> None:
        key = normalize(cfg_path)
        if key in visited:
            return
        visited.add(key)
        s.refs.append(_ref(tree, "cfg", key, via=via))
        cfg = cache.get(key)
        if cfg is None:
            return
        if isinstance(cfg, Exception):
            s.errors.append(f"{key}: {cfg}")
            return
        if depth == 0:
            stem = key.rsplit(".", 1)[0]
            for ext in COMPANION_EXTENSIONS:
                companion = stem + ext
                if tree.exists(companion):
                    s.refs.append(_ref(tree, "companion" + ext, companion))
        for kind, path in cfg.referenced_files():
            if kind == "cfg":
                if depth < MAX_CFG_DEPTH:
                    follow(path, depth + 1, key)
                else:
                    s.errors.append(f"{key}: nested cfg depth limit reached at {normalize(path)}")
                continue
            s.refs.append(_ref(tree, kind, path, texture=kind.startswith(("texture", "decal")), via=key))
        s.vertex_formats.extend(sorted(cfg.vertex_formats()))
        s.shader_indices.extend(sorted(cfg.shader_indices()))

    for cfg_path in asset.variation_files():
        s.cfgs.append(normalize(cfg_path))
        follow(cfg_path, 0, None)
    return s


def run_survey(tree: SourceTree, db: AssetDatabase | None = None) -> SurveyResult:
    db = db or load_database(tree)
    texts = load_texts(tree)
    cache = _CfgCache(tree)
    surveys = [survey_asset(tree, a, cache, texts) for a in db.assets]
    return SurveyResult(
        source_root=str(tree.root),
        language=tree.language,
        archives=[a.name for a in tree.archives],
        ignored_folders=list(tree.ignored_folders),
        indexed_files=len(tree),
        asset_sources=list(db.sources),
        assets=surveys,
        template_count=len(db.templates),
        property_count=len(db.properties),
        text_count=len(texts),
        problems=list(db.problems),
    )


# Reports


def _md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def render_summary(result: SurveyResult) -> str:
    status = result.status_counts()
    missing = result.missing_files().most_common(40)
    parts = [
        "# Anno 2070 asset survey",
        "",
        f"Source: `{result.source_root}` (language `{result.language}`)",
        "",
        f"Archives in override order: {', '.join(result.archives)}",
        "",
    ]
    if result.ignored_folders:
        parts += [f"Ignored folders (not archive names): {', '.join(result.ignored_folders)}", ""]
    parts += [
        "## Totals",
        "",
        _md_table(
            ["metric", "count"],
            [
                ["indexed files", result.indexed_files],
                ["asset files", len(result.asset_sources)],
                ["assets", len(result.assets)],
                ["templates", result.template_count],
                ["components (properties)", result.property_count],
                ["text entries", result.text_count],
                ["assets complete", status.get("complete", 0)],
                ["assets partial (missing files)", status.get("partial", 0)],
                ["assets with cfg errors", status.get("error", 0)],
                ["assets without model", status.get("no-model", 0)],
                ["assets inheriting via BaseAssetGUID", sum(1 for a in result.assets if a.base_guid is not None)],
                ["assets referencing placeholder files", sum(1 for a in result.assets if a.placeholders)],
            ],
        ),
        "",
        "Asset files: " + ", ".join(f"`{s}`" for s in result.asset_sources),
        "",
        "## Per template",
        "",
        _md_table(
            ["template", "assets", "complete", "partial", "error", "no-model"],
            [
                [t, sum(c.values()), c.get("complete", 0), c.get("partial", 0), c.get("error", 0), c.get("no-model", 0)]
                for t, c in result.per_template().items()
            ],
        ),
        "",
        "## Referenced file kinds",
        "",
        _md_table(["kind", "references"], [[k, n] for k, n in result.kind_counts().most_common()]),
        "",
        "## Vertex formats (per asset occurrence)",
        "",
        _md_table(["format", "count"], [[k, n] for k, n in result.vertex_format_counts().most_common()]),
        "",
        "## Shader indices (per asset occurrence)",
        "",
        _md_table(["shader", "count"], [[k, n] for k, n in sorted(result.shader_counts().items())]),
        "",
        "## Most common missing files",
        "",
        _md_table(["file", "assets"], [[k, n] for k, n in missing]) if missing else "none",
        "",
    ]
    errors = [(a.guid, e) for a in result.assets for e in a.errors]
    if errors:
        parts += ["## Config parse errors", ""] + [f"- {g}: {e}" for g, e in errors[:100]] + [""]
    if result.problems:
        parts += ["## Database problems", ""] + [f"- {p}" for p in result.problems[:100]] + [""]
    return "\n".join(parts)


def write_reports(result: SurveyResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = out / "summary.md"
    summary.write_text(render_summary(result), encoding="utf-8", newline="\n")

    assets_csv = out / "assets.csv"
    with assets_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            [
                "guid",
                "name",
                "display_name",
                "template",
                "group",
                "source",
                "status",
                "base_guid",
                "cfgs",
                "refs",
                "missing",
                "vertex_formats",
                "shaders",
            ]
        )
        for a in result.assets:
            w.writerow(
                [
                    a.guid,
                    a.name,
                    a.display_name,
                    a.template,
                    a.group,
                    a.source,
                    a.status,
                    a.base_guid if a.base_guid is not None else "",
                    ";".join(a.cfgs),
                    len(a.refs),
                    len(a.missing),
                    ";".join(sorted(set(a.vertex_formats))),
                    ";".join(str(s) for s in sorted(set(a.shader_indices))),
                ]
            )

    files_json = out / "files.json"
    payload = [
        {
            "guid": a.guid,
            "name": a.name,
            "template": a.template,
            "status": a.status,
            "base_guid": a.base_guid,
            "cfgs": a.cfgs,
            "refs": [asdict(r) for r in a.refs],
            "errors": a.errors,
        }
        for a in result.assets
    ]
    files_json.write_text(json.dumps(payload, indent=1), encoding="utf-8", newline="\n")
    return {"summary": summary, "assets": assets_csv, "files": files_json}
