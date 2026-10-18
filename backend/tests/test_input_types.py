"""Input types: task, spec or implementation plan, and spec/plan files.

A spec or plan can be far longer than a task. It is condensed before the
analyzer sees it, and when it comes from a file the handoff points at that
file instead of carrying the text, because the text may not fit in an agent's
prompt at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

import pytest

from app.config import get_settings
from app.db.models import TaskSource
from app.schemas.api import TASK_MAX_CHARS
from app.schemas.enums import InputType
from app.schemas.task_fingerprint import AnalyzerMetadata, FingerprintEnvelope
from app.services import pi_service
from app.services.analyzer import HeuristicAnalyzer
from app.services.input_digest import ANALYSIS_LIMIT, condense_for_analysis
from app.services.preparation import recommend_preparation

from .fixtures import fp

PLAN_PATH = "docs/plans/billing-plan.md"


def long_plan(sections: int = 40, filler: int = 9_000) -> str:
    """A realistic plan: headings, numbered steps, checkboxes and file paths."""
    parts = ["# Billing multi-currency plan", "", "Overview: add currencies to billing.", ""]
    for i in range(1, sections + 1):
        parts += [
            f"## Step {i}: change module {i}",
            f"1. Edit `backend/billing/module_{i}.py` and frontend/src/pages/Page{i}.tsx",
            f"2. Add tests in tests/test_module_{i}.py",
            "- [ ] verify the migration",
            f"OPENING-OF-SECTION-{i} " + ("lorem ipsum " * (filler // 12)),
            "",
        ]
    return "\n".join(parts)


@pytest.fixture
def seen(monkeypatch):
    """Record exactly what the analyzer is given, and return a fixed fingerprint."""
    calls: list[dict] = []
    fingerprint = {"value": fp(complexity=0.5)}

    async def analyze(self, task, overrides=None, input_type=InputType.TASK):  # noqa: ARG001
        calls.append({"text": task, "input_type": InputType(input_type)})
        return FingerprintEnvelope(
            fingerprint=fingerprint["value"],
            analyzer=AnalyzerMetadata(provider="heuristic", model="test", confidence=0.9),
        )

    monkeypatch.setattr(HeuristicAnalyzer, "analyze", analyze)
    calls_fingerprint = fingerprint
    return calls, calls_fingerprint


def as_file(text: str, path: str = PLAN_PATH) -> dict:
    """The path and hash the Router page sends for a picked file."""
    return {"source_path": path, "source_sha256": hashlib.sha256(text.encode()).hexdigest()}


def create(client, text, input_type="task", **extra):
    body = {"task": text, "analyzer": "heuristic", "input_type": input_type, **extra}
    return client.post("/api/tasks", json=body)


# --- Condensing -------------------------------------------------------------


def test_short_documents_and_every_task_are_sent_unchanged():
    short = "1. do it"
    assert condense_for_analysis(short, InputType.PLAN).text == short
    huge_task = "x" * (ANALYSIS_LIMIT * 3)
    digest = condense_for_analysis(huge_task, InputType.TASK)
    assert digest.text == huge_task and not digest.condensed


def test_a_long_plan_is_condensed_to_its_shape():
    plan = long_plan()
    assert len(plan) > ANALYSIS_LIMIT * 10
    digest = condense_for_analysis(plan, InputType.PLAN)

    assert digest.condensed and digest.original_chars == len(plan)
    assert len(digest.text) <= ANALYSIS_LIMIT
    text = digest.text
    # The real size, so the analyzer does not mistake the digest for the whole.
    assert f"is {len(plan):,} characters" in text and "implementation plan" in text
    assert "40 numbered steps" not in text  # two numbered lines per section
    assert "80 numbered steps" in text and "40 checklist items" in text
    # Outline: every heading.
    for i in (1, 20, 40):
        assert f"## Step {i}: change module {i}" in text
    # Referenced files, including bare `names.ext` in backticks.
    assert "backend/billing/module_7.py" in text and "frontend/src/pages/Page7.tsx" in text
    # The opening of sections, not just the first one.
    assert "OPENING-OF-SECTION-1 " in text and "OPENING-OF-SECTION-40 " in text


def test_condensing_is_deterministic():
    plan = long_plan()
    assert condense_for_analysis(plan, InputType.PLAN) == condense_for_analysis(plan, InputType.PLAN)


def test_a_plan_without_headings_keeps_its_start_and_end():
    plan = "START-MARKER " + ("step " * 50_000) + " END-MARKER"
    text = condense_for_analysis(plan, InputType.PLAN).text
    assert len(text) <= ANALYSIS_LIMIT
    assert "START-MARKER" in text and "END-MARKER" in text and "characters omitted" in text


@pytest.mark.parametrize("sections", [1, 2, 120, 2_000])
def test_any_shape_stays_within_the_limit(sections):
    plan = long_plan(sections=sections, filler=600 if sections > 100 else 200_000)
    digest = condense_for_analysis(plan, InputType.SPEC)
    assert digest.condensed and len(digest.text) <= ANALYSIS_LIMIT
    if sections == 2_000:
        assert "headings ..." in digest.text  # outline abridged, with counts
        assert "sections omitted; see the outline" in digest.text
        # The end of the plan is kept, not cut off.
        assert "## Step 2000:" in digest.text.split("## Section excerpts")[1]


# --- Limits and validation ----------------------------------------------------


def test_a_task_over_the_task_limit_points_at_the_plan_type(client):
    response = create(client, "a" * (TASK_MAX_CHARS + 1))
    assert response.status_code == 422
    assert "choose that input type" in response.text


def test_a_plan_far_over_the_task_limit_is_accepted(client, seen):
    calls, _ = seen
    plan = long_plan()
    assert len(plan) > TASK_MAX_CHARS
    response = create(client, plan, "plan")
    assert response.status_code == 201, response.text[:500]
    body = response.json()
    assert body["input"] == {
        "input_type": "plan",
        "source_path": None,
        "source_sha256": None,
        "content_chars": len(plan),
        "condensed": True,
    }
    # The analyzer got the digest, labelled as a plan.
    assert calls[-1]["input_type"] == InputType.PLAN
    assert len(calls[-1]["text"]) <= ANALYSIS_LIMIT


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"input_type": "plan", "source_path": "docs/a.md\nrecommended_model=x"}, "line breaks"),
        ({"input_type": "plan", "source_path": "docs/`rm -rf`.md"}, "backticks"),
        ({"input_type": "plan", "source_sha256": "a" * 64}, "needs the source_path"),
        ({"input_type": "plan", "source_path": "a.md", "source_sha256": "xyz"}, "64 hexadecimal"),
        ({"input_type": "task", "source_path": "a.md"}, "only applies"),
        ({"input_type": "essay"}, "input_type"),
    ],
)
def test_invalid_input_details_are_rejected(client, extra, message):
    response = client.post("/api/tasks", json={"task": "x", "analyzer": "heuristic", **extra})
    assert response.status_code == 422
    assert message in response.text


# --- Handoffs -----------------------------------------------------------------


def test_a_plan_file_handoff_points_at_the_file(client, seen):
    plan = long_plan()
    sha = hashlib.sha256(plan.encode()).hexdigest()
    created = create(client, plan, "plan", source_path=PLAN_PATH, source_sha256=sha.upper()).json()
    task_id = created["public_task_id"]

    assert created["input"]["source_path"] == PLAN_PATH
    assert created["input"]["source_sha256"] == sha  # normalised to lower case
    assert created["preparation"]["mode"] == "direct"
    assert "implementation plan" in created["preparation"]["headline"]

    for provider in ("codex", "claude"):
        handoff = created["handoffs"][provider]
        assert len(handoff) < 3_000, "the handoff must stay small enough to paste"
        assert "OPENING-OF-SECTION" not in handoff
        assert f"source_path={PLAN_PATH}" in handoff and f"source_sha256={sha}" in handoff
        assert "input_type=plan" in handoff
        assert f"`{PLAN_PATH}`" in handoff and sha in handoff
        assert "IMPLEMENTATION PLAN" in handoff and "Execute the plan" in handoff
        # Metadata lines stay inside the block the outcome skill parses.
        block = handoff.split("[/LLM-ROUTER]")[0]
        assert f"task_id={task_id}" in block and "source_path=" in block

    single = client.get(f"/api/tasks/{task_id}/handoff", params={"provider": "codex"}).json()
    assert single["handoff"] == created["handoffs"]["codex"]
    assert single["task_only"].startswith("The implementation plan is in the file")
    # "Copy task only" on the Router page copies this, never the huge file.
    assert created["task_only"] == single["task_only"]
    assert "OPENING-OF-SECTION" not in created["task_only"]
    fetched = client.get(f"/api/tasks/{task_id}").json()
    assert fetched["input"] == created["input"]
    assert fetched["handoffs"] == created["handoffs"]


def test_a_pasted_plan_is_carried_inline_under_its_own_title(client, seen):
    created = create(client, "1. Edit a.py\n2. Test it", "plan").json()
    handoff = created["handoffs"]["codex"]
    assert "IMPLEMENTATION PLAN\n\n1. Edit a.py\n2. Test it" in handoff
    assert "source_path=" not in handoff


def test_a_complex_spec_file_gets_a_plan_written_from_it(client, seen):
    _, fingerprint = seen
    fingerprint["value"] = fp(
        complexity=0.9, scope="multi_service", requirements_clarity=0.3, ambiguity=0.7
    )
    created = create(client, "Goal: everything", "spec", **as_file("Goal: everything", "docs/specs/s.md")).json()
    prep = created["preparation"]
    # Vague as well, but a spec is never told to write a spec.
    assert prep["mode"] == "plan" and prep["spec_path"] is None
    assert "decisions open" in prep["headline"]
    handoff = created["handoffs"]["claude"]
    assert "from the spec in docs/specs/s.md" in handoff
    assert "Write a spec" not in handoff
    assert handoff.index("PREPARATION") < handoff.index("SPEC\n")


def test_a_pasted_complex_spec_plans_from_the_spec_below(client, seen):
    _, fingerprint = seen
    fingerprint["value"] = fp(complexity=0.9, scope="multi_service")
    handoff = create(client, "Goal: everything", "spec").json()["handoffs"]["codex"]
    assert "from the spec below" in handoff


def test_a_plain_task_is_unchanged(client, seen, session):
    created = create(client, "Fix the typo").json()
    assert created["input"]["input_type"] == "task" and created["input"]["source_path"] is None
    handoff = created["handoffs"]["codex"]
    assert "TASK\n\nFix the typo" in handoff
    assert "input_type=" not in handoff and "source_path=" not in handoff
    assert session.query(TaskSource).count() == 0


def test_reanalysis_keeps_the_input_type(client, seen):
    calls, _ = seen
    plan = long_plan()
    task_id = create(client, plan, "plan", **as_file(plan)).json()["public_task_id"]
    again = client.post(f"/api/tasks/{task_id}/analyze", json={"analyzer": "heuristic"}).json()
    assert calls[-1]["input_type"] == InputType.PLAN and len(calls[-1]["text"]) <= ANALYSIS_LIMIT
    assert again["input"]["condensed"] is True
    assert f"`{PLAN_PATH}`" in again["handoffs"]["codex"]


def test_deleting_a_task_removes_its_source(client, seen, session):
    task_id = create(client, "1. x", "plan", **as_file("1. x")).json()["public_task_id"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert session.query(TaskSource).count() == 0


def test_preparation_never_asks_a_plan_for_more_preparation():
    hard_and_vague = fp(complexity=0.95, scope="architecture", requirements_clarity=0.1, ambiguity=0.9)
    assert recommend_preparation(hard_and_vague, "RT-1", InputType.TASK).mode == "spec_and_plan"
    assert recommend_preparation(hard_and_vague, "RT-1", InputType.SPEC).mode == "plan"
    assert recommend_preparation(hard_and_vague, "RT-1", InputType.PLAN).mode == "direct"


# --- The Pi client --------------------------------------------------------------


def test_the_pi_client_sends_the_input_type(app_env, monkeypatch):
    sent: list[dict] = []

    async def post(self, payload):  # noqa: ARG001
        sent.append(payload)
        raise pi_service.httpx.TimeoutException("stop here")

    monkeypatch.setattr(pi_service.PiBridgeAnalyzer, "_post_analyze", post)
    analyzer = pi_service.PiBridgeAnalyzer(get_settings())
    for input_type in InputType:
        with pytest.raises(Exception):
            asyncio.run(analyzer.analyze("text", None, input_type))
    assert [p["input_type"] for p in sent] == ["task", "spec", "plan"]


# --- Review round 1: late risks survive condensing ---------------------------


def cosmetic_plan_with(risky: dict[int, str], sections: int = 400) -> str:
    """Many cosmetic steps; the given step numbers carry real risk."""
    parts = ["# Plan"]
    for i in range(1, sections + 1):
        title = "Risky step" if i in risky else "Tweak UI copy"
        body = risky.get(i, "Rename a label and adjust spacing. " * 8)
        parts += [f"## Step {i}: {title}", body, ""]
    return "\n".join(parts)


def test_a_late_risky_step_is_never_dropped():
    """Regression: only early sections used to survive, so step 390 vanished."""
    plan = cosmetic_plan_with({390: "LATE-MIGRATION: write a data migration that rebuilds the payments table."})
    text = condense_for_analysis(plan, InputType.PLAN).text
    assert len(text) <= ANALYSIS_LIMIT
    excerpts = text[text.index("## Section excerpts"):]
    assert "LATE-MIGRATION" in excerpts, "the risky step's own text must be excerpted"
    outline = text[text.index("## Outline"):text.index("## Section excerpts")]
    assert "## Step 390: Risky step" in outline
    assert "data migration / schema: 1 sections, e.g. ## Step 390" in text


@pytest.mark.parametrize(
    ("phrase", "category"),
    [
        ("rotate the encryption keys for stored card tokens", "security"),
        ("make the retry path idempotent so a race condition cannot double-charge", "concurrency"),
        ("cut over production with zero downtime", "destructive / production"),
        ("ALTER TABLE invoices DROP COLUMN legacy_total", "data migration / schema"),
    ],
)
def test_every_risk_category_is_detected_anywhere(phrase, category):
    text = condense_for_analysis(cosmetic_plan_with({397: phrase}), InputType.PLAN).text
    assert f"{category}: 1 sections, e.g. ## Step 397" in text
    assert phrase in text


def test_risk_signals_report_none_when_there_are_none():
    text = condense_for_analysis(cosmetic_plan_with({}), InputType.PLAN).text
    assert "## Risk signals (whole document)\n- none found" in text


def test_more_risky_sections_than_room_are_sampled_across_the_whole_plan():
    """When risk is everywhere, the kept excerpts must not all come from the start."""
    risky = {i: f"RISK-{i}: data migration for table t{i}." for i in range(1, 1201)}
    text = condense_for_analysis(cosmetic_plan_with(risky, sections=1200), InputType.PLAN).text
    kept = [int(n) for n in re.findall(r"RISK-(\d+):", text[text.index("## Section excerpts"):])]
    assert 20 < len(kept) < 1200
    assert min(kept) <= 10 and max(kept) >= 1190, "both ends of the plan are represented"
    assert max(b - a for a, b in zip(kept, kept[1:])) <= 3 * 1200 // len(kept)


def test_kept_sections_are_spread_evenly_not_bunched_at_the_start():
    text = condense_for_analysis(cosmetic_plan_with({}), InputType.PLAN).text
    for block, start, end in [("excerpts", "## Section excerpts", None), ("outline", "## Outline", "## Section excerpts")]:
        part = text[text.index(start): text.index(end) if end else None]
        kept = [int(n) for n in re.findall(r"## Step (\d+):", part)]
        gaps = [b - a for a, b in zip(kept, kept[1:])]
        assert max(gaps) - min(gaps) <= 2, f"{block} spacing is uneven: {gaps[:12]}"
        assert kept[-1] == 400, f"{block} must reach the last step"


# --- Review round 1: a file always carries a verified hash -------------------


def test_a_file_path_without_its_hash_is_rejected(client, session):
    response = create(client, "1. x", "plan", source_path=PLAN_PATH)
    assert response.status_code == 422 and "needs source_sha256" in response.text
    assert session.query(TaskSource).count() == 0


def test_a_hash_of_other_bytes_is_rejected(client, session):
    response = create(client, "1. x", "plan", **as_file("1. something else"))
    assert response.status_code == 422 and "does not match" in response.text
    assert session.query(TaskSource).count() == 0


def test_a_hash_of_the_file_with_its_byte_order_mark_is_accepted(client, seen):
    """The browser strips a BOM when decoding; the hash is of the bytes on disk."""
    text = "# Plan\r\n1. x\r\n"
    sha = hashlib.sha256(b"\xef\xbb\xbf" + text.encode()).hexdigest()
    response = create(client, text, "plan", source_path=PLAN_PATH, source_sha256=sha)
    assert response.status_code == 201, response.text
    assert f"source_sha256={sha}" in response.json()["handoffs"]["codex"]


def test_a_risk_deep_inside_a_long_section_is_what_gets_excerpted():
    """Regression: an excerpt was always a section's opening, so a migration
    mentioned 6,000 characters into a step was counted but never shown."""
    deep = ("Polish the settings page copy. " * 200) + "DEEP-RISK: then backfill the ledger with a data migration."
    text = condense_for_analysis(cosmetic_plan_with({200: deep}), InputType.PLAN).text
    assert len(text) <= ANALYSIS_LIMIT
    excerpts = text[text.index("## Section excerpts"):]
    # The risky instruction is shown, inside the excerpt labelled with its own
    # heading (not floating under some other section's).
    own = excerpts[excerpts.index("## Step 200: Risky step"):]
    own = own[: own.index("\n\n", own.index("DEEP-RISK"))] if "DEEP-RISK" in own else own
    assert "DEEP-RISK: then backfill the ledger with a data migration." in own
    assert "## Step 201:" not in own


# --- Review round 2: risks anywhere inside one long section -----------------


def test_a_risk_in_the_middle_of_a_plan_without_headings_is_excerpted():
    """Regression: a heading-less plan kept only its start and end."""
    filler = "Rename a label and adjust spacing on the settings page. " * 1500
    plan = filler + " MIDDLE-RISK: write a data migration that rebuilds the payments table in batches. " + filler + " FINAL-CHECK"
    text = condense_for_analysis(plan, InputType.PLAN).text
    assert len(text) <= ANALYSIS_LIMIT
    excerpts = text[text.index("## Section excerpts"):]
    assert "MIDDLE-RISK: write a data migration that rebuilds the payments table in batches." in excerpts
    assert excerpts.count("Rename a label") > 0, "the opening is still shown"
    assert "FINAL-CHECK" in excerpts, "the end of a single-section document is still shown"
    assert "characters omitted" in excerpts


def test_every_risk_category_in_one_section_gets_its_own_window():
    """Regression: only the first risk in a section was excerpted."""
    body = (
        ("Polish copy. " * 3000) + "RISK-A: data migration of the ledger. "
        + ("Polish copy. " * 3000) + "RISK-B: rotate the encryption keys. "
        + ("Polish copy. " * 3000) + "RISK-C: make retries idempotent. "
    )
    plan = "\n".join(["# Plan"] + [f"## Step {i}\n" + (body if i == 3 else "Tweak a label. " * 50) for i in range(1, 6)])
    text = condense_for_analysis(plan, InputType.PLAN).text
    assert len(text) <= ANALYSIS_LIMIT
    for marker in ("RISK-A: data migration", "RISK-B: rotate the encryption keys", "RISK-C: make retries idempotent"):
        assert marker in text


def test_squeezed_risky_excerpts_keep_their_heading():
    """With little room per section, an excerpt is still labelled."""
    risky = {i: ("Polish copy. " * 100) + f"RISK-{i}: data migration of t{i}." for i in range(1, 1201)}
    text = condense_for_analysis(cosmetic_plan_with(risky, sections=1200), InputType.PLAN).text
    excerpts = text[text.index("## Section excerpts"):]
    for found in re.finditer(r"RISK-(\d+):", excerpts):
        before = excerpts[: found.start()]
        assert before.rfind(f"## Step {found.group(1)}: Risky step") > before.rfind("sections omitted")
