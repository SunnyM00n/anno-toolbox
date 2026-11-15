"""glTF 2.0 files, written and read without a library: binary ``.glb``, ``.gltf`` with a ``.bin`` beside it, or one
``.gltf`` with its buffer embedded.

``Builder`` collects the buffer, its views and accessors while a document is filled; ``Builder.files`` writes it in
one of the three forms. ``read_gltf`` reads any of them back (``Gltf.values`` gives an accessor's elements, sparse
accessors and strides included). The specification: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

BYTE, UBYTE, SHORT, USHORT, UINT, FLOAT = 5120, 5121, 5122, 5123, 5125, 5126
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_PACK = {BYTE: "b", UBYTE: "B", SHORT: "h", USHORT: "H", UINT: "I", FLOAT: "f"}
_SIZE = {BYTE: 1, UBYTE: 1, SHORT: 2, USHORT: 2, UINT: 4, FLOAT: 4}
_NORM = {BYTE: 127.0, UBYTE: 255.0, SHORT: 32767.0, USHORT: 65535.0}
FORMS = ("glb", "gltf", "gltfmin", "embedded")
GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = b"glTF", b"JSON", b"BIN\x00"
DATA_URI = "data:application/octet-stream;base64,"


class GltfError(ValueError):
    """Not a glTF 2.0 file, or one this reader cannot take (the message says what)."""


@dataclass
class Builder:
    """The parts of a document that point into the one buffer; ``doc`` holds everything else."""

    doc: dict = field(default_factory=lambda: {"asset": {"version": "2.0"}})
    blob: bytearray = field(default_factory=bytearray)

    def view(self, payload: bytes, target: int | None = None) -> int:
        """A buffer view over ``payload`` (aligned to 4 bytes); its index."""
        while len(self.blob) % 4:
            self.blob.append(0)
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self.blob.extend(payload)
        self.doc.setdefault("bufferViews", []).append(view)
        return len(self.doc["bufferViews"]) - 1

    def accessor(
        self, values: list, kind: str, component: int, *, normalized: bool = False, target: int | None = None
    ) -> int:
        """An accessor of ``values`` (one tuple per element, or numbers for a SCALAR); its index. A float accessor
        gets its ``min`` / ``max`` (glTF asks for them on positions)."""
        n = COMPONENTS[kind]
        flat = [c for v in values for c in v] if n > 1 else list(values)
        payload = struct.pack(f"<{len(flat)}{_PACK[component]}", *flat)
        acc = {"bufferView": self.view(payload, target), "componentType": component, "count": len(values)}
        acc["type"] = kind
        if normalized:
            acc["normalized"] = True
        if component == FLOAT and values and kind in ("SCALAR", "VEC2", "VEC3", "VEC4"):
            rows = [v if n > 1 else (v,) for v in values]
            acc["min"] = [min(r[k] for r in rows) for k in range(n)]
            acc["max"] = [max(r[k] for r in rows) for k in range(n)]
        self.doc.setdefault("accessors", []).append(acc)
        return len(self.doc["accessors"]) - 1

    def add(self, key: str, item: dict) -> int:
        self.doc.setdefault(key, []).append(item)
        return len(self.doc[key]) - 1

    def files(self, path: Path, form: str = "glb") -> dict[Path, bytes]:
        """The file(s) of the document at ``path``: one ``.glb``, a ``.gltf`` and its ``.bin`` (``gltfmin``: the JSON
        on one line), or an embedded ``.gltf`` (``FORMS``)."""
        doc = dict(self.doc)
        blob = bytes(self.blob) + bytes(-len(self.blob) % 4)
        if form == "glb":
            doc["buffers"] = [{"byteLength": len(blob)}] if blob else []
            return {path: glb_bytes(doc, blob)}
        if form in ("gltf", "gltfmin"):
            bin_path = path.with_suffix(".bin")
            doc["buffers"] = [{"byteLength": len(blob), "uri": bin_path.name}] if blob else []
            return {path: _json(doc, form == "gltfmin"), **({bin_path: blob} if blob else {})}
        if form == "embedded":
            uri = DATA_URI + base64.b64encode(blob).decode("ascii")
            doc["buffers"] = [{"byteLength": len(blob), "uri": uri}] if blob else []
            return {path: _json(doc)}
        raise GltfError(f"unknown glTF form {form!r} (one of {', '.join(FORMS)})")


def _json(doc: dict, compact: bool = False) -> bytes:
    text = json.dumps(doc, separators=(",", ":")) if compact else json.dumps(doc, indent=1)
    return (text + "\n").encode("utf-8")


def glb_bytes(doc: dict, blob: bytes) -> bytes:
    """A binary glTF: the JSON chunk (padded with spaces) and the binary chunk (padded with zeros)."""
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), JSON_CHUNK) + text
    if blob:
        blob = blob + bytes(-len(blob) % 4)
        body += struct.pack("<I4s", len(blob), BIN_CHUNK) + blob
    return struct.pack("<4sII", GLB_MAGIC, 2, 12 + len(body)) + body


@dataclass
class Gltf:
    """A glTF document and its buffers' bytes."""

    doc: dict
    buffers: list[bytes]

    def items(self, key: str) -> list[dict]:
        return self.doc.get(key, [])

    def view_bytes(self, index: int) -> bytes:
        view = self.doc["bufferViews"][index]
        data = self.buffers[view["buffer"]]
        start = view.get("byteOffset", 0)
        return data[start : start + view["byteLength"]]

    def values(self, index: int, normalize: bool = True) -> list:
        """The elements of accessor ``index``: tuples (numbers for a SCALAR); a normalized integer accessor as
        floats in [0, 1] or [-1, 1] unless ``normalize`` is False."""
        acc = self.doc["accessors"][index]
        component, kind, count = acc["componentType"], acc["type"], acc["count"]
        n = COMPONENTS[kind]
        if "bufferView" in acc:
            rows = self._dense(acc, n)
        else:
            rows = [tuple([0] * n) for _ in range(count)]
        if "sparse" in acc:
            rows = self._sparse(acc, rows, n)
        if normalize and acc.get("normalized") and component in _NORM:
            scale = _NORM[component]
            rows = [tuple(max(c / scale, -1.0) for c in r) for r in rows]
        return [r[0] for r in rows] if n == 1 else rows

    def _dense(self, acc: dict, n: int) -> list[tuple]:
        component, count = acc["componentType"], acc["count"]
        view = self.doc["bufferViews"][acc["bufferView"]]
        data = self.view_bytes(acc["bufferView"])
        size = _SIZE[component] * n
        stride = view.get("byteStride", size)
        start = acc.get("byteOffset", 0)
        if start + stride * (count - 1) + size > len(data):
            raise GltfError("an accessor runs past the end of its buffer view")
        fmt = f"<{n}{_PACK[component]}"
        return [struct.unpack_from(fmt, data, start + k * stride) for k in range(count)]

    def _sparse(self, acc: dict, rows: list[tuple], n: int) -> list[tuple]:
        sparse = acc["sparse"]
        idx, val = sparse["indices"], sparse["values"]
        k = sparse["count"]
        ifmt = f"<{k}{_PACK[idx['componentType']]}"
        where = struct.unpack_from(ifmt, self.view_bytes(idx["bufferView"]), idx.get("byteOffset", 0))
        vfmt = f"<{k * n}{_PACK[acc['componentType']]}"
        flat = struct.unpack_from(vfmt, self.view_bytes(val["bufferView"]), val.get("byteOffset", 0))
        rows = list(rows)
        for j, at in enumerate(where):
            rows[at] = tuple(flat[j * n : (j + 1) * n])
        return rows


