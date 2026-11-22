"""Which texture tools a run uses: texconv where it is there, the built-in numpy and etcpak path where it is not (and on
Linux, where texconv does not run). Every texconv call of the package asks ``texconv()`` here, so one setting decides
(docs/texture-pipeline.md "Choosing the path", docs/linux.md phase 4).

``ANNO_TEXTURE_BACKEND``: ``auto`` (the default: texconv when it is found), ``texconv`` (it must be there, else
``CToolError``), ``builtin`` (never texconv). The in-process decoders and encoders come first in every case where they
read or write the format (``textures``); texconv is the first choice only for what has two equivalent routes (BC4 maps,
the cube mean, the BC7 file when etcpak is missing), and for what the built-in path cannot read.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..infra import buildcache
from ..infra.tools import find_tool, require_tool

BACKEND_ENV = "ANNO_TEXTURE_BACKEND"
MODES = ("auto", "texconv", "builtin")
BUILTIN_VERSION = 1  # bumped when a built-in encoder or reader changes what comes out (cache keys hold it)


def mode() -> str:
    """The setting (``auto`` for an unset or unknown value)."""
    value = os.environ.get(BACKEND_ENV, "").strip().lower()
    return value if value in MODES else "auto"


def texconv() -> Path | None:
    """The texconv to run now: None when ``builtin`` is set or none is found (it must be found with ``texconv``)."""
    if mode() == "builtin":
        return None
    if mode() == "texconv":
        return require_tool("texconv")
    return find_tool("texconv")


def identity() -> str:
    """What the cache keys hold of the choice: the texconv build in use, else the built-in path's version."""
    found = texconv()
    return buildcache.file_digest(found) if found else f"builtin-{BUILTIN_VERSION}"
