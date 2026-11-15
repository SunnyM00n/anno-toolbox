"""117 assets written by us: from the ground up (a template plus the values we mean) or cloned from a vanilla one.

117's ``templates.xml`` holds each template's defaults, and a vanilla asset lists only what differs from them.
Writing ours the same way leaves nothing of a donor behind; :func:`donor_profile` lists the fields the vanilla
assets of a template set, :func:`gaps` names those a generated asset leaves to the defaults, :func:`clone_asset` is
the full copy with fields rewritten (systems.md, "Written from the ground up").
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

Value = str | ET.Element | None


def set_value(values: ET.Element, path: str, value: Value) -> None:
    """Put ``value`` at the ``Values``-relative ``path``: a string sets the leaf's text (the path is created
    when missing), an ``Element`` replaces the subtree at the path (its own tag names it), ``None`` removes it."""
    parts = path.split("/")
    node = values
    for tag in parts[:-1]:
        child = node.find(tag)
        node = child if child is not None else ET.SubElement(node, tag)
    leaf = node.find(parts[-1])
    if value is None or isinstance(value, ET.Element):
        if leaf is not None:
            node.remove(leaf)
        if value is not None:
            node.append(value)
    else:
        (leaf if leaf is not None else ET.SubElement(node, parts[-1])).text = value


def build(template: str, values: dict[str, Value]) -> str:
    """An ``<Asset>`` of ``template`` with ``values`` at their ``Values``-relative paths (:func:`set_value`;
    ``None`` is left out)."""
    asset = ET.Element("Asset")
    ET.SubElement(asset, "Template").text = template
    root = ET.SubElement(asset, "Values")
    for path, value in values.items():
        if value is not None:
            set_value(root, path, value)
    return ET.tostring(asset, encoding="unicode")


def clone_asset(asset_xml: str, overrides: dict[str, Value]) -> str:
    """A copy of a vanilla asset with ``overrides`` at their ``Values``-relative paths (:func:`set_value`)."""
    asset = ET.fromstring(asset_xml)
    values = asset.find("Values")
    if values is None:
        raise ValueError("asset without Values")
    for path, value in overrides.items():
        set_value(values, path, value)
    return ET.tostring(asset, encoding="unicode")


def _leaf_paths(node: ET.Element, prefix: str = ""):
    kids = list(node)
    if not kids:
        yield prefix
    for c in kids:
        yield from _leaf_paths(c, f"{prefix}/{c.tag}" if prefix else c.tag)


def donor_profile(assets_xml: str, template: str) -> tuple[int, dict[str, int]]:
    """How many vanilla assets of ``template`` there are, and how many of them set each field (a list
    counts as the path down to its ``Item``s, so the items' own fields do not split it up)."""
    blocks = re.findall(rf"<Asset>\s*<Template>{re.escape(template)}</Template>.*?</Asset>", assets_xml, re.S)
    counts: dict[str, int] = {}
    for block in blocks:
        values = ET.fromstring(block).find("Values")
        seen = {p.split("/Item")[0] for p in _leaf_paths(values)} if values is not None else set()
        for p in seen:
            counts[p] = counts.get(p, 0) + 1
    return len(blocks), counts


IDENTITY = ("Standard", "Text/OasisId")  # what makes an asset itself, never shared


def _fields(node: ET.Element, prefix: str = ""):
    """(path, value) per field: a leaf's text, or a whole list (an element holding ``Item``s)."""
    for c in node:
        path = f"{prefix}/{c.tag}" if prefix else c.tag
        if len(c) and all(k.tag == "Item" for k in c):
            yield path, c
        elif len(c):
            yield from _fields(c, path)
        else:
            yield path, c.text or ""


def consensus(assets_xml: str, template: str, names: str = "") -> dict[str, Value]:
    """The fields every vanilla asset of ``template`` (whose name matches the ``names`` pattern) sets to
    the same value: the engine wiring a template's assets share in practice, which its defaults in
    ``templates.xml`` leave out. Unlike a copied donor, nothing of one asset in particular."""
    shared: dict[str, tuple[str, Value]] | None = None
    for block in re.findall(rf"<Asset>\s*<Template>{re.escape(template)}</Template>.*?</Asset>", assets_xml, re.S):
        values = ET.fromstring(block).find("Values")
        if values is None:
            continue
        if names and not re.search(names, values.findtext("Standard/Name") or ""):
            continue
        mine = {}
        for path, v in _fields(values):
            if not path.startswith(IDENTITY):
                mine[path] = (ET.tostring(v, encoding="unicode") if isinstance(v, ET.Element) else v, v)
        shared = mine if shared is None else {p: sv for p, sv in shared.items() if p in mine and mine[p][0] == sv[0]}
    return {p: v for p, (_, v) in (shared or {}).items()}


def compose(base: dict[str, Value], ours: dict[str, Value]) -> dict[str, Value]:
    """``ours`` over ``base``: a value replaces whatever ``base`` has at or below its path (and a list
    above it), and ``None`` removes path and all below, so a whole component can be switched off."""
    out = dict(base)
    for path, value in ours.items():
        for p in list(out):
            under = p == path or p.startswith(path + "/")
            # a list above a value we set would hold it twice; an empty component above is its parent
            if under or (value is not None and path.startswith(p + "/") and isinstance(out[p], ET.Element)):
                del out[p]
        if value is not None:
            out[path] = value
    return out


def template_paths(templates_xml: str, template: str) -> set[str]:
    """The fields ``template``'s own defaults set in ``templates.xml`` (lists down to their ``Item``s)."""
    m = re.search(rf"<Name>{re.escape(template)}</Name>\s*<Properties>.*?</Properties>", templates_xml, re.S)
    if not m:
        return set()
    props = ET.fromstring(m.group(0)[m.group(0).index("<Properties>") :])
    return {p.split("/Item")[0] for p in _leaf_paths(props)}


def gaps(
    values: dict[str, Value],
    profile: tuple[int, dict[str, int]],
    share: float = 1.0,
    defaults: set[str] = frozenset(),
) -> list[str]:
    """The fields at least ``share`` of the donors set that neither ``values`` nor the template's
    ``defaults`` (:func:`template_paths`) give: a field counts as covered when something sits at it,
    above it, or below it."""
    total, counts = profile
    given = [p for p, v in values.items() if v is not None] + list(defaults)

    def covered(path: str) -> bool:
        return any(g == path or path.startswith(g + "/") or g.startswith(path + "/") for g in given)

    return sorted(p for p, n in counts.items() if total and n / total >= share and not covered(p))


CULTURE = "2070"  # stands where 117 names its culture: "Production Food Roman Porridge"
FACTIONS = ("Ecos", "Tycoons", "Techs")


def words(identifier: str) -> str:
    """A 2070 identifier as 117 writes a name: ``food_supply_factory`` -> ``Food Supply Factory``,
    ``TVDinner`` -> ``TV Dinner``. Lower case and underscores give the identifier back."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", identifier.replace("_", " "))
    return " ".join(w[:1].upper() + w[1:] for w in spaced.split())


def culture(faction: str | None = None) -> str:
    """``2070`` or ``2070 Tycoons``: our assets' culture word, the faction where the asset has one."""
    return f"{CULTURE} {faction}" if faction in FACTIONS else CULTURE


def name(kind: str, faction: str | None = None, *subject: str) -> str:
    """An asset name in 117's order, kind first: ``name("Production", "Tycoons", "Food Supply Factory")``
    -> ``Production 2070 Tycoons Food Supply Factory`` (docs/systems.md, "Naming")."""
    return " ".join(p for p in (kind, culture(faction), *subject) if p)


# What 117's own buildings carry and 2070's never had: water, Romanization, the Roman population
# link, skins, quest objects, Roman sounds (2070's come with the sound system, systems.md)
NOT_2070 = (
    "AqueductConsumer",
    "Romanization",
    "AttributeProvider",
    "Object/Skins",
    "QuestObject",
    "SoundEmitter",
    "AmbientMoodProvider/Murmur",  # the Latin crowd voices around a building (sounds.md "Every slot of the emitter")
    "Selection/ParticipantMessageArcheType",  # the Roman resident who speaks in the info panel
    "BuildingUnique",
    "Building/FunctionalEffects",  # 117's buffs; 2070's effects come with their systems
)


def _consensus(source, template: str, names: str) -> dict[str, Value]:
    """:func:`consensus` over ``source.text`` (an ``Anno117Assets``), remembered on the source: one scan of
    the asset list per kind, not one per asset written."""
    cache = source.__dict__.setdefault("_consensus", {})
    if (template, names) not in cache:
        cache[template, names] = consensus(source.text, template, names)
    return cache[template, names]


def ground_up(source, template: str, names: str, *layers: dict, donor: str | None = None) -> str:
    """A 117 asset from the ground up (systems.md, "Written from the ground up"): what every vanilla
    asset of its kind sets alike (``names`` picks them), less what 2070 has none of, then each layer
    over the last - the 117 wiring of the kind, then the 2070 asset's own values. ``donor``: the template
    whose vanilla assets give that consensus, for a template no vanilla asset uses (the guild house)."""
    values = compose(_consensus(source, donor or template, names), dict.fromkeys(NOT_2070))
    for layer in layers:
        values = compose(values, layer)
    first = [p for p in values if p.startswith(IDENTITY)]
    return build(template, {p: values[p] for p in first + [p for p in values if p not in first]})


def identity(guid: int, name: str, icon: str | None = None) -> dict[str, Value]:
    return {
        "Standard/GUID": str(guid),
        "Standard/Name": name,
        "Standard/IconFilename": icon,
        "Text/OasisId": str(guid),
        "Locked/DefaultLockedState": "0",
    }
