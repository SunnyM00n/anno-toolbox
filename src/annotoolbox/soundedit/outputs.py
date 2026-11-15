"""The files an edited bank makes: the bank in every language folder it lives in, and the loose streamed ``.wem`` files.

``render(doc, tree)`` returns ``Outputs``: game path -> bytes for the project (a mod keeps the game's own paths).
A localised bank lives once in every language folder (and the game reads the copy of the audio language, the music fix
of ``docs/music.md``), so the edits are made again on each copy (``BankDoc.replay``); the clips that sit loose beside
the banks go where the game looks for them (``docs/sound-editor.md`` "Files").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..exporting.banks import BankInfo
from ..formats.bnkgraph import BankLayout, layout
from ..infra.i18n import tr
from .bankdoc import BankDoc

WINDOWS = "/windows/"


@dataclass
class Outputs:
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
    return [p for p in paths if p.startswith(root) and p.endswith("/" + name) and p.count("/") <= root.count("/") + 1]


def loose_path(
    root: str, language: str, media: int, rec: BankLayout, exists: Callable[[str], bool], info: BankInfo | None = None
) -> str:
    """Where a loose clip goes: where the game has one of that name (the bank info's path first, as the player and
    the export look), else the folder the family's banks keep theirs in."""
    default = f"{root}{rec.loose_folder}{language}{media}.wem"
    candidates = [default, f"{root}media/{language}{media}.wem", f"{root}{language}{media}.wem"]
    if info is not None and media in info.media and not language:
        candidates.insert(0, root + info.media[media][1])
    return next((p for p in candidates if exists(p)), default)


def render(doc: BankDoc, tree, only: list[str] | None = None) -> Outputs:
    """The files of the edits of ``doc`` (opened from ``tree``'s path ``doc.path``) on its copies (``only``: these paths
    of them, by default every copy the tree has)."""
    out = Outputs()
    if not doc.changed:
        return out
    root, _language = split(doc.path)
    targets = only if only is not None else copies_of(tree.paths, doc.path) or [doc.path]
    for path in targets:
        copy = doc
        if path != doc.path:
            copy = BankDoc(tree.read(path), path)
            out.problems += [f"{path}: {p}" for p in copy.replay(doc.log)]
        out.files[path] = copy.to_bytes()
        out.problems += [
            tr("{path}: media {media} could not be read and was left out", path=path, media=m) for m in copy.unreadable
        ]
        _, lang = split(path)
        for media, loose in copy.loose.items():
            where = lang if loose.language else ""
            out.files[loose_path(root, where, media, layout(copy.version), tree.exists, doc.info)] = loose.data
    return out
