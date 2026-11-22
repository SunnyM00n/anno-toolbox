"""The live widget values of a running game, held for the UI editor's preview and recorded for later (no Qt).

``from_reply`` turns the answer of the live link's ``ui.values`` (docs/live-link.md section 17) into ``SHeld``: per
widget only its text, image id, visibility and own property values, by the keys the preview matches an element by
(its instance GUID, ``crc:<name CRC>``). ``CRecordings`` keeps one per game and file in the Toolbox's data folder, so
the values fill the preview without the game. docs/ui-editor.md, "Live values".
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from . import elements

FORMAT = 1
FOLDER = "ui-values"  # below the workspace (or the Toolbox's data folder)
KEPT = ("guid", "crc", "name", "visible", "text", "image", "values")  # what a widget may hold: never more
VISIBLE, IMAGE_ID = 0x80000007, 0x8000002A  # the built-in properties the preview takes (``layout``)
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class SHeld:
    """The values of one file's widgets, taken from the game now or read from a recording."""

    file_key: str
    game: str
    build: str = ""
    taken: str = ""  # when, ISO 8601 to the second, local time
    states: list[str] = field(default_factory=list)
    widgets: list[dict] = field(default_factory=list)
    cut: bool = False  # the game's answer ended at its size: some widgets are missing
    recorded: bool = False  # read from a recording, not taken from a running game in this run
    language: str = ""  # the game's text language when taken, where the answer or the recording says it

    def by_key(self) -> dict[str, dict]:
        """Each widget by its keys; the first widget that has a key keeps it."""
        out: dict[str, dict] = {}
        for widget in self.widgets:
            for key in keys_of(widget):
                out.setdefault(key, widget)
        return out

    def texts(self) -> dict[str, str]:
        """The labels' texts by key (``layout.Context.live_text``)."""
        return {k: widget["text"] for k, widget in self.by_key().items() if isinstance(widget.get("text"), str)}

    def overrides(self) -> dict[str, dict[tuple[int, ...], object]]:
        """The rest as the preview's property values by key (``layout.Context.live_values``): Visible, the image's
        id, and the instance's own properties by UniqueId."""
        out = {}
        for k, widget in self.by_key().items():
            values: dict[tuple[int, ...], object] = {}
            if isinstance(widget.get("visible"), bool):
                values[(VISIBLE,)] = widget["visible"]
            if isinstance(widget.get("image"), str):
                values[(IMAGE_ID,)] = widget["image"]
            for uid, value in own_values(widget):
                if str(uid).isdigit() and isinstance(value, (bool, int, float, str)):
                    values[(int(uid),)] = value
            if values:
                out[k] = values
        return out

    def unmatched(self, element_keys: set[str]) -> int:
        """How many widgets match no element of the open file (it changed since): they keep their sample values."""
        return sum(1 for widget in self.widgets if not set(keys_of(widget)) & element_keys)


@dataclass(frozen=True)
class SAssigned:
    """What the game assigned to a widget, by reference: an asset by GUID (a property value), an image or a portrait
    by the id it draws; ``name`` and ``icon`` from the installed game, ``note`` when the install has no file."""

    widget: str
    what: str  # "asset", "image" or "portrait"
    ref: str
    name: str = ""
    icon: str = ""
    note: str = ""


MIN_GUID = 100  # the games' asset GUIDs have 3 to 10 digits; a smaller number is a count or a switch
# An image the game draws from a texture it made at run time (a render target, a composed portrait), seen in the
# game's own scenes on 2026-11-17 as ``rtt_<n>`` and ``tex_<n>``: no file of the install holds it.
RUN_TIME = re.compile(r"(?:rtt|tex)_\d+")
NO_FILE, MADE_AT_RUN_TIME = "no file", "made at run time"


def image_note(image: str, has_file: Callable[[str], bool]) -> str:
    """ "" when the install has the picture, else why not."""
    if RUN_TIME.fullmatch(image):
        return MADE_AT_RUN_TIME
    return "" if has_file(image) else NO_FILE


