"""The asset editor's sanity check: every edited or new asset's values against the schema, and the change set
against the game and other mods. Stdlib only.

``check(doc)`` returns ``SFinding`` rows: references that resolve to no asset or to one without the needed property
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
from .assetdoc import CAssetDocument
from .gamefiles import LAYOUTS
from .schema import SValueSpec
from .values import values_of
from .xmltree import leaves

ERROR, WARNING = "error", "warning"
BOOLS = ("0", "1", "true", "false", "True", "False")
ID_TYPES = ("ScriptId", "ScriptIdGroup", "TextGroup")  # values that are ids of 64 bits, not GUIDs of assets


@dataclass(frozen=True)
class SFinding:
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


def _asset_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
    if not (spec.kind == "asset" and spec.type == "Asset" and text not in ("", "0")):
        return None
    if not text.lstrip("-").isdigit():
        return [(tr("assets.text_no_guid", text=text), ERROR)]
    if not doc.exists(int(text)):
        return [(tr("assets.no_asset_text", text=text), ERROR)]
    ref, out = int(text), []
    have = set(doc.layers(ref).property_names())
    needs = [property_name for property_name in spec.needed_property.split(";") if property_name]  # any one of them
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


def _choice_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
    data_set = doc.m_schema.data_sets.get(spec.data_set)
    if spec.kind == "choice" and data_set and text and text not in data_set.names:
        return [(tr("assets.text_not_in_data_set", text=text, data_set=spec.data_set), ERROR)]
    return None


def _flags_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
    data_set = doc.m_schema.data_sets.get(spec.data_set)
    if not (spec.kind == "flags" and data_set and text):
        return None
    unknown = [flag for flag in text.split(";") if flag and flag not in data_set.names]
    if not unknown:
        return []
    return [(tr("assets.names_not_in_data_set", names=", ".join(unknown), data_set=spec.data_set), ERROR)]


def _number_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
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


def _bool_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
    if spec.kind == "bool" and text and text not in BOOLS:
        return [(tr("assets.text_no_boolean", text=text), ERROR)]
    return None


def _id_value(doc: CAssetDocument, spec: SValueSpec, text: str) -> Problems | None:
    if spec.type in ID_TYPES and text not in ("", "0") and not _int64(text):
        message = tr("assets.no_id_value_whole_number", text=text, type=spec.type)
        return [(message, ERROR)]
    return None


# the first checker that takes a value's kind decides, as the kinds are exclusive
VALUE_CHECKS = (_asset_value, _choice_value, _flags_value, _number_value, _bool_value, _id_value)


def _value(doc: CAssetDocument, guid: int, path: tuple[str, ...], text: str, spec: SValueSpec) -> list[SFinding]:
    for check in VALUE_CHECKS:
        found = check(doc, spec, text)
        if found is not None:
            return [SFinding(guid, "/".join(path), severity, message) for message, severity in found]
    return []


def _int64(text: str) -> bool:
    try:
        return -(2**63) <= int(text) < 2**64
    except ValueError:
        return False


def _variables(doc: CAssetDocument, guid: int, leaf_texts: dict[tuple[str, ...], str]) -> list[SFinding]:
    """A variable value (``IsVariable`` 1) names a variable: its ``Value`` is a name, not empty and not a number."""
    out = []
    for path, flag in leaf_texts.items():
        if path[-1] == "IsVariable" and flag == "1":
            value = leaf_texts.get((*path[:-1], "Value"), "")
            if not value or value.lstrip("-").isdigit():
                parent_path = "/".join(path[:-1])
                out.append(
                    SFinding(
                        guid,
                        parent_path,
                        WARNING,
                        tr("assets.marked_variable_value_no_name", value=value),
                    )
                )
    return out


def check_asset(doc: CAssetDocument, guid: int, only: list[str] | None = None) -> list[SFinding]:
    """One asset's own values against the schema; ``only``: the paths (and what is below them) to look at, the
    values a change set touched (the game's own values are the game's)."""
    out: list[SFinding] = []
    if not doc.m_schema.properties:
        return out
    values = values_of(doc.asset(guid))
    prefixes = tuple(only_path.split("/") for only_path in only) if only is not None else None
    for property_element in values:
        if property_element.tag not in doc.m_schema.properties and (
            prefixes is None or any(only_path[0] == property_element.tag for only_path in prefixes)
        ):
            out.append(SFinding(guid, property_element.tag, WARNING, tr("assets.property_schema_not_know_game")))
    leaf_texts = dict(leaves(values))
    out += _variables(doc, guid, leaf_texts)
    template = doc.template(guid)
    if template and doc.m_schema.templates and template not in doc.m_schema.templates:
        out.append(
            SFinding(
                guid,
                "Template",
                WARNING,
                tr("assets.template_not_games_templates_removed", template=template),
            )
        )
    for path, text in leaves(values):
        if len(path) < 2 or path[0] not in doc.m_schema.properties:
            continue  # an empty property block keeps its defaults
        if prefixes is not None and not any(
            tuple(path[: len(only_path)]) == tuple(only_path) for only_path in prefixes
        ):
            continue
        spec = doc.m_schema.spec_at(path)
        if spec is not None:
            out += _value(doc, guid, path, text, spec)
        elif not doc.m_schema.known(path):
            out.append(SFinding(guid, "/".join(path), WARNING, tr("assets.value_schema_not_know_game")))
    return out


def check(doc: CAssetDocument, guids: list[int] | None = None) -> list[SFinding]:
    """The change set's assets (or ``guids``) and the change set itself."""
    out: list[SFinding] = []
    changes = doc.m_changes
    for guid in (
        guids
        if guids is not None
        else [touched_guid for touched_guid in changes.touched() if touched_guid not in changes.m_removed]
    ):
        if doc.exists(guid):
            edited = changes.m_assets.get(guid)
            only = None if guids is not None or guid in changes.m_new or edited is None else edited.paths()
            out += check_asset(doc, guid, only)
    for guid, record in changes.m_new.items():
        if record.base is not None and not doc.exists(record.base):
            out.append(SFinding(guid, "BaseAssetGUID", ERROR, tr("assets.base_asset_not_exist", base=record.base)))
        if record.base is None and record.copy_of is None and record.template not in doc.m_schema.templates:
            out.append(SFinding(guid, "Template", ERROR, tr("assets.no_template_lower", template=record.template)))
        if guid in doc.m_index.m_by_guid:
            out.append(SFinding(guid, "", ERROR, tr("assets.game_already_asset_guid")))
        min_guid, max_guid = changes.m_guid_range
        group = doc.group_range(record.anchor)
        if not record.xml and not min_guid <= guid <= max_guid and not (group and group[0] <= guid <= group[1]):
            out.append(
                SFinding(guid, "", WARNING, tr("assets.outside_change_sets_guid_range", lo=min_guid, hi=max_guid))
            )
    for guid in changes.m_removed:
        users = [user for user in doc.m_index.references(guid) if user not in changes.m_removed]
        if users:
            shown = ", ".join(map(str, users[:5])) + (" ..." if len(users) > 5 else "")
            out.append(
                SFinding(
                    guid,
                    "",
                    WARNING,
                    tr("assets.removed_assets_still_name", n=len(users), shown=shown),
                )
            )
    return out


