"""Anno 2205 editing of the island editor: a session map with hand edits, put back where the game reads it.

``apply`` puts an edit file's object and prop edits on an opened session (``editor.formats.a7m.session``, the editing is
``islandedits``' vocabulary in 2205's fixed-point object layout); ``build`` writes the changed members
(``gamedata.data``, ``rd3d.data``) into an archive of its own and points the game's archive index at them
(``othergames.index2205``); ``install`` puts the archive into a game folder through ``othergames.delivery2205``,
the one owner of the index, which the UI editor's archive shares.
Why the index and not a loose file or a mod: docs/formats/anno2205-map-format.md, "How the game finds a map".
"""

from __future__ import annotations

import copy
import math
import shutil
import struct
from collections.abc import Callable
from pathlib import Path

from ....formats import filedb
from ....formats.rda import RdaArchive, write_v22
from ....infra.i18n import tr
from ....islands import islandedits, objectedits
from ....othergames import delivery2205
from ....othergames.index2205 import FILE_DB, ArchiveIndex
from ... import groundedit, objectclones
from . import session as a7m_session
from . import sessionground as a7m_sessionground
from .session import Session2205, unique_key
from .sessionground import set_axes

EDIT_PREFIX = "2205_"  # edit files of 2205 sessions: island-edits/2205_<session>.json
OUT_NAME = "anno2205-islands"  # the build's folder in the workspace
ARCHIVE = delivery2205.SESSIONS  # our archive, next to the game's data*.rda


def edit_name(source: str) -> str:
    """The edit file's name for a 2205 session (its game path)."""
    return f"{EDIT_PREFIX}{Path(source).stem}.json"


# Objects


def _set_free_orientation(o: filedb.Tag, rotation: list[float], scale: float) -> None:
    free = o.find("FreeOrientation")
    if free is None:
        free = filedb.Tag("FreeOrientation", [])
        o.children.append(free)
    for name, data in (("Orientation", struct.pack("<4f", *rotation)), ("Scale", struct.pack("<f", scale))):
        attr = free.find(name)
        if attr is not None:
            attr.data = data
        else:
            free.children.append(filedb.Attrib(name, data))


def set_object(o: filedb.Tag, change: dict) -> None:
    """Apply an object edit (``position`` / ``rotation`` / ``scale``) to a 2205 object. A turn about the vertical
    axis alone is ``Direction``; a tilt or a scale needs ``FreeOrientation``, which then holds the whole rotation."""
    if "position" in change:
        pos = o.find("Position")
        if pos is None:
            pos = filedb.Tag("Position", [])
            o.children.append(pos)
        set_axes(pos, dict(zip("xyz", map(float, change["position"]), strict=True)))
    if "rotation" not in change and "scale" not in change:
        return
    rotation = [float(v) for v in change.get("rotation", a7m_session.object_rotation(o))]
    scale = float(change.get("scale", a7m_session.object_scale(o)))
    upright = abs(rotation[0]) < 1e-4 and abs(rotation[2]) < 1e-4
    if upright:
        degrees = math.degrees(-2.0 * math.atan2(rotation[1], rotation[3])) % 360
        set_axes(o, {"Direction": degrees})
    if o.find("FreeOrientation") is not None or not upright or abs(scale - 1.0) > 1e-6:
        _set_free_orientation(o, rotation, scale)


def _tags(isl: Session2205) -> dict[str, filedb.Tag]:
    gom = isl.gm.find("GameObjectManager")
    return {g: gom.path(f"{g}/objects") for g in a7m_session.OBJECT_GROUPS if gom.path(f"{g}/objects") is not None}


def _referenced(isl: Session2205) -> set[bytes]:
    """The object IDs that labels, object groups and snap pairs name: deleting such an object leaves them dangling."""
    out: set[bytes] = set()
    for path in ("GameObjectManager/GameObjectLabelMap", "GameObjectManager/ObjectGroupCollection", "CSlotManager"):
        node = isl.gm.path(path)
        if node is not None:
            out |= {a.data for a in node.iter() if isinstance(a, filedb.Attrib) and len(a.data) == 8}
    return out


def _next_serial(lists: dict[str, filedb.Tag], high: bytes) -> int:
    """One past the highest serial among the objects whose ID carries this high word (the asset's kind)."""
    serials = [
        struct.unpack("<I", o.find("ID").data[:4])[0]
        for objs in lists.values()
        for o in objs.children
        if isinstance(o, filedb.Tag) and o.find("ID") is not None and o.find("ID").data[4:] == high
    ]
    return max(serials, default=0) + 1


def _sample(lists: dict[str, filedb.Tag], guid: int, plain: bool) -> tuple[filedb.Tag, int, filedb.Tag] | None:
    """(objects list, index, object) of the first object of this asset; with ``plain`` (an entry placed from the asset
    list), of the first game object when the session holds none of it."""
    found = [
        (objs, k, o)
        for objs in lists.values()
        for k, o in enumerate(objs.children)
        if isinstance(o, filedb.Tag) and o.find("guid") is not None and o.find("ID") is not None
    ]
    same = next((f for f in found if f[2].find("guid").u32() == guid), None)
    if same is None and plain:
        game = lists.get("GameObject")
        same = next((f for f in found if f[0] is game), None)
    return same


