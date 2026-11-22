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
class SProblem:
    level: str  # ERROR stops the range, WARNING is shown and may be accepted
    text: str


@dataclass(frozen=True)
class SPublicChoice:
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
    return [(first, last, who, kind) for first, last, who, kind in game_data(game).get("published", [])]


def vanilla_runs(game: str) -> list[tuple[int, int]]:
    return [(first, last) for first, last in data()["vanilla"]["games"].get(game, {}).get("runs", [])]


def overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] <= second[1] and second[0] <= first[1]


def _claimed(spans, rng: tuple[int, int]) -> bool:
    return any(overlap(span, rng) for span in spans)


def _all_claims() -> list[tuple[int, int]]:
    """Every row of both registries (a range is meant to be free in 117's and 1800's at once) and the vanilla runs."""
    spans = [(first, last) for game in ("anno117", "anno1800") for first, last, _w, _k in published(game)]
    return spans + [run for game in data()["vanilla"]["games"] for run in vanilla_runs(game)]


def _free_aligned(guid_count: int, safe: tuple[int, int], taken) -> list[tuple[int, int]]:
    first, last = (
        -(-safe[0] // guid_count),
        (safe[1] + 1) // guid_count,
    )  # the last block may end on the range's last GUID
    blocks = ((index * guid_count, index * guid_count + guid_count - 1) for index in range(first, last))
    return [block for block in blocks if not _claimed(taken, block)]


def free_blocks(game: str, guid_count: int = BLOCK, count: int = 12, other=()) -> list[tuple[int, int]]:
    """``count`` aligned blocks of ``guid_count`` GUIDs, spread over the game's safe range, that no registry row, no
    vanilla GUID and none of the ``other`` ranges touches. A game without a registry: its declared free ranges."""
    safe = game_data(game).get("safe")
    if not safe:
        return [(first, last) for first, last in game_data(game).get("free", [])][:count]
    free = _free_aligned(guid_count, tuple(safe), _all_claims() + list(other))
    return free[:: max(1, len(free) // count)][:count]


def public_choices(game: str) -> list[SPublicChoice]:
    """What a person may pick as a publicly declared range: the community's personal-use range, then free blocks."""
    game_info = game_data(game)
    out: list[SPublicChoice] = []
    if not game_info.get("safe"):
        for first, last in free_blocks(game):
            source = tr(
                "common.measured_no_community_registry_declared",
                date=data()["vanilla"]["measured"],
            )
            out.append(SPublicChoice(f"free-{first}", f"{number(first)} - {number(last)}", (first, last), source, ""))
        return out
    registry = game_info["registry"]
    if game_info.get("personal"):
        first, last = game_info["personal"]
        out.append(
            SPublicChoice(
                PERSONAL_ID,
                tr("common.personal_use_span", span=f"{number(first)} - {number(last)}"),
                (first, last),
                f"{registry['text']}, {registry['url']}",
                tr("common.declared_people_not_publish_mod"),
            )
        )
    for first, last in free_blocks(game):
        out.append(
            SPublicChoice(
                f"free-{first}",
                tr("common.free_block_span", span=f"{number(first)} - {number(last)}"),
                (first, last),
                tr(
                    "common.free_registry_read_date_url",
                    registry=registry["text"],
                    date=data()["retrieved"],
                    url=registry["url"],
                ),
                tr("common.free_when_read_not_yours", how=game_info["claim"]),
                game_info["claim_url"],
            )
        )
    return out


def public_choice(game: str, key: str) -> SPublicChoice | None:
    return next((choice for choice in public_choices(game) if choice.key == key), None)


# the own range
def own_reserve(settings) -> tuple[int, int]:
    """The user's own reserve: a random free block of ``RESERVE_SIZE`` GUIDs in the safe range, outside every
    registry row, the community's personal-use range and the games' own GUIDs, made once and kept in the settings
    (``settings.own_reserve``; the caller saves them). Every own range of every project comes from it."""
    held = tuple(getattr(settings, "own_reserve", None) or ())
    if len(held) == 2 and all(type(value) is int for value in held) and 0 < held[0] < held[1] <= MAX_GUID:
        return held[0], held[1]  # anything else (a settings file edited by hand) is made anew
    safe = tuple(game_data("anno117")["safe"])
    made = secrets.choice(_free_aligned(RESERVE_SIZE, safe, _all_claims()))
    settings.own_reserve = list(made)
    return made


def own_range(settings, taken: dict[str, tuple[int, int]] | None = None) -> tuple[int, int]:
    """The first ``OWN_SIZE`` block of the reserve that none of the user's other projects (``taken``: name ->
    range) uses."""
    min_guid, max_guid = own_reserve(settings)
    used = [tuple(span) for span in (taken or {}).values()]
    for start in range(min_guid, max_guid, OWN_SIZE):
        block = (start, start + OWN_SIZE - 1)
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
) -> list[SProblem]:
    """What is wrong with ``rng`` as a range of new GUIDs for ``game``. ``others``: the user's other projects by name
    and their ranges; ``vanilla``: the game's own GUIDs when known exactly (else the measured runs are used)."""
    min_guid, max_guid = rng
    if not 0 < min_guid <= max_guid <= MAX_GUID:
        return [SProblem(ERROR, tr("common.not_range_positive_32_bit", lo=min_guid, hi=max_guid))]
    out: list[SProblem] = []
    if _claimed(vanilla_runs(game), rng) and (vanilla is None or any(min_guid <= guid <= max_guid for guid in vanilla)):
        out.append(
            SProblem(
                ERROR,
                tr(
                    "common.game_itself_uses_guids_new",
                    span=f"{number(min_guid)}-{number(max_guid)}",
                ),
            )
        )
    safe = game_data(game).get("safe")
    if safe and not (safe[0] <= min_guid and max_guid <= safe[1]):
        out.append(
            SProblem(
                WARNING,
                tr(
                    "common.outside_range_ubisoft_keeps_mods",
                    span=f"{number(safe[0])} - {number(safe[1])}",
                ),
            )
        )
    out += [SProblem(WARNING, _registry_clash(*row)) for row in published(game) if overlap(row[:2], rng)]
    for name, other in (others or {}).items():
        if overlap(tuple(other), rng):
            out.append(
                SProblem(
                    WARNING,
                    tr(
                        "common.overlaps_range_your_project",
                        name=name,
                        span=f"{number(other[0])}-{number(other[1])}",
                    ),
                )
            )
    return out


def _registry_clash(first: int, last: int, who: str, kind: str) -> str:
    span = f"{number(first)}-{number(last)}"
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


def blocking(problems: list[SProblem]) -> list[SProblem]:
    return [problem for problem in problems if problem.level == ERROR]
