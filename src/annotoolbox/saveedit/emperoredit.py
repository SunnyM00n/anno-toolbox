"""The emperor in the Saves app's Edit tab (docs/save-emperor.md; ``edits`` takes ``EDIT`` with its own rows): ``fields``
gives the rows for a 117 save (the emperor who reigns, each reputation the app may change), ``apply`` checks a change
against the installed game's emperor and zone assets (``rules_of``) and makes it with ``emperor117`` (the emperor)
and ``emperorzones`` (a reputation, across a zone border too).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..assetedit import assetgame
from ..formats import savegame117
from ..infra.i18n import N_
from ..suite import extspecs
from . import edits, emperor117, emperorzones

GAME = "anno117"
EMPEROR = "emperor"
REPUTATION = "reputation:"
EMPEROR_TEMPLATE = "Participant 3rdParty Emperor"
FEATURE_TEMPLATE = "ReputationFeature"
BOUNDS = ("ZoneLowerBound", "ZoneLowerInnerBound", "ZoneUpperInnerBound", "ZoneUpperBound")


def _zone_name(zone: int) -> str:
    return emperor117.ZONES[zone] if 0 <= zone < len(emperor117.ZONES) else str(zone)


def fields(save) -> list[edits.Field]:
    """The emperor who reigns and the reputations the app may change; none when no emperor reigns."""
    part = save.part("data.a7s")
    if part is None:
        return []
    try:
        state = emperor117.read(part.doc)
    except emperor117.EmperorError:
        return []
    if state.active is None:
        return []
    out = [edits.Field(EMPEROR, N_("saves.emperor"), edits.GUID, state.active, choices=tuple(sorted(state.emperors)))]
    reigning = state.emperors.get(state.active)
    book = reigning.book if reigning is not None else ()
    for s in sorted(book, key=lambda s: (s.participant != savegame117.HUMAN_PARTICIPANT, s.participant)):
        if s.reputation is None or s.special is not None:  # left out, or the special state's rules hold
            continue
        human = s.participant == savegame117.HUMAN_PARTICIPANT
        label = N_("saves.reputation_emperor_player") if human else N_("saves.reputation_emperor_who")
        who = f"{{0}}, {_zone_name(s.zone)}"
        key = f"{REPUTATION}{s.participant}"
        out.append(edits.Field(key, label, edits.NUMBER, s.reputation, who, named=(s.participant,)))
    return out


def apply(save, parsed: savegame117.Save, changes: dict, settings) -> savegame117.Save:
    """The emperor first (his book moves to the new one), then the reputations, all checked against the game."""
    rules = rules_of(settings)
    doc = parsed.docs["data.a7s"]
    if EMPEROR in changes:
        doc = emperor117.set_emperor(doc, int(changes[EMPEROR]), rules)
    for key, value in changes.items():
        if key.startswith(REPUTATION):
            doc = emperorzones.set_reputation(doc, int(key.removeprefix(REPUTATION)), int(value), rules)
    return parsed.with_docs({"data.a7s": doc})


def rules_of(settings) -> emperor117.Rules:
    """The rules of the installed 117 of the suite's settings (its asset index, the shared cache)."""
    try:
        game = assetgame.open_installed(settings, GAME)
    except (OSError, ValueError) as e:
        raise emperor117.EmperorError(f"a change of the emperor is checked against the installed game: {e}") from None
    return rules_from_index(game.index)


def rules_from_index(index) -> emperor117.Rules:
    """The emperors (template ``Participant 3rdParty Emperor``) with their zone configuration, and the zones'
    bounds of the ``ReputationFeature`` asset, from an asset index (``assetedit.assetindex``)."""
    assets = list(index.by_guid.values())
    emperors = {e.guid: _zones_text(index.element(e.guid)) for e in assets if e.template == EMPEROR_TEMPLATE}
    feature = next((e for e in assets if e.template == FEATURE_TEMPLATE), None)
    if not emperors or feature is None:
        raise emperor117.EmperorError("the game's assets have no emperor or no ReputationFeature")
    zones = index.element(feature.guid).find("Values/ReputationFeature/ReputationZones")
    bounds = []
    for name in emperor117.ZONES:
        zone = zones.find(name) if zones is not None else None
        try:
            bounds.append(tuple(int(zone.find(b).text) for b in BOUNDS))
        except (AttributeError, TypeError, ValueError):
            raise emperor117.EmperorError(f"ReputationFeature {feature.guid}: {name} has no bounds") from None
    zone_effects = {g: _zone_effects(index.element(g)) for g in emperors}
    effects = {e for zones_ in zone_effects.values() for z in zones_ for e in z.effects}
    scopes = {e: _scope(index, e) for e in effects}
    return emperor117.Rules(emperors, tuple(bounds), zone_effects, {e: s for e, s in scopes.items() if s})


def _zone_effects(asset: ET.Element) -> tuple[emperor117.ZoneEffects, ...]:
    """Per zone (``ZONES`` order) the effects it starts and the assets it unlocks."""
    out = []
    for name in emperor117.ZONES:
        block = asset.find(f"Values/Emperor/ZoneConfiguration/{name}/ZoneEffects")
        effects = [] if block is None else [int(e.text) for e in block.findall("ActiveEffects/Item/Effect") if e.text]
        unlocks = [] if block is None else [int(e.text) for e in block.findall("AssetUnlocks/Item/Asset") if e.text]
        out.append(emperor117.ZoneEffects(tuple(effects), tuple(unlocks)))
    return tuple(out)


def _scope(index, effect: int) -> str:
    """An effect asset's ``EffectScope``, "" when the game has no such effect or it names none."""
    if effect not in index.by_guid:
        return ""
    return (index.element(effect).findtext("Values/Effect/EffectScope") or "").strip()


def _zones_text(asset: ET.Element) -> str:
    """An emperor's zone configuration as comparable text: its elements and their texts, in order."""
    zones = asset.find("Values/Emperor/ZoneConfiguration")
    return "" if zones is None else "|".join(f"{e.tag}={(e.text or '').strip()}" for e in zones.iter())


EDIT = extspecs.SaveEdit(frozenset({GAME}), fields, apply)
