"""A layout dump of the running game held against the preview: each widget of the open file as the game placed it
(its rectangle and whether it shows), put into the file's reference space, and every widget whose rectangle or
visibility differs from the preview's beyond a tolerance, so the preview's rules can be calibrated.

``load(path)`` reads a dump (its format: docs/ui-editor.md, "Comparing with the game"); ``compare(dump, top, frame,
tolerance)`` is the entry the window calls; its ``overlay`` is what the preview draws over the file.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import tr
from . import elements
from .layout import Frame, Matrix, bounds, invert, mul

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1
GAME, PREVIEW_ONLY, GAME_ONLY = (80, 230, 120), (255, 120, 90), (120, 160, 255)


@dataclass
class GameWidget:
    """One widget of the dump: who it is, the widget file it belongs to, where the game drew it."""

    id: int
    parent: int
    owner: int
    name: str | None
    guid: str | None
    in_file: str | None  # the widget file it is an instance of
    template: str | None  # the widget file it places (a container, the scene)
    cls: str
    world: Matrix | None
    bbox: tuple[float, float, float, float]  # x, y, w, h in its own units
    shown: bool
    visible: bool
    alpha: float
    raw: dict = field(repr=False, default_factory=dict)


@dataclass
class Dump:
    scene: str
    state: str
    stamp: str
    ui_scale: tuple[float, float]
    reference_size: tuple[float, float]
    widgets: list[GameWidget]
    path: Path | None = None


@dataclass
class Difference:
    """A widget whose game and preview disagree; ``node_id`` selects it in the preview (0: not in the file)."""

    name: str
    guid: str | None
    kind: str  # "rect", "visibility", "only in the game", "only in the preview"
    game: Rect | None
    preview: Rect | None
    delta: float  # the largest corner difference in reference pixels
    game_shown: bool | None
    preview_shown: bool | None
    node_id: int = 0


@dataclass
class Comparison:
    anchor: GameWidget | None
    differences: list[Difference]
    matched: int
    overlay: list[tuple[Rect, tuple[int, int, int]]]
    note: str = ""


def to_matrix(m: list | None) -> Matrix | None:
    """The dump's 4 x 4 row-vector matrix (p' = p M, the translation in 12 and 13) as the preview's 2D matrix."""
    if not m or len(m) < 16 or any(v is None for v in (m[0], m[1], m[4], m[5], m[12], m[13])):
        return None
    return (m[0], m[1], m[4], m[5], m[12], m[13])


def _widget(w: dict) -> GameWidget:
    lay = w.get("layout") or {}
    pos, size = lay.get("bbox_pos") or [0, 0], lay.get("bbox_size") or [0, 0]
    return GameWidget(
        id=w["id"], parent=w.get("parent", -1), owner=w.get("owner", -1), name=w.get("name"), guid=w.get("guid"),
        in_file=w.get("in"), template=w.get("template"), cls=w.get("class") or "", world=to_matrix(w.get("world")),
        bbox=(pos[0] or 0.0, pos[1] or 0.0, size[0] or 0.0, size[1] or 0.0), shown=bool(w.get("shown")),
        visible=bool(w.get("visible")), alpha=float(w.get("alpha_product") or 0.0), raw=w,
    )  # fmt: skip


def load(path: str | Path) -> Dump:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("probe") != "ui_dump" or "widgets" not in data:
        raise ValueError(tr("{path} is not a UI layout dump of the game", path=path))
    ref = data.get("reference_size") or [1920, 1080]
    scale = data.get("ui_scale") or [1.0, 1.0]
    return Dump(
        scene=data.get("scene", ""), state=data.get("state", ""), stamp=data.get("stamp", ""),
        ui_scale=(scale[0] or 1.0, scale[1] or 1.0), reference_size=(float(ref[0]), float(ref[1])),
        widgets=[_widget(w) for w in data["widgets"]], path=Path(path),
    )  # fmt: skip


LABEL_COLUMNS = (
    "name", "in_file", "AlignmentY", "AlignmentX", "TextPlane", "DisplayedOffset", "DisplayedSize", "BaselineY",
    "bbox", "Text", "HorizontalSizing", "VerticalSizing", "HorizontalOverflow", "VerticalOverflow", "Scale",
)  # fmt: skip


def _number(value) -> str:
    """A dump value for a cell: a list as its numbers joined by a space, None as empty."""
    if isinstance(value, (list, tuple)):
        return " ".join(_number(v) for v in value)
    return "" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))


def label_table(dump: Dump) -> list[dict[str, str]]:
    """The dump's labels as rows of what a label's vertical alignment is read from: the stored alignment values and
    text plane as the game reports them (``AlignmentY`` is the float the file stores as a bit pattern), where the
    shown text starts (``DisplayedOffset``), its box, its baseline, the widget's own box and the sizing and overflow
    modes, one row per label.
    Written for the calibration of the preview's text placement (docs/ui-editor.md, "Comparing with the game")."""
    rows = []
    for w in dump.widgets:
        if not w.cls.startswith("Label"):
            continue
        props = w.raw.get("props") or {}
        row = {"name": w.name or "", "in_file": w.in_file or "", "bbox": _number(list(w.bbox))}
        row.update({k: _number(props.get(k)) for k in LABEL_COLUMNS if k not in row})
        rows.append(row)
    return rows


