"""Where a project or change set gets its GUID range: the user's own range, a publicly declared free range, or a
range typed in, and the checks that go with each (docs/tool-suite.md, "GUID ranges"). Stdlib only.

The data is ``data/guidranges.json``: per game the safe range Ubisoft declares, the community's registry of claimed
ranges (with where to claim one), and the GUIDs the installed game itself uses (measured). ``public_choices`` lists
what a person may pick for a game, ``own_range`` hands out the user's own (from a reserve made once and kept in
the settings), ``check`` says what is wrong with a range (errors stop it, warnings are shown and may be accepted)
and ``resolve`` turns a choice into the numbers. A range is a ``(first, last)`` tuple.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ..infra.i18n import number, tr

OWN, PUBLIC, CUSTOM = "own", "public", "custom"
CHOICES = (OWN, PUBLIC, CUSTOM)
RESERVE_SIZE = 1_000_000  # the own reserve every project of the user takes its range from
OWN_SIZE = 100_000  # one project's own range (the community asks for no more than this per entry)
BLOCK = 10_000  # the size of a suggested public range (the community's "usually enough")
MAX_GUID = 2**31 - 1
ERROR, WARNING = "error", "warning"
DATA = Path(__file__).parent / "data" / "guidranges.json"
PERSONAL_ID = "personal-use"


@dataclass(frozen=True)
class Problem:
    level: str  # ERROR stops the range, WARNING is shown and may be accepted
    text: str


@dataclass(frozen=True)
class PublicChoice:
    key: str  # what the project keeps: ``personal-use`` or ``free-<first>``
    label: str
    span: tuple[int, int]
    source: str  # the text and link that declare it
    note: str  # what the person must do (claim it, or that it is shared)
    claim_url: str = ""


@cache
def data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def game_data(game: str) -> dict:
    return data()["games"].get(game, {})


def published(game: str) -> list[tuple[int, int, str, str]]:
    """The registry's rows for a game: (first, last, who, kind), kind ``claim``, ``reserved`` or ``personal``."""
    return [(a, b, who, kind) for a, b, who, kind in game_data(game).get("published", [])]


def vanilla_runs(game: str) -> list[tuple[int, int]]:
    return [(a, b) for a, b in data()["vanilla"]["games"].get(game, {}).get("runs", [])]


def overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _claimed(spans, rng: tuple[int, int]) -> bool:
    return any(overlap(s, rng) for s in spans)


def _all_claims() -> list[tuple[int, int]]:
    """Every row of both registries (a range is meant to be free in 117's and 1800's at once) and the vanilla runs."""
    spans = [(a, b) for g in ("anno117", "anno1800") for a, b, _w, _k in published(g)]
    return spans + [r for g in data()["vanilla"]["games"] for r in vanilla_runs(g)]