def assigned(
    held: SHeld, asset: Callable[[int], tuple[str, str] | None], has_file: Callable[[str], bool]
) -> list[SAssigned]:
    """The assets, images and portraits the held widgets show. ``asset(guid)``: (name, icon path) of an asset of the
    installed game, None for a number that is none; ``has_file(image id)``: whether the install has a picture for it
    (the ids come as the game names them, backslashes and all). An image with no file is one the game made at run
    time (``RUN_TIME``) or an id the Toolbox does not resolve."""
    out = []
    for widget in held.widgets:
        label = str(widget.get("name") or widget.get("guid") or "?")
        image = widget.get("image")
        if isinstance(image, str) and image:
            what = "portrait" if "portrait" in f"{label} {image}".lower() else "image"
            note = image_note(image, has_file)
            out.append(SAssigned(label, what, image, icon="" if note else image, note=note))
        for uid, value in own_values(widget):
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value == int(value)
                and value >= MIN_GUID
            ):
                found = asset(int(value))
                if found is not None:
                    out.append(SAssigned(f"{label} ({uid})", "asset", str(int(value)), found[0], found[1]))
    return out


def own_values(widget: dict) -> list[tuple[str, object]]:
    """A widget's own property values as (UniqueId, value); none where the answer or the recording holds no table."""
    values = widget.get("values")
    return list(values.items()) if isinstance(values, dict) else []


def keys_of(widget: dict) -> list[str]:
    keys = [widget["guid"]] if isinstance(widget.get("guid"), str) else []
    crc = widget.get("crc")
    if isinstance(crc, str) and re.fullmatch(r"[0-9a-fA-F]{8}", crc):
        keys.append(elements.crc_name(int(crc, 16)))
    return keys


def from_reply(data: dict, file_key: str, game: str, build: str, taken: str) -> SHeld:
    """The answer's widgets of every scene (the first scene's first, as the game answered them), each with the kept
    keys only."""
    widgets, states = [], []
    for scene in data.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        states.append(str(scene.get("state", "")))
        widgets += [
            {k: widget[k] for k in KEPT if k in widget}
            for widget in scene.get("widgets") or []
            if isinstance(widget, dict)
        ]
    language = data.get("language")
    return SHeld(file_key, game, build, taken, states, widgets, bool(data.get("cut")),
                language=language if isinstance(language, str) else "")  # fmt: skip


def file_guid(guid: bytes | str | None) -> str:
    """A GUID as the game's answer writes it (the files' little-endian layout, as ``uuid`` bytes_le)."""
    if isinstance(guid, bytes):
        return str(uuid.UUID(bytes_le=guid))
    return str(guid or "")


class CRecordings:
    """One recording per game and file below ``folder``; written whole or not at all, read back after a restart."""

    def __init__(self, folder: Path) -> None:
        self.m_folder = Path(folder)

    def path(self, game: str, file_key: str) -> Path:
        stem = _SAFE.sub("_", file_key.strip("/")).strip("_")[-120:] or "file"
        return self.m_folder / _SAFE.sub("_", game) / f"{stem}.json"

    def save(self, held: SHeld) -> Path:
        record = {"format": FORMAT, **asdict(held)}
        record.pop("recorded")
        path = self.path(held.game, held.file_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"))
        return path

    def load(self, game: str, file_key: str) -> SHeld | None:
        """The recording of the file, None when there is none or it is not one this format reads."""
        try:
            record = json.loads(self.path(game, file_key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("format") != FORMAT or record.get("file_key") != file_key:
            return None
        widgets = [
            {k: widget[k] for k in KEPT if k in widget}
            for widget in record.get("widgets") or []
            if isinstance(widget, dict)
        ]
        states = [str(state) for state in record.get("states") or []]
        return SHeld(file_key, game, str(record.get("build", "")), str(record.get("taken", "")), states, widgets,
                    bool(record.get("cut")), recorded=True, language=str(record.get("language") or ""))  # fmt: skip

    def clear(self, game: str, file_key: str) -> bool:
        try:
            self.path(game, file_key).unlink()
        except OSError:
            return False
        return True

    def clear_all(self) -> int:
        """Every recording of every game deleted; how many."""
        gone = 0
        for path in self.m_folder.glob("*/*.json") if self.m_folder.is_dir() else ():
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
        return gone
