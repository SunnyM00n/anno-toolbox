"""Every widget of a template placed in the reference space and turned into draw items, with the template's
bindings evaluated over the sample values, so the preview draws what the game would show for them.

``place(key, root, ctx)`` is the entry: a ``Frame`` of ``DrawItem`` (pictures, texts, videos, empty boxes) and the
box of each element of the open file. The rules are docs/ui/117/layout.md (anchor, pivot, scale as size, the cancel
modes, the size of a container, stacks, labels) and clipping-and-masks.md; what is inferred is marked there.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from ..formats import phoenixedit
from ..infra.i18n import tr
from . import arrange, effects, elements, fields, labels, listlayout, loaders
from .fields import property_values
from .labels import TextStyle
from .memo import MISS, Memo
from .scope import SIZE, GameObjectRef, ListSlot, RootInfo, Scope, canon, inputs_key, inputs_of, root_info
from .viewmodels import Samples

Matrix = tuple[float, float, float, float, float, float]  # x' = a x + c y + tx, y' = b x + d y + ty
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_DEPTH = 32  # files inside files: 117's scenes nest up to 24 (statisticsscene); cycles are cut by themselves
PPP = "c7b1e237"  # ProportionalPivotPoint, by the first part of its GUID
PPP_VALUE = 1965950531  # its own property (the Widget Library)
SIZE_OVERRIDE, OVERRIDE_SIZE = "72b40010", 0x76C0BADC  # SizeOverride: the size a widget reports
FIXED_SIZE = "072d960f"  # a template that keeps its ReferenceSize as its size (inferred: layout.md "ConsiderSize")
VIDEO_SIZE = (640.0, 360.0)
HIDDEN_ALPHA = 0.01
REPLACE = 3  # placings of one frame at most: a value seeded or made up late drops what read it before
EFFECTED = ("image", "container", "group")  # the kinds whose effects the preview reads
VISIBLE, IMAGE_ID = 0x80000007, 0x8000002A


def mul(m: Matrix, n: Matrix) -> Matrix:
    """``m`` after ``n`` (a point goes through ``n`` first)."""
    a, b, c, d, tx, ty = m
    e, f, g, h, ux, uy = n
    return (a * e + c * f, b * e + d * f, a * g + c * h, b * g + d * h, a * ux + c * uy + tx, b * ux + d * uy + ty)


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def translate(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def scale(sx: float, sy: float) -> Matrix:
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def rotate(degrees: float) -> Matrix:
    r = math.radians(degrees)
    return (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)


def invert(m: Matrix) -> Matrix:
    a, b, c, d, tx, ty = m
    det = a * d - b * c or 1e-12
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty))


def bounds(m: Matrix, w: float, h: float) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the local rectangle 0..w x 0..h after ``m``."""
    pts = [apply(m, x, y) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


@dataclass
class Clip:
    """A mask over a draw item: its rectangle, and for a soft mask its picture and the soft mask's values."""

    matrix: Matrix
    size: tuple[float, float]
    image: str | None = None  # the mask's image asset
    image_id: str | None = None
    soft: dict | None = None  # SoftMaskParams' values: the picture's alpha, smooth-stepped, multiplies


@dataclass
class DrawItem:
    kind: str  # image, text, video, box
    matrix: Matrix
    size: tuple[float, float]
    color: tuple[int, int, int] = (255, 255, 255)
    alpha: float = 1.0
    blend: int = 0
    image: str | None = None  # an ImageAsset's GUID
    image_id: str | None = None  # a picture named at run time (DynamicImageId)
    nine: tuple[float, float, float, float] | None = None  # the border as fractions: left, top, right, bottom
    uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # offset x, y, scale x, y
    text: str = ""
    style: TextStyle | None = None
    clips: list[Clip] = field(default_factory=list)
    effects: dict[str, dict] = field(default_factory=dict)  # the picture's effects (effects.drawn)
    owner: int = 0  # id() of the element of the open file the item belongs to
    mask: bool = False  # a mask widget: not drawn, it clips the masked siblings after it


@dataclass
class Box:
    matrix: Matrix
    size: tuple[float, float]
    visible: bool = True
    counts: bool = True  # ConsiderSize: whether its container measures it
    parent: Matrix = IDENTITY  # the parent's matrix: a move in the preview is a move in the parent's units


@dataclass
class Frame:
    size: tuple[float, float]
    items: list[DrawItem] = field(default_factory=list)
    boxes: dict[int, Box] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Paint:
    """What a widget hands down to everything under it: colour, alpha, the masks that cover it and its effects."""

    color: tuple[int, int, int] = (255, 255, 255)
    alpha: float = 1.0
    clips: tuple[Clip, ...] = ()
    effects: dict = field(default_factory=dict)  # drawn effects of the containers above (effects.drawn)


@dataclass(frozen=True)
class TemplateRun:
    """How one template is placed: ``measure_only`` (boxes only, nothing painted), the ``stack`` of template keys
    above it, the ``owner`` element, the ``paint`` it inherits and ``data_owner``, the key a list's data is read by."""

    measure_only: bool = False
    stack: tuple = ()
    owner: object = None
    paint: Paint | None = None
    data_owner: str | None = None


@dataclass
class Child:
    """A child of a group or template with its own values, before the group's rule moves it."""

    el: elements.Element
    overrides: dict
    visible: bool
    counts: bool
    inputs: dict
    slot: arrange.Slot


@dataclass
class Context:
    """What placing needs besides the file: the other files, the texts, the sample values, text measuring."""

    files: object  # uifiles.UiFiles
    samples: Samples = field(default_factory=Samples)
    measure: Callable[[str, TextStyle, float], tuple[float, float]] | None = None
    fonts: Callable[[str | None, str], tuple[str | None, float]] | None = None  # a localized font -> (family, scale)
    language: str = "english"
    palettes: dict[str, tuple[dict | None, dict[int, tuple[int, int, int]]]] = field(default_factory=dict)
    roots: dict[int, RootInfo] = field(default_factory=dict)  # what each file gives once (``forget`` after an edit)
    memo: Memo = field(default_factory=Memo)  # scopes, measures and placed files kept between frames
    epoch: tuple | None = None  # what the kept results were made under (``place`` starts over when it changes)
    design: bool = True  # the design view: what an animation fades in is shown (ui-editor.md "The preview")
    editor_hidden: bool = False  # draw the layers the author hid in the editor (``IsHidden``) too
    animation: tuple[str, float] | None = None  # (animation GUID, seconds) of the open file played at that time
    tweens: dict[str, dict[tuple[int, ...], object]] | None = None  # the open file's tweens at a time (tweens.writes)
    live_text: dict[str, str] = field(default_factory=dict)  # the running game's label texts by instance GUID (U8)
    node_values: dict[int, tuple[dict, dict]] = field(default_factory=dict)  # id(node) -> (node, ``values``)
    prefixes: set[str] = field(default_factory=set)  # the sample key prefixes a list counted its elements under

    def __post_init__(self) -> None:
        self.samples.on_read = self._sample_read
        self.samples.on_change = self._sample_changed
        self.memo.owner_of = lambda mk: mk[2] if isinstance(mk, tuple) and len(mk) > 2 else None

    def _sample_read(self, key: str) -> None:
        if key.endswith("/"):
            self.prefixes.add(key)
        self.memo.note(("sample", key))

    def _sample_changed(self, key: str, made: bool) -> None:
        """A sample value changed: drop what read it, and the lists whose count it may change. A value made up
        changes nothing read before: every binding reads its own placeholder (``Scope._follow``), and a list counts
        only values not made up."""
        if made:
            return
        self.memo.forget(("sample", key))
        for prefix in [p for p in self.prefixes if key.startswith(p)]:
            self.memo.forget(("sample", prefix))

    def forget(self, root: dict | None = None) -> None:
        """Drop what was derived from ``root`` (an edit changed it), or from every file."""
        self.node_values.clear()
        if root is None:
            self.roots.clear()
            self.memo.clear()
            self.palettes.clear()
            self.prefixes.clear()
        else:
            self.roots.pop(id(root), None)
            self.roots = {k: v for k, v in self.roots.items() if all(t != id(root) for _, t in v.types)}  # its type
            self.memo.forget(id(root))
            self.palettes = {g: p for g, p in self.palettes.items() if p[0] is not root}

    def conditions(self) -> tuple:
        """What the kept results depend on besides the files and the sample values: the view's switches."""
        return (id(self.files), self.language, self.design, self.editor_hidden, id(self.samples), self.samples.fill,
                tuple(sorted(self.live_text.items())))  # fmt: skip

    def root(self, key: str | None) -> dict | None:
        """The decoded file ``key`` (``files.root``); what is being computed now depends on it."""
        found = self.files.root(key)
        if found is not None:
            self.memo.note(id(found))
            self.memo.note(("file", key))  # whichever tree of it was read (``forget_file``)
        return found

    def forget_file(self, key: str) -> None:
        """Drop what read the file ``key`` in any of its trees: the game's decoded one when the file is opened (its
        edits are made in a tree of its own)."""
        self.node_values.clear()
        self.palettes.pop(key, None)
        self.roots = {k: v for k, v in self.roots.items() if all(t != key for t, _ in v.types)}
        self.memo.forget(("file", key))

    def values(self, node: dict) -> dict[tuple[int, ...], object]:
        """``fields.property_values`` of an instance, kept until an edit (shared: do not change); the History
        Editions' ``DynamicProperties`` give the placed file's properties by their AtomPropertyId."""
        hit = self.node_values.get(id(node))
        if hit is not None and hit[0] is node:
            return hit[1]
        out = property_values(node)
        dynamic = fields.dynamic_properties(node)
        placed = self.root(fields.get(node, "Template")) if dynamic else None
        atoms = (root_info(self, placed).atoms or {}) if placed is not None else {}
        for atom, value in dynamic:
            if atom in atoms and value is not None:
                out.setdefault((atoms[atom],), value)
        self.node_values[id(node)] = (node, out)
        return out

    def text_size(self, text: str, style: TextStyle, width: float) -> tuple[float, float]:
        if self.measure is not None:
            return self.measure(text, style, width)
        per_line = max(1, int(width / (style.size * 0.5))) if width > 0 and style.wrap else 10**6
        lines = sum(max(1, math.ceil(len(line) / per_line)) for line in text.split("\n")) if text else 1
        longest = max((len(line) for line in text.split("\n")), default=0)
        return (width if width > 0 and style.wrap else longest * style.size * 0.5), lines * style.size * 1.25

    def palette(self, guid: str) -> dict[int, tuple[int, int, int]]:
        """A palette file's colours by entry id."""
        if guid not in self.palettes:
            root = self.files.root(guid)
            out = {}
            for entry in phoenixedit.objects(root, "Colors") if root is not None else []:
                c = phoenixedit.objects(entry, "Color")
                rgb = (
                    tuple(int(fields.get(c[0], h, 255)) for h in ("5767df55", "3aba3bbe", "4ad0cf31"))
                    if c
                    else (255,) * 3
                )
                out[int(fields.get(entry, "46a17a33", 0) or 0)] = rgb
            self.palettes[guid] = (root, out)
        root, colours = self.palettes[guid]
        if root is not None:
            self.memo.note(id(root))
            self.memo.note(("file", guid))  # as ``root`` notes it: the palette opened later drops what read it
        return colours

    def tint(self, node: dict, name: str = "ColorTint") -> tuple[int, int, int]:
        found = fields.color_of(node, name)
        if found is None:
            return (255, 255, 255)
        palette, entry, rgb = found
        return self.palette(palette).get(entry, rgb) if palette else rgb


def paint_key(paint: Paint) -> tuple:
    """What of a ``Paint`` decides the items drawn under it (the masks as their rectangles and pictures)."""
    clips = tuple((c.matrix, c.size, c.image, c.image_id, repr(c.soft)) for c in paint.clips)
    return paint.color, paint.alpha, clips, repr(sorted(paint.effects.items()))


def vector(value, default: tuple) -> tuple:
    """``value`` as a vector of floats as long as ``default`` (a missing or unusable part from ``default``)."""
    parts = list(value) if isinstance(value, (tuple, list)) else []
    out = []
    for i, d in enumerate(default):
        v = parts[i] if i < len(parts) else d
        out.append(float(v) if isinstance(v, (int, float)) else float(d))
    return tuple(out)


def scalar(value, default: float) -> float:
    """``value`` as one number (the first part of a vector, ``default`` for anything else)."""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else default
    return float(value) if isinstance(value, (int, float)) else float(default)


def cancel(modes: dict[str, int], pos, sc, ancestors: tuple[float, float]):
    """Position and scale with the modes that undo the ancestors' scale applied: Fixed (1) and FixedToParent (2,
    taken as 1 here) per axis, and the aspect ratio (1 fit the smaller axis, 2 follow the width, 3 the height)
    (docs/ui/117/layout.md "Position, anchor and pivot")."""
    ax, ay = (max(abs(a), 1e-6) for a in ancestors)
    pos, sc = list(pos), list(sc)
    for i, (mode, s) in enumerate((("XRepositioning", ax), ("YRepositioning", ay))):
        if modes[mode]:
            pos[i] = float(pos[i]) / s
    for i, (mode, s) in enumerate((("WidthResizing", ax), ("HeightResizing", ay))):
        if modes[mode]:
            sc[i] = float(sc[i]) / s
    aspect = modes["AspectRatio"]
    if aspect == 1:
        low = min(ax, ay)
        sc[0], sc[1] = float(sc[0]) * low / ax, float(sc[1]) * low / ay
    elif aspect == 2:
        sc[1] = float(sc[1]) * ax / ay
    elif aspect == 3:
        sc[0] = float(sc[0]) * ay / ax
    return tuple(pos), tuple(sc)


def is_size(value) -> bool:
    """Whether a value is a usable size (two numbers or more)."""
    return isinstance(value, (tuple, list)) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2])


