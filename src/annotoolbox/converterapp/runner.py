"""An entry's command lines run off the window's thread, in this process: what a command prints goes to the
window's log line by line, and Stop takes effect at the next line it prints or the next source.

``run(change, parser, lines, talk)`` runs each command line in turn and returns an ``Outcome``. While a job runs,
``sys.stdout`` and ``sys.stderr`` are a ``Router``: the job thread's writes go to its log, every other thread's go
on as before. No Qt. docs/converter-app.md, "Running".
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from . import commandform


class Halted(BaseException):
    """Stop was asked. A BaseException, so that a command's own ``except Exception`` around one file lets it pass."""


_sinks: dict[int, Callable[[str], None]] = {}
_lock = threading.Lock()
_active = [0]


class Router:
    """A standard stream while a job runs: a job thread's text goes to its sink, the rest to the stream it replaced."""

    def __init__(self, original) -> None:
        self.original = original

    def write(self, text: str) -> int:
        sink = _sinks.get(threading.get_ident())
        if sink is None:
            return self.original.write(text) if self.original is not None else len(text)
        sink(text)
        return len(text)

    def flush(self) -> None:
        if self.original is not None:
            self.original.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str):
        return getattr(self.original, name)


def _route(sink: Callable[[str], None] | None) -> None:
    """Send this thread's output to ``sink``; None hands it back (the streams return once no job runs)."""
    with _lock:
        if sink is not None:
            if not _active[0]:
                sys.stdout, sys.stderr = Router(sys.stdout), Router(sys.stderr)
            _active[0] += 1
            _sinks[threading.get_ident()] = sink
            return
        _sinks.pop(threading.get_ident(), None)
        _active[0] -= 1
        if not _active[0]:
            for name in ("stdout", "stderr"):
                stream = getattr(sys, name)
                if isinstance(stream, Router):
                    setattr(sys, name, stream.original)


@dataclass
class Talk:
    """What a job and its window say to each other: ``log(line)``, ``step(done, total)`` and the stop request."""

    log: Callable[[str], None]
    step: Callable[[int, int], None] = lambda _done, _total: None
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class Outcome:
    """How the command lines ended: their exit statuses in order, and whether Stop ended the job."""

    statuses: list[int] = field(default_factory=list)
    stopped: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for s in self.statuses if s)


class _Lines:
    """A command's text as whole lines for the log; each piece it writes is a place where Stop takes effect."""

    def __init__(self, talk: Talk) -> None:
        self.talk, self.rest = talk, ""

    def __call__(self, text: str) -> None:
        if self.talk.stop.is_set():
            raise Halted
        whole, _cut, self.rest = (self.rest + text).rpartition("\n")
        for line in whole.split("\n") if _cut else ():
            self.talk.log(line)

    def close(self) -> None:
        if self.rest:
            self.talk.log(self.rest)
        self.rest = ""


def run(change, parser, lines: list[list[str]], talk: Talk, program: str = "") -> Outcome:
    """Run each command line of ``lines`` (``commandform.argv``'s) in turn on this thread."""
    outcome = Outcome()
    for done, line in enumerate(lines):
        if talk.stop.is_set():
            outcome.stopped = True
            break
        talk.step(done, len(lines))
        talk.log("> " + commandform.shown(program, line))
        try:
            outcome.statuses.append(_one(change, parser, line, talk))
        except Halted:
            outcome.stopped = True
            break
    talk.step(len(outcome.statuses), len(lines))
    return outcome


def _one(change, parser, line: list[str], talk: Talk) -> int:
    sink = _Lines(talk)
    _route(sink)
    try:
        args = commandform.parse(change, parser, line)
        if change.prepare is not None:
            change.prepare(args)
        status = args.func(args)
        return int(status or 0)
    except commandform.ParseError as exc:
        sink.close()
        talk.log(f"error: {exc}")
        return 2
    except SystemExit as exc:  # a command that ends itself: a status, nothing (done), or the message it leaves with
        if isinstance(exc.code, int) or exc.code is None:
            return exc.code or 0
        sink.close()
        talk.log(str(exc.code))
        return 1
    except Exception as exc:  # the command's error is the run's: the log says it, the next source goes on
        sink.close()
        lines = str(exc).splitlines()
        talk.log(f"error: {lines[0] if lines else type(exc).__name__}")
        return 1
    finally:
        sink.close()
        _route(None)
