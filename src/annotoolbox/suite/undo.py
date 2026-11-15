"""The one undo stack every document of the suite uses: an island's edit session, an asset change set.

A change is a ``Command`` (its label and the callables that take it back and do it again); ``UndoStack`` keeps them in
order with the saved point (``clean``), so a window can say "unsaved" and a history list can jump. Plan:
``docs/tool-suite.md`` (C2).
"""

from __future__ import annotations

from dataclasses import dataclass

LIMIT = 200  # changes kept


@dataclass
class Command:
    label: str
    undo: object  # callables
    redo: object


class UndoStack:
    """Changes in order, each with how to take it back and do it again; ``clean`` is the saved point."""

    def __init__(self, limit: int = LIMIT) -> None:
        self.limit, self.commands, self.index, self.clean = limit, [], 0, 0
        self.listeners: list = []

    def push(self, cmd: Command) -> None:
        """A change that has been made."""
        del self.commands[self.index :]
        if self.clean > self.index:
            self.clean = -1  # the saved state can no longer be reached
        self.commands.append(cmd)
        if len(self.commands) > self.limit:
            self.commands.pop(0)
            self.clean -= 1
        self.index = len(self.commands)
        self._changed()

    def undo(self) -> str | None:
        if not self.index:
            return None
        self.index -= 1
        self.commands[self.index].undo()
        self._changed()
        return self.commands[self.index].label

    def redo(self) -> str | None:
        if self.index >= len(self.commands):
            return None
        self.commands[self.index].redo()
        self.index += 1
        self._changed()
        return self.commands[self.index - 1].label

    @property
    def dirty(self) -> bool:
        return self.index != self.clean

    def mark_clean(self) -> None:
        self.clean = self.index
        self._changed()

    def labels(self) -> list[str]:
        return [c.label for c in self.commands]

    def _changed(self) -> None:
        for f in list(self.listeners):
            f()
