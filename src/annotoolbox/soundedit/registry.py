"""The edits of a bank by name: ``@edit(label)`` registers a function ``fn(doc, **args)`` with the label the status line
shows for it, ``EditError`` says why one cannot be made. ``edit`` is the entry (a decorator); docs/sound-editor.md
"What can be edited". Edits are looked up by name so that a list of them (``BankDoc.log``) can be made again on
another copy of a bank. The checks every edit module makes of its arguments and its reads are here too (``reading``,
``check_index``, ``check_u32``, ``check_byte``): each module keeps its reader and the words for ``what``.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from ..infra.i18n import tr

EDITS: dict[str, Callable] = {}
LABELS: dict[str, str] = {}  # edit name -> its label (a string id)


class EditError(ValueError):
    """An edit that cannot be made (the sentence says why); the bank is as it was."""


def edit(label: str) -> Callable[[Callable], Callable]:
    """Register an edit by its function's name; ``label`` (an ``N_`` text) is what the window's status line shows."""

    def register(fn: Callable) -> Callable:
        EDITS[fn.__name__] = fn
        LABELS[fn.__name__] = label
        return fn

    return register


def label(name: str) -> str:
    """The status-line text of an edit, in the interface language (the name itself for an edit without one)."""
    return tr(LABELS[name]) if name in LABELS else name


@contextmanager
def reading(what: str) -> Iterator[None]:
    """A read of a bank's bytes: what does not read becomes an ``EditError`` that says what it was (``what``)."""
    try:
        yield
    except EditError:
        raise
    except (ValueError, struct.error) as exc:
        raise EditError(tr("sound.what_not_read_exc", what=what, exc=exc)) from exc


def check_index(items: Sequence, index: int, what: str) -> int:
    """``index`` when ``items`` has one there, else an ``EditError`` ("there are 3 curves, none at 5")."""
    if not 0 <= index < len(items):
        raise EditError(tr("common.count_what_none_index", count=len(items), what=what, index=index))
    return index


def check_u32(value: int, what: str, zero: bool = True) -> int:
    """``value`` as a 32-bit unsigned number (``zero`` False: an id, which is never 0)."""
    if not (0 if zero else 1) <= int(value) < 2**32:
        raise EditError(tr("sound.32_bit_number", what=what))
    return int(value)


def check_byte(value: int, what: str) -> int:
    """``value`` as a number from 0 to 255."""
    if not 0 <= int(value) < 256:
        raise EditError(tr("common.number_0_255", what=what))
    return int(value)
