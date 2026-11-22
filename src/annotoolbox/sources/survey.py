"""Asset survey: follow every asset to its files and report coverage."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..models.cfg2070 import SCfg, parse_cfg_bytes
from .assetdb import CAssetDatabase, SAsset
from .loca2070 import load_texts
from .overlay import CSourceTree, normalize
from .toolone import load_database

COMPANION_EXTENSIONS = (".ifo", ".fc")
MAX_CFG_DEPTH = 8

# Developer leftovers referenced by shipped configs; the engine substitutes them.
PLACEHOLDER_FILES = {
    "tools/bobnet/data/default_diff.png",
}


@dataclass
class SFileRef:
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
class SAssetSurvey:
    guid: int | None
    name: str
    display_name: str
    template: str
    group: str
    source: str
    base_asset: int | None = None
    cfgs: list[str] = field(default_factory=list)
    refs: list[SFileRef] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    vertex_formats: list[str] = field(default_factory=list)
    shader_indices: list[int] = field(default_factory=list)

    @property
    def missing(self) -> list[SFileRef]:
        return [ref for ref in self.refs if not ref.found and not ref.placeholder]

    @property
    def placeholders(self) -> list[SFileRef]:
        return [ref for ref in self.refs if ref.placeholder]

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
class SSurveyResult:
    source_root: str
    language: str
    archives: list[str]
    ignored_folders: list[str]
    indexed_files: int
    asset_sources: list[str]
    assets: list[SAssetSurvey]
    template_count: int
    property_count: int
    text_count: int
    problems: list[str] = field(default_factory=list)

    def status_counts(self) -> Counter:
        return Counter(asset.status for asset in self.assets)

    def per_template(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for asset in self.assets:
            out.setdefault(asset.template, Counter())[asset.status] += 1
        return dict(sorted(out.items(), key=lambda item: (-sum(item[1].values()), item[0])))

    def missing_files(self) -> Counter:
        counter: Counter = Counter()
        for asset in self.assets:
            for ref in asset.missing:
                counter[f"{ref.kind} {ref.requested}"] += 1
        return counter

    def kind_counts(self) -> Counter:
        counter: Counter = Counter()
        for asset in self.assets:
            for ref in asset.refs:
                counter[ref.kind.split(":")[0]] += 1
        return counter

    def vertex_format_counts(self) -> Counter:
        return Counter(vertex_format for asset in self.assets for vertex_format in asset.vertex_formats)

    def shader_counts(self) -> Counter:
        return Counter(shader_index for asset in self.assets for shader_index in asset.shader_indices)


class _CCfgCache:
    def __init__(self, tree: CSourceTree) -> None:
        self.tree = tree
        self._cache: dict[str, SCfg | Exception] = {}

    def get(self, rel: str) -> SCfg | Exception | None:
        key = normalize(rel)
        if key in self._cache:
            return self._cache[key]
        if not self.tree.exists(key):
            return None
        try:
            cfg: SCfg | Exception = parse_cfg_bytes(self.tree.read_bytes(key), key)
        except (ET.ParseError, ValueError) as error:
            cfg = error
        self._cache[key] = cfg
        return cfg


def _ref(tree: CSourceTree, kind: str, requested: str, texture: bool = False, via: str | None = None) -> SFileRef:
    key = normalize(requested)
    found = tree.texture_key(key) if texture else key
    resolved = tree.locate(found) if found else None
    return SFileRef(
        kind=kind,
        requested=key,
        resolved=resolved,
        archive=tree.archive_of(found) if resolved and found else None,
        placeholder=key in PLACEHOLDER_FILES,
        via=via,
    )


def survey_asset(tree: CSourceTree, asset: SAsset, cache: _CCfgCache, texts: dict[int, str]) -> SAssetSurvey:
    survey = SAssetSurvey(
        guid=asset.guid,
        name=asset.name,
        display_name=texts.get(asset.guid, "") if asset.guid is not None else "",
        template=asset.template,
        group=asset.group_path,
        source=asset.source,
        base_asset=asset.base_asset,
    )
    visited: set[str] = set()

    def follow(cfg_path: str, depth: int, via: str | None) -> None:
        key = normalize(cfg_path)
        if key in visited:
            return
        visited.add(key)
        survey.refs.append(_ref(tree, "cfg", key, via=via))
        cfg = cache.get(key)
        if cfg is None:
            return
        if isinstance(cfg, Exception):
            survey.errors.append(f"{key}: {cfg}")
            return
        if depth == 0:
            stem = key.rsplit(".", 1)[0]
            for extension in COMPANION_EXTENSIONS:
                companion = stem + extension
                if tree.exists(companion):
                    survey.refs.append(_ref(tree, "companion" + extension, companion))
        for kind, path in cfg.referenced_files():
            if kind == "cfg":
                if depth < MAX_CFG_DEPTH:
                    follow(path, depth + 1, key)
                else:
                    survey.errors.append(f"{key}: nested cfg depth limit reached at {normalize(path)}")
                continue
            survey.refs.append(_ref(tree, kind, path, texture=kind.startswith(("texture", "decal")), via=key))
        survey.vertex_formats.extend(sorted(cfg.vertex_formats()))
        survey.shader_indices.extend(sorted(cfg.shader_indices()))

    for cfg_path in asset.variation_files():
        survey.cfgs.append(normalize(cfg_path))
        follow(cfg_path, 0, None)
    return survey


def run_survey(tree: CSourceTree, db: CAssetDatabase | None = None) -> SSurveyResult:
    db = db or load_database(tree)
    texts = load_texts(tree)
    cache = _CCfgCache(tree)
    surveys = [survey_asset(tree, asset, cache, texts) for asset in db.m_assets]
    return SSurveyResult(
        source_root=str(tree.m_root),
        language=tree.m_language,
        archives=[asset.name for asset in tree.m_archives],
        ignored_folders=list(tree.m_ignored_folders),
        indexed_files=len(tree),
        asset_sources=list(db.m_sources),
        assets=surveys,
        template_count=len(db.m_templates),
        property_count=len(db.m_properties),
        text_count=len(texts),
        problems=list(db.m_problems),
    )


# Reports


def _md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_summary(result: SSurveyResult) -> str:
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
                [
                    "assets inheriting via BaseAssetGUID",
                    sum(1 for asset in result.assets if asset.base_asset is not None),
                ],
                ["assets referencing placeholder files", sum(1 for asset in result.assets if asset.placeholders)],
            ],
        ),
        "",
        "Asset files: " + ", ".join(f"`{source}`" for source in result.asset_sources),
        "",
        "## Per template",
        "",
        _md_table(
            ["template", "assets", "complete", "partial", "error", "no-model"],
            [
                [
                    template,
                    sum(counts.values()),
                    counts.get("complete", 0),
                    counts.get("partial", 0),
                    counts.get("error", 0),
                    counts.get("no-model", 0),
                ]
                for template, counts in result.per_template().items()
            ],
        ),
        "",
        "## Referenced file kinds",
        "",
        _md_table(["kind", "references"], [[k, count] for k, count in result.kind_counts().most_common()]),
        "",
        "## Vertex formats (per asset occurrence)",
        "",
        _md_table(["format", "count"], [[k, count] for k, count in result.vertex_format_counts().most_common()]),
        "",
        "## Shader indices (per asset occurrence)",
        "",
        _md_table(["shader", "count"], [[k, count] for k, count in sorted(result.shader_counts().items())]),
        "",
        "## Most common missing files",
        "",
        _md_table(["file", "assets"], [[k, count] for k, count in missing]) if missing else "none",
        "",
    ]
    errors = [(asset.guid, error) for asset in result.assets for error in asset.errors]
    if errors:
        parts += ["## Config parse errors", ""] + [f"- {guid}: {error}" for guid, error in errors[:100]] + [""]
    if result.problems:
        parts += ["## Database problems", ""] + [f"- {problem}" for problem in result.problems[:100]] + [""]
    return "\n".join(parts)


def write_reports(result: SSurveyResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = out / "summary.md"
    summary.write_text(render_summary(result), encoding="utf-8", newline="\n")

    assets_csv = out / "assets.csv"
    with assets_csv.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file, lineterminator="\n")
        writer.writerow(
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
        for asset in result.assets:
            writer.writerow(
                [
                    asset.guid,
                    asset.name,
                    asset.display_name,
                    asset.template,
                    asset.group,
                    asset.source,
                    asset.status,
                    asset.base_asset if asset.base_asset is not None else "",
                    ";".join(asset.cfgs),
                    len(asset.refs),
                    len(asset.missing),
                    ";".join(sorted(set(asset.vertex_formats))),
                    ";".join(str(shader_index) for shader_index in sorted(set(asset.shader_indices))),
                ]
            )

    files_json = out / "files.json"
    payload = [
        {
            "guid": asset.guid,
            "name": asset.name,
            "template": asset.template,
            "status": asset.status,
            "base_guid": asset.base_asset,
            "cfgs": asset.cfgs,
            "refs": [asdict(ref) for ref in asset.refs],
            "errors": asset.errors,
        }
        for asset in result.assets
    ]
    files_json.write_text(json.dumps(payload, indent=1), encoding="utf-8", newline="\n")
    return {"summary": summary, "assets": assets_csv, "files": files_json}
