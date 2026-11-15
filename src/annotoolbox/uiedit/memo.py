"""Results of placing that outlive a frame: what a widget file's bindings evaluate to for given inputs, what it measures,
and the draw items it makes, each kept with what it was computed from.

``Memo.compute(key, token, make)`` runs ``make`` once per key and remembers the tokens it read: ``token`` (a file),
every token ``note``d while it ran (another file, a sample value) and those of every kept result it took. ``forget``
drops what read a token, so an edit places the edited file again and nothing else; a computation running while one
of its tokens is forgotten is not kept; one that holds a placing ``cut`` short where it was asked from depends on that
place too and is kept under the key and the place (``context``; docs/tool-suite.md section 20, "Speed").
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

MISS = object()
LIMIT = 200_000  # entries: far more than any scene holds; a runaway key space starts over instead of growing
_STALE = object()  # in a running computation's tokens: one of them changed under it


class Memo:
    def __init__(self) -> None:
        self.items: dict[object, object] = {}
        self.deps: dict[object, frozenset] = {}
        self.users: dict[Hashable, set] = {}  # token -> the keys whose result read it
        self.reading: list[set] = []  # the tokens read by the computations running now, innermost last
        self.running: list[object] = []  # their keys (None for ``gather``)
        self.owner_of: Callable[[object], object] = lambda key: None  # the file a key's result is the placing of
        self.dropped = 0  # results dropped by ``forget`` since the counter was last reset (``layout.place``)

    def clear(self) -> None:
        self.items.clear()
        self.deps.clear()
        self.users.clear()
        for tokens in self.reading:
            tokens.add(_STALE)

    def note(self, token: Hashable) -> None:
        """The running computation (if any) depends on ``token``."""
        if self.reading:
            self.reading[-1].add(token)

    def forget(self, token: Hashable) -> int:
        """Drop every result that read ``token``; the running computations that read it are not kept. The count."""
        n = 0
        for key in self.users.pop(token, ()):
            n += self._drop(key)
        for tokens in self.reading:
            if token in tokens:
                tokens.add(_STALE)
                n += 1
        self.dropped += n
        return n

    def _drop(self, key: object) -> bool:
        """Remove a kept result and the entries of ``users`` that name it (else they would grow with every edit and
        drop a later result of the same key for a token it no longer reads). Whether there was one."""
        for token in self.deps.pop(key, ()):
            readers = self.users.get(token)
            if readers is not None:
                readers.discard(key)
                if not readers:
                    del self.users[token]
        return self.items.pop(key, MISS) is not MISS

    def gather(self, make: Callable[[], object]) -> tuple[object, set]:
        """``make()`` and the tokens it read, kept results included (nothing is kept under a key)."""
        self.reading.append(set())
        self.running.append(None)
        try:
            value = make()
        finally:
            tokens = self.reading.pop()
            self.running.pop()
        return value, tokens

    def cut(self, name: object) -> None:
        """The running computation places ``name`` (a file) inside itself, cut short there (None: too deep). What
        runs inside the placing of that file is kept by where it was asked from, up to that placing's own result
        (``owner_of``), which holds its own cycle wherever it is asked from."""
        self.note(("cut", name))

    def get(self, key: object) -> object:
        """The kept result of ``key`` (``MISS`` without one); the running computation now depends on its tokens too."""
        hit = self.items.get(key, MISS)
        if hit is not MISS and self.reading:
            self.reading[-1] |= self.deps.get(key, frozenset())
        return hit

    def compute(self, key: object, token: Hashable, make: Callable[[], object], provisional: object = MISS,
                context: Hashable = None) -> object:  # fmt: skip
        """The result of ``make()`` kept under ``key``, or under ``(key, context)`` when it holds a cut;
        ``provisional`` stands in for it while ``make`` runs (a file that reads its own result gets that)."""
        if len(self.items) > LIMIT:
            self.clear()
        if provisional is not MISS:
            self.items[key] = provisional
        self.reading.append({token})
        self.running.append(key)
        try:
            value = make()
        except BaseException:
            self._drop(key)
            raise
        finally:
            used = self.reading.pop()
            self.running.pop()
            used.discard(("cut", self.owner_of(key)))  # its own cycle: the same wherever it is asked from
            cuts = frozenset(t for t in used if isinstance(t, tuple) and t[:1] == ("cut",))
            if self.reading:
                self.reading[-1] |= used
        self._drop(key)  # the stand-in, and whatever an earlier result of the key read
        if cuts:
            key = (key, context)  # where it was asked from decides it too
        if _STALE in used or (cuts and context is None):
            return value
        self.items[key] = value
        self.deps[key] = frozenset(used)
        for t in used:
            self.users.setdefault(t, set()).add(key)
        return value