def _clone(lists: dict[str, filedb.Tag], guid: int, entry: dict, report: dict) -> bool:
    """Place a copy of the first object of this asset (``_sample``) where the entry says, next to the original, with an
    ID of its own and the entry's asset. False when the session holds nothing to copy. A copy of another asset's
    object keeps that object's kind (the ID's high word): the report lists it (an unverified guess, see
    anno2205-map-format "Object IDs")."""
    found = _sample(lists, guid, bool(entry.get("plain")))
    if found is None:
        return False
    objs, k, o = found
    if o.find("guid").u32() != guid:
        report.setdefault("placed with another asset's kind", []).append(f"guid:{guid}")
    new = copy.deepcopy(o)
    high = o.find("ID").data[4:]
    new.find("ID").data = struct.pack("<I", _next_serial(lists, high)) + high
    new.find("guid").data = struct.pack("<I", guid)
    if "variation" in entry:
        objectclones.set_variation(new, int(entry["variation"]))
    set_object(new, {"position": entry["position"], "rotation": entry["rotation"]} | _scale(entry))
    objs.children.insert(k + 1, new)
    return True


def _scale(entry: dict) -> dict:
    return {"scale": entry["scale"]} if "scale" in entry else {}


def apply_objects(isl: Session2205, edits: dict, report: dict) -> None:
    lists, seen, refs = _tags(isl), {}, _referenced(isl)

    def keyed():
        for objs in lists.values():
            for o in list(objs.children):
                name = islandedits.object_name(o) if isinstance(o, filedb.Tag) and o.find("guid") is not None else None
                if name:
                    yield unique_key(islandedits.key(name, a7m_session.object_position(o)), seen), (objs, o)

    def remove(dead: list[tuple[filedb.Tag, filedb.Tag]]) -> None:
        for objs, o in dead:
            objs.children.remove(o)

    def referenced(handle: tuple[filedb.Tag, filedb.Tag]) -> bool:
        ident = handle[1].find("ID")
        return ident is not None and ident.data in refs

    ops = objectedits.ObjectOps(lambda handle, change: set_object(handle[1], change), remove, referenced)
    objectedits.apply_changes(keyed(), edits.get("objects", {}), ops, report)
    objectedits.add_each(
        edits.get("added_objects", []),
        lambda entry: bool(entry.get("guid")) and _clone(lists, int(entry["guid"]), entry, report),
        lambda entry: f"guid:{entry.get('guid') or entry.get('cfg')}",
        report,
    )


# Props


def _new_prop(entry: dict) -> filedb.Tag:
    scale = entry.get("scale", 1.0)
    scale = scale if isinstance(scale, list) else [scale] * 3
    return filedb.Tag(
        "#1",
        [
            filedb.Attrib("FileName", entry["cfg"].encode("utf-16-le")),
            filedb.Attrib("Position", struct.pack("<3f", *map(float, entry["position"]))),
            filedb.Attrib("Rotation", struct.pack("<4f", *map(float, entry.get("rotation", (0, 0, 0, 1))))),
            filedb.Attrib("Scale", struct.pack("<3f", *map(float, scale))),
            filedb.Attrib("Color", struct.pack("<4f", 1.0, 1.0, 1.0, 0.0)),
        ],
    )


def _set_prop(p: filedb.Tag, change: dict) -> None:
    for name, fmt in (("position", "<3f"), ("rotation", "<4f")):
        if name in change:
            p.find(name.capitalize()).data = struct.pack(fmt, *map(float, change[name]))
    if "scale" in change:
        s = change["scale"]
        p.find("Scale").data = struct.pack("<3f", *(map(float, s) if isinstance(s, list) else (float(s),) * 3))


def apply_props(isl: Session2205, edits: dict, report: dict) -> None:
    props = isl.rd.path("PropGrid/Props")
    wanted, seen, dead = dict(edits.get("props", {})), {}, set()
    for p in isl.prop_tags():
        key = unique_key(islandedits.key(*_cfg_position(p)), seen)
        change = wanted.pop(key, None)
        if change is None:
            continue
        if change.get("deleted"):
            dead.add(id(p))
        else:
            _set_prop(p, change)
            report["props changed"] += 1
    if props is not None:
        props.children = [p for p in props.children if id(p) not in dead]
        for entry in edits.get("added_props", []):
            props.children.append(_new_prop(entry))
            report["props added"] += 1
    report["props deleted"] = len(dead)
    report["unmatched"] += sorted(wanted)


def _cfg_position(p: filedb.Tag) -> tuple[str, list[float]]:
    return p.find("FileName").data.decode("utf-16-le").replace("\\", "/"), list(
        struct.unpack("<3f", p.find("Position").data)
    )


