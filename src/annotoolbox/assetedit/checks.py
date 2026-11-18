"""The asset editor's sanity check: every edited or new asset's values against the schema, and the change set
against the game and other mods. Stdlib only.

``check(doc)`` returns ``Finding`` rows: references that resolve to no asset or to one without the needed property
or of a template not allowed, choices and flags outside their data set, numbers outside their range or no numbers,
values and properties the schema does not know (the game drops them without a word, engine-modloader), a missing
base asset or template, a removed asset others still name, a new GUID that another mod uses. ``final_check`` adds
the build's own check before a mod is written. ``docs/tool-suite.md`` A7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..infra.i18n import alternatives, tr
from . import modops
from .assetdoc import AssetDocument
from .gamefiles import LAYOUTS
from .schema import ValueSpec
from .values import values_of
from .xmltree import leaves

ERROR, WARNING = "error", "warning"
BOOLS = ("0", "1", "true", "false", "True", "False")
ID_TYPES = ("ScriptId", "ScriptIdGroup", "TextGroup")  # values that are ids of 64 bits, not GUIDs of assets


@dataclass(frozen=True)
class Finding:
    guid: int | None
    path: str
    severity: str
    message: str

    def __str__(self) -> str:
        where = f"{self.guid} {self.path}".strip() if self.guid is not None else self.path
        severity = {ERROR: tr("common.error_lower"), WARNING: tr("common.warning_lower")}.get(
            self.severity, self.severity
        )
        return f"{severity}: {where}: {self.message}"


Problems = list[tuple[str, str]]  # (message, severity)


def _asset_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    if not (spec.kind == "asset" and spec.type == "Asset" and text not in ("", "0")):
        return None
    if not text.lstrip("-").isdigit():
        return [(tr("assets.text_no_guid", text=text), ERROR)]
    if not doc.exists(int(text)):
        return [(tr("assets.no_asset_text", text=text), ERROR)]
    ref, out = int(text), []
    have = set(doc.layers(ref).property_names())
    needs = [n for n in spec.needed_property.split(";") if n]  # any one of them
    if needs and not set(needs) & have:
        out.append((tr("assets.asset_no_value_needs", ref=ref, needs=alternatives(needs)), ERROR))
    if spec.allowed_templates and doc.template(ref) not in spec.allowed_templates:
        allowed = ", ".join(spec.allowed_templates)
        out.append(
            (
                tr(
                    "assets.asset_not_one",
                    ref=ref,
                    template=doc.template(ref),
                    allowed=allowed,
                ),
                ERROR,
            )
        )
    allowed = set(spec.allowed_properties)
    if allowed and not allowed & have:
        names = ", ".join(spec.allowed_properties)
        out.append((tr("assets.asset_none_properties", ref=ref, names=names), WARNING))
    return out


def _choice_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    data_set = doc.schema.data_sets.get(spec.data_set)
    if spec.kind == "choice" and data_set and text and text not in data_set.names:
        return [(tr("assets.text_not_in_data_set", text=text, data_set=spec.data_set), ERROR)]
    return None


def _flags_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    data_set = doc.schema.data_sets.get(spec.data_set)
    if not (spec.kind == "flags" and data_set and text):
        return None
    unknown = [f for f in text.split(";") if f and f not in data_set.names]
    if not unknown:
        return []
    return [(tr("assets.names_not_in_data_set", names=", ".join(unknown), data_set=spec.data_set), ERROR)]


def _number_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    if not (spec.kind == "number" and text):
        return None
    try:
        number = float(text)
    except ValueError:
        return [(tr("assets.text_no_number", text=text), ERROR)]
    out = []
    if spec.minimum is not None and number < spec.minimum:
        out.append((tr("assets.text_below_minimum", text=text, minimum=spec.minimum), ERROR))
    if spec.maximum is not None and number > spec.maximum:
        out.append((tr("assets.text_above_maximum", text=text, maximum=spec.maximum), ERROR))
    return out


def _bool_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    if spec.kind == "bool" and text and text not in BOOLS:
        return [(tr("assets.text_no_boolean", text=text), ERROR)]
    return None


def _id_value(doc: AssetDocument, spec: ValueSpec, text: str) -> Problems | None:
    if spec.type in ID_TYPES and text not in ("", "0") and not _int64(text):
        message = tr("assets.no_id_value_whole_number", text=text, type=spec.type)
        return [(message, ERROR)]
    return None


# the first checker that takes a value's kind decides, as the kinds are exclusive
VALUE_CHECKS = (_asset_value, _choice_value, _flags_value, _number_value, _bool_value, _id_value)


def _value(doc: AssetDocument, guid: int, path: tuple[str, ...], text: str, spec: ValueSpec) -> list[Finding]:
    for check in VALUE_CHECKS:
        found = check(doc, spec, text)
        if found is not None:
            return [Finding(guid, "/".join(path), severity, message) for message, severity in found]
    return []


def _int64(text: str) -> bool:
    try:
        return -(2**63) <= int(text) < 2**64
    except ValueError:
        return False


def _variables(doc: AssetDocument, guid: int, leaf_texts: dict[tuple[str, ...], str]) -> list[Finding]:
    """A variable value (``IsVariable`` 1) names a variable: its ``Value`` is a name, not empty and not a number."""
    out = []
    for path, flag in leaf_texts.items():
        if path[-1] == "IsVariable" and flag == "1":
            value = leaf_texts.get((*path[:-1], "Value"), "")
            if not value or value.lstrip("-").isdigit():
                at = "/".join(path[:-1])
                out.append(
                    Finding(
                        guid,
                        at,
                        WARNING,
                        tr("assets.marked_variable_value_no_name", value=value),
                    )
                )
    return out


def check_asset(doc: AssetDocument, guid: int, only: list[str] | None = None) -> list[Finding]:
    """One asset's own values against the schema; ``only``: the paths (and what is below them) to look at, the
    values a change set touched (the game's own values are the game's)."""
    out: list[Finding] = []
    if not doc.schema.properties:
        return out
    values = values_of(doc.asset(guid))
    prefixes = tuple(p.split("/") for p in only) if only is not None else None
    for prop in values:
        if prop.tag not in doc.schema.properties and (prefixes is None or any(p[0] == prop.tag for p in prefixes)):
            out.append(Finding(guid, prop.tag, WARNING, tr("assets.property_schema_not_know_game")))
    leaf_texts = dict(leaves(values))
    out += _variables(doc, guid, leaf_texts)
    template = doc.template(guid)
    if template and doc.schema.templates and template not in doc.schema.templates:
        out.append(
            Finding(
                guid,
                "Template",
                WARNING,
                tr("assets.template_not_games_templates_removed", template=template),
            )
        )
    for path, text in leaves(values):
        if len(path) < 2 or path[0] not in doc.schema.properties:
            continue  # an empty property block keeps its defaults
        if prefixes is not None and not any(tuple(path[: len(p)]) == tuple(p) for p in prefixes):
            continue
        spec = doc.schema.spec_at(path)
        if spec is not None:
            out += _value(doc, guid, path, text, spec)
        elif not doc.schema.known(path):
            out.append(Finding(guid, "/".join(path), WARNING, tr("assets.value_schema_not_know_game")))
    return out


def check(doc: AssetDocument, guids: list[int] | None = None) -> list[Finding]:
    """The change set's assets (or ``guids``) and the change set itself."""
    out: list[Finding] = []
    c = doc.changes
    for guid in guids if guids is not None else [g for g in c.touched() if g not in c.removed]:
        if doc.exists(guid):
            edited = c.assets.get(guid)
            only = None if guids is not None or guid in c.new or edited is None else edited.paths()
            out += check_asset(doc, guid, only)
    for guid, record in c.new.items():
        if record.base is not None and not doc.exists(record.base):
            out.append(Finding(guid, "BaseAssetGUID", ERROR, tr("assets.base_asset_not_exist", base=record.base)))
        if record.base is None and record.copy_of is None and record.template not in doc.schema.templates:
            out.append(Finding(guid, "Template", ERROR, tr("assets.no_template_lower", template=record.template)))
        if guid in doc.index.by_guid:
            out.append(Finding(guid, "", ERROR, tr("assets.game_already_asset_guid")))
        lo, hi = c.guid_range
        group = doc.group_range(record.anchor)
        if not record.xml and not lo <= guid <= hi and not (group and group[0] <= guid <= group[1]):
            out.append(Finding(guid, "", WARNING, tr("assets.outside_change_sets_guid_range", lo=lo, hi=hi)))
    for guid in c.removed:
        users = [u for u in doc.index.references(guid) if u not in c.removed]
        if users:
            shown = ", ".join(map(str, users[:5])) + (" ..." if len(users) > 5 else "")
            out.append(
                Finding(
                    guid,
                    "",
                    WARNING,
                    tr("assets.removed_assets_still_name", n=len(users), shown=shown),
                )
            )
    return out