def _free_aligned(size: int, safe: tuple[int, int], taken) -> list[tuple[int, int]]:
    first, last = -(-safe[0] // size), (safe[1] + 1) // size  # the last block may end on the range's last GUID
    blocks = ((n * size, n * size + size - 1) for n in range(first, last))
    return [b for b in blocks if not _claimed(taken, b)]


def free_blocks(game: str, size: int = BLOCK, count: int = 12, other=()) -> list[tuple[int, int]]:
    """``count`` aligned blocks of ``size`` GUIDs, spread over the game's safe range, that no registry row, no
    vanilla GUID and none of the ``other`` ranges touches. A game without a registry: its declared free ranges."""
    safe = game_data(game).get("safe")
    if not safe:
        return [(a, b) for a, b in game_data(game).get("free", [])][:count]
    free = _free_aligned(size, tuple(safe), _all_claims() + list(other))
    return free[:: max(1, len(free) // count)][:count]


def public_choices(game: str) -> list[PublicChoice]:
    """What a person may pick as a publicly declared range: the community's personal-use range, then free blocks."""
    info = game_data(game)
    out: list[PublicChoice] = []
    if not info.get("safe"):
        for a, b in free_blocks(game):
            source = tr(
                "common.measured_no_community_registry_declared",
                date=data()["vanilla"]["measured"],
            )
            out.append(PublicChoice(f"free-{a}", f"{number(a)} - {number(b)}", (a, b), source, ""))
        return out
    registry = info["registry"]
    if info.get("personal"):
        a, b = info["personal"]
        out.append(
            PublicChoice(
                PERSONAL_ID,
                tr("common.personal_use_span", span=f"{number(a)} - {number(b)}"),
                (a, b),
                f"{registry['text']}, {registry['url']}",
                tr("common.declared_people_not_publish_mod"),
            )
        )
    for a, b in free_blocks(game):
        out.append(
            PublicChoice(
                f"free-{a}",
                tr("common.free_block_span", span=f"{number(a)} - {number(b)}"),
                (a, b),
                tr(
                    "common.free_registry_read_date_url",
                    registry=registry["text"],
                    date=data()["retrieved"],
                    url=registry["url"],
                ),
                tr("common.free_when_read_not_yours", how=info["claim"]),
                info["claim_url"],
            )
        )
    return out


def public_choice(game: str, key: str) -> PublicChoice | None:
    return next((c for c in public_choices(game) if c.key == key), None)


# the own range
def own_reserve(settings) -> tuple[int, int]:
    """The user's own reserve: a random free block of ``RESERVE_SIZE`` GUIDs in the safe range, outside every
    registry row, the community's personal-use range and the games' own GUIDs, made once and kept in the settings
    (``settings.own_reserve``; the caller saves them). Every own range of every project comes from it."""
    held = tuple(getattr(settings, "own_reserve", None) or ())
    if len(held) == 2 and all(type(v) is int for v in held) and 0 < held[0] < held[1] <= MAX_GUID:
        return held[0], held[1]  # anything else (a settings file edited by hand) is made anew
    safe = tuple(game_data("anno117")["safe"])
    made = secrets.choice(_free_aligned(RESERVE_SIZE, safe, _all_claims()))
    settings.own_reserve = list(made)
    return made


def own_range(settings, taken: dict[str, tuple[int, int]] | None = None) -> tuple[int, int]:
    """The first ``OWN_SIZE`` block of the reserve that none of the user's other projects (``taken``: name ->
    range) uses."""
    lo, hi = own_reserve(settings)
    used = [tuple(r) for r in (taken or {}).values()]
    for a in range(lo, hi, OWN_SIZE):
        block = (a, a + OWN_SIZE - 1)
        if not _claimed(used, block):
            return block
    raise ValueError(tr("common.own_reserve_used_up_your"))


def resolve(
    choice: str, source: str, custom: tuple[int, int], settings, game: str, taken: dict | None = None
) -> tuple[int, int]:
    """The numbers a choice stands for: a own block no other project of the user uses, the public choice
    ``source`` of the game, or the typed range ``custom``."""
    if choice == OWN:
        return own_range(settings, taken)
    if choice == PUBLIC:
        found = public_choice(game, source)
        if found is None:
            raise ValueError(f"{source!r} is not a declared range of {game}")
        return found.span
    if choice == CUSTOM:
        return tuple(custom)
    raise ValueError(f"unknown GUID range choice {choice!r}")


# the checks
def check(
    game: str,
    rng: tuple[int, int],
    others: dict[str, tuple[int, int]] | None = None,
    vanilla=None,
) -> list[Problem]:
    """What is wrong with ``rng`` as a range of new GUIDs for ``game``. ``others``: the user's other projects by name
    and their ranges; ``vanilla``: the game's own GUIDs when known exactly (else the measured runs are used)."""
    lo, hi = rng
    if not 0 < lo <= hi <= MAX_GUID:
        return [Problem(ERROR, tr("common.not_range_positive_32_bit", lo=lo, hi=hi))]
    out: list[Problem] = []
    if _claimed(vanilla_runs(game), rng) and (vanilla is None or any(lo <= g <= hi for g in vanilla)):
        out.append(
            Problem(
                ERROR,
                tr(
                    "common.game_itself_uses_guids_new",
                    span=f"{number(lo)}-{number(hi)}",
                ),
            )
        )
    safe = game_data(game).get("safe")
    if safe and not (safe[0] <= lo and hi <= safe[1]):
        out.append(
            Problem(
                WARNING,
                tr(
                    "common.outside_range_ubisoft_keeps_mods",
                    span=f"{number(safe[0])} - {number(safe[1])}",
                ),
            )
        )
    out += [Problem(WARNING, _registry_clash(*row)) for row in published(game) if overlap(row[:2], rng)]
    for name, other in (others or {}).items():
        if overlap(tuple(other), rng):
            out.append(
                Problem(
                    WARNING,
                    tr(
                        "common.overlaps_range_your_project",
                        name=name,
                        span=f"{number(other[0])}-{number(other[1])}",
                    ),
                )
            )
    return out


def _registry_clash(a: int, b: int, who: str, kind: str) -> str:
    span = f"{number(a)}-{number(b)}"
    if kind == "personal":
        return tr(
            "common.overlaps_communitys_personal_use_range",
            span=span,
        )
    if kind == "reserved":
        return tr("common.overlaps_range_registry_reserves_special", span=span)
    return tr(
        "common.overlaps_range_registry_gives_both",
        span=span,
        who=who,
    )


def blocking(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.level == ERROR]
