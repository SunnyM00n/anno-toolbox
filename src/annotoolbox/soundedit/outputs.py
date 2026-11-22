"""The files an edited bank makes: the bank in every language folder it lives in, and the loose streamed ``.wem`` files.

``render(doc, tree)`` returns ``SOutputs``: game path -> bytes for the project (a mod keeps the game's own paths).
A localised bank lives once in every language folder (and the game reads the copy of the audio language, the music fix
of ``docs/music.md``), so the edits are made again on each copy (``CBankDoc.replay``); the clips that sit loose beside
the banks go where the game looks for them (``docs/sound-editor.md`` "Files").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..exporting.banks import SBankInfo
from ..formats.bnkgraph import SBankLayout, layout
from ..infra.i18n import tr
from .bankdoc import CBankDoc

WINDOWS = "/windows/"


@dataclass
class SOutputs:
    files: dict[str, bytes] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # edits a copy of the bank could not take, in the copy's words


def split(path: str) -> tuple[str, str]:
    """(the banks' folder, the language folder with its slash or "") of a bank path."""
    folder = path.rsplit("/", 1)[0] + "/"
    if WINDOWS not in folder:
        return folder, ""
    root = folder[: folder.index(WINDOWS) + len(WINDOWS)]
    return root, folder[len(root) :]


def copies_of(paths: list[str], path: str) -> list[str]:
    """Every copy of a bank: the file beside the language folders and the one in each of them."""
    root, _language = split(path)
    name = path.rsplit("/", 1)[1]
    return [
        candidate_path
        for candidate_path in paths
        if candidate_path.startswith(root)
        and candidate_path.endswith("/" + name)
        and candidate_path.count("/") <= root.count("/") + 1
    ]


def loose_path(
    root: str,
    language: str,
    media: int,
    rec: SBankLayout,
    exists: Callable[[str], bool],
    bank_info: SBankInfo | None = None,
) -> str:
    """Where a loose clip goes: where the game has one of that name (the bank info's path first, as the player and
    the export look), else the folder the family's banks keep theirs in."""
    default = f"{root}{rec.loose_folder}{language}{media}.wem"
    candidates = [default, f"{root}media/{language}{media}.wem", f"{root}{language}{media}.wem"]
    if bank_info is not None and media in bank_info.media and not language:
        candidates.insert(0, root + bank_info.media[media][1])
    return next((candidate for candidate in candidates if exists(candidate)), default)


def render(doc: CBankDoc, tree, only: list[str] | None = None) -> SOutputs:
    """The files of the edits of ``doc`` (opened from ``tree``'s path ``doc.path``) on its copies (``only``: these paths
    of them, by default every copy the tree has)."""
    out = SOutputs()
    if not doc.changed:
        return out
    root, _language = split(doc.m_path)
    targets = only if only is not None else copies_of(tree.m_paths, doc.m_path) or [doc.m_path]
    for path in targets:
        copy = doc
        if path != doc.m_path:
            copy = CBankDoc(tree.read(path), path)
            out.problems += [f"{path}: {problem}" for problem in copy.replay(doc.m_log)]
        out.files[path] = copy.to_bytes()
        out.problems += [
            tr("sound.media_could_not_read_left", path=path, media=media_id) for media_id in copy.m_unreadable
        ]
        _, lang = split(path)
        for media, loose in copy.m_loose.items():
            where = lang if loose.language else ""
            out.files[loose_path(root, where, media, layout(copy.m_version), tree.exists, doc.m_bank_info)] = loose.data
    return out
