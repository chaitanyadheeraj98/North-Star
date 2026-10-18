"""Condense a long spec or plan into something the analyzer can classify.

Implementation plans run to hundreds of thousands of characters, which the
analyzer neither needs nor should be sent on every analysis: picking a model
depends on the plan's size, shape and riskiest steps, not on every word. The
executing model reads the full file from the repository instead (see the
handoff), so nothing is lost by condensing here.

Deterministic on purpose: the same document always condenses to the same text,
so re-analysing it is comparable, and the condensed text is plain enough for the
offline analyzer too. Tasks are never condensed; they keep their own limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas.enums import InputType

#: Upper bound on what is sent to the analyzer for a spec or plan.
ANALYSIS_LIMIT = 30_000

_HEADING = re.compile(r"^ {0,3}#{1,6}\s+\S")
_NUMBERED_STEP = re.compile(r"^\s*\d+[.)]\s+\S")
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s")
#: path/like/this.ext, or a bare file.ext inside backticks.
_PATH = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,7}\b|`([\w.-]+\.[A-Za-z][A-Za-z0-9]{0,7})`"
)
_KIND = {InputType.SPEC: "spec", InputType.PLAN: "implementation plan"}


@dataclass(frozen=True)
class Digest:
    text: str
    condensed: bool
    original_chars: int


def condense_for_analysis(text: str, input_type: InputType) -> Digest:
    """The text to analyze. Unchanged unless it is a spec or plan over the limit."""
    if input_type == InputType.TASK or len(text) <= ANALYSIS_LIMIT:
        return Digest(text=text, condensed=False, original_chars=len(text))

    lines = text.splitlines()
    headings = [line.strip() for line in lines if _HEADING.match(line)]
    steps = sum(1 for line in lines if _NUMBERED_STEP.match(line))
    checkboxes = sum(1 for line in lines if _CHECKBOX.match(line))
    paths = _paths(text)

    note = (
        f"[CONDENSED FOR CLASSIFICATION] The original {_KIND[input_type]} is "
        f"{len(text):,} characters and {len(lines):,} lines, with {len(headings):,} "
        f"headings, {steps:,} numbered steps, {checkboxes:,} checklist items and "
        f"{len(paths):,} distinct file paths. Below are its outline, the file paths "
        "it references, and the opening of each section. The executing model reads "
        "the full document."
    )
    budget = ANALYSIS_LIMIT - len(note) - 200  # room for the section separators
    outline = _bounded_list("Outline", headings, budget * 30 // 100)
    files = _bounded_list("Referenced files", paths, budget * 15 // 100)
    excerpts = _excerpts(lines, budget - len(outline) - len(files))

    parts = [note, outline, files, "## Section openings", excerpts]
    condensed = "\n\n".join(part for part in parts if part)
    # The pieces are each bounded; this only guards against rounding.
    return Digest(text=condensed[:ANALYSIS_LIMIT], condensed=True, original_chars=len(text))


def _paths(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in _PATH.finditer(text):
        path = match.group(1) or match.group(0)
        if "://" not in path and not path.startswith("www."):
            seen.setdefault(path, None)
    return list(seen)


def _bounded_list(title: str, items: list[str], limit: int) -> str:
    if not items:
        return ""
    out = [f"## {title}"]
    used = len(out[0])
    for index, item in enumerate(items):
        line = f"- {item[:200]}"
        if used + len(line) + 1 > limit:
            out.append(f"- ... and {len(items) - index:,} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _excerpts(lines: list[str], budget: int) -> str:
    """The opening of every section, an equal share each.

    Without headings the document is one section, and its start and end are
    kept: plans tend to put the overview first and verification last.
    """
    sections: list[list[str]] = [[]]
    for line in lines:
        if _HEADING.match(line) and sections[-1]:
            sections.append([])
        sections[-1].append(line)
    sections = [s for s in sections if any(line.strip() for line in s)]
    if not sections or budget <= 0:
        return ""

    if len(sections) == 1:
        body = "\n".join(sections[0])
        head, tail = budget * 70 // 100, budget * 25 // 100
        return f"{body[:head]}\n[... {len(body) - head - tail:,} characters omitted ...]\n{body[-tail:]}"

    # Each excerpt also costs a "[...]" marker and a blank line.
    share = budget // len(sections) - 10
    if share < 300:
        # Too many sections for a useful share each: fewer, longer excerpts.
        keep = max(1, (budget - 80) // 310)
        share = (budget - 80) // keep - 10
        omitted = len(sections) - keep
        sections = sections[:keep]
    else:
        omitted = 0

    out = []
    for section in sections:
        body = "\n".join(section)
        if len(body) > share:
            cut = body.rfind("\n", 0, share)
            body = body[: cut if cut > share // 2 else share] + "\n[...]"
        out.append(body)
    if omitted:
        out.append(f"[... {omitted:,} later sections omitted; see the outline ...]")
    return "\n\n".join(out)
