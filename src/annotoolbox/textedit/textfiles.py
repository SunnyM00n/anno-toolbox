"""A game's text files as entries with their places, and written back: byte for byte when nothing changed.

Two styles (docs/text-editor.md, "What each game's text files are"): ``xml`` (117, 1800, 2205: ``<Text>`` entries with a
``LineId`` or ``GUID``) and ``lines`` (2070, 1404, 1701: ``key=text`` lines, UTF-16 as a rule). ``parse`` reads one
file, ``STextFile.render`` writes it with some texts replaced and some added, touching nothing else; one pass, so a
whole translation of a 7 MB file costs what reading it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

XML, LINES = "xml", "lines"
_XML_ENTRY = re.compile(r"<Text>\s*<(LineId|GUID)>\s*(-?\d+)\s*</\1>\s*(<Text(?:\s*/>|>(.*?)</Text>))\s*</Text>", re.S)
_LINE = re.compile(r"^[ \t]*([-\w.]+)[ \t]*=([^\r\n]*)", re.M)
_ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#[0-9]+|lt|gt|amp|quot|apos);")
_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


@dataclass(frozen=True)
class SSlot:
    """One text of a file: its key, its value as shown and the span in the decoded text a rewrite replaces."""

    key: str
    value: str
    start: int
    end: int


@dataclass
class STextFile:
    path: str
    style: str
    raw: bytes
    text: str
    encoding: str  # how ``text`` becomes bytes again
    slots: list[SSlot] = field(default_factory=list)
    key_tag: str = ""  # xml: LineId or GUID

    @property
    def eol(self) -> str:
        return "\r\n" if "\r\n" in self.text else "\n"

    def render(self, changes: dict[str, str] | None = None, added: dict[str, str] | None = None) -> bytes:
        """The file with ``changes`` (key -> text) put in where the key is (every slot of it; one that already holds the
        text stays as it is) and ``added`` appended; the original bytes when that changes nothing."""
        changes, added = changes or {}, added or {}
        pieces, cursor = [], 0
        for slot in self.slots:  # in file order: the pieces are joined once
            if slot.key in changes and changes[slot.key] != slot.value:
                pieces += [self.text[cursor : slot.start], self._put(changes[slot.key])]
                cursor = slot.end
        text = self.text
        if pieces:
            text = "".join([*pieces, self.text[cursor:]])
        if added:
            text = self._append(text, added)
        return self.raw if text == self.text else encode(text, self.encoding)

    def _put(self, value: str) -> str:
        if self.style == LINES:
            return one_line(value)
        return f"<Text>{_escape(value).replace(chr(10), self.eol)}</Text>"

    def _append(self, text: str, added: dict[str, str]) -> str:
        eol = self.eol
        if self.style == LINES:
            tail = "" if not text or text.endswith(("\n", "\r")) else eol
            return text + tail + "".join(f"{key}={one_line(value)}{eol}" for key, value in added.items())
        tag = self.key_tag or "GUID"
        body = "".join(
            f"    <Text>{eol}      <{tag}>{key}</{tag}>{eol}      <Text>{_escape(value).replace(chr(10), eol)}</Text>{eol}    </Text>{eol}"
            for key, value in added.items()
        )
        end = text.rfind("</Texts>")
        if end < 0:
            raise ValueError(f"{self.path}: no </Texts> to add the texts before")
        return text[:end].rstrip(" ") + body + "  " + text[end:]


def detect(raw: bytes) -> str:
    """The codec that turns a file's bytes into text and back the way the file has them."""
    if raw.startswith(b"\xff\xfe"):
        return "utf-16"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be-bom"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if len(raw) >= 2 and raw[1] == 0:
        return "utf-16-le"
    return "utf-8"


def decode(raw: bytes, encoding: str) -> str:
    """The text of a file; a byte that is no character (a broken file) is kept as a lone surrogate, so ``encode`` writes
    the very same byte again when the file is rewritten."""
    if encoding.startswith("utf-16"):
        return raw.decode("utf-16" if encoding != "utf-16-le" else encoding, "surrogatepass")
    return raw.decode(encoding, "surrogateescape")


def encode(text: str, encoding: str) -> bytes:
    if encoding == "utf-16":
        return b"\xff\xfe" + text.encode("utf-16-le", "surrogatepass")
    if encoding == "utf-16-be-bom":
        return b"\xfe\xff" + text.encode("utf-16-be", "surrogatepass")
    return text.encode(encoding, "surrogatepass" if encoding.startswith("utf-16") else "surrogateescape")


def one_line(value: str) -> str:
    """A text for a file that keeps one text per line: a line break is the tag ``<br/>`` there."""
    return value.replace("\r\n", "<br/>").replace("\n", "<br/>").replace("\r", "<br/>")


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unescape(text: str) -> str:
    def one(match: re.Match) -> str:
        name = match.group(1)
        if name in _NAMED:
            return _NAMED[name]
        code = int(name[2:], 16) if name.startswith("#x") else int(name[1:])
        return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code < 0xE000 else match.group()  # no such character

    return _ENTITY.sub(one, text).replace("\r\n", "\n")


def parse(path: str, raw: bytes, style: str) -> STextFile:
    """A file's entries with their places (``style``: ``XML`` or ``LINES``)."""
    encoding = detect(raw)
    text = decode(raw, encoding)
    if style == LINES:
        slots = [SSlot(match.group(1), match.group(2).strip(), *_value_span(match)) for match in _LINE.finditer(text)]
        return STextFile(path, style, raw, text, encoding, slots)
    slots = [
        SSlot(match.group(2), _unescape(match.group(4) or ""), match.start(3), match.end(3))
        for match in _XML_ENTRY.finditer(text)
    ]
    tag = "LineId" if "<LineId>" in text else "GUID"
    return STextFile(path, style, raw, text, encoding, slots, tag)


def _value_span(match: re.Match) -> tuple[int, int]:
    """The span of a line's text without the blanks round it, which a rewrite keeps."""
    value = match.group(2)
    if not value.strip():
        return match.start(2), match.start(2)
    return match.start(2) + len(value) - len(value.lstrip()), match.end(2) - (len(value) - len(value.rstrip()))
