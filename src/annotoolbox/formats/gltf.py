"""glTF 2.0 files, written and read without a library: binary ``.glb``, ``.gltf`` with a ``.bin`` beside it, or one
``.gltf`` with its buffer embedded.

``CBuilder`` collects the buffer, its views and accessors while a document is filled; ``CBuilder.files`` writes it in
one of the three forms. ``read_gltf`` reads any of them back (``SGltf.values`` gives an accessor's elements, sparse
accessors and strides included). The specification: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

BYTE, UBYTE, SHORT, USHORT, UINT, FLOAT = 5120, 5121, 5122, 5123, 5125, 5126
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_PACK = {BYTE: "b", UBYTE: "B", SHORT: "h", USHORT: "H", UINT: "I", FLOAT: "f"}
_SIZE = {BYTE: 1, UBYTE: 1, SHORT: 2, USHORT: 2, UINT: 4, FLOAT: 4}
_NORM = {BYTE: 127.0, UBYTE: 255.0, SHORT: 32767.0, USHORT: 65535.0}
FORMS = ("glb", "gltf", "gltfmin", "embedded")
GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = b"glTF", b"JSON", b"BIN\x00"
DATA_URI = "data:application/octet-stream;base64,"


class CGltfError(ValueError):
    """Not a glTF 2.0 file, or one this reader cannot take (the message says what)."""


@dataclass
class CBuilder:
    """The parts of a document that point into the one buffer; ``doc`` holds everything else."""

    m_doc: dict = field(default_factory=lambda: {"asset": {"version": "2.0"}})
    m_blob: bytearray = field(default_factory=bytearray)

    def view(self, payload: bytes, target: int | None = None) -> int:
        """A buffer view over ``payload`` (aligned to 4 bytes); its index."""
        while len(self.m_blob) % 4:
            self.m_blob.append(0)
        view = {"buffer": 0, "byteOffset": len(self.m_blob), "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self.m_blob.extend(payload)
        self.m_doc.setdefault("bufferViews", []).append(view)
        return len(self.m_doc["bufferViews"]) - 1

    def accessor(
        self, values: list, kind: str, component: int, *, normalized: bool = False, target: int | None = None
    ) -> int:
        """An accessor of ``values`` (one tuple per element, or numbers for a SCALAR); its index. A float accessor
        gets its ``min`` / ``max`` (glTF asks for them on positions)."""
        component_count = COMPONENTS[kind]
        flat = [component_value for item in values for component_value in item] if component_count > 1 else list(values)
        payload = struct.pack(f"<{len(flat)}{_PACK[component]}", *flat)
        acc = {"bufferView": self.view(payload, target), "componentType": component, "count": len(values)}
        acc["type"] = kind
        if normalized:
            acc["normalized"] = True
        if component == FLOAT and values and kind in ("SCALAR", "VEC2", "VEC3", "VEC4"):
            rows = [item if component_count > 1 else (item,) for item in values]
            acc["min"] = [min(row[k] for row in rows) for k in range(component_count)]
            acc["max"] = [max(row[k] for row in rows) for k in range(component_count)]
        self.m_doc.setdefault("accessors", []).append(acc)
        return len(self.m_doc["accessors"]) - 1

    def add(self, key: str, item: dict) -> int:
        self.m_doc.setdefault(key, []).append(item)
        return len(self.m_doc[key]) - 1

    def files(self, path: Path, form: str = "glb") -> dict[Path, bytes]:
        """The file(s) of the document at ``path``: one ``.glb``, a ``.gltf`` and its ``.bin`` (``gltfmin``: the JSON
        on one line), or an embedded ``.gltf`` (``FORMS``)."""
        doc = dict(self.m_doc)
        blob = bytes(self.m_blob) + bytes(-len(self.m_blob) % 4)
        if form == "glb":
            doc["buffers"] = [{"byteLength": len(blob)}] if blob else []
            return {path: glb_bytes(doc, blob)}
        if form in ("gltf", "gltfmin"):
            bin_path = path.with_suffix(".bin")
            doc["buffers"] = [{"byteLength": len(blob), "uri": quote(bin_path.name)}] if blob else []
            return {path: _json(doc, form == "gltfmin"), **({bin_path: blob} if blob else {})}
        if form == "embedded":
            uri = DATA_URI + base64.b64encode(blob).decode("ascii")
            doc["buffers"] = [{"byteLength": len(blob), "uri": uri}] if blob else []
            return {path: _json(doc)}
        raise CGltfError(f"unknown glTF form {form!r} (one of {', '.join(FORMS)})")


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
class SGltf:
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
        component_count = COMPONENTS[kind]
        if "bufferView" in acc:
            rows = self._dense(acc, component_count)
        else:
            rows = [tuple([0] * component_count) for _ in range(count)]
        if "sparse" in acc:
            rows = self._sparse(acc, rows, component_count)
        if normalize and acc.get("normalized") and component in _NORM:
            scale = _NORM[component]
            rows = [tuple(max(packed_component / scale, -1.0) for packed_component in row) for row in rows]
        return [row[0] for row in rows] if component_count == 1 else rows

    def _dense(self, acc: dict, component_count: int) -> list[tuple]:
        component, count = acc["componentType"], acc["count"]
        view = self.doc["bufferViews"][acc["bufferView"]]
        data = self.view_bytes(acc["bufferView"])
        size = _SIZE[component] * component_count
        stride = view.get("byteStride", size)
        start = acc.get("byteOffset", 0)
        if start + stride * (count - 1) + size > len(data):
            raise CGltfError("an accessor runs past the end of its buffer view")
        pack_format = f"<{component_count}{_PACK[component]}"
        return [struct.unpack_from(pack_format, data, start + k * stride) for k in range(count)]

    def _sparse(self, acc: dict, rows: list[tuple], component_count: int) -> list[tuple]:
        sparse = acc["sparse"]
        indices, sparse_values = sparse["indices"], sparse["values"]
        k = sparse["count"]
        ifmt = f"<{k}{_PACK[indices['componentType']]}"
        where = struct.unpack_from(ifmt, self.view_bytes(indices["bufferView"]), indices.get("byteOffset", 0))
        vfmt = f"<{k * component_count}{_PACK[acc['componentType']]}"
        flat = struct.unpack_from(
            vfmt, self.view_bytes(sparse_values["bufferView"]), sparse_values.get("byteOffset", 0)
        )
        rows = list(rows)
        for j, offset in enumerate(where):
            rows[offset] = tuple(flat[j * component_count : (j + 1) * component_count])
        return rows


def read_gltf(path: Path) -> SGltf:
    """A ``.glb`` or ``.gltf`` file with its buffers (a ``.bin`` beside it, or embedded as a data URI)."""
    data = path.read_bytes()
    if data[:4] == GLB_MAGIC:
        doc, chunk = parse_glb(data)
        buffers = [chunk if "uri" not in buffer else _uri(path, buffer["uri"]) for buffer in doc.get("buffers", [])]
    else:
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CGltfError(f"{path.name}: not a glTF file ({exc})") from exc
        buffers = [_uri(path, buffer.get("uri", "")) for buffer in doc.get("buffers", [])]
    if not str(doc.get("asset", {}).get("version", "")).startswith("2"):
        raise CGltfError(f"{path.name}: not glTF 2.0")
    return SGltf(doc, buffers)


def parse_glb(data: bytes) -> tuple[dict, bytes]:
    """The JSON document and the binary chunk of a ``.glb``."""
    if len(data) < 12:
        raise CGltfError("not a binary glTF 2.0 file")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC or version != 2 or length > len(data):
        raise CGltfError("not a binary glTF 2.0 file")
    offset, doc, blob = 12, None, b""
    while offset + 8 <= length:
        size, kind = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8 : offset + 8 + size]
        if kind == JSON_CHUNK:
            try:
                doc = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CGltfError(f"the JSON chunk does not read ({exc})") from exc
        elif kind == BIN_CHUNK:
            blob = chunk
        offset += 8 + size
    if doc is None:
        raise CGltfError("binary glTF without a JSON chunk")
    return doc, blob


def _uri(path: Path, uri: str) -> bytes:
    if uri.startswith("data:"):
        _head, _, payload = uri.partition(",")
        return base64.b64decode(payload)
    target = path.parent / unquote(uri)  # a URI is percent-encoded (spaces, umlauts)
    if not target.is_file():
        target = path.parent / uri  # a writer that left the name as it is
    if not target.is_file():
        raise CGltfError(f"{path.name}: the buffer {uri!r} is not beside it")
    return target.read_bytes()
