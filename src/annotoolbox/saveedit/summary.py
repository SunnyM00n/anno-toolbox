"""A save's summary for its game: ``summarize(save)`` dispatches to ``summary117`` and ``summary2070``; a game whose
layout no sample pinned gets the part list only (``docs/save-editor.md``, "The containers").
"""

from __future__ import annotations

from ..infra.i18n import N_
from . import summary117, summary2070, summaryfiledb, summaryold
from .container import FILEDB, SSaveFile
from .records import CSummary, SSection, STable


def parts_section(save: SSaveFile) -> SSection:
    """The archive's parts: name, packing, what the document is, its size."""
    table = STable([N_("saves.archive_part"), N_("saves.packed_as"), N_("saves.holds"), N_("saves.bytes")])
    for part in save.parts:
        table.rows.append(
            [
                part.m_name,
                part.m_packing,
                part.m_kind + (f" ({part.m_error})" if part.m_error else ""),
                f"{len(part.m_doc):,}",
            ]
        )
    return SSection(N_("viewer.parts"), [(N_("saves.archive"), f"RDA {save.archive}" if save.archive else "-")], table)


def summarize(save: SSaveFile) -> CSummary:
    """The summary of ``save`` for its game; the part list for every game."""
    if save.game == "anno117" and save.part("data.a7s") is not None:
        out = summary117.summarize(save)
    elif save.game == "anno2070" and save.part("save.sww") is not None:
        out = summary2070.summarize(save)
    elif save.game in ("anno1800", "anno2205") and any(part.m_kind == FILEDB for part in save.parts):
        out = summaryfiledb.summarize(save)
    elif save.game in ("anno1404", "anno1701") and save.parts:
        out = summaryold.summarize(save)
    else:
        out = CSummary(save.game)
        out.note(N_("saves.no_sample_games_saves_pinned"))
    out.m_sections.append(parts_section(save))
    return out


def sessions(save: SSaveFile, say=lambda _text, **_values: None) -> SSection | None:
    """The islands of every session, read from the sessions' own documents (117; slow: off the window's thread)."""
    return summary117.islands(save, say) if save.game == "anno117" and save.part("data.a7s") is not None else None
