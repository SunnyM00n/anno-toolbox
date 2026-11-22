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
from ....formats.rda import CRdaArchive, write_v22
from ....infra.i18n import tr
from ....islands import islandedits, objectedits
from ....othergames import delivery2205
from ....othergames.index2205 import FILE_DB, CArchiveIndex
from ... import groundedit, objectclones
from . import session as a7m_session
from . import sessionground as a7m_sessionground
from .session import CSession2205, unique_key
from .sessionground import set_axes

EDIT_PREFIX = "2205_"  # edit files of 2205 sessions: island-edits/2205_<session>.json
OUT_NAME = "anno2205-islands"  # the build's folder in the workspace
ARCHIVE = delivery2205.SESSIONS  # our archive, next to the game's data*.rda


def edit_name(source: str) -> str:
    """The edit file's name for a 2205 session (its game path)."""
    return f"{EDIT_PREFIX}{Path(source).stem}.json"


# Objects


def _set_free_orientation(game_object: filedb.STag, rotation: list[float], scale: float) -> None:
    free = game_object.find("FreeOrientation")
    if free is None:
        free = filedb.STag("FreeOrientation", [])
        game_object.children.append(free)
    for name, data in (("Orientation", struct.pack("<4f", *rotation)), ("Scale", struct.pack("<f", scale))):
        attribute = free.find(name)
        if attribute is not None:
            attribute.data = data
        else:
            free.children.append(filedb.SAttrib(name, data))


def set_object(game_object: filedb.STag, change: dict) -> None:
    """Apply an object edit (``position`` / ``rotation`` / ``scale``) to a 2205 object. A turn about the vertical
    axis alone is ``Direction``; a tilt or a scale needs ``FreeOrientation``, which then holds the whole rotation."""
    if "position" in change:
        position_tag = game_object.find("Position")
        if position_tag is None:
            position_tag = filedb.STag("Position", [])
            game_object.children.append(position_tag)
        set_axes(position_tag, dict(zip("xyz", map(float, change["position"]), strict=True)))
    if "rotation" not in change and "scale" not in change:
        return
    rotation = [float(value) for value in change.get("rotation", a7m_session.object_rotation(game_object))]
    scale = float(change.get("scale", a7m_session.object_scale(game_object)))
    upright = abs(rotation[0]) < 1e-4 and abs(rotation[2]) < 1e-4
    if upright:
        degrees = math.degrees(-2.0 * math.atan2(rotation[1], rotation[3])) % 360
        set_axes(game_object, {"Direction": degrees})
    if game_object.find("FreeOrientation") is not None or not upright or abs(scale - 1.0) > 1e-6:
        _set_free_orientation(game_object, rotation, scale)


def _tags(session: CSession2205) -> dict[str, filedb.STag]:
    """The object lists of the session by group (none in a scene without objects)."""
    game_object_manager = session.game_session_manager.find("GameObjectManager")
    lists = {
        group: game_object_manager.path(f"{group}/objects") if game_object_manager is not None else None
        for group in a7m_session.OBJECT_GROUPS
    }
    return {group: objs for group, objs in lists.items() if objs is not None}


def _referenced(session: CSession2205) -> set[bytes]:
    """The object IDs that labels, object groups and snap pairs name: deleting such an object leaves them dangling."""
    out: set[bytes] = set()
    for path in ("GameObjectManager/GameObjectLabelMap", "GameObjectManager/ObjectGroupCollection", "CSlotManager"):
        node = session.game_session_manager.path(path)
        if node is not None:
            out |= {
                attribute.data
                for attribute in node.iter()
                if isinstance(attribute, filedb.SAttrib) and len(attribute.data) == 8
            }
    return out


def _next_serial(lists: dict[str, filedb.STag], high: bytes) -> int:
    """One past the highest serial among the objects whose ID carries this high word (the asset's kind)."""
    serials = [
        struct.unpack("<I", game_object.find("ID").data[:4])[0]
        for objs in lists.values()
        for game_object in objs.children
        if isinstance(game_object, filedb.STag)
        and game_object.find("ID") is not None
        and game_object.find("ID").data[4:] == high
    ]
    return max(serials, default=0) + 1


def _sample(lists: dict[str, filedb.STag], guid: int, plain: bool) -> tuple[filedb.STag, int, filedb.STag] | None:
    """(objects list, index, object) of the first object of this asset; with ``plain`` (an entry placed from the asset
    list), of the first game object when the session holds none of it."""
    found = [
        (objs, k, game_object)
        for objs in lists.values()
        for k, game_object in enumerate(objs.children)
        if isinstance(game_object, filedb.STag)
        and game_object.find("guid") is not None
        and game_object.find("ID") is not None
    ]
    same = next((candidate for candidate in found if candidate[2].find("guid").u32() == guid), None)
    if same is None and plain:
        game = lists.get("GameObject")
        same = next((candidate for candidate in found if candidate[0] is game), None)
    return same


