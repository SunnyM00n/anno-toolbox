"""What a translation must keep: placeholders, markup, line breaks, and what is only worth a warning (docs/text-editor.md,
"What is checked"). Pure functions over two strings, the rest of the app asks ``check`` per text.

``check(source, target, one_line=...)`` returns ``SProblem``s, errors first; ``placeholders`` and ``tags`` are the
tokens it compares.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ..infra.i18n import tr

ERROR, WARNING, HINT = "error", "warning", "hint"
_TAG = re.compile(r"</?[A-Za-z][^<>]*>|</>")
_BRACKET = re.compile(r"\[[^\[\]\r\n]{1,100}\]")
_VOID = {"br", "img", "hr", "custom-properties", "trigger", "icon"}  # tags that are never closed
_LONG_FACTOR, _LONG_EXTRA = 1.4, 12


@dataclass(frozen=True)
class SProblem:
    severity: str
    kind: str  # placeholder, markup, braces, break, breaks, length, ends
    message: str


def braces(text: str) -> tuple[list[str], bool]:
    """The ``{...}`` groups of a text, nesting kept (117's Lua expressions and holes), and whether all were closed."""
    out, depth, start, ok = [], 0, 0, True
    for i, character in enumerate(text):
        if character == "{":
            if depth == 0:
                start = i
            depth += 1
        elif character == "}":
            if depth == 0:
                ok = False
                continue
            depth -= 1
            if depth == 0:
                out.append(text[start : i + 1])
    return out, ok and depth == 0


def placeholders(text: str) -> list[str]:
    """The tokens a translation must carry over unchanged: ``{expression}`` groups and ``[Tags]``."""
    groups, _ = braces(text)
    outside = text
    for group in groups:
        outside = outside.replace(group, " ")
    return [*groups, *_BRACKET.findall(outside)]


def tags(text: str) -> list[str]:
    """The rich-text tags of a text, as one spelling (``<br />`` is ``<br/>``)."""
    return [re.sub(r"\s*/>$", "/>", tag) for tag in _TAG.findall(text)]


def _markup(text: str) -> list[str]:
    """The tags that must match: ``<br/>`` is left to the line break check (a translator may break lines elsewhere)."""
    return [tag for tag in tags(text) if _name(tag) != "br"]


def _name(tag: str) -> str:
    return re.match(r"</?\s*([A-Za-z][\w-]*)", tag).group(1).lower() if tag != "</>" else ""


def unbalanced(text: str) -> list[str]:
    """The tags a text leaves open or closes without opening (``</>`` closes the last one)."""
    stack, bad = [], []
    for tag in tags(text):
        name = _name(tag)
        if tag.endswith("/>") or name in _VOID:
            continue
        if tag.startswith("</"):
            if name and stack and stack[-1] == name or (not name and stack):
                stack.pop()
            else:
                bad.append(tag)
        else:
            stack.append(name)
    return bad + [f"<{name}>" for name in stack]


def breaks(text: str) -> int:
    """How many line breaks a text holds: real ones and ``<br/>``."""
    return text.count("\n") + len(re.findall(r"<br\s*/?>", text, re.I))


def _differences(source: list[str], target: list[str]) -> tuple[list[str], list[str]]:
    missing, extra = Counter(source) - Counter(target), Counter(target) - Counter(source)
    return sorted(missing.elements()), sorted(extra.elements())


def check(source: str, target: str, one_line: bool = False) -> list[SProblem]:
    """Everything wrong or doubtful about ``target`` as a translation of ``source``; errors first. ``one_line``: the
    game keeps one text per line, so a real line break would end the text."""
    if not target.strip():
        return [SProblem(WARNING, "length", tr("texts.text_empty"))] if source.strip() else []
    out: list[SProblem] = []
    _, closed = braces(target)
    if not closed and braces(source)[1]:
        out.append(SProblem(ERROR, "braces", tr("texts.brace_no_partner")))
    missing, extra = _differences(placeholders(source), placeholders(target))
    out += [SProblem(ERROR, "placeholder", tr("texts.missing_token", token=token)) for token in missing]
    out += [SProblem(ERROR, "placeholder", tr("texts.token_not_source", token=token)) for token in extra]
    missing, extra = _differences(_markup(source), _markup(target))
    out += [SProblem(ERROR, "markup", tr("texts.missing_tag_token", token=token)) for token in missing]
    out += [SProblem(ERROR, "markup", tr("texts.tag_not_source", token=token)) for token in extra]
    if not (missing or extra):
        out += [
            SProblem(ERROR, "markup", tr("texts.left_open_closed_twice", token=token))
            for token in (Counter(unbalanced(target)) - Counter(unbalanced(source))).elements()
        ]
    if one_line and "\n" in target:
        out.append(SProblem(ERROR, "break", tr("texts.game_keeps_one_text_per")))
    elif breaks(source) != breaks(target):
        out.append(
            SProblem(
                WARNING,
                "breaks",
                tr("texts.line_breaks_source", target=breaks(target), source=breaks(source)),
            )
        )
    if len(target) > max(len(source) * _LONG_FACTOR, len(source) + _LONG_EXTRA):
        out.append(
            SProblem(
                WARNING,
                "length",
                tr(
                    "texts.characters_source_not_fit",
                    target=len(target),
                    source=len(source),
                ),
            )
        )
    if (source[:1].isspace(), source[-1:].isspace()) != (target[:1].isspace(), target[-1:].isspace()):
        out.append(SProblem(HINT, "ends", tr("texts.space_break_end_differs_source")))
    return sorted(out, key=lambda problem: (ERROR, WARNING, HINT).index(problem.severity))
