"""The UI editor's Play mode (Qt): the Design / Play switch (toolbar, View menu, F5 and Shift+F5), the preview's pointer
and keys taken by the input (``playinput``) while it is on, what they set laid out (``playstate``), the info tip and
the keyboard focus drawn over the preview, the scene's start animation played, and the choices the game keeps
written to the Preview settings (``CStore.set(..., play=True)``). docs/ui-editor.md "Play mode".

``install(window, view_menu)`` is the window's entry; ``CPlayController`` the mode; ``CPlayedList`` the Preview
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
from .playinput import SOutcome
from .scopevalues import ADDED

FOCUS = (120, 255, 160)
REST_MS, STEP_MS = 450, 33  # how long the pointer rests before an info tip shows; the start animation's frame
LIST_ROW = 28.0  # a row of an open combo box's list, reference pixels [I]
ARROWS = {Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right"}


class CPlayController(QObject):
    """Play mode of one UI window: on or off, what the input set (``state``), the choices the store holds."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window, self.m_active = window, False
        self.m_state = playstate.CPlayState()
        self.m_store: previewdefaults.CStore | None = None
        self.m_choices: dict = {}
        self.m_pointer: QPointF | None = None  # in the reference space
        self.m_pressed: playstate.Trail | None = None  # the widget a press went to, until the release
        self.m_focus: playstate.Trail | None = None  # the keyboard's widget
        self.m_tip_text, self.m_tip_at = "", QPointF()
        self._m_rest = QTimer(self, singleShot=True, interval=REST_MS)
        self._m_rest.timeout.connect(self._show_tip)
        self._m_intro, self._m_intro_seconds = None, 0.0  # the start animation being played: (guid, seconds)
        self._m_clock = QTimer(self, interval=STEP_MS)
        self._m_clock.timeout.connect(self._tick)
        self._m_ages = QTimer(self, interval=STEP_MS)  # the animations a change started, played over time
        self._m_ages.timeout.connect(self._age_step)
        self._m_still = None  # the latest change whose animations were found standing still
        self._m_waiting, self._m_longest = False, {}  # a layout asked for while one runs; the files' longest animation
        self._m_keys: tuple = (None, set())  # the files the shown frame placed
        self.m_drag: playinput.SDrag | None = None  # a scroll bar's handle or a draggable button being dragged

    # the switch

    def set_active(self, is_on: bool) -> None:
        """Play mode on or off; off shows the file as designed again (what was set stays for the next time)."""
        window = self.m_window
        for action, checked in (
            (getattr(window, "play_mode_action", None), is_on),
            (getattr(window, "design_mode_action", None), not is_on),
        ):
            if action is not None:
                action.setChecked(checked)
        if is_on == self.m_active:
            return
        self.m_active = is_on
        playinput.forget()
        self.m_state.m_passing.clear()
        self.m_pressed, self.m_focus, self.m_tip_text = None, None, ""
        if is_on:
            self._start_intro()
            self._apply()
            window.statusBar().showMessage(tr("ui.play_on"))
        else:
            self._stop_intro()
            self._m_ages.stop()
            self.m_drag = None
            window._when_idle(lambda: window._set_view(play=None))
            window.statusBar().showMessage(tr("ui.play_off"))
        window.preview.update()

    def file(self) -> str:
        doc = self.m_window.doc
        return doc.m_key if doc is not None else ""

    def _apply(self, outcome: SOutcome | None = None) -> None:
        """Lay the preview out with what the input set (only the placings it reaches are placed again)."""
        if outcome is not None:
            before = self.m_state.entries(self.file())
            for trail, path, value in outcome.kept:
                self.m_state.keep(self.file(), trail, path, value)
            for trail, first in outcome.scrolls:
                self.m_state.scroll(self.file(), trail, first)
            self.m_state.touch(self.file(), before, self.m_state.entries(self.file()), time.monotonic())
        if self.m_active:
            self._lay_out(is_whole=outcome is not None and bool(outcome.choices))
        for name, value in outcome.choices.items() if outcome is not None and self.m_store is not None else ():
            self.m_store.set(name, value, play=True)  # what read the field follows once this layout is done
        if outcome is not None and outcome.open:
            self.m_window.open_file(outcome.open)
        if outcome is not None and outcome.note:
            self.m_window.statusBar().showMessage(outcome.note)

    def _set_passing(self, passing: dict) -> None:
        """Take the hover values the input passes now and time the changes."""
        before = self.m_state.entries(self.file())
        self.m_state.m_passing = passing
        self.m_state.touch(self.file(), before, self.m_state.entries(self.file()), time.monotonic())

    def _trees(self) -> dict:
        """Play mode's values for the next layout, timed: the animations a change started play from then."""
        now, longest = time.monotonic(), self._longest_animation()
        latest = max(self.m_state.m_since.get(self.file(), {}).values(), default=None)
        if self.m_state.running(self.file(), now, longest) and latest != self._m_still:
            self._m_ages.start()
        else:
            self._m_ages.stop()
        return self.m_state.trees(self.file(), self.m_state.ages(self.file(), now, longest))

    def _lay_out(self, is_whole: bool = False, is_tick: bool = False) -> None:
        """Place what Play mode's values changed, off the window's thread: one layout at a time, the newest values
        win, the last finished frame stays shown meanwhile (``playstate.replay``; ``whole``: the whole file, as for
        a choice that every reader of its field follows; ``tick``: a step of the animations' clock)."""
        window = self.m_window
        if not self.m_active:
            return
        if window._m_placing:
            if not self._m_waiting:
                self._m_waiting = True
                window._when_idle(self._waited)
            return
        trees, doc, ctx = self._trees(), window.doc, window.m_ctx
        if doc is None or ctx is None:
            return
        if is_whole or window._m_view or ctx.m_play is None or window.m_frame is None:
            window._set_view(play=trees)
            return
        if trees == ctx.m_play:
            return
        old, frame = ctx.m_play, window.m_frame
        ctx.m_play, window._m_placing = trees, True
        window.m_background.run(lambda: playstate.replay(ctx, doc.m_key, doc.m_root, frame, old, trees),
                         lambda result: self._replayed(doc, frame, result, is_tick), window.m_sync_layout)  # fmt: skip

    def _replayed(self, doc, before, result, is_tick: bool) -> None:
        """A replay is done: shown as any layout; a step of the clock that changed nothing stops the clock until the
        next change (no animation is playing)."""
        if is_tick and not isinstance(result, Exception) and _drawn(result) == _drawn(before):
            self._m_still = max(self.m_state.m_since.get(self.file(), {}).values(), default=None)
            self._m_ages.stop()
        self.m_window._placed(doc, result)

    def _age_step(self) -> None:
        """One step of the clock that plays the animations a change started."""
        self._lay_out(is_tick=True)

    def _waited(self) -> None:
        """Lay out again once the layout that was running is done."""
        self._m_waiting = False
        self._lay_out()

    def _longest_animation(self) -> float:
        """The longest animation of the files the preview shows: how long the changes' animations may play."""
        frame, files = self.m_window.m_frame, self.m_window.game.m_files if self.m_window.game is not None else None
        if self._m_keys[0] is not frame:  # the files a frame placed, once per frame
            self._m_keys = (
                frame,
                {span.key for span in playstate.walk_spans(frame.spans if frame is not None else ())},
            )
        keys = {self.file(), *self._m_keys[1]}
        for key in keys - set(self._m_longest):
            root = files.root(key) if files is not None else None
            self._m_longest[key] = (
                max((animation.seconds for animation in animations.read(root)), default=0.0) if root else 0.0
            )
        return max((self._m_longest.get(k, 0.0) for k in keys), default=0.0)

    def reset_choices(self) -> None:
        """Back to the design state: the choices kept in the Preview settings and what this session set."""
        self.m_state.clear()
        if self.m_store is not None:
            self.m_store.reset(played=True)
        self._apply()

    # the store's choices

    def store_opened(self, store: previewdefaults.CStore) -> None:
        """A game was opened: its store holds the choices; the preview reads them from now on."""
        self.m_store, self.m_choices = store, previewdefaults.play_choices(store.m_defaults)
        store.m_listeners.append(lambda defaults, kept_store=store: self._store_changed(kept_store, defaults))
        if self.m_window.m_ctx is not None:
            self.m_window.m_ctx.m_play_choices = playstate.SChoices(dict(self.m_choices))

    def _store_changed(self, store, defaults) -> None:
        if store is not self.m_store:
            return
        new_choices = previewdefaults.play_choices(defaults)
        changed = {
            choice_name
            for choice_name in {*new_choices, *self.m_choices}
            if new_choices.get(choice_name) != self.m_choices.get(choice_name)
        }
        if not changed:
            return
        self.m_choices = new_choices
        snapshot = playstate.SChoices(dict(new_choices))

        def follow() -> None:
            ctx = self.m_window.m_ctx
            if ctx is not None:
                for name in changed:
                    for token in playstate.choice_tokens(name):  # what looked for it before it existed too
                        ctx.m_memo.forget(token)
            self.m_window._set_view(play_choices=snapshot)

        self.m_window._when_idle(follow)

    # the pointer

    def _hits(self) -> list:
        """The widgets under the pointer, none without one."""
        frame = self.m_window.m_frame
        return playinput.under(frame, self.m_pointer.x(), self.m_pointer.y()) if self.m_pointer is not None else []

    def _passing(self) -> dict:
        """The hover (and the press held) on what the pointer is over, the keyboard's widget and a wheel's segment."""
        ctx, frame, out = self.m_window.m_ctx, self.m_window.m_frame, {}
        for hit in self._hits():
            values = playinput.hover_values(ctx, hit)
            if values:
                out[hit.trail] = values
        focused = playinput.by_trail(frame, self.m_focus) if self.m_focus is not None else None
        if focused is not None:
            out.setdefault(focused.trail, {}).update(playinput.hover_values(ctx, focused))
        if self.m_pointer is not None:
            wheel = playinput.wheel_focus(ctx, frame, (self.m_pointer.x(), self.m_pointer.y()))
            if wheel is not None:
                out.setdefault(wheel[0].trail, {})[(playinput.FOCUSED,)] = wheel[1]
        if self.m_pressed is not None and self.m_pressed in out:
            held = playinput.by_trail(frame, self.m_pressed)
            if held is not None:
                out[self.m_pressed].update(playinput.press_values(ctx, held, False))
        if self.m_drag is not None and self.m_drag.kind == "draggable":  # its DragStarted while the drag lasts
            out.setdefault(self.m_drag.trail, {})[(playinput.DRAG_STARTED,)] = True
        return out

    def _follow(self) -> None:
        """Lay the preview out again when what the pointer and the focus pass has changed."""
        passing = self._passing()
        if passing != self.m_state.m_passing:
            self._set_passing(passing)
            self._apply()

    def mouse(self, kind: str, position: QPointF, widget_at: QPointF) -> None:
        """The pointer moved, was pressed or released (``kind``) at a point of the reference space."""
        if self.m_window.m_frame is None or self.m_window.m_ctx is None:
            return
        self.m_pointer, self.m_tip_at = position, widget_at
        ctx, frame, point = self.m_window.m_ctx, self.m_window.m_frame, (position.x(), position.y())
        if kind == "press":
            if self._pick_from_list(point):
                return
            hit = playinput.target(ctx, self._hits())
            self.m_pressed = hit.trail if hit is not None else None
            self.m_drag = playinput.begin_drag(ctx, frame, self._hits(), point)
            self._drag_or_slide(point)
        elif kind == "move":
            if self.m_tip_text:
                self.m_tip_text = ""
                self.m_window.preview.update()
            self._m_rest.start()
            if self.m_pressed is not None or self.m_drag is not None:
                self._drag_or_slide(point)
        elif kind == "release":
            pressed, drag, self.m_pressed, self.m_drag = self.m_pressed, self.m_drag, None, None
            hits = self._hits()
            if drag is not None and drag.kind == "draggable":  # a drag ends: its DragStopped, no click
                held = playinput.by_trail(frame, drag.trail)
                self._follow_release(held, {(playinput.DRAG_STOPPED,): True})
                self._apply()
                return
            if pressed is not None and any(candidate.trail == pressed for candidate in hits):
                held = playinput.by_trail(frame, pressed)
                outcome = playinput.click(ctx, frame, hits, point, self.file(), self._models())
                self._follow_release(held)
                self._apply(outcome)
                return
        self._follow()

    def _models(self):
        """The game's models, None without a game."""
        game = self.m_window.game
        return game.m_models if game is not None else None

    def _follow_release(self, held, extra: dict | None = None) -> None:
        """The release events on the widget that was pressed, while the pointer stays on it."""
        passing = self._passing()
        if held is not None:
            passing.setdefault(held.trail, {}).update(playinput.press_values(self.m_window.m_ctx, held, True))
            passing[held.trail].update(extra or {})
        self._set_passing(passing)

    def _drag_or_slide(self, point) -> None:
        """A press held and moved: a scroll bar or a draggable button follows the pointer, else a slider."""
        ctx, frame = self.m_window.m_ctx, self.m_window.m_frame
        if self.m_drag is not None:
            scrolls = self.m_state.m_scrolls.get(self.file(), {})
            self._apply(playinput.drag_to(ctx, frame, self.m_drag, point, scrolls))
            return
        outcome = playinput.slide(ctx, frame, self._hits(), point)
        if outcome is not None:
            self._apply(outcome)

    def wheel(self, position: QPointF, steps: int) -> None:
        """The wheel over a list or a grid scrolls it by an element (a grid by a row)."""
        frame, ctx = self.m_window.m_frame, self.m_window.m_ctx
        if frame is None or ctx is None:
            return
        self.m_pointer = position
        hits = self._hits()
        for hit in reversed(hits):
            first = self.m_state.first(self.file(), hit.trail)
            found = playinput.scroll(ctx, frame, [hit], steps, first)
            if found is not None:
                self._apply(SOutcome(scrolls=[found]))
                return

    def leave(self) -> None:
        """The pointer left the preview: drop what it held."""
        self.m_pointer, self.m_pressed, self.m_drag, self.m_tip_text = None, None, None, ""
        self._m_rest.stop()
        self._follow()
        self.m_window.preview.update()

    # a combo box's list, drawn over the preview while it is open

    def _open_list(self):
        """The open combo box: its hit, its entries and each entry's row (reference space), or None."""
        frame, ctx = self.m_window.m_frame, self.m_window.m_ctx
        if frame is None or ctx is None:
            return None
        for trail, values in self.m_state.m_kept.get(self.file(), {}).items():
            if values.get((playinput.COMBO_OPEN,)):
                hit = playinput.by_trail(frame, trail)
                if hit is not None and "combo" in playinput.kinds(ctx, hit):
                    names = playinput.entries(ctx, hit, self._models())
                    (x0, y0), (x1, y1) = (apply(hit.matrix, *corner) for corner in ((0, 0), hit.size))
                    rows = [QRectF(x0, y1 + i * LIST_ROW, max(x1 - x0, 120.0), LIST_ROW) for i in range(len(names))]
                    return hit, names, rows
        return None

    def _pick_from_list(self, point) -> bool:
        """A press on an entry of the open combo box's list picks it; True when it did."""
        found = self._open_list()
        if found is None:
            return False
        hit, _names, rows = found
        index = next((i for i, row in enumerate(rows) if row.contains(QPointF(*point))), None)
        if index is None:
            return False
        self._apply(playinput.pick_entry(self.m_window.m_ctx, self.m_window.m_frame, hit, index))
        return True

    def _show_tip(self) -> None:
        """Show the info tip of the widget under the pointer that has rested."""
        if self.m_active and self.m_pointer is not None and self.m_window.m_ctx is not None:
            self.m_tip_text = playinput.tip(self.m_window.m_ctx, self._hits())
            self.m_window.preview.update()

    # the keyboard

    def move_focus(self, step: int) -> None:
        """Tab and Shift+Tab: the next or the previous widget that takes a press."""
        order = playinput.focusable(self.m_window.m_ctx, self.m_window.m_frame)
        if not order:
            return
        trails = [hit.trail for hit in order]
        current = trails.index(self.m_focus) if self.m_focus in trails else (-1 if step > 0 else 0)
        self.m_focus = trails[(current + step) % len(trails)]
        self._follow()
        self.m_window.preview.update()

    def move_focus_to(self, direction: str) -> bool:
        """An arrow key: the focus moves to the neighbour that way (``playinput.neighbour``); True when it moved."""
        ctx, frame = self.m_window.m_ctx, self.m_window.m_frame
        order = playinput.focusable(ctx, frame)
        focused = playinput.by_trail(frame, self.m_focus) if self.m_focus is not None else None
        found = playinput.neighbour(ctx, frame, focused, direction, order)
        if found is None:
            return False
        self.m_focus = found.trail
        self._follow()
        self.m_window.preview.update()
        return True

    def key(self, event) -> bool:
        """Enter or Space presses the widget that has the focus, left and right move a slider or step a toggle, the
        arrows move the focus to the neighbour that way; True when taken."""
        frame, ctx = self.m_window.m_frame, self.m_window.m_ctx
        hit = playinput.by_trail(frame, self.m_focus) if self.m_focus is not None and frame is not None else None
        if ctx is None or frame is None:
            return False
        sideways = event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
        if hit is not None and sideways and "switch" in playinput.kinds(ctx, hit):
            width_px, height_px = hit.size
            point = apply(hit.matrix, width_px * (0.25 if event.key() == Qt.Key.Key_Left else 0.75), height_px / 2)
            self._apply(playinput.click(ctx, frame, [hit], point, self.file(), self._models()))
            return True
        if (hit is None or not sideways or "slider" not in playinput.kinds(ctx, hit)) and event.key() in ARROWS:
            return self.move_focus_to(ARROWS[event.key()])
        if hit is None:
            return False
        width_px, height_px = hit.size
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            point = apply(hit.matrix, width_px / 2, height_px / 2)
            self._follow_release(hit)
            self._apply(playinput.click(ctx, frame, [hit], point, self.file(), self._models()))
            return True
        if sideways and "slider" in playinput.kinds(ctx, hit):
            value = float(hit.values.get((playinput.S_VALUE,)) or 0.0)
            min_value, max_value = playinput.slider_range(hit)
            fraction = (
                min(
                    1.0,
                    max(
                        0.0,
                        (value - min_value) / (max_value - min_value)
                        + (0.1 if event.key() == Qt.Key.Key_Right else -0.1),
                    ),
                )
                if max_value != min_value
                else 0.0
            )
            outcome = playinput.slide(ctx, frame, [hit], apply(hit.matrix, width_px * fraction, height_px / 2))
            self._apply(outcome)
            return True
        return False

    # drawn over the preview

    def paint(self, painter) -> None:
        """The keyboard's widget outlined (the painter in the reference space), an open combo box's list, the info tip
        at the pointer."""
        if not self.m_active:
            return
        frame = self.m_window.m_frame
        hit = playinput.by_trail(frame, self.m_focus) if self.m_focus is not None and frame is not None else None
        if hit is not None:
            canvas.CPainterCanvas(painter).outline(hit.matrix, hit.size, FOCUS, 2.0)
        found = self._open_list()
        if found is not None:
            _, names, rows = found
            for name, row in zip(names, rows, strict=True):
                painter.setPen(QPen(QColor(200, 170, 110)))
                painter.setBrush(QColor(28, 24, 20, 235))
                painter.drawRect(row)
                painter.setPen(QColor(240, 232, 214))
                painter.drawText(row.adjusted(8, 0, -8, 0), int(Qt.AlignmentFlag.AlignVCenter), name)
        if not self.m_tip_text:
            return
        painter.resetTransform()
        metrics = QFontMetrics(painter.font())
        box = metrics.boundingRect(QRectF(0, 0, 420, 400).toRect(), int(Qt.TextFlag.TextWordWrap), self.m_tip_text)
        rect = QRectF(self.m_tip_at.x() + 16, self.m_tip_at.y() + 16, box.width() + 16, box.height() + 12)
        painter.setPen(QPen(QColor(200, 170, 110)))
        painter.setBrush(QColor(28, 24, 20, 235))
        painter.drawRect(rect)
        painter.setPen(QColor(240, 232, 214))
        painter.drawText(rect.adjusted(8, 6, -8, -6), int(Qt.TextFlag.TextWordWrap), self.m_tip_text)

    # the scene's start animation

    def _start_intro(self) -> None:
        """Play the open file's animation that its AddedToScene event starts, once, then hold its end."""
        doc = self.m_window.doc
        self._m_intro = None
        if doc is None:
            return
        graph, by_guid = bindings.read(doc.m_root), {item.guid: item for item in animations.read(doc.m_root)}
        for binding in graph.bindings:
            if not any(source.kind == "path" and source.ids and source.ids[-1] == ADDED for source in binding.sources):
                continue
            for target in binding.targets:
                animation = (
                    by_guid.get(target.steps[-1][1]) if target.mode == playstate.ANIMATION and target.steps else None
                )
                if (
                    animation is not None
                    and animation.seconds > 0
                    and (self._m_intro is None or animation.seconds > self._m_intro[1])
                ):
                    self._m_intro = (animation.guid, animation.seconds)
        if self._m_intro is not None:
            self._m_intro_seconds = 0.0
            self._m_clock.start()

    def _tick(self) -> None:
        """One step of the start animation's clock: advance the time and show it."""
        if self._m_intro is None or not self.m_active:
            self._m_clock.stop()
            return
        if not self.m_window.preview.isVisible():
            return
        self._m_intro_seconds = min(self._m_intro[1], self._m_intro_seconds + STEP_MS / 1000)
        self.m_window._set_view(animation=(self._m_intro[0], self._m_intro_seconds))
        if self._m_intro_seconds >= self._m_intro[1]:
            self._m_clock.stop()

    def _stop_intro(self) -> None:
        """Stop the start animation and show the file without it."""
        self._m_clock.stop()
        if self._m_intro is not None:
            self._m_intro = None
            self.m_window._set_view(animation=None)