class Placer:
    """Walks the open file and the templates its containers place, writing draw items into a frame."""

    def __init__(self, ctx: Context, frame: Frame) -> None:
        self.ctx, self.frame = ctx, frame
        self.stack: list[str] = []

    # sizes

    def natural_size(self, el: elements.Element, overrides: dict, scope_inputs: dict,
                     owner: str | None = None) -> tuple[float, float]:  # fmt: skip
        """An element's size before its scale; ``owner``: a container's instance (a list's elements are its)."""
        node = el.node
        size = overrides.get((SIZE,))
        if is_size(size):
            return float(size[0]), float(size[1])
        if el.kind == "image":
            img = self.ctx.root(fields.get(node, "Image"))
            return tuple(fields.get(img, "Size", (64.0, 64.0))) if img is not None else (64.0, 64.0)
        if el.kind == "label":
            return labels.label(self.ctx, node, overrides)[2]
        if el.kind == "video":
            return VIDEO_SIZE
        if el.kind == "container":
            root = self.ctx.root(el.template)
            return self.template_size(root, el.template or "", scope_inputs, owner) if root is not None else (0.0, 0.0)
        return (0.0, 0.0)

    def template_size(self, root: dict, key: str, inputs: dict, owner: str | None = None) -> tuple[float, float]:
        ref = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        if fields.get(root, FIXED_SIZE):
            return ref
        return self.measure(key, root, inputs, owner)[0]

    def measure(self, key: str, root: dict, inputs: dict,
                owner: str | None = None) -> tuple[tuple[float, float], dict[int, object]]:  # fmt: skip
        """A widget file placed with ``inputs``: the union of what counts for its size, and its own properties'
        values after its bindings (what a binding of the file that places it can read). A list is measured with the
        elements of the instance ``owner``, as it is drawn."""
        ref = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        if self._cut(key):
            return ref, {}
        owner = owner if listlayout.list_kind(self.ctx, root) is not None else None  # only a list's size is its own

        def make():
            sub = Frame(ref)
            run = TemplateRun(True, self.stack, data_owner=owner)
            scope = Placer(self.ctx, sub)._template(key, root, IDENTITY, inputs, run)
            boxes = [bounds(b.matrix, *b.size) for b in sub.boxes.values() if b.visible and b.counts]
            size = (max(0.0, max(b[2] for b in boxes)), max(0.0, max(b[3] for b in boxes))) if boxes else (0.0, 0.0)
            return size, scope.computed()

        # the open file names the sample keys; a file that reads its own size while it is measured gets the reference
        mk = ("size", id(root), key, inputs_key(self.ctx.samples, inputs), tuple(self.stack[:1]), self._elements(owner))
        return self._kept(mk, root, make, provisional=(ref, {}))

    def _elements(self, owner: str | None) -> tuple | None:
        """A list instance's elements as a part of what is kept: two lists whose elements hold no given value measure
        alike (``canon``)."""
        if owner is None:
            return None
        n = listlayout.element_count(self.ctx.samples, owner)
        return tuple(canon(self.ctx.samples, GameObjectRef(listlayout.element_key(owner, i))) for i in range(n))

    def _cut(self, key: str) -> bool:
        """Whether placing ``key`` here is cut short: it places itself (a file already being placed above) or the
        files are nested too deep. What is computed inside that file's placing then depends on where it was asked
        from: it is kept by its key and the stack of files (``Memo.cut``, ``_kept``)."""
        if key in self.stack:
            self.ctx.memo.cut(key)
            return True
        if len(self.stack) >= MAX_DEPTH:
            self.ctx.memo.cut(None)
            return True
        return False

    def _kept(self, mk: tuple, root: dict, make, provisional=MISS):
        """``make()`` once per ``mk`` while nothing it read changes (``memo``); a result holding a cut, once per
        ``mk`` and stack of files (what takes it holds the cut too: it reads the cut's token)."""
        memo, here = self.ctx.memo, tuple(self.stack)
        hit = memo.get(mk)
        if hit is MISS:
            hit = memo.get((mk, here))
        return memo.compute(mk, id(root), make, provisional, here) if hit is MISS else hit

    def size_of(self, node: dict, scope: Scope) -> tuple[float, float]:
        """The size a binding reads from an instance of ``scope``'s template (``scope.Host``)."""
        el = scope.by_node.get(id(node))
        if el is None:
            return (0.0, 0.0)
        overrides = {**self.ctx.values(node), **scope.writes.get(el.guid, {})}
        if el.kind == "group":
            return self.group_size(el, scope, tuple(fields.get(scope.root, "ReferenceSize", (1920.0, 1080.0))))
        return self.natural_size(el, overrides, inputs_of(overrides), f"{scope.key}:{el.guid}")

    def outputs(self, node: dict, scope: Scope) -> dict[int, object]:
        """The own property values of the widget file an instance places, given what the instance passes in."""
        el = scope.by_node.get(id(node))
        if el is None or el.kind != "container" or not el.template:
            return {}
        root = self.ctx.root(el.template)
        if root is None:
            return {}
        overrides = {**self.ctx.values(node), **scope.writes.get(el.guid, {})}
        key, inputs = el.template, inputs_of(overrides)
        if self._cut(key):
            return {}
        # the file's bindings alone, not its whole placing: what the measure of its size would evaluate first
        sub = Placer(self.ctx, Frame(self.frame.size))
        sub.stack = [*self.stack, key]
        sub._seed(key, root)
        owner = f"{self.stack[0]}:{key}" if self.stack else key
        return sub.scope(key, root, inputs, owner, kept=bool(self.stack)).computed()

    def group_size(self, el: elements.Element, scope: Scope, parent_size: tuple[float, float]) -> tuple[float, float]:
        """The union of the rectangles of a group's children that count for its size, from its own origin."""
        sub = Placer(self.ctx, Frame(parent_size))
        sub.stack = self.stack
        rule = self.group_rule(el, scope)
        sub._children(el, scope, IDENTITY, parent_size, None, True, Paint(), rule)
        content = el.children[1:] if rule is not None and rule.background is not None else el.children
        kids = [sub.frame.boxes.get(id(k.node)) for k in content]  # a background is not content
        rects = [bounds(b.matrix, *b.size) for b in kids if b is not None and b.visible and b.counts]
        return (max(0.0, max(r[2] for r in rects)), max(0.0, max(r[3] for r in rects))) if rects else (0.0, 0.0)

    def group_rule(self, el: elements.Element, scope: Scope) -> arrange.Rule | None:
        """The stack and background rule of a group instance, None without either component."""
        overrides = {**self.ctx.values(el.node), **scope.writes.get(el.guid, {})}
        return arrange.read_rule(arrange.components(el.node), overrides)

    # placing

    def local(
        self, el, overrides, size, parent_size, pivot_fraction=None, ancestors=(1.0, 1.0), stretch=None
    ) -> Matrix:
        node = el.node

        def val(uid, name, default):
            v = overrides.get((uid,))
            v = v if v is not None else fields.get(node, name, default)
            return vector(v, default) if isinstance(default, tuple) else v

        pos = val(0x80000001, "Position", (0.0, 0.0, 0.0))
        pivot = val(0x80000002, "PivotPoint", (0.0, 0.0, 0.0))
        if pivot_fraction is not None:
            fx, fy = vector(pivot_fraction, (0.0, 0.0))
            pivot = (fx * size[0], fy * size[1])
        sc = val(0x80000035, "Scale", (1.0, 1.0, 1.0))
        if stretch is not None:  # a picture given a size is scaled to it, its pivot (in picture units) with it
            sc = (*stretch, 1.0)
        elif is_size(overrides.get((SIZE,))) and (0x80000035,) not in overrides:  # a bound size is the size itself
            sc = (1.0, 1.0, 1.0)
        modes = fields.anchor_fields(node)  # 117's own, or what the History Editions' anchor constraints say
        pos, sc = cancel(modes, pos, sc, ancestors)
        angle = scalar(val(0x80000003, "RotationAngle", 0.0), 0.0)
        anchor = modes["AnchorPoint"]
        ax, ay = (anchor % 3) / 2, (anchor // 3) / 2
        m = translate(parent_size[0] * ax + float(pos[0]), parent_size[1] * ay + float(pos[1]))
        m = mul(m, rotate(angle))
        m = mul(m, scale(float(sc[0]), float(sc[1])))
        return mul(m, translate(-float(pivot[0]), -float(pivot[1])))

    def _template(self, key, root, matrix, inputs, run: TemplateRun | None = None) -> Scope:
        run = run or TemplateRun()
        measure_only, owner, data_owner = run.measure_only, run.owner, run.data_owner
        paint = run.paint or Paint()
        self.stack = [*run.stack, key]
        info = self._seed(key, root)
        owner_key = key if not run.stack else f"{run.stack[0]}:{key}"
        scope = self.scope(key, root, inputs, owner_key, kept=bool(run.stack))
        size = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        top = info.top
        loader = loaders.loader_kind(self.ctx, root)
        if loader is not None:  # a loader instantiates one of its children
            top = SimpleNamespace(children=loaders.chosen(loader, top.children, inputs, self.ctx.design))
        kind = listlayout.list_kind(self.ctx, root)
        if kind is None or not top.children:
            self._children(top, scope, matrix, size, owner, measure_only, paint)
            return scope
        item = top.children[0]  # a list file holds its item template as its one child
        data = data_owner or owner_key
        n = listlayout.element_count(self.ctx.samples, data) if data_owner else listlayout.PLACEHOLDER_ITEMS
        first = self.scope(key, root, inputs, owner_key,
                           ListSlot(GameObjectRef(listlayout.element_key(data, 0)), 0, item.guid), bool(run.stack))  # fmt: skip
        for i, (dx, dy) in enumerate(listlayout.offsets(kind, inputs, n, self.size_of(item.node, first))):
            slot = ListSlot(GameObjectRef(listlayout.element_key(data, i)), i, item.guid)
            copy = first if i == 0 else self.scope(key, root, inputs, owner_key, slot, bool(run.stack))
            self._children(SimpleNamespace(children=[item]), copy, mul(matrix, translate(dx, dy)), size, owner,
                           measure_only, paint)  # fmt: skip
            box = self.frame.boxes.get(id(item.node))
            if box is not None and i:  # each copy measures; the first keeps the item's own key
                self.frame.boxes[hash((id(item.node), i))] = box
        return scope

    def _seed(self, key: str, root: dict) -> RootInfo:
        """The file's own preview values for its lists into the samples, as the file holds them now."""
        info = root_info(self.ctx, root)
        if info.seeds is None:  # the file is new or was edited
            info.seeds = listlayout.preview_values(info.top, key)
            self.ctx.samples.unseed(f"{key}:", info.seeds)
        for k, v in info.seeds.items():
            self.ctx.samples.seed(k, v)
        return info

    def scope(self, key: str, root: dict, inputs: dict, owner_key: str, slot: ListSlot | None = None,
              kept: bool = True) -> Scope:  # fmt: skip
        """The bindings of ``key`` evaluated over ``inputs``, once however many instances place it (``kept`` False: the
        open file, whose animation and tweens change between frames)."""
        if not kept:
            return Scope(key, root, inputs, self.ctx, owner_key, self, slot)

        def make():
            scope = Scope(key, root, inputs, self.ctx, owner_key, self, slot)
            scope.host = None  # evaluated: a kept scope must not hold this placing's frames
            return scope

        shared = slot and replace(slot, element=canon(self.ctx.samples, slot.element))
        return self._kept(("scope", id(root), key, inputs_key(self.ctx.samples, inputs), owner_key, shared), root, make)

    def stretch_to(self, el, overrides: dict, inputs: dict):
        """For a picture or video whose size a binding or the instance gives: (the scale that makes the picture that
        size, the picture's own size), else None. The game's ``Size`` is the picture's size times its scale, so
        writing it scales the picture and its pivot (docs/ui/117/layout.md "Position, anchor and pivot")."""
        bound = overrides.get((SIZE,))
        if el.kind not in ("image", "video") or not is_size(bound) or (0x80000035,) in overrides:
            return None
        base = self.natural_size(el, {k: v for k, v in overrides.items() if k != (SIZE,)}, inputs)
        if base[0] <= 0 or base[1] <= 0:
            return None
        return (float(bound[0]) / base[0], float(bound[1]) / base[1]), tuple(base)

    def _child(self, el: elements.Element, scope: Scope, matrix: Matrix, parent_size, sized: bool = True) -> Child:
        """One child of a group or template, placed by its own values (the group's rule comes after); ``sized``
        False: a hidden child's size is not worked out (nothing it holds is drawn or counted)."""
        overrides = dict(self.ctx.values(el.node))
        overrides.update(scope.writes.get(el.guid, {}))
        if self.ctx.design:  # a scale or size the game feeds at run time (its UI scale) stays as stored
            for uid in (0x80000035, SIZE):
                v = overrides.get((uid,))
                if isinstance(v, (tuple, list)) and any(isinstance(x, (int, float)) and abs(x) < 1e-6 for x in v[:2]):
                    del overrides[(uid,)]
        visible = overrides.get((VISIBLE,))
        if visible is None:  # a layer the author hid in the editor (designer helpers) starts hidden
            visible = bool(fields.get(el.node, "Visible", True))
            visible = visible and (self.ctx.editor_hidden or not fields.get(el.node, "IsHidden"))
        visible = bool(visible)
        inputs = inputs_of(overrides)
        if not visible and not sized:
            return self._hidden(el, overrides, inputs, parent_size)
        size = self.natural_size(el, overrides, inputs, f"{scope.key}:{el.guid}")
        if el.kind == "group" and (SIZE,) not in overrides:  # a group is as large as what it holds
            size = self.group_size(el, scope, parent_size)
        stretch = self.stretch_to(el, overrides, inputs)
        if stretch is not None:
            size = stretch[1]
        comps = arrange.components(el.node)
        if SIZE_OVERRIDE in comps and is_size(overrides.get((comps[SIZE_OVERRIDE], OVERRIDE_SIZE))):
            size = tuple(float(v) for v in overrides[(comps[SIZE_OVERRIDE], OVERRIDE_SIZE)][:2])
        fraction = overrides.get((comps[PPP], PPP_VALUE)) if PPP in comps else None
        ancestors = (math.hypot(matrix[0], matrix[1]), math.hypot(matrix[2], matrix[3]))
        local = self.local(el, overrides, size, parent_size, fraction, ancestors, stretch and stretch[0])
        counts = overrides.get((0x80000080,))
        counts = bool(fields.get(el.node, "ConsiderSize", True) if counts is None else counts)
        pad, align = arrange.read_child(comps, overrides)
        slot = arrange.Slot(el.guid, local, size, bounds(local, *size), visible and counts, pad, align)
        return Child(el, overrides, visible, counts, inputs, slot)

    def _hidden(self, el: elements.Element, overrides: dict, inputs: dict, parent_size) -> Child:
        """A hidden child where no box of it is shown: at its place, no size (measuring a container's widget file
        would place that whole file for nothing)."""
        local = self.local(el, overrides, (0.0, 0.0), parent_size)
        return Child(
            el, overrides, False, False, inputs, arrange.Slot(el.guid, local, (0.0, 0.0), bounds(local, 0, 0), False)
        )

    def _children(self, parent, scope, matrix, parent_size, owner, measure_only, paint: Paint,
                  rule: arrange.Rule | None = None) -> None:  # fmt: skip
        order = sorted(parent.children, key=lambda e: int(fields.get(e.node, "ZIndex", 0) or 0))
        sized = owner is None and not measure_only  # the open file's boxes, the selection's outline among them
        kids = [self._child(el, scope, matrix, parent_size, sized) for el in order]
        if rule is not None:
            if rule.background is not None and kids:
                kids[0].slot.background = True  # the first layer's the background
            arrange.arrange(rule, [k.slot for k in kids])
        masks: list[Clip] = []
        for kid in kids:
            self._visit(kid, scope, matrix, owner, measure_only, paint, masks)

    def _visit(self, kid: Child, scope, matrix, owner, measure_only, paint: Paint, masks: list[Clip]) -> None:
        """Draw one child, and what it holds: a group's children, the widget file a container places."""
        el, overrides, size = kid.el, kid.overrides, kid.slot.size
        world = mul(matrix, kid.slot.local)
        me = owner if owner is not None else id(el.node)
        if owner is None:
            self.frame.boxes[id(el.node)] = Box(world, size, kid.visible, kid.counts, matrix)
        if not kid.visible:
            return
        tint = self.ctx.tint(el.node)
        c = tuple(a * b // 255 for a, b in zip(paint.color, tint, strict=True))
        own_alpha = scalar(overrides.get((0x80000006,), fields.get(el.node, "AlphaTint", 1.0)), 1.0)
        if self.ctx.design and own_alpha < HIDDEN_ALPHA:  # faded in by an animation in the game
            own_alpha = 1.0
        is_mask = int(fields.get(el.node, "MaskMode", 0) or 0) != 0
        receives = bool(fields.get(el.node, "c90bf7fc"))  # "receives the mask" (clipping-and-masks.md)
        clips = [*paint.clips, *masks] if receives else list(paint.clips)
        if not is_mask and not receives:
            masks.clear()  # a sibling that does not take the masks ends their run
        fx = effects.read(el.node, overrides, self.ctx.root) if el.kind in EFFECTED else {}
        below = replace(paint, color=c, alpha=paint.alpha * own_alpha, clips=tuple(clips),
                        effects={**paint.effects, **effects.drawn(fx)} if el.kind != "image" else paint.effects)  # fmt: skip
        if not measure_only:
            self._emit(el, world, size, below, me, overrides, is_mask, fx if el.kind == "image" else {})
        if is_mask:
            soft = fx.get("soft_mask")
            soft = soft if soft is not None and soft.get("Enabled", True) else None
            dyn = overrides.get((IMAGE_ID,), fields.get(el.node, "DynamicImageId")) or None
            masks.append(Clip(world, size, fields.get(el.node, "Image"), dyn if isinstance(dyn, str) else None, soft))
        if el.kind == "container":
            self._place_file(kid, scope, world, owner, measure_only, below, me)
        elif el.kind == "group":
            self._children(el, scope, world, size, owner, measure_only, below, self.group_rule(el, scope))
            if owner is None:  # a group's own box: its children's bounds
                self._group_box(el, world)

    def _place_file(self, kid: Child, scope, world, owner, measure_only, paint: Paint, me) -> None:
        """The widget file a container places, placed at the container with the values it passes in."""
        el = kid.el
        if not el.template or len(self.stack) >= MAX_DEPTH:
            return
        if el.template in self.stack:
            self.frame.notes.append(
                tr("{name}: places {template} inside itself; not followed", name=el.name, template=el.template)
            )
            return
        root = self.ctx.root(el.template)
        if root is None:
            self.frame.notes.append(
                tr("{name}: the widget file {template} is not in the game", name=el.name, template=el.template)
            )
        elif not measure_only:
            run = TemplateRun(stack=self.stack, owner=me, paint=paint, data_owner=f"{scope.key}:{el.guid}")
            mk = ("draw", id(root), el.template, inputs_key(self.ctx.samples, kid.inputs), world, me, run.data_owner,
                  paint_key(paint), tuple(self.stack))  # fmt: skip

            def make():
                sub = Frame(self.frame.size)
                Placer(self.ctx, sub)._template(el.template, root, world, kid.inputs, run)
                return sub.items, sub.notes

            items, notes = self._kept(mk, root, make)  # placed the same way before: the same items
            self.frame.items.extend(items)
            self.frame.notes.extend(notes)

    def _group_box(self, el: elements.Element, world: Matrix) -> None:
        kids = [self.frame.boxes.get(id(k.node)) for k in el.children]
        rects = [bounds(b.matrix, *b.size) for b in kids if b is not None and b.visible]
        if rects:
            inv = invert(world)
            x1 = max(apply(inv, r[2], r[3])[0] for r in rects)
            y1 = max(apply(inv, r[2], r[3])[1] for r in rects)
            self.frame.boxes[id(el.node)] = Box(world, (max(x1, 1.0), max(y1, 1.0)), True)

    def _emit(self, el, world, size, paint: Paint, owner, overrides, is_mask, fx=None) -> None:
        node = el.node
        color, alpha, clips = paint.color, paint.alpha, list(paint.clips)
        blend = int(fields.get(node, "BlendingMode", 0) or 0)
        if el.kind == "image":
            img_guid = fields.get(node, "Image")
            uvo = vector(overrides.get((0x80000038,), fields.get(node, "UVOffset")), (0.0, 0.0))
            uvs = vector(overrides.get((0x80000039,), fields.get(node, "UVScale")), (1.0, 1.0))
            dyn = overrides.get((IMAGE_ID,), fields.get(node, "DynamicImageId")) or None
            dyn = dyn if isinstance(dyn, str) else None
            fx = effects.drawn({**paint.effects, **(fx or {})})  # what a container passes down, then the picture's own
            self.frame.items.append(DrawItem("image", world, size, color, alpha, blend, img_guid, dyn,
                                             self._nine_slice(img_guid), (*uvo, *uvs), clips=clips, owner=owner,
                                             mask=is_mask, effects=fx))  # fmt: skip
        elif el.kind == "label":
            text, style, _ = labels.label(self.ctx, node, overrides)
            style.color = tuple(a * b // 255 for a, b in zip(style.color, color, strict=True))
            self.frame.items.append(DrawItem("text", world, size, color, alpha, blend, text=text, style=style,
                                             clips=clips, owner=owner))  # fmt: skip
        elif el.kind == "video":
            self.frame.items.append(DrawItem("video", world, size, color, alpha, blend, clips=clips, owner=owner))

    def _nine_slice(self, image: str | None) -> tuple[float, float, float, float] | None:
        """The border of an image asset as fractions (left, top, right, bottom), None for a plain picture."""
        img = self.ctx.root(image)
        ns = phoenixedit.objects(img, "NineSliceScaling") if img is not None else []
        if not ns:
            return None
        tl = fields.get(ns[0], "TopLeft", (0.0, 0.0))
        br = fields.get(ns[0], "BottomRight", (1.0, 1.0))
        if tuple(tl) == (0.0, 0.0) and tuple(br) == (1.0, 1.0):
            return None
        return (tl[0], tl[1], 1.0 - br[0], 1.0 - br[1])


def place(key: str, root: dict, ctx: Context, inputs: dict | None = None) -> Frame:
    """The open file ``key`` (its decoded ``root``) placed: its draw items in drawing order and its elements' boxes."""
    if ctx.epoch != ctx.conditions():  # the view changed: nothing kept holds
        ctx.forget()
        ctx.epoch = ctx.conditions()
    size = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
    if elements.kind_of(root) != "template":
        return Frame(size, notes=[tr("only widget files and scenes have a preview")])
    for _ in range(REPLACE):
        ctx.memo.dropped = 0
        frame = Frame(size)
        _, read = ctx.memo.gather(lambda f=frame: Placer(ctx, f)._template(key, root, IDENTITY, inputs or {}))
        if not ctx.memo.dropped:  # no value a part of it read was made up or seeded after it was read
            break
    ctx.samples.seen = {}
    for token in read:  # what the frame read, kept results included: the sample panel lists it
        if isinstance(token, tuple) and token[0] == "sample" and not token[1].endswith("/"):
            ctx.samples.get(token[1])
    return frame