def read_gltf(path: Path) -> Gltf:
    """A ``.glb`` or ``.gltf`` file with its buffers (a ``.bin`` beside it, or embedded as a data URI)."""
    data = path.read_bytes()
    if data[:4] == GLB_MAGIC:
        doc, chunk = parse_glb(data)
        buffers = [chunk if "uri" not in b else _uri(path, b["uri"]) for b in doc.get("buffers", [])]
    else:
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GltfError(f"{path.name}: not a glTF file ({exc})") from exc
        buffers = [_uri(path, b.get("uri", "")) for b in doc.get("buffers", [])]
    if not str(doc.get("asset", {}).get("version", "")).startswith("2"):
        raise GltfError(f"{path.name}: not glTF 2.0")
    return Gltf(doc, buffers)


def parse_glb(data: bytes) -> tuple[dict, bytes]:
    """The JSON document and the binary chunk of a ``.glb``."""
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != 2 or length > len(data):
        raise GltfError("not a binary glTF 2.0 file")
    at, doc, blob = 12, None, b""
    while at + 8 <= length:
        size, kind = struct.unpack_from("<I4s", data, at)
        chunk = data[at + 8 : at + 8 + size]
        if kind == JSON_CHUNK:
            doc = json.loads(chunk.decode("utf-8"))
        elif kind == BIN_CHUNK:
            blob = chunk
        at += 8 + size
    if doc is None:
        raise GltfError("binary glTF without a JSON chunk")
    return doc, blob


def _uri(path: Path, uri: str) -> bytes:
    if uri.startswith("data:"):
        _head, _, payload = uri.partition(",")
        return base64.b64decode(payload)
    target = path.parent / uri.replace("%20", " ")
    if not target.is_file():
        raise GltfError(f"{path.name}: the buffer {uri!r} is not beside it")
    return target.read_bytes()
