"""Delivery of edited 2070-format islands: 2070's ``maindata/patch9.rda``, and the edited files 1404 shares.

``edited_files`` applies every edit file of a game to its islands (``formats.isd.edit.Island2070``) and returns the files
to put in an archive; ``build_patch`` / ``install_patch`` / ``uninstall_patch`` are 2070's route, the archive that 2070
mounts last and without a checksum (engine-notes "Anno 2070 archives"). 1404's route is ``oldenginebuild``.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ....formats import isd2070
from ....formats.rda import RdaArchive, write_v20
from ....infra.buildcache import copy_atomic, write_atomic
from ....infra.i18n import tr
from ....islands import islandedits
from ....othergames import gamebackups
from ...editchange import count_line
from . import blank as isd_blank
from .edit import EDIT_PREFIX, Island2070

PATCH = "maindata/patch9.rda"
MARKER = "data/anno-toolbox/islands.txt"  # in our archive: which islands it carries
# <island>_2070_0.dds, the island seen from afar (A8R8G8B8 with mips, the island stretched over a
# power-of-two square, rows from the far edge): A the land by height, G a band along the coast, R and B a grey
# picture of the ground. A and G are redrawn after height edits by these tables, read off the
# vanilla islands (A matches at r 0.99, G at 0.78-0.90); R and B stay the vanilla island's.
FAR_SUFFIX = "_2070_0.dds"
FAR_LAND = ([-2.5, -2, -1.5, -1, -0.5, 0, 0.25, 0.5], [0, 7, 23, 77, 163, 212, 234, 254])  # height -> A
FAR_COAST = ([-4, -3, -2, -1, 0, 1, 2, 3], [0, 34, 119, 154, 154, 117, 39, 0])  # tiles inland -> G


def far_view(island: isd2070.Island, vanilla: bytes) -> bytes:
    """The island's far-view texture with land and coast redrawn from its (edited) heights."""
    from PIL import Image
    from scipy.ndimage import distance_transform_edt, map_coordinates

    rgba = np.flipud(np.asarray(Image.open(io.BytesIO(vanilla)).convert("RGBA"))).copy()
    hm = np.asarray(island.heightmap(), np.float32)
    size = rgba.shape[0]
    per = size / (hm.shape[0] - 1)  # pixels per tile
    at = (np.arange(size) + 0.5) / per
    zz, xx = np.meshgrid(at, at, indexing="ij")
    h = map_coordinates(hm, [zz, xx], order=1)
    land = h > 0
    inland = (distance_transform_edt(land) - distance_transform_edt(~land)) / per
    rgba[..., 3] = np.round(np.interp(h, *FAR_LAND))
    rgba[..., 1] = np.round(np.interp(inland, *FAR_COAST))
    (levels,) = struct.unpack_from("<I", vanilla, 28)
    img = Image.fromarray(np.flipud(rgba), "RGBA")
    out = bytearray(vanilla[:128])  # the same size, format and mip count
    for k in range(max(1, levels)):
        side = max(1, size >> k)
        r, g, b, a = (img if k == 0 else img.resize((side, side), Image.BOX)).split()
        out += Image.merge("RGBA", (b, g, r, a)).tobytes()  # stored B, G, R, A
    return bytes(out)


def edited_files(
    tree, edits_dir: Path, types: dict, log: Callable[[str], None], prefix: str = EDIT_PREFIX
) -> tuple[dict[str, bytes], list[str]]:
    """Every island with an edit file ``<prefix>*.json`` in ``edits_dir``, edited and written: the files (game path ->
    bytes; the far-view texture too where a height edit moved the coast) and the islands' paths."""
    files: dict[str, bytes] = {}
    written = []
    tree = isd_blank.with_new_islands(tree, edits_dir, prefix)  # a new island has no file in the game
    for path in sorted(edits_dir.glob(f"{prefix}*.json")):
        edits = islandedits.load(path)
        source = edits.get("source")
        if not source or not tree.exists(source):
            log(tr("{name}: no source island, skipped", name=path.name))
            continue
        isl = Island2070(tree.read_bytes(source))
        report = isl.apply(edits, types)
        files[source.lower()] = isl.write()
        written.append(source.lower())
        far = source.lower().removesuffix(".isd") + FAR_SUFFIX
        if report["heights"] and tree.exists(far):  # the coast may have moved
            files[far] = far_view(isl.island, tree.read_bytes(far))
        log(f"{source}: " + count_line(report))
        for miss in report["unmatched"]:
            log(tr("  not found: {name}", name=miss))
    return files, written


# 2070: patch9.rda
def is_ours(path: Path) -> bool:
    try:
        return MARKER in RdaArchive(path).entries
    except (OSError, ValueError):
        return False


def build_patch(
    tree2070, edits_dir: Path, original: Path | None, log: Callable[[str], None] = print
) -> tuple[bytes, list[str]]:
    """The patch9.rda that carries every edited 2070 island, and the islands' paths. ``original``:
    the game's own patch9.rda (official credits text), whose files the new one keeps."""
    from ....islands.objects2070 import read_types

    edited, written = edited_files(tree2070, edits_dir, read_types(tree2070), log)
    return patch_bytes(edited, written, original), written


def patch_bytes(edited: dict[str, bytes], written: list[str], original: Path | None) -> bytes:
    """The patch9.rda of ``edited`` (game path -> bytes) over the files of the game's own ``original`` (if any);
    ``written``: the islands among them, for the marker."""
    files: dict[str, bytes] = {}
    if original is not None and original.is_file():
        arc = RdaArchive(original)
        files = {e.path: arc.read_entry(e) for e in arc if e.path != MARKER}
    files |= edited
    files[MARKER] = ("\r\n".join(written) + "\r\n").encode("utf-8")
    return write_v20(files)


def own_patch(install: Path, backup: Path) -> Path | None:
    """The game's own patch9.rda, whose files a new patch keeps: the one in the game while it is not ours (a
    launcher repair makes it the game's again), else the kept copy; None when the game has none. Refused when ours
    lies there without a kept copy: a patch built from nothing would lose the game's files."""
    target = install / PATCH
    if not target.is_file():
        return None
    if not is_ours(target):
        return target
    if not backup.is_file():
        raise gamebackups.no_copy(target, backup)
    return backup


def install_patch(install: Path, patch: bytes, backup: Path, log: Callable[[str], None] = print) -> Path:
    """Put ``patch`` in place of the game's patch9.rda; the game's own is kept at ``backup`` first, again whenever
    the one in the game is not ours (an update or a launcher repair replaced it). Refused when ours lies there
    without a kept copy."""
    target = install / PATCH
    if target.is_file() and not is_ours(target):
        gamebackups.keep(target, backup)
        log(tr("kept the game's {file} as {backup}", file=PATCH, backup=backup))
    elif target.is_file() and not backup.is_file():
        raise gamebackups.no_copy(target, backup)
    write_atomic(target, patch)
    log(tr("wrote {target}", target=target))
    return target


def uninstall_patch(install: Path, backup: Path, log: Callable[[str], None] = print) -> bool:
    """Put the game's own patch9.rda back."""
    target = install / PATCH
    if not is_ours(target):
        log(tr("{target} is the game's own; nothing to undo", target=target))
        return False
    if not backup.is_file():
        raise FileNotFoundError(
            tr("no copy of the game's {file} at {backup}: verify the files in the launcher", file=PATCH, backup=backup)
        )
    copy_atomic(backup, target)
    log(tr("restored the game's {file}", file=PATCH))
    return True