def label_csv(dump: Dump) -> bytes:
    """``label_table`` as a CSV file (UTF-8, LF)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, LABEL_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(label_table(dump))
    return buf.getvalue().encode("utf-8")


def anchor(dump: Dump, file_guid: str | None, file_key: str = "") -> GameWidget | None:
    """The widget that places the open file: the first that names it as its template; for a scene, its root."""
    hit = next((w for w in dump.widgets if file_guid and w.template == file_guid), None)
    if hit is None and dump.widgets and file_key and file_key.lower() == dump.scene.lower():
        hit = dump.widgets[0]
    return hit


def rect_in(w: GameWidget, space: Matrix) -> Rect | None:
    """The widget's bounding box through its world matrix, then into the space ``space`` maps from."""
    if w.world is None:
        return None
    x, y, bw, bh = w.bbox
    m = mul(invert(space), mul(w.world, (1.0, 0.0, 0.0, 1.0, x, y)))
    return bounds(m, bw, bh)


def placed(dump: Dump, top_anchor: GameWidget) -> dict[str, GameWidget]:
    """The widgets of the anchor's file, by instance GUID: those it owns (groups included, nested files not)."""
    return {w.guid: w for w in dump.widgets if w.owner == top_anchor.id and w.guid and w.id != top_anchor.id}


def live_text(dump: Dump, top_guid: str | None, file_key: str = "") -> dict[str, str]:
    """What the game's labels of the open file show, by instance GUID (U8): the preview shows these in place of the
    sample values. Only a label with a text of its own; an empty text is a label the game has not filled."""
    found = anchor(dump, top_guid, file_key)
    if found is None:
        return {}
    out = {}
    for guid, w in placed(dump, found).items():
        text = (w.raw.get("props") or {}).get("Text")
        if w.cls.startswith("Label") and isinstance(text, str) and text:
            out[guid] = text
    return out


def _delta(a: Rect, b: Rect) -> float:
    return max(abs(p - q) for p, q in zip(a, b, strict=True))


def _row(
    el: elements.Element, gw: GameWidget | None, frame: Frame, space: Matrix | None, tol: float
) -> Difference | None:
    box = frame.boxes.get(id(el.node))
    pr = bounds(box.matrix, *box.size) if box is not None else None
    gr = rect_in(gw, space) if gw is not None and space is not None else None
    name = el.name if el.named or el.kind == "template" or gw is None else gw.name or el.name
    if gw is None:
        return Difference(
            name, el.guid, "only in the preview", None, pr, 0.0, None, box.visible if box else None, id(el.node)
        )
    if box is None:
        return Difference(name, el.guid, "only in the game", gr, None, 0.0, gw.shown, None, id(el.node))
    if gw.shown != box.visible:
        d = _delta(gr, pr) if gr and pr else 0.0
        return Difference(name, el.guid, "visibility", gr, pr, d, gw.shown, box.visible, id(el.node))
    if gw.shown and gr and pr and _delta(gr, pr) > tol:
        return Difference(name, el.guid, "rect", gr, pr, _delta(gr, pr), gw.shown, box.visible, id(el.node))
    return None


def compare(
    dump: Dump, top: elements.Element, frame: Frame | None, tolerance: float = 2.0, file_key: str = ""
) -> Comparison:
    """Every element of the open file against the game's widget of the same instance GUID under the anchor."""
    found = anchor(dump, top.guid, file_key)
    if found is None or frame is None or found.world is None:
        why = (
            tr("the dump has no widget that places this file")
            if found is None
            else tr("the preview or the dump has no layout")
        )
        return Comparison(found, [], 0, [], why)
    space = found.world
    game = placed(dump, found)
    rows, matched, overlay = [], 0, []
    for el in top.walk():
        if el.kind == "template" or not el.guid:
            continue
        gw = game.pop(el.guid, None)
        matched += gw is not None
        row = _row(el, gw, frame, space, tolerance)
        if row is not None:
            rows.append(row)
        gr = rect_in(gw, space) if gw is not None and gw.shown else None
        if gr is not None:
            overlay.append((gr, GAME if row is None else PREVIEW_ONLY))
    for gw in game.values():  # made at run time (a list's items) or from another version of the file
        gr = rect_in(gw, space)
        rows.append(Difference(gw.name or gw.guid or "?", gw.guid, "only in the game", gr, None, 0.0, gw.shown, None))
        if gr is not None and gw.shown:
            overlay.append((gr, GAME_ONLY))
    rows.sort(key=lambda r: (r.kind != "visibility", -r.delta))
    note = tr(
        "{matched} widget(s) matched, {differ} differ (tolerance {tolerance:g} px)",
        matched=matched,
        differ=len(rows),
        tolerance=tolerance,
    )
    return Comparison(found, rows, matched, overlay, note)