def _drawn(frame) -> list:
    """What a frame draws, to tell whether a step of the animations' clock moved anything."""
    return (
        [(item.kind, item.matrix, item.size, item.alpha, item.color, item.image_id, item.text) for item in frame.items]
        if frame
        else []
    )


class CPlayedList(QTreeWidget):
    """The Preview settings' list of the choices Play mode made: each field's path of names and its value."""

    def __init__(self) -> None:
        super().__init__()
        self.setHeaderLabels([tr("ui.play_choice_field"), tr("common.value")])
        self.setRootIsDecorated(False)
        self.setMaximumHeight(120)
        self.setToolTip(tr("ui.play_choices_tip"))
        self.setAccessibleName(tr("ui.play_choices"))
        self.setVisible(False)

    def show_choices(self, store: previewdefaults.CStore | None) -> None:
        """List the choices Play mode made, or hide the list when there are none."""
        self.clear()
        found = previewdefaults.play_choices(store.m_defaults) if store is not None else {}
        for name, value in sorted(found.items()):
            QTreeWidgetItem(self, [name, str(value)])
        self.setVisible(bool(found))


def install(window, view_menu) -> CPlayController:
    """The window's Play mode: its switch in a toolbar and the View menu, and the preview's input routed to it."""
    play = window.play = CPlayController(window)
    window.preview.m_play = play
    group = QActionGroup(window)
    bar = QToolBar(tr("ui.play_toolbar"), window, objectName="uiPlayBar")
    view_menu.addSeparator()
    for name, text, tip, keys, is_play in (
        ("design_mode_action", N_("ui.play_design_mode"), N_("ui.play_design_mode_tip"), "Shift+F5", False),
        ("play_mode_action", N_("ui.play_play_mode"), N_("ui.play_play_mode_tip"), "F5", True),
    ):
        action = QAction(tr(text), window, checkable=True, checked=not is_play, shortcut=QKeySequence(keys))
        action.setToolTip(tr(tip))
        action.triggered.connect(lambda _checked=False, is_play=is_play: play.set_active(is_play))
        group.addAction(action)
        view_menu.addAction(action)
        bar.addAction(action)
        setattr(window, name, action)
    window.reset_play_action = view_menu.addAction(tr("ui.preview_reset_played"), play.reset_choices)
    window.addToolBar(bar)
    return play
