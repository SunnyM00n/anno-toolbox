"""Loader for Anno 2070 localisation text files.

Texts live under ``data/loca/<lang>/txt/*.txt`` (plus ``txt/addon/`` for the
Deep Ocean addon). Each file is UTF-16 with BOM and holds ``GUID=text`` lines,
``#`` comments and blank lines. Text GUIDs share the number space with asset
GUIDs, so an asset's display name is simply ``texts[asset.guid]``.
"""

from __future__ import annotations

import re

from .overlay import CSourceTree


def decode_text_file(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    # Heuristic: UTF-16LE without BOM shows NUL bytes in odd positions.
    if len(data) >= 2 and data[1] == 0:
        return data.decode("utf-16-le", "replace")
    return data.decode("utf-8", "replace")


def parse_guid_text(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key.lstrip("-").isdigit():
            continue
        out[int(key)] = value.strip()
    return out


GUIDNAME_RE = re.compile(r"\[GUIDNAME (\d+)\]")


def resolve_references(texts: dict[int, str], depth: int = 5) -> dict[int, str]:
    """Expand ``[GUIDNAME n]`` tokens (a text quoting another text, e.g. a building
    variation named after its base and a company) in place; unknown references vanish."""
    for _ in range(depth):
        changed = False
        for guid, text in texts.items():
            if "[GUIDNAME" not in text:
                continue
            new = GUIDNAME_RE.sub(
                lambda match, group=guid: texts.get(int(match.group(1)), "") if int(match.group(1)) != group else "",
                text,
            )
            if new != text:
                texts[guid] = re.sub(r"\s{2,}", " ", new).strip()
                changed = True
        if not changed:
            break
    return texts


DEVELOPER_TEXT_FILES = ("test.txt",)  # 2070's developer strings: they fill gaps, they never replace a shipped text


def load_texts(tree: CSourceTree, language: str | None = None) -> dict[int, str]:
    """Merge every text file of a language (later files override earlier ones, developer files give way to
    all of them) and expand text references."""
    language_key = (language or tree.m_language).lower()
    paths = list(tree.iter_paths(f"data/loca/{language_key}/txt/", suffix=".txt"))
    paths.sort(key=lambda rel: rel.rsplit("/", 1)[-1] not in DEVELOPER_TEXT_FILES)  # stable: developer files first
    merged: dict[int, str] = {}
    for rel in paths:
        merged.update(parse_guid_text(decode_text_file(tree.read_bytes(rel))))
    return resolve_references(merged)
