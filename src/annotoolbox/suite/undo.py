"""The one undo stack every document of the suite uses: an island's edit session, an asset change set.

A change is a ``SCommand`` (its label and the callables that take it back and do it again); ``CUndoStack`` keeps them in
order with the saved point (``clean``), so a window can say "unsaved" and a history list can jump. Plan:
``docs/tool-suite.md`` (C2).
"""

from __future__ import annotations

from dataclasses import dataclass

LIMIT = 200  # changes kept


@dataclass
class SCommand:
    label: str
    undo: object  # callables
    redo: object


class CUndoStack:
    """Changes in order, each with how to take it back and do it again; ``clean`` is the saved point."""

    def __init__(self, limit: int = LIMIT) -> None:
        self.m_limit, self.m_commands, self.m_index, self.m_clean = limit, [], 0, 0
        self.m_listeners: list = []

    def push(self, command: SCommand) -> None:
        """A change that has been made."""
        del self.m_commands[self.m_index :]
        if self.m_clean > self.m_index:
            self.m_clean = -1  # the saved state can no longer be reached
        self.m_commands.append(command)
        if len(self.m_commands) > self.m_limit:
            self.m_commands.pop(0)
            self.m_clean -= 1
        self.m_index = len(self.m_commands)
        self._changed()

    def undo(self) -> str | None:
        if not self.m_index:
            return None
        self.m_index -= 1
        self.m_commands[self.m_index].undo()
        self._changed()
        return self.m_commands[self.m_index].label

    def redo(self) -> str | None:
        if self.m_index >= len(self.m_commands):
            return None
        self.m_commands[self.m_index].redo()
        self.m_index += 1
        self._changed()
        return self.m_commands[self.m_index - 1].label

    @property
    def dirty(self) -> bool:
        return self.m_index != self.m_clean

    def mark_clean(self) -> None:
        self.m_clean = self.m_index
        self._changed()

    def labels(self) -> list[str]:
        return [command.label for command in self.m_commands]

    def _changed(self) -> None:
        for listener in list(self.m_listeners):
            listener()