GUID_TAG = re.compile(rb"<GUID>\s*(\d+)\s*</GUID>")


def clashes(doc: CAssetDocument, mods: Path) -> list[SFinding]:
    """New GUIDs of the change set that another mod in the folder ``mods`` uses too (its own folder skipped)."""
    mine = set(doc.m_changes.m_new)
    own = {
        doc.m_changes.m_mod_id,
        *(Path(carried.folder).name for carried in doc.m_changes.m_carry),
    }  # the imported mods themselves
    out = []
    for path in sorted(Path(mods).glob("*/**/*.xml")) if mine and Path(mods).is_dir() else ():
        if path.relative_to(mods).parts[0] in own or not path.is_file():  # a folder named x.xml matches the glob too
            continue
        try:
            data = path.read_bytes()
        except OSError:  # locked or gone while another tool writes it: that mod cannot be judged
            continue
        for found in {int(match.group(1)) for match in GUID_TAG.finditer(data)} & mine:
            out.append(
                SFinding(
                    found,
                    "",
                    ERROR,
                    tr("assets.mod_uses_guid_too", mod=path.relative_to(mods).parts[0]),
                )
            )
    return out


def final_check(doc: CAssetDocument, mods: Path | None = None) -> list[SFinding]:
    """Everything ``check`` finds, the clashes with the other mods, and whether the patches render."""
    out = check(doc) + (clashes(doc, mods) if mods is not None else [])
    if "modops" in LAYOUTS[doc.m_changes.m_game].outputs:
        try:
            modops.render(doc)
        except (ValueError, KeyError) as error:
            out.append(SFinding(None, "build", ERROR, str(error)))
    return out