GUID_TAG = re.compile(rb"<GUID>\s*(\d+)\s*</GUID>")


def clashes(doc: AssetDocument, mods: Path) -> list[Finding]:
    """New GUIDs of the change set that another mod in the folder ``mods`` uses too (its own folder skipped)."""
    mine = set(doc.changes.new)
    own = {doc.changes.mod_id, *(Path(c.folder).name for c in doc.changes.carry)}  # the imported mods themselves
    out = []
    for path in sorted(Path(mods).glob("*/**/*.xml")) if mine and Path(mods).is_dir() else ():
        if path.relative_to(mods).parts[0] in own:
            continue
        for found in {int(m.group(1)) for m in GUID_TAG.finditer(path.read_bytes())} & mine:
            out.append(
                Finding(
                    found,
                    "",
                    ERROR,
                    tr("assets.mod_uses_guid_too", mod=path.relative_to(mods).parts[0]),
                )
            )
    return out


def final_check(doc: AssetDocument, mods: Path | None = None) -> list[Finding]:
    """Everything ``check`` finds, the clashes with the other mods, and whether the patches render."""
    out = check(doc) + (clashes(doc, mods) if mods is not None else [])
    if "modops" in LAYOUTS[doc.changes.game].outputs:
        try:
            modops.render(doc)
        except (ValueError, KeyError) as exc:
            out.append(Finding(None, "build", ERROR, str(exc)))
    return out
