"""The data a placed frame is made of: draw items and clips, the boxes of the open file's elements, the frame, and
the records the placer hands between its steps (what a widget passes down, how one template is placed, a child, a
list being placed). ``layout`` places the widgets and re-exports these."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import (
    arrange,
    elements,
)
from .labels import STextStyle
from .matrices import IDENTITY, Matrix
from .scope import (
    CScope,
)


@dataclass
class SClip:
    """A mask over a draw item: its rectangle, and for a soft mask its picture and the soft mask's values."""

    matrix: Matrix
    size: tuple[float, float]
    image: str | None = None  # the mask's image asset
    image_id: str | None = None
    soft: dict | None = None  # SoftMaskParams' values: the picture's alpha, smooth-stepped, multiplies


@dataclass
class SDrawItem:
    kind: str  # image, text, video, box
    matrix: Matrix
    size: tuple[float, float]
    color: tuple[int, int, int] = (255, 255, 255)
    alpha: float = 1.0
    blend: int = 0
    image: str | None = None  # an ImageAsset's GUID
    image_id: str | None = None  # a picture named at run time (DynamicImageId)
    nine: tuple[float, float, float, float] | None = None  # the border as fractions: left, top, right, bottom
    uv_offset_scale: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # offset x, y, scale x, y
    text: str = ""
    style: STextStyle | None = None
    clips: list[SClip] = field(default_factory=list)
    effects: dict[str, dict] = field(default_factory=dict)  # the picture's effects (effects.drawn)
    owner: int = 0  # id() of the element of the open file the item belongs to
    mask: bool = False  # a mask widget: not drawn, it clips the masked siblings after it


@dataclass
class SBox:
    matrix: Matrix
    size: tuple[float, float]
    visible: bool = True
    counts: bool = True  # ConsiderSize: whether its container measures it
    parent: Matrix = IDENTITY  # the parent's matrix: a move in the preview is a move in the parent's units
    mask: bool = False  # a mask widget: it takes no input


@dataclass
class SFrame:
    size: tuple[float, float]
    items: list[SDrawItem] = field(default_factory=list)
    boxes: dict[int, SBox] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    hits: list = field(default_factory=list)  # the areas Play mode's input reaches, drawn order (``playstate.SHit``)
    spans: list = field(default_factory=list)  # where each placed file's items and hits are (``playstate.SSpan``)


@dataclass(frozen=True)
class SPaint:
    """What a widget hands down to everything under it: colour, alpha, the masks that cover it and its effects."""

    color: tuple[int, int, int] = (255, 255, 255)
    alpha: float = 1.0
    clips: tuple[SClip, ...] = ()
    effects: dict = field(default_factory=dict)  # drawn effects of the containers above (effects.drawn)


@dataclass(frozen=True)
class STemplateRun:
    """How one template is placed: ``measure_only`` (boxes only, nothing painted), the ``stack`` of template keys
    above it, the ``owner`` element, the ``paint`` it inherits and ``data_owner``, the key a list's data is read by."""

    measure_only: bool = False
    stack: tuple = ()
    owner: object = None
    paint: SPaint | None = None
    data_owner: str | None = None
    scale: tuple[float, float] = (1.0, 1.0)  # the placing container's own scale (what FixedToParent divides by)
    trail: tuple = ()  # the placing instances from the open file down (``playstate.Trail``)
    placing: str = ""  # the baseline's key of the placing container (``Placer._placing_key``)


@dataclass
class SChild:
    """A child of a group or template with its own values, before the group's rule moves it."""

    element: elements.SElement
    overrides: dict
    visible: bool
    counts: bool
    inputs: dict
    slot: arrange.SSlot
    origin: tuple[float, float] = (0.0, 0.0)  # where its box starts from its own origin (a label's text)


@dataclass
class SListed:
    """A list file being placed: its kind, its item child, its element count and keys, what it is given (the item
    count with it), the scope of its first item and what every item is given from all of them (``_compared``)."""

    kind: str
    item: elements.SElement
    count: int
    element: Callable[[int], str]
    inputs: dict
    scope: CScope
    given: tuple = ()
