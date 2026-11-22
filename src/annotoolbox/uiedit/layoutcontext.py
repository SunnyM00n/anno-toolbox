"""What placing needs besides the file: the other files, the texts, the sample values and what was derived from
them, kept between frames (``CContext``). ``layout`` places the widgets and re-exports it."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import phoenixedit
from . import (
    arrange,
    baseline,
    elements,
    fields,
    gamedata,
    previewdefaults,
)
from .fields import property_values
from .labels import STextStyle
from .memo import CMemo
from .scope import (
    SRootInfo,
    root_info,
)
from .viewmodels import CSamples


@dataclass
class CContext:
    """What placing needs besides the file: the other files, the texts, the sample values, text measuring."""

    m_files: object  # uifiles.CUiFiles
    m_samples: CSamples = field(default_factory=CSamples)
    m_measure: Callable[[str, STextStyle, float], tuple[float, float]] | None = None
    m_fonts: Callable[[str | None, str], tuple[str | None, float]] | None = None  # a localized font -> (family, scale)
    m_language: str = "english"
    m_palettes: dict[str, tuple[dict | None, dict[int, tuple[int, int, int]]]] = field(default_factory=dict)
    m_roots: dict[int, SRootInfo] = field(default_factory=dict)  # what each file gives once (``forget`` after an edit)
    m_memo: CMemo = field(default_factory=CMemo)  # scopes, measures and placed files kept between frames
    m_epoch: tuple | None = None  # what the kept results were made under (``place`` starts over when it changes)
    m_design: bool = True  # the design view: what an animation fades in is shown (ui-editor.md "The preview")
    m_editor_hidden: bool = False  # draw the layers the author hid in the editor (``IsHidden``) too
    m_window_aspect: float = 0.0  # a window of this width over height, 0 the file's own: the root stretches per axis
    m_animation: tuple[str, float] | None = None  # (animation GUID, seconds) of the open file played at that time
    m_tweens: dict[str, dict[tuple[int, ...], object]] | None = None  # the open file's tweens at a time (tweens.writes)
    m_live_text: dict[str, str] = field(
        default_factory=dict
    )  # label texts held from a running game, by instance GUID (U8)
    # the rest a running game's widgets show (Visible, an image's id, own property values), by the same keys
    m_live_values: dict[str, dict[tuple[int, ...], object]] = field(default_factory=dict)
    m_defaults: object = None  # what the preview assumes of the player's game (``previewdefaults.current``)
    m_baseline: baseline.CBaseline | None = None  # what the game showed in many sessions, over the install (None: off)
    m_game_data: object = None  # the installed game's data for what the game fills (``gamedata.CGameData``)
    m_node_values: dict[int, tuple[dict, dict]] = field(default_factory=dict)  # id(node) -> (node, ``values``)
    m_prefixes: set[str] = field(default_factory=set)  # the sample key prefixes a list counted its elements under
    m_play: dict | None = None  # Play mode's values by file (``playstate.CPlayState.trees``); None in Design mode
    m_play_choices: object = None  # the choices Play mode keeps in the Preview settings (``playstate.SChoices``)

    def __post_init__(self) -> None:
        self.m_samples.m_on_read = self._sample_read
        self.m_samples.m_on_change = self._sample_changed
        self.m_memo.m_owner_of = lambda mk: mk[2] if isinstance(mk, tuple) and len(mk) > 2 else None

    def _sample_read(self, key: str) -> None:
        if key.endswith("/"):
            self.m_prefixes.add(key)
        self.m_memo.note(("sample", key))

    def _sample_changed(self, key: str, made: bool) -> None:
        """A sample value changed: drop what read it, and the lists whose count it may change. A value made up
        changes nothing read before: every binding reads its own placeholder (``Scope._follow``), and a list counts
        only values not made up."""
        if made:
            return
        self.m_memo.forget(("sample", key))
        for prefix in [known_prefix for known_prefix in self.m_prefixes if key.startswith(known_prefix)]:
            self.m_memo.forget(("sample", prefix))

    def forget(self, root: dict | None = None) -> None:
        """Drop what was derived from ``root`` (an edit changed it), or from every file."""
        self.m_node_values.clear()
        if root is None:
            self.m_roots.clear()
            self.m_memo.clear()
            self.m_palettes.clear()
            self.m_prefixes.clear()
        else:
            self.m_roots.pop(id(root), None)
            self.m_roots = {
                k: entry for k, entry in self.m_roots.items() if all(type_id != id(root) for _, type_id in entry.types)
            }  # its type
            self.m_memo.forget(id(root))
            self.m_palettes = {guid: palette for guid, palette in self.m_palettes.items() if palette[0] is not root}

    def conditions(self) -> tuple:
        """What the kept results depend on besides the files and the sample values: the view's switches."""
        return (id(self.m_files), self.m_language, self.m_design, self.m_editor_hidden, self.m_window_aspect, id(self.m_samples),
                self.m_samples.m_fill, tuple(sorted(self.m_live_text.items())), repr(sorted(self.m_live_values.items())),
                previewdefaults.current(self).key(), gamedata.stamp(self), id(self.m_baseline))  # fmt: skip

    def live_of(self, element: elements.SElement) -> dict[tuple[int, ...], object]:
        """What the running game's widget of this element shows, as property values (by instance GUID, else by the
        name CRC); empty without held values."""
        return elements.held_for(self.m_live_values, element) or {}

    def root(self, key: str | None) -> dict | None:
        """The decoded file ``key`` (``files.root``); what is being computed now depends on it."""
        found = self.m_files.root(key)
        if found is not None:
            self.m_memo.note(id(found))
            self.m_memo.note(("file", key))  # whichever tree of it was read (``forget_file``)
        return found

    def forget_file(self, key: str) -> None:
        """Drop what read the file ``key`` in any of its trees: the game's decoded one when the file is opened (its
        edits are made in a tree of its own)."""
        self.m_node_values.clear()
        self.m_palettes.pop(key, None)
        self.m_roots = {
            k: entry for k, entry in self.m_roots.items() if all(file_key != key for file_key, _ in entry.types)
        }
        self.m_memo.forget(("file", key))

    def values(self, node: dict) -> dict[tuple[int, ...], object]:
        """``fields.property_values`` of an instance, kept until an edit (shared: do not change), with what the
        game gives the components it fills from the player's state; the History Editions' ``DynamicProperties`` give
        the placed file's properties by their AtomPropertyId."""
        hit = self.m_node_values.get(id(node))
        if hit is not None and hit[0] is node:
            return hit[1]
        out = property_values(node)
        comps = arrange.components(node)  # what the game fills from the player's state (previewdefaults)
        if comps:
            out.update(previewdefaults.component_values(comps, previewdefaults.current(self)))
            previewdefaults.resolve_portrait(out, comps, previewdefaults.current(self))
        dynamic = fields.dynamic_properties(node)
        placed = self.root(fields.get(node, "Template")) if dynamic else None
        atoms = (root_info(self, placed).atoms or {}) if placed is not None else {}
        for atom, value in dynamic:
            if atom in atoms and value is not None:
                out.setdefault((atoms[atom],), value)
        self.m_node_values[id(node)] = (node, out)
        return out

    def text_size(self, text: str, style: STextStyle, width_px: float) -> tuple[float, float]:
        if self.m_measure is not None:
            return self.m_measure(text, style, width_px)
        per_line = max(1, int(width_px / (style.size * 0.5))) if width_px > 0 and style.wrap else 10**6
        lines = sum(max(1, math.ceil(len(line) / per_line)) for line in text.split("\n")) if text else 1
        longest = max((len(line) for line in text.split("\n")), default=0)
        return (width_px if width_px > 0 and style.wrap else longest * style.size * 0.5), lines * style.size * 1.25

    def palette(self, guid: str) -> dict[int, tuple[int, int, int]]:
        """A palette file's colours by entry id."""
        if guid not in self.m_palettes:
            root = self.m_files.root(guid)
            out = {}
            for entry in phoenixedit.objects(root, "Colors") if root is not None else []:
                colour_list = phoenixedit.objects(entry, "Color")
                rgb = (
                    tuple(
                        int(fields.get(colour_list[0], field_name, 255))
                        for field_name in ("5767df55", "3aba3bbe", "4ad0cf31")
                    )
                    if colour_list
                    else (255,) * 3
                )
                out[int(fields.get(entry, "46a17a33", 0) or 0)] = rgb
            self.m_palettes[guid] = (root, out)
        root, colours = self.m_palettes[guid]
        if root is not None:
            self.m_memo.note(id(root))
            self.m_memo.note(("file", guid))  # as ``root`` notes it: the palette opened later drops what read it
        return colours

    def tint(self, node: dict, name: str = "ColorTint") -> tuple[int, int, int]:
        found = fields.color_of(node, name)
        if found is None:
            return (255, 255, 255)
        palette, entry, rgb = found
        return self.palette(palette).get(entry, rgb) if palette else rgb
