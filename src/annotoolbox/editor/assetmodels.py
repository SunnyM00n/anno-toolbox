"""The model a placed game object draws: its GUID to its asset in the game's ``assets.xml``, the asset's model
variations (its own, its base asset's, else its template's default) and the variation the island file names.

``AssetModels`` is built once over a game's asset files and template text (``AssetGame``: where they are, what
differs per game) and asks them for the GUIDs wanted, never parsing a 46 MB (117) or 290 MB (1800) file whole. A later
definition of a GUID wins. ``model_for(guid, variation)`` answers a ``Resolved``: the config to draw, or ``""`` and
``why`` (a reason the Checks tab lists). ``Reason`` names the reasons. Used by ``IslandEditor.objects`` (the 117
island's buildings, slots, ruins, units) and by the 1800 and 2205 viewers (``a7mviewer``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..infra.i18n import N_, tr
from ..sources.anno117 import ASSETS_XML_PATH, TEMPLATES_XML_PATH
from ..sources.overlay import normalize


class Reason:
    """Why an object has no model of its own to draw (the Checks tab groups its findings by these): data, kept in
    English in an entry; ``describe`` shows one."""

    NO_ASSET = "the game has no asset with this GUID"
    NO_MODEL = "its asset names no model"
    PLACEHOLDER = "its asset only names an editor placeholder"
    UNREADABLE = "its model config cannot be read"
    EMPTY = "its model config draws nothing (a decal or an effect the view does not show)"


SHOWN = {
    Reason.NO_ASSET: N_("islands.game_no_asset_guid"),
    Reason.NO_MODEL: N_("islands.asset_names_no_model"),
    Reason.PLACEHOLDER: N_("islands.asset_only_names_editor_placeholder"),
    Reason.UNREADABLE: N_("islands.model_config_cannot_read"),
    Reason.EMPTY: N_("islands.model_config_draws_nothing_decal"),
}


def describe(why: str) -> str:
    """A reason as the interface language says it (a reason may carry a detail after a colon)."""
    for reason, shown in SHOWN.items():
        if why == reason or why.startswith(reason + ":"):
            return tr(shown) + why[len(reason) :]
    return why


NO_PICTURE = (Reason.NO_MODEL, Reason.PLACEHOLDER, Reason.EMPTY)  # genuinely nothing to draw: a pin stands
VARIATIONS = re.compile(rb"<Object>(?:(?!</Object>).)*?<Variations>(.*?)</Variations>", re.S)
ITEM = re.compile(rb"<Item>(.*?)</Item>", re.S)
FILENAME = re.compile(rb"<Filename>([^<]*)</Filename>")
BASE = re.compile(rb"<BaseAssetGUID>(\d+)</BaseAssetGUID>")
TEMPLATE = re.compile(rb"<Template>([^<]*)</Template>")
NAME = re.compile(rb"<Name>([^<]*)</Name>")
PLACEHOLDERS = ("constraint_dummy.cfg", "empty_render_object")  # configs the game uses for objects with no look
MAX_BASES = 8  # a base asset's own base, and so on


@dataclass(frozen=True)
class AssetGame:
    """Where a game keeps its assets and what differs in reading them."""

    assets: str  # the entry asset file
    templates: str  # the templates file (its defaults give an asset without models its variations)
    includes: bool = False  # assets.xml names more files through its groups; read like the engine (2205)
    suffixes: tuple[str, ...] = (".cfg",)  # an asset's model files
    anywhere: bool = False  # an asset with no ``Variations`` list may name its model elsewhere (1800's props)
    library: tuple[str, ...] = ()  # the templates whose assets the editor's library offers


ASSETS_117 = AssetGame(ASSETS_XML_PATH, TEMPLATES_XML_PATH)
ASSETS_1800 = AssetGame(
    "data/config/export/main/asset/assets.xml",
    "data/config/export/main/asset/templates.xml",
    suffixes=(".cfg", ".prp"),
    anywhere=True,
    library=("VisualObject", "Prop"),  # the decoration an island carries: 540 and 220 assets, each with a model
)
ASSETS_2205 = AssetGame("data/config/game/asset/assets.xml", "data/config/game/asset/templates.xml", includes=True)


@dataclass(frozen=True)
class Resolved:
    cfg: str = ""  # the model config to draw, "" for none
    why: str = ""  # with no config: a ``Reason``
    name: str = ""  # the asset's own name
    template: str = ""


@dataclass
class AssetInfo:
    guid: int
    name: str
    template: str
    variations: list[str] = field(default_factory=list)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def definition_start(assets: bytes, guid: int) -> int:
    """Where the asset ``guid`` is defined (its ``<GUID>`` inside ``<Standard>``), -1 when it is not. The same tag
    also names the GUID in other assets (a pool's members), which are not the asset itself."""
    tag = b"<GUID>%d</GUID>" % guid
    at = assets.find(tag)
    while at >= 0:
        if assets[max(at - 200, 0) : at].rstrip().endswith(b"<Standard>"):
            return at
        at = assets.find(tag, at + len(tag))
    return -1


def variations_of(body: bytes) -> list[str]:
    """An asset's ``Object/Variations`` as paths ("" for an item that names none, so the indexes hold)."""
    found = VARIATIONS.search(body)
    if not found:
        return []
    out = []
    for item in ITEM.findall(found.group(1)):
        f = FILENAME.search(item)
        out.append(normalize(_text(f.group(1)), lower=False) if f else "")
    return out


def template_models(templates: str) -> dict[str, list[str]]:
    """Template name -> the model variations its defaults name, over a ``templates.xml`` text."""
    out = {}
    for name, body in re.findall(r"<Template>\s*<Name>([^<]+)</Name>(.*?)</Template>", templates, re.S):
        found = variations_of(body.encode("utf-8"))
        if found:
            out[name] = [v for v in found if v]
    return out


def read_assets(read: Callable[[str], bytes], game: AssetGame) -> list[bytes]:
    """The game's asset files in its order, a later one winning: ``assets.xml``, then (``game.includes``) the files its
    groups include, as the engine takes them: the entry file first, then the include queue from the back."""
    files, queue, seen, first = [], [game.assets], {game.assets}, True
    while queue:
        path = queue.pop(0) if first else queue.pop()
        first = False
        try:
            data = read(path)
        except (OSError, KeyError):
            continue
        files.append(data)
        if not game.includes:
            break
        for m in re.finditer(rb"<FileName>([^<]+\.xml)</FileName>", data):
            inc = normalize(_text(m.group(1)))
            if "/asset" in inc and inc not in seen:
                seen.add(inc)
                queue.append(inc)
    return files


class AssetModels:
    """GUID -> model, over one game's asset files (bytes, in the game's order) and ``templates.xml`` (text, for the
    templates' defaults)."""

    def __init__(self, assets: bytes | list[bytes], templates: str = "", game: AssetGame = ASSETS_117) -> None:
        self.files = [assets] if isinstance(assets, bytes) else list(assets)
        self.game = game
        self.templates = templates
        self.template_models = template_models(templates)
        self._seen: dict[int, AssetInfo | None] = {}

    @classmethod
    def read(cls, read: Callable[[str], bytes], game: AssetGame) -> AssetModels:
        """The game's assets and templates through ``read`` (game path -> bytes); no templates file is no defaults,
        no asset file raises ``KeyError``."""
        files = read_assets(read, game)
        if not files:
            raise KeyError(game.assets)
        try:
            templates = _text(read(game.templates))
        except (OSError, KeyError):
            templates = ""
        return cls(files, templates, game)

    def _definition(self, guid: int) -> tuple[bytes, int] | None:
        for data in reversed(self.files):
            start = definition_start(data, guid)
            if start >= 0:
                return data, start
        return None

    def asset(self, guid: int, depth: int = 0) -> AssetInfo | None:
        """The asset ``guid``: its name, template and model variations (a base asset's when it has none of its own)."""
        if guid in self._seen:
            return self._seen[guid]
        found = self._definition(guid)
        info = None
        if found is not None:
            data, start = found
            first = data.rfind(b"<Asset>", 0, start)
            end = data.find(b"</Asset>", start)
            head = data[first : data.find(b"<Values>", first, start)]
            body = data[start : end if end > 0 else None]
            template = TEMPLATE.search(head)
            name = NAME.search(body)
            info = AssetInfo(guid, _text(name.group(1)) if name else "", _text(template.group(1)) if template else "")
            info.variations = variations_of(body) or self._loose_model(body)
            base = BASE.search(head)
            if base and depth < MAX_BASES:
                parent = self.asset(int(base.group(1)), depth + 1)
                if parent is not None:
                    info.template = info.template or parent.template
                    info.variations = info.variations or parent.variations
            if not info.variations:
                info.variations = list(self.template_models.get(info.template, ()))
        self._seen[guid] = info
        return info

    def value_of(self, guid: int, tag: str, depth: int = 0) -> str | None:
        """The text of the element ``tag`` in the asset ``guid`` (a base asset's when it has none of its own), None when
        no asset on the way has it."""
        found = self._definition(guid)
        if found is None:
            return None
        data, start = found
        first = data.rfind(b"<Asset>", 0, start)
        end = data.find(b"</Asset>", start)
        hit = re.search(rb"<%b>([^<]*)</%b>" % (tag.encode(), tag.encode()), data[start : end if end > 0 else None])
        if hit:
            return _text(hit.group(1))
        base = BASE.search(data[first : data.find(b"<Values>", first, start)])
        return self.value_of(int(base.group(1)), tag, depth + 1) if base and depth < MAX_BASES else None

    def _loose_model(self, body: bytes) -> list[str]:
        """The first model file named anywhere in an asset with no ``Variations`` (1800's props), as a one-item list."""
        if not self.game.anywhere:
            return []
        files = (normalize(_text(f), lower=False) for f in FILENAME.findall(body))
        return next(([f] for f in files if f.lower().endswith(self.game.suffixes)), [])

    def first_model(self, guid: int) -> str:
        """The first model file of the asset (of the game's ``suffixes``), "" for an asset without one or none."""
        info = self.asset(guid)
        files = [v for v in info.variations if v.lower().endswith(self.game.suffixes)] if info else []
        return files[0] if files else ""

    def model_for(self, guid: int, variation: int = 0) -> Resolved:
        """The config the object draws: the asset's variation ``variation`` (the first when the island names one
        past the list, as the game's fallback), else why there is none."""
        info = self.asset(guid)
        if info is None:
            return Resolved(why=Reason.NO_ASSET)
        files = [v for v in info.variations if v]
        if not files:
            return Resolved(why=Reason.NO_MODEL, name=info.name, template=info.template)
        cfg = info.variations[variation] if 0 <= variation < len(info.variations) else ""
        cfg = cfg or files[0]
        if any(p in cfg.lower() for p in PLACEHOLDERS):
            return Resolved(why=Reason.PLACEHOLDER, name=info.name, template=info.template)
        return Resolved(cfg, "", info.name, info.template)
