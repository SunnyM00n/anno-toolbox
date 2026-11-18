"""The parts the preview settings panel (``previewpanel``) is built from: ``Icons`` (the game's pictures decoded off the
window's thread), ``IconGrid`` and ``IconCombo`` (a choice shown with its picture, like the game's profile
customization), ``Section`` (a tab's base: the store, the catalogue and a row with its reset button).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QToolButton,
    QWidget,
)

from ..infra.i18n import tr
from . import previewdefaults
from .previewcatalogue import Catalogue, Option

CELL = 56  # a picture's side in the grids
PICTURE = 64  # the side the pictures are decoded at
TILE = "#3c3c3c"  # the ground of a white icon


def swatch(rgb: tuple[int, int, int], side: int = 40) -> QIcon:
    """A colour as a picture."""
    pix = QPixmap(side, side)
    pix.fill(QColor(*rgb))
    return QIcon(pix)


def humanise(name: str) -> str:
    """``SetupStartCredits`` as "Start Credits": the game's tag as words."""
    text = name[5:] if name.startswith("Setup") and len(name) > 5 else name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).strip()


class Icons(QObject):
    """The game's pictures by path as icons. ``request`` decodes the ones not held off the window's thread (``load``
    gives a ``QImage`` or None) and tells ``ready``; ``get`` is what is held (None: not yet, or no picture)."""

    ready = Signal()

    def __init__(self, background, load: Callable[[str], QImage | None] | None = None) -> None:
        super().__init__()
        self.background, self.load = background, load
        self.held: dict[str, QIcon | None] = {}
        self._asked: set[str] = set()

    def reset(self, load) -> None:
        """Another game: forget the pictures held and read them with ``load``."""
        self.held.clear()
        self._asked.clear()
        self.load = load

    def get(self, path: str) -> QIcon | None:
        return self.held.get(path) if path else None

    def request(self, paths, wait: bool = False) -> None:
        new = sorted({p for p in paths if p and p not in self._asked})
        if not new or self.load is None:
            return
        self._asked.update(new)
        load = self.load

        def work() -> dict[str, QImage | None]:
            return {p: load(p) for p in new}

        self.background.run(work, self._taken, wait)

    def _taken(self, found) -> None:
        if isinstance(found, Exception):
            return
        for path, image in found.items():
            self.held[path] = QIcon(QPixmap.fromImage(image)) if image is not None and not image.isNull() else None
        self.ready.emit()


def is_white(rgba) -> bool:
    """Whether a picture is white (the game tints such an icon: the glyph is the picture's alpha)."""
    seen = rgba[..., 3] > 16
    return bool(seen.any()) and int(rgba[..., :3][seen].mean(axis=0).min()) > 215


