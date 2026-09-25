"""Receipt parsing and validation.

Receipt text is untrusted input produced by an agent outside this application.
The rule that matters most is the usage rule: a guessed token count is
indistinguishable from a measurement once it is stored, so the schema makes it
impossible to supply numbers while claiming they were not reported.
"""

from __future__ import annotations

import json

import pytest

from app.schemas.execution_receipt import (
    ExecutionReceipt,
    extract_receipt_block,
    parse_receipt,
)

from .fixtures import receipt, wrap


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def test_extracts_the_block_from_surrounding_chatter():
    text = (
        "All done. Here is your receipt:\n\n"
        + wrap(receipt("RT-000001"))
        + "\n\nPaste this into the Result page."
    )
    parsed, _ = parse_receipt(text)
    assert parsed.task_id == "RT-000001"


def test_accepts_bare_json_without_the_markers():
    """A user who copied only the JSON should not be punished for it."""
    parsed, _ = parse_receipt(json.dumps(receipt("RT-000042")))
    assert parsed.task_id == "RT-000042"


def test_tolerates_a_code_fence_inside_the_markers():
    body = json.dumps(receipt("RT-000007"), indent=2)
    text = f"[LLM-ROUTER-RESULT]\n```json\n{body}\n```\n[/LLM-ROUTER-RESULT]"
    parsed, _ = parse_receipt(text)
    assert parsed.task_id == "RT-000007"


def test_empty_input_is_reported_plainly():
    with pytest.raises(ValueError, match="Nothing was pasted"):
        extract_receipt_block("   ")


def test_prose_without_json_is_reported_plainly():
    with pytest.raises(ValueError, match="No JSON object found"):
        extract_receipt_block("The agent said it went fine.")


def test_malformed_json_names_the_problem():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_receipt('[LLM-ROUTER-RESULT]\n{"task_id": }\n[/LLM-ROUTER-RESULT]')


# --------------------------------------------------------------------------
# Task id
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", ["RT-1", "000001", "rt-000001", "TASK-000001", ""])
def test_a_non_router_task_id_is_rejected(task_id):
    with pytest.raises(Exception, match="task id|task_id"):
        ExecutionReceipt.model_validate(receipt(task_id))


def test_a_valid_task_id_is_accepted():
    assert ExecutionReceipt.model_validate(receipt("RT-000123")).task_id == "RT-000123"


# --------------------------------------------------------------------------
# Usage: the rule that protects the learning data
# --------------------------------------------------------------------------


def test_usage_numbers_are_refused_when_the_source_says_unavailable():
    payload = receipt("RT-000001", usage={"source": "unavailable", "output_tokens": 35_000})
    with pytest.raises(Exception, match="unavailable"):
        ExecutionReceipt.model_validate(payload)


def test_usage_numbers_are_accepted_when_the_provider_reported_them():
    payload = receipt(
        "RT-000001",
        usage={
            "source": "provider_reported",
            "input_tokens": 120_000,
            "cached_input_tokens": 90_000,
            "output_tokens": 8_000,
            "reasoning_tokens": 22_000,
            "provider_reported_cost": 0.42,
        },
    )
    parsed = ExecutionReceipt.model_validate(payload)
    assert parsed.usage.output_tokens == 8_000
    assert parsed.usage.source.value == "provider_reported"


def test_null_usage_is_the_default():
    parsed = ExecutionReceipt.model_validate(receipt("RT-000001"))
    assert parsed.usage.output_tokens is None
    assert parsed.usage.source.value == "unavailable"


def test_negative_token_counts_are_rejected():
    payload = receipt(
        "RT-000001", usage={"source": "provider_reported", "output_tokens": -5}
    )
    with pytest.raises(Exception):
        ExecutionReceipt.model_validate(payload)


# --------------------------------------------------------------------------
# Internal consistency
# --------------------------------------------------------------------------


def test_first_pass_success_cannot_coexist_with_debug_cycles():
    payload = receipt("RT-000001", work={"debug_cycles": 3})
    with pytest.raises(Exception, match="debug_cycles"):
        ExecutionReceipt.model_validate(payload)


def test_first_pass_success_cannot_coexist_with_an_escalation():
    payload = receipt(
        "RT-000001",
        escalation={
            "occurred": True,
            "to_provider": "claude",
            "to_model": "opus_5",
            "to_effort": "high",
        },
    )
    with pytest.raises(Exception, match="escalation"):
        ExecutionReceipt.model_validate(payload)


def test_a_failed_task_cannot_be_a_first_pass_success():
    payload = receipt(
        "RT-000001",
        outcome={"status": "failed", "first_pass_success": True},
    )
    with pytest.raises(Exception, match="first-pass success"):
        ExecutionReceipt.model_validate(payload)


def test_an_escalation_must_name_its_target():
    payload = receipt(
        "RT-000001",
        outcome={"status": "success", "first_pass_success": False},
        escalation={"occurred": True},
    )
    with pytest.raises(Exception, match="target configuration is incomplete"):
        ExecutionReceipt.model_validate(payload)


def test_a_complete_escalation_is_accepted():
    payload = receipt(
        "RT-000001",
        outcome={"status": "success", "first_pass_success": False},
        work={"debug_cycles": 2},
        escalation={
            "occurred": True,
            "from_provider": "codex",
            "from_model": "terra",
            "from_effort": "medium",
            "to_provider": "codex",
            "to_model": "sol",
            "to_effort": "high",
            "reason": "terra kept reintroducing the same bug",
        },
    )
    parsed = ExecutionReceipt.model_validate(payload)
    assert parsed.escalation.occurred
    assert parsed.escalation.to_model == "sol"


def test_negative_work_counts_are_rejected():
    with pytest.raises(Exception):
        ExecutionReceipt.model_validate(receipt("RT-000001", work={"debug_cycles": -1}))


def test_an_unsupported_receipt_version_is_rejected():
    with pytest.raises(Exception, match="receipt_version"):
        ExecutionReceipt.model_validate(receipt("RT-000001", receipt_version="2.0"))


def test_an_unknown_effort_is_rejected():
    payload = receipt(
        "RT-000001", execution={"provider": "codex", "model": "sol", "effort": "turbo"}
    )
    with pytest.raises(Exception):
        ExecutionReceipt.model_validate(payload)


def test_unknown_extra_fields_are_ignored_rather_than_fatal():
    """A future skill version adding a field must not break an older router."""
    payload = receipt("RT-000001")
    payload["future_field"] = {"anything": True}
    payload["outcome"]["future_signal"] = 7
    parsed = ExecutionReceipt.model_validate(payload)
    assert parsed.task_id == "RT-000001"


def test_tests_and_build_may_be_unknown():
    payload = receipt(
        "RT-000001", outcome={"tests_passed": None, "build_passed": None}
    )
    parsed = ExecutionReceipt.model_validate(payload)
    assert parsed.outcome.tests_passed is None
    assert parsed.outcome.build_passed is None
