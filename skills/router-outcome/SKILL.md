---
name: router-outcome
description: Generate an execution receipt for Adaptive LLM Router after finishing a task that carried an [LLM-ROUTER] metadata block. Use when the user says "generate the router result receipt", "router result", "/router-result", "router-outcome", or asks for the receipt to paste back into the router. Produces a validated [LLM-ROUTER-RESULT] JSON block from observed facts about the session.
---

# Router Outcome Receipt

You have just finished (or stopped working on) a task that came from Adaptive
LLM Router. Your job now is to report **what actually happened**, so the router
can learn whether the configuration it recommended was the right amount of AI
for the job.

You are producing **evidence**, not a review. Report observations. Do not
grade yourself, and do not try to be encouraging.

## 1. Find the task id

Look back through this conversation for the metadata block that came with the
task:

```
[LLM-ROUTER]
task_id=RT-000123
recommended_provider=codex
recommended_model=sol
recommended_effort=high
router_version=1.0.0
[/LLM-ROUTER]
```

Copy `task_id` **exactly**. It is the only link between this work and the
router's record of it; a wrong or invented id makes the receipt unimportable.

If there is no `[LLM-ROUTER]` block anywhere in the conversation, stop and tell
the user:

> I can't find an `[LLM-ROUTER]` block in this conversation, so I don't know
> which router task this was. Paste the handoff block and I'll generate the
> receipt.

Do not guess an id. Do not use `RT-000001` as a placeholder.

## 2. Report the configuration you actually ran on

`execution.provider`, `execution.model` and `execution.effort` describe **what
really ran**, which is not necessarily what was recommended.

- If you know your own provider, model and effort, report them.
- If the user switched model or effort partway through, report the
  configuration that did most of the work, and record the change under
  `escalation`.
- If you genuinely cannot tell, use the recommended values from the
  `[LLM-ROUTER]` block, and say in `agent_assessment.notes` that the
  configuration was assumed rather than observed.

Use the router's own vocabulary: `provider` is `claude` or `codex`; `model` is
the registry key (`sonnet_5`, `opus_5`, `haiku_4_5`, `fable_5_1`, `luna`,
`terra`, `sol`, `astra`); `effort` is one of `none`, `low`, `medium`, `high`,
`xhigh`, `max`, `ultra`.

The router does **not** reject a receipt for a configuration that differs from
the recommendation. A deviation is some of the most useful evidence it can
receive, so report it accurately rather than tidying it up.

## 3. Collect the objective signals

Work through the conversation and answer from what you can see:

| Field | What it means | How to decide |
| --- | --- | --- |
| `outcome.status` | `success`, `partial`, `failed` | Success = the task is done. Partial = some of it is done, or it works with known gaps. Failed = it does not work, or you gave up. |
| `outcome.implementation_complete` | every requested change was made | Not the same as "tests pass". |
| `outcome.first_pass_success` | it worked the first time | False if you had to fix your own work after trying it. Must be false if `debug_cycles > 0` or an escalation happened. |
| `outcome.tests_passed` | `true`, `false`, or `null` | `null` when no tests were run. Do not report `true` for tests you did not actually execute. |
| `outcome.build_passed` | `true`, `false`, or `null` | `null` when nothing was built or type-checked. |
| `outcome.known_regression` | you know something else broke | Only `true` for a regression you actually observed. |
| `work.files_read` | files you opened | `null` if you cannot count them. |
| `work.files_modified` | files you edited | `null` if you cannot count them. |
| `work.debug_cycles` | rounds of fix-and-retry after the first attempt | Count each diagnose-then-fix loop. Zero is a real and common answer. |
| `work.major_replans` | times you abandoned an approach and started over | Not small course corrections. |
| `work.user_corrections` | times the user had to correct your work or direction | Clarifying questions they answered do not count; telling you that you did it wrong does. |

### Escalation

Set `escalation.occurred` to `true` only if the model or effort level actually
changed during the task. When it did, fill in the `from_*` and `to_*` fields
and give a one-line `reason`. `to_provider`, `to_model` and `to_effort` are all
required when `occurred` is `true`.

### Usage

This is the rule that matters most:

> **If the provider did not report token usage to you, every usage number stays
> `null` and `source` stays `"unavailable"`.**

Do not estimate. Do not approximate. Do not reason about how many tokens the
task "probably" used. A guessed number is indistinguishable from a measurement
once it is in the database, and it corrupts every statistic derived from it
from then on.

Only set `source: "provider_reported"` if you have actual reported numbers
(from a usage command, a cost display, or an API response you can see).

## 4. Assess, briefly and honestly

`agent_assessment` is explicitly lower-trust than the objective signals above,
and the router weights it accordingly. Fill it in anyway, plainly:

- `actual_complexity`: `low`, `medium`, `high`, `very_high` - how hard the task
  turned out to be, not how hard it was described.
- `recommendation_fit`:
  - `underpowered` - you struggled in ways a stronger configuration would
    plausibly have avoided.
  - `appropriate` - the configuration matched the work.
  - `potentially_overpowered` - the task was routine and a lighter
    configuration would very likely have handled it.
  - `inconclusive` - you cannot tell.
- `notes`: one or two sentences, only if you have something concrete to add.
  Leave it empty rather than padding it.

## 5. Emit the receipt

Output the block exactly as below, with nothing between the markers except the
JSON object. No commentary inside the markers, no code fence inside them.

```
[LLM-ROUTER-RESULT]
{
  "receipt_version": "1.0",
  "task_id": "RT-000123",

  "execution": {
    "provider": "codex",
    "model": "sol",
    "effort": "high"
  },

  "outcome": {
    "status": "success",
    "implementation_complete": true,
    "first_pass_success": true,
    "tests_passed": true,
    "build_passed": true,
    "known_regression": false
  },

  "work": {
    "files_read": 9,
    "files_modified": 4,
    "debug_cycles": 0,
    "major_replans": 0,
    "user_corrections": 0
  },

  "escalation": {
    "occurred": false,
    "from_provider": null,
    "from_model": null,
    "from_effort": null,
    "to_provider": null,
    "to_model": null,
    "to_effort": null,
    "reason": null
  },

  "usage": {
    "input_tokens": null,
    "cached_input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "provider_reported_cost": null,
    "source": "unavailable"
  },

  "agent_assessment": {
    "actual_complexity": "high",
    "recommendation_fit": "appropriate",
    "notes": ""
  }
}
[/LLM-ROUTER-RESULT]
```

Then add one short line outside the markers:

> Paste this into the Result page at http://localhost:3000.

## Consistency rules the router enforces

Your receipt is validated on import and rejected if it contradicts itself.
Check these before emitting:

- `first_pass_success: true` requires `debug_cycles: 0` and
  `escalation.occurred: false`.
- `status: "failed"` cannot have `first_pass_success: true`.
- `escalation.occurred: true` requires `to_provider`, `to_model` and
  `to_effort`.
- Usage numbers must be `null` when `source` is `"unavailable"`.
- Counts are whole numbers and never negative.
- `task_id` matches `RT-` followed by at least six digits.

## What not to do

- Do not invent a `task_id`.
- Do not estimate tokens, cost, or "roughly how much context" was used.
- Do not round `debug_cycles` up or down to look better or worse.
- Do not ask the user to count anything for you. If you cannot determine a
  count, the answer is `null`.
- Do not report `tests_passed: true` unless you ran tests and saw them pass.
- Do not soften a `failed` into a `partial`. The router learns most from the
  configurations that did not work.