def on_tile(image: QImage) -> QImage:
    """A white icon on a dark tile, as the game shows it on its frame, so that a light list shows it."""
    tile = QImage(PICTURE, PICTURE, QImage.Format.Format_ARGB32)
    tile.fill(0)
    painter = QPainter(tile)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(TILE))
    painter.drawRoundedRect(0, 0, PICTURE, PICTURE, 8, 8)
    inner = image.scaled(PICTURE - 12, PICTURE - 12, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)  # fmt: skip
    painter.drawImage((PICTURE - inner.width()) // 2, (PICTURE - inner.height()) // 2, inner)
    painter.end()
    return tile


def image_loader(pictures) -> Callable[[str], QImage | None]:
    """A loader of ``Icons`` over a ``pictures.Pictures``: the game's picture at the grid's size, a white one on a
    dark tile."""

    def load(path: str) -> QImage | None:
        found = pictures.get(image_id=path, size=(PICTURE, PICTURE))
        if found is None:
            return None
        rgba = found.rgba
        image = QImage(rgba.data, found.width, found.height, 4 * found.width, QImage.Format.Format_RGBA8888).copy()
        if is_white(rgba):
            return on_tile(image)
        return image.scaled(PICTURE, PICTURE, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)  # fmt: skip

    return load


def icon_at(icons: Icons | None, path: str) -> QIcon:
    """The game's picture at ``path`` as held by ``icons``; an empty icon while it is not (or there is none)."""
    return (icons.get(path) if icons is not None else None) or QIcon()


def icon_of(option: Option, icons: Icons | None) -> QIcon:
    return swatch(option.rgb) if option.rgb is not None else icon_at(icons, option.icon)


class IconGrid(QListWidget):
    """One choice out of ``options`` shown as pictures (a name as the tool tip); ``picked(guid)`` when the person
    chooses, not when ``select`` sets it."""

    picked = Signal(object)

    def __init__(self, rows: int = 2, backdrop: str = "") -> None:
        super().__init__()
        self.options: list[Option] = []
        self.icons: Icons | None = None
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWrapping(True)
        self.setIconSize(QSize(CELL, CELL))
        self.setGridSize(QSize(CELL + 10, CELL + 10))
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setFixedHeight(rows * (CELL + 10) + 8)
        back = f"QListWidget {{ background: {backdrop}; }}" if backdrop else ""  # white emblems need a dark one
        self.setStyleSheet(back + "QListWidget::item:selected { background: palette(highlight); }")
        self.currentItemChanged.connect(self._changed)
        self._quiet = False

    def fill(self, options: list[Option], icons: Icons | None) -> None:
        self.options, self.icons = list(options), icons
        self._quiet = True
        self.clear()
        for option in self.options:
            item = QListWidgetItem(icon_of(option, icons), "")
            item.setToolTip(option.name)
            item.setData(Qt.ItemDataRole.UserRole, option.guid)
            self.addItem(item)
        self._quiet = False

    def update_icons(self) -> None:
        for row, option in enumerate(self.options):
            self.item(row).setIcon(icon_of(option, self.icons))

    def select(self, guid) -> None:
        self._quiet = True
        self.setCurrentRow(next((i for i, o in enumerate(self.options) if o.guid == guid), -1))
        self._quiet = False

    def current_guid(self):
        item = self.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _changed(self, item, _old) -> None:
        if item is not None and not self._quiet:
            self.picked.emit(item.data(Qt.ItemDataRole.UserRole))


class IconCombo(QComboBox):
    """The same as a drop-down: ``options`` with their pictures, ``picked(guid)`` on the person's choice."""

    picked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.options: list[Option] = []
        self.icons: Icons | None = None
        self._quiet = False
        self.activated.connect(lambda i: self.picked.emit(self.itemData(i)))

    def fill(self, options: list[Option], icons: Icons | None = None) -> None:
        self.options, self.icons = list(options), icons
        self._quiet = True
        self.clear()
        for option in self.options:
            self.addItem(icon_of(option, icons), option.name, option.guid)
        self._quiet = False

    def update_icons(self) -> None:
        for row, option in enumerate(self.options):
            self.setItemIcon(row, icon_of(option, self.icons))

    def select(self, guid) -> None:
        index = self.findData(guid)
        self._quiet = True
        self.setCurrentIndex(index)
        self._quiet = False

    def current_guid(self):
        return self.currentData()


class Section(QWidget):
    """A tab of the panel: it builds its controls once (``build``), reads the store into them (``refresh``) and writes
    a change through ``put``. A row of a form carries a small reset button that puts its values back."""

    def __init__(self, panel) -> None:
        super().__init__()
        self.panel = panel
        self.keyed: list = []  # the labels and buttons that get an access key

    @property
    def store(self):
        return self.panel.store

    @property
    def cat(self) -> Catalogue:
        return self.panel.catalogue

    @property
    def d(self) -> previewdefaults.Defaults:
        return self.panel.store.defaults

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """Read the store into the controls."""

    def update_icons(self) -> None:
        """The pictures arrived: put them on the controls."""

    def put(self, name: str, value) -> None:
        """A value the person chose (``Store.set``); not while the controls are being filled from the store."""
        if not self.panel.busy:
            self.panel.store.set(name, value)

    def put_option(self, name: str, value) -> None:
        """An option: the campaign's own value is no change."""
        base = previewdefaults.CAMPAIGN_OPTIONS.get(name, 0 if name.startswith("title.") else None)
        self.put(f"options.{name}", None if value == base else value)

    def row(self, form: QFormLayout, text: str, control: QWidget, names: tuple[str, ...], tip: str) -> None:
        """A form row: the label (an access key, the control its buddy), the control and its reset button."""
        label = QLabel(text)
        label.setBuddy(control)
        label.setToolTip(tip)
        control.setToolTip(tip)
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(control, 1)
        lay.addWidget(self.panel.reset_button(names))
        form.addRow(label, box)
        self.keyed.append(label)


def reset_icon(widget: QWidget) -> QIcon:
    return widget.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)


class ResetButton(QToolButton):
    """A small button that puts the values ``names`` back to the campaign's; it is off while they are not changed."""

    def __init__(self, panel, names: tuple[str, ...]) -> None:
        super().__init__()
        self.names = names
        self.setIcon(reset_icon(self))
        self.setAutoRaise(True)
        self.setToolTip(tr("ui.preview_reset_one_tip"))
        self.setAccessibleName(tr("ui.preview_reset_one"))
        self.clicked.connect(lambda _c=False: panel.reset_names(self.names))