def _clone(lists: dict[str, filedb.STag], guid: int, entry: dict, report: dict) -> bool:
    """Place a copy of the first object of this asset (``_sample``) where the entry says, next to the original, with an
    ID of its own and the entry's asset. False when the session holds nothing to copy. A copy of another asset's
    object keeps that object's kind (the ID's high word): the report lists it (an unverified guess, see
    anno2205-map-format "Object IDs")."""
    found = _sample(lists, guid, bool(entry.get("plain")))
    if found is None:
        return False
    objs, k, game_object = found
    if game_object.find("guid").u32() != guid:
        report.setdefault("placed with another asset's kind", []).append(f"guid:{guid}")
    new = copy.deepcopy(game_object)
    high = game_object.find("ID").data[4:]
    new.find("ID").data = struct.pack("<I", _next_serial(lists, high)) + high
    new.find("guid").data = struct.pack("<I", guid)
    if "variation" in entry:
        objectclones.set_variation(new, int(entry["variation"]))
    set_object(new, {"position": entry["position"], "rotation": entry["rotation"]} | _scale(entry))
    objs.children.insert(k + 1, new)
    return True


def _scale(entry: dict) -> dict:
    return {"scale": entry["scale"]} if "scale" in entry else {}


def apply_objects(session: CSession2205, edits: dict, report: dict) -> None:
    lists, seen, refs = _tags(session), {}, _referenced(session)

    def keyed():
        for objs in lists.values():
            for game_object in list(objs.children):
                name = (
                    islandedits.object_name(game_object)
                    if isinstance(game_object, filedb.STag) and game_object.find("guid") is not None
                    else None
                )
                if name:
                    yield (
                        unique_key(islandedits.key(name, a7m_session.object_position(game_object)), seen),
                        (objs, game_object),
                    )

    def remove(dead: list[tuple[filedb.STag, filedb.STag]]) -> None:
        for objs, game_object in dead:
            objs.children.remove(game_object)

    def referenced(handle: tuple[filedb.STag, filedb.STag]) -> bool:
        ident = handle[1].find("ID")
        return ident is not None and ident.data in refs

    ops = objectedits.SObjectOps(lambda handle, change: set_object(handle[1], change), remove, referenced)
    objectedits.apply_changes(keyed(), edits.get("objects", {}), ops, report)
    objectedits.add_each(
        edits.get("added_objects", []),
        lambda entry: bool(entry.get("guid")) and _clone(lists, int(entry["guid"]), entry, report),
        lambda entry: f"guid:{entry.get('guid') or entry.get('cfg')}",
        report,
    )


# Props


def _new_prop(entry: dict) -> filedb.STag:
    scale = entry.get("scale", 1.0)
    scale = scale if isinstance(scale, list) else [scale] * 3
    return filedb.STag(
        "#1",
        [
            filedb.SAttrib("FileName", entry["cfg"].encode("utf-16-le")),
            filedb.SAttrib("Position", struct.pack("<3f", *map(float, entry["position"]))),
            filedb.SAttrib("Rotation", struct.pack("<4f", *map(float, entry.get("rotation", (0, 0, 0, 1))))),
            filedb.SAttrib("Scale", struct.pack("<3f", *map(float, scale))),
            filedb.SAttrib("Color", struct.pack("<4f", 1.0, 1.0, 1.0, 0.0)),
        ],
    )


def _set_prop(prop: filedb.STag, change: dict) -> None:
    """A prop edit on its item; a ``Rotation`` or ``Scale`` the item does not store (the game's default) is added."""
    packed = {
        name.capitalize(): struct.pack(struct_format, *map(float, change[name]))
        for name, struct_format in (("position", "<3f"), ("rotation", "<4f"))
        if name in change
    }
    if "scale" in change:
        scale_value = change["scale"]
        packed["Scale"] = struct.pack(
            "<3f", *(map(float, scale_value) if isinstance(scale_value, list) else (float(scale_value),) * 3)
        )
    for name, data in packed.items():
        attribute = prop.find(name)
        if attribute is not None:
            attribute.data = data
        else:
            prop.children.append(filedb.SAttrib(name, data))


def apply_props(session: CSession2205, edits: dict, report: dict) -> None:
    props = session.rd3d.path("PropGrid/Props")
    wanted, seen, dead = dict(edits.get("props", {})), {}, set()
    for prop in session.prop_tags():
        key = unique_key(islandedits.key(*_cfg_position(prop)), seen)
        change = wanted.pop(key, None)
        if change is None:
            continue
        if change.get("deleted"):
            dead.add(id(prop))
        else:
            _set_prop(prop, change)
            report["props changed"] += 1
    if props is not None:
        props.children = [prop for prop in props.children if id(prop) not in dead]
        for entry in edits.get("added_props", []):
            props.children.append(_new_prop(entry))
            report["props added"] += 1
    report["props deleted"] = len(dead)
    report["unmatched"] += sorted(wanted)


def _cfg_position(prop: filedb.STag) -> tuple[str, list[float]]:
    return prop.find("FileName").data.decode("utf-16-le").replace("\\", "/"), list(
        struct.unpack("<3f", prop.find("Position").data)
    )


