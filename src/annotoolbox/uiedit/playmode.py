"""The UI editor's Play mode (Qt): the Design / Play switch (toolbar, View menu, F5 and Shift+F5), the preview's pointer
and keys taken by the input (``playinput``) while it is on, what they set laid out (``playstate``), the info tip and
the keyboard focus drawn over the preview, the scene's start animation played, and the choices the game keeps
written to the Preview settings (``Store.set(..., play=True)``). docs/ui-editor.md "Play mode".

``install(window, view_menu)`` is the window's entry; ``PlayController`` the mode; ``PlayedList`` the Preview
settings' list of the choices. Play mode never edits the file; Design mode never writes to the store.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFontMetrics, QKeySequence, QPen
from PySide6.QtWidgets import QToolBar, QTreeWidget, QTreeWidgetItem

from ..infra.i18n import N_, tr
from . import animations, bindings, canvas, playinput, playstate, previewdefaults
from .layout import apply
from .playinput import Outcome

FOCUS = (120, 255, 160)
REST_MS, STEP_MS = 450, 33  # how long the pointer rests before an info tip shows; the start animation's frame
ADDED = 0x8000003C  # a widget's AddedToScene event
LIST_ROW = 28.0  # a row of an open combo box's list, reference pixels [I]
ARROWS = {Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right"}


class PlayController(QObject):
    """Play mode of one UI window: on or off, what the input set (``state``), the choices the store holds."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window, self.active = window, False
        self.state = playstate.PlayState()
        self.store: previewdefaults.Store | None = None
        self.choices: dict = {}
        self.pointer: QPointF | None = None  # in the reference space
        self.pressed: playstate.Trail | None = None  # the widget a press went to, until the release
        self.focus: playstate.Trail | None = None  # the keyboard's widget
        self.tip_text, self.tip_at = "", QPointF()
        self._rest = QTimer(self, singleShot=True, interval=REST_MS)
        self._rest.timeout.connect(self._show_tip)
        self._intro, self._intro_t = None, 0.0  # the start animation being played: (guid, seconds)
        self._clock = QTimer(self, interval=STEP_MS)
        self._clock.timeout.connect(self._tick)
        self._ages = QTimer(self, interval=STEP_MS)  # the animations a change started, played over time
        self._ages.timeout.connect(self._age_step)
        self._still = None  # the latest change whose animations were found standing still
        self._waiting, self._longest = False, {}  # a layout asked for while one runs; the files' longest animation
        self._keys: tuple = (None, set())  # the files the shown frame placed
        self.drag: playinput.Drag | None = None  # a scroll bar's handle or a draggable button being dragged

    # the switch

    def set_active(self, on: bool) -> None:
        """Play mode on or off; off shows the file as designed again (what was set stays for the next time)."""
        w = self.window
        for action, checked in (
            (getattr(w, "play_mode_action", None), on),
            (getattr(w, "design_mode_action", None), not on),
        ):
            if action is not None:
                action.setChecked(checked)
        if on == self.active:
            return
        self.active = on
        playinput.forget()
        self.state.passing.clear()
        self.pressed, self.focus, self.tip_text = None, None, ""
        if on:
            self._start_intro()
            self._apply()
            w.statusBar().showMessage(tr("ui.play_on"))
        else:
            self._stop_intro()
            self._ages.stop()
            self.drag = None
            w._when_idle(lambda: w._set_view(play=None))
            w.statusBar().showMessage(tr("ui.play_off"))
        w.preview.update()

    def file(self) -> str:
        doc = self.window.doc
        return doc.key if doc is not None else ""

    def _apply(self, outcome: Outcome | None = None) -> None:
        """Lay the preview out with what the input set (only the placings it reaches are placed again)."""
        if outcome is not None:
            before = self.state.entries(self.file())
            for trail, path, value in outcome.kept:
                self.state.keep(self.file(), trail, path, value)
            for trail, first in outcome.scrolls:
                self.state.scroll(self.file(), trail, first)
            self.state.touch(self.file(), before, self.state.entries(self.file()), time.monotonic())
        if self.active:
            self._lay_out(whole=outcome is not None and bool(outcome.choices))
        for name, value in outcome.choices.items() if outcome is not None and self.store is not None else ():
            self.store.set(name, value, play=True)  # what read the field follows once this layout is done
        if outcome is not None and outcome.open:
            self.window.open_file(outcome.open)
        if outcome is not None and outcome.note:
            self.window.statusBar().showMessage(outcome.note)

    def _set_passing(self, passing: dict) -> None:
        before = self.state.entries(self.file())
        self.state.passing = passing
        self.state.touch(self.file(), before, self.state.entries(self.file()), time.monotonic())

    def _trees(self) -> dict:
        """Play mode's values for the next layout, timed: the animations a change started play from then."""
        now, longest = time.monotonic(), self._longest_animation()
        latest = max(self.state.since.get(self.file(), {}).values(), default=None)
        if self.state.running(self.file(), now, longest) and latest != self._still:
            self._ages.start()
        else:
            self._ages.stop()
        return self.state.trees(self.file(), self.state.ages(self.file(), now, longest))

    def _lay_out(self, whole: bool = False, tick: bool = False) -> None:
        """Place what Play mode's values changed, off the window's thread: one layout at a time, the newest values
        win, the last finished frame stays shown meanwhile (``playstate.replay``; ``whole``: the whole file, as for
        a choice that every reader of its field follows; ``tick``: a step of the animations' clock)."""
        w = self.window
        if not self.active:
            return
        if w._placing:
            if not self._waiting:
                self._waiting = True
                w._when_idle(self._waited)
            return
        trees, doc, ctx = self._trees(), w.doc, w.ctx
        if doc is None or ctx is None:
            return
        if whole or w._view or ctx.play is None or w.frame is None:
            w._set_view(play=trees)
            return
        if trees == ctx.play:
            return
        old, frame = ctx.play, w.frame
        ctx.play, w._placing = trees, True
        w.background.run(lambda: playstate.replay(ctx, doc.key, doc.root, frame, old, trees),
                         lambda result: self._replayed(doc, frame, result, tick), w.sync_layout)  # fmt: skip

    def _replayed(self, doc, before, result, tick: bool) -> None:
        """A replay is done: shown as any layout; a step of the clock that changed nothing stops the clock until the
        next change (no animation is playing)."""
        if tick and not isinstance(result, Exception) and _drawn(result) == _drawn(before):
            self._still = max(self.state.since.get(self.file(), {}).values(), default=None)
            self._ages.stop()
        self.window._placed(doc, result)

    def _age_step(self) -> None:
        self._lay_out(tick=True)

    def _waited(self) -> None:
        self._waiting = False
        self._lay_out()

    def _longest_animation(self) -> float:
        """The longest animation of the files the preview shows: how long the changes' animations may play."""
        frame, files = self.window.frame, self.window.game.files if self.window.game is not None else None
        if self._keys[0] is not frame:  # the files a frame placed, once per frame
            self._keys = (frame, {s.key for s in playstate.walk_spans(frame.spans if frame is not None else ())})
        keys = {self.file(), *self._keys[1]}
        for key in keys - set(self._longest):
            root = files.root(key) if files is not None else None
            self._longest[key] = max((a.seconds for a in animations.read(root)), default=0.0) if root else 0.0
        return max((self._longest.get(k, 0.0) for k in keys), default=0.0)

    def reset_choices(self) -> None:
        """Back to the design state: the choices kept in the Preview settings and what this session set."""
        self.state.clear()
        if self.store is not None:
            self.store.reset(played=True)
        self._apply()

    # the store's choices

    def store_opened(self, store: previewdefaults.Store) -> None:
        """A game was opened: its store holds the choices; the preview reads them from now on."""
        self.store, self.choices = store, previewdefaults.play_choices(store.defaults)
        store.listeners.append(lambda d, s=store: self._store_changed(s, d))
        if self.window.ctx is not None:
            self.window.ctx.play_choices = playstate.Choices(dict(self.choices))

    def _store_changed(self, store, d) -> None:
        if store is not self.store:
            return
        new = previewdefaults.play_choices(d)
        changed = {n for n in {*new, *self.choices} if new.get(n) != self.choices.get(n)}
        if not changed:
            return
        self.choices = new
        snapshot = playstate.Choices(dict(new))

        def follow() -> None:
            ctx = self.window.ctx
            if ctx is not None:
                for name in changed:
                    for token in playstate.choice_tokens(name):  # what looked for it before it existed too
                        ctx.memo.forget(token)
            self.window._set_view(play_choices=snapshot)

        self.window._when_idle(follow)

    # the pointer

    def _hits(self) -> list:
        frame = self.window.frame
        return playinput.under(frame, self.pointer.x(), self.pointer.y()) if self.pointer is not None else []

    def _passing(self) -> dict:
        """The hover (and the press held) on what the pointer is over, the keyboard's widget and a wheel's sector."""
        ctx, frame, out = self.window.ctx, self.window.frame, {}
        for hit in self._hits():
            values = playinput.hover_values(ctx, hit)
            if values:
                out[hit.trail] = values
        focused = playinput.by_trail(frame, self.focus) if self.focus is not None else None
        if focused is not None:
            out.setdefault(focused.trail, {}).update(playinput.hover_values(ctx, focused))
        if self.pointer is not None:
            wheel = playinput.wheel_focus(ctx, frame, (self.pointer.x(), self.pointer.y()))
            if wheel is not None:
                out.setdefault(wheel[0].trail, {})[(playinput.FOCUSED,)] = wheel[1]
        if self.pressed is not None and self.pressed in out:
            held = playinput.by_trail(frame, self.pressed)
            if held is not None:
                out[self.pressed].update(playinput.press_values(ctx, held, False))
        if self.drag is not None and self.drag.kind == "draggable":  # its DragStarted while the drag lasts
            out.setdefault(self.drag.trail, {})[(playinput.DRAG_STARTED,)] = True
        return out

    def _follow(self) -> None:
        passing = self._passing()
        if passing != self.state.passing:
            self._set_passing(passing)
            self._apply()

    def mouse(self, kind: str, at: QPointF, widget_at: QPointF) -> None:
        """The pointer moved, was pressed or released (``kind``) at a point of the reference space."""
        if self.window.frame is None or self.window.ctx is None:
            return
        self.pointer, self.tip_at = at, widget_at
        ctx, frame, point = self.window.ctx, self.window.frame, (at.x(), at.y())
        if kind == "press":
            if self._pick_from_list(point):
                return
            hit = playinput.target(ctx, self._hits())
            self.pressed = hit.trail if hit is not None else None
            self.drag = playinput.begin_drag(ctx, frame, self._hits(), point)
            self._drag_or_slide(point)
        elif kind == "move":
            if self.tip_text:
                self.tip_text = ""
                self.window.preview.update()
            self._rest.start()
            if self.pressed is not None or self.drag is not None:
                self._drag_or_slide(point)
        elif kind == "release":
            pressed, drag, self.pressed, self.drag = self.pressed, self.drag, None, None
            hits = self._hits()
            if drag is not None and drag.kind == "draggable":  # a drag ends: its DragStopped, no click
                held = playinput.by_trail(frame, drag.trail)
                self._follow_release(held, {(playinput.DRAG_STOPPED,): True})
                self._apply()
                return
            if pressed is not None and any(h.trail == pressed for h in hits):
                held = playinput.by_trail(frame, pressed)
                outcome = playinput.click(ctx, frame, hits, point, self.file(), self._models())
                self._follow_release(held)
                self._apply(outcome)
                return
        self._follow()

    def _models(self):
        game = self.window.game
        return game.models if game is not None else None

    def _follow_release(self, held, extra: dict | None = None) -> None:
        """The release events on the widget that was pressed, while the pointer stays on it."""
        passing = self._passing()
        if held is not None:
            passing.setdefault(held.trail, {}).update(playinput.press_values(self.window.ctx, held, True))
            passing[held.trail].update(extra or {})
        self._set_passing(passing)

    def _drag_or_slide(self, point) -> None:
        """A press held and moved: a scroll bar or a draggable button follows the pointer, else a slider."""
        ctx, frame = self.window.ctx, self.window.frame
        if self.drag is not None:
            scrolls = self.state.scrolls.get(self.file(), {})
            self._apply(playinput.drag_to(ctx, frame, self.drag, point, scrolls))
            return
        outcome = playinput.slide(ctx, frame, self._hits(), point)
        if outcome is not None:
            self._apply(outcome)

    def wheel(self, at: QPointF, steps: int) -> None:
        """The wheel over a list or a grid scrolls it by an element (a grid by a row)."""
        frame, ctx = self.window.frame, self.window.ctx
        if frame is None or ctx is None:
            return
        self.pointer = at
        hits = self._hits()
        for hit in reversed(hits):
            first = self.state.first(self.file(), hit.trail)
            found = playinput.scroll(ctx, frame, [hit], steps, first)
            if found is not None:
                self._apply(Outcome(scrolls=[found]))
                return

    def leave(self) -> None:
        self.pointer, self.pressed, self.drag, self.tip_text = None, None, None, ""
        self._rest.stop()
        self._follow()
        self.window.preview.update()

    # a combo box's list, drawn over the preview while it is open

    def _open_list(self):
        """The open combo box: its hit, its entries and each entry's row (reference space), or None."""
        frame, ctx = self.window.frame, self.window.ctx
        if frame is None or ctx is None:
            return None
        for trail, values in self.state.kept.get(self.file(), {}).items():
            if values.get((playinput.COMBO_OPEN,)):
                hit = playinput.by_trail(frame, trail)
                if hit is not None and "combo" in playinput.kinds(ctx, hit):
                    names = playinput.entries(ctx, hit, self._models())
                    (x0, y0), (x1, y1) = (apply(hit.matrix, *p) for p in ((0, 0), hit.size))
                    rows = [QRectF(x0, y1 + i * LIST_ROW, max(x1 - x0, 120.0), LIST_ROW) for i in range(len(names))]
                    return hit, names, rows
        return None

    def _pick_from_list(self, point) -> bool:
        """A press on an entry of the open combo box's list picks it; True when it did."""
        found = self._open_list()
        if found is None:
            return False
        hit, _names, rows = found
        index = next((i for i, r in enumerate(rows) if r.contains(QPointF(*point))), None)
        if index is None:
            return False
        self._apply(playinput.pick_entry(self.window.ctx, self.window.frame, hit, index))
        return True

    def _show_tip(self) -> None:
        if self.active and self.pointer is not None and self.window.ctx is not None:
            self.tip_text = playinput.tip(self.window.ctx, self._hits())
            self.window.preview.update()

    # the keyboard

    def move_focus(self, step: int) -> None:
        """Tab and Shift+Tab: the next or the previous widget that takes a press."""
        order = playinput.focusable(self.window.ctx, self.window.frame)
        if not order:
            return
        trails = [h.trail for h in order]
        at = trails.index(self.focus) if self.focus in trails else (-1 if step > 0 else 0)
        self.focus = trails[(at + step) % len(trails)]
        self._follow()
        self.window.preview.update()

    def move_focus_to(self, direction: str) -> bool:
        """An arrow key: the focus moves to the neighbour that way (``playinput.neighbour``); True when it moved."""
        ctx, frame = self.window.ctx, self.window.frame
        order = playinput.focusable(ctx, frame)
        now = playinput.by_trail(frame, self.focus) if self.focus is not None else None
        found = playinput.neighbour(ctx, frame, now, direction, order)
        if found is None:
            return False
        self.focus = found.trail
        self._follow()
        self.window.preview.update()
        return True

    def key(self, event) -> bool:
        """Enter or Space presses the widget that has the focus, left and right move a slider or step a toggle, the
        arrows move the focus to the neighbour that way; True when taken."""
        frame, ctx = self.window.frame, self.window.ctx
        hit = playinput.by_trail(frame, self.focus) if self.focus is not None and frame is not None else None
        if ctx is None or frame is None:
            return False
        sideways = event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
        if hit is not None and sideways and "switch" in playinput.kinds(ctx, hit):
            w, h = hit.size
            point = apply(hit.matrix, w * (0.25 if event.key() == Qt.Key.Key_Left else 0.75), h / 2)
            self._apply(playinput.click(ctx, frame, [hit], point, self.file(), self._models()))
            return True
        if (hit is None or not sideways or "slider" not in playinput.kinds(ctx, hit)) and event.key() in ARROWS:
            return self.move_focus_to(ARROWS[event.key()])
        if hit is None:
            return False
        w, h = hit.size
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            point = apply(hit.matrix, w / 2, h / 2)
            self._follow_release(hit)
            self._apply(playinput.click(ctx, frame, [hit], point, self.file(), self._models()))
            return True
        if sideways and "slider" in playinput.kinds(ctx, hit):
            value = float(hit.values.get((playinput.S_VALUE,)) or 0.0)
            lo, hi = (float(hit.values.get((u,), d) or d) for u, d in ((playinput.S_MIN, 0.0), (playinput.S_MAX, 1.0)))
            f = (
                min(1.0, max(0.0, (value - lo) / (hi - lo) + (0.1 if event.key() == Qt.Key.Key_Right else -0.1)))
                if hi != lo
                else 0.0
            )
            outcome = playinput.slide(ctx, frame, [hit], apply(hit.matrix, w * f, h / 2))
            self._apply(outcome)
            return True
        return False

    # drawn over the preview

    def paint(self, painter) -> None:
        """The keyboard's widget outlined (the painter in the reference space), an open combo box's list, the info tip
        at the pointer."""
        if not self.active:
            return
        frame = self.window.frame
        hit = playinput.by_trail(frame, self.focus) if self.focus is not None and frame is not None else None
        if hit is not None:
            canvas.PainterCanvas(painter).outline(hit.matrix, hit.size, FOCUS, 2.0)
        found = self._open_list()
        if found is not None:
            _, names, rows = found
            for name, row in zip(names, rows, strict=True):
                painter.setPen(QPen(QColor(200, 170, 110)))
                painter.setBrush(QColor(28, 24, 20, 235))
                painter.drawRect(row)
                painter.setPen(QColor(240, 232, 214))
                painter.drawText(row.adjusted(8, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter), name)
        if not self.tip_text:
            return
        painter.resetTransform()
        metrics = QFontMetrics(painter.font())
        box = metrics.boundingRect(QRectF(0, 0, 420, 400).toRect(), int(Qt.TextFlag.TextWordWrap), self.tip_text)
        rect = QRectF(self.tip_at.x() + 16, self.tip_at.y() + 16, box.width() + 16, box.height() + 12)
        painter.setPen(QPen(QColor(200, 170, 110)))
        painter.setBrush(QColor(28, 24, 20, 235))
        painter.drawRect(rect)
        painter.setPen(QColor(240, 232, 214))
        painter.drawText(rect.adjusted(8, 6, -8, -6), int(Qt.TextFlag.TextWordWrap), self.tip_text)

    # the scene's start animation

    def _start_intro(self) -> None:
        """Play the open file's animation that its AddedToScene event starts, once, then hold its end."""
        doc = self.window.doc
        self._intro = None
        if doc is None:
            return
        graph, anims = bindings.read(doc.root), {a.guid: a for a in animations.read(doc.root)}
        for b in graph.bindings:
            if not any(e.kind == "path" and e.ids and e.ids[-1] == ADDED for e in b.sources):
                continue
            for t in b.targets:
                anim = anims.get(t.steps[-1][1]) if t.mode == playstate.ANIMATION and t.steps else None
                if anim is not None and anim.seconds > 0 and (self._intro is None or anim.seconds > self._intro[1]):
                    self._intro = (anim.guid, anim.seconds)
        if self._intro is not None:
            self._intro_t = 0.0
            self._clock.start()

    def _tick(self) -> None:
        if self._intro is None or not self.active:
            self._clock.stop()
            return
        if not self.window.preview.isVisible():
            return
        self._intro_t = min(self._intro[1], self._intro_t + STEP_MS / 1000)
        self.window._set_view(animation=(self._intro[0], self._intro_t))
        if self._intro_t >= self._intro[1]:
            self._clock.stop()

    def _stop_intro(self) -> None:
        self._clock.stop()
        if self._intro is not None:
            self._intro = None
            self.window._set_view(animation=None)


