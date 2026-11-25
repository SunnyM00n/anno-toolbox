"""Every widget of a template placed in the reference space and turned into draw items, with the template's
bindings evaluated over the sample values, so the preview draws what the game would show for them.

``place(key, root, ctx)`` is the entry: a ``SFrame`` of ``SDrawItem`` (pictures, texts, videos, empty boxes) and the
box of each element of the open file. The rules are docs/ui/117/layout.md (anchor, pivot, scale as size, the cancel
modes, the size of a container, stacks, labels) and clipping-and-masks.md; what is inferred is marked there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

from ..formats import phoenixedit
from ..infra.i18n import tr
from . import arrange, effects, elements, fields, gamedata, labels, listlayout, loaders, playstate
from .arrange import vector
from .layoutcontext import CContext
from .layoutrules import cancel, draw_order, is_mask_widget, is_size, paint_key, scalar, settled
from .layouttypes import SBox, SChild, SClip, SDrawItem, SFrame, SListed, SPaint, STemplateRun
from .matrices import IDENTITY, Matrix, apply, bounds, invert, mul, own_scale, rotate, scale, translate, union_size
from .memo import MISS
from .scope import (
    DISPLAYED,
    SIZE,
    CScope,
    SGameObjectRef,
    SListSlot,
    SRootInfo,
    canon,
    component_default,
    inputs_key,
    inputs_of,
    root_info,
)

__all__ = ["SBox", "SChild", "SClip", "CContext", "SDrawItem", "SFrame", "SListed", "SPaint", "CPlacer", "STemplateRun",
           "apply", "bounds", "cancel", "draw_order", "invert", "mul", "own_scale", "place", "translate",
           "union_size", "vector", "IDENTITY", "Matrix"]  # fmt: skip

MAX_DEPTH = 32  # files inside files: 117's scenes nest up to 24 (statisticsscene); cycles are cut by themselves
PPP = "c7b1e237"  # ProportionalPivotPoint, by the first part of its GUID
PPP_VALUE = 1965950531  # its own property (the Widget Library)
SIZE_OVERRIDE, OVERRIDE_SIZE = "72b40010", 0x76C0BADC  # SizeOverride: the size a widget reports
FIXED_SIZE = "072d960f"  # a template that keeps its ReferenceSize as its size (inferred: layout.md "ConsiderSize")
VIDEO_SIZE = (160.0, 160.0)  # the Resolution every video asset of 117 stores (layout.md "Scale is the size")
MASK_KINDS = ("image", "shape")
HIDDEN_ALPHA = 0.01
REPLACE = 3  # placings of one frame at most: a value seeded or made up late drops what read it before
EFFECTED = ("image", "container", "group")  # the kinds whose effects the preview reads
VISIBLE, IMAGE_ID = 0x80000007, 0x8000002A
POSITION, ROTATION = 0x80000001, 0x80000003
DISPLAYED_TEXT, DISPLAYED_SIZE = DISPLAYED  # a label's text as laid out (``CPlacer.displayed``)


class CPlacer:
    """Walks the open file and the templates its containers place, writing draw items into a frame."""

    def __init__(self, ctx: CContext, frame: SFrame) -> None:
        self.m_ctx, self.m_frame = ctx, frame
        self.m_stack: list[str] = []
        self.m_parent_scale = (1.0, 1.0)  # the own scale of the widget whose children are being placed
        self.m_filled: dict[str, dict] = {}  # what the game fills in a list's item being placed, by instance GUID
        self.m_trail: tuple = ()  # the placing instances down to the file being placed (``STemplateRun.trail``)
        self.m_placing = ""  # the baseline's key of the container placing the file (``baseline.CBaseline.entry``)

    # sizes

    def natural_size(self, element: elements.SElement, overrides: dict, scope_inputs: dict,
                     owner: str | None = None) -> tuple[float, float]:  # fmt: skip
        """An element's size before its scale; ``owner``: a container's instance (a list's elements are its)."""
        node = element.node
        size_px = overrides.get((SIZE,))
        if is_size(size_px):
            return float(size_px[0]), float(size_px[1])
        if element.kind == "image":
            image = self.m_ctx.root(fields.get(node, "Image"))
            return tuple(fields.get(image, "Size", (64.0, 64.0))) if image is not None else (64.0, 64.0)
        if element.kind == "label":
            return labels.label(self.m_ctx, node, overrides)[2]
        if element.kind == "video":  # its asset's Resolution, not the frames' size (layout.md "Scale is the size")
            video = self.m_ctx.root(fields.get(node, "Video"))
            return tuple(fields.get(video, "Resolution", VIDEO_SIZE)) if video is not None else VIDEO_SIZE
        if element.kind == "container":
            root = self.m_ctx.root(element.template)
            return (
                self.template_size(root, element.template or "", scope_inputs, owner)
                if root is not None
                else (0.0, 0.0)
            )
        return (0.0, 0.0)

    def template_size(self, root: dict, key: str, inputs: dict, owner: str | None = None) -> tuple[float, float]:
        reference_size = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        if fields.get(root, FIXED_SIZE):
            return reference_size
        return self.measure(key, root, inputs, owner)[0]

    def measure(self, key: str, root: dict, inputs: dict,
                owner: str | None = None) -> tuple[tuple[float, float], dict[int, object]]:  # fmt: skip
        """A widget file placed with ``inputs``: the union of what counts for its size, and its own properties'
        values after its bindings (what a binding of the file that places it can read). A list is measured with the
        elements of the instance ``owner``, as it is drawn."""
        return self._measured(key, root, inputs, owner)[:2]

    def start_of(self, key: str, root: dict, inputs: dict, owner: str | None = None) -> tuple[float, float]:
        """Where a widget file's measured box starts from its origin: the smallest corner of what counts, before or
        after the origin (the options tab's content stands above it: layout.md "ConsiderSize and the size of a
        container")."""
        if fields.get(root, FIXED_SIZE):
            return (0.0, 0.0)
        return self._measured(key, root, inputs, owner)[2]

    def _measured(self, key: str, root: dict, inputs: dict, owner: str | None = None) -> tuple:
        """``measure`` with ``start_of``: (size, own values, start)."""
        reference_size = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        if self._cut(key):
            return reference_size, {}, (0.0, 0.0)
        owner = owner if listlayout.list_kind(self.m_ctx, root) is not None else None  # only a list's size is its own

        def make():
            sub_frame = SFrame(reference_size)
            run = STemplateRun(True, self.m_stack, data_owner=owner)
            scope = CPlacer(self.m_ctx, sub_frame)._template(key, root, IDENTITY, inputs, run)
            # a hidden child that counts stays in the measure (layout.md "ConsiderSize and the size of a container")
            boxes = [bounds(box.matrix, *box.size) for box in sub_frame.boxes.values() if box.counts]
            if not boxes:
                return (0.0, 0.0), scope.computed(), (0.0, 0.0)
            # the union from its smallest corner, not from the origin (layout.md "ConsiderSize and the size ...")
            start = (min(box[0] for box in boxes), min(box[1] for box in boxes))
            size_px = (max(box[2] for box in boxes) - start[0], max(box[3] for box in boxes) - start[1])
            return size_px, scope.computed(), start  # a list's copies each keep a box (``_template``)

        # the open file names the sample keys; a file that reads its own size while it is measured gets the reference
        cache_key = (
            "size",
            id(root),
            key,
            inputs_key(self.m_ctx.m_samples, inputs),
            tuple(self.m_stack[:1]),
            self._elements(owner),
        )
        return self._kept(cache_key, root, make, provisional=(reference_size, {}, (0.0, 0.0)))

    def _elements(self, owner: str | None) -> tuple | None:
        """A list instance's elements as a part of what is kept: two lists whose elements hold no given value measure
        alike (``canon``)."""
        if owner is None:
            return None
        count = listlayout.element_count(self.m_ctx.m_samples, owner)
        return tuple(
            canon(self.m_ctx.m_samples, SGameObjectRef(listlayout.element_key(owner, i))) for i in range(count)
        )

    def _cut(self, key: str) -> bool:
        """Whether placing ``key`` here is cut short: it places itself (a file already being placed above) or the
        files are nested too deep. What is computed inside that file's placing then depends on where it was asked
        from: it is kept by its key and the stack of files (``Memo.cut``, ``_kept``)."""
        if key in self.m_stack:
            self.m_ctx.m_memo.cut(key)
            return True
        if len(self.m_stack) >= MAX_DEPTH:
            self.m_ctx.m_memo.cut(None)
            return True
        return False

    def _kept(self, cache_key: tuple, root: dict, make, provisional=MISS):
        """``make()`` once per ``mk`` while nothing it read changes (``memo``); a result holding a cut, once per
        ``mk`` and stack of files (what takes it holds the cut too: it reads the cut's token)."""
        memo, here = self.m_ctx.m_memo, tuple(self.m_stack)
        hit = memo.get(cache_key)
        if hit is MISS:
            hit = memo.get((cache_key, here))
        return memo.compute(cache_key, id(root), make, provisional, here) if hit is MISS else hit

    def own(self, element: elements.SElement, scope: CScope) -> dict:
        """An instance's values: its stored ones, what its scope's bindings write, what the game fills in a list's
        item (``filled``), then what the game shows (held)."""
        filled = self.m_filled.get(element.guid or "", {})
        mine = {**self.m_ctx.values(element.node), **scope.m_writes.get(element.guid, {}), **filled}
        return {**mine, **self._baseline(element, scope, mine), **self.m_ctx.live_of(element)}

    def _baseline(self, element: elements.SElement, scope: CScope, values: dict) -> dict:
        """What the game's baseline sets over the values of an instance (``baseline.CBaseline.apply``): not in Play
        mode, whose values are the input's."""
        base = self.m_ctx.m_baseline
        crc = elements.crc_key(element) if base is not None and self.m_ctx.m_play is None else None
        top = root_info(self.m_ctx, scope.m_root).top if crc else None
        if top is None or not top.guid:
            return {}
        context = self.m_placing if self.m_stack and self.m_stack[-1] == scope.m_key else ""
        return base.apply(
            self.m_ctx, top.guid, crc[4:], context, element.kind, values, scope.m_writes.get(element.guid, {})
        )

    def size_of(self, node: dict, scope: CScope) -> tuple[float, float]:
        """The size a binding reads from an instance of ``scope``'s template (``scope.IHost``): its box times its
        ``Scale``, as the game reports ``Size`` for every kind (layout.md "Scale is the size")."""
        element = scope.m_by_node.get(id(node))
        if element is None:
            return (0.0, 0.0)
        overrides = self.own(element, scope)
        if is_size(overrides.get((SIZE,))):  # a given size is read back as given
            return float(overrides[(SIZE,)][0]), float(overrides[(SIZE,)][1])
        if element.kind == "group":
            size_px = self.group_size(
                element, scope, tuple(fields.get(scope.m_root, "ReferenceSize", (1920.0, 1080.0)))
            )
        else:
            size_px = self.natural_size(element, overrides, inputs_of(overrides, node), f"{scope.m_key}:{element.guid}")
        scale_x, scale_y = vector(self.drawn_scale(node, overrides), (1.0, 1.0))
        return (size_px[0] * scale_x, size_px[1] * scale_y)

    def drawn_scale(self, node: dict, overrides: dict):
        """The ``Scale`` an instance is drawn with: in the design view a scale of 0 a binding gives (an animation's
        start) stays as stored, as ``_child`` keeps it."""
        value = overrides.get((0x80000035,))
        if value is None or (
            self.m_ctx.m_design
            and isinstance(value, (tuple, list))
            and any(isinstance(component, (int, float)) and abs(component) < 1e-6 for component in value[:2])
        ):
            return fields.get(node, "Scale")
        return value

    def outputs(self, node: dict, scope: CScope) -> dict[int, object]:
        """The own property values of the widget file an instance places, given what the instance passes in."""
        placed = self.placed_scope(node, scope)
        return placed.computed() if placed is not None else {}

    def places(self, node: dict, scope: CScope) -> bool:
        """Whether an instance of ``scope``'s file is placed at all: a loader places only its chosen children (what
        the others would write into a shared object never happens)."""
        kind = loaders.loader_kind(self.m_ctx, scope.m_root)
        element = scope.m_by_node.get(id(node))
        if kind is None or element is None or len(element.path()) < 2:
            return True
        top = root_info(self.m_ctx, scope.m_root).top
        return element.path()[1] in loaders.chosen(kind, top.children, scope.m_inputs, self.m_ctx.m_design,
                                                  self._picker(scope.m_root, scope.m_inputs))  # fmt: skip

    def _picker(self, root: dict, inputs: dict) -> Callable:
        """What picks an object loader's child (``loaders.chosen``): the class of the game object it is given."""
        given = inputs.get(gamedata.CURRENT_OBJECT)
        return lambda children: gamedata.object_child(self.m_ctx, root, given, children)

    def displayed(self, node: dict, scope: CScope) -> dict[int, object]:
        """A label's ``DisplayedText`` and ``DisplayedSize`` (the shown text's size after its scale), as its
        bindings leave it (layout.md "Labels"); {} for any other instance."""
        element = scope.m_by_node.get(id(node))
        if element is None or element.kind != "label":
            return {}
        overrides = self.own(element, scope)
        text, style, _ = labels.label(self.m_ctx, node, overrides)
        scale_x, scale_y = vector(overrides.get((0x80000035,), fields.get(node, "Scale")), (1.0, 1.0))
        return {DISPLAYED_TEXT: text, DISPLAYED_SIZE: (style.shown[0] * scale_x, style.shown[1] * scale_y)}

    def placed_scope(self, node: dict, scope: CScope) -> CScope | None:
        """The bindings of the widget file an instance places, evaluated with what the instance passes in (None for
        any other instance)."""
        element = scope.m_by_node.get(id(node))
        if element is None or element.kind != "container" or not element.template:
            return None
        root = self.m_ctx.root(element.template)
        if root is None:
            return None
        overrides = self.own(element, scope)
        key, inputs = element.template, inputs_of(overrides, node)
        if self._cut(key):
            return None
        # the file's bindings alone, not its whole placing: what the measure of its size would evaluate first
        sub_placer = CPlacer(self.m_ctx, SFrame(self.m_frame.size))
        sub_placer.m_stack = [*self.m_stack, key]
        sub_placer._seed(key, root)
        owner = f"{self.m_stack[0]}:{key}" if self.m_stack else key
        listed = sub_placer._list(
            key, root, inputs, owner, f"{scope.m_key}:{element.guid}", bool(self.m_stack)
        )  # as it is placed
        return listed.scope if listed else sub_placer.scope(key, root, inputs, owner, is_kept=bool(self.m_stack))

    def group_size(
        self, element: elements.SElement, scope: CScope, parent_size: tuple[float, float]
    ) -> tuple[float, float]:
        """The size of the union of the rectangles of a group's children that count for its size."""
        return self.group_extent(element, scope, parent_size)[0]

    def group_extent(self, element: elements.SElement, scope: CScope, parent_size: tuple[float, float]) -> tuple:
        """A group's size and where its box starts from its origin: the union of its children that count, a
        background stretched round the others among them (the size the game reports: layout.md "ConsiderSize and
        the size of a container"), grown by a stack's margin."""
        sub_placer = CPlacer(self.m_ctx, SFrame(parent_size))
        sub_placer.m_stack = self.m_stack
        rule = self.group_rule(element, scope)
        sub_placer._children(element, scope, IDENTITY, parent_size, None, True, SPaint(), rule)
        kids = [sub_placer.m_frame.boxes.get(id(k.node)) for k in element.children]
        rects = [bounds(box.matrix, *box.size) for box in kids if box is not None and box.counts]
        width_px, height_px = union_size(rects)
        start = (min(r[0] for r in rects), min(r[1] for r in rects)) if rects else (0.0, 0.0)
        margin_px = rule.margin if rule is not None and rule.flow and (width_px or height_px) else (0.0,) * 4
        size_px = (width_px + margin_px[0] + margin_px[2], height_px + margin_px[1] + margin_px[3])
        return size_px, start  # a stack's margin grows its box

    def group_rule(self, element: elements.SElement, scope: CScope) -> arrange.SRule | None:
        """The stack and background rule of a group instance, None without either component."""
        overrides = self.own(element, scope)
        return arrange.read_rule(arrange.components(element.node), overrides)

    # placing

    def local(
        self,
        element,
        overrides,
        size_px,
        parent_size,
        pivot_fraction=None,
        ancestors=(1.0, 1.0),
        stretch=None,
        start=(0.0, 0.0),
    ) -> Matrix:
        """``start``: where a container's box starts (``start_of``); a proportional pivot is a point of that box (the
        main menu's portrait: its box from -170, its pivot (0, 0) there, *game* ``20261116-235858-129``)."""
        node = element.node

        def value_of(uid, name, default):
            value = overrides.get((uid,))
            value = value if value is not None else fields.get(node, name, default)
            return vector(value, default) if isinstance(default, tuple) else value

        position = value_of(0x80000001, "Position", (0.0, 0.0, 0.0))
        pivot = value_of(0x80000002, "PivotPoint", (0.0, 0.0, 0.0))
        if pivot_fraction is not None:
            fraction_x, fraction_y = vector(pivot_fraction, (0.0, 0.0))
            pivot = (start[0] + fraction_x * size_px[0], start[1] + fraction_y * size_px[1])
        scale_vector = value_of(0x80000035, "Scale", (1.0, 1.0, 1.0))
        if stretch is not None:  # a picture given a size is scaled to it, its pivot (in picture units) with it
            scale_vector = (*stretch, 1.0)
        elif is_size(overrides.get((SIZE,))) and (0x80000035,) not in overrides:  # a bound size is the size itself
            scale_vector = (1.0, 1.0, 1.0)
        modes = fields.anchor_fields(node)  # 117's own, or what the History Editions' anchor constraints say
        position, scale_vector = cancel(modes, position, scale_vector, ancestors, self.m_parent_scale)
        angle_deg = scalar(value_of(0x80000003, "RotationAngle", 0.0), 0.0)
        anchor = modes["AnchorPoint"]
        anchor_x, anchor_y = (anchor % 3) / 2, (anchor // 3) / 2
        matrix = translate(
            parent_size[0] * anchor_x + float(position[0]), parent_size[1] * anchor_y + float(position[1])
        )
        matrix = mul(matrix, rotate(angle_deg))
        matrix = mul(matrix, scale(float(scale_vector[0]), float(scale_vector[1])))
        return mul(matrix, translate(-float(pivot[0]), -float(pivot[1])))

    def _template(self, key, root, matrix, inputs, run: STemplateRun | None = None) -> CScope:
        run = run or STemplateRun()
        measure_only, owner, data_owner = run.measure_only, run.owner, run.data_owner
        paint = run.paint or SPaint()
        self.m_stack, self.m_parent_scale, self.m_trail = [*run.stack, key], run.scale, run.trail
        self.m_placing = run.placing
        file_info = self._seed(key, root)
        owner_key = key if not run.stack else f"{run.stack[0]}:{key}"
        listed = self._list(key, root, inputs, owner_key, data_owner, bool(run.stack))
        scope = listed.scope if listed else self.scope(key, root, inputs, owner_key, is_kept=bool(run.stack))
        size_px = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
        top = file_info.top
        loader = loaders.loader_kind(self.m_ctx, root)
        if loader is not None:  # a loader instantiates one of its children
            top = SimpleNamespace(children=loaders.chosen(loader, top.children, inputs, self.m_ctx.m_design,
                                                          self._picker(root, inputs)))  # fmt: skip
        if listed is None:
            self._children(top, scope, matrix, size_px, owner, measure_only, paint)
            return scope
        kind, item, element, first, inputs = listed.kind, listed.item, listed.element, scope, listed.inputs
        settings = settled(scope, inputs)
        offsets = listlayout.offsets(kind, settings, listed.count, self.size_of(item.node, first))
        sectors = listlayout.sectors(kind, settings, listed.count) if kind in listlayout.RADIAL else None
        for i, (offset_x, offset_y) in enumerate(offsets):
            slot = SListSlot(SGameObjectRef(element(i)), i, item.guid, listed.given)
            copy = first if i == 0 else self.scope(key, root, inputs, owner_key, slot, bool(run.stack))
            if sectors is not None:  # a wheel's item stands at its arc point, its stored place is the editor's
                self._sector(item, copy, sectors[i])
                offset_x = offset_y = 0.0
            if self.m_ctx.m_play is None and item.kind == "container":  # Play mode's tab choice is its own
                if tab := listlayout.selected(self.m_ctx.root(item.template), i):
                    self.m_filled.setdefault(item.guid or "", {}).update(tab)
            self._children(SimpleNamespace(children=[item]), copy, mul(matrix, translate(offset_x, offset_y)), size_px, owner,
                           measure_only, paint)  # fmt: skip
            box = self.m_frame.boxes.get(id(item.node))
            if box is not None:  # each copy measures; the item's own key holds the last one placed
                self.m_frame.boxes[hash((id(item.node), i))] = box
        self.m_filled.pop(item.guid or "", None)
        return scope

    def _placing_key(self, element: elements.SElement, scope: CScope) -> str:
        """The baseline's key of a container that places a file: its file's GUID and its name CRC."""
        crc, top = elements.crc_key(element), root_info(self.m_ctx, scope.m_root).top
        return f"{top.guid}/{crc[4:]}" if crc and top.guid and self.m_ctx.m_baseline is not None else ""

    def _sector(self, item: elements.SElement, scope: CScope, sector: tuple[float, float, float, float]) -> None:
        """What the game gives a radial list's item (``filled``): its place, its point on the arc, and the
        ``SectorInfo`` of the file it places, that point, its angle and span; the file's own bindings that set its
        place and rotation from them turn it (the quick tools' segments: layout.md "Lists, grids and loaders")."""
        x, y, angle_deg, span = sector
        filled = self.m_filled[item.guid or ""] = {(POSITION,): (x, y, 0.0)}
        root = self.m_ctx.root(item.template) if item.kind == "container" else None
        comp = arrange.components(root).get(listlayout.SECTOR) if root is not None else None
        if comp is None:
            return
        filled.update({(comp, listlayout.SECTOR_POSITION): (x, y, 0.0), (comp, listlayout.SECTOR_ROTATION): angle_deg,
                       (comp, listlayout.SECTOR_SIZE): span})  # fmt: skip
        placed = self.placed_scope(item.node, scope)
        own = placed.m_writes.get(None, {}) if placed is not None else {}
        filled.update({path: value for path, value in own.items() if path in ((POSITION,), (ROTATION,))})

    def _list(self, key, root, inputs, owner_key, data_owner, is_kept) -> SListed | None:
        """A list file's elements and the scope of its first item (None for any other file): the item count joins
        what it is given, as the game fills it, and what the list reads from its item (``DataMode`` 3) is the first
        item's, so a placing file reads the list's outputs as laid out (layout.md "Lists, grids and loaders")."""
        kind = listlayout.list_kind(self.m_ctx, root)
        top = root_info(self.m_ctx, root).top
        if kind is None or not top.children or loaders.loader_kind(self.m_ctx, root) is not None:
            return None
        item = top.children[0]  # a list file holds its item template as its one child
        data = data_owner or owner_key
        item_count = (
            listlayout.element_count(self.m_ctx.m_samples, data) if data_owner else listlayout.PLACEHOLDER_ITEMS
        )
        element = lambda i: listlayout.element_key(data, i)  # noqa: E731
        item_count, element = gamedata.list_elements(
            self.m_ctx, data, inputs, item_count, element
        )  # the game's elements, where known
        inputs = {**inputs, listlayout.TOTAL_ITEMS: item_count}
        item_count, element = playstate.scrolled(
            inputs, item_count, element
        )  # Play mode scrolled it: from a later element on
        given = self._compared(key, root, inputs, owner_key, is_kept, item, item_count, element)
        slot = SListSlot(SGameObjectRef(element(0)), 0, item.guid, given)
        return SListed(
            kind, item, item_count, element, inputs, self.scope(key, root, inputs, owner_key, slot, is_kept), given
        )

    def _compared(self, key, root, inputs, owner_key, is_kept, item, item_count, element) -> tuple:
        """What the game gives every item of a list from all of them: an item's ``ValueCompare`` gets the smallest and
        the largest ``MyValue`` the list's bindings give the items (the tab bar's widest tab, a category list's
        widest entry: layout.md "Lists, grids and loaders in the game"). Empty for an item without one."""
        comp = arrange.components(item.node).get(listlayout.COMPARE)
        if comp is None:
            return ()
        mine = (comp, listlayout.COMPARE_MINE)
        values = [self.scope(key, root, inputs, owner_key, SListSlot(SGameObjectRef(element(i)), i, item.guid), is_kept)
                  .m_writes.get(item.guid, {}).get(mine) for i in range(item_count)]  # fmt: skip
        found = listlayout.compared(values)
        if found is None:
            return ()
        return (((comp, listlayout.COMPARE_MIN), found[0]), ((comp, listlayout.COMPARE_MAX), found[1]))

    def _seed(self, key: str, root: dict) -> SRootInfo:
        """The file's own preview values for its lists into the samples, as the file holds them now."""
        file_info = root_info(self.m_ctx, root)
        if file_info.seeds is None:  # the file is new or was edited
            file_info.seeds = listlayout.preview_values(file_info.top, key)
            self.m_ctx.m_samples.unseed(f"{key}:", file_info.seeds)
        for k, value in file_info.seeds.items():
            self.m_ctx.m_samples.seed(k, value)
        return file_info

    def scope(self, key: str, root: dict, inputs: dict, owner_key: str, slot: SListSlot | None = None,
              is_kept: bool = True) -> CScope:  # fmt: skip
        """The bindings of ``key`` evaluated over ``inputs``, once however many instances place it (``is_kept`` False: the
        open file, whose animation and tweens change between frames)."""
        inputs = playstate.narrow(inputs, slot)  # a list's copy gets only its own Play mode values
        if not is_kept:
            return CScope(key, root, inputs, self.m_ctx, owner_key, self, slot)

        def make():
            scope = CScope(key, root, inputs, self.m_ctx, owner_key, self, slot)
            scope.m_host = None  # evaluated: a kept scope must not hold this placing's frames
            return scope

        shared = slot and replace(slot, element=canon(self.m_ctx.m_samples, slot.element))
        return self._kept(
            ("scope", id(root), key, inputs_key(self.m_ctx.m_samples, inputs), owner_key, shared), root, make
        )

    def stretch_to(self, element, overrides: dict, inputs: dict):
        """For a picture or video whose size a binding or the instance gives: (the scale that makes the picture that
        size, the picture's own size), else None. The game's ``Size`` is the picture's size times its scale, so
        writing it scales the picture and its pivot (docs/ui/117/layout.md "Position, anchor and pivot")."""
        bound = overrides.get((SIZE,))
        if element.kind not in ("image", "video") or not is_size(bound) or (0x80000035,) in overrides:
            return None
        base = self.natural_size(element, {k: value for k, value in overrides.items() if k != (SIZE,)}, inputs)
        if base[0] <= 0 or base[1] <= 0:
            return None
        return (float(bound[0]) / base[0], float(bound[1]) / base[1]), tuple(base)

    def _child(self, element: elements.SElement, scope: CScope, matrix: Matrix, parent_size, is_sized: bool = True,
               is_room_needed: bool = False) -> SChild:  # fmt: skip
        """One child of a group or template, placed by its own values (the group's rule comes after); ``is_sized``
        False: a hidden child's size is not worked out (nothing it holds is drawn), unless ``is_room_needed`` and it
        counts: a stack gives it its room and a measure keeps it (layout.md "Layout components", "ConsiderSize and
        the size of a container")."""
        overrides = self.own(element, scope)
        if self.m_ctx.m_design:  # a scale or size the game feeds at run time (its UI scale) stays as stored
            closed = (
                all if is_mask_widget(element.node) else any
            )  # a mask's size of 0 on one axis is its state (a closed mask)
            for uid, test in ((0x80000035, any), (SIZE, closed)):
                value = overrides.get((uid,))
                if isinstance(value, (tuple, list)) and test(
                    isinstance(component, (int, float)) and abs(component) < 1e-6 for component in value[:2]
                ):
                    del overrides[(uid,)]
        visible = overrides.get((VISIBLE,))
        if visible is None:  # a layer the author hid in the editor (designer helpers) starts hidden
            visible = bool(fields.get(element.node, "Visible", True))
            visible = visible and (self.m_ctx.m_editor_hidden or not fields.get(element.node, "IsHidden"))
        visible = bool(visible)
        inputs = inputs_of(overrides, element.node)
        counts = overrides.get((0x80000080,))
        counts = bool(fields.get(element.node, "ConsiderSize", True) if counts is None else counts)
        if not visible and not is_sized and not (is_room_needed and counts):
            return self._hidden(element, overrides, inputs, parent_size)
        origin = (0.0, 0.0)
        if element.kind == "label" and not is_size(overrides.get((SIZE,))):  # its box may start at its text
            _, style, size_px = labels.label(self.m_ctx, element.node, overrides)
            origin = style.origin
        else:
            size_px = self.natural_size(element, overrides, inputs, f"{scope.m_key}:{element.guid}")
            placed = (
                self.m_ctx.root(element.template)
                if element.kind == "container" and not is_size(overrides.get((SIZE,)))
                else None
            )
            if placed is not None:  # its box starts where what it holds starts, above or left of it
                origin = self.start_of(element.template or "", placed, inputs, f"{scope.m_key}:{element.guid}")
        if element.kind == "group" and (SIZE,) not in overrides:  # a group is as large as what it holds
            size_px, origin = self.group_extent(element, scope, parent_size)  # from its smallest corner
        start = origin if element.kind in ("container", "group") else (0.0, 0.0)
        stretch = self.stretch_to(element, overrides, inputs)
        if stretch is not None:
            size_px = stretch[1]
        components = arrange.components(element.node)
        if SIZE_OVERRIDE in components and is_size(overrides.get((components[SIZE_OVERRIDE], OVERRIDE_SIZE))):
            size_px = tuple(float(value) for value in overrides[(components[SIZE_OVERRIDE], OVERRIDE_SIZE)][:2])
            origin = start = (0.0, 0.0)
        fraction = overrides.get((components[PPP], PPP_VALUE)) if PPP in components else None
        if fraction is None and PPP in components:  # not stored: the component's default (the main menu's list: 0, 0)
            fraction = component_default(self.m_ctx, element.node, (components[PPP], PPP_VALUE)) or (0.0, 0.0)
        ancestors = own_scale(matrix)
        local = self.local(element, overrides, size_px, parent_size, fraction, ancestors, stretch and stretch[0], start)
        pad, align = arrange.read_child(components, overrides)
        rectangle = bounds(mul(local, translate(*origin)), *size_px)
        slot = arrange.SSlot(element.guid, local, size_px, rectangle, visible and counts, pad, align, holds=counts)
        return SChild(element, overrides, visible, counts, inputs, slot, origin)

    def _hidden(self, element: elements.SElement, overrides: dict, inputs: dict, parent_size) -> SChild:
        """A hidden child where no box of it is shown: at its place, no size (measuring a container's widget file
        would place that whole file for nothing)."""
        local = self.local(element, overrides, (0.0, 0.0), parent_size)
        return SChild(
            element,
            overrides,
            False,
            False,
            inputs,
            arrange.SSlot(element.guid, local, (0.0, 0.0), bounds(local, 0, 0), False),
        )

    def _info_tips(self, in_file: list[SChild]) -> list[tuple[SChild, SChild]]:
        """The children that place an info tip, each with the sibling it hangs on; they count for no size (the
        game's placings have ``ConsiderSize`` off: layout.md "Info tips")."""
        by_guid = {kid.element.guid: kid for kid in in_file}
        out = []
        for kid in in_file:
            placed = self.m_ctx.root(kid.element.template) if kid.element.kind == "container" else None
            reference = by_guid.get(arrange.reference_of(placed, kid.overrides))
            if reference is not None and reference is not kid:
                kid.counts = kid.slot.flows = kid.slot.holds = False
                out.append((kid, reference))
        return out

    def _children(self, parent, scope, matrix, parent_size, owner, measure_only, paint: SPaint,
                  rule: arrange.SRule | None = None) -> None:  # fmt: skip
        sized = owner is None and not measure_only  # the open file's boxes, the selection's outline among them
        is_room_needed = measure_only or rule is not None
        in_file = [
            self._child(element, scope, matrix, parent_size, sized, is_room_needed) for element in parent.children
        ]
        tips = self._info_tips(in_file)
        if (
            rule is not None
        ):  # the game's child array is the file's order, not the ZIndex's (layout.md "Layout components")
            if rule.background is not None and in_file:
                in_file[0].slot.background = True  # the first layer's the background
            arrange.arrange(rule, [k.slot for k in in_file])
        for tip, reference in tips:
            arrange.hang(tip.slot, reference.slot)
            tip.origin = (0.0, 0.0)
        kids = draw_order(in_file, lambda k: is_mask_widget(k.element.node), lambda k: bool(fields.get(k.element.node, "c90bf7fc")),
                          lambda k: int(fields.get(k.element.node, "ZIndex", 0) or 0))  # fmt: skip
        masks: list[SClip] = []
        previous_mask = False
        for kid in kids:
            now_mask = is_mask_widget(kid.element.node)
            if now_mask and not previous_mask:
                masks.clear()  # a new run of masks starts its chain at the container's own mask
            previous_mask = now_mask
            self._visit(kid, scope, matrix, owner, measure_only, paint, masks)

    def _visit(self, kid: SChild, scope, matrix, owner, measure_only, paint: SPaint, masks: list[SClip]) -> None:
        """Draw one child, and what it holds: a group's children, the widget file a container places."""
        element, overrides, size_px = kid.element, kid.overrides, kid.slot.size
        world = mul(matrix, kid.slot.local)
        item_owner = owner if owner is not None else id(element.node)
        if owner is None:
            box_matrix = mul(world, translate(*kid.origin)) if any(kid.origin) else world
            self.m_frame.boxes[id(element.node)] = SBox(
                box_matrix, size_px, kid.visible, kid.counts, matrix, is_mask_widget(element.node)
            )
        if not kid.visible:
            return
        tint = self.m_ctx.tint(element.node)
        tinted_colour = tuple(
            paint_channel * tint_channel // 255 for paint_channel, tint_channel in zip(paint.color, tint, strict=True)
        )
        own_alpha = scalar(overrides.get((0x80000006,), fields.get(element.node, "AlphaTint", 1.0)), 1.0)
        if self.m_ctx.m_design and own_alpha < HIDDEN_ALPHA:  # faded in by an animation in the game
            own_alpha = 1.0
        is_mask = is_mask_widget(element.node)
        receives = bool(fields.get(element.node, "c90bf7fc"))  # "receives the mask" (clipping-and-masks.md)
        clips = [*paint.clips, *masks] if receives else list(paint.clips)
        if not is_mask and not receives:
            masks.clear()  # a sibling that does not take the masks ends their run
        effect_values = effects.read(element.node, overrides, self.m_ctx.root) if element.kind in EFFECTED else {}
        below = replace(paint, color=tinted_colour, alpha=paint.alpha * own_alpha, clips=tuple(clips),
                        effects={**paint.effects, **effects.drawn(effect_values)} if element.kind != "image" else paint.effects)  # fmt: skip
        if not measure_only:
            self._emit(
                element,
                world,
                size_px,
                below,
                item_owner,
                overrides,
                is_mask,
                effect_values if element.kind == "image" else {},
            )
            playstate.record(self, kid, scope, world)  # where Play mode's input finds it
        if is_mask and element.kind in MASK_KINDS:  # only a picture or a vector shape clips (layout.md "Draw order")
            soft = effect_values.get("soft_mask")
            soft = soft if soft is not None and soft.get("Enabled", True) else None
            dynamic_image = overrides.get((IMAGE_ID,), fields.get(element.node, "DynamicImageId")) or None
            masks.append(
                SClip(
                    world,
                    size_px,
                    fields.get(element.node, "Image"),
                    dynamic_image if isinstance(dynamic_image, str) else None,
                    soft,
                )
            )
        if element.kind == "container":
            self._place_file(kid, scope, world, owner, measure_only, below, item_owner)
        elif element.kind == "group":
            outer, self.m_parent_scale = self.m_parent_scale, own_scale(kid.slot.local)
            self._children(element, scope, world, size_px, owner, measure_only, below, self.group_rule(element, scope))
            self.m_parent_scale = outer
            if owner is None:  # a group's own box: its children's bounds
                self._group_box(element, world, kid.counts, matrix)

    def _place_file(self, kid: SChild, scope, world, owner, measure_only, paint: SPaint, item_owner) -> None:
        """The widget file a container places, placed at the container with the values it passes in."""
        element = kid.element
        if not element.template or len(self.m_stack) >= MAX_DEPTH:
            return
        if element.template in self.m_stack:
            self.m_frame.notes.append(
                tr("ui.places_inside_itself_not_followed", name=element.name, template=element.template)
            )
            return
        root = self.m_ctx.root(element.template)
        if root is None:
            self.m_frame.notes.append(tr("ui.widget_file_not_game", name=element.name, template=element.template))
        elif not measure_only:
            index = scope.m_slot.index if scope.m_slot is not None else None
            run = STemplateRun(stack=self.m_stack, owner=item_owner, paint=paint, data_owner=f"{scope.m_key}:{element.guid}",
                              scale=own_scale(kid.slot.local), trail=(*self.m_trail, (element.guid, index)),
                              placing=self._placing_key(element, scope))  # fmt: skip
            cache_key = ("draw", id(root), element.template, inputs_key(self.m_ctx.m_samples, kid.inputs), world, item_owner, run.data_owner,
                  paint_key(paint), tuple(self.m_stack), run.trail)  # fmt: skip

            def make():
                sub_frame = SFrame(self.m_frame.size)
                CPlacer(self.m_ctx, sub_frame)._template(element.template, root, world, kid.inputs, run)
                return sub_frame.items, sub_frame.notes, sub_frame.hits, sub_frame.spans

            items, notes, hits, spans = self._kept(cache_key, root, make)  # placed the same way before: the same items
            playstate.span(self.m_frame, (run.trail, element.template, world, kid.inputs, run), items, hits, spans)
            self.m_frame.items.extend(items)
            self.m_frame.notes.extend(notes)
            self.m_frame.hits.extend(hits)

    def _group_box(self, element: elements.SElement, world: Matrix, counts: bool, parent: Matrix) -> None:
        """A group's own box: its counting children's union (``counts``, ``parent``: its own box's, kept)."""
        kids = [
            self.m_frame.boxes.get(id(k.node)) for k in element.children
        ]  # what counts, as for its size (``group_size``)
        rects = [bounds(box.matrix, *box.size) for box in kids if box is not None and box.counts]
        if rects:  # the union from its smallest corner, as a container's (layout.md "ConsiderSize and the size ...")
            inverse_world = invert(world)
            corners = [
                apply(inverse_world, x, y) for rect in rects for x, y in ((rect[0], rect[1]), (rect[2], rect[3]))
            ]
            near_x, near_y = min(corner[0] for corner in corners), min(corner[1] for corner in corners)
            far_x, far_y = max(corner[0] for corner in corners), max(corner[1] for corner in corners)
            self.m_frame.boxes[id(element.node)] = SBox(
                mul(world, translate(near_x, near_y)), (max(far_x - near_x, 1.0), max(far_y - near_y, 1.0)), True,
                counts, parent,
            )  # fmt: skip

    def _emit(self, element, world, size_px, paint: SPaint, owner, overrides, is_mask, effect_values=None) -> None:
        node = element.node
        color, alpha, clips = paint.color, paint.alpha, list(paint.clips)
        blend = int(fields.get(node, "BlendingMode", 0) or 0)
        if element.kind == "image":
            image = fields.get(node, "Image")
            uv_offset = vector(overrides.get((0x80000038,), fields.get(node, "UVOffset")), (0.0, 0.0))
            uv_scale = vector(overrides.get((0x80000039,), fields.get(node, "UVScale")), (1.0, 1.0))
            dynamic_image = overrides.get((IMAGE_ID,), fields.get(node, "DynamicImageId")) or None
            dynamic_image = dynamic_image if isinstance(dynamic_image, str) else None
            effect_values = effects.drawn(
                {**paint.effects, **(effect_values or {})}
            )  # what a container passes down, then the picture's own
            self.m_frame.items.append(SDrawItem("image", world, size_px, color, alpha, blend, image, dynamic_image,
                                             self._nine_slice(image), (*uv_offset, *uv_scale), clips=clips, owner=owner,
                                             mask=is_mask, effects=effect_values))  # fmt: skip
        elif element.kind == "label":
            text, style, _ = labels.label(self.m_ctx, node, overrides)
            style.color = tuple(
                style_channel * paint_channel // 255
                for style_channel, paint_channel in zip(style.color, color, strict=True)
            )
            self.m_frame.items.append(SDrawItem("text", world, size_px, color, alpha, blend, text=text, style=style,
                                             clips=clips, owner=owner))  # fmt: skip
        elif element.kind == "video":
            self.m_frame.items.append(SDrawItem("video", world, size_px, color, alpha, blend, clips=clips, owner=owner))

    def _nine_slice(self, image: str | None) -> tuple[float, float, float, float] | None:
        """The border of an image asset as fractions (left, top, right, bottom), None for a plain picture."""
        image_root = self.m_ctx.root(image)
        nine_slice = phoenixedit.objects(image_root, "NineSliceScaling") if image_root is not None else []
        if not nine_slice:
            return None
        top_left = fields.get(nine_slice[0], "TopLeft", (0.0, 0.0))
        bottom_right = fields.get(nine_slice[0], "BottomRight", (1.0, 1.0))
        if tuple(top_left) == (0.0, 0.0) and tuple(bottom_right) == (1.0, 1.0):
            return None
        return (top_left[0], top_left[1], 1.0 - bottom_right[0], 1.0 - bottom_right[1])


def place(key: str, root: dict, ctx: CContext, inputs: dict | None = None) -> SFrame:
    """The open file ``key`` (its decoded ``root``) placed: its draw items in drawing order and its elements' boxes."""
    if ctx.m_epoch != ctx.conditions():  # the view changed: nothing kept holds
        ctx.forget()
        ctx.m_epoch = ctx.conditions()
    size_px = tuple(fields.get(root, "ReferenceSize", (1920.0, 1080.0)))
    if elements.kind_of(root) != "template":
        return SFrame(size_px, notes=[tr("ui.only_widget_files_scenes_preview")])
    matrix = IDENTITY
    if ctx.m_window_aspect > 0:  # the game stretches the root per axis to the window, the height kept (layout.md)
        size_px, matrix = (
            (size_px[1] * ctx.m_window_aspect, size_px[1]),
            (size_px[1] * ctx.m_window_aspect / size_px[0], 0.0, 0.0, 1.0, 0.0, 0.0),
        )
    run = STemplateRun(scale=own_scale(matrix))  # the root's own scale is the window's stretch
    for _ in range(REPLACE):
        ctx.m_memo.m_dropped = 0
        frame = SFrame(size_px)
        _, read = ctx.m_memo.gather(
            lambda target_frame=frame: CPlacer(ctx, target_frame)._template(key, root, matrix, inputs or {}, run)
        )
        if not ctx.m_memo.m_dropped:  # no value a part of it read was made up or seeded after it was read
            break
    ctx.m_samples.m_seen = {}
    for token in read:  # what the frame read, kept results included: the sample panel lists it
        if isinstance(token, tuple) and token[0] == "sample" and not token[1].endswith("/"):
            ctx.m_samples.get(token[1])
    return frame
