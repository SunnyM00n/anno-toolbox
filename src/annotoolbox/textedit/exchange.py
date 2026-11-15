"""A translation as a file for outside tools and back: CSV, XLIFF 1.2 and PO (docs/text-editor.md, "Import and export").

``rows_of(doc, ids)`` makes the rows of the open pair of languages; ``write(fmt, rows, ...)`` and ``read(fmt, data)``
are the formats; ``apply(doc, rows, add_new)`` puts the rows into the project as one undo step and returns an
``Applied`` that says what became of them.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..infra.i18n import tr
from . import checks
from .textdoc import OUTDATED, REVIEW, TRANSLATED, TextDoc

FORMATS = ("csv", "xliff", "po")
SUFFIXES = {"csv": ".csv", "xliff": ".xlf", "po": ".po"}
COLUMNS = ("id", "file", "group", "source", "target", "status")
XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CODES = {
    "english": "en", "german": "de", "french": "fr", "italian": "it", "spanish": "es", "polish": "pl",
    "russian": "ru", "czech": "cs", "hungarian": "hu", "japanese": "ja", "korean": "ko", "brazilian": "pt-BR",
    "portuguese": "pt", "chinese": "zh", "simplified_chinese": "zh-Hans", "traditional_chinese": "zh-Hant",
    "taiwanese": "zh-TW",
}  # fmt: skip


@dataclass(frozen=True)
class Row:
    id: str
    source: str
    target: str
    file: str = ""
    group: str = ""
    status: str = ""


@dataclass
class Applied:
    applied: int = 0
    unchanged: int = 0
    empty: int = 0
    unknown: list[str] = field(default_factory=list)
    added: int = 0
    problems: int = 0

    def line(self) -> str:
        return tr(
            "{applied} applied, {unchanged} unchanged, {empty} without a translation, {unknown} unknown ids, {added} added, {problems} with problems",
            applied=self.applied, unchanged=self.unchanged, empty=self.empty, unknown=len(self.unknown),
            added=self.added, problems=self.problems,
        )  # fmt: skip


def rows_of(doc: TextDoc, ids: list[str]) -> list[Row]:
    out = []
    for key in ids:
        entry = doc.entry(key)
        out.append(Row(key, doc.source_text(key), doc.target_text(key), entry.file if entry else "",
                       entry.group if entry else "", doc.status(key)))  # fmt: skip
    return out


def code_of(language: str) -> str:
    return CODES.get(language, language)


def write(fmt: str, rows: list[Row], game: str, source: str, target: str) -> bytes:
    if fmt == "csv":
        return write_csv(rows)
    if fmt == "xliff":
        return write_xliff(rows, game, source, target)
    if fmt == "po":
        return write_po(rows, game, source, target)
    raise ValueError(tr("unknown format {fmt}", fmt=fmt))


def read(fmt: str, data: bytes) -> list[Row]:
    readers = {"csv": read_csv, "xliff": read_xliff, "po": read_po}
    if fmt not in readers:
        raise ValueError(tr("unknown format {fmt}", fmt=fmt))
    try:
        return readers[fmt](data)
    except (ET.ParseError, csv.Error, UnicodeError) as exc:
        raise ValueError(tr("not a readable {fmt} file: {error}", fmt=fmt, error=exc)) from exc


def format_of(path: str) -> str:
    """The format a file name says (``.csv``, ``.xlf`` / ``.xliff``, ``.po``)."""
    suffix = path.lower().rsplit(".", 1)[-1]
    found = {"csv": "csv", "xlf": "xliff", "xliff": "xliff", "po": "po"}.get(suffix)
    if found is None:
        raise ValueError(tr("{path}: not a CSV, XLIFF or PO file", path=path))
    return found


# CSV
def write_csv(rows: list[Row]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows([r.id, r.file, r.group, r.source, r.target, r.status] for r in rows)
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def read_csv(data: bytes) -> list[Row]:
    """A CSV as a spreadsheet saves it: UTF-8 or UTF-16 (Excel's "Unicode text"), the separator the person's region
    uses (a comma, a semicolon or a tab: the one the header line holds most of)."""
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig")
    head = text.split("\n", 1)[0]
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=max(",;\t", key=head.count))
    reader.fieldnames = [name.strip().lower() for name in reader.fieldnames or []]
    if "id" not in reader.fieldnames or "target" not in reader.fieldnames:
        raise ValueError(tr("the CSV needs the columns id and target"))
    return [
        Row((r.get("id") or "").strip(), r.get("source") or "", r.get("target") or "", r.get("file") or "",
            r.get("group") or "")  # the status column is for the reader: a translator who edited a row made it current
        for r in reader
        if (r.get("id") or "").strip()
    ]  # fmt: skip


# XLIFF 1.2
def write_xliff(rows: list[Row], game: str, source: str, target: str) -> bytes:
    ET.register_namespace("", XLIFF_NS)
    root = ET.Element(f"{{{XLIFF_NS}}}xliff", {"version": "1.2"})
    attrs = {
        "original": game,
        "datatype": "plaintext",
        "source-language": code_of(source),
        "target-language": code_of(target),
    }
    body = ET.SubElement(ET.SubElement(root, f"{{{XLIFF_NS}}}file", attrs), f"{{{XLIFF_NS}}}body")
    for row in rows:
        unit = ET.SubElement(body, f"{{{XLIFF_NS}}}trans-unit", {"id": row.id, "xml:space": "preserve"})
        ET.SubElement(unit, f"{{{XLIFF_NS}}}source").text = row.source
        state = (
            "translated"
            if row.status in (TRANSLATED, "changed")
            else ("needs-review-translation" if row.status == OUTDATED else "new")
        )
        ET.SubElement(unit, f"{{{XLIFF_NS}}}target", {"state": state}).text = row.target
        if row.file:
            ET.SubElement(unit, f"{{{XLIFF_NS}}}note").text = row.file
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_xliff(data: bytes) -> list[Row]:
    """XLIFF 1.2 (``trans-unit``) and 2.x (``unit`` with ``segment``s); a state that asks for a review marks the row."""
    rows = []
    for unit in ET.fromstring(data).iter():
        if _local(unit.tag) not in ("trans-unit", "unit") or not (unit.get("id") or "").strip():
            continue
        parts, states = {}, []
        for node in unit.iter():
            name = _local(node.tag)
            if name in ("source", "target", "note") and name not in parts:
                parts[name] = "".join(node.itertext())
            if name in ("target", "segment") and node.get("state"):
                states.append(node.get("state", ""))
        review = any(s.startswith(("needs-review", "needs-adaptation", "needs-l10n")) for s in states)
        rows.append(
            Row(
                unit.get("id", "").strip(),
                parts.get("source", ""),
                parts.get("target", ""),
                parts.get("note", ""),
                "",
                OUTDATED if review else "",
            )  # fmt: skip
        )
    return rows


# PO
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def _quote(text: str) -> str:
    return '"' + "".join(_ESCAPES.get(c, c) for c in text) + '"'


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _UNESCAPES.get(m.group(1), m.group(1)), text[1:-1])


def write_po(rows: list[Row], game: str, source: str, target: str) -> bytes:
    head = f"Project-Id-Version: {game}\\nLanguage: {code_of(target)}\\nX-Source-Language: {code_of(source)}\\nContent-Type: text/plain; charset=UTF-8\\n"
    blocks = [f'msgid ""\nmsgstr ""\n"{head}"\n']
    for row in rows:
        lines = ([f"#: {row.file}"] if row.file else []) + (["#, fuzzy"] if row.status == OUTDATED else [])
        lines += [f"msgctxt {_quote(row.id)}", f"msgid {_quote(row.source)}", f"msgstr {_quote(row.target)}"]
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks).encode("utf-8")


def _po_entry(fields: dict[str, str], comments: list[str]) -> Row | None:
    if not fields.get("msgctxt"):
        return None  # the header, or an entry without an id
    where = next((c[3:].strip() for c in comments if c.startswith("#:")), "")
    fuzzy = any(c.startswith("#,") and "fuzzy" in c for c in comments)
    return Row(
        fields["msgctxt"], fields.get("msgid", ""), fields.get("msgstr", ""), where, "", OUTDATED if fuzzy else ""
    )


def read_po(data: bytes) -> list[Row]:
    """The entries of a PO file that carry a ``msgctxt`` (the id). Lines end at a line feed only: a text may hold other
    characters Python takes for line ends (U+2028, U+0085). An entry also ends where the next begins, blank line or not."""
    rows: list[Row] = []
    fields: dict[str, str] = {}
    last, comments = "", []
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    for raw in [*text.split("\n"), ""]:
        line = raw.strip()
        if line.startswith('"') and last:
            fields[last] += _unquote(line)
            continue
        starts = re.match(r"(msgctxt|msgid|msgstr)\s+(\".*\")$", line)
        if (
            not line
            or (line.startswith("#") and "msgstr" in fields)
            or (starts and starts.group(1) == "msgctxt" and "msgstr" in fields)
        ):
            if (row := _po_entry(fields, comments)) is not None:
                rows.append(row)
            fields, last, comments = {}, "", []
        if starts:
            last = starts.group(1)
            fields[last] = _unquote(starts.group(2))
        elif line.startswith("#") and not line.startswith("#~"):
            comments.append(line)
    return rows


# into the project
def apply(doc: TextDoc, rows: list[Row], add_new: bool = False) -> Applied:
    """The rows' targets as the project's edits, one undo step. A row that carries a source different from the one now is
    taken as written against that source (the row becomes outdated); an unknown id is skipped, or added as a new text."""
    result, items = Applied(), []
    known = set(doc.ids())
    for row in {r.id: r for r in rows}.values():  # an id twice in a file: the last one counts
        target = row.target.replace("\r\n", "\n").replace("\r", "\n")
        if row.id not in known and not add_new:
            result.unknown.append(row.id)
        elif not target.strip():
            result.empty += 1
        elif row.id not in known:
            items += [(doc.source, row.id, row.source, None)] if row.source.strip() else []
            items.append((doc.target, row.id, target, None))
            result.added += 1
        elif target == doc.target_text(row.id):
            result.unchanged += 1
        else:
            now = doc.source_text(row.id)
            written_against = (
                row.source if row.source and row.source != now else (REVIEW if row.status == OUTDATED else None)
            )
            items.append((doc.target, row.id, target, written_against))
            result.applied += 1
    doc.edit_many(items, tr("import {count} texts", count=len(items)))
    result.problems = sum(
        1 for lang, key, *_ in items if lang == doc.target and any(p.severity != checks.HINT for p in doc.problems(key))
    )
    return result
