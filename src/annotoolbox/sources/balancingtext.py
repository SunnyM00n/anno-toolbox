"""Reading 2070's balancing and config text by its tags: the one place for "the block that holds X" and "the text of
one tag".

The balancing repeats tag names (an empty default block sits beside the one with the values, and some blocks nest
their own name), so a reader asks for a block by what it holds. ``balancingfiles`` finds and loads the files;
these functions read what is in them. Pure text, no files.
"""

from __future__ import annotations

import re


def blocks(text: str, tag: str) -> list[str]:
    """Every ``<tag>...</tag>`` of a text, tags included, in file order."""
    return re.findall(rf"<{tag}>.*?</{tag}>", text, re.S)


def longest_block(text: str, tag: str) -> str:
    """The longest ``<tag>`` block, tags included, else "": the values of a section, not the empty default block that
    sits beside them."""
    return max(blocks(text, tag), key=len, default="")


def block_holding(text: str, tag: str, holding: str = "", skip: str = "") -> str:
    """The body of the first ``<tag>`` that holds ``holding`` and not ``skip``, else "" (tags not included)."""
    for m in re.finditer(rf"<{tag}>(.*?)</{tag}>", text, re.S):
        body = m.group(1)
        if holding in body and not (skip and skip in body):
            return body
    return ""


def outer_block_holding(text: str, tag: str, holding: str) -> str:
    """The body of the longest outermost ``<tag>`` that holds ``holding``, else "": for a tag that nests its own name
    in its values (``EcoBalance`` curves), which ``block_holding`` would cut short."""
    found, depth, start = [], 0, 0
    for m in re.finditer(rf"<(/?){tag}>", text):
        if not m.group(1):
            depth += 1
            if depth == 1:
                start = m.end()
        elif depth:
            depth -= 1
            if depth == 0 and holding in text[start : m.start()]:
                found.append(text[start : m.start()])
    return max(found, key=len, default="")


def tag_text(block: str, tag: str, default: str = "") -> str:
    """The text of the first ``<tag>text</tag>`` in a block (no child tags in it), ``default`` without one."""
    m = re.search(rf"<{re.escape(tag)}>([^<]*)</{re.escape(tag)}>", block)
    return m.group(1) if m else default


def tag_float(block: str, tag: str, default: float = 0.0) -> float:
    """The number of the first ``<tag>`` in a block, ``default`` without the tag (a tag with no number is an error)."""
    m = re.search(rf"<{re.escape(tag)}>([^<]*)</{re.escape(tag)}>", block)
    return float(m.group(1)) if m else default


def tag_int(block: str, tag: str, default: int | None = None) -> int | None:
    """The whole number of the first ``<tag>number</tag>`` in a block, ``default`` when it holds none."""
    m = re.search(rf"<{re.escape(tag)}>(-?\d+)</{re.escape(tag)}>", block)
    return int(m.group(1)) if m else default


def tag_ints(body: str) -> dict[str, int]:
    """Every ``<tag>number</tag>`` of a text as tag -> number (the last of a repeated tag wins)."""
    return {k: int(v) for k, v in re.findall(r"<(\w+)>(-?\d+)</\1>", body)}


def plain(text: str) -> str:
    """2070's markup to plain text: line breaks kept, tags and doubled blanks dropped."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", text)).strip()
