"""Stand-ins for the RDM tools: an export and a strip of a mesh."""

from __future__ import annotations

from annotoolbox.formats.rdmread import read_rdm
from annotoolbox.rdm import rdmgltf


def export(name, data, anims=(), **kw):
    sources = [rdmgltf.SSource(name, read_rdm(data))]
    anim = [rdmgltf.SSource(n, read_rdm(a)) for n, a in anims]
    builder, notes = rdmgltf.export(sources, anim, options=rdmgltf.SOptions(**kw))
    return builder, notes


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k != "extras"}
    return [strip(v) for v in o] if isinstance(o, list) else o
