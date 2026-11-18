"""A save's summary for its game: ``summarize(save)`` dispatches to ``summary117`` and ``summary2070``; a game whose
layout no sample pinned gets the part list only (``docs/save-editor.md``, "The containers").
"""

from __future__ import annotations

from ..infra.i18n import N_
from . import summary117, summary2070, summaryfiledb, summaryold
from .container import FILEDB, SaveFile
from .records import Section, Summary, Table


def parts_section(save: SaveFile) -> Section:
    """The archive's parts: name, packing, what the document is, its size."""
    table = Table([N_("saves.archive_part"), N_("saves.packed_as"), N_("saves.holds"), N_("saves.bytes")])
    for p in save.parts:
        table.rows.append([p.name, p.packing, p.kind + (f" ({p.error})" if p.error else ""), f"{len(p.doc):,}"])
    return Section(N_("viewer.parts"), [(N_("saves.archive"), f"RDA {save.archive}" if save.archive else "-")], table)


def summarize(save: SaveFile) -> Summary:
    """The summary of ``save`` for its game; the part list for every game."""
    if save.game == "anno117" and save.part("data.a7s") is not None:
        out = summary117.summarize(save)
    elif save.game == "anno2070" and save.part("save.sww") is not None:
        out = summary2070.summarize(save)
    elif save.game in ("anno1800", "anno2205") and any(p.kind == FILEDB for p in save.parts):
        out = summaryfiledb.summarize(save)
    elif save.game in ("anno1404", "anno1701") and save.parts:
        out = summaryold.summarize(save)
    else:
        out = Summary(save.game)
        out.note(N_("saves.no_sample_games_saves_pinned"))
    out.sections.append(parts_section(save))
    return out


def sessions(save: SaveFile, say=lambda _t, **_p: None) -> Section | None:
    """The islands of every session, read from the sessions' own documents (117; slow: off the window's thread)."""
    return summary117.islands(save, say) if save.game == "anno117" and save.part("data.a7s") is not None else None
