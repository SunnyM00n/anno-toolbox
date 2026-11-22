"""The RDM toolchain as calls a window makes: import a glTF with its report, list a file, compare two.

``import_model`` is ``rdm import`` for one file without the command line: the ``.rdm`` files it would write as bytes
and the report (``notes``: what the import dropped, rebuilt or filled; a model it cannot take without changing it
raises ``ValueError`` with the reason). ``write_files`` puts them on disk without overwriting, ``describe`` lists
an ``.rdm`` (from bytes) or a glTF (from its path); ``listing.diff`` compares two listings. ``commands`` uses the
same import. Rules and mapping: docs/rdm-gltf.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..formats.gltf import read_gltf
from ..formats.rdmread import read_rdm
from ..formats.rdmwrite import write_file
from . import gltfrdm, listing
from .gltfdoc import SOptions

GLTF_SUFFIXES = (".glb", ".gltf")


@dataclass
class SImportReport:
    """The files an import makes (name -> bytes, the mesh files first) and what it says about them."""

    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    meshes: list[str] = field(default_factory=list)  # the names of the files that hold meshes


def read_model(path: Path, name: str | None = None, options: SOptions | None = None) -> gltfrdm.SImported:
    """The import of a glTF file: its RDM files in memory, nothing written."""
    return gltfrdm.import_gltf(read_gltf(path), name or path.with_suffix(".rdm").name, options)


def import_model(path: Path, target_name: str | None = None, options: SOptions | None = None) -> SImportReport:
    """Import a ``.glb`` or ``.gltf``: ``target_name`` is the file name of the mesh file when the document holds one
    (default: the document's name with ``.rdm``); animation files and further meshes keep their own names."""
    target = target_name or path.with_suffix(".rdm").name
    got = read_model(path, target, options)
    meshes = [name for name, rdm in got.files if rdm.meshes]
    files = {}
    for name, rdm in got.files:
        files[target if len(meshes) == 1 and name == meshes[0] else name] = write_file(rdm)
    return SImportReport(files, list(got.notes), [target if len(meshes) == 1 else mesh for mesh in meshes])


def write_files(report: SImportReport, folder: Path, force: bool = False) -> tuple[list[Path], list[str]]:
    """Write the report's files into ``folder``; the paths written and the names left alone (they exist, no ``force``)."""
    folder.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for name, data in report.files.items():
        path = folder / name
        if path.exists() and not force:
            skipped.append(name)
            continue
        path.write_bytes(data)
        written.append(path)
    return written, skipped


def describe(name: str, data: bytes | None = None, values: bool = False, path: Path | None = None) -> list[str]:
    """The element listing of an ``.rdm`` (its ``data``) or a glTF (its ``path``: a ``.gltf`` may need its ``.bin``):
    for a glTF the document's own items, then what the import of it writes."""
    if name.lower().endswith(GLTF_SUFFIXES):
        if path is None:
            raise ValueError(f"{name}: a glTF is listed from its path")
        lines = listing.describe_gltf(read_gltf(path))
        for out_name, rdm in read_model(path).files:
            lines += [f"-- the import writes {out_name}:", *listing.describe(rdm, values)]
        return lines
    if data is None:
        raise ValueError(f"{name}: no data")
    return listing.describe(read_rdm(data), values)


def describe_as_rdm(path: Path, values: bool = False, like_name: str = "") -> list[str]:
    """The listing of the RDM file of a path, for a comparison: an ``.rdm`` itself, a glTF as the file its import
    writes (the one named ``like_name`` when it makes several, else the first that holds meshes)."""
    if path.suffix.lower() not in GLTF_SUFFIXES:
        return listing.describe(read_rdm(path.read_bytes()), values)
    files = read_model(path).files
    chosen = (
        [entry for entry in files if entry[0] == like_name]
        or [entry for entry in files if entry[1].meshes][:1]
        or files[:1]
    )
    if not chosen:
        raise ValueError(f"{path.name}: the import writes no file")
    return listing.describe(chosen[0][1], values)