# Apply and write


def apply(isl: Session2205, edits: dict) -> dict:
    """Put ``edits`` on an opened session and say what matched; ``members`` names the files that changed."""
    report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "props changed": 0}
    report |= {"props deleted": 0, "props added": 0, "unmatched": [], "still referenced": []}
    ground = a7m_sessionground.apply(isl, edits)
    apply_objects(isl, edits, report)
    if edits.get("props") or edits.get("added_props"):
        apply_props(isl, edits, report)
    report["members"] = [
        n
        for n, hit in (
            ("gamedata.data", report["objects changed"] + report["objects deleted"] + report["objects added"]),
            ("rd3d.data", report["props changed"] + report["props deleted"] + report["props added"]),
        )
        if hit or n in ground["members"]
    ]
    report |= {k: v for k, v in ground.items() if k != "members"}
    report["unmatched"] += groundedit.unmatched(ground)
    return report


def member_bytes(isl: Session2205, name: str) -> bytes:
    """A member as the engine reads it, in the layout it was read in (an unedited one is the original, byte for byte)."""
    tree = isl.gd if name == "gamedata.data" else isl.rd
    return filedb.write(tree, *isl.ids[name], version=isl.versions[name])


def edited_members(files, edits_dir: Path, log: Callable[[str], None] = print) -> tuple[dict[str, bytes], list[str]]:
    """Every 2205 session with an edit file in ``edits_dir``, edited: the changed members (index path -> bytes) and
    the sessions' paths."""
    members, written = {}, []
    for path in sorted(edits_dir.glob(f"{EDIT_PREFIX}*.json")):
        edits = islandedits.load(path)
        source = edits.get("source")
        if not source:
            log(tr("{name}: no source session, skipped", name=path.name))
            continue
        isl = Session2205(files, source)
        report = apply(isl, edits)
        log(tr("{source}: {report}", source=source, report={k: v for k, v in report.items() if v}))
        for name in report["members"]:
            members[isl.member_paths[name]] = member_bytes(isl, name)
        members.update(isl.new_files)
        written.append(source)
    return members, written


def pack(members: dict[str, bytes], index_raw: bytes) -> dict[str, bytes]:
    """Our archive holding ``members`` and the game's archive index (``index_raw``) pointing at them, by their path
    in the install (``ARCHIVE``, ``FILE_DB``). A path the index lacks is not a file of this install."""
    index = ArchiveIndex(index_raw)
    archive = write_v22(members)
    entries = RdaArchive.from_bytes(archive).entries
    for path in members:
        if index.entry(path) is None:
            raise ValueError(tr("the game's archive index has no entry {path}: not a file of this install", path=path))
        index.point(path, ARCHIVE, entries[path])
    return {ARCHIVE: archive, FILE_DB: index.write()}


def build(files, index_raw: bytes, edits_dir: Path, out: Path, log: Callable[[str], None] = print) -> list[str]:
    """Write every 2205 session with edits into ``out``: our archive (the changed members) and the game's archive
    index pointing at them (``out/maindata``). ``index_raw``: the game's own ``file.db``. Returns the sessions."""
    if not out.is_absolute() or out.name != OUT_NAME:  # a folder that is removed: only ever the build's own
        raise ValueError(
            tr("refusing to rebuild {out}: not an absolute path ending in {name}", out=out, name=repr(OUT_NAME))
        )
    members, written = edited_members(files, edits_dir, log)
    if out.exists():
        shutil.rmtree(out)
    (out / "maindata").mkdir(parents=True)
    if not members:
        log(tr("no edit changes a session: nothing written"))
        return []
    for rel, data in pack(members, index_raw).items():
        (out / rel).write_bytes(data)
    return written


# The game folder


def is_ours(index_raw: bytes) -> bool:
    """Whether an archive index lists our archive: the one ``install`` wrote."""
    return ARCHIVE in ArchiveIndex(index_raw).archive_names()


def original_index(game: Path, old_backup: Path | None = None) -> bytes:
    """The game's own ``file.db`` (``delivery2205.game_index``); ``old_backup``: where an earlier version kept it."""
    return delivery2205.game_index(game, (old_backup,) if old_backup else ())


def install(built: Path, game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> None:
    """Put the built archive into ``game`` (its install folder) through the shared delivery, which keeps the game's
    own index first and points it at the UI's archive too when that is installed."""
    if not (built / ARCHIVE).is_file():
        raise RuntimeError(tr("nothing built yet: build the edited sessions first"))
    delivery2205.install(game, ARCHIVE, built / ARCHIVE, log, (old_backup,) if old_backup else ())
    log(tr("start a new game of the session to see the edits"))


def uninstall(game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> bool:
    """Take our archive out of ``game``; the index keeps pointing at the UI's archive when that is installed."""
    return delivery2205.uninstall(game, ARCHIVE, log, (old_backup,) if old_backup else ())