def _drawn(frame) -> list:
    """What a frame draws, to tell whether a step of the animations' clock moved anything."""
    return [(i.kind, i.matrix, i.size, i.alpha, i.color, i.image_id, i.text) for i in frame.items] if frame else []


class PlayedList(QTreeWidget):
    """The Preview settings' list of the choices Play mode made: each field's path of names and its value."""

    def __init__(self) -> None:
        super().__init__()
        self.setHeaderLabels([tr("ui.play_choice_field"), tr("common.value")])
        self.setRootIsDecorated(False)
        self.setMaximumHeight(120)
        self.setToolTip(tr("ui.play_choices_tip"))
        self.setAccessibleName(tr("ui.play_choices"))
        self.setVisible(False)

    def show_choices(self, store: previewdefaults.Store | None) -> None:
        self.clear()
        found = previewdefaults.play_choices(store.defaults) if store is not None else {}
        for name, value in sorted(found.items()):
            QTreeWidgetItem(self, [name, str(value)])
        self.setVisible(bool(found))


def install(window, view_menu) -> PlayController:
    """The window's Play mode: its switch in a toolbar and the View menu, and the preview's input routed to it."""
    play = window.play = PlayController(window)
    window.preview.play = play
    group = QActionGroup(window)
    bar = QToolBar(tr("ui.play_toolbar"), window, objectName="uiPlayBar")
    view_menu.addSeparator()
    for name, text, tip, keys, on in (
        ("design_mode_action", N_("ui.play_design_mode"), N_("ui.play_design_mode_tip"), "Shift+F5", False),
        ("play_mode_action", N_("ui.play_play_mode"), N_("ui.play_play_mode_tip"), "F5", True),
    ):
        action = QAction(tr(text), window, checkable=True, checked=not on, shortcut=QKeySequence(keys))
        action.setToolTip(tr(tip))
        action.triggered.connect(lambda _c=False, o=on: play.set_active(o))
        group.addAction(action)
        view_menu.addAction(action)
        bar.addAction(action)
        setattr(window, name, action)
    window.reset_play_action = view_menu.addAction(tr("ui.preview_reset_played"), play.reset_choices)
    window.addToolBar(bar)
    return play
