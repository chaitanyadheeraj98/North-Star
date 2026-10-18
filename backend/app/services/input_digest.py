"""Condense a long spec or plan into something the analyzer can classify.

Implementation plans run to hundreds of thousands of characters, which the
analyzer neither needs nor should be sent on every analysis: picking a model
depends on the plan's size, shape and riskiest steps, not on every word. The
executing model reads the full file from the repository instead (see the
handoff), so nothing is lost by condensing here.

What must never be lost is the hard part. A plan of 400 cosmetic steps with
one data migration at step 390 is a migration plan, and a digest that kept only
the first sections would route it as cosmetic. So the WHOLE document is scanned
for risk signals, and the sections that carry them are kept ahead of everything
else, wherever they are. The remaining room goes to the first and last
sections and an even spread across the middle.

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

#: What makes a step expensive to get wrong. These mirror the fingerprint's own
#: risk dimensions (database, security, concurrency) plus destructive and
#: production-facing operations. Matching is a cheap, broad net: a false
#: positive costs one kept excerpt; a miss can under-route the whole plan.
RISK_SIGNALS: dict[str, re.Pattern[str]] = {
    "data migration / schema": re.compile(
        r"\b(migrat\w*|schema|alter\s+table|drop\s+(?:table|column)|backfill\w*|"
        r"foreign\s+key|data\s+loss|re-?key\w*)\b",
        re.IGNORECASE,
    ),
    "security": re.compile(
        r"\b(auth\w*|password\w*|secret\w*|credential\w*|encrypt\w*|decrypt\w*|"
        r"permission\w*|csrf|xss|injection|token\w*|oauth|rbac|pii)\b",
        re.IGNORECASE,
    ),
    "concurrency": re.compile(
        r"\b(race\s+condition\w*|concurren\w*|deadlock\w*|idempoten\w*|"
        r"mutex\w*|locking|distributed\s+lock\w*|transaction\w*)\b",
        re.IGNORECASE,
    ),
    "destructive / production": re.compile(
        r"\b(delete\s+(?:all|existing|old)|truncate|irreversibl\w*|rollback|"
        r"production|downtime|zero[- ]downtime|cut\s*over)\b",
        re.IGNORECASE,
    ),
}

#: Smallest excerpt worth sending; below this a section says nothing useful.
_MIN_EXCERPT = 300


@dataclass(frozen=True)
class Digest:
    text: str
    condensed: bool
    original_chars: int


@dataclass(frozen=True)
class _Section:
    index: int
    heading: str
    body: str
    risks: tuple[str, ...]


def condense_for_analysis(text: str, input_type: InputType) -> Digest:
    """The text to analyze. Unchanged unless it is a spec or plan over the limit."""
    if input_type == InputType.TASK or len(text) <= ANALYSIS_LIMIT:
        return Digest(text=text, condensed=False, original_chars=len(text))

    lines = text.splitlines()
    sections = _sections(lines)
    headings = [s.heading for s in sections if s.heading]
    steps = sum(1 for line in lines if _NUMBERED_STEP.match(line))
    checkboxes = sum(1 for line in lines if _CHECKBOX.match(line))
    paths = _paths(text)
    risky = [s for s in sections if s.risks]

    note = (
        f"[CONDENSED FOR CLASSIFICATION] The original {_KIND[input_type]} is "
        f"{len(text):,} characters and {len(lines):,} lines, with {len(headings):,} "
        f"headings, {steps:,} numbered steps, {checkboxes:,} checklist items and "
        f"{len(paths):,} distinct file paths. {len(risky):,} of its {len(sections):,} "
        "sections carry risk signals; all of them are listed below and excerpted "
        "first. Below are those signals, its outline, the file paths it references, "
        "and excerpts from sections across the whole document. The executing model "
        "reads the full document."
    )
    budget = ANALYSIS_LIMIT - len(note) - 200  # room for the section separators
    risk_block = _risk_summary(sections, budget * 15 // 100)
    outline = _outline(sections, budget * 22 // 100)
    files = _bounded_list("Referenced files", paths, budget * 10 // 100)
    excerpts = _excerpts(sections, budget - len(risk_block) - len(outline) - len(files))

    parts = [note, risk_block, outline, files, "## Section excerpts", excerpts]
    condensed = "\n\n".join(part for part in parts if part)
    # The pieces are each bounded; this only guards against rounding.
    return Digest(text=condensed[:ANALYSIS_LIMIT], condensed=True, original_chars=len(text))


def _sections(lines: list[str]) -> list[_Section]:
    """Split at headings. Text before the first heading is its own section."""
    groups: list[list[str]] = [[]]
    for line in lines:
        if _HEADING.match(line) and groups[-1]:
            groups.append([])
        groups[-1].append(line)
    out = []
    for group in groups:
        body = "\n".join(group)
        if not body.strip():
            continue
        heading = group[0].strip() if _HEADING.match(group[0]) else ""
        risks = tuple(name for name, pattern in RISK_SIGNALS.items() if pattern.search(body))
        out.append(_Section(index=len(out), heading=heading, body=body, risks=risks))
    return out


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


def _label(section: _Section) -> str:
    return section.heading[:160] if section.heading else f"(section {section.index + 1}, untitled)"


def _risk_summary(sections: list[_Section], limit: int) -> str:
    """Every risk category found anywhere, with how many sections and where."""
    if limit <= 0:
        return ""
    # Fewer examples per category until it fits; never cut a category off.
    for examples_per_category in (4, 3, 2, 1, 0):
        lines = ["## Risk signals (whole document)"]
        for name in RISK_SIGNALS:
            hits = [s for s in sections if name in s.risks]
            if not hits:
                continue
            # Spread the examples across the document, not just its start.
            shown = [hits[i] for i in _spread(len(hits), examples_per_category)]
            examples = f", e.g. {'; '.join(_label(s) for s in shown)}" if shown else ""
            more = f" and {len(hits) - len(shown):,} more" if shown and len(hits) > len(shown) else ""
            lines.append(f"- {name}: {len(hits):,} sections{examples}{more}")
        if len(lines) == 1:
            lines.append("- none found")
        summary = "\n".join(lines)
        if len(summary) <= limit:
            return summary
    return ""


def _outline(sections: list[_Section], limit: int) -> str:
    """All headings if they fit; otherwise risky first and last, spread between."""
    titled = [s for s in sections if s.heading]
    if not titled or limit <= 0:
        return ""
    lines = [f"- {s.heading[:200]}" for s in titled]
    if sum(len(line) + 1 for line in lines) + 20 <= limit:
        return "## Outline\n" + "\n".join(lines)

    # Start from an estimate of how many lines fit, and shrink until it does.
    # Never truncate the text: cutting the end is exactly how late (risky)
    # headings used to disappear.
    average = max(1, sum(len(line) + 1 for line in lines) // len(lines))
    room = max(2, (limit - 60) // (average + 22))
    while True:
        outline = _abridged_outline(titled, _choose(titled, room))
        if len(outline) <= limit or room <= 2:
            return outline if len(outline) <= limit else ""
        room = max(2, room * limit // len(outline) - 1)


def _abridged_outline(titled: list[_Section], keep: set[int]) -> str:
    out = ["## Outline (abridged; risky headings are always kept)"]
    previous = -1
    for position, section in enumerate(titled):
        if section.index not in keep:
            continue
        if position - previous > 1:
            out.append(f"- ... {position - previous - 1:,} headings ...")
        out.append(f"- {section.heading[:200]}")
        previous = position
    if previous < len(titled) - 1:
        out.append(f"- ... {len(titled) - previous - 1:,} headings ...")
    return "\n".join(out)


def _excerpts(sections: list[_Section], budget: int) -> str:
    """Excerpts from risky sections first, then first/last and an even spread."""
    if not sections or budget <= 0:
        return ""
    if len(sections) == 1:
        # The whole document is one section: keep its end too, since plans
        # tend to put verification last.
        return _excerpt(sections[0], budget, keep_end=True)

    # Each excerpt also costs a "[...]" marker, a gap marker and blank lines.
    overhead = 60
    count = min(len(sections), max(1, budget // (_MIN_EXCERPT + overhead)))
    keep = _choose(sections, count)
    share = budget // len(keep) - overhead

    out = []
    previous = -1
    for section in sections:
        if section.index not in keep:
            continue
        if section.index - previous > 1:
            out.append(f"[... {section.index - previous - 1:,} sections omitted; see the outline ...]")
        out.append(_excerpt(section, share))
        previous = section.index
    if previous < len(sections) - 1:
        out.append(f"[... {len(sections) - previous - 1:,} later sections omitted; see the outline ...]")
    return "\n\n".join(out)


#: Shortest useful window around a risk signal: enough for the instruction.
_MIN_WINDOW = 150
#: What each omitted stretch costs in the output.
_GAP_OVERHEAD = 40


def _excerpt(section: _Section, share: int, keep_end: bool = False) -> str:
    """The section if it fits; otherwise its opening plus a window around the
    first mention of EVERY risk category in it, wherever that is.

    Showing only the opening, or only the first risk, loses instructions that
    the risk summary still counts: a migration halfway through a heading-less
    plan, or a key rotation after a migration in the same step. `keep_end`
    also keeps the end (for a document that is a single section).
    """
    body = section.body
    if len(body) <= share:
        return body

    anchors = sorted(
        {m.start() for p in RISK_SIGNALS.values() if (m := p.search(body))}
    )
    # Window order is priority order: risks first, then context.
    starts: list[tuple[str, int]] = [("risk", a) for a in anchors]
    starts.append(("open", 0))
    if keep_end:
        starts.append(("end", len(body)))
    # Too many windows for the room: drop context, then the last risks.
    while len(starts) > 1 and share // len(starts) - _GAP_OVERHEAD < _MIN_WINDOW:
        drop = next((i for i, (kind, _) in enumerate(starts) if kind == "end"), None)
        if drop is None:
            drop = next((i for i, (kind, _) in enumerate(starts) if kind == "open"), None)
        starts.pop(drop if drop is not None else len(starts) - 1)

    if not anchors:
        # No risk: as before, mostly opening, some end.
        if keep_end:
            head, tail = share * 70 // 100, share * 25 // 100
            return f"{body[:head]}\n[... {len(body) - head - tail:,} characters omitted ...]\n{body[-tail:]}"
        cut = body.rfind("\n", 0, share)
        return body[: cut if cut > share // 2 else share] + "\n[...]"

    # The heading labels the excerpt even when the opening window was dropped.
    label = section.heading[:120] if section.heading else ""
    room = share - (len(label) + _GAP_OVERHEAD if label else 0)
    width = max(_MIN_WINDOW // 2, room // len(starts) - _GAP_OVERHEAD)
    windows = []
    for kind, at in starts:
        if kind == "open":
            windows.append((0, width))
        elif kind == "end":
            windows.append((len(body) - width, len(body)))
        else:  # a little context before the signal, the instruction after it
            begin = max(0, at - width // 4)
            windows.append((begin, min(len(body), begin + width)))
    merged: list[list[int]] = []
    for begin, end in sorted(windows):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])

    out = []
    cursor = 0
    if label and merged[0][0] > 0:
        out.append(label)
        # The heading line is shown, not omitted. It is stored stripped, so
        # measure it in the body, where markdown allows up to 3 leading spaces.
        cursor = body.index(section.heading) + len(section.heading)
    for begin, end in merged:
        if begin > cursor:
            out.append(f"[... {begin - cursor:,} characters omitted ...]")
        out.append(body[begin:end])
        cursor = end
    if cursor < len(body):
        out.append(f"[... {len(body) - cursor:,} characters omitted ...]")
    return "\n".join(out)


def _choose(sections: list[_Section], count: int) -> set[int]:
    """Which sections to keep, by `index`, when not all of them fit.

    Order of priority: sections with the most kinds of risk, then the first and
    last (overview and verification), then an even spread across the rest.
    """
    if count >= len(sections):
        return {s.index for s in sections}
    chosen: set[int] = set()

    def take_spread(group: list[_Section], room: int) -> None:
        candidates = [s for s in group if s.index not in chosen]
        for i in _spread(len(candidates), room):
            chosen.add(candidates[i].index)

    # Most kinds of risk first. A group larger than the room left is itself
    # sampled evenly, so late risky sections are as likely to stay as early ones.
    by_risk: dict[int, list[_Section]] = {}
    for s in sections:
        if s.risks:
            by_risk.setdefault(len(s.risks), []).append(s)
    for kinds in sorted(by_risk, reverse=True):
        take_spread(by_risk[kinds], count - len(chosen))
    take_spread([sections[0], sections[-1]], count - len(chosen))
    # The rest: an even grid over the WHOLE document (ends included, so already
    # taken), then whatever room the grid's overlap with risky sections left.
    for i in _spread(len(sections), count):
        if len(chosen) >= count:
            break
        chosen.add(sections[i].index)
    take_spread(sections, count - len(chosen))
    return chosen


def _spread(n: int, k: int) -> list[int]:
    """min(n, k) distinct indexes into range(n), evenly spaced, ends included."""
    if n <= 0 or k <= 0:
        return []
    if k >= n:
        return list(range(n))
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