# Apply and write


def apply(session: CSession2205, edits: dict) -> dict:
    """Put ``edits`` on an opened session and say what matched; ``members`` names the files that changed."""
    report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "props changed": 0}
    report |= {"props deleted": 0, "props added": 0, "unmatched": [], "still referenced": []}
    ground = a7m_sessionground.apply(session, edits)
    apply_objects(session, edits, report)
    if edits.get("props") or edits.get("added_props"):
        apply_props(session, edits, report)
    report["members"] = [
        member_name
        for member_name, changed_count in (
            ("gamedata.data", report["objects changed"] + report["objects deleted"] + report["objects added"]),
            ("rd3d.data", report["props changed"] + report["props deleted"] + report["props added"]),
        )
        if changed_count or member_name in ground["members"]
    ]
    report |= {k: value for k, value in ground.items() if k != "members"}
    report["unmatched"] += groundedit.unmatched(ground)
    return report


def member_bytes(session: CSession2205, name: str) -> bytes:
    """A member as the engine reads it, in the layout it was read in (an unedited one is the original, byte for byte)."""
    tree = session.gamedata if name == "gamedata.data" else session.rd3d
    return filedb.write(tree, *session.file_name_ids[name], version=session.file_versions[name])


def edited_members(files, edits_dir: Path, log: Callable[[str], None] = print) -> tuple[dict[str, bytes], list[str]]:
    """Every 2205 session with an edit file in ``edits_dir``, edited: the changed members (index path -> bytes) and
    the sessions' paths."""
    members, written = {}, []
    for path in sorted(edits_dir.glob(f"{EDIT_PREFIX}*.json")):
        edits = islandedits.load(path)
        source = edits.get("source")
        if not source:
            log(tr("islands.no_source_session_skipped", name=path.name))
            continue
        session = CSession2205(files, source)
        report = apply(session, edits)
        log(tr("islands.source_report", source=source, report={k: value for k, value in report.items() if value}))
        for name in report["members"]:
            members[session.member_paths[name]] = member_bytes(session, name)
        members.update(session.new_files)
        written.append(source)
    return members, written


def pack(members: dict[str, bytes], index_raw: bytes) -> dict[str, bytes]:
    """Our archive holding ``members`` and the game's archive index (``index_raw``) pointing at them, by their path
    in the install (``ARCHIVE``, ``FILE_DB``). A path the index lacks is not a file of this install."""
    index = CArchiveIndex(index_raw)
    archive = write_v22(members)
    entries = CRdaArchive.from_bytes(archive).entries
    for path in members:
        if index.entry(path) is None:
            raise ValueError(tr("islands.games_archive_index_no_entry", path=path))
        index.point(path, ARCHIVE, entries[path])
    return {ARCHIVE: archive, FILE_DB: index.write()}


def build(files, index_raw: bytes, edits_dir: Path, out: Path, log: Callable[[str], None] = print) -> list[str]:
    """Write every 2205 session with edits into ``out``: our archive (the changed members) and the game's archive
    index pointing at them (``out/maindata``). ``index_raw``: the game's own ``file.db``. Returns the sessions."""
    if not out.is_absolute() or out.name != OUT_NAME:  # a folder that is removed: only ever the build's own
        raise ValueError(tr("islands.refusing_rebuild_not_absolute_path", out=out, name=repr(OUT_NAME)))
    members, written = edited_members(files, edits_dir, log)
    if out.exists():
        shutil.rmtree(out)
    (out / "maindata").mkdir(parents=True)
    if not members:
        log(tr("islands.no_edit_changes_session_nothing"))
        return []
    for relative_path, data in pack(members, index_raw).items():
        (out / relative_path).write_bytes(data)
    return written


# The game folder


def is_ours(index_raw: bytes) -> bool:
    """Whether an archive index lists our archive: the one ``install`` wrote."""
    return ARCHIVE in CArchiveIndex(index_raw).archive_names()


def original_index(game: Path, old_backup: Path | None = None) -> bytes:
    """The game's own ``file.db`` (``delivery2205.game_index``); ``old_backup``: where an earlier version kept it."""
    return delivery2205.game_index(game, (old_backup,) if old_backup else ())


def install(built: Path, game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> None:
    """Put the built archive into ``game`` (its install folder) through the shared delivery, which keeps the game's
    own index first and points it at the UI's archive too when that is installed."""
    if not (built / ARCHIVE).is_file():
        raise RuntimeError(tr("islands.nothing_built_yet_build_edited"))
    delivery2205.install(game, ARCHIVE, built / ARCHIVE, log, (old_backup,) if old_backup else ())
    log(tr("islands.start_new_game_session_see"))


def uninstall(game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> bool:
    """Take our archive out of ``game``; the index keeps pointing at the UI's archive when that is installed."""
    return delivery2205.uninstall(game, ARCHIVE, log, (old_backup,) if old_backup else ())
